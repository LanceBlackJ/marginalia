"""三线表（图注 + 几条通栏横线）：格子里的文字属于表格，不能当正文段落。"""
from pathlib import Path

import pymupdf

from paperdesk.parsing.figures import detect_regions
from paperdesk.parsing.pdf_parser import parse_pdf


def _make(path: Path, *, with_rules: bool = True) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=791)
    page.insert_textbox(pymupdf.Rect(57, 60, 540, 80), "Table 1  A review of representative works on multi-agent systems", fontsize=9, fontname="helv")
    if with_rules:
        for x0, x1 in ((57, 197), (197, 339), (339, 539)):  # 每条线被切成每列一段，首尾相连
            for y in (100, 115, 330):
                page.draw_line((x0, y), (x1, y), width=0.6)
    rows = [
        "Work Object Modality Base model Train Feedback Evaluation Interaction Environment, Agent on dataset, Cooperative with models",
        "Avalonbench Avalon Text GPT-3.5, Llama2 No Environment, Agent Interaction on dataset, Mixed with models Cooperative",
        "Welfare diplomacy Welfare Diplomacy Text No Environment, Agent Interaction on dataset, Human with models Debate",
    ]
    y = 120
    for r in rows:
        page.insert_textbox(pymupdf.Rect(57, y, 539, y + 30), r, fontsize=8, fontname="helv")
        y += 40
    body = ("We present current representative works, providing a detailed analysis of each work from the perspectives of the object, modality, "
            "feedback, evaluation, and interaction, which together characterise how these systems are built and assessed in practice.")
    page.insert_textbox(pymupdf.Rect(57, 350, 539, 420), body, fontsize=10, fontname="helv")
    doc.save(path)
    return path


def test_three_line_table_region_is_found_below_its_caption(tmp_path):
    doc = pymupdf.open(_make(tmp_path / "t.pdf"))
    regs = detect_regions(doc[0], 595, 791)
    tables = [r for r in regs if r.table]
    assert len(tables) == 1
    t = tables[0]
    assert 95 <= t.y0 <= 101 and 329 <= t.y1 <= 332 and t.x0 <= 58 and t.x1 >= 538


def test_without_rules_nothing_is_guessed(tmp_path):
    doc = pymupdf.open(_make(tmp_path / "t.pdf", with_rules=False))
    assert not [r for r in detect_regions(doc[0], 595, 791) if r.table]


def test_table_cells_are_not_body_paragraphs(tmp_path):
    parsed = parse_pdf(_make(tmp_path / "t.pdf"))
    cells = [p for p in parsed.passages if "Avalonbench" in p.text or "Welfare diplomacy" in p.text]
    assert cells and all(p.kind == "figure_text" for p in cells)
    body = [p for p in parsed.passages if "We present current representative works" in p.text]
    assert body and all(p.kind == "paragraph" for p in body)
