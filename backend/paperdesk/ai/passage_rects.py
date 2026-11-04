"""段落在 PDF 里的真实位置：跨栏、跨页的续行也包含（从文字层的词里把整段重新找出来）；找不到就退回起始块的坐标。
阅读器里“这一段怎么读”的聚光灯用它把整段框住，而不是只框起始那一块。
"""
from __future__ import annotations

import re
import unicodedata

from .. import reader
from ..context import AppContext


def _norm_tok(w: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", w).lower())  # NFKC：把 ﬁ 之类的连字展开成 fi


def _merge_boxes(boxes: list[list[float]]) -> list[list[float]]:
    """把同一栏里上下相接的行/块并成一个矩形（按纵向间隙 < 一个行高 且横向重叠判断）。"""
    out: list[list[float]] = []
    for b in sorted(boxes, key=lambda b: (round(b[0] / 40), b[1])):
        for o in out:
            h = max(b[3] - b[1], o[3] - o[1])
            if min(o[2], b[2]) - max(o[0], b[0]) > 0 and b[1] - o[3] < h * 1.2 and o[1] - b[3] < h * 1.2:
                o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                break
        else:
            out.append(list(b))
    return out


def passage_rects(app: AppContext, paper_id: str, ord_: int) -> dict:
    """这一段在 PDF 里真实占的位置：跨栏、跨页的续行也包含（从文字层的词里把整段重新找出来）；找不到就退回起始块的坐标。"""
    cur = reader.get_passage(app, paper_id, ord_)
    fallback = {"rects": [{"page": cur["page"], "bbox": cur["bbox"]}], "exact": False}
    flat = _norm_tok(cur.get("text") or "")  # 去掉空白、连字符、标点：断行连字符、排版差异都不影响对齐
    if len(flat) < 40:
        return fallback
    pdf = app.paths.paper_dir(paper_id) / "original.pdf"
    if not pdf.is_file():
        return fallback
    try:
        import pymupdf

        with pymupdf.open(pdf) as doc:
            seq: list[tuple[str, int, tuple[float, float, float, float], tuple[int, int]]] = []
            for pg in (cur["page"], cur["page"] + 1):
                if pg > doc.page_count:
                    break
                for w in doc[pg - 1].get_text("words"):
                    t = _norm_tok(w[4])
                    if t:
                        seq.append((t, pg, (w[0], w[1], w[2], w[3]), (w[5], w[6])))
    except Exception:  # noqa: BLE001  定位失败只是退回起始块，不影响解读
        return fallback
    rects = span_rects(seq, flat, cur["page"], cur["bbox"])
    return {"rects": rects, "exact": True} if rects else fallback


def span_rects(seq: list, flat: str, page: int, bbox: list[float]) -> list[dict] | None:
    """在文字层的词序列 seq（(归一化词, 页, 框, (块号, 行号))，按文字层顺序、可跨页）里，找到归一化后等于 flat 的那一段，
    返回它占的位置（同一页同一栏里相接的行并成一块）；找不到返回 None。跨栏、跨页的续行也会包含。"""
    import bisect

    stream = "".join(t[0] for t in seq)
    starts: list[int] = []  # 每个词在 stream 里的起始偏移
    pos = 0
    for t in seq:
        starts.append(pos)
        pos += len(t[0])
    K = 24
    x0, y0, x1, y1 = bbox
    start = None
    i = stream.find(flat[:K])
    while i >= 0:
        wi = bisect.bisect_right(starts, i) - 1
        bx = seq[wi][2]
        if seq[wi][1] == page and x0 - 8 <= bx[0] <= x1 + 8 and y0 - 8 <= bx[1] <= y1 + 8 and i - starts[wi] <= 3:
            start = wi
            break
        i = stream.find(flat[:K], i + 1)
    if start is None:
        return None
    base = starts[start]
    tail = flat[-K:]
    end = None
    best = None
    j = stream.find(tail, base + max(0, len(flat) - 400))
    while j >= 0 and j <= base + int(len(flat) * 1.3) + 80:
        d = abs((j + len(tail) - base) - len(flat))
        if best is None or d < best:
            best, end = d, bisect.bisect_right(starts, j + len(tail) - 1)
        j = stream.find(tail, j + 1)
    if end is None or end <= start:
        return None
    lines: dict[tuple[int, int, int], list[float]] = {}
    for t, pg, b, (blk, ln) in seq[start:end]:
        k = (pg, blk, ln)
        if k in lines:
            o = lines[k]
            o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
        else:
            lines[k] = list(b)
    rects = []
    for pg in sorted({k[0] for k in lines}):
        for b in _merge_boxes([v for k, v in lines.items() if k[0] == pg]):
            rects.append({"page": pg, "bbox": [b[0] - 1.5, b[1] - 1.5, b[2] + 1.5, b[3] + 1.5]})
    return rects
