"""补回行内“单个字母”的公式（变量 N、s、d、α……）。

MinerU 只会把它认成公式的片段标成 equation_inline，孤零零的一个变量常常被当成普通文字。PDF 文字层里这样的变量是
斜体或数学字体的单个字母：按位置找出来，补进段落的 math 列表（前端据此渲染成公式），已经有的公式不动。

不确定就不补：英文里斜体的 “a”“I” 可能是冠词、代词，只有数学字体的才认；其它单字母要求前后都不是字母数字。"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from .font_style import _is_italic
from .layout import MATH_FONT_RE, clean_text
from .pdf_parser import _PARSE_LOCK, ParsedDoc

log = logging.getLogger("paperdesk.parse")
PAD = 3.0
_ALNUM = re.compile(r"[^\W_]", re.U)
_RISKY = set("aAI")  # 英文冠词 / 代词：只有数学字体里的才认


def _page_letters(page) -> list[tuple[str, tuple[float, float, float, float], bool]]:
    """页面里的单字母候选 [(字母, bbox, 是不是数学字体)]，按阅读顺序。"""
    out = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            if ln.get("dir", (1, 0))[0] < 0.99:
                continue
            for sp in ln["spans"]:
                t = clean_text(sp["text"]).strip()
                if len(t) != 1 or not t.isalpha():
                    continue
                math_font = bool(MATH_FONT_RE.search(sp["font"]))
                if math_font or _is_italic(sp):
                    out.append((t, tuple(sp["bbox"]), math_font))
    return out


def _page_subscripts(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    """页面里“字母 + 小一号、压低的下标”（d_i）：MinerU 常把它写成粘在一起的 “di”。返回 [(“di”, bbox)]。"""
    out = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            if ln.get("dir", (1, 0))[0] < 0.99:
                continue
            sps = ln["spans"]
            for a, b in zip(sps, sps[1:]):
                ta, tb = clean_text(a["text"]).strip(), clean_text(b["text"]).strip()
                if len(ta) != 1 or not ta.isalpha() or len(tb) != 1 or not tb.isalnum():
                    continue
                if not (MATH_FONT_RE.search(a["font"]) or _is_italic(a)):
                    continue
                if b["size"] < a["size"] * 0.85 and b["origin"][1] > a["origin"][1] + 0.5:
                    x0, y0, x1, y1 = a["bbox"]
                    bx0, by0, bx1, by1 = b["bbox"]
                    out.append((ta + tb, (min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1))))
    return out


def _spans(text: str, runs: list[dict]) -> list[tuple[int, int, dict]]:
    """已有公式在文字里的位置（和前端 mathRanges 同一套规则：按顺序、往后找、单词中间的不算）。"""
    out, cur = [], 0
    for r in runs:
        src, tex = r.get("src") or "", r.get("tex") or ""
        if not src or not tex:
            continue
        start = cur
        while True:
            i = text.find(src, start)
            if i < 0:
                break
            before = text[i - 1] if i > 0 else ""
            after = text[i + len(src)] if i + len(src) < len(text) else ""
            if (_ALNUM.match(src[0]) and _ALNUM.match(before or " ")) or (_ALNUM.match(src[-1]) and _ALNUM.match(after or " ")):
                start = i + 1
                continue
            out.append((i, i + len(src), r))
            cur = i + len(src)
            break
    return out


def add_letters(parsed: ParsedDoc, path: Path) -> int:
    added = 0
    by_page: dict[int, list] = {}
    for p in parsed.passages:
        if p.kind in ("paragraph", "caption"):
            by_page.setdefault(p.page, []).append(p)
    with _PARSE_LOCK:
        doc = pymupdf.open(str(path))
        try:
            for pno, ps in by_page.items():
                if not 1 <= pno <= doc.page_count or doc[pno - 1].rotation:
                    continue
                letters = _page_letters(doc[pno - 1])
                subs = _page_subscripts(doc[pno - 1])
                if not letters:
                    continue
                for p in ps:
                    x0, y0, x1, y1 = p.bbox
                    mine = [(ch, mf) for ch, (a, b, c, d), mf in letters
                            if x0 - PAD <= (a + c) / 2 <= x1 + PAD and y0 - PAD <= (b + d) / 2 <= y1 + PAD]
                    if not mine:
                        continue
                    text = p.text
                    runs = list(p.math or [])
                    existing = _spans(text, runs)
                    # 文字层里字母的顺序和阅读顺序不一致（下标、行内排版），不按顺序配：
                    # 这段落里斜体 / 数学字体的单字母出现过几次，文字里“前后都不是字母数字”的同一个字母就最多认几处
                    budget: dict[str, int] = {}
                    for ch, mf in mine:
                        if ch in _RISKY and not mf:
                            continue
                        budget[ch] = budget.get(ch, 0) + 1
                    found: list[tuple[int, str]] = []
                    pairs_new: list[tuple[int, dict]] = []
                    for src, (a, b, c, d) in subs:  # 粘在一起的 “di” → d_{i}
                        if not (x0 - PAD <= (a + c) / 2 <= x1 + PAD and y0 - PAD <= (b + d) / 2 <= y1 + PAD):
                            continue
                        for m in re.finditer(re.escape(src), text):
                            i = m.start()
                            before = text[i - 1] if i > 0 else " "
                            after = text[i + 2] if i + 2 < len(text) else " "
                            if _ALNUM.match(before) or _ALNUM.match(after) or any(x <= i < y or x < i + 2 <= y for x, y, _r in existing):
                                continue
                            if any(i == j for j, _r in pairs_new):
                                continue
                            pairs_new.append((i, {"src": src, "tex": f"{src[0]}_{{{src[1]}}}"}))
                    taken = [(i, i + 2) for i, _r in pairs_new]
                    for i, ch in enumerate(text):
                        if budget.get(ch, 0) <= 0:
                            continue
                        before = text[i - 1] if i > 0 else " "
                        after = text[i + 1] if i + 1 < len(text) else " "
                        if _ALNUM.match(before) or _ALNUM.match(after) or any(x <= i < y for x, y, _r in existing) or any(x <= i < y for x, y in taken):
                            continue
                        found.append((i, ch))
                        budget[ch] -= 1
                    if not found and not pairs_new:
                        continue
                    pairs = [(a, r) for a, _b, r in existing]  # 找不到位置的旧公式不动它（前端同样会跳过）
                    pairs += [(i, {"src": ch, "tex": ch}) for i, ch in found] + pairs_new
                    pairs.sort(key=lambda x: x[0])
                    p.math = [r for _i, r in pairs]
                    added += len(found) + len(pairs_new)
        finally:
            doc.close()
    return added


def add_letters_safe(parsed: ParsedDoc, path: Path) -> None:
    try:
        n = add_letters(parsed, path)
        if n:
            log.info("补回行内单字母公式 %d 处", n)
    except Exception as exc:  # noqa: BLE001  只是增强
        log.warning("补行内单字母公式失败，跳过：%s", exc)
