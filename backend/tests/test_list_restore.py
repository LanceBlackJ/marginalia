from __future__ import annotations

import pymupdf

from paperdesk.parsing import list_restore as lr
from paperdesk.parsing.pdf_parser import ParsedPassage, ParsedSection


class _Doc:
    def __init__(self, passages, sections):
        self.passages = passages
        self.sections = sections


def _pdf(tmp_path):
    p = tmp_path / "abbr.pdf"
    d = pymupdf.open()
    pg = d.new_page()
    y = 100
    pg.insert_text((57, 80), "Abbreviations", fontsize=10)
    for ab, full in [("LLMs", "Large language models"), ("MAS", "Multi-agent systems"), ("RL", "Reinforcement learning")]:
        pg.insert_text((57, y), ab + "\t", fontsize=9)
        pg.insert_text((57, y + 11), "\x07" + full, fontsize=9)
        y += 24
    d.save(p)
    return p


def test_abbreviation_list_gets_its_line_breaks_back(tmp_path):
    path = _pdf(tmp_path)
    text = "LLMs Large language models MAS Multi-agent systems RL Reinforcement learning"
    p = ParsedPassage(0, 1, "paragraph", text, (50, 70, 250, 190), 0, "mineru", [], [])
    doc = _Doc([p], [ParsedSection(0, 1, "Abbreviations", 1, "detected")])
    lr.restore_lists_safe(doc, path)
    assert p.text.split("\n") == [
        "LLMs Large language models",
        "MAS Multi-agent systems",
        "RL Reinforcement learning",
    ]


def test_other_sections_and_mismatched_text_are_left_alone(tmp_path):
    path = _pdf(tmp_path)
    keep = "Some other words entirely that do not match the list"
    p = ParsedPassage(0, 1, "paragraph", keep, (50, 70, 250, 190), 0, "mineru", [], [])
    lr.restore_lists_safe(_Doc([p], [ParsedSection(0, 1, "Abbreviations", 1, "detected")]), path)
    assert p.text == keep
    q = ParsedPassage(0, 1, "paragraph", "LLMs Large language models MAS Multi-agent systems", (50, 70, 250, 190), 0, "mineru", [], [])
    lr.restore_lists_safe(_Doc([q], [ParsedSection(0, 1, "Introduction", 1, "detected")]), path)
    assert "\n" not in q.text
