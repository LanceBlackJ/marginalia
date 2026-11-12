"""解析的回归测试：每一条都来自真实论文里出过的问题（用合成的片段重现，不依赖 PDF 文件）。"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def _page_with_inline_fraction():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "f(t) =", fontsize=10)
    page.insert_text((72, 140), "A longer body line of ordinary text so that ten points is the body size of this page.", fontsize=10)
    page.insert_text((104, 97), "ab", fontsize=7)  # 分子
    page.draw_line((103, 100.6), (116, 100.6), width=0.4)  # 分数线
    page.insert_text((108, 108), "2", fontsize=7)  # 分母
    page.insert_text((120, 100), ". Since", fontsize=10)
    return doc, page


def test_inline_stacked_fraction_is_one_span_on_the_baseline_of_the_text_beside_it():
    """“s̈(t) = ∂ₛb(s)/2” 以前分子被并进上面的公式块、分母单独成行，正文只剩 “= 2”。"""
    doc, page = _page_with_inline_fraction()
    spans = layout.extract_spans(page)
    fr = [s for s in spans if s.frac]
    assert len(fr) == 1 and fr[0].text == "(ab)/(2)"
    assert abs(fr[0].baseline - 100.0) < 0.5 and fr[0].size == 10.0
    assert not [s for s in spans if s.text.strip() in ("ab", "2")]  # 原来的分子分母片段不再单独存在
    doc.close()


def test_full_size_fraction_and_plain_rules_are_left_alone():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "x = ", fontsize=10)
    page.insert_text((100, 95), "ab", fontsize=10)  # 整行大小的分式不是行内分式
    page.draw_line((99, 98), (115, 98), width=0.4)
    page.insert_text((104, 110), "2", fontsize=10)
    assert not [s for s in layout.extract_spans(page) if s.frac]
    doc.close()


# ---------------- 2026-11 跨期刊真实论文测试里发现的问题 ----------------
def _words(y, x_start, x_end, n=10, size=10.0):
    """一行 n 个词（每个词一个片段，和 PRL / PLOS 的 PDF 一样）。"""
    step = (x_end - x_start) / n
    jitter = (round(y) * 7) % 11  # 真实文字的词间空隙不会逐行对齐成竖条：每行错开一点，词和词之间只留 1-2pt
    out, x = [], x_start + jitter % 4
    for i in range(n):
        w = step * (0.92 + ((i + round(y)) % 3) * 0.02)
        out.append(sp("w", x, y, size, w=w))
        x += w + 1.5
    return out


def test_gutter_found_when_full_width_abstract_sits_above_two_columns():
    """PRL 首页：通栏的标题、摘要盖住栏沟，原来的覆盖度直方图失效，两栏的行被逐行交错拼在一起。"""
    spans = []
    for k in range(12):  # 通栏摘要
        spans += _words(130 + k * 12, 60, 550, n=14)
    for k in range(30):  # 下面两栏
        spans += _words(330 + k * 12, 52, 297, n=7)
        spans += _words(330 + k * 12, 315, 560, n=7)
    g = layout.detect_gutter(spans, 792.0)
    assert g is not None and 290 <= g[0] <= 305 and 305 <= g[1] <= 320


def test_gutter_found_for_narrow_sidebar_next_to_main_text():
    """PLOS ONE 首页：左边一条窄侧栏（版权、日期、基金），沟不在页面正中。"""
    spans = []
    for k in range(40):
        spans += _words(100 + k * 12, 40, 170, n=4)    # 侧栏
        spans += _words(100 + k * 12, 200, 560, n=12)  # 正文
    g = layout.detect_gutter(spans, 792.0)
    assert g is not None and 160 <= g[0] <= 185 and 185 <= g[1] <= 205


def test_sidebar_gutter_is_found_even_when_a_wider_free_strip_exists_on_the_right():
    """PLOS ONE 首页：靠右排版的零星几行在页面右侧留出一条比侧栏沟更宽的空档，只看最宽的一条就找不到真正的侧栏沟。"""
    spans = []
    for k in range(40):
        spans += _words(100 + k * 12, 40, 170, n=4)    # 侧栏
        spans += _words(100 + k * 12, 200, 470, n=10)  # 正文（右边留出一条比侧栏沟更宽的空白）
    for k in range(6):
        spans += _words(600 + k * 12, 200, 560, n=12)
    g = layout.detect_gutter(spans, 792.0)
    assert g is not None and 160 <= g[0] <= 185 and 185 <= g[1] <= 205, g


def test_hanging_indent_references_are_not_taken_for_a_sidebar():
    """单栏的参考文献（第一行顶格、后面的行缩进）：长行从任何偶然的空白条上跨过去，不能因此切成两栏。"""
    spans = []
    for k in range(40):
        if k % 3 == 0:
            spans += _words(100 + k * 12, 108, 500, n=14)
        else:
            spans += _words(100 + k * 12, 120, 350, n=8)
    assert layout.detect_gutter(spans, 792.0) is None


def test_single_column_page_has_no_gutter():
    spans = []
    for k in range(40):
        spans += _words(100 + k * 12, 60, 550, n=14)
    assert layout.detect_gutter(spans, 792.0) is None


def test_big_sum_glyph_does_not_chain_lines_together():
    """行内的大号求和号（基线抬高）把参照基线带偏，上下两行（甚至另一栏）被串成一行，词序混乱、词被粘在一起。"""
    big = sp("P", 300, 523.0, size=14.0)
    big.y0, big.y1 = 509.0, 527.0
    lines = [[sp(w, 54 + i * 30, base) for i, w in enumerate(("The", "max", "operator"))] for base in (523.4, 534.4, 545.3)]
    spans = [s for ln in lines for s in ln] + [big]
    clusters = layout._cluster(spans)
    texts = sorted(" ".join(s.text for s in sorted(c, key=lambda s: s.x0)) for c in clusters)
    assert len([c for c in clusters if len(c) >= 3]) == 3, texts  # 三行还是三行


def test_accented_letters_are_composed_but_math_dots_are_not():
    assert layout.clean_text("J¨urgen Schmidhuber, Jos´e, Arbel´aez, Fran¸cois") == "Jürgen Schmidhuber, José, Arbeláez, François"
    assert layout.clean_text("x = ¨s(t)") == "x = ¨s(t)"  # 公式里的 s̈ 前面不是字母


def test_real_hyphens_in_compounds_survive_line_breaks_but_syllable_breaks_do_not():
    from paperdesk.parsing import pdf_parser as P
    from paperdesk.parsing.pdf_parser import ParsedPassage

    sh = layout.SOFT_HYPHEN
    texts = [
        "A task-specific model is used. Another task-specific head. The classification result and classification score.",
        f"We use task{sh}specific heads and classi{sh}fication scores, sequence{sh}aligned RNNs, the sequence length, the sequence order, aligned data and aligned labels.",
    ]

    class D:  # 只用到 passages / metadata / sections
        passages = [ParsedPassage(i, 1, "paragraph", t, (0, 0, 1, 1), None) for i, t in enumerate(texts)]
        metadata = type("M", (), {"abstract": None, "title": None})()
        sections: list = []

    P._resolve_soft_hyphens(D)
    out = D.passages[1].text
    assert "task-specific" in out  # 别处出现过带连字符的写法
    assert "classification scores" in out  # 别处出现过连起来的写法（断词）
    assert "sequence-aligned" in out  # 前后两截都是文中常用的词：复合词
    assert sh not in out


def test_collaboration_style_authors():
    from paperdesk.parsing.metadata import guess_authors, split_authors

    assert split_authors("B. P. Abbott et al.*") == ["B. P. Abbott"]
    got = guess_authors(["Ashish Vaswani∗ Noam Shazeer∗ Niki Parmar∗ Jakob Uszkoreit∗ Google Brain Google Brain Google Research Google Research avaswani@google.com"])
    assert got == ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"]
    assert guess_authors(["CERN-PH-EP-2012-218", "The ATLAS Collaboration"]) == ["The ATLAS Collaboration"]
    assert guess_authors(["The Swiss AI Lab IDSIA"]) == []


# ---------------- 2026-11 第二批跨期刊论文（Batch Norm / VGG / ELMo / GAN / Frontiers / CMS / Nature Materials）里发现的问题 ----------------
def test_heading_beside_body_text_is_split_by_the_column_gap_not_the_largest_font():
    """右栏顶上 14pt 的粗体标题、左栏同一基线上 10pt 的正文，中间只隔 10pt：按两侧紧挨着的片段里较小的字号判断，仍是两栏。"""
    gutter = (301.0, 311.0)
    spans = [sp("change in the", 230, 46, 10.0, w=70), sp("2", 311, 46, 14.3, bold=True, w=7), sp("Towards", 333, 46, 14.3, bold=True, w=52)]
    lines = layout.build_lines(spans, gutter, 10.0)
    assert sorted(l.cls for l in lines) == ["L", "R"]


def test_two_authors_side_by_side_are_read_before_the_left_column_body():
    """“Sergey Ioffe | Christian Szegedy” 并排两格：右边的作者不能排到左栏摘要、引言的后面去。"""
    def ln(cls, y, text, x0, x1):
        l = line(y, [(x0, text, {"w": x1 - x0})])
        l.cls = cls
        return l

    body_l = [ln("L", 200 + 12 * k, "Training deep networks is complicated by the fact that the distribution", 72, 300) for k in range(6)]
    body_r = [ln("R", 200 + 12 * k, "of each layer inputs changes during training as the parameters change", 311, 540) for k in range(6)]
    top_l = [ln("L", 100, "Sergey Ioffe", 72, 140), ln("L", 118, "Google Inc., sioffe@google.com", 72, 230)]
    top_r = [ln("R", 100, "Christian Szegedy", 330, 420), ln("R", 118, "Google Inc., szegedy@google.com", 330, 480)]
    out = layout.reading_order(body_l + body_r + top_l + top_r)
    assert [l.text for l in out[:4]] == ["Sergey Ioffe", "Google Inc., sioffe@google.com", "Christian Szegedy", "Google Inc., szegedy@google.com"]


def test_appendix_numbering_and_all_caps_appendix_titles_end_the_references():
    from paperdesk.parsing import pdf_parser as P

    assert P._heading_level_numbered("B.1 TRAINING") == (2, "num")
    assert P._heading_level_numbered("B.1.2 SELF-SUPERVISION") == (3, "num")
    assert P._appendix_head("A LOCALISATION") and P._appendix_head("A.1 LOCALISATION CONVNET")
    assert not P._appendix_head("Y. Bengio. Maxout networks. In ICML, 2013.")


def test_wrapped_two_line_heading_is_merged_into_one_section():
    from paperdesk.parsing import pdf_parser as P

    a = layout.Block(3, 72, 419, 300, 439, "3 Normalization via Mini-Batch", 14.3, 1.0, 1, "L", lines=[], heading_hint=True)
    b = layout.Block(3, 93, 437, 200, 457, "Statistics", 14.3, 1.0, 1, "L", lines=[], heading_hint=True)
    c = layout.Block(3, 72, 470, 300, 520, "Since the full whitening of each layer inputs is costly.", 10.0, 0.0, 4, "L")
    out = P._merge_wrapped_headings([a, b, c], set(), set())
    assert [x.text for x in out] == ["3 Normalization via Mini-Batch Statistics", c.text]
    d = layout.Block(3, 72, 437, 300, 457, "3.1 Another numbered heading", 14.3, 1.0, 1, "L", lines=[], heading_hint=True)
    assert len(P._merge_wrapped_headings([a, d], set(), set())) == 2  # 带编号的下一条是新标题，不并


def test_numbered_bold_heading_with_math_or_lowercase_words_is_a_heading():
    big = lambda t: line(100, [(72, t, {"bold": True, "w": 230})])
    assert layout._is_heading_line(big("4.1 Global Optimality of pg = pdata"), 240, 10.0)
    assert layout._is_heading_line(big("5.1 Alternate layer weighting schemes"), 240, 10.0)
    assert layout._is_heading_line(big("5.3 What information is captured by the biLM representations?"), 260, 10.0)


def test_table_header_cells_are_not_sections():
    from paperdesk.parsing import pdf_parser as P

    cells = [layout.Block(5, 80 + 60 * k, 300 + 10 * k, 130 + 60 * k, 310 + 10 * k, t, 10.9, 1.0, 1, "F", heading_hint=True)
             for k, t in enumerate(["TASK PREVIOUS SOTA", "INCREASE", "(ABSOLUTE/", "RELATIVE)"])]
    real = layout.Block(6, 72, 100, 200, 112, "5 Analysis", 10.9, 1.0, 1, "L", heading_hint=True)
    P._drop_table_header_hints(cells + [real], 10.0)
    assert not any(c.heading_hint for c in cells) and real.heading_hint


def test_all_caps_heading_on_its_own_first_line_becomes_a_run_in_section():
    lines = [line(100, [(55, "MATERIALS THAT SHOULD NOT EXIST", {"w": 200})]), line(112, [(55, "More than 70 years ago, Landau and Peierls argued that", {"w": 450})]),
             line(124, [(55, "strictly two-dimensional crystals were thermodynamically unstable.", {"w": 380})])]
    blocks = layout.segment_paragraphs(lines, 1, 10.0)
    assert len(blocks) == 1 and blocks[0].run_in == "MATERIALS THAT SHOULD NOT EXIST" and blocks[0].text.startswith("More than 70")


def test_superscript_exponent_a_little_too_high_joins_its_line():
    """fb⁻¹：上标字号 8.3、基线抬高 4.9，和正文的竖向重叠刚好不够 65%：不能自成一行把摘要切成三段。"""
    body = [sp("5.1 fb", 95, 460.0, 10.9, w=25), sp("at 7 TeV and 5.3 fb", 134, 460.0, 10.9, w=88)]
    sup = [sp("−", 120, 455.1, 8.3, w=6), sp("1", 126, 455.1, 8.0, w=4), sp("−", 222, 455.1, 8.3, w=6), sp("1", 228, 455.1, 8.0, w=4)]
    lines = layout.build_lines(body + sup, None, 10.9)
    assert len(lines) == 1


def test_radical_drawn_on_the_previous_line_moves_to_the_line_of_its_radicand():
    """“√s”：根号字形抽出来落在上一行的末尾，被开方的 s 在下一行。"""
    spans = [sp("collisions at", 95, 432.1, 10.9, w=80), sp("s", 203, 432.1, 10.9, w=4), sp("= 7 TeV", 213, 432.1, 10.9, w=40),
             sp("presented", 151, 418.7, 10.9, w=48), sp("√", 193, 418.7, 11.4, w=10), sp("from", 212, 418.7, 10.9, w=24)]
    spans[4].y0, spans[4].y1 = 414.8, 426.1
    lines = layout.build_lines(spans, None, 10.9)
    assert any("√" in l.text and "TeV" in l.text for l in lines) and not any("√" in l.text and "presented" in l.text for l in lines)


def test_report_numbers_and_parentheses_are_not_authors():
    from paperdesk.parsing.metadata import guess_authors

    assert guess_authors(["CERN-PH-EP/2012-220 2024/11/27", "CMS-HIG-12-028", "The CMS Collaboration"]) == ["The CMS Collaboration"]
    got = guess_authors(["B. P. Abbott et al. (LIGO Scientific Collaboration and Virgo Collaboration)"])
    assert all("(" not in a and ")" not in a for a in got)


def test_authors_joined_by_ampersand_with_plus_marker():
    from paperdesk.parsing.metadata import guess_authors

    got = guess_authors(["Karen Simonyan∗ & Andrew Zisserman+ Visual Geometry Group, Department of Engineering Science, University of Oxford {karen,az}@robots.ox.ac.uk"])
    assert got == ["Karen Simonyan", "Andrew Zisserman"]


def test_side_band_next_to_a_wide_abstract_is_found_and_split():
    """Frontiers 首页：上面通栏的标题作者，中段左边窄侧栏 + 右边宽摘要，下面两栏正文。侧栏和摘要各排各的行距。"""
    spans = []
    for k in range(8):  # 通栏的单位行
        spans += _words(120 + k * 12, 45, 550, n=14)
    for k in range(22):  # 侧栏（行距 9.5）
        spans += _words(240 + k * 9.5, 45, 150, n=4)
    for k in range(18):  # 摘要（行距 12，从 174 起）
        spans += _words(240 + k * 12, 174, 550, n=13)
    for k in range(10):  # 下面两栏正文
        spans += _words(500 + k * 12, 45, 292, n=7)
        spans += _words(500 + k * 12, 303, 550, n=7)
    band = layout.detect_side_band(spans, 780.0)
    assert band is not None and 148 <= band[0] <= 175 and 150 <= band[1] <= 176 and band[2] < 260 and 440 <= band[3] <= 500, band
    # 普通两栏没有这样的侧栏
    plain = []
    for k in range(40):
        plain += _words(100 + k * 12, 45, 292, n=7)
        plain += _words(100 + k * 12, 303, 550, n=7)
    assert layout.detect_side_band(plain, 780.0) is None


# ---------------- 第二轮：参考文献条数、Frontiers/PLOS 新版式 ----------------
def test_reference_unfinished_detection():
    from paperdesk.parsing import pdf_parser as P

    assert P._ref_unfinished("[3] K. Simonyan and A. Zisserman, “Very deep convolutional")
    assert not P._ref_unfinished("[2] R. Girshick, “Fast R-CNN,” in IEEE International Conference on Computer Vision (ICCV), 2015.")
    assert not P._ref_unfinished("Brass, M., and Haggard, P. (2008). The model. Neuroscientist 14, 319–325. doi: 10.1177/1073858408317417")
    assert not P._ref_unfinished("Cross, K. A. (2013). Optimized neural coding? Hum. Brain Mapp. 34, 213–225")


def test_author_year_reference_after_a_doi_wrapped_line_starts_a_new_entry():
    """Frontiers 的参考文献第一行顶格、后面的行缩进，DOI 折行后（“09.2010”）下一条仍要另起一条。"""
    lines = [
        line(100, [(46, "Daniel, R., and Pollmann, S. (2010). Comparing the neural basis of", {"w": 244})]),
        line(110, [(55, "category learning. J. Neurosci. 30, 47–55. doi: 10.1523/JNEUROSCI.2205-", {"w": 235})]),
        line(120, [(55, "09.2010", {})]),
        line(130, [(46, "Desmurget, M., Reilly, K. T., and Sirigu, A. (2009). Movement intention. Science 324, 811.", {"w": 244})]),
    ]
    blocks = layout.segment_paragraphs(lines, 11, 10.0, in_references=True)
    assert len(blocks) == 2 and blocks[1].text.startswith("Desmurget")


def test_heading_fonts_are_found_from_isolated_short_lines():
    def mk(y, text, font, size=10.0):
        s = Span(58, y - size, 58 + 5 * len(text), y + 2, y, text, size, False, False, font, False)
        return _line_from([s])

    lines = []
    for k in range(6):  # 正文行，夹着 6 个标题（另一个字体）
        lines += [mk(100 + k * 60, "Results and discussion", "AdvP41461E")]
        lines += [mk(100 + k * 60 + 12 * j + 12, "The quick brown fox jumps over the lazy dog and runs away from home", "AdvP49811", 9.0) for j in range(4)]
    assert layout.detect_heading_fonts(lines, 9.0) == frozenset({"AdvP41461E"})
    para = [mk(1000 + 12 * j, "Some italic words that go on for a while without ending", "Italic") for j in range(8)]
    assert layout.detect_heading_fonts(lines + para, 9.0) == frozenset({"AdvP41461E"})  # 整段斜体行与行之间字体不变，不算标题字体


def test_equation_fragment_numbered_like_a_heading_is_not_a_heading():
    assert not layout._is_heading_line(line(100, [(72, "2 S s T s 2", {"bold": True, "w": 50})]), 240, 10.0)
    assert not layout._is_heading_line(line(100, [(72, "1 2 3 4", {"w": 30})]), 240, 10.0)


def test_algorithm_steps_are_one_paragraph_each_and_never_display_formulas():
    """伪代码框：“5: H∗, (y, T) ← IsMaxVolume(H)”和“6: P∗ ← Resample(…)”原来并成一行，星号上标成了独立的大号公式块。
    每个“N:”步骤一块、一个 paragraph；小字号的上标片段并回它所在的步骤行。"""
    def step(y, text, x=324.0, size=10.0):
        return line(y, [(x, text, {"w": 5.0 * len(text)})], size)

    star = _line_from([sp("\u2217 (\u2217) \u2217", 358, 153.0, 7.0, w=40)])
    star.cls = "R"
    lines = [step(112, "4: H \u2190 Eq (29) \u2190Y\u2217"), step(158, "5: H , (yr , T ) \u2190 IsMaxVolume(H)"), star,
             step(171, "6: P\u2217 \u2190 Resample(y(r\u2217), T \u2217)"), step(183, "7: u \u2190 NMPC (P , H )"), step(194, "8: end if")]
    for l in lines:
        l.cls = "R"
    blocks = layout.segment_paragraphs(lines, 5, 10.0)
    assert [b.kind for b in blocks] == ["paragraph"] * 5, [(b.kind, b.text) for b in blocks]
    assert [b.text[:2] for b in blocks] == ["4:", "5:", "6:", "7:", "8:"]
    assert "\u2217" in blocks[1].text and blocks[1].text.startswith("5: H")  # 上标并回了第 5 步
    # 正文里偶然以 “3: ” 开头、没有相邻编号步骤的行不当步骤
    assert layout._algo_step_ids([step(100, "3: the ratio is large")]) == set()


# ---------------- 第三轮：用户下载的 Nature/Cell/Elsevier/ACM/Oxford/Springer 论文里发现的问题 ----------------
def test_html_entities_in_the_text_layer_are_decoded():
    assert layout.clean_text("PGC-1&alpha; and &alpha;-Catenin &mdash; done") == "PGC-1α and α-Catenin — done"
    assert layout.clean_text("R&D & more") == "R&D & more"  # 不是实体的 & 不动


def test_root_bar_glyph_run_is_not_text():
    assert layout.clean_text("the value pﬃﬃﬃﬃ N and pffiffiffiffiffiffi x") == "the value √ N and √ x"


def test_glosa_math_is_a_text_font_not_a_math_font():
    """Nature 的正文字体叫 GlosaMath-Roman：名字里有 math，但是正文。"""
    assert not layout.MATH_FONT_RE.search("GlosaMath-Roman") and layout.MATH_FONT_RE.search("CMMI10")


def test_authors_with_affiliation_digit_before_the_star_and_superscript_letters():
    from paperdesk.parsing.metadata import guess_authors

    got = guess_authors(["David Silver1*, Aja Huang1*, Chris J. Maddison1, Arthur Guez1, Laurent Sifre1, George van den Driessche1"])
    assert got[:3] == ["David Silver", "Aja Huang", "Chris J. Maddison"], got
    assert guess_authors(["Fei Sun, Roberto Cipolla, Senior Member, IEEE"]) == ["Fei Sun", "Roberto Cipolla"]
    assert guess_authors(["BIOINFORMATICS ORIGINAL PAPER", "Associate Editor: Martin Bishop", "Yang Liao, Gordon K. Smyth and Wei Shi"])[:1] == ["Yang Liao"]


def test_two_consecutive_numbered_equations_stay_two_blocks():
    """(33)(34) 两个紧挨着的编号公式：不能并成一个块、丢掉前一个编号。"""
    lines = [
        line(581, [(85, "s.t. x0 = xinit,", {"w": 90}), (272, "(33)", {})]),
        line(598, [(100, "xk+1 = xk + f(xk, uk)Δt,", {"w": 150}), (272, "(34)", {})]),
    ]
    blocks = layout.segment_paragraphs(lines, 5, 10.0)
    assert [b.eq_number for b in blocks if b.kind == "formula"] == ["33", "34"], [(b.kind, b.text) for b in blocks]


def test_cross_page_paragraph_continuation_is_joined():
    from paperdesk.parsing import pdf_parser as P

    a = layout.Block(3, 300, 700, 552, 750, "polynomials in s. For brevity, we", 10.0, 0.0, 2, "R")
    b = layout.Block(4, 48, 60, 289, 72, "use the a_j to explain,", 10.0, 0.0, 1, "L")
    c = layout.Block(4, 48, 90, 289, 130, "Another paragraph starts here.", 10.0, 0.0, 3, "L")
    out = P._join_cross_page([a, b, c], set())
    assert [x.text for x in out] == ["polynomials in s. For brevity, we use the a_j to explain,", "Another paragraph starts here."]
    assert out[0].cont == [b]


def test_orphan_superscript_stacked_on_a_subscript_joins_the_text_line():
    """c_a^{(j)}：上标 “(j)” 压在下标 “a” 上面、离正文基线太远，自成一行会把一句话截成三段。"""
    body = [sp("use the a", 72, 100.0, 10.0, w=40), sp("(ˆs, c", 114, 100.0, 10.0, w=30), sp("a", 146, 102.0, 7.0, w=4)]
    sup = [sp("(j)", 146, 95.0, 7.0, w=10)]
    lines = layout.build_lines(body + sup, None, 10.0)
    assert len(lines) == 1, [l.text for l in lines]


def test_panel_label_helpers():
    from paperdesk.parsing import pdf_parser as P
    assert P._prose_like("This protein binds the receptor and then recruits the kinase to membranes")
    assert not P._prose_like("00 - - + 10 5")
    assert P._sentence_like("Not applicable.")
    assert not P._sentence_like("MCT4 12010080")


def test_overprinted_label_fragments_are_dropped():
    """Cell 的示意图把标签画了好几层（整词 “Stabilizes” 上面再叠 “Sta”“es”）：字典式提取不去重，会读成 “StabilizesStaes”。"""
    whole = sp("Stabilizes", 100, 200, 8.0, w=50)
    frag1 = sp("Sta", 100, 200, 8.0, w=15)
    frag2 = sp("es", 135, 200, 8.0, w=12)
    other = sp("Agrin", 100, 230, 8.0, w=30)
    kept = layout._drop_overprint([whole, frag1, frag2, other])
    assert [s.text for s in kept] == ["Stabilizes", "Agrin"]
    dup = layout._drop_overprint([sp("AChR", 10, 10, 8.0, w=20), sp("AChR", 10, 10, 8.0, w=20)])
    assert len(dup) == 1


# ---- 第二轮：用不同版式的论文测出来的问题 ----
ZH_FONT = next((str(p) for p in (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc")) if p.exists()), None)


def _pdf_from_pages(tmp_path, pages, name="t.pdf"):
    import pymupdf

    doc = pymupdf.open()
    for items in pages:
        pg = doc.new_page(width=595, height=842)
        for x, y, text, size, font in items:
            if font == "zh":
                pg.insert_font(fontname="zh", fontfile=ZH_FONT)
            pg.insert_text((x, y), text, fontsize=size, fontname=font)
    path = tmp_path / name
    doc.save(str(path))
    return path


@pytest.mark.skipif(ZH_FONT is None, reason="本机没有中文字体")
def test_chinese_numbered_headings_become_sections(tmp_path):
    from paperdesk.parsing.pdf_parser import parse_pdf

    body = "参评各类奖学金、荣誉均需同时满足以下基本条件，并按照学院规定的时间和程序提交相应的证明材料以供审核。"
    items = [(150, 70, "奖学金评审细则", 18, "zh")] + [(70, 110 + 20 * i, body, 12, "zh") for i in range(4)]
    items += [(70, 210, "一、基本条件", 14, "zh")] + [(70, 240 + 20 * i, body, 12, "zh") for i in range(6)]
    items += [(70, 380, "（一）体育竞技奖", 14, "zh")] + [(70, 410 + 20 * i, body, 12, "zh") for i in range(6)]
    d = parse_pdf(_pdf_from_pages(tmp_path, [items]))
    titles = [s.title for s in d.sections]
    assert "一、基本条件" in titles and "（一）体育竞技奖" in titles
    assert next(s.level for s in d.sections if s.title.startswith("（一）")) > next(s.level for s in d.sections if s.title.startswith("一、"))


def test_chinese_sentence_split_by_a_page_break_is_joined_without_a_space():
    from paperdesk.parsing.pdf_parser import _cjk_cont

    assert _cjk_cont("获大连理工大学一等奖学金，可直接获大", "连理工大学“优秀大学生”荣誉称号。")
    assert not _cjk_cont("获大连理工大学一等奖学金。", "连理工大学")  # 不影响英文/已结束的句子由别处判断；这里只管汉字结尾
    assert not _cjk_cont("条件如下：", "（一）体育竞技奖")  # 新的小节编号不是续句


def test_author_guess_ignores_chinese_sentences():
    from paperdesk.parsing.metadata import split_authors

    assert split_authors("绩得分，L代表科研论文得分；") == []
    assert split_authors("张三 李四 王五") == ["张三", "李四", "王五"]


def test_vertical_ocr_margin_stamp_is_dropped():
    from pathlib import Path

    from paperdesk.parsing import ocr

    def fake(png):
        return [([[10, 10], [60, 10], [60, 400], [10, 400]], "arXiv:1406.2661v1 [stat.ML] 10 Jun 2014", 0.9),
                ([[100, 50], [400, 50], [400, 70], [100, 70]], "Generative Adversarial Nets", 0.95)]

    import pymupdf

    # 一页空白 PDF 就够：OCR 引擎是注入的假引擎
    doc = pymupdf.open(); doc.new_page(width=595, height=842)
    import tempfile, os

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.pdf"; doc.save(str(p)); doc.close()
        ocr.set_engine(fake)
        try:
            spans = ocr.ocr_page(p, 1)
        finally:
            ocr.set_engine(None)
    assert [s.text for s in spans] == ["Generative Adversarial Nets"]


# ---- 第二轮多版式测试的回归（对应 PARSER_VERSION 25 的改动）----
def _blk(text, y0=100.0, size=10.0, bold=0.0, nlines=1, kind="paragraph", hint=False, font="F1", page=1, col="L"):
    from paperdesk.parsing.layout import Block

    ln = line(y0 + size, [(50, text, {"font": font, "bold": bold > 0.5})], size=size)
    return Block(page, 50, y0, 50 + 5 * len(text), y0 + size * nlines, text, size, bold, nlines, col, kind, heading_hint=hint, lines=[ln])


def test_table_header_cells_followed_by_number_rows_are_not_headings():
    from paperdesk.parsing import pdf_parser as P

    blocks = [_blk("Method Error %", 100, bold=1.0, hint=True), _blk("Binary Features (Netzer et al., 2011) 36.7 HOG 35.1 Stacked Sparse Coding 30.6 K-Means 29.7", 120, nlines=3),
              _blk("Method", 300, bold=1.0, hint=True), _blk("Type", 312, bold=1.0, hint=True),
              _blk("Standard Neural Net 1.6 Logistic 1.4 SVM Gaussian kernel 1.4 Dropout NN 1.25 DBN 1.18", 324, nlines=3)]
    P._drop_table_header_hints(blocks, 11.0)
    assert not blocks[0].heading_hint and not blocks[2].heading_hint


def test_real_back_matter_headings_survive_the_table_header_rule():
    from paperdesk.parsing import pdf_parser as P

    blocks = [_blk("Code availability", 100, bold=1.0, hint=True), _blk("The code is available at https://github.com/x/y (2020) and 12 other places 1 2 3", 120, nlines=2)]
    P._drop_table_header_hints(blocks, 10.0)
    assert blocks[0].heading_hint


def test_dotted_roman_heading_is_not_demoted_even_in_a_label_font():
    from paperdesk.parsing import layout, pdf_parser as P

    old = layout.LABEL_FONTS
    layout.LABEL_FONTS = frozenset({"Adv.B"})
    try:
        blocks = [_blk("I. INTRODUCTION", 100, hint=True, font="Adv.B"), _blk("In 1916, the year after the final formulation of the field equations of general relativity, Einstein predicted.", 120, nlines=3)]
        P._demote_panel_labels(blocks, 10.0)
        assert blocks[0].heading_hint
        panel = [_blk("15 Spo11 oligos", 100, hint=True, font="Adv.B"), _blk("12 8", 120)]
        P._demote_panel_labels(panel, 10.0)
        assert not panel[0].heading_hint  # 面板标签仍然被撤掉
    finally:
        layout.LABEL_FONTS = old


def test_bold_abstract_first_line_is_text_not_a_heading_and_rejoins_its_continuation():
    from paperdesk.parsing import layout, pdf_parser as P

    old = layout.LABEL_FONTS
    layout.LABEL_FONTS = frozenset()
    a = _blk("Deeper neural networks are more difficult to train. We", 100, hint=True, bold=1.0)
    b = _blk("present a residual learning framework to ease the training of networks", 112, nlines=3)
    blocks = [a, b]
    try:
        P._demote_panel_labels(blocks, 10.0)
    finally:
        layout.LABEL_FONTS = old
    assert len(blocks) == 1 and blocks[0].text.startswith("Deeper neural networks") and "present a residual" in blocks[0].text


def test_a_sentence_in_the_heading_font_is_not_a_heading():
    from paperdesk.parsing import pdf_parser as P

    assert not P._is_heading_block(_blk("The depth of representations is of central importance. For many visual tasks we", 100, hint=True, bold=1.0), 10.0)
    assert P._is_heading_block(_blk("3.2. Identity Mapping by Shortcuts", 100, hint=True, bold=1.0), 10.0)


def test_wrapped_heading_last_word_rejoins_the_heading():
    from paperdesk.parsing import layout, pdf_parser as P

    a = _blk("Cold storage exacerbates mitochondrial and renal damage after", 100, size=11.0, bold=1.0, hint=True)
    b = _blk("transplantation", 112, size=11.0, bold=1.0, hint=False)
    out = P._merge_wrapped_headings([a, b], set(), set())
    assert len(out) == 1 and out[0].text.endswith("after transplantation")


def test_chinese_heading_patterns():
    from paperdesk.parsing import pdf_parser as P

    assert P._heading_level_numbered("一、基本条件") == (1, "cn")
    assert P._heading_level_numbered("（二）创新创业奖")[0] == 2
    assert P._heading_level_numbered("第三章 总则")[0] == 1


def test_two_authors_followed_directly_by_the_affiliation():
    from paperdesk.parsing.metadata import _names_in_block, guess_authors

    assert guess_authors(["Karen Simonyan & Andrew Zisserman Visual Geometry Group, Department of Engineering Science, University of Oxford"]) == ["Karen Simonyan", "Andrew Zisserman"]
    assert guess_authors(["T. Kalewicz and V. Bommier"]) == ["T. Kalewicz", "V. Bommier"]
    assert "⃝c ESO" not in guess_authors(["⃝c ESO 2019", "T. Kalewicz and V. Bommier"])


def test_stacked_baselines_are_split_into_their_own_lines():
    from paperdesk.parsing.layout import _split_stacked

    group = [sp("Define", 100, 100, 12), sp("inf", 150, 100, 12, w=20), sp("where", 200, 100, 12), sp("infimum", 250, 100, 12),
             sp("is", 100, 114, 12), sp("just", 130, 114, 12), sp("the", 170, 114, 12), sp("lowest", 200, 114, 12), sp("eigenvalue", 260, 114, 12)]
    rows = _split_stacked(group)
    assert len(rows) == 2 and {s.text for s in rows[0]} >= {"Define", "where"} and {s.text for s in rows[1]} >= {"lowest", "eigenvalue"}
    one_line = [sp("a", 100, 100, 12), sp("b", 120, 100, 12)]
    assert _split_stacked(one_line) == [one_line]


def test_tall_integral_glyph_takes_the_baseline_of_the_text_beside_it():
    from paperdesk.parsing.layout import Span, _fix_tall_operators

    text = Span(110, 380, 160, 392, 389.8, "satisfying", 12.0, False, False, "Times", False)
    after = Span(172, 380, 190, 392, 389.8, "e", 12.0, False, False, "Times", False)
    other = Span(110, 366, 160, 378, 375.4, "Define", 12.0, False, False, "Times", False)
    integral = Span(163.4, 379.7, 169.1, 408.4, 380.2, "∫", 12.0, False, False, "CMEX10", False)
    _fix_tall_operators([text, after, other, integral])
    assert abs(integral.baseline - 389.8) < 0.01  # 不再算作上一行（375.4）的一部分


def test_link_label_lines_are_recognised():
    from paperdesk.parsing.pdf_parser import LINK_LABEL_RE

    for t in ("Publisher Full Text", "PubMed Abstract | Publisher Full Text | Free Full Text", "Data Source", "Reference Source"):
        assert LINK_LABEL_RE.fullmatch(t)
    assert not LINK_LABEL_RE.fullmatch("Publisher Full Text of the study design")  # 不会吃掉真正的句子


def test_vancouver_style_reference_starts():
    from paperdesk.parsing.layout import AUTHOR_START_RE
    from paperdesk.parsing.pdf_parser import _looks_like_ref_start

    assert AUTHOR_START_RE.match("Capobianco M, Pizzuto EA, Devescovi A: Gesture–speech combinations")
    assert _looks_like_ref_start("Cerniglia L: “Preterm/Fullterm”, Mendeley Data, v2. 2017.")
    assert not _looks_like_ref_start("The authors acknowledge and are grateful to all children and parents.")


def test_repeated_template_headings_are_not_sections():
    from paperdesk.parsing import pdf_parser as P

    q = "Is the study design appropriate and is the work technically sound?"
    blocks = [_blk(q, 100, bold=1.0, hint=True), _blk("Yes.", 120), _blk(q, 300, bold=1.0, hint=True, page=2), _blk("Yes.", 320, page=2),
              _blk("Statistical analysis plan overview", 400, bold=1.0, hint=True)]
    P._drop_repeated_headings(blocks)
    assert [b.heading_hint for b in blocks] == [False, False, False, False, True]


# ---- 本地版面模型（表格/图/显示公式的框）补规则漏掉的对象 ----
def _page(spans_text, w=612.0, h=792.0):
    """造一个最小的 _Page：只要有 native 尺寸、spans、regions、lines、rot。"""
    import pymupdf

    from paperdesk.parsing import pdf_parser as P

    pg = P._Page.__new__(P._Page)
    pg.no, pg.native_w, pg.native_h, pg.rot = 1, w, h, pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    pg.spans = [sp(t, x, y, 10.0, w=5.0 * len(t)) for t, x, y in spans_text]
    pg.regions, pg.lines = [], []
    return pg


def test_model_table_box_becomes_a_table_region_unless_it_is_prose():
    from paperdesk.parsing import pdf_parser as P

    cells = [("Method", 100, 200), ("Error", 220, 200), ("A", 100, 215), ("1.5", 220, 215), ("B", 100, 230), ("2.5", 220, 230)]
    pg = _page(cells)
    P._add_model_regions(pg, [{"kind": "table", "conf": 0.9, "box": [90 / 612, 185 / 792, 300 / 612, 240 / 792]}])
    assert len(pg.regions) == 1 and pg.regions[0].table
    # 低置信度 / 占了大半页 / 框里是成段的句子：都不采用
    pg2 = _page(cells)
    P._add_model_regions(pg2, [{"kind": "table", "conf": 0.2, "box": [0.1, 0.2, 0.5, 0.3]}, {"kind": "table", "conf": 0.9, "box": [0, 0, 1, 0.9]}])
    assert pg2.regions == []
    sentences = [("This is a long sentence with many ordinary words that ends properly in a period.", 100, 200 + 14 * i) for i in range(6)]
    pg3 = _page(sentences)
    P._add_model_regions(pg3, [{"kind": "figure", "conf": 0.9, "box": [90 / 612, 190 / 792, 540 / 612, 300 / 792]}])
    assert pg3.regions == []


def test_model_region_marks_an_existing_plain_region_as_a_table_instead_of_duplicating_it():
    from paperdesk.parsing import pdf_parser as P
    from paperdesk.parsing.figures import Region

    pg = _page([("x", 100, 200)])
    pg.regions = [Region(90, 180, 300, 260, "vector")]
    P._add_model_regions(pg, [{"kind": "table", "conf": 0.9, "box": [92 / 612, 182 / 792, 298 / 612, 258 / 792]}])
    assert len(pg.regions) == 1 and pg.regions[0].table


def test_model_formula_box_makes_its_lines_one_formula_block():
    from paperdesk.parsing import pdf_parser as P
    from paperdesk.parsing.layout import segment_paragraphs

    pg = _page([])
    pg.lines = [line(300, [(200, "P(h|r, v) = prod P(hj|rj, v),", {})]), line(314, [(200, "Z(theta)", {})]),
                line(100, [(50, "Plain prose line that is not an equation at all and stays prose here", {})])]
    P._tag_model_formulas(pg, [{"kind": "formula", "conf": 0.8, "box": [190 / 612, 285 / 792, 400 / 612, 322 / 792]}])
    assert [l.role for l in pg.lines] == ["formula", "formula", ""] and pg.lines[0].region == pg.lines[1].region >= 10_000
    blocks = segment_paragraphs(sorted(pg.lines, key=lambda l: l.y0), 1, 10.0)
    kinds = [b.kind for b in blocks]
    assert kinds.count("formula") == 1 and "paragraph" in kinds


def test_model_formula_box_around_prose_is_ignored():
    from paperdesk.parsing import pdf_parser as P

    sent = "This long line is ordinary prose with plenty of words so the box around it is a false positive"
    pg = _page([])
    pg.lines = [line(300, [(50, sent, {})]), line(314, [(50, sent + " again", {})])]
    P._tag_model_formulas(pg, [{"kind": "formula", "conf": 0.9, "box": [40 / 612, 285 / 792, 560 / 612, 325 / 792]}])
    assert [l.role for l in pg.lines] == ["", ""]


def test_model_boxes_end_to_end_on_a_pdf_with_a_table_the_rules_miss(tmp_path):
    """用 PyMuPDF 造一页带“没有线的表格”的 PDF：没有模型框时表里的单元格是正文段落，有框时它们成了图内文字。"""
    import pymupdf

    from paperdesk.parsing.pdf_parser import parse_pdf

    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    y = 80
    for _ in range(5):
        pg.insert_textbox(pymupdf.Rect(72, y, 540, y + 58), "Ordinary running text sentence about the experiment and its results in the paper. " * 3, fontsize=10, fontname="helv")
        y += 62
    for r, row in enumerate([("Method", "Top-1", "Top-5"), ("A", "70.1", "89.2"), ("B", "71.5", "90.3"), ("C", "73.0", "91.0")]):
        for c, cell in enumerate(row):
            pg.insert_text((90 + 120 * c, 440 + 16 * r), cell, fontsize=10, fontname="helv")
    pg.insert_text((90, 530), "Table 1: Results of three methods.", fontsize=9, fontname="helv")
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    plain = parse_pdf(path)
    boxes = {1: [{"kind": "table", "conf": 0.9, "box": [80 / 612, 425 / 792, 470 / 612, 500 / 792]}]}
    with_model = parse_pdf(path, model_boxes=boxes)
    assert any(p.kind == "figure_text" and "Top-1" in p.text for p in with_model.passages)
    assert not any(p.kind == "paragraph" and "Top-1" in p.text for p in with_model.passages)
    assert any(f.kind == "table" for f in with_model.figures) or any(p.kind == "caption" for p in with_model.passages)
    assert any("Ordinary running text" in p.text for p in with_model.passages if p.kind == "paragraph")  # 正文不受影响
    assert len(plain.passages) >= 1
