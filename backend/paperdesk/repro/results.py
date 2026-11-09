"""C：导入用户自己跑出的结果，与论文里的报告数值逐项对比。

系统只陈述事实（差值、相对误差、是否在用户设定的容差/论文报告的波动范围内），不替用户断言“复现成功/失败”；
可能的原因只列出和事实相关的线索，推测一律标明“推测”。
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path

from ..context import AppContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import plan as plan_mod

MAX_BYTES = 2_000_000
MAX_METRICS = 5000
FILE_EXT = (".csv", ".tsv", ".json", ".txt", ".log", ".md")
NUM_RX = re.compile(r"^\s*([+-]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+-]?\d+)?)\s*(%)?\s*(?:(?:±|\+/-|\+-)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*%?)?\s*$")


# ---------------------------------------------------------------- 数值与格式
def parse_number(cell: object) -> tuple[float, float | None, str] | None:
    """'12.3' | '12.3%' | '12.3 ± 0.4' | 12.3 → (value, std, raw)；不是数字返回 None。"""
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return (float(cell), None, str(cell)) if math.isfinite(float(cell)) else None
    s = str(cell).strip()
    m = NUM_RX.match(s)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
        std = float(m.group(3)) if m.group(3) else None
    except ValueError:
        return None
    return (value, std, s) if math.isfinite(value) else None


def _mean_std(xs: list[float]) -> tuple[float, float | None]:
    mean = sum(xs) / len(xs)
    if len(xs) < 2:
        return mean, None
    return mean, math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def _flatten_json(data, prefix: str, out: list[dict]) -> None:
    if len(out) >= MAX_METRICS:
        return
    if isinstance(data, dict):
        # [{"metric": "acc", "value": 0.9}] 的单个元素
        if {"metric", "value"} <= {k.lower() for k in data}:
            low = {k.lower(): v for k, v in data.items()}
            num = parse_number(low["value"])
            if num:
                out.append({"name": f"{prefix}{low['metric']}".strip(), "value": num[0], "std": parse_number(low.get("std"))[0] if parse_number(low.get("std")) else num[1], "n": 1, "raw": num[2]})
            return
        for k, v in data.items():
            _flatten_json(v, f"{prefix}{k}." if isinstance(v, (dict, list)) else f"{prefix}{k}", out) if isinstance(v, (dict, list)) else _leaf(f"{prefix}{k}", v, out)
    elif isinstance(data, list):
        nums = [parse_number(x) for x in data]
        if data and all(n is not None for n in nums):
            mean, std = _mean_std([n[0] for n in nums])  # type: ignore[index]
            out.append({"name": prefix.rstrip("."), "value": mean, "std": std, "n": len(nums), "raw": f"{len(nums)} 个值的均值"})
        else:
            for i, v in enumerate(data):
                if isinstance(v, (dict, list)):
                    _flatten_json(v, f"{prefix}{i}.", out)
                else:
                    _leaf(f"{prefix}{i}", v, out)


def _leaf(name: str, value, out: list[dict]) -> None:
    num = parse_number(value)
    if num and len(out) < MAX_METRICS:
        out.append({"name": name, "value": num[0], "std": num[1], "n": 1, "raw": num[2]})


def _split_rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not re.match(r"^\s*\|?\s*[-:| ]+\|?\s*$", ln)]
    if not lines:
        return []
    sample = "\n".join(lines[:5])
    if "|" in sample:
        return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    for delim in ("\t", ",", ";"):
        if delim in sample:
            return [[c.strip() for c in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]
    return [re.split(r"\s{2,}|\s(?=[+-]?\d)", ln.strip()) for ln in lines]  # 粘贴的空格对齐表格


def parse_results(content: str, fmt: str | None = None) -> tuple[str, list[dict], list[str]]:
    """返回 (格式, 指标列表[{name,value,std,n,raw}], 警告)。解析不出任何数值时抛 ApiError。"""
    text = content.strip().lstrip("﻿")
    if not text:
        raise ApiError("bad_request", "没有内容")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ApiError("bad_request", "内容超过 2 MB")
    warnings: list[str] = []
    metrics: list[dict] = []
    detected = fmt if fmt in ("csv", "json", "text") else ("json" if text[0] in "{[" else "csv")
    if detected == "json":
        try:
            _flatten_json(json.loads(text), "", metrics)
        except ValueError as exc:
            raise ApiError("bad_request", f"不是合法的 JSON：{exc}") from exc
    else:
        rows = _split_rows(text)
        if len(rows) < 2:
            raise ApiError("bad_request", "没有解析出表格：至少需要一行表头和一行数据")
        header, body = rows[0], rows[1:]
        low = [h.lower() for h in header]
        if "metric" in low and "value" in low:  # 长格式
            mi, vi = low.index("metric"), low.index("value")
            si = low.index("std") if "std" in low else None
            for r in body:
                if len(r) <= max(mi, vi):
                    continue
                num = parse_number(r[vi])
                if num:
                    std = parse_number(r[si])[0] if si is not None and len(r) > si and parse_number(r[si]) else num[1]
                    metrics.append({"name": r[mi], "value": num[0], "std": std, "n": 1, "raw": num[2]})
        else:  # 宽格式
            label_col = all(parse_number(r[0]) is None for r in body if r) and len(header) > 1
            start = 1 if label_col else 0
            for j in range(start, len(header)):
                cells = [(r[0] if label_col else None, parse_number(r[j])) for r in body if len(r) > j]
                cells = [(lab, n) for lab, n in cells if n is not None]
                if not cells:
                    continue
                if label_col:
                    for lab, n in cells:
                        metrics.append({"name": f"{lab} / {header[j]}", "value": n[0], "std": n[1], "n": 1, "raw": n[2]})
                elif len(cells) == 1:
                    metrics.append({"name": header[j], "value": cells[0][1][0], "std": cells[0][1][1], "n": 1, "raw": cells[0][1][2]})
                else:
                    mean, std = _mean_std([n[0] for _l, n in cells])
                    metrics.append({"name": header[j], "value": mean, "std": std, "n": len(cells), "raw": f"{len(cells)} 行的均值"})
                    warnings.append(f"列“{header[j]}”有 {len(cells)} 行数值，已取均值与样本标准差；如果每行是不同设置，请改成带标签列的格式。")
    metrics = [m for m in metrics if m["name"]][:MAX_METRICS]
    if not metrics:
        raise ApiError("bad_request", "没有解析出任何数值指标。支持 CSV、TSV、粘贴的表格和 JSON。")
    return detected, metrics, warnings


# ---------------------------------------------------------------- 论文目标数值
METRIC_RX = re.compile(r"(?i)\b(accuracy|acc|f1(?:-score)?|precision|recall|auc|map|bleu|rouge-?\w*|perplexity|error rate|anc|score|reward|mse|rmse|mae|top-[15]|speedup|runtime|time)\b[^.\n]{0,50}?(\d+(?:\.\d+)?)\s*(%)?")
PM_RX = re.compile(r"([A-Za-z][\w\-./ ]{2,40}?)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:±|\+/-)\s*(\d+(?:\.\d+)?)")
ROW_LABEL_RX = re.compile(r"(?i)\b(avg|average|mean|score|accuracy|acc|f1|auc|anc|bleu|rouge|error|time|loss|reward)\b")
ROW_RX = re.compile(r"^\s*([A-Za-z][\w\-./ ()]{1,40}?)\s{1,}((?:[+-]?\d+(?:\.\d+)?\s*%?\s*){2,})$")


def target_candidates(app: AppContext, paper_id: str, limit: int = 80) -> list[dict]:
    """从论文文字里按规则找出可能的“报告数值”，供用户挑选并确认含义。不是结论，只是候选。"""
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        rows = conn.execute("SELECT ord, page, kind, text FROM passages WHERE paper_id = ? AND kind IN ('paragraph','caption','table','figure_text') ORDER BY ord", (paper_id,)).fetchall()
    out, seen = [], set()

    def add(r, label: str, value: float, std: float | None, quote: str, how: str, unit: str = "") -> None:
        key = (r["ord"], round(value, 6))  # 同一段落里相同的数值只保留一条（先加入的标签优先）
        if key in seen or len(out) >= limit:
            return
        seen.add(key)
        out.append({"ord": r["ord"], "page": r["page"], "kind": r["kind"], "label": label.strip()[:60], "value": value, "std": std, "unit": unit,
                    "quote": quote.strip()[:240], "how": how})

    for r in rows:
        text = r["text"]
        occ: dict[str, int] = {}
        for m in PM_RX.finditer(text):
            add(r, m.group(1), float(m.group(2)), float(m.group(3)), text[max(0, m.start() - 30):m.end() + 30], "mean±std")
        # 表格行放在指标词规则之前：同一个数两条规则都能抓到时，保留标签更完整的表格行
        if r["kind"] in ("table", "figure_text", "caption"):
            for ln in text.splitlines():
                m = ROW_RX.match(ln)
                # 图里的文字常是坐标轴刻度（“0.05 0.04 0.03…”）：图内文字只在行标签像指标行（Avg score、Accuracy…）时才算
                if m and (r["kind"] == "table" or ROW_LABEL_RX.search(m.group(1))):
                    for j, tok in enumerate(re.findall(r"[+-]?\d+(?:\.\d+)?", m.group(2)), 1):
                        add(r, f"{m.group(1)} #{j}", float(tok), None, ln, "table-row")
        for m in METRIC_RX.finditer(text):
            num, pct = m.group(2), bool(m.group(3))
            before = text[m.start(2) - 1] if m.start(2) > 0 else " "
            # 指标词后面的整数多半是引用编号、实例数或网络名里的数字（如 “(ANC)29”“100 instances”“Gnutella31”）：只接受小数或带 % 的数，且数字不能紧贴字母/右括号
            if ("." not in num and not pct) or before.isalnum() or before in ")]":
                continue
            # “Avg score 6.36 6.62 6.68 6.04”：指标词后面紧跟的一串小数是同一行的各列，一起列出来（标 #1、#2…），而不是只取第一个
            extra = re.match(r"((?:\s+\d+\.\d+){1,7})(?!\s*[.\d])", text[m.end(2):]) if "." in num else None
            if extra:
                # 同一段落里同一个指标词出现多次（表格的多行）：从第二次起加序号，避免名字重复
                occ[m.group(1).lower()] = occ.get(m.group(1).lower(), 0) + 1
                name = m.group(1) if occ[m.group(1).lower()] == 1 else f"{m.group(1)}（第 {occ[m.group(1).lower()]} 行）"
                for j, tok in enumerate([num, *extra.group(1).split()], 1):
                    add(r, f"{name} #{j}", float(tok), None, text[max(0, m.start() - 30):m.end(2) + len(extra.group(1)) + 10], "metric-row")
            else:
                add(r, m.group(1), float(num), None, text[max(0, m.start() - 30):m.end() + 30], "metric-word", "%" if pct else "")
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _target_row(app: AppContext, row) -> dict:
    verified = None
    if row["ord"] is not None and row["quote"]:
        with app.db.session() as conn:
            p = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND ord = ?", (row["paper_id"], row["ord"])).fetchone()
        verified = bool(p and _norm(row["quote"]) in _norm(p["text"]))
    return {"id": row["id"], "name": row["name"], "value": row["value"], "std": row["std"], "unit": row["unit"], "tol_rel": row["tol_rel"], "tol_abs": row["tol_abs"],
            "comparable": bool(row["comparable"]), "page": row["page"], "ord": row["ord"], "quote": row["quote"], "quote_verified": verified,
            "source": row["source"], "created_at": row["created_at"]}


def _float(v, field: str, *, required: bool = False, nonneg: bool = False) -> float | None:
    if v is None or v == "":
        if required:
            raise ApiError("bad_request", f"{field} 不能为空")
        return None
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise ApiError("bad_request", f"{field} 必须是数字") from exc
    if not math.isfinite(x) or (nonneg and x < 0):
        raise ApiError("bad_request", f"{field} 不合法")
    return x


def list_targets(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM repro_targets WHERE paper_id = ? ORDER BY created_at, rowid", (paper_id,)).fetchall()
    return [_target_row(app, r) for r in rows]


def save_target(app: AppContext, paper_id: str, body: dict, target_id: str | None = None) -> dict:
    name = str(body.get("name", "")).strip()[:100]
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        old = conn.execute("SELECT * FROM repro_targets WHERE id = ? AND paper_id = ?", (target_id, paper_id)).fetchone() if target_id else None
        if target_id and old is None:
            raise ApiError("not_found", "目标数值不存在", 404)
        merged = {**(dict(old) if old else {}), **body}
        name = str(merged.get("name", "")).strip()[:100]
        if not name:
            raise ApiError("bad_request", "名称不能为空")
        vals = (name, _float(merged.get("value"), "数值", required=True), _float(merged.get("std"), "标准差", nonneg=True), str(merged.get("unit", ""))[:12],
                _float(merged.get("tol_rel"), "相对容差", nonneg=True), _float(merged.get("tol_abs"), "绝对容差", nonneg=True),
                0 if merged.get("comparable") in (False, 0, "0") else 1, merged.get("page"), merged.get("ord"), str(merged.get("quote", ""))[:400],
                merged.get("source") if merged.get("source") in ("table", "text", "user") else "user")
        if old:
            conn.execute("UPDATE repro_targets SET name=?, value=?, std=?, unit=?, tol_rel=?, tol_abs=?, comparable=?, page=?, ord=?, quote=?, source=? WHERE id=?", (*vals, target_id))
            tid = target_id
        else:
            tid = new_id()
            conn.execute("INSERT INTO repro_targets(id, paper_id, name, value, std, unit, tol_rel, tol_abs, comparable, page, ord, quote, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (tid, paper_id, *vals, utcnow()))
        row = conn.execute("SELECT * FROM repro_targets WHERE id = ?", (tid,)).fetchone()
    return _target_row(app, row)


def delete_target(app: AppContext, paper_id: str, target_id: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM repro_targets WHERE id = ? AND paper_id = ?", (target_id, paper_id))
        if cur.rowcount == 0:
            raise ApiError("not_found", "目标数值不存在", 404)


# ---------------------------------------------------------------- 结果导入
def _run_row(row) -> dict:
    return {"id": row["id"], "paper_id": row["paper_id"], "repo_id": row["repo_id"], "name": row["name"], "source_format": row["source_format"],
            "metrics": loads(row["metrics_json"], []), "env": loads(row["env_json"], {}), "notes": row["notes"], "conclusion": row["conclusion"], "created_at": row["created_at"]}


def _clean_env(env) -> dict:
    if not isinstance(env, dict):
        return {}
    return {str(k)[:40]: str(v)[:300] for k, v in list(env.items())[:40]}


def read_local_file(path: str) -> str:
    p = Path(path).expanduser()
    if p.suffix.lower() not in FILE_EXT:
        raise ApiError("bad_request", f"只支持导入 {'、'.join(FILE_EXT)} 文件")
    try:
        if not p.is_file():
            raise ApiError("not_found", "文件不存在", 404)
        if p.stat().st_size > MAX_BYTES:
            raise ApiError("bad_request", "文件超过 2 MB")
        return p.read_bytes().decode("utf-8-sig", "replace")
    except OSError as exc:
        raise ApiError("bad_request", f"无法读取文件：{exc.strerror or exc}") from exc


def create_run(app: AppContext, paper_id: str, body: dict) -> dict:
    content = body.get("content")
    if not content and body.get("path"):
        content = read_local_file(str(body["path"]))
    fmt, metrics, warnings = parse_results(str(content or ""), body.get("format"))
    name = str(body.get("name", "")).strip()[:100] or f"复现结果 {utcnow()[:16].replace('T', ' ')}"
    repo_id = body.get("repo_id")
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        if repo_id and conn.execute("SELECT 1 FROM code_repos WHERE id = ? AND paper_id = ?", (repo_id, paper_id)).fetchone() is None:
            raise ApiError("bad_request", "代码仓库不属于这篇论文")
        rid = new_id()
        conn.execute("INSERT INTO repro_runs(id, paper_id, repo_id, name, source_format, metrics_json, env_json, notes, conclusion, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (rid, paper_id, repo_id or None, name, fmt, dumps(metrics), dumps(_clean_env(body.get("env"))), str(body.get("notes", ""))[:4000], "", utcnow()))
        row = conn.execute("SELECT * FROM repro_runs WHERE id = ?", (rid,)).fetchone()
    return {**_run_row(row), "warnings": warnings}


def create_run_from_metrics(app: AppContext, paper_id: str, repo_id: str | None, name: str, fmt: str, metrics: list[dict], env: dict, notes: str = "") -> dict:
    """隔离执行器用：指标已经由执行器从日志/输出文件里解析出来，带 source（哪次运行、哪个文件、哪一行）。"""
    with app.db.session() as conn:
        rid = new_id()
        conn.execute("INSERT INTO repro_runs(id, paper_id, repo_id, name, source_format, metrics_json, env_json, notes, conclusion, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (rid, paper_id, repo_id, name, fmt, dumps(metrics[:MAX_METRICS]), dumps(_clean_env(env)), notes[:4000], "", utcnow()))
        row = conn.execute("SELECT * FROM repro_runs WHERE id = ?", (rid,)).fetchone()
    return {**_run_row(row), "warnings": []}


def list_runs(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM repro_runs WHERE paper_id = ? ORDER BY created_at DESC, rowid DESC", (paper_id,)).fetchall()
    return [{**_run_row(r), "metrics": None, "metric_count": len(loads(r["metrics_json"], []))} for r in rows]


def get_run(app: AppContext, run_id: str) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT * FROM repro_runs WHERE id = ?", (run_id,)).fetchone()
        maps = conn.execute("SELECT target_id, metric FROM repro_mappings WHERE run_id = ?", (run_id,)).fetchall()
    if row is None:
        raise ApiError("not_found", "结果不存在", 404)
    return {**_run_row(row), "mappings": {m["target_id"]: m["metric"] for m in maps}}


def update_run(app: AppContext, run_id: str, patch: dict) -> dict:
    run = get_run(app, run_id)
    with app.db.session() as conn:
        conn.execute("UPDATE repro_runs SET name=?, env_json=?, notes=?, conclusion=? WHERE id=?",
                     (str(patch.get("name", run["name"])).strip()[:100] or run["name"], dumps(_clean_env(patch["env"])) if "env" in patch else dumps(run["env"]),
                      str(patch.get("notes", run["notes"]))[:4000], str(patch.get("conclusion", run["conclusion"]))[:4000], run_id))
    return get_run(app, run_id)


def delete_run(app: AppContext, run_id: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM repro_runs WHERE id = ?", (run_id,)).rowcount == 0:
            raise ApiError("not_found", "结果不存在", 404)


def set_mapping(app: AppContext, run_id: str, target_id: str, metric: str | None) -> dict:
    run = get_run(app, run_id)
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM repro_targets WHERE id = ? AND paper_id = ?", (target_id, run["paper_id"])).fetchone() is None:
            raise ApiError("not_found", "目标数值不存在", 404)
        if metric:
            if metric not in {m["name"] for m in run["metrics"]}:
                raise ApiError("bad_request", "这份结果里没有这个指标")
            conn.execute("INSERT INTO repro_mappings(run_id, target_id, metric) VALUES (?,?,?) ON CONFLICT(run_id, target_id) DO UPDATE SET metric = excluded.metric", (run_id, target_id, metric))
        else:
            conn.execute("DELETE FROM repro_mappings WHERE run_id = ? AND target_id = ?", (run_id, target_id))
    return get_run(app, run_id)


# ---------------------------------------------------------------- 对比
def _auto_match(target_name: str, metrics: list[dict]) -> dict | None:
    t = _norm(target_name)
    if not t:
        return None
    exact = [m for m in metrics if _norm(m["name"]) == t]
    if len(exact) == 1:
        return exact[0]
    part = [m for m in metrics if len(t) >= 3 and (t in _norm(m["name"]) or _norm(m["name"]) in t) and len(_norm(m["name"])) >= 3]
    return part[0] if len(part) == 1 else None  # 有歧义就不自动对应，交给用户


def compare(app: AppContext, run_id: str) -> dict:
    run = get_run(app, run_id)
    targets = list_targets(app, run["paper_id"])
    by_name = {m["name"]: m for m in run["metrics"]}
    items = []
    for t in targets:
        metric, how = None, None
        if t["id"] in run["mappings"]:
            metric, how = by_name.get(run["mappings"][t["id"]]), "user"
        else:
            metric = _auto_match(t["name"], run["metrics"])
            how = "auto" if metric else None
        item = {"target": t, "metric": metric, "mapping": how, "diff": None, "rel_error": None, "status": "unmapped", "notes": []}
        if not t["comparable"]:
            item["status"] = "not_comparable"
            item["notes"].append("你把这一项标记为“指标定义不同，无法比较”。")
        elif metric:
            diff = metric["value"] - t["value"]
            rel = abs(diff) / abs(t["value"]) if t["value"] else None
            item.update(diff=diff, rel_error=rel)
            if t["tol_abs"] is not None or t["tol_rel"] is not None:
                ok = (t["tol_abs"] is not None and abs(diff) <= t["tol_abs"]) or (t["tol_rel"] is not None and rel is not None and rel <= t["tol_rel"])
                item["status"] = "within_tolerance" if ok else "outside_tolerance"
            elif t["std"] is not None:
                item["status"] = "within_reported_range" if abs(diff) <= t["std"] else "outside_reported_range"
                item["notes"].append("没有设定容差，按论文报告的标准差判断。")
            else:
                item["status"] = "no_tolerance"
                item["notes"].append("没有设定容差，也没有论文的标准差，只给出差值，是否可接受由你判断。")
            if t["unit"] == "%" and metric["raw"] and "%" not in str(metric["raw"]) and metric["value"] <= 1.0 < t["value"]:
                item["notes"].append("论文值带 %，而你的值看起来是 0–1 的小数，请确认单位是否一致，系统没有自动换算。")
        items.append(item)
    summary: dict[str, int] = {}
    for it in items:
        summary[it["status"]] = summary.get(it["status"], 0) + 1
    return {"run_id": run_id, "items": items, "summary": summary, "reasons": _reasons(app, run, items),
            "verdict": None, "note": "系统不会判断“复现成功”或“复现失败”，这里只列出差值与可能相关的线索；结论请你自己写在“结论”里。"}


def _reasons(app: AppContext, run: dict, items: list[dict]) -> list[dict]:
    """与差异相关的线索：事实（有记录依据）与推测（明确标注）分开。"""
    out: list[dict] = []
    off = [i for i in items if i["status"] in ("outside_tolerance", "outside_reported_range")]
    env = {k.lower(): v for k, v in run["env"].items()}
    if off:
        if not any(k in env for k in ("seed", "随机种子", "random_seed")):
            out.append({"kind": "conjecture", "text": "你没有记录随机种子；随机性可能造成数值波动（推测）。"})
        out.append({"kind": "conjecture", "text": "数据划分/预处理、框架与 CUDA 版本、评估协议的差异都可能造成差异（推测）。"})
    if run["repo_id"]:
        with app.db.session() as conn:
            repo = conn.execute("SELECT commit_sha FROM code_repos WHERE id = ?", (run["repo_id"],)).fetchone()
        sha = (env.get("commit") or env.get("commit_sha") or "").strip()
        if repo and repo["commit_sha"] and sha and not repo["commit_sha"].startswith(sha[:7]) and not sha.startswith(repo["commit_sha"][:7]):
            out.append({"kind": "fact", "text": f"你记录的 commit（{sha[:10]}）与评估时的仓库 commit（{repo['commit_sha'][:10]}）不同。"})
    return out


STATUS_LABEL = {"within_tolerance": "在容差内", "outside_tolerance": "超出容差", "within_reported_range": "在论文报告的波动范围内", "outside_reported_range": "超出论文报告的波动范围",
                "no_tolerance": "未设容差", "unmapped": "没有对应指标", "not_comparable": "无法比较"}


def _fmt(x: float | None, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}g}"


def _src(m: dict | None) -> str:
    src = (m or {}).get("source")
    if not src:
        return "你导入的内容" if m else "—"
    return f"运行 {src['run_id']} 的 {src['path']}" + (f" 第 {src['line']} 行" if src.get("line") else "")


def report_markdown(app: AppContext, run_id: str) -> dict:
    run = get_run(app, run_id)
    cmp = compare(app, run_id)
    out = [f"# 复现报告：{run['name']}", "", "> 由 Marginalia 生成。对比只陈述差值，**不判断复现成功与否**；结论由你自己填写。", "", "## 环境与命令", ""]
    if run["env"]:
        out += [f"- {k}：{v}" for k, v in run["env"].items()]
    else:
        out.append("- 你没有记录环境信息，建议补上 Python、框架、CUDA 版本、随机种子、commit 和命令")
    if run["repo_id"]:
        plan = plan_mod.get_plan(app, run["repo_id"])["plan"]
        steps = [s for s in (plan["steps"] if plan else []) if s["command"] and s["approval"] == "approved"]
        if steps:
            out += ["", "已批准的计划步骤：", ""] + [f"- {s['title']}：`{s['command']}`" for s in steps]
    out += ["", "## 数值对比", "", "| 指标 | 论文值 | 你的值 | 差值 | 相对误差 | 状态 | 论文出处 | 你的值的来源 |", "|---|---|---|---|---|---|---|---|"]
    for it in cmp["items"]:
        t, m = it["target"], it["metric"]
        paper = _fmt(t["value"]) + (f" ± {_fmt(t['std'])}" if t["std"] is not None else "") + t["unit"]
        out.append(f"| {t['name']} | {paper} | {_fmt(m['value']) if m else '—'} | {_fmt(it['diff'])} | {_fmt(it['rel_error'] * 100 if it['rel_error'] is not None else None, 3)}{'%' if it['rel_error'] is not None else ''} | "
                   f"{STATUS_LABEL[it['status']]} | {'第 ' + str(t['page']) + ' 页' if t['page'] else '—'} | {_src(m)} |")
    if cmp["reasons"]:
        out += ["", "## 可能相关的线索", ""] + [f"- （{'事实' if r['kind'] == 'fact' else '推测'}）{r['text']}" for r in cmp["reasons"]]
    out += ["", "## 你的结论", "", run["conclusion"] or "待你填写", ""]
    return {"filename": "复现报告.md", "format": "md", "content": "\n".join(out)}
