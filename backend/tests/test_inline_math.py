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
