"""行内公式还原：从 PDF 文字层的字体、字号、基线位置重建正文里夹着的行内数学（v_i、σ(G)、δ_i(δ_i − 1) …）。

PDF 里的行内公式是一串零散的文字片段（span）：斜体的变量、小号的上下标、数学字体里的符号。
这些在提取出的正文里会变形：下标贴在变量后面（“vi”），自定义编码的符号变成乱码（“2”表示 ∈，“¼”表示 =）。
这里按几何关系把相邻的“像数学”的片段合成一个簇，重建 LaTeX；只有簇里每个字符都认得才采用，认不全的整簇放弃（正文保持提取出的原样）。
结果是 [{"src": 正文里这段公式对应的文字, "tex": LaTeX}]，前端在正文里找到 src 的位置，换成渲染好的公式。

只处理正文里的行内公式；独立成行的公式（公式块）由视觉识别负责，不在这里。分式、求和、积分这类多层结构不处理。
纯函数，便于测试：输入是已经按行分好的片段。
"""
from __future__ import annotations

import dataclasses
import re

GREEK = {
    "α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta", "ε": "\\epsilon", "ϵ": "\\epsilon", "ζ": "\\zeta", "η": "\\eta",
    "θ": "\\theta", "ι": "\\iota", "κ": "\\kappa", "λ": "\\lambda", "μ": "\\mu", "ν": "\\nu", "ξ": "\\xi", "π": "\\pi", "ρ": "\\rho",
    "σ": "\\sigma", "τ": "\\tau", "υ": "\\upsilon", "φ": "\\phi", "ϕ": "\\phi", "χ": "\\chi", "ψ": "\\psi", "ω": "\\omega",
    "Γ": "\\Gamma", "Δ": "\\Delta", "Θ": "\\Theta", "Λ": "\\Lambda", "Ξ": "\\Xi", "Π": "\\Pi", "Σ": "\\Sigma", "Φ": "\\Phi",
    "Ψ": "\\Psi", "Ω": "\\Omega",
}
# 正文字体里的常见数学符号（Unicode 写法）
OPERATORS = {
    "=": "=", "+": "+", "−": "-", "-": "-", "–": "-", "×": "\\times ", "·": "\\cdot ", "∈": "\\in ", "∉": "\\notin ", "≤": "\\le ",
    "≥": "\\ge ", "≠": "\\ne ", "≈": "\\approx ", "→": "\\to ", "±": "\\pm ", "∞": "\\infty ", "∪": "\\cup ", "∩": "\\cap ",
    "⊂": "\\subset ", "⊆": "\\subseteq ", "∅": "\\emptyset ", "|": "|", "/": "/", "(": "(", ")": ")", "[": "[", "]": "]",
    ",": ",", ".": ".", ":": ":", ";": ";", "<": "<", ">": ">", "…": "\\ldots ", "∗": "*", "*": "*", "′": "'", "'": "'",
}
OPERATORS.update({
    "∥": "\\Vert ", "‖": "\\Vert ", "∂": "\\partial ", "∇": "\\nabla ", "≪": "\\ll ", "≫": "\\gg ", "∝": "\\propto ", "≡": "\\equiv ",
    "∼": "\\sim ", "⊥": "\\perp ", "⊤": "\\top ", "∀": "\\forall ", "∃": "\\exists ", "∘": "\\circ ", "⋅": "\\cdot ", "∧": "\\wedge ",
    "∨": "\\vee ", "⊙": "\\odot ", "⊗": "\\otimes ", "⊕": "\\oplus ", "←": "\\leftarrow ", "⇒": "\\Rightarrow ", "↦": "\\mapsto ",
    "⟨": "\\langle ", "⟩": "\\rangle ", "⌈": "\\lceil ", "⌉": "\\rceil ", "⌊": "\\lfloor ", "⌋": "\\rfloor ",
    "⊇": "\\supseteq ", "⊃": "\\supset ", "∖": "\\setminus ", "≜": "\\triangleq ", "⋯": "\\cdots ", "∣": "|",
})
ELLIPSIS_3 = ""  # 自定义编码里的 ⋯
# Springer Nature 类排版用的自定义编码数学字体（TeX_CM_Maths_*）：字符 → LaTeX
CM_SYMBOLS = {"¼": "=", "ð": "(", "Þ": ")", "2": "\\in ", "þ": "+", "\x1a": "-", "f": "\\{", "g": "\\}", "n": "\\setminus ",
              ";": ",", " ": " ", "j": "|", "!": "\\to ", "·": "\\cdot ", "": "\\cdot "}
CM_ITALIC_PUNCT = {";": ",", ":": ":", " ": " ", ".": ".", ",": ","}
MACOPEN = {"R": "\\mathbb{R}", "N": "\\mathbb{N}", "Z": "\\mathbb{Z}", "Q": "\\mathbb{Q}", "C": "\\mathbb{C}"}

MATHFONT_RE = re.compile(r"cmmi|cmsy|cmex|tex_cm|maths|stix|symbol|mathematical|cambria\s*math|macopen|msbm|msam", re.I)
_BODY_FONT = ("minionpro", "times", "helvetica", "whitney", "arial", "calibri", "garamond")
_ITALIC_FONT = re.compile(r"italic|-it\d*$|oblique", re.I)
_SCRIPT_TEXT = re.compile(r"^[A-Za-z0-9,;:=+\-−()  ]+$")  # 上下标里可以有的字符：字母数字、逗号、运算符、括号
_PUNCT_ONLY = re.compile(r"^[\s,.;:=+\-−×·/|()\[\]<>≤≥≠≈…]+$|^\d{1,3}$")
_FUNCS = {"log", "ln", "min", "max", "exp", "sin", "cos", "sup", "inf"}
RADICAL = "\x00radical"  # 根号的占位：build_tex 把它和后面的被开方式一起写成 \sqrt{…}
# 被开方式到这里结束：括号配平之后遇到的运算符、比较号、逗号
_RADICAND_END = re.compile(r"\s*(?:[+\-=<>,;]|\\(?:cdot|times|pm|leq?|geq?|neq|approx|to|in)\b)")
_NEEDS_BASE = re.compile(r"[=+\-,(|]$|\\(?:in|to|cdot|times|setminus|le|ge|ldots)$|\{$")
_TRAILING_OP = re.compile(r"(?:[+\-]|\\times|\\cdot)$")
MAX_CLUSTER_SPANS = 28


def _is_italic(s) -> bool:
    return bool(s.italic) or bool(_ITALIC_FONT.search(s.font))


# 变量头上的重音：PDF 里它是单独的一个小片段（点、双点、帽、波浪、横线），提取时和变量的先后顺序不定（“s˙(t)”“¨s(t)”）
ACCENTS = {"˙": "\\dot", "¨": "\\ddot", "ˆ": "\\hat", "˜": "\\tilde",
           "¯": "\\bar", "ˉ": "\\bar", "‾": "\\bar"}


def attach_accents(spans: list, body: float) -> list:
    """把单独成片段的重音并进它下面的变量：返回新的片段列表（x 顺序不变），变量带上 accent，members 记着原来的两个片段。
    找不到下面的变量（要求单个字母或希腊字母的数学片段、水平上对得上）的重音原样留着，所在的簇之后会因为“不认识的字符”被放弃。"""
    out = list(spans)
    for a in spans:
        t = a.text.strip()
        if t not in ACCENTS or len(a.text.strip()) != 1 or a.size > 1.05 * body:
            continue
        cx = (a.x0 + a.x1) / 2
        best, best_d = None, None
        for b in out:
            if b is a or b.accent or b.text.strip() in ACCENTS:
                continue
            bt = b.text.strip()
            if len(bt) != 1 or not (bt.isascii() and bt.isalpha() or bt in GREEK) or not _mathish(b, body):
                continue
            d = abs(cx - (b.x0 + b.x1) / 2)
            if d <= max(b.x1 - b.x0, 0.5 * body) * 0.9 and (best_d is None or d < best_d):
                best, best_d = b, d
        if best is None:
            continue
        merged = dataclasses.replace(best, accent=ACCENTS[t], members=[a, best],
                                     x0=min(a.x0, best.x0), x1=max(a.x1, best.x1), y0=min(a.y0, best.y0))
        out[out.index(best)] = merged
        out.remove(a)
    return out


def expand_members(cluster: list) -> list:
    """还原成原始片段（并进变量的重音片段重新出现），用来取这一簇在正文里的原文。"""
    return [m for s in cluster for m in (s.members or [s])]


def _frac_tex(s) -> str | None:
    """行内堆叠分式：分子、分母各自还原，任何一边认不全就整个放弃（交给视觉模型）。"""
    parts = []
    for group in s.frac:
        size = max(x.size for x in group)
        ref = next(x for x in group if x.size >= 0.95 * size)
        t = build_tex(group, size, ref.baseline, numeric_ok=True)
        if t is None:
            return None
        parts.append(t)
    return "\\frac{" + parts[0] + "}{" + parts[1] + "}"


def decode(s) -> str | None:
    """一个片段的 LaTeX；有不认识的字符返回 None。"""
    if getattr(s, "frac", None):
        return _frac_tex(s)
    tex = _decode_plain(s)
    if tex is None or not s.accent:
        return tex
    return f"{s.accent}{{{tex.strip()}}}"


def _decode_plain(s) -> str | None:
    t = s.text
    if not t.strip():
        return " " if t else ""
    if t.strip() == "√":
        return RADICAL  # 任何字体里的根号（CMEX 的大号根号已在提取时改成 √）
    low = s.font.lower()
    out: list[str] = []
    if "tex_cm_maths_sym" in low:
        raw = t.replace(":::", "").replace(ELLIPSIS_3, "")
        for ch in raw:
            if ch == "":
                out.append("\\ldots ")
            elif ch == "":
                out.append("\\cdots ")
            elif ch in CM_SYMBOLS:
                out.append(CM_SYMBOLS[ch])
            elif ch.isascii() and ch.isalpha() and ch.isupper():
                out.append(f"\\mathcal{{{ch}}}")  # 这个字体里的大写字母是花体
            elif ch.isascii() and ch.isdigit():
                out.append(ch)
            else:
                return None
        return "".join(out)
    if "tex_cm_maths_ita" in low:
        for ch in t.replace(":::", ""):
            if ch == "":
                out.append("\\ldots ")
            elif ch.isascii() and ch.isalpha():
                out.append(ch)
            elif ch in CM_ITALIC_PUNCT:
                out.append(CM_ITALIC_PUNCT[ch])
            else:
                return None
        return "".join(out)
    if "tex_cm_maths_ext" in low or "cmex" in low:
        word = t.strip()
        if not word:
            return ""
        if word == "√":
            return RADICAL  # 被开方式有多长要看后面的片段，由 build_tex 决定在哪里收尾
        return {"P": "\\sum ", "X": "\\sum ", "Q": "\\prod "}.get(word)  # 行内的求和、连乘；积分等别的大号符号不处理
    if "macopen" in low:
        return MACOPEN.get(t.strip())
    for ch in t:
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif ch in GREEK:
            out.append(GREEK[ch] + " ")
        elif ch in OPERATORS:
            out.append(OPERATORS[ch])
        elif ch in "    ":
            out.append(" ")
        else:
            return None
    return "".join(out)


# 斜体的两个字母“变量”其实常是英文虚词（有的论文排版把正文词也排成斜体字体）：渲染成公式会变成“isf central”之类的乱码
_SHORT_WORDS = frozenset("an as at be by do he if in is it me my no of on or so to up us we am go oh ok".split())


def _mathish(s, body: float) -> bool:
    """这个片段本身像数学（不含“夹在两个数学片段之间的标点”）。"""
    t = s.text.strip()
    if not t or all(ch in "ﬀﬁﬂﬃﬄ" for ch in t):  # 连字（ﬁ ﬂ）常单独排在一个子集字体里，不是数学
        return False
    low = s.font.lower()
    if "tex_cm" in low:
        return True
    if MATHFONT_RE.search(low) and not low.startswith(_BODY_FONT):
        return True
    if all(ch in GREEK for ch in t):
        return True
    if _is_italic(s) and len(t) <= 2 and t.replace("'", "").isalpha() and s.size >= 0.55 * body and t.lower() not in _SHORT_WORDS:
        return True  # 斜体的单个变量（正文里的强调是整个词，不会这么短）；“to / of / as / We”这类两个字母的英文词是正文，不是变量
    return False


def _level(s, base_size: float, base_line: float) -> str:
    """相对基线：'n' 正常、'sub' 下标、'sup' 上标。"""
    if s.text.strip() == "√":
        return "n"  # 根号的字号比正文小、基线也低，但它不是下标
    if s.size < 0.86 * base_size:
        d = s.baseline - base_line
        if d > 0.12 * base_size:
            return "sub"
        if d < -0.18 * base_size:
            return "sup"
    return "n"


def build_tex(spans: list, base_size: float, base_line: float, *, numeric_ok: bool = False) -> str | None:
    """把一簇按 x 排好的片段拼成 LaTeX；任何一个字符不认得、结构太复杂就返回 None。

    上标和下标各攒在自己的缓冲里（求和号的上下限在 x 上是交错的，但各自内部的顺序是对的），遇到下一个正常大小的片段再一起写出。"""
    parts: list[str] = []
    subs: list[str] = []
    sups: list[str] = []
    bad = False
    rad_open = False  # 正在写根号里的被开方式
    rad_depth = 0  # 被开方式里还没配平的括号数
    rad_has = False  # 被开方式里已经有内容

    def flush() -> None:
        nonlocal subs, sups, bad
        lo, hi = "".join(subs).strip(), "".join(sups).strip()
        subs, sups = [], []
        if not lo and not hi:
            return
        prev = parts[-1].rstrip() if parts else ""
        # 上下标前面必须是一个可以带上下标的东西；前面是运算符或什么都没有，说明是分式这类多层结构
        if not prev or _NEEDS_BASE.search(prev):
            bad = True
        if lo:
            parts.append("_{" + lo + "}")
        if hi:
            parts.append("^{" + hi + "}")

    for s in spans:
        lv = _level(s, base_size, base_line)
        tex = decode(s)
        if tex is None:
            return None
        word = s.text.strip()
        plain_word = len(word) > 1 and word.isascii() and word.isalpha() and "tex_cm" not in s.font.lower() and not _is_italic(s)
        if lv != "n":
            (subs if lv == "sub" else sups).append(f"\\mathrm{{{word}}}" if plain_word else tex)  # 下标里的 cost、pair 这类正体单词
            continue
        flush()
        if tex == RADICAL:
            if rad_open:
                return None  # 根号套根号：不处理，交给视觉模型
            parts.append("\\sqrt{")
            rad_open, rad_depth, rad_has = True, 0, False
            continue
        if rad_open and rad_has and rad_depth == 0 and _RADICAND_END.match(tex):
            parts.append("}")
            rad_open = False
        if plain_word:
            if word.lower() in _FUNCS:
                tex = f"\\{word.lower()} "
            else:
                return None  # 普通英文单词混进来了：说明簇切得不对，放弃
        parts.append(tex)
        if rad_open:
            rad_depth += tex.count("(") - tex.count(")")
            rad_has = True
    flush()
    if rad_open:
        if not rad_has:
            return None
        parts.append("}")
    out = re.sub(r"\s+", " ", "".join(parts)).strip()
    if bad or not out or not (numeric_ok or re.search(r"[A-Za-z\\]", out)) or _TRAILING_OP.search(out):
        return None  # 以运算符结尾：表达式在这一行被截断了
    return out


def line_clusters(spans: list, body: float, base_line: float) -> list[list]:
    """一行里连续的“数学片段”簇（片段已按 x 排序）。簇两端必须是数学片段；中间只允许标点、数字、运算符和上下标。"""
    n = len(spans)
    is_m = [_mathish(s, body) for s in spans]
    # 紧跟在数学片段后面、字号更小且偏离基线的短片段是它的上下标
    for i in range(1, n):
        s = spans[i]
        t = s.text.strip()
        if is_m[i] or not t or not is_m[i - 1]:
            continue
        if s.size < 0.86 * body and _level(s, body, base_line) != "n" and len(t) <= 5 and _SCRIPT_TEXT.match(t):
            is_m[i] = True
    clusters: list[list] = []
    cur: list = []
    i = 0
    while i < n:
        if is_m[i]:
            cur.append(spans[i])
            i += 1
            continue
        t = spans[i].text
        blank = not t.strip()
        if cur and (blank or spans[i].size >= 0.86 * body) and _PUNCT_ONLY.match(t) and len(t.strip()) <= 6:
            j = i + 1  # 标点夹在两个数学片段之间才算；否则簇到此为止
            while j < n and not spans[j].text.strip():
                j += 1
            if j < n and is_m[j]:
                cur.append(spans[i])
                i += 1
                continue
        if cur:
            clusters.append(cur)
            cur = []
        i += 1
    if cur:
        clusters.append(cur)
    return [c for c in clusters if len(c) <= MAX_CLUSTER_SPANS]
