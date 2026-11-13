"""给 MinerU 的解析结果补回粗体、斜体：MinerU 不输出字体样式，这里读 PDF 文字层里的字体，按位置对回各段落。

只补 paragraph / caption；对不上的片段直接丢掉（宁可少加，不加错）。"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from .layout import MATH_FONT_RE, clean_text
from .pdf_parser import _PARSE_LOCK, ParsedDoc

log = logging.getLogger("paperdesk.parse")
PAD = 3.0


def _is_bold(sp: dict) -> bool:
    f = sp["font"].lower()
    return bool(sp["flags"] & 16) or "bold" in f or "semibold" in f or "-bd" in f


def _is_italic(sp: dict) -> bool:
    import re

    f = sp["font"].lower()
    return bool(sp["flags"] & 2) or bool(re.search(r"italic|-it\d*$|oblique", f))


def _runs(page, pred) -> list[tuple[str, tuple[float, float, float, float]]]:
    """页面里满足 pred 的连续片段 [(文字, bbox)]：同一行内相邻的合成一段；数学字体不算。"""
    out: list[tuple[str, list[float]]] = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            if ln.get("dir", (1, 0))[0] < 0.99:
                continue
            cur, box = "", None
            for sp in ln["spans"]:
                t = clean_text(sp["text"]).strip()
                if not t:
                    continue
                if pred(sp) and not MATH_FONT_RE.search(sp["font"]):
                    cur = f"{cur} {t}".strip() if cur else t
                    b = list(sp["bbox"])
                    box = b if box is None else [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
                elif cur:
                    out.append((cur, box))
                    cur, box = "", None
            if cur:
                out.append((cur, box))
    return [(t, tuple(b)) for t, b in out if len(t) >= 2 and any(ch.isalnum() for ch in t)]


def _respace(page, p) -> None:
    """MinerU 在旋转的表格页上会丢空格（“Table1|Evaluationof…”）：文字层里有空格，且字母完全一致时用文字层的。"""
    t = p.text
    if len(t) < 25 or t.count(" ") / len(t) > 0.04:
        return
    x0, y0, x1, y1 = p.bbox
    got = " ".join(page.get_text("text", clip=pymupdf.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1)).split())
    squash = lambda x: re.sub(r"[\s|]+", "", x).lower()
    if got and got.count(" ") > t.count(" ") and squash(got) == squash(t):
        p.text = got


def annotate(parsed: ParsedDoc, path: Path) -> None:
    with _PARSE_LOCK:
        doc = pymupdf.open(str(path))
        try:
            by_page: dict[int, list] = {}
            for p in parsed.passages:
                if p.kind in ("paragraph", "caption"):
                    by_page.setdefault(p.page, []).append(p)
            for pno, ps in by_page.items():
                if not 1 <= pno <= doc.page_count:
                    continue
                page = doc[pno - 1]
                for p in ps:
                    _respace(page, p)
                if page.rotation:
                    continue
                for attr, pred in (("bold", _is_bold), ("italic", _is_italic)):
                    runs = _runs(page, pred)
                    for p in ps:
                        x0, y0, x1, y1 = p.bbox
                        got: list[tuple[int, str]] = []
                        for t, (a, b, c, d) in runs:
                            cx, cy = (a + c) / 2, (b + d) / 2
                            if x0 - PAD <= cx <= x1 + PAD and y0 - PAD <= cy <= y1 + PAD:
                                i = p.text.find(t)
                                if i >= 0:
                                    got.append((i, t))
                        got.sort()
                        setattr(p, attr, [t for _, t in got])
        finally:
            doc.close()


def annotate_safe(parsed: ParsedDoc, path: Path) -> None:
    try:
        annotate(parsed, path)
    except Exception as exc:  # noqa: BLE001  只是增强
        log.warning("补粗体/斜体失败，跳过：%s", exc)
