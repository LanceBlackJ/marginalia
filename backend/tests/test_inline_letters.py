from __future__ import annotations

import pymupdf

from paperdesk.parsing import inline_letters as il
from paperdesk.parsing.pdf_parser import ParsedPassage, ParsedSection


class _Doc:
    def __init__(self, passages):
        self.passages = passages
        self.sections = [ParsedSection(0, 1, "x", 1, "detected")]


def _pdf(tmp_path):
    p = tmp_path / "v.pdf"
    d = pymupdf.open()
    pg = d.new_page()
    # “the size N and rate s of a network”：N、s 用斜体字体，a 是普通冠词
    x = 60
    for word, italic in [("the size ", False), ("N", True), (" and rate ", False), ("s", True), (" of a network", False)]:
        pg.insert_text((x, 100), word, fontsize=11, fontname="tiit" if italic else "helv")
        x += pymupdf.get_text_length(word, fontname="tiit" if italic else "helv", fontsize=11)
    d.save(p)
    return p


def test_isolated_italic_letters_become_inline_math(tmp_path):
    path = _pdf(tmp_path)
    p = ParsedPassage(0, 1, "paragraph", "the size N and rate s of a network", (50, 85, 400, 110), 0, "mineru", [], [])
    il.add_letters_safe(_Doc([p]), path)
    assert [(r["src"], r["tex"]) for r in p.math] == [("N", "N"), ("s", "s")]


def test_existing_math_is_kept_in_order_and_words_are_not_touched(tmp_path):
    path = _pdf(tmp_path)
    runs = [{"src": "network", "tex": r"\mathrm{network}"}]
    p = ParsedPassage(0, 1, "paragraph", "the size N and rate s of a network", (50, 85, 400, 110), 0, "mineru", [], runs)
    il.add_letters_safe(_Doc([p]), path)
    assert [r["src"] for r in p.math] == ["N", "s", "network"]  # 按在文字里的先后排好
