from paperdesk.parsing import inline_math as im
from paperdesk.parsing.layout import Span

BODY, BASE = 8.0, 100.0


def sp(text, x, font="Whitney-Book", size=BODY, base=BASE, italic=False):
    return Span(x, base - size, x + 4, base + 2, base, text, size, False, italic, font, False)


def tex_of(spans):
    clusters = im.line_clusters(spans, BODY, BASE)
    assert len(clusters) == 1, [[s.text for s in c] for c in clusters]
    return im.build_tex(clusters[0], BODY, BASE)


def test_sum_with_limits_whose_spans_interleave_in_x():
    """求和号的上限和下限在 x 上同起点、交错，各自内部的顺序是对的。"""
    spans = [
        sp("R", 10, "Whitney-BookItalic2", italic=True),
        sp("¼", 20, "TeX_CM_Maths_Symbols"),
        sp("P", 30, "TeX_CM_Maths_Extension", base=BASE - 6),
        sp("i", 40, "Whitney-BookItalic2", size=5.6, base=BASE - 3.8, italic=True),
        sp("k", 40, "Whitney-BookItalic2", size=5.6, base=BASE + 2.3, italic=True),
        sp("þ", 42, "TeX_CM_Maths_Symbols", size=5.6, base=BASE - 3.8),
        sp("¼", 43, "TeX_CM_Maths_Symbols", size=5.6, base=BASE + 2.3),
        sp("n", 45, "Whitney-BookItalic2", size=5.6, base=BASE - 3.8, italic=True),
        sp("i", 46, "Whitney-BookItalic2", size=5.6, base=BASE + 2.3, italic=True),
        sp("R", 52, "Whitney-BookItalic2", italic=True),
        sp("k", 58, "Whitney-BookItalic2", size=5.6, base=BASE + 1.3, italic=True),
    ]
    assert tex_of(spans) == "R=\\sum _{k=i}^{i+n}R_{k}"


def test_script_with_punctuation_and_thin_spaces():
    spans = [
        sp("R", 10, "Whitney-BookItalic", italic=True),
        sp("(", 20, size=4.7, base=BASE + 1.7),
        sp("i", 22, "Whitney-BookItalic", size=4.7, base=BASE + 1.7, italic=True),
        sp(", ", 24, size=4.7, base=BASE + 1.7),
        sp("i", 27, "Whitney-BookItalic", size=4.7, base=BASE + 1.7, italic=True),
        sp(" ", 29, size=4.7, base=BASE + 1.7),
        sp("+", 30, "STIXGeneral-Regular", size=4.7, base=BASE + 1.7),
        sp(" ", 32, size=4.7, base=BASE + 1.7),
        sp("n", 33, "Whitney-BookItalic", size=4.7, base=BASE + 1.7, italic=True),
        sp(")", 35, size=4.7, base=BASE + 1.7),
    ]
    assert tex_of(spans).replace(" ", "") == "R_{(i,i+n)}"  # 公式里的空格对渲染没有影响


def test_unknown_glyph_drops_the_whole_cluster():
    spans = [sp("x", 10, "Whitney-BookItalic", italic=True), sp("∑", 20, "TeX_CM_Maths_Symbols")]
    assert im.build_tex(spans, BODY, BASE) is None


def test_scripts_without_a_base_or_after_an_operator_are_rejected():
    spans = [sp("¼", 10, "TeX_CM_Maths_Symbols"), sp("i", 20, "Whitney-BookItalic", size=5.6, base=BASE + 2.3, italic=True)]
    assert im.build_tex(spans, BODY, BASE) is None


def test_plain_english_word_inside_a_cluster_is_rejected():
    spans = [sp("x", 10, "Whitney-BookItalic", italic=True), sp("hello", 20), sp("y", 40, "Whitney-BookItalic", italic=True)]
    assert im.build_tex(spans, BODY, BASE) is None


def test_dot_and_ddot_accents_are_attached_to_their_variable_whatever_the_extraction_order():
    it = "Whitney-BookItalic"
    # “s˙(t) = b(s)”：点在变量之后
    after = [sp("s", 10, it, italic=True), sp("˙", 11, "CMR10"), sp("(", 16), sp("t", 19, it, italic=True), sp(") = ", 24), sp("b", 33, it, italic=True), sp("(", 38), sp("s", 41, it, italic=True), sp(")", 46)]
    spans = im.attach_accents(after, BODY)
    assert [s.text for s in spans if not s.accent][:1] == ["("] and spans[0].accent == "\\dot" and len(spans) == 8
    assert tex_of(spans) == "\\dot{s}(t) = b(s"  # 末尾的右括号由 math_runs 从后面的正文里并回来
    # 取原文：重音片段要回来，和正文里提取出的文字一致
    cl = im.line_clusters(spans, BODY, BASE)[0]
    assert sorted(s.text for s in im.expand_members(cl))[:0] == [] and {"s", "˙"} <= {s.text for s in im.expand_members(cl)}  # 排序由 _line_from 按 x 完成
    # “¨s(t)”：双点在变量之前
    before = [sp("¨", 8, "CMR10"), sp("s", 10, it, italic=True), sp("(", 16), sp("t", 19, it, italic=True), sp(")", 24)]
    assert tex_of(im.attach_accents(before, BODY)) == "\\ddot{s}(t"
    # 离变量太远的重音不乱并
    far = [sp("s", 10, it, italic=True), sp("˙", 40, "CMR10")]
    assert [s.text for s in im.attach_accents(far, BODY)] == ["s", "˙"]


def test_two_closing_parentheses_that_ended_up_in_the_following_body_text_are_taken_back():
    from paperdesk.parsing import layout
    from paperdesk.parsing.pdf_parser import math_runs

    it = "Whitney-BookItalic"
    spans = [sp("trajectories ", 0), sp("y", 60, it, italic=True), sp("r", 64, it, size=5.6, base=BASE + 2.3, italic=True),
             sp("(", 68), sp("s", 71, it, italic=True), sp("(", 76), sp("t", 79, it, italic=True), sp("))", 84), sp(".", 90)]
    ln = layout._line_from(spans)
    b = layout.Block(page=1, x0=0, y0=0, x1=100, y1=10, text=ln.text, size=BODY, bold=0, nlines=1, lines=[ln])
    runs = math_runs(b)
    assert runs and runs[0]["tex"] == "y_{r}(s(t))" and runs[0]["src"].startswith("yr(") and runs[0]["src"].endswith("))"), runs


def test_norm_bars_in_a_symbol_font_are_recognised():
    it = "Whitney-BookItalic"
    spans = [sp("\u2225", 10, "CMSY10"), sp("p", 14, "CMMIB10", italic=True), sp("(", 20), sp("t", 23, "CMMI10", italic=True), sp(")", 28), sp("\u2225", 32, "CMSY10")]
    cl = im.line_clusters(spans, BODY, BASE)
    assert len(cl) == 1
    tex = im.build_tex(cl[0], BODY, BASE)
    assert tex is not None and tex.replace(" ", "") == chr(92) + "Vertp(t)" + chr(92) + "Vert", tex


def test_prose_line_that_is_mostly_inline_math_stays_in_its_paragraph():
    """“points are defined as B ≜ {b(s_i) | …, N −” 数学符号占大半，但它是一句话里的行内公式：不能被切成独立公式块，
    否则换行后剩下的 “1}, for simplicity…” 会成为下一段的开头。"""
    from paperdesk.parsing import layout

    def line(base, parts):
        spans, x = [], 40.0
        for text, font, is_math in parts:
            w = 5.0 * len(text)
            spans.append(Span(x, base - 8, x + w, base + 2, base, text, 8.0, False, is_math, font, is_math))
            x += w + 1
        return layout._line_from(spans)

    first = line(100, [("the discretisation ", "Whitney-Book", False), ("points are defined as ", "Whitney-Book", False),
                       ("B", "CMMI10", True), ("≜", "CMSY10", True), ("{b(s", "CMMI10", True), ("i", "CMMI7", True),
                       (")|s", "CMMI10", True), ("i", "CMMI7", True), ("=ih,i=0,1,N−", "CMR10", True)])
    second = line(111, [("1},", "CMR10", True), (" for simplicity, we denote ", "Whitney-Book", False), ("b", "CMMI10", True), ("i", "CMMI7", True)])
    blocks = layout.segment_paragraphs([first, second], 1, 8.0)
    assert [b.kind for b in blocks] == ["paragraph"] and len(blocks) == 1, [(b.kind, b.text) for b in blocks]


def test_radical_from_cmex_wraps_the_radicand():
    """CMEX10 里的根号：ṡ(t)=√b(s)。被开方式到运算符或簇尾结束，括号配平之后才收尾。"""
    cm = "CMMI10"
    spans = [
        sp("s", 10, cm, italic=True),
        sp("(", 15, "CMR10"),
        sp("t", 19, cm, italic=True),
        sp(")=", 23, "CMR10"),
        sp("√", 30, "CMEX10"),
        sp("b", 40, cm, italic=True),
        sp("(", 45, "CMR10"),
        sp("s", 49, cm, italic=True),
        sp(")", 54, "CMSY10"),
    ]
    assert tex_of(spans) == "s(t)=\\sqrt{b(s)}"


def test_radical_stops_at_the_next_operator():
    cm = "CMMI10"
    spans = [sp("√", 10, "CMEX10"), sp("x", 20, cm, italic=True), sp("+", 25, "CMSY10"), sp("y", 31, cm, italic=True)]
    assert tex_of(spans) == "\\sqrt{x}+y"


def test_small_radical_over_a_stacked_fraction_is_not_a_subscript():
    """t_i = √(h/b_i)：根号字号小、基线低（CMSY），后面是并好的分式；根号不能被当成下标。"""
    frac = sp("(h)/(b)", 30, "CMMI7")
    frac.frac = ([sp("h", 30, "CMMI7", size=5.6, base=BASE - 3.0)], [sp("b", 30, "CMMI7", size=5.6, base=BASE + 4.0)])
    spans = [
        sp("t", 10, "CMMI10", italic=True),
        sp("=", 16, "CMR10"),
        sp("√", 24, "CMSY10", size=5.6, base=BASE + 3.0),
        frac,
    ]
    assert tex_of(spans) == "t=\\sqrt{\\frac{h}{b}}"


def test_fraction_with_an_unreadable_half_gives_up_instead_of_guessing():
    frac = sp("(h)/(?)", 30, "CMMI7")
    frac.frac = ([sp("h", 30, "CMMI7", size=5.6, base=BASE - 3.0)], [sp("中", 30, "CMMI7", size=5.6, base=BASE + 4.0)])
    spans = [sp("t", 10, "CMMI10", italic=True), sp("=", 16, "CMR10"), frac]
    assert tex_of(spans) is None


def test_subscript_only_one_point_below_the_baseline_is_still_a_subscript():
    """ACM 版式（CMMI）：下标只比基线低 1 磅、字号 6/9。以前判成正文，ŷ_{ui} 变成 “ŷui”。"""
    spans = [sp("y", 10, "CMMI9", size=BODY, base=BASE, italic=True), sp("ui", 16, "CMMI6", size=5.2, base=BASE + 1.0, italic=True)]
    assert tex_of(spans) == "y_{ui}"


def test_sum_with_only_an_upper_limit_is_not_given_a_wrong_formula():
    """下限和下标跑到别的行去了：求和号只剩上限，不能给出一个缺了东西的式子，交给视觉模型。"""
    spans = [
        sp("q", 10, "CMMI10", italic=True), sp("=", 16, "CMR10"),
        sp("P", 24, "TeX_CM_Maths_Extension", base=BASE - 6),
        sp("d", 34, "CMMI7", size=5.6, base=BASE - 7.5, italic=True), sp("k", 38, "CMMI5", size=4.0, base=BASE - 6, italic=True),
        sp("q", 44, "CMMI10", italic=True),
    ]
    assert tex_of(spans) is None
