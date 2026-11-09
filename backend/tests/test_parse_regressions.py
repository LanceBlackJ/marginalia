"""解析的回归测试：每一条都来自真实论文里出过的问题（用合成的片段重现，不依赖 PDF 文件）。"""
from __future__ import annotations

from paperdesk.parsing import layout
from paperdesk.parsing.layout import Span, _line_from


def sp(text, x0, y, size=10.0, font="Times-Roman", bold=False, italic=False, w=None):
    w = w if w is not None else 5.0 * len(text)
    return Span(x0, y - size, x0 + w, y + 2, y, text, size, bold, italic, font, False)


def line(y, parts, size=10.0):
    spans, x = [], parts[0][0]
    for x0, text, kw in parts:
        spans.append(sp(text, x0, y, size, **kw))
    return _line_from(spans)


def test_numbered_references_fold_back_continuation_lines():
    """“Proc. IEEE Int.” 换行后 “Conf. Robot. Automat.” 以大写开头、上一行以句号结尾：不能被当成新条目切开。"""
    lines = [
        line(100, [(40, "[6] F. Zhao et al., “Gate-aware online planning,” in Proc. IEEE Int.", {})]),
        line(111, [(40, "Conf. Robot. Automat., May 2025, pp. 8536–8542.", {})]),
        line(122, [(40, "[7] Y. Shao et al., “Design and evaluation,” in Proc. IEEE, 2020.", {})]),
        line(133, [(40, "[8] S. Liu et al., “Planning dynamically feasible trajectories,” RA-L, 2019.", {})]),
    ]
    blocks = layout.segment_paragraphs(lines, 8, 10.0, in_references=True)
    assert [b.text[:3] for b in blocks] == ["[6]", "[7]", "[8]"]
    assert "Conf. Robot." in blocks[0].text


def test_hanging_indent_continuation_lines_belong_to_the_previous_reference():
    lines = [
        line(100, [(108, "Oana-Maria Camburu, Tim Rocktäschel, Thomas Lukasiewicz, Phil Blunsom. 2018. e-SNLI:", {"w": 390})]),
        line(111, [(118, "Natural language inference with explanations. NeurIPS.", {})]),
        line(122, [(108, "Howard Chen, Jacqueline He. 2022. Can rationalization improve?", {})]),
    ]
    blocks = layout.segment_paragraphs(lines, 10, 10.0, in_references=True)
    assert len(blocks) == 2 and "Natural language inference" in blocks[0].text


def test_roman_numeral_table_captions_are_captions():
    assert layout.is_caption("TABLE I")
    assert layout.is_caption("TABLE II COMPARISON OF CORRIDOR GENERATION METHODS")
    assert not layout.is_caption("Table 1 shows the results of our experiments on the dataset")


def test_numbered_subsection_headings_with_a_control_glyph_or_long_italic_title():
    heading = line(100, [(38, "D. Planner Comparison in Cluttered Environments", {"italic": True})])
    assert layout._is_heading_line(heading, 251.0, 10.0, None)  # 占栏宽 82%，IEEE 斜体小节标题
    sem = line(100, [(305, "3.3.1  Memory", {"bold": True})], size=9.2)
    assert layout._is_heading_line(sem, 252.0, 10.0, None)  # 编号占了字符的大半，字母占比不能把它判成图内数字
    eq = line(100, [(46, "1 D ZH i∗ZHT i", {"bold": True})])
    assert not layout._is_heading_line(eq, 252.0, 10.0, None)  # 含数学符号的是公式碎片


def test_far_apart_mixed_size_text_on_one_baseline_is_not_one_line():
    """章节标题（12pt）和同一基线附近、隔得很远的图例（9pt）不能聚成一行。"""
    heading = [sp("5", 108, 82.0, 12.0), sp("Symbolic Reasoning", 126, 82.0, 12.0)]
    legend = [sp("Standard prompting", 390, 83.9, 9.0)]
    lines = layout.build_lines(heading + legend, None, 10.0, 792.0)
    assert sorted(l.text for l in lines) == ["5 Symbolic Reasoning", "Standard prompting"]


def test_caption_beside_wrapped_text_is_pulled_out_of_the_paragraph_lines():
    """文字绕排的图：图注排在右边、正文排在左边，同一基线上被合成了一行。"""
    rows = []
    for k, y in enumerate((260.0, 271.0, 282.0)):
        left = sp(f"left text line number {k} goes here", 108, y, w=225)
        right = [sp("Figure 2: PaLM 540B uses chain-of-thought", 346, y, w=150)] if k == 0 else [sp(f"caption continues on row {k}", 346, y, w=150)]
        rows.append(_line_from([left, *right]))
    rest, caps = layout.split_side_captions(rows, 2, 10.0)
    assert len(caps) == 1 and caps[0].kind == "caption" and caps[0].text.startswith("Figure 2:") and "caption continues on row 2" in caps[0].text
    assert [l.text for l in rest] == [f"left text line number {k} goes here" for k in range(3)]


def test_spaceless_text_runs_get_word_spaces_from_glyph_gaps():
    chars = []
    x = 100.0
    for word in ("enabling", "the", "representation"):
        for c in word:
            chars.append({"c": c, "bbox": (x, 0, x + 4.0, 10)})
            x += 4.0
        x += 1.4  # 词间只有位置上的空隙，没有空格字符
    text = "".join(c["c"] for c in chars)
    assert layout._looks_spaceless(text)
    assert layout._respace(text, {(0.0, 100.0): chars}, (0.0, 100.0), 9.5) == "enabling the representation"
    assert not layout._looks_spaceless("https://example.com/paper")  # 正常的网址、公式不受影响（字母占比不够）
