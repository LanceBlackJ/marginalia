"""整篇论文的表格识别：打开文字阅读器时在后台把所有表格转成结构化的行列数据，供界面直接渲染成表格。

解析阶段没有单独的“表格区域”，表格主体只是图注（“Table 2: …”）后面一串零散的短文字段落。所以这里先从图注出发，
推出表格的位置（图注之后同一页上连续的“行文字”，遇到长句子、标题、下一个图注就结束），再：
- 如果这些行文字本身就带制表符或“ | ”分隔、列数一致，直接按文字建表（不调用模型）；
- 否则把表格区域截图发给视觉模型转录（`figures._do_table`），并逐个单元格与 PDF 文字层核对，核对不上的标记出来。
发给云端的截图记入隐私日志（includes.images=true）并计入用量；单个表失败只记为失败，不影响其它表。
"""
from __future__ import annotations

import re

from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..db import register_schema
from ..jobs import JobCancelled, JobContext, JobManager
from ..util import ApiError, dumps, loads, utcnow
from ..parsing import datablocks
from . import figures, layout_tables
from .images import crop_png, crop_png_fit
from .table_check import fetch_words

register_schema("vision_tables", """
CREATE TABLE IF NOT EXISTS table_analysis (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  caption_ord INTEGER NOT NULL,
  status TEXT NOT NULL,            -- pending | running | ready | failed
  source TEXT,                     -- text（行文字自带分隔符，直接建表） | layout（文字层词坐标重建） | vision（视觉模型转录）
  data_json TEXT,
  error TEXT,
  model TEXT,
  job_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, caption_ord)
);
""")

JOB_TYPE = "tables_analysis"
CAPTION_RE = re.compile(r"^\s*((?:Supplementary |Extended Data )?)(?:Table|TABLE)\s+([A-Z]?\d+(?:\.\d+)*[A-Za-z]?)\s*[:.|]")
# 有的期刊（Springer 等）的图注编号后面没有冒号或句点：“Table 2  Representative applications…”。
# 这种写法只对解析阶段已经认定为图注（kind=caption）的段落放宽，正文里的 “Table 2 shows …” 不会被当成表题。
CAPTION_LOOSE_RE = re.compile(r"^\s*((?:Supplementary |Extended Data )?)(?:Table|TABLE)\s+([A-Z]?\d+(?:\.\d+)*[A-Za-z]?)\s+\S")
OTHER_CAPTION_LOOSE_RE = re.compile(r"^\s*(?:(?:Supplementary |Extended Data )?(?:Fig\.|Figure|FIGURE|Table|TABLE))\s+[A-Z]?\d+(?:\.\d+)*[A-Za-z]?\s+\S")
OTHER_CAPTION_RE = re.compile(r"^\s*(?:(?:Supplementary |Extended Data )?(?:Fig\.|Figure|FIGURE|Table|TABLE))\s+[A-Z]?\d+(?:\.\d+)*[A-Za-z]?\s*[:.|]")
BODY_KINDS = {"paragraph", "figure_text", "caption", "table"}
MAX_BODY = 120
MIN_ROWS = 2
PAD = 4.0


def _label(m: re.Match) -> str:
    return f"{m.group(1)}Table {m.group(2)}".strip()


def _stops(p: dict) -> bool:
    """这一段不再属于表格主体：别的图注、标题、参考文献、公式，或一整句的正文。"""
    if p["kind"] not in BODY_KINDS or OTHER_CAPTION_RE.match(p["text"]):
        return True
    if p["kind"] == "caption" and OTHER_CAPTION_LOOSE_RE.match(p["text"]):
        return True
    t = p["text"].strip()
    return len(t) > 180 and bool(re.search(r"[.!?。！？]\s*$", t))


def _walk(passages: list[dict], start: int, step: int, page: int) -> list[dict]:
    out: list[dict] = []
    i = start + step
    while 0 <= i < len(passages) and len(out) < MAX_BODY:
        p = passages[i]
        if p["page"] != page or _stops(p):
            break
        out.append(p)
        i += step
    return out if step > 0 else out[::-1]


def _enough(body: list[dict]) -> bool:
    """够不够当表体：至少两段；或者只有一段、但有三行以上那么高（整张表排成了一个段落）。"""
    if len(body) >= MIN_ROWS:
        return True
    return len(body) == 1 and body[0]["bbox"][3] - body[0]["bbox"][1] >= 36.0 and len(body[0]["text"]) <= 2500


GEO_GAP = 30.0  # 按位置找表体时，相邻两行之间最大的空隙（pt）
# “Table A9 – continued from previous page”：续页的题注，常常和表头、表体连在同一个段落里
CONT_CAPTION_RE = re.compile(r"^\s*((?:Supplementary |Extended Data )?)(?:Table|TABLE)\s+([A-Z]?\d+(?:\.\d+)*[A-Za-z]?)\s*[–—-]?\s*\(?(?:continued|cont\.?)")
CONT_RE = re.compile(r"\((?:continued|cont\.?|续)\)|[,，]?\s*(?:continued|续表)(?![A-Za-z])", re.I)


def _geometric_body(passages: list[dict], cap: dict) -> list[dict]:
    """表体和表题在序号上不相邻时（表体被标成“图内文字”、双栏排版把它挪到了页尾）：按位置找——
    表题上方或下方、同一栏里紧挨着的那些行就是表体。两侧都有就取离表题近的一侧。"""
    page = cap["page"]
    cx0, cy0, cx1, cy1 = cap["bbox"]
    on_page = [p for p in passages if p["page"] == page]
    px0 = min((p["bbox"][0] for p in on_page), default=cx0)
    px1 = max((p["bbox"][2] for p in on_page), default=cx1)
    wide = (cx1 - cx0) > 0.6 * max(1.0, px1 - px0)
    lo, hi = (px0 - 4, px1 + 4) if wide else (cx0 - 10, cx1 + 10)

    def in_col(b: tuple) -> bool:
        return min(b[2], hi) - max(b[0], lo) > 0.5 * max(1.0, b[2] - b[0])

    cands = [p for p in on_page if p["ord"] != cap["ord"] and p["kind"] in ("paragraph", "figure_text", "table") and in_col(p["bbox"]) and not _stops(p)]

    def grow(seq: list[dict], edge: float, down: bool) -> list[dict]:
        out: list[dict] = []
        last = edge
        for p in seq:
            gap = (p["bbox"][1] - last) if down else (last - p["bbox"][3])
            if gap > GEO_GAP:
                break
            out.append(p)
            last = p["bbox"][3] if down else p["bbox"][1]
        return out

    below = grow(sorted((p for p in cands if p["bbox"][1] >= cy1 - 2), key=lambda p: p["bbox"][1]), cy1, True)
    above = grow(sorted((p for p in cands if p["bbox"][3] <= cy0 + 2), key=lambda p: -p["bbox"][3]), cy0, False)
    if _enough(below) and _enough(above):
        near_below = below[0]["bbox"][1] - cy1
        near_above = cy0 - above[0]["bbox"][3]
        return sorted(below if near_below <= near_above else above, key=lambda p: p["bbox"][1])
    if _enough(below):
        return below
    if _enough(above):
        return sorted(above, key=lambda p: p["bbox"][1])
    return []


def _union(body: list[dict]) -> tuple[float, float, float, float]:
    return (min(b["bbox"][0] for b in body) - PAD, min(b["bbox"][1] for b in body) - PAD,
            max(b["bbox"][2] for b in body) + PAD, max(b["bbox"][3] for b in body) + PAD)


def detect_tables(passages: list[dict]) -> list[dict]:
    """从按 ord 排好序的段落里找出表格：[{caption_ord, label, page, caption, body_ords, bbox, rows_text, parts}]。纯函数，便于测试。

    表体先按序号相邻去找，找不到再按位置找。一张表跨两页以上时合成一张：后面页的部分写在 parts 里（各自的 page、bbox、body_ords），
    后一页要么有“Table N (continued)”这样的图注，要么紧接上一页表尾、没有图注，直接接着排。"""
    out: list[dict] = []
    for i, p in enumerate(passages):
        m = CAPTION_RE.match(p["text"]) if p["kind"] in ("paragraph", "caption") else None
        if not m and p["kind"] == "caption":
            m = CAPTION_LOOSE_RE.match(p["text"])
        cont_only = False
        if not m and p["kind"] in ("paragraph", "caption"):
            m = CONT_CAPTION_RE.match(p["text"])
            cont_only = m is not None
        if not m:
            continue
        body = _walk(passages, i, +1, p["page"])
        if not _enough(body):  # 图注在表格下面的排版：往上找
            body = _walk(passages, i, -1, p["page"])
        if not _enough(body):
            body = _geometric_body(passages, p)
        if not _enough(body) and cont_only and p["bbox"][3] - p["bbox"][1] >= 36.0:
            body = [p]  # 续页的题注、表头和表体连成了一整段：这一段本身就是这一页的表
        if not _enough(body):
            continue
        out.append({"caption_ord": p["ord"], "label": _label(m), "page": p["page"], "caption": p["text"],
                    "body_ords": [b["ord"] for b in body], "bbox": _union(body), "rows_text": [b["text"] for b in body],
                    "caption_bbox": p["bbox"], "parts": []})
    _absorb_side_columns(passages, out)
    return _merge_pages(passages, out)


def _absorb_side_columns(passages: list[dict], tables: list[dict]) -> None:
    """通栏的文字表（Application | Domain | Work）被双栏版面切成左右两半：右边那一列会成了一个单独的段落，漏在表外、又在正文里重复出现。
    和表体同一页、完全落在表的纵向范围内、紧挨在表的右边（或左边）、而且第一行和表里某一段的第一行对齐的段落，是表的另一列，并进表体。"""
    taken = {o for t in tables for o in t["body_ords"]} | {t["caption_ord"] for t in tables}
    for t in tables:
        x0, y0, x1, y1 = t["bbox"]
        tops = [passages[i]["bbox"][1] for i in range(len(passages)) if passages[i]["ord"] in set(t["body_ords"])]
        for p in passages:
            if p["page"] != t["page"] or p["ord"] in taken or p["kind"] not in ("paragraph", "figure_text"):
                continue
            bx0, by0, bx1, by1 = p["bbox"]
            beside = bx0 >= x1 - 2 or bx1 <= x0 + 2
            if beside and by0 >= y0 - 3 and by1 <= y1 + 3 and any(abs(by0 - y) <= 3 for y in tops):
                t["body_ords"].append(p["ord"])
                t["rows_text"].append(p["text"])
                t["bbox"] = (min(x0, bx0), y0, max(x1, bx1), y1)
                x0, _, x1, _ = t["bbox"]
                taken.add(p["ord"])


def _merge_pages(passages: list[dict], tables: list[dict]) -> list[dict]:
    """把跨页的表接成一张。带 “(continued)” 的图注是续页，并入同名的那张正式的表（正式的表题可能在续页之前，也可能在之后）。"""
    groups: dict[str, list[dict]] = {}
    for t in tables:
        groups.setdefault(CONT_RE.sub("", t["label"]).strip(), []).append(t)
    drop: set[int] = set()
    for ts in groups.values():
        conts = [x for x in ts if CONT_RE.search(x["caption"][:160])]
        mains = [x for x in ts if x not in conts]
        if len(mains) == 1 and conts:
            main = mains[0]
        elif not mains and len(conts) > 1:
            main, conts = conts[0], conts[1:]  # 只剩续页的图注：最前面的当主表
        else:
            continue  # 同名的正式表不止一张、或没有续页：不合并
        for c in sorted(conts, key=lambda x: (x["page"], x["bbox"][1])):
            main["parts"].append({"page": c["page"], "bbox": c["bbox"], "body_ords": c["body_ords"], "rows_text": c["rows_text"], "caption_ord": c["caption_ord"]})
            main["body_ords"] = main["body_ords"] + c["body_ords"] + [c["caption_ord"]]
            drop.add(id(c))
    kept = [t for t in tables if id(t) not in drop]
    # 没有图注、直接接在上一页表尾后面的部分
    by_page: dict[int, list[dict]] = {}
    for p in passages:
        by_page.setdefault(p["page"], []).append(p)
    taken = {o for t in tables for o in t["body_ords"]}  # 已经是别的表的表体，不能再当续页
    page_last = {}  # 每页排在最下面的表：只有它才可能接到下一页
    for t in kept:
        pg = t["parts"][-1]["page"] if t["parts"] else t["page"]
        bt = t["parts"][-1]["bbox"][3] if t["parts"] else t["bbox"][3]
        if pg not in page_last or bt > page_last[pg][0]:
            page_last[pg] = (bt, t)
    for t in kept:
        last_page = t["parts"][-1]["page"] if t["parts"] else t["page"]
        if page_last.get(last_page, (0, None))[1] is not t:
            continue
        last_bbox = t["parts"][-1]["bbox"] if t["parts"] else t["bbox"]
        for _ in range(3):  # 最多再接三页
            cont = _headless_continuation(by_page, last_page, last_bbox, {x["caption_ord"] for x in tables} | taken)
            if cont is None:
                break
            part, ords = cont
            t["parts"].append(part)
            t["body_ords"] = t["body_ords"] + ords
            last_page, last_bbox = part["page"], part["bbox"]
    return kept


def _headless_continuation(by_page: dict[int, list[dict]], page: int, bbox: tuple, caption_ords: set[int]):
    """上一页的表一直排到页底，下一页顶上紧接着没有图注的表行：认为是同一张表的续页。要求都满足才接，宁可不接也不乱接。"""
    cur = by_page.get(page)
    nxt = by_page.get(page + 1)
    if not cur or not nxt:
        return None
    page_bottom = max(p["bbox"][3] for p in cur)
    page_top = min(p["bbox"][1] for p in nxt)
    if bbox[3] < page_bottom - 0.06 * (page_bottom - page_top):  # 表没排到页底，不会有续页
        return None
    top_limit = page_top + 0.14 * (page_bottom - page_top)
    rows = [p for p in sorted(nxt, key=lambda p: p["bbox"][1])
            if p["bbox"][1] <= top_limit and p["kind"] in ("paragraph", "figure_text", "table") and p["ord"] not in caption_ords
            and bbox[0] - 20 <= p["bbox"][0] and p["bbox"][2] <= bbox[2] + 20]
    if len(rows) < 2 or any(len(p["text"]) > 120 or CAPTION_RE.match(p["text"]) or OTHER_CAPTION_RE.match(p["text"]) for p in rows):
        return None
    if any(re.search(r"[a-z]{3,} [a-z]{3,} [a-z]{3,} [a-z]{3,}", p["text"]) for p in rows):  # 正文句子，不是表格行
        return None
    # 续页的第一行必须是数据行（数字占一定比例）：表头一样的行是另一张表的开头，接上去会把两张表搅在一起
    toks = rows[0]["text"].split()
    if not toks or sum(1 for t in toks if re.fullmatch(r"[-+−–]?[\d.,]+[%×x]?", t)) / len(toks) < 0.3:
        return None
    ub = _union(rows)
    return {"page": page + 1, "bbox": ub, "body_ords": [p["ord"] for p in rows], "rows_text": [p["text"] for p in rows], "caption_ord": None}, [p["ord"] for p in rows]


FIG_LABEL_RE = re.compile(r"^\s*((?:Supplementary |Extended Data )?(?:Fig\.|Figure|FIGURE|图)\s*[A-Z]?\d+(?:\.\d+)*[A-Za-z]?)")
BAND_GAP = 14.0
BAND_PAD = 14.0
LAYOUT_MIN_CONF = 0.8


def detect_figure_data(passages: list[dict], figs: list[dict]) -> list[dict]:
    """图内的数据网格（子图下方的 “行标签 + 数值” 表）：[{caption_ord, label, page, caption, body_ords, bbox, bands, kind:'figure_data'}]。

    先用图注推出每张图的版面区，区内“行标签 + 数字”的图内文字段落按纵向间距聚成带（每带通常是一排并排的子表），
    每一带稍微向上下扩一点（把列名行和子图标题包进来）。曲线图的刻度和图例不是数据行，不会被当成表。纯函数，便于测试。
    """
    get = lambda o, k: o[k]  # noqa: E731
    zones = datablocks.figure_zones(passages, figs, get)
    by_ord = {p["ord"]: p for p in passages}
    out: list[dict] = []
    for z in zones:
        cap = by_ord.get(z["caption_ord"])
        rows = [p for p in passages if p["page"] == z["page"] and p["kind"] == "figure_text" and datablocks._inside(p["bbox"], z)
                and datablocks.is_tabular_rows(p["text"])]
        if not rows or cap is None:
            continue
        rows.sort(key=lambda p: p["bbox"][1])
        bands: list[list[dict]] = []
        for p in rows:
            if bands and p["bbox"][1] - max(q["bbox"][3] for q in bands[-1]) <= BAND_GAP:
                bands[-1].append(p)
            else:
                bands.append([p])
        boxes = []
        for b in bands:
            boxes.append((min(q["bbox"][0] for q in b), max(z["y0"], min(q["bbox"][1] for q in b) - BAND_PAD),
                          max(q["bbox"][2] for q in b), min(z["y1"], max(q["bbox"][3] for q in b) + BAND_PAD)))
        m = FIG_LABEL_RE.match(cap["text"])
        out.append({"caption_ord": cap["ord"], "label": m.group(1).strip() if m else "Fig.", "page": z["page"], "caption": cap["text"],
                    "body_ords": [p["ord"] for p in rows], "bbox": (min(x[0] for x in boxes), min(x[1] for x in boxes),
                                                                  max(x[2] for x in boxes), max(x[3] for x in boxes)),
                    "bands": boxes, "kind": "figure_data", "rows_text": []})
    return out


def layout_figure_data(app: AppContext, paper_id: str, t: dict) -> dict | None:
    """图内数据网格：用文字层词坐标重建各子表；没有可信的子表返回 None。"""
    blocks: list[dict] = []
    for bb in t["bands"]:
        words = fetch_words(app, paper_id, t["page"], bb, pad=2.0)
        blocks += layout_tables.reconstruct_panels(words)
    if not blocks:
        return None
    first = blocks[0]
    conf = min(b["confidence"] for b in blocks)
    warnings = [f"「{b['title'] or '子表'}」：{'；'.join(b['reasons'])}" for b in blocks if b["reasons"]]
    return {"title": "", "columns": first["columns"], "rows": first["rows"], "notes": "", "unverified_cells": [], "misplaced_cells": [],
            "verified_ratio": 1.0, "warnings": warnings, "unreadable": 0,
            "blocks": [{"title": b["title"], "columns": b["columns"], "rows": b["rows"], "confidence": b["confidence"]} for b in blocks],
            "layout": {"confidence": conf, "blocks": len(blocks)}}


def _reconstruct_part(app: AppContext, paper_id: str, page: int, bbox: tuple, caption_bbox: tuple | None, *, anchors: list[float] | None = None,
                      prefer: str | None = None) -> tuple[dict, str] | None:
    """一页里的一段表（整张表，或跨页表的某一页）用文字层词坐标重建。返回 (结果, 'panel'|'text')；重建不了返回 None。
    anchors 是第一页重建出的列边界：续页没有表头，沿用它。prefer 指定只试其中一种（续页要和第一页用同一种办法）。"""
    if anchors is None and prefer != "text":
        words = fetch_words(app, paper_id, page, bbox, pad=2.0)
        tabs = layout_tables.reconstruct_panels(words, min_conf=LAYOUT_MIN_CONF)
        if len(tabs) == 1 and tabs[0]["n_rows"] >= 3:
            return tabs[0], "panel"
    if prefer == "panel":
        words = fetch_words(app, paper_id, page, bbox, pad=2.0)
        tabs = layout_tables.reconstruct_panels(words, min_rows=1, min_conf=LAYOUT_MIN_CONF)
        return (tabs[0], "panel") if len(tabs) == 1 else None
    # 文字型表格（列是文字、折行、没有竖线）：版面分栏会把它切成左右两半，所以用整页宽度的词重建
    x0, y0, x1, y1 = bbox
    if caption_bbox and caption_bbox[3] <= y0 + 30:  # 图注在表上方：表头行常常被并进图注段落里，从图注开头取词，由重建函数跳过图注自己的行
        y0 = min(y0, caption_bbox[1] - 1.0)
    wide = fetch_words(app, paper_id, page, (0.0, y0, 5000.0, y1 + 8.0), pad=0.0)
    b = layout_tables.reconstruct_text_table(wide, anchors=anchors)
    return (b, "text") if b is not None else None


def layout_caption_table(app: AppContext, paper_id: str, t: dict) -> dict | None:
    """有 “Table N:” 图注的表：用文字层词坐标重建；可信度不够（行列不整齐、不是数值表）返回 None，交给视觉模型。
    跨页的表把各页的行接成一张：续页没有表头就沿用第一页的列；任何一页重建不了，整张表都不当作重建成功。"""
    segs = [{"page": t["page"], "bbox": t["bbox"], "caption_bbox": t.get("caption_bbox"), "main": True}]
    segs += [{"page": q["page"], "bbox": q["bbox"], "caption_bbox": None, "main": False} for q in t.get("parts") or []]
    segs.sort(key=lambda x: (x["page"], x["bbox"][1]))  # 按页码顺序：正式的表题可能在续页之后（表题在表的下面）
    first = _reconstruct_part(app, paper_id, segs[0]["page"], segs[0]["bbox"], segs[0]["caption_bbox"])
    if first is None:
        return None
    b, how = first
    columns, rows, reasons = list(b["columns"]), [list(r) for r in b["rows"]], list(b["reasons"])
    for part in segs[1:]:
        got = _reconstruct_part(app, paper_id, part["page"], part["bbox"], None, anchors=b.get("anchors") if how == "text" else None, prefer=how)
        if got is None or got[0]["n_cols"] != b["n_cols"]:
            if part["main"] and part is segs[-1]:
                continue  # 表题在表的后面时，“表题上方的那一段”常常只是表题旁边的正文，不是表体：其余各页都重建好了就不要它
            return None
        pr = [list(r) for r in got[0]["rows"]]
        if pr and columns and [c.strip() for c in pr[0]] == [c.strip() for c in columns]:
            pr = pr[1:]  # 续页重复了表头
        rows += pr
    if len(segs) > 1:
        reasons.append(f"这张表跨 {len(segs)} 页，已合成一张")
        if any("合并单元格" in r for r in reasons):  # 首列是合并单元格：续页的空首格也向下填充
            last = ""
            for r in rows:
                if r and r[0]:
                    last = r[0]
                elif r:
                    r[0] = last
    return {"title": "", "columns": columns, "rows": rows, "notes": "", "unverified_cells": [], "misplaced_cells": [],
            "verified_ratio": 1.0, "warnings": [f"重建提示：{'；'.join(reasons)}"] if reasons else [], "unreadable": 0,
            "layout": {"confidence": b["confidence"], "blocks": 1}}


def parse_delimited(rows_text: list[str]) -> dict | None:
    """行文字本身带制表符或“ | ”分隔、且各行列数一致时直接建表；否则返回 None（交给视觉模型）。"""
    for sep in ("\t", " | "):
        cells = [[c.strip() for c in r.split(sep)] for r in rows_text if r.strip()]
        n = len(cells[0]) if cells else 0
        if n >= 2 and len(cells) >= MIN_ROWS and all(len(r) == n for r in cells):
            return {"title": "", "columns": cells[0], "rows": cells[1:], "notes": "", "unverified_cells": [], "verified_ratio": None,
                    "warnings": [], "unreadable": 0}
    return None


def _passages(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND kind IN ('paragraph','figure_text','caption','table','heading','reference','formula') ORDER BY ord",
            (paper_id,)).fetchall()
    return [{"ord": r["ord"], "page": r["page"], "kind": r["kind"], "text": r["text"], "bbox": (r["x0"], r["y0"], r["x1"], r["y1"])} for r in rows]


def _figures(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT page, x0, y0, x1, y1, kind, label, caption_ord FROM figures WHERE paper_id = ?", (paper_id,)).fetchall()
    return [{"page": r["page"], "bbox": (r["x0"], r["y0"], r["x1"], r["y1"]), "kind": r["kind"], "label": r["label"],
             "caption_ord": r["caption_ord"]} for r in rows]


FIGURE_DATA_AS_TABLE = False  # 图（Fig.）里的数据网格先不转成表格；只有图注明确写 Table 的才转。检测与重建代码保留，改成 True 即可恢复


def tables_for(app: AppContext, paper_id: str) -> list[dict]:
    """带 “Table N:” 图注的表，按图注序号排序；FIGURE_DATA_AS_TABLE 打开时再加上图内的数据网格（kind=figure_data）。"""
    passages = _passages(app, paper_id)
    found = detect_tables(passages)
    for t in found:
        t.setdefault("kind", "table")
    if FIGURE_DATA_AS_TABLE:
        found += detect_figure_data(passages, _figures(app, paper_id))
    return sorted(found, key=lambda t: t["caption_ord"])


def _rows(app: AppContext, paper_id: str) -> dict[int, dict]:
    with app.db.session() as conn:
        return {r["caption_ord"]: dict(r) for r in conn.execute("SELECT * FROM table_analysis WHERE paper_id = ?", (paper_id,))}


def status(app: AppContext, paper_id: str) -> dict:
    """{total, ready, failed, running, complete, items}（只读缓存，不调用模型）。item.status：none|pending|running|ready|failed。"""
    found, saved = tables_for(app, paper_id), _rows(app, paper_id)
    items, counts = [], {"ready": 0, "failed": 0, "running": 0}
    for t in found:
        r = saved.get(t["caption_ord"])
        st = r["status"] if r else "none"
        if st in ("pending", "running"):
            counts["running"] += 1
        elif st in counts:
            counts[st] += 1
        data = loads(r["data_json"]) if r and st == "ready" else None
        items.append({"caption_ord": t["caption_ord"], "kind": t.get("kind", "table"), "label": t["label"], "page": t["page"], "body_ords": t["body_ords"],
                      "bbox": list(t["bbox"]), "status": st, "source": r["source"] if r else None, "error": r["error"] if r else None,
                      "model": r["model"] if r else None, "data": data})
    return {"total": len(found), **counts, "complete": bool(found) and counts["ready"] + counts["failed"] >= len(found), "items": items}


def image_png(app: AppContext, paper_id: str, caption_ord: int, dpi: int = 150) -> bytes:
    t = next((x for x in tables_for(app, paper_id) if x["caption_ord"] == caption_ord), None)
    if t is None:
        raise ApiError("not_found", "表格不存在", 404)
    return crop_png(app, paper_id, t["page"], t["bbox"], max(72, min(300, dpi)))


def image_svg(app: AppContext, paper_id: str, caption_ord: int) -> str:
    """表格区域的矢量图（线条和文字是真正的矢量，放大不糊）；导不出来时调用方退回位图。"""
    from ..parsing.render import RenderError, render_svg
    from .images import paper_pdf_path

    t = next((x for x in tables_for(app, paper_id) if x["caption_ord"] == caption_ord), None)
    if t is None:
        raise ApiError("not_found", "表格不存在", 404)
    try:
        return render_svg(paper_pdf_path(app, paper_id), t["page"], t["bbox"], pad=6.0)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc


def _check_vision(app: AppContext) -> None:
    """没有 API Key 或没配置视觉模型时给出可读错误（和图表理解一致）。"""
    service = AIService(app)
    try:
        _provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    choose_model(TaskType.VISION, cfg)


def _upsert(app: AppContext, paper_id: str, caption_ord: int, **f) -> None:
    f.setdefault("updated_at", utcnow())
    cols = ["paper_id", "caption_ord", *f]
    with app.db.session() as conn:
        conn.execute(
            f"INSERT INTO table_analysis({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(paper_id, caption_ord) DO UPDATE SET {','.join(f'{k} = excluded.{k}' for k in f)}",
            (paper_id, caption_ord, *f.values()))


def start(app: AppContext, paper_id: str, *, force: bool = False, only: list[int] | None = None, method: str = "auto") -> str | None:
    """创建后台任务识别还没识别的表格；没有表格、已识别完、或已有任务在跑时返回 None（在跑的任务 id 见 status）。
    only：只处理这些表题序号的表（重试单张表、或只验证几张表）；force：已识别好的也重新识别；
    method：auto（先文字建表、再文字层坐标还原，还原不了才用视觉模型）| vision（强制用视觉模型，文字层还原得不对时换个办法再试）。"""
    found, saved = tables_for(app, paper_id), _rows(app, paper_id)
    if only is not None:
        found = [t for t in found if t["caption_ord"] in set(only)]
    todo = [t for t in found if force or saved.get(t["caption_ord"], {}).get("status") not in ("ready", "pending", "running")]
    # 不需要模型的先做完：图内数据网格、文字层能重建出整齐行列的表（确定性，当场完成，不创建后台任务）
    for t in list(todo):
        if method == "vision" and t.get("kind") != "figure_data":
            continue  # 强制视觉模型：不走文字建表和坐标还原
        if t.get("kind") == "figure_data":
            data = layout_figure_data(app, paper_id, t)
            if data is not None:
                _upsert(app, paper_id, t["caption_ord"], status="ready", source="layout", data_json=dumps(data), model=None, error=None, job_id=None)
            else:
                _upsert(app, paper_id, t["caption_ord"], status="failed", source=None, data_json=None, model=None, job_id=None,
                        error="没能从文字层把图里的数据还原成表格")
            todo.remove(t)
        elif parse_delimited(t["rows_text"]) is None:
            data = layout_caption_table(app, paper_id, t)
            if data is not None:
                _upsert(app, paper_id, t["caption_ord"], status="ready", source="layout", data_json=dumps(data), model=None, error=None, job_id=None)
                todo.remove(t)
    if not todo:
        return None
    needs_model = method == "vision" or any(parse_delimited(t["rows_text"]) is None for t in todo)
    if needs_model:
        _check_vision(app)
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    # 先把状态写成“等待中”，再提交任务：小表格的任务可能在提交返回前就跑完，顺序反了会把“已完成”覆盖回“等待中”
    for t in todo:
        _upsert(app, paper_id, t["caption_ord"], status="pending", error=None, job_id=None)
    job_id = app.jobs.submit(JOB_TYPE, f"识别表格：{title[:40]}", {"paper_id": paper_id, "ords": [t["caption_ord"] for t in todo], "method": method}, paper_id)
    with app.db.session() as conn:
        conn.execute(
            f"UPDATE table_analysis SET job_id = ? WHERE paper_id = ? AND status = 'pending' AND caption_ord IN ({','.join('?' * len(todo))})",
            (job_id, paper_id, *[t["caption_ord"] for t in todo]),
        )
    return job_id


class _Sub:
    """把 `_do_table` 里的进度换算成整个批量任务的进度，并在日志里带上表名。"""

    def __init__(self, job: JobContext, lo: float, hi: float, label: str):
        self.job, self.lo, self.hi, self.label = job, lo, hi, label
        self.job_id = job.job_id

    def check_cancelled(self) -> None:
        self.job.check_cancelled()

    def log(self, msg: str) -> None:
        self.job.log(f"{self.label}：{msg}")

    def progress(self, p: float, msg: str = "") -> None:
        self.job.progress(self.lo + (self.hi - self.lo) * p, f"{self.label}：{msg}")


async def _run(app: AppContext, job: JobContext, paper_id: str, ords: list[int], method: str = "auto") -> dict:
    found = {t["caption_ord"]: t for t in tables_for(app, paper_id) if t.get("kind", "table") == "table"}
    todo = [found[o] for o in ords if o in found]
    service = AIService(app)
    provider = cfg = vmodel = None
    done = failed = 0
    try:
        for i, t in enumerate(todo):
            job.check_cancelled()
            lo, hi = i / len(todo), (i + 1) / len(todo)
            _upsert(app, paper_id, t["caption_ord"], status="running", error=None, job_id=job.job_id)
            try:
                data = parse_delimited(t["rows_text"]) if method != "vision" else None
                if data is not None:
                    _upsert(app, paper_id, t["caption_ord"], status="ready", source="text", data_json=dumps(data), model=None, error=None)
                    done += 1
                    continue
                if provider is None:
                    provider, cfg = service.provider()
                    vmodel = choose_model(TaskType.VISION, cfg).model
                fig = {"label": t["label"], "page": t["page"], "bbox": t["bbox"], "caption": t["caption"], "kind": "table",
                       "source_text": "\n".join(t["rows_text"])}
                png = crop_png_fit(app, paper_id, t["page"], t["bbox"])
                status, _kind, data, _w = await figures._do_table(app, service, provider, cfg, vmodel, fig, png, _Sub(job, lo, hi, t["label"]), paper_id)
                _upsert(app, paper_id, t["caption_ord"], status="ready", source="vision", data_json=dumps(data), model=vmodel, error=None)
                done += 1
            except JobCancelled:
                raise
            except (ApiError, LLMError, ValueError) as err:
                code = getattr(err, "code", "")
                msg = getattr(err, "message", None) or str(err)
                if code in ("auth_failed", "no_api_key", "not_configured", "vision_not_configured"):
                    raise ApiError(code, msg) from err  # 整体性错误：中止全部
                _upsert(app, paper_id, t["caption_ord"], status="failed", error=msg[:300])
                failed += 1
            job.progress(hi, f"已识别 {done}/{len(todo)} 个表格" + (f"，{failed} 个失败" if failed else ""))
    except BaseException:
        with app.db.session() as conn:  # 被取消或整体失败：还没出结果的行回到“没处理过”，避免一直显示“识别中”
            conn.execute("DELETE FROM table_analysis WHERE paper_id = ? AND job_id = ? AND status IN ('pending','running')", (paper_id, job.job_id))
        raise
    return {"recognized": done, "failed": failed}


def handle(job: JobContext) -> dict:
    return job.run_async(_run(job.app, job, job.payload["paper_id"], list(job.payload.get("ords") or []), str(job.payload.get("method") or "auto")))


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle)
