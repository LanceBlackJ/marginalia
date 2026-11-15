"""三线表（图注 + 几条通栏横线）：格子里的文字属于表格，不能当正文段落。"""
from pathlib import Path

import pymupdf

from paperdesk.parsing.figures import detect_regions, text_panel_boxes
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


def test_text_only_table_is_found_without_rules(tmp_path):
    doc = pymupdf.open(_make(tmp_path / "t.pdf", with_rules=False))
    tables = [r for r in detect_regions(doc[0], 595, 791) if r.table]
    assert len(tables) == 1
    assert tables[0].y0 < 130 and tables[0].y1 > 200


def test_filled_text_panel_preserves_hard_line_breaks_without_making_it_a_figure(tmp_path):
    """提示词/算法文本框的行边界要保留，但文字仍属于可翻译的正文。"""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=791)
    page.draw_rect(pymupdf.Rect(55, 90, 540, 190), color=(0.5, 0.5, 0.5), fill=(0.95, 0.95, 0.95), width=1)
    page.insert_text((68, 108), "You are an AI assistant tasked with evaluating each step.", fontsize=10, fontname="helv")
    page.insert_text((68, 122), "The problem being addressed is: {problem}.", fontsize=10, fontname="helv")
    page.insert_text((68, 136), "Here is the conversation history: {failure log}.", fontsize=10, fontname="helv")
    page.insert_text((68, 150), "Please respond with Yes or No.", fontsize=10, fontname="helv")
    page.insert_textbox(
        pymupdf.Rect(55, 220, 540, 300),
        "This paper studies how structured prompts can support reliable evaluation of multi-agent systems. "
        "We compare several settings and report the resulting accuracy and failure patterns in detail.",
        fontsize=10,
        fontname="helv",
    )
    path = tmp_path / "prompt-box.pdf"
    doc.save(str(path))
    doc.close()

    check = pymupdf.open(str(path))
    assert text_panel_boxes(check[0], 595, 791)
    check.close()
    parsed = parse_pdf(path)
    prompt = next(p for p in parsed.passages if "The problem being addressed" in p.text)
    assert prompt.kind == "paragraph"
    assert prompt.text.splitlines() == [
        "You are an AI assistant tasked with evaluating each step.",
        "The problem being addressed is: {problem}.",
        "Here is the conversation history: {failure log}.",
        "Please respond with Yes or No.",
    ]


def test_table_cells_are_not_body_paragraphs(tmp_path):
    parsed = parse_pdf(_make(tmp_path / "t.pdf"))
    cells = [p for p in parsed.passages if "Avalonbench" in p.text or "Welfare diplomacy" in p.text]
    assert cells and all(p.kind == "figure_text" for p in cells)
    body = [p for p in parsed.passages if "We present current representative works" in p.text]
    assert body and all(p.kind == "paragraph" for p in body)
