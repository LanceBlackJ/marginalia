"""数据集检查（说明书 §10.6）：CSV/TSV/Excel/JSON 的格式、字段、样本数、缺失值、类型、基础统计、分布与少量样本预览。

大文件只读元数据与有限行数（分块读取，内存可控）；结果可直接序列化为 JSON 落库。
绝不执行文件内容；只读。
"""
from __future__ import annotations

import csv
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..util import ApiError

PREVIEW_ROWS = 50
SCAN_ROW_LIMIT = 1_000_000  # 单个表最多完整扫描的行数；超过则标记 partial
EXCEL_ROW_LIMIT = 200_000
CHUNK_ROWS = 50_000
ISO_DATE_RE = r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
SAMPLE_CAP = 100_000  # 每个数值列保留用于分位数/直方图的样本数
DISTINCT_CAP = 50_000  # 每列精确统计的不同取值上限
HIST_BINS = 12
MAX_SHEETS = 20
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".json"}


def _clean(v: Any) -> Any:
    """转成可 JSON 序列化的值。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, bytes):
        return v[:100].decode("utf-8", "replace")
    return str(v)[:500]


class _Col:
    def __init__(self, name: str):
        self.name = name
        self.total = 0
        self.non_null = 0
        self.n_num = 0
        self.n_int = 0
        self.n_bool = 0
        self.n_dt = 0
        self.sum = 0.0
        self.sumsq = 0.0
        self.min = math.inf
        self.max = -math.inf
        self.s_vals = np.empty(0)  # 均匀蓄水池样本：每个值带一个随机键，始终保留键最小的 SAMPLE_CAP 个
        self.s_keys = np.empty(0)
        self.counter: Counter = Counter()
        self.overflow = False
        self.str_min: str | None = None
        self.str_max: str | None = None
        self.dt_min: str | None = None
        self.dt_max: str | None = None
        self.rng = np.random.default_rng(0)

    def _add_sample(self, vals: np.ndarray) -> None:
        keys = self.rng.random(len(vals))
        allk = np.concatenate([self.s_keys, keys])
        allv = np.concatenate([self.s_vals, vals])
        if len(allk) > SAMPLE_CAP:
            keep = np.argpartition(allk, SAMPLE_CAP - 1)[:SAMPLE_CAP]
            allk, allv = allk[keep], allv[keep]
        self.s_keys, self.s_vals = allk, allv

    def feed(self, s: pd.Series) -> None:
        self.total += len(s)
        s = s.dropna()
        if pd.api.types.is_string_dtype(s) or s.dtype == object:
            s = s[~s.map(lambda x: isinstance(x, str) and not x.strip())]  # 空白字符串视为缺失
        self.non_null += len(s)
        if s.empty:
            return
        is_bool = s.map(lambda x: isinstance(x, (bool, np.bool_)))
        self.n_bool += int(is_bool.sum())
        rest = s[~is_bool]
        is_dt = rest.map(lambda x: hasattr(x, "isoformat"))
        self.n_dt += int(is_dt.sum())
        if is_dt.any():
            iso = rest[is_dt].map(lambda x: x.isoformat())
            lo, hi = iso.min(), iso.max()
            self.dt_min = lo if self.dt_min is None or lo < self.dt_min else self.dt_min
            self.dt_max = hi if self.dt_max is None or hi > self.dt_max else self.dt_max
        rest = rest[~is_dt]
        if rest.empty:
            return
        # CSV / JSON 里的日期是字符串：形如 2021-03-29 或 2021-03-29 10:00:00 的按时间处理（字典序即时间序）
        is_str = rest.map(lambda x: isinstance(x, str))
        if is_str.any():
            iso_mask = is_str & rest.astype(str).str.strip().str.match(ISO_DATE_RE)
            if iso_mask.any():
                iso = rest[iso_mask].astype(str).str.strip()
                self.n_dt += int(iso_mask.sum())
                lo, hi = iso.min(), iso.max()
                self.dt_min = lo if self.dt_min is None or lo < self.dt_min else self.dt_min
                self.dt_max = hi if self.dt_max is None or hi > self.dt_max else self.dt_max
        numf = pd.to_numeric(rest, errors="coerce").astype("float64")
        ok = np.isfinite(numf.to_numpy())
        vals = numf.to_numpy()[ok]
        self.n_num += len(vals)
        if len(vals):
            self.n_int += int(np.sum(np.mod(vals, 1) == 0))
            self.sum += float(vals.sum())
            self.sumsq += float((vals ** 2).sum())
            self.min = min(self.min, float(vals.min()))
            self.max = max(self.max, float(vals.max()))
            self._add_sample(vals)
        # 取值频次（高基数列限制精确统计的上限）
        for key, cnt in rest.map(str).value_counts().items():
            if key in self.counter or len(self.counter) < DISTINCT_CAP:
                self.counter[key] += int(cnt)
            else:
                self.overflow = True
        texts = rest[~ok]
        for v in texts.head(2000):
            sv = str(v)
            self.str_min = sv if self.str_min is None or sv < self.str_min else self.str_min
            self.str_max = sv if self.str_max is None or sv > self.str_max else self.str_max

    def finish(self) -> dict:
        nn = self.non_null
        dtype = "string"
        if nn:
            keys = {str(k).strip().lower() for k in self.counter}
            if self.n_bool == nn or (not self.overflow and 0 < len(keys) <= 2 and keys <= {"true", "false", "yes", "no", "t", "f", "y", "n"}):
                dtype = "boolean"
            elif self.n_dt >= 0.9 * nn:
                dtype = "datetime"
            elif self.n_num >= 0.98 * nn:
                dtype = "integer" if self.n_int == self.n_num else "float"
            elif self.n_num + self.n_bool + self.n_dt >= 0.5 * nn and self.n_num > 0:
                dtype = "mixed"
        out: dict = {
            "name": self.name, "dtype": dtype, "non_null": nn, "missing": self.total - nn,
            "missing_rate": round((self.total - nn) / self.total, 4) if self.total else 0.0,
            "unique": len(self.counter), "unique_exact": not self.overflow,
            "stats": None, "histogram": None, "top_values": None,
        }
        if dtype in ("integer", "float") and self.n_num:
            vals = self.s_vals
            mean = self.sum / self.n_num
            var = max(0.0, self.sumsq / self.n_num - mean ** 2)
            stats = {"min": self.min, "max": self.max, "mean": mean, "std": math.sqrt(var)}
            if len(vals):
                q25, med, q75 = np.percentile(vals, [25, 50, 75])
                stats.update({"median": float(med), "q25": float(q25), "q75": float(q75)})
            out["stats"] = {k: _clean(v) for k, v in stats.items()}
            if len(vals) and self.max > self.min:
                counts, edges = np.histogram(vals, bins=HIST_BINS)
                out["histogram"] = {"bins": [{"start": float(edges[i]), "end": float(edges[i + 1]), "count": int(counts[i])}
                                             for i in range(len(counts))], "sampled": self.n_num > len(vals)}
            elif len(vals):
                out["histogram"] = {"bins": [{"start": self.min, "end": self.max, "count": int(len(vals))}], "sampled": False}
        if dtype in ("string", "boolean", "mixed") or (dtype in ("integer",) and out["unique"] <= HIST_BINS):
            top = self.counter.most_common(10)
            out["top_values"] = [{"value": v, "count": c} for v, c in top]
        if dtype == "datetime":
            out["stats"] = {"min": self.dt_min, "max": self.dt_max}
        elif dtype == "string" and self.str_min is not None:
            out["stats"] = {"min": self.str_min[:80], "max": (self.str_max or "")[:80]}
        return out


def _profile_frames(name: str, frames, *, limit: int, preview: bool = True) -> dict:
    cols: dict[str, _Col] = {}
    order: list[str] = []
    rows = 0
    partial = False
    preview_rows: list[list] = []
    preview_cols: list[str] = []
    for df in frames:
        df = df.copy()
        df.columns = [str(c) for c in df.columns]
        if not order:
            order = list(df.columns)
            preview_cols = order[:]
        for c in df.columns:
            cols.setdefault(c, _Col(c))
        if preview and len(preview_rows) < PREVIEW_ROWS:
            head = df.head(PREVIEW_ROWS - len(preview_rows))
            preview_rows.extend([[_clean(v) for v in row] for row in head[order].itertuples(index=False, name=None)])
        for c in df.columns:
            cols[c].feed(df[c])
        rows += len(df)
        if rows >= limit:
            partial = True
            break
    return {
        "name": name, "rows": rows, "rows_scanned": rows, "partial": partial,
        "columns": [cols[c].finish() for c in order if c in cols],
        "preview": {"columns": preview_cols, "rows": preview_rows},
    }


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:200_000] if path.stat().st_size else b""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError as exc:
            # 样本在多字节字符中间截断不算错误
            if exc.start >= len(raw) - 4:
                return enc
    return "latin-1"


def _detect_delimiter(path: Path, encoding: str, ext: str) -> str:
    if ext == ".tsv":
        return "\t"
    with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(16384)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _count_lines(path: Path) -> int:
    n = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            n += chunk.count(b"\n")
    return n


def profile_csv(path: Path, ext: str) -> dict:
    enc = _detect_encoding(path)
    delim = _detect_delimiter(path, enc, ext)
    try:
        reader = pd.read_csv(path, sep=delim, encoding=enc, dtype=str, chunksize=CHUNK_ROWS, on_bad_lines="skip",
                             skipinitialspace=True, engine="c", encoding_errors="replace")
        sheet = _profile_frames(path.stem, reader, limit=SCAN_ROW_LIMIT)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError) as exc:
        raise ApiError("bad_request", f"无法解析该表格文件：{str(exc)[:120]}") from exc
    if sheet["partial"]:
        sheet["rows"] = max(sheet["rows_scanned"], _count_lines(path) - 1)
        sheet["rows_approx"] = True
    return {"kind": "table", "format": ext.lstrip("."), "encoding": enc, "delimiter": delim,
            "size": path.stat().st_size, "sheets": [sheet]}


def profile_excel(path: Path) -> dict:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ApiError("bad_request", f"无法打开 Excel 文件：{str(exc)[:120]}") from exc
    sheets = []
    try:
        for ws in list(wb.worksheets)[:MAX_SHEETS]:
            def frames(ws=ws):
                it = ws.iter_rows(values_only=True)
                header = next(it, None)
                if header is None:
                    return
                names = [(str(h) if h is not None and str(h).strip() else f"列{i + 1}") for i, h in enumerate(header)]
                buf: list[tuple] = []
                for row in it:
                    buf.append(row)
                    if len(buf) >= CHUNK_ROWS:
                        yield pd.DataFrame(buf, columns=names[: len(buf[0])] if buf else names, dtype=object)
                        buf = []
                if buf:
                    yield pd.DataFrame(buf, columns=names[: len(buf[0])], dtype=object)

            sheet = _profile_frames(ws.title, frames(), limit=EXCEL_ROW_LIMIT)
            if sheet["partial"] and ws.max_row:
                sheet["rows"] = max(sheet["rows_scanned"], ws.max_row - 1)
                sheet["rows_approx"] = True
            sheets.append(sheet)
    finally:
        wb.close()
    return {"kind": "table", "format": "xlsx", "size": path.stat().st_size, "sheets": sheets,
            "sheet_names": [s["name"] for s in sheets]}


def profile_json(path: Path) -> dict:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ApiError("bad_request", "JSON 文件超过 20MB，暂不做表格分析")
    try:
        df = pd.read_json(path)
    except ValueError as exc:
        raise ApiError("bad_request", "JSON 不是“记录列表”形式，无法按表格分析") from exc
    sheet = _profile_frames(path.stem, [df.astype(object)], limit=SCAN_ROW_LIMIT)
    return {"kind": "table", "format": "json", "size": path.stat().st_size, "sheets": [sheet]}


def profile_file(path: Path) -> dict:
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        return profile_csv(path, ext)
    if ext in (".xlsx", ".xlsm"):
        return profile_excel(path)
    if ext == ".json":
        return profile_json(path)
    if ext == ".xls":
        raise ApiError("bad_request", "暂不支持旧版 .xls，请先另存为 .xlsx 或 .csv")
    raise ApiError("bad_request", f"不支持的表格格式：{ext}")


def read_page(path: Path, profile: dict, sheet: str | None, offset: int, limit: int) -> dict:
    """按页读取表格内容（预览分页）。offset 上限防止大文件反复整读。"""
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    if offset > 500_000:
        raise ApiError("bad_request", "偏移量过大，请使用统计与分布查看大文件")
    sheets = profile.get("sheets") or []
    meta = next((s for s in sheets if s["name"] == sheet), sheets[0] if sheets else None)
    if meta is None:
        raise ApiError("not_found", "没有可读取的表")
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        df = pd.read_csv(path, sep=profile.get("delimiter", ","), encoding=profile.get("encoding", "utf-8"), dtype=str,
                         skiprows=range(1, offset + 1) if offset else None, nrows=limit, on_bad_lines="skip",
                         encoding_errors="replace")
    elif ext in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, sheet_name=meta["name"], skiprows=range(1, offset + 1) if offset else None,
                           nrows=limit, dtype=object)
    else:
        df = pd.read_json(path).astype(object).iloc[offset: offset + limit]
    cols = [str(c) for c in df.columns]
    return {"sheet": meta["name"], "columns": cols, "offset": offset, "limit": limit, "total_rows": meta.get("rows"),
            "rows": [[_clean(v) for v in row] for row in df.itertuples(index=False, name=None)]}


def profile_brief(profile: dict, *, max_cols: int = 40) -> str:
    """给 AI 的紧凑文字摘要（只含结构与统计，不含原始数据行之外的大量内容）。"""
    lines = [f"文件格式：{profile.get('format')}"]
    for sh in profile.get("sheets", []):
        approx = "（约）" if sh.get("rows_approx") else ""
        lines.append(f"表“{sh['name']}”：{sh['rows']}{approx} 行，{len(sh['columns'])} 列" + ("（仅扫描了前一部分行）" if sh.get("partial") else ""))
        for c in sh["columns"][:max_cols]:
            desc = f"- {c['name']}：{c['dtype']}，缺失 {c['missing_rate']:.1%}，不同取值 {c['unique']}"
            st = c.get("stats")
            if st and c["dtype"] in ("integer", "float"):
                desc += f"，范围 [{st.get('min')}, {st.get('max')}]，均值 {st.get('mean'):.4g}" if st.get("mean") is not None else ""
            elif c.get("top_values"):
                desc += "，常见值：" + "、".join(f"{t['value']}({t['count']})" for t in c["top_values"][:4])
            lines.append(desc)
        prev = sh.get("preview", {}).get("rows", [])[:3]
        if prev:
            lines.append("前几行样本：" + " | ".join(str(r)[:160] for r in prev))
    return "\n".join(lines)


def random_sample_rows(profile: dict, n: int = 3) -> list:
    rows = profile.get("sheets", [{}])[0].get("preview", {}).get("rows", [])
    return random.Random(0).sample(rows, min(n, len(rows)))
