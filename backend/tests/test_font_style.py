"""MinerU 结果补回粗体/斜体：按 PDF 文字层的字体对回段落。"""
from __future__ import annotations

import pymupdf

from paperdesk.parsing import font_style
from paperdesk.parsing.pdf_parser import ParsedPassage


class _Doc:
    def __init__(self, passages):
        self.passages = passages


def test_bold_and_italic_runs_are_mapped_back(tmp_path):
    d = pymupdf.open()
    pg = d.new_page()
    pg.insert_text((72, 160), "Plain then", fontsize=11, fontname="helv")
    pg.insert_text((140, 160), "BoldWord", fontsize=11, fontname="hebo")
    pg.insert_text((200, 160), "ItalWord", fontsize=11, fontname="heit")
    f = tmp_path / "t.pdf"
    d.save(f)
    p = ParsedPassage(0, 1, "paragraph", "Plain then BoldWord ItalWord", (60, 145, 400, 170))
    other = ParsedPassage(1, 1, "paragraph", "BoldWord elsewhere", (60, 500, 400, 520))
    font_style.annotate(_Doc([p, other]), f)
    assert p.bold == ["BoldWord"] and p.italic == ["ItalWord"]
    assert other.bold == [] and other.italic == []
