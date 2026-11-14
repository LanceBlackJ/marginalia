from paperdesk.parsing.mineru_adapter import _safe_equation_latex
from paperdesk.vision.formula import clean_latex


def test_mineru_does_not_mark_split_math_as_verified():
    assert _safe_equation_latex(r"\tan h(x) + 1 0") is None
    assert _safe_equation_latex(r"\frac{a}{b} + c") == r"\frac{a}{b} + c"


def test_formula_cleaner_keeps_structural_warnings():
    latex, confidence, warnings = clean_latex("```latex\n\\frac{a}{b}\n```\nCONFIDENCE: high")
    assert latex == r"\frac{a}{b}"
    assert confidence == "high"
    assert warnings == []


def test_formula_cleaner_repairs_only_obvious_command_spacing():
    latex, _, warnings = clean_latex("```latex\n\\tan h(x)\n```\nCONFIDENCE: medium")
    assert latex == r"\tanh(x)"
    assert warnings == []
