"""表格识别结果的二次核对：位置感知核对 + 类型一致性告警。

`validate.validate_table` 只检查“这个数字在 PDF 文字层里出现过没有”，不看它在哪一行哪一列：模型把相邻行的数填错了位置
（比如把“平均度 5.72”填进“直径”那一格）也会被判成“已核对”。这里用文字层每个词的坐标补上两层核对：

1. 位置核对：行标签单元格定位出这一行在文字层里的那条文本行（y 区间）；数值必须出现在这条文本行里（行），
   并且它的 x 位置要和同一列其它单元格的位置一致（列）。出现在文字层里、但不在对应行列的值标为“位置存疑”
   （与“文字层里找不到”的 `unverified_cells` 区分开，记入 `misplaced_cells`），并计入核对率。
2. 类型一致性：同一行/列的单元格类型应该自洽——整数型的行或列里冒出小数、行标签通常是整数（diameter、nodes…）却是小数、
   百分比列里出现无单位数字、同一行里小数重复——给出温和告警。只告警，不改数值。

纯函数在前（便于测试），读取文字层的 `fetch_words` 在最后。
"""
from __future__ import annotations

import math
import re
import statistics
import unicodedata

import pymupdf

from ..context import AppContext
from ..parsing.render import PDF_LOCK
from . import validate as V
from .images import paper_pdf_path

Word = tuple[float, float, float, float, str]  # x0, y0, x1, y1, text

LINE_TOL = 3.5
PURE_NUMBER = re.compile(r"^[-+−–]?\d[\d,]*(?:\.\d+)?%?$")


def _norm(text: str) -> str:
    """词的比较用写法：NFKC 先把连字拆开（文字层里的 ﬃ / ﬁ 对应模型写的 ffi / fi），再去掉标点、转小写。"""
    return V.norm_text(unicodedata.normalize("NFKC", text))


def _expand_overprint(text: str) -> str:
    """粗体“重复叠印”造成的 3.343.343.34 → 3.34（同一个值被重复写了 2–3 次）。"""
    for k in (3, 2):
        if len(text) >= 2 * k and len(text) % k == 0:
            piece = text[: len(text) // k]
            if piece * k == text and re.search(r"\d", piece):
                return piece
    return text


def _num_key(text: str) -> str | None:
    t = _expand_overprint(text.strip()).replace("−", "-").replace("–", "-").replace(",", "")
    t = t.rstrip("%")
    return t if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", t) else None


def _cell_key(cell: str) -> str | None:
    c = cell.strip()
    return _num_key(c) if PURE_NUMBER.match(c) else None


def _lines(words: list[Word]) -> list[list[Word]]:
    """按 y 中心把词聚成文本行（自上而下），每行内按 x 排序。"""
    out: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        yc = (w[1] + w[3]) / 2
        if out and abs(yc - sum((x[1] + x[3]) / 2 for x in out[-1]) / len(out[-1])) <= LINE_TOL:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in out]


def _row_line(label: str, lines: list[list[Word]], after: int) -> int | None:
    """行标签对应的文本行下标：标签的各个词要出现在这一行最前面的几个词里；行必须在上一行之后（表格自上而下）。"""
    toks = [t for t in (_norm(x) for x in label.split()) if t]
    if not toks:
        return None
    need = max(1, math.ceil(0.6 * len(toks)))
    best, best_score = None, 0
    for i in range(after + 1, len(lines)):
        head = [_norm(w[4]) for w in lines[i][: len(toks) + 2]]
        score = sum(1 for t in toks if t in head or any(h.startswith(t) or (t.startswith(h) and len(h) >= 3) for h in head))
        if score >= need and score > best_score:
            best, best_score = i, score
            if score == len(toks):
                break
    return best


def positional_check(words: list[Word], columns: list[str], rows: list[list[str]]) -> dict:
    """返回 {misplaced: [[行,列]…], checked: 位置核对过的单元格数, located_rows: 在文字层里定位到的行数}。"""
    lines = _lines(words)
    table_numbers = {k for w in words if (k := _num_key(w[4])) is not None}
    located: dict[int, int] = {}
    prev = -1
    for r, row in enumerate(rows):
        label = row[0] if row else ""
        if not label or _cell_key(label) is not None:
            continue
        li = _row_line(label, lines, prev)
        if li is not None:
            located[r] = li
            prev = li
    # 第一遍：各列的 x 锚点（该列里能在行文本里找到的数值的 x 中心的中位数）
    col_x: dict[int, list[float]] = {}
    hits: dict[tuple[int, int], list[float]] = {}
    for r, li in located.items():
        for c, cell in enumerate(rows[r]):
            if c == 0:
                continue
            key = _cell_key(cell)
            if key is None:
                continue
            xs = [(w[0] + w[2]) / 2 for w in lines[li] if _num_key(w[4]) == key]
            if xs:
                hits[(r, c)] = xs
                if len(xs) == 1:
                    col_x.setdefault(c, []).append(xs[0])
    anchor = {c: statistics.median(v) for c, v in col_x.items() if len(v) >= 2}
    ordered = sorted(anchor.values())
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > 1]
    tol = max(12.0, min(60.0, 0.5 * min(gaps))) if gaps else 40.0
    misplaced: list[list[int]] = []
    checked = 0
    for r, li in located.items():
        for c, cell in enumerate(rows[r]):
            if c == 0:
                continue
            key = _cell_key(cell)
            if key is None:
                continue
            checked += 1
            xs = hits.get((r, c))
            if xs is None:
                if key in table_numbers:  # 值在文字层里有，但不在这一行：典型的“填错了行”
                    misplaced.append([r, c])
                continue  # 整个表的文字层里都没有：交给 validate 的“未核对”
            if c in anchor and min(abs(x - anchor[c]) for x in xs) > tol:
                misplaced.append([r, c])  # 在这一行，但不在这一列的位置
    return {"misplaced": misplaced, "checked": checked, "located_rows": len(located)}


def _num_info(cell: str) -> tuple[bool, bool, int] | None:
    """(是整数, 带百分号, 小数位数)；不是纯数字单元格返回 None。"""
    c = cell.strip()
    if not PURE_NUMBER.match(c):
        return None
    body = c.rstrip("%").replace(",", "").replace("−", "-").replace("–", "-")
    dec = len(body.split(".")[1]) if "." in body else 0
    return dec == 0, c.endswith("%"), dec


# 通用的行/列名：本来就混着整数、小数、带单位的值，不做整数一致性告警
GENERIC_LABELS = {
    "value", "values", "result", "results", "score", "scores", "parameter", "parameters", "param", "params", "metric", "metrics",
    "setting", "settings", "default", "description", "name", "method", "methods", "item", "items", "type", "unit", "units", "notes", "note",
    "hyperparameter", "hyperparameters", "statistics", "dataset", "datasets",
}
# 行/列标签明显是计数、整数度量（diameter、number、nodes、edges、maximum degree、size…）；平均值、比率、系数不算
COUNT_LABEL = re.compile(
    r"\b(diameter|number|num|count|counts|nodes?|edges?|vertices|size|layers?|epochs?|steps?|iterations?|dimensions?|"
    r"(?:max(?:imum)?|min(?:imum)?)\s+degree)\b", re.I)
NOT_COUNT = re.compile(r"\b(average|avg|mean|ratio|rate|coefficient|length|percent|percentage|fraction|density|score)\b", re.I)


def _is_generic(label: str) -> bool:
    toks = [t for t in re.split(r"\W+", unicodedata.normalize("NFKC", label).lower()) if t]
    return not toks or all(t in GENERIC_LABELS for t in toks)


def _is_count_label(label: str) -> bool:
    lab = unicodedata.normalize("NFKC", label)
    return not _is_generic(lab) and bool(COUNT_LABEL.search(lab)) and not NOT_COUNT.search(lab)


def consistency_warnings(columns: list[str], rows: list[list[str]]) -> list[str]:
    """类型一致性：只给温和的提示（可能是识别错误，也可能原文如此），不改任何数值。最多 6 条。

    规则故意很紧，避免把“本来就混着整数和小数的列”（超参数表的 Value 列）误报：
    - 行/列标签明显是计数、整数度量（diameter、number、nodes、edges、maximum degree、size…）而单元格是小数；
    - 或者整行/整列有 ≥5 个数值、≥90% 是整数、只有 1 个小数，并且这个小数与表里另一处的值完全相同（疑似复制到了错行）；
    - 整行/整列有 ≥5 个数值、≥80% 带百分号，却有一个没有单位；
    - 一行里有 ≥5 个数值，同一个小数（≥2 位小数）出现了 ≥2 次。
    通用的行/列名（Value、Result、Score、Parameter…）不做整数一致性告警。
    """
    out: list[str] = []
    cells_all: dict[str, list[tuple[int, int]]] = {}
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            k = _num_key(v) if c > 0 else None
            if k is not None:
                cells_all.setdefault(k, []).append((r, c))

    def where(r: int, c: int) -> str:
        return (rows[r][0] if rows[r] else "").strip() or f"第 {r + 1} 行"

    def scan(label: str, who: str, cells: list[tuple[int, int, str]]) -> None:
        nums = [(r, c, v, i) for r, c, v in cells if (i := _num_info(v)) is not None]
        if not nums:
            return
        generic = _is_generic(label)
        if not generic and _is_count_label(label):
            for r, c, v, i in nums:
                if not i[0] and not i[1]:
                    out.append(f"{who}通常是整数，但「{v}」是小数。可能是识别错误，也可能原文如此，请对照原图。")
        if len(nums) >= 5 and not generic:
            ints = [x for x in nums if x[3][0]]
            decs = [x for x in nums if not x[3][0] and not x[3][1]]
            if len(ints) / len(nums) >= 0.9 and len(decs) == 1:
                r, c, v, _i = decs[0]
                key = _num_key(v)
                others = [p for p in cells_all.get(key or "", []) if p != (r, c)]
                if others:
                    r2, c2 = others[0]
                    out.append(f"{who}基本都是整数，但「{v}」是小数，并且与「{where(r2, c2)}」里的值相同，可能复制到了错行；也可能原文如此，请对照原图。")
            pct = [x for x in nums if x[3][1]]
            if len(pct) / len(nums) >= 0.8:
                for r, c, v, i in nums:
                    if not i[1]:
                        out.append(f"{who}的单元格大多带百分号，但「{v}」没有单位，请核对。")

    for r, row in enumerate(rows):  # 按行
        label = (row[0] if row else "").strip()
        scan(label, f"「{label or f'第 {r + 1} 行'}」这一行", [(r, c, v) for c, v in enumerate(row) if c > 0])
        infos = [(c, v, i) for c, v in enumerate(row) if c > 0 and (i := _num_info(v)) is not None]
        if len(infos) >= 5:
            seen: dict[str, int] = {}
            for _c, v, i in infos:
                if i[2] >= 2:
                    seen[v.strip()] = seen.get(v.strip(), 0) + 1
            for v, n in seen.items():
                if n >= 2:
                    out.append(f"「{label or f'第 {r + 1} 行'}」这一行里「{v}」出现了 {n} 次，可能是把相邻单元格的数复制错了。")
    ncols = max((len(r) for r in rows), default=0)
    for c in range(1, ncols):  # 按列
        head = (columns[c] if c < len(columns) else "").strip()
        scan(head, f"「{head or f'第 {c + 1} 列'}」这一列", [(r, c, row[c]) for r, row in enumerate(rows) if c < len(row)])
    dedup: list[str] = []
    for m in out:
        if m not in dedup:
            dedup.append(m)
    return dedup[:6]


def refine_data(data: dict, words: list[Word] | None) -> dict:
    """把位置核对和类型一致性并进识别结果：`misplaced_cells`、核对率（位置存疑也算没核对上）、告警。就地修改并返回。"""
    columns, rows = data.get("columns", []), data.get("rows", [])
    unverified = {tuple(x) for x in data.get("unverified_cells", [])}
    data["misplaced_cells"] = []
    warnings = list(data.get("warnings", []))
    if words:
        res = positional_check(words, columns, rows)
        mis = [x for x in res["misplaced"] if tuple(x) not in unverified]
        data["misplaced_cells"] = mis
        if mis:
            warnings.append(f"{len(mis)} 个单元格位置存疑：这个值在 PDF 文字层里出现过，但不在对应的行/列位置，可能填错了格子，请对照原图。")
        total = sum(1 for row in rows for cell in row if cell and cell != "?")
        if data.get("verified_ratio") is not None and total:
            data["verified_ratio"] = round(max(0.0, 1 - (len(unverified) + len(mis)) / total), 3)
    warnings += [w for w in consistency_warnings(columns, rows) if w not in warnings]
    data["warnings"] = warnings
    return data


def fetch_words(app: AppContext, paper_id: str, page: int, bbox: tuple[float, float, float, float], pad: float = 3.0) -> list[Word]:
    """表格区域内文字层的词（带坐标，PDF point）。失败返回空列表（调用方退回原来的核对）。"""
    try:
        path = paper_pdf_path(app, paper_id)
        with PDF_LOCK:
            doc = pymupdf.open(path)
            try:
                if not 1 <= page <= doc.page_count:
                    return []
                clip = pymupdf.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
                raw = doc[page - 1].get_text("words", clip=clip)
            finally:
                doc.close()
        return [(w[0], w[1], w[2], w[3], w[4]) for w in raw]
    except Exception:  # noqa: BLE001  取不到文字层不影响识别结果本身
        return []


def refine(app: AppContext, paper_id: str, fig: dict, data: dict) -> dict:
    words = fetch_words(app, paper_id, fig["page"], fig["bbox"]) if fig.get("bbox") and fig.get("page") else []
    return refine_data(data, words)
