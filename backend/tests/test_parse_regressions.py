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
