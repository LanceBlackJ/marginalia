"""版面分析：把 PyMuPDF 最底层的文字片段（span）重组为视觉行，再按栏、段落切分。

为什么不直接用 PyMuPDF 的 block：在数学密集的论文里，行内公式的字体变化会把同一视觉行拆进多个 block，
段落被切成互相重叠的碎片，阅读顺序也会错乱。这里以 span 为原子，按基线重组成行，再做栏检测与分段。

所有坐标在页面的“原生”坐标系（未旋转）里计算；输出前由调用方按 page.rotation_matrix 转换到显示坐标。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import pymupdf

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "­": ""}
_LIG_RE = re.compile("|".join(map(re.escape, LIGATURES)))
_LIG_CHARS = frozenset("ﬀﬁﬂﬃﬄ")

# 数学字体：自定义编码的“带数字后缀”字体变体（MinionPro-It2 / Regular3…）、CM 数学字体、STIX、Symbol 等
MATH_FONT_RE = re.compile(
    r"((?<!glosa)math|symbol|stix|cmmi|cmsy|cmex|cmmib|cmbsy|msam|msbm|mnsymbol|extension|mtextra|euclid|"
    r"lmmath|latinmodern-math|cambria\s*math|advmacmth|advp4)|-(?:regular|italic|it)\d+(?:\+\d+)?$",
    re.I,
)
MATH_CHARS = set("∑∏∫√∞≈≤≥≠∈∉⊂⊆⊃⊇∪∩∂∇∀∃±×÷∘→←↔⇒⇐⇔⟨⟩⟦⟧αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ¼ðÞ")
# 少数自定义编码数学字体里的“安全”还原（这些字符在正常文字里几乎不会出现）
GLYPH_FIX = {"¼": "=", "ð": "(", "Þ": ")", ":::": "…", "": "…"}
EQ_NUMBER_RE = re.compile(r"[ð(]\s*(\d{1,3})\s*[Þ)]\s*$")
_CAPTION_HEAD = re.compile(
    r"^\s*((?:extended data |supplementary )?(?:fig(?:ure)?\.?|table|algorithm)|图|表)\s*(\d{1,3}(?:\.\d{1,2})*[a-z]?|(?-i:[A-Z])(?![A-Za-z])|[一二三四五六七八九十]+)(?P<rest>.*)$",
    re.I | re.S,
)


_ROMAN_TABLE = re.compile(r"^\s*(?:TABLE|FIG\.?|FIGURE)\s+[IVX]{1,5}\s*(?:\.|:)?\s*$")  # IEEE：“TABLE I” 单独一行，标题在下一行
_PANEL_BEFORE_CAPTION = re.compile(
    r"^\s*[A-Fa-f]\s+(?=(?:Fig(?:ure)?\.?|Table|TABLE)\s*[A-Z]?\d+)", re.I
)


def clean_caption_text(text: str) -> str:
    """去掉复合图面板标签误粘到图注前面的情况。

    复合图的最后一个面板常在同一文字块里先出现 ``F``，随后才是
    ``Fig. 2 | ...``。这不是图注正文，不能让它进入阅读器、翻译和图表问答。
    只清理“单个 A–F + 图表编号”这一种强特征，避免改动正常图注内容。
    """
    return _PANEL_BEFORE_CAPTION.sub("", text, count=1).strip()


def is_caption(text: str) -> bool:
    """图表标题：`Figure 1:` / `Table 2.` / `Fig. 1 | …` / `Fig. 3 Overview…`；排除正文里的 “Table 1 shows …”。"""
    if _ROMAN_TABLE.match(text.strip()) or re.match(r"^\s*(?:TABLE|FIG\.?)\s+[IVX]{1,5}\s*[.:]?\s+[A-Z][A-Z0-9 ,\-:()/&]{6,}$", text.strip()):
        return True  # 全大写的罗马编号图表标题（IEEE）
    m = _CAPTION_HEAD.match(text.strip())
    if not m:
        return False
    rest = m.group("rest")
    if re.match(r"\s*[–—-]\s*\d", rest):
        return False  # “Fig. 8–10” 这样的范围引用
    if re.match(r"\s*[.:：、|–—-]", rest):
        return True
    nxt = rest.strip()[:1]
    if not nxt or not (nxt.isupper() or re.match(r"[一-鿿]", nxt)):
        return False
    # 出版社排版里编号和图注之间是一个 en/em 空格（“Fig. 1<U+2002>Overview…”）：这不会出现在正文引用里，长图注也算
    if re.match(r"[\u2002\u2003\u2009]", rest) and len(text) <= 1500:
        return True
    return len(text) <= 240


class _CaptionMatcher:
    """兼容 CAPTION_START_RE.match(text) 的调用形式。"""

    @staticmethod
    def match(text: str):
        return True if is_caption(text) else None


CAPTION_START_RE = _CaptionMatcher()
NUMBERED_HEAD_RE = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-Z]\.|[A-Z]\.\d{1,2}(?:\.\d{1,2}){0,2})\s+[A-Z一-鿿]")
OCR_SECTION_RE = re.compile(r"(?i)^\s*(?:\d{1,2}\.?\s+)?(?:abstract|introduction|related\s+works?|background|preliminar(?:y|ies)|methods?|methodology|materials\s+and\s+methods|"
                            r"experiments?|results?(?:\s+and\s+discussion)?|discussion|conclusions?|references|acknowledg(?:e)?ments?|appendix|摘要|引言|参考文献|结论)\s*$")
CN_HEAD_RE = re.compile(r"^\s*(?:[（(][一二三四五六七八九十百]+[）)]|[一二三四五六七八九十百]+[、．]|第[一二三四五六七八九十百零\d]+[章节篇部分])")
REF_START_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s+\S")
# 作者-年份格式的条目开头：“Frome, A., Corrado, G.” / “K. He, X. Zhang”
AUTHOR_START_RE = re.compile(r"^\s*(?:[A-Z][\w'’\-]+,\s+(?:[A-Z]\.[\s\-]*){1,3}|(?:[A-Z]\.[\s\-]*){1,3}[A-Z][\w'’\-]+,|[A-Z][\w'’\-]+\s[A-Z]{1,3}[,:]\s)")
_YEAR_IN_ENTRY = re.compile(r"(?<!\d)(?:19|20)\d{2}[a-z]?(?!\d)")
TERMINAL = tuple(".?!:;。？！：；")


@dataclass
class Span:
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    text: str
    size: float
    bold: bool
    italic: bool
    font: str
    math: bool
    cls: str = "F"  # L / R / F
    # 行内公式还原：这个片段（一个变量）头上的重音（\dot 等），以及并进来的原始片段（重音片段 + 变量本身，还原正文里的原文用）
    accent: str = ""
    members: list = field(default_factory=list)
    # 行内堆叠分式（分子、分数线、分母）并成的一个片段：(分子片段, 分母片段)；text 写成 “(分子)/(分母)”
    frac: tuple | None = None


@dataclass
class Line:
    spans: list[Span]
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    cls: str
    text: str
    bold: float
    italic: float
    math: float  # 数学字符占比
    in_figure: bool = False
    region: int = -1  # 视觉划分里它属于第几个区域（-1 = 没有视觉划分）
    role: str = ""  # 区域的角色：heading / paragraph / caption / formula / reference ……

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Block:
    """段落级单元（或标题行、公式、图内文字）。"""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: float
    nlines: int
    col: str = "F"
    kind: str = "paragraph"  # paragraph | formula | figure_text | caption
    level: int = 0
    run_in: str | None = None  # 行内粗体小标题（如 “Problem formalization.”）
    eq_number: str | None = None
    lines: list[Line] = field(default_factory=list)
    heading_hint: bool = False
    ref_page: bool = False  # 这一页是按内容判断出来的参考文献页（整篇没有“References”标题时的兜底）
    role: str = ""  # 视觉划分给的角色（title / authors / abstract / heading / sidebar …），没有视觉划分时为空
    cont: list = field(default_factory=list)  # 跨页的段落：下一页接着的那一块（文字已并进 text，行内公式、粗体按各自的块取）

    @property
    def width(self) -> float:
        return self.x1 - self.x0


# 全角的字母、数字和方括号（中文排版常把 [12] 写成 ［１２］）换成半角；中文标点保持全角
_FULLWIDTH = {c: c - 0xFEE0 for c in [*range(0xFF10, 0xFF1A), *range(0xFF21, 0xFF3B), *range(0xFF41, 0xFF5B), 0xFF3B, 0xFF3D, 0xFF0E]}
_CJK_CHARS = "一-鿿㐀-䶿，。、；：？！（）《》“”‘’"
_CJK_RUNS = re.compile(rf"[{_CJK_CHARS}]+(?:[ \t\u00a0\u3000]+[{_CJK_CHARS}]+)+")
_GAP_SPLIT = re.compile(r"[ \t\u00a0\u3000]+")


def _join_cjk_runs(m: re.Match) -> str:
    """汉字之间的空格：版面上字距拉得很开的（“大 模 型 时 代”，每段只有一个字）是排版造成的，去掉；
    两个字以上的词之间的空格（作者名单“张三 李四 王五”）是真的分隔，保留。"""
    parts = _GAP_SPLIT.split(m.group(0))
    out = parts[0]
    for prev, nxt in zip(parts, parts[1:]):
        out += ("" if len(prev) == 1 or len(nxt) == 1 else " ") + nxt
    return out


# TeX 排版的带重音字母，PDF 文字层里是“重音符号 + 字母”两个字符（J¨urgen、Jos´e、Arbel´aez）：合成带重音的字母。
# 只在重音前面紧挨着字母时合并（人名、地名在词中间）；公式里的 ¨s（点点）前面不是字母，不受影响
_ACCENTS = {
    "¨": {"a": "ä", "e": "ë", "i": "ï", "o": "ö", "u": "ü", "y": "ÿ", "A": "Ä", "E": "Ë", "I": "Ï", "O": "Ö", "U": "Ü"},
    "´": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "y": "ý", "c": "ć", "n": "ń", "s": "ś", "z": "ź", "A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú", "C": "Ć"},
    "`": {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù", "A": "À", "E": "È", "O": "Ò"},
    "ˆ": {"a": "â", "e": "ê", "i": "î", "o": "ô", "u": "û", "A": "Â", "E": "Ê", "O": "Ô"},
    "˜": {"a": "ã", "n": "ñ", "o": "õ", "A": "Ã", "N": "Ñ", "O": "Õ"},
    "¸": {"c": "ç", "C": "Ç", "s": "ş", "S": "Ş"},
    "ˇ": {"c": "č", "s": "š", "z": "ž", "r": "ř", "e": "ě", "n": "ň", "C": "Č", "S": "Š", "Z": "Ž"},
    "˘": {"a": "ă", "g": "ğ", "A": "Ă", "G": "Ğ"},
}
_ACCENT_RE = re.compile("(?<=[A-Za-z])([" + "".join(_ACCENTS) + "])([A-Za-z])")


def _compose_accent(m: re.Match) -> str:
    return _ACCENTS[m.group(1)].get(m.group(2), m.group(0))


_ENTITY_RE = re.compile(r"&(?:#\d{1,5}|[A-Za-z]{2,8});")


def clean_text(text: str) -> str:
    if "&" in text and _ENTITY_RE.search(text):
        import html

        text = _ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)  # Cell：PGC-1&alpha; → PGC-1α，&mdash; → —
    # 根号的横线在有的字体（Computer Modern 的 CMEX、Cambria Math）里按字符 “ﬃ” 的编码存成一长串，文字层提取出来是 “pﬃﬃﬃﬃﬃ”：
    # 它只是 √ 的画法，不是文字，先换成 √ 再处理连字
    text = re.sub(r"p(?:ﬃ){2,}", "√", text)
    text = re.sub(r"p(?:ffi){3,}", "√", text)
    # AdvOT/Nature 的上横线字形有时单独以 U+0001/U+0003 输出，紧跟变量本体。
    # 这是确定的覆盖字形，不是未知字符；先还原成可继续参与公式渲染的 TeX。
    text = re.sub(r"[\x01\x03]\s*([A-Za-z])", r"\\bar{\1}", text)
    # 同一套字体的另一种 ToUnicode 顺序把横线放在变量后面（d¯i，视觉上是 d̄_i）。
    text = re.sub(r"([A-Za-z])[¯ˉ‾]", r"\\bar{\1}", text)
    text = _LIG_RE.sub(lambda m: LIGATURES[m.group(0)], text)
    text = _ACCENT_RE.sub(_compose_accent, text)
    text = text.translate(_FULLWIDTH)
    text = _CJK_RUNS.sub(_join_cjk_runs, text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)  # control chars from odd fonts, e.g. BEL after a section number
    text = re.sub(r"\ba1{6,}\b", " ", text)  # PLOS 首页的防抓取水印（a1111111111）
    return re.sub(r"[ \t   ]+", " ", text).strip()


def is_cjk(ch: str) -> bool:
    return bool(re.match(r"[一-鿿]", ch))


# ---------------------------------------------------------------- 片段提取
def unscramble_cjk(text: str) -> str:
    """有的 PDF（如部分会议论文集）字体映射有误，英文字母被存成 U+6520–U+657E（本该是 U+0020–U+007E 加上 0x6500）。
    一个片段里这个区间的字符占绝大多数时，才当作这种乱码还原；正常的中文里这个区间的字不会占绝大多数。"""
    if len(text) < 2:
        return text
    bad = sum(1 for ch in text if 0x6520 <= ord(ch) <= 0x657E)
    if bad / len(text) < 0.8:
        return text
    return "".join(chr(ord(ch) - 0x6500) if 0x6520 <= ord(ch) <= 0x657E else ch for ch in text)


def _looks_spaceless(text: str) -> bool:
    """一整行画成一个没有空格的串：很长、没有空白、字母占绝大多数（带脚注号、括号、连字都算）。"""
    return len(text) >= 14 and not any(c.isspace() or c in "/@\\_=" for c in text) and sum(c.isalpha() for c in text) >= 0.8 * len(text)  # 网址、邮箱、路径不算


def _has_glued_run(text: str) -> bool:
    """片段里夹着一段连续 24 个以上字母（“dormantmicrometastasesthathavesolved”）：同一片段前半有空格、后半被粘成一串（Cell 的 Hallmarks）。
    再长的正常单词也很少超过 24 个字母，真是长词时 _respace 只在字符间隙 ≥ 0.1 个字号处补空格，不会拆开。"""
    return re.search(r"[A-Za-zﬀ-ﬆ]{24,}", text) is not None


def _respace(text: str, raw_spans: dict, key: tuple[float, float], size: float) -> str:
    """有的出版社 PDF（Springer 等）把一整行正文画成一个没有空格字符的串，词间靠位置留出空隙（“duality,astructuraltransformation…”）。
    按字符位置补空格：相邻字符间隙 ≥ 0.1 个字号就是词界。只对“很长、没有空格、几乎全是字母”的片段做，正常片段不受影响。"""
    chars = raw_spans.get(key)
    if not chars or "".join(c["c"] for c in chars) != text:
        return text
    out = [chars[0]["c"]]
    for prev, cur in zip(chars, chars[1:]):
        if cur["bbox"][0] - prev["bbox"][2] >= 0.1 * size:
            out.append(" ")
        out.append(cur["c"])
    return "".join(out)


def _drop_overprint(spans: list[Span]) -> list[Span]:
    """叠印文字：有的示意图（Cell 的 SnapShot）把标签画了不止一遍，整词 “Stabilizes” 之上又叠着 “Sta”“abiliz”“es” 这些片段，
    MuPDF 的 words/text 提取会去重，但 dict 的 span 不会，解析出来就成了 “StabilizesStaabilizes”“Dok-7Dokk-7”。
    同一基线、同字号、横向范围落在另一个更长片段之内、文字又是它的子串的片段，是叠印的重复，丢掉（完全重复的只留一个）。"""
    if len(spans) < 2:
        return spans
    rows: dict[int, list[Span]] = {}
    for sp in spans:
        rows.setdefault(round(sp.baseline), []).append(sp)
    drop: set[int] = set()
    for r in rows.values():
        for neigh in (r,):
            for s in neigh:
                t = s.text.strip()
                if not t or id(s) in drop:
                    continue
                for k in neigh:
                    if k is s or id(k) in drop:
                        continue
                    kt = k.text.strip()
                    if (
                        abs(k.baseline - s.baseline) < 1.0 and abs(k.size - s.size) < 0.6 and s.x0 >= k.x0 - 1.0 and s.x1 <= k.x1 + 1.0
                        and t in kt and (len(kt) > len(t) or (kt == t and id(k) < id(s)))
                    ):
                        drop.add(id(s))
                        break
    if not drop:
        return spans
    return [sp for sp in spans if id(sp) not in drop]


def extract_spans(page: pymupdf.Page) -> list[Span]:
    data = page.get_text("dict")
    raw_spans: dict | None = None
    dirs: dict[tuple[int, int], int] = {}
    for b in data["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if any(s["text"].strip() for s in ln["spans"]):
                key = (round(ln["dir"][0]), round(ln["dir"][1]))
                dirs[key] = dirs.get(key, 0) + 1
    dominant = max(dirs, key=dirs.get) if dirs else (1, 0)
    out: list[Span] = []
    for b in data["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if (round(ln["dir"][0]), round(ln["dir"][1])) != dominant:
                continue  # 侧边水印（如 arXiv 编号）等非主方向文字
            for sp in ln["spans"]:
                text = unscramble_cjk(sp["text"])
                if not text.strip():
                    continue
                lead_space = sp["text"][:1].isspace()
                if _looks_spaceless(text) or _has_glued_run(text) or lead_space:
                    if raw_spans is None:
                        raw_spans = {}
                        for rb in page.get_text("rawdict")["blocks"]:
                            for rl in rb.get("lines", []):
                                for rs in rl["spans"]:
                                    raw_spans[(round(rs["origin"][1], 1), round(rs["bbox"][0], 1))] = rs["chars"]
                if _looks_spaceless(text) or _has_glued_run(text):
                    text = _respace(text, raw_spans, (round(sp["origin"][1], 1), round(sp["bbox"][0], 1)), float(sp["size"]))
                x0, y0, x1, y1 = sp["bbox"]
                base_y = sp["origin"][1]
                if lead_space and raw_spans is not None:
                    # TeX 排出来的上下标常是 “ K” 这样以空格开头的片段：PyMuPDF 把整个片段的基线记成那个空格的（正文基线），
                    # 上标 K 就被当成了正文字符（W^K_i 变成 “KW K_i”）。基线、位置要用第一个非空格字符自己的
                    chars = raw_spans.get((round(sp["origin"][1], 1), round(sp["bbox"][0], 1)))
                    k = next((i for i, c in enumerate(chars or []) if not c["c"].isspace()), 0)
                    if chars and k > 0:
                        c0 = chars[k]
                        dy = c0["origin"][1] - base_y
                        if abs(dy) > 0.4:
                            base_y += dy
                            y0 += dy
                            y1 += dy
                        x0 = max(x0, c0["bbox"][0])
                font = sp["font"]
                font_low = font.lower()
                # AdvMacMthSyN 是 Nature/AdvOT 常见的自定义数学符号字体，
                # 其 ToUnicode 映射把括号和减号输出成了私有码位。按字体解码，
                # 不依赖具体论文里的公式内容。
                if "advmacmthsy" in font_low:
                    text = text.translate({0xF0: "(", 0xDE: ")", 0x02: "-"})
                elif "advp4" in font_low and text.strip() in ("\x01", "\x03"):
                    text = "¯"
                if "cmex" in font.lower() and len(text.strip()) == 1 and text.strip() not in "PXQ" and (x1 - x0) >= 0.7 * float(sp["size"]):
                    text = "√"  # CMEX10 里的根号（控制字符码，clean_text 会把它当空白丢掉，式子就少了一个根号）
                elif "cmex" in font.lower() and text.strip() in ("R", "Z"):
                    text = "∫"  # CMEX10 里行内/显示的积分号（编码成字母 R / Z，抽出来是个孤零零的 “R”）
                if text.strip() == "I" and (x1 - x0) < 0.45 * float(sp["size"]) and MATH_FONT_RE.search(font):
                    continue  # 自定义数学字体里的装饰字形（竖线/覆盖符），不是文字
                out.append(
                    Span(
                        x0, y0, x1, y1, base_y, text, float(sp["size"]),
                        bool(sp["flags"] & 16) or "bold" in font.lower() or "semibold" in font.lower()
                        or bool(re.search(r"-(bd|bol)\b", font.lower())),
                        bool(sp["flags"] & 2) or bool(re.search(r"italic|-it\d*$|oblique", font.lower())),
                        font, bool(MATH_FONT_RE.search(font)) and not set(text.strip()) <= _LIG_CHARS,  # 单独排在子集字体里的连字（ﬁ）不是数学
                    )
                )
    # 根号的横线是细线、字形又比正文高，基线和后面的被开方式差得多，会被排到另一行、把 “ṡ(t)=√b(s)” 拦腰切断：
    # 给它换上紧右边那个片段的基线，让它和被开方式、前面的等号排在同一行
    out = _stack_fractions(page, out)  # 先并分式：根号要对齐的是“根号 + 分式”整体的基线
    for r in out:
        if r.text != "√":
            continue
        # 根号字形整个抬在被开方式上面（字形底边几乎只碰到被开方式的顶边），所以不能要求竖向重叠，只看紧右边、纵向相距不到一个字号
        nxt = [s for s in out if s is not r and -0.9 * r.size <= s.x0 - r.x1 <= 0.6 * r.size and abs((s.y0 + s.y1) / 2 - (r.y0 + r.y1) / 2) <= 1.2 * r.size]
        if nxt:
            r.baseline = min(nxt, key=lambda s: s.x0).baseline
    return _drop_overprint(out)


def _stack_fractions(page: pymupdf.Page, spans: list[Span]) -> list[Span]:
    """行内的堆叠分式（如 “s̈(t) = ∂ₛb(s)/2”）：小字号的分子、一条分数线、小字号的分母。
    它们本来会被切成三处：分子成了另一行（后来被并进上面的公式块）、分母单独成行，正文里只剩一个 “= 2”。
    用画出来的那条分数线找回来：线正上方、正下方且落在线的横向范围内的片段，合成一个和左边正文同基线的片段。"""
    small = [s for s in spans if s.text.strip()]
    if len(small) < 3:
        return spans
    body = body_font_size([spans])
    if not any(s.size < 0.9 * body for s in small):
        return spans
    try:
        bars = [d["rect"] for d in page.get_drawings() if d["rect"].height < 0.8 and 5.0 <= d["rect"].width <= 120.0]
    except Exception:  # noqa: BLE001  取不到矢量线就不处理分式
        return spans
    used: set[int] = set()
    out = list(spans)
    for bar in bars:
        y = (bar.y0 + bar.y1) / 2
        x0, x1 = bar.x0 - 3.0, bar.x1 + 3.0
        # 按片段的中心点分上下：减号这类字形的包围盒会跨过分数线（m−1 的 “−” 落在线上），按整个包围盒分会把它漏掉
        num = [s for s in spans if id(s) not in used and s.x0 >= x0 and s.x1 <= x1 and y - 14 <= (s.y0 + s.y1) / 2 < y and s.text.strip()]
        den = [s for s in spans if id(s) not in used and s.x0 >= x0 and s.x1 <= x1 and y < (s.y0 + s.y1) / 2 <= y + 14 and s.text.strip()]
        if not num or not den or any(s.size >= 0.9 * body for s in num + den):
            continue  # 整行大小的分式是单独的公式块，不在这里处理
        num.sort(key=lambda s: s.x0)
        den.sort(key=lambda s: s.x0)
        # 左边紧挨着的正文大小片段：分式的基线要和它对齐，找不到就不是行内分式
        left = [s for s in spans if s.size >= 0.9 * body and -2.0 <= x0 + 3.0 - s.x1 <= 14.0 and s.y0 - 4 <= y <= s.y1 + 4]
        if not left:
            continue
        ref = max(left, key=lambda s: s.x1)
        up = "".join(s.text for s in num).strip()
        dn = "".join(s.text for s in den).strip()
        frac = Span(
            min(s.x0 for s in num + den), min(s.y0 for s in num), max(s.x1 for s in num + den), max(s.y1 for s in den), ref.baseline,
            f"({up})/({dn})", ref.size, False, False, num[0].font, True,
        )
        frac.frac = (num, den)
        for s in num + den:
            used.add(id(s))
        out = [s for s in out if id(s) not in used] + [frac]
    return out


def body_font_size(all_spans: list[list[Span]]) -> float:
    counter: dict[float, int] = {}
    for spans in all_spans:
        for s in spans:
            k = round(s.size * 2) / 2
            counter[k] = counter.get(k, 0) + len(s.text)
    return max(counter, key=counter.get) if counter else 10.0


# ---------------------------------------------------------------- 栏检测
def _gutter(spans: list[Span], page_h: float | None, reach: float, min_side: float, min_run: int, sidebar: bool = False) -> tuple[float, float] | None:
    """在窄片段（栏内文字）的 x 占用里找页面中部的空隙，作为栏间沟。没有可靠的沟则视为单栏。
    页眉页脚区域（页码、期刊名等）不参与判断，且两侧都必须有足够多的栏内文字。"""
    if page_h:
        spans = [s for s in spans if s.y1 >= 0.08 * page_h and s.y0 <= 0.92 * page_h]
    if len(spans) < 8:
        return None
    left = min(s.x0 for s in spans)
    right = max(s.x1 for s in spans)
    span_w = right - left
    if span_w < 200:
        return None
    mid = (left + right) / 2
    # 跨过页面中线的行（通栏的标题、摘要，居中的作者行、收稿日期：中间没有空档）不参与统计，否则它们盖住栏沟，两栏页面会被判成单栏。
    # 证据只来自“确实在中间断开”或者“整行落在一侧”的行
    # PRL、Nature 这类首页上方都有大段通栏文字，而且 PDF 里每个词常常是单独的片段，片段本身都很窄。
    rows: dict[int, list[Span]] = {}
    for sp in spans:
        if sp.text.strip():
            rows.setdefault(round(sp.baseline / 3.0), []).append(sp)
    full_ids: set[int] = set()
    for r in rows.values():
        r.sort(key=lambda x: x.x0)
        if r[0].x0 < mid < r[-1].x1 and not any(
            b_.x0 - a_.x1 >= 6.0 and mid - reach * span_w <= (a_.x1 + b_.x0) / 2 <= mid + reach * span_w for a_, b_ in zip(r, r[1:])
        ):
            full_ids.update(id(x) for x in r)
    narrow = [s for s in spans if (s.x1 - s.x0) < 0.55 * span_w and id(s) not in full_ids]
    if len(narrow) < 8:
        return None
    # 覆盖度直方图（1pt 一格）：栏间沟允许被少数跨栏片段（作者行、短标题）穿过，
    # 只要中部有一段覆盖度远低于两侧即可。
    nbins = int(span_w) + 2
    cov = [0] * (nbins + 1)
    for sp in narrow:
        i0, i1 = max(0, int(sp.x0 - left)), min(nbins, int(sp.x1 - left) + 1)
        cov[i0] += 1
        cov[i1] -= 1
    run = 0
    cover: list[int] = []
    for d in cov[:nbins]:
        run += d
        cover.append(run)
    ordered = sorted(cover)
    peak = ordered[int(0.9 * (len(ordered) - 1))] if ordered else 0
    thresh = max(2.0, 0.12 * max(cover) if cover else 2.0)
    if max(cover, default=0) < 6:
        return None
    lo, hi = int(max(0, (mid - left) - reach * span_w)), int(min(nbins, (mid - left) + reach * span_w))
    runs: list[tuple[int, int]] = []
    i = lo
    while i < hi:
        if cover[i] <= thresh:
            j = i
            while j < hi and cover[j] <= thresh:
                j += 1
            if j - i >= min_run:
                runs.append((i, j))
            i = j
        else:
            i += 1
    # 从最宽的空档开始，第一个“两侧都是成段文字”的就是栏沟（宽的那条可能只是靠右排版的零星几行留出来的）
    ranked = sorted(runs, key=lambda r: r[1] - r[0], reverse=True)
    # 普通两栏只认最宽的那条空档；侧栏模式（栏沟不在正中）才逐条试：最宽的那条可能是靠右排版的零星几行留出来的，
    # 但真正的侧栏沟必须靠近版心的一侧边缘（侧栏宽度不超过版心的 35%），否则表格、参考文献里的空白条会被误当成栏沟
    for best in (ranked if sidebar else ranked[:1]):
        g0, g1 = left + best[0], left + best[1]
        if sidebar and min(g0 - left, right - g1) > 0.35 * span_w:
            continue
        if sidebar:
            # 真正的侧栏沟几乎没有文字穿过；单栏的参考文献（悬挂缩进）偶然在某处留出的空白，会有大量长行从上面跨过去
            texts = [sp for sp in spans if sp.text.strip()]
            if sum(1 for sp in texts if sp.x0 < g0 - 1 and sp.x1 > g1 + 1) > 0.05 * len(texts):
                continue
        n_left = sum(1 for sp in narrow if sp.x1 <= g0 + 2)
        n_right = sum(1 for sp in narrow if sp.x0 >= g1 - 2)
        if n_left < max(6, min_side * len(narrow)) or n_right < max(6, min_side * len(narrow)):
            continue
        # 两侧都必须是成段的文字：各有至少 8 行、每行文字总宽不少于 50pt。
        # 目录页右边一列页码、表格右边一列数字也会留出一条空白，但它们每行只有十几 pt 宽，不是栏
        side_w: dict[int, list[float]] = {}
        for sp in spans:
            if sp.text.strip():
                cnt = side_w.setdefault(round(sp.baseline / 3.0), [0.0, 0.0])
                if sp.x1 <= g0 + 1:
                    cnt[0] += sp.x1 - sp.x0
                elif sp.x0 >= g1 - 1:
                    cnt[1] += sp.x1 - sp.x0
        min_w, min_rows = (35, 6) if sidebar else (50, 8)  # 侧栏的行很窄、行数也少（JOSS 左边的 DOI、Review、License 小栏）
        if sum(1 for lw, _rw in side_w.values() if lw >= min_w) < min_rows or sum(1 for _lw, rw in side_w.values() if rw >= min_w) < min_rows:
            continue
        # 收紧到左右两栏的真实边缘：参考文献是悬挂缩进（第一行顶格、后面的行缩进），右栏在缩进区只有零星几行，
        # 覆盖度很低，会把“空档”从栏边一直拉宽到缩进处，顶格的行就被误判成跨栏的通栏行
        starts = [sp.x0 for sp in narrow if sp.x0 >= g0 + 1]
        if starts:
            t1 = min(starts)  # 右栏最靠左的文字
            ends = [sp.x1 for sp in narrow if sp.x1 <= t1 + 0.5]
            t0 = max(ends) if ends else g0  # 左栏最靠右的文字
            if t1 - t0 >= 4 and t0 >= g0 - 12:
                g0, g1 = t0, t1
        # 侧栏和正文之间的真实空隙可能比直方图里的“空档”窄得多（正文最右的词伸进了空档）：收到两侧文字的真实边缘
        intr = [sp.x1 for sp in spans if sp.text.strip() and sp.x0 < g0 and g0 < sp.x1 < g1 - 2]
        if len(intr) >= 3 and g1 - max(intr) >= 4:
            g0 = max(intr)
        intr = [sp.x0 for sp in spans if sp.text.strip() and sp.x1 > g1 and g0 + 2 < sp.x0 < g1]
        if len(intr) >= 3 and min(intr) - g0 >= 4:
            g1 = min(intr)
        return (g0, g1)
    return None




def detect_gutter(spans: list[Span], page_h: float | None = None) -> tuple[float, float] | None:
    """栏间沟。先在页面正中间找（普通两栏）；找不到再放宽范围找一次——首页左边常有一条窄的侧栏
    （PLOS ONE、Frontiers 的版权、日期、基金信息），沟不在页面正中，居中找就会把侧栏和正文一行一行交错拼在一起。
    放宽后要求两侧都是成段的文字（见 _gutter 末尾），避免把单栏页里偶然的空白条当成栏沟。"""
    return _gutter(spans, page_h, 0.14, 0.25, 5) or _gutter(spans, page_h, 0.36, 0.1, 6, True)

# ---------------------------------------------------------------- 行重组
def _classify(spans: list[Span], gutter: tuple[float, float] | None) -> None:
    for s in spans:
        if gutter is None:
            s.cls = "F"
        elif s.x1 <= gutter[0] + 2:
            s.cls = "L"
        elif s.x0 >= gutter[1] - 2:
            s.cls = "R"
        else:
            s.cls = "F"


_ICON_FONT = re.compile(r"marvosym|wingding|dingbats|webdings", re.I)


def _line_from(spans: list[Span]) -> Line:
    spans = sorted(spans, key=lambda s: s.x0)
    real = [s for s in spans if not _ICON_FONT.search(s.font)] or spans  # 信封、电话这类图标字形（字号常比正文大）不决定这一行的字号
    main = max(real, key=lambda s: (s.size, len(s.text)))
    parts: list[str] = []
    chars = 0
    bold = ital = mathc = 0
    for i, s in enumerate(spans):
        # 第一个片段只去掉开头的空白：结尾的空格要留着，否则后面紧跟的斜体变量会粘在词上（“chooseM”“then-step”）
        t = s.text.lstrip() if i == 0 else s.text
        if i > 0:
            prev_s = spans[i - 1]
            gap = s.x0 - prev_s.x1
            alnum_edge = prev_s.text.rstrip()[-1:].isalnum() and t.lstrip()[:1].isalnum()
            similar = min(s.size, prev_s.size) >= 0.85 * max(s.size, prev_s.size)
            need = gap > 0.12 * main.size or (alnum_edge and similar and prev_s.font != s.font and gap > 0.0)
            if set(s.text.strip()) <= _LIG_CHARS or set(prev_s.text.strip()) <= _LIG_CHARS:
                need = gap > 0.5 * main.size  # 连字（ﬁ）单独排在一个子集字体里：和前后字母之间不是词间空格
            if need and not parts[-1].endswith(" ") and not t.startswith(" "):
                parts.append(" ")
            t = t if parts and parts[-1].endswith(" ") else t
        txt = t
        if s.math:
            for k, v in GLYPH_FIX.items():
                txt = txt.replace(k, v)
        parts.append(txt)
        n = len(s.text.strip())
        chars += n
        if s.bold:
            bold += n
        if s.italic:
            ital += n
        if s.math or any(ch in MATH_CHARS for ch in s.text):
            mathc += n
    text = clean_text("".join(parts))
    chars = max(1, chars)
    return Line(
        spans, min(s.x0 for s in spans), min(s.y0 for s in spans), max(s.x1 for s in spans), max(s.y1 for s in spans),
        main.baseline, main.size, spans[0].cls, text, bold / chars, ital / chars, mathc / chars,
    )


_ACCENT_DOTS = {"...", "…", "˙", "¨"}


def _accent_over(cur: list[Span], sp: Span) -> bool:
    """sp 在 cur 里某个点状重音（s⃛ 的三点、ṗ 的点）的正下方、基线只低 0.8 个字号以内：它是被加重音的变量，并进同一行。"""
    for c in cur:
        if c.text.strip() in _ACCENT_DOTS and sp.x0 - 2.0 <= c.x1 and sp.x1 + 2.0 >= c.x0 and 0 < sp.baseline - c.baseline <= 0.8 * sp.size:
            return True
    return False


def _script_adjacent(cur: list[Span], sp: Span) -> bool:
    """sp 是紧挨着 cur 里某个片段（横向相距不超过 4pt）的上下标：一边字号不到另一边的 0.85 倍，而且只有 1-3 个字符。
    只有这样的片段才允许用“放宽的竖向重叠”并进这一行；否则 14pt 的标题会把相邻的 10pt 正文行（基线只差 7 磅）吸进来，两栏的行被串成一行。"""
    t = sp.text.strip()
    for c in cur:
        if not (-4.0 <= sp.x0 - c.x1 <= 4.0 or -4.0 <= c.x0 - sp.x1 <= 4.0):
            continue
        if sp.size <= 0.85 * c.size and 1 <= len(t) <= 3:
            return True
        if c.size <= 0.85 * sp.size and 1 <= len(c.text.strip()) <= 3:
            return True
    return False


def _cluster(group: list[Span]) -> list[list[Span]]:
    """按基线（或垂直范围大幅重叠）把片段聚成视觉行。"""
    group = sorted(group, key=lambda s: (s.baseline, s.x0))
    out: list[list[Span]] = []
    cur: list[Span] = []
    ref_base = ref_size = 0.0
    for sp in group:
        same = False
        loose = False
        if cur:
            same = abs(sp.baseline - ref_base) <= 0.42 * max(ref_size, sp.size)
            if not same:
                loose = True
                top = min(c.y0 for c in cur)
                bot = max(c.y1 for c in cur)
                ov = min(bot, sp.y1) - max(top, sp.y0)
                # 行内的大号求和号等会把基线抬高 6 左右（字号 8）：竖向大幅重叠时容忍到 0.85 倍字号；
                # 相邻两行的基线距至少是 1.1 倍字号，而且几乎不重叠，所以不会把两行并成一行
                same = ov >= 0.65 * min(bot - top, sp.y1 - sp.y0) and abs(sp.baseline - ref_base) <= 0.85 * max(ref_size, sp.size)
                if not same and _accent_over(cur, sp):
                    same = True
                elif not same and _script_adjacent(cur, sp):
                    # 上标（fb⁻¹、10⁻⁵）字号只有正文的 0.76 倍、基线抬高 5 磅左右，和正文的竖向重叠刚好不够 65%：
                    # 一边明显小一号时，重叠有一半、基线差不到 0.6 倍字号就是上下标，并进这一行；否则它们自成一行，把一句话切成三段
                    same = ov >= 0.5 * min(bot - top, sp.y1 - sp.y0) and abs(sp.baseline - ref_base) <= 0.6 * max(ref_size, sp.size)
        if same:
            cur.append(sp)
            # 靠宽松规则并进来的大号字形（求和号等，基线抬高好几磅）不能改这一行的参照基线：
            # 否则下一行的词会以它为准被并进来，一连串行（甚至另一栏的行）被串成一行再按横坐标排序，词序混乱、词被粘在一起
            if sp.size >= ref_size and not loose:
                ref_base, ref_size = sp.baseline, sp.size
        else:
            if cur:
                out.append(cur)
            cur, ref_base, ref_size = [sp], sp.baseline, sp.size
    if cur:
        out.append(cur)
    return out


def _split_by_gutter(group: list[Span], gutter: tuple[float, float] | None) -> list[tuple[str, list[Span]]]:
    """一条视觉行是整宽（F）还是左右两栏各一段：看栏沟处文字是否连续。"""
    if gutter is None:
        return [("F", group)]
    g0, g1 = gutter
    mid_x = (g0 + g1) / 2
    # 一栏的行尾可以略微伸进栏沟（参考文献页常见：左栏行尾离右栏起点只差几个点）：只要整个片段还在栏沟中线的一侧、没有碰到另一栏的文字，就仍属于那一栏
    left = [s for s in group if s.x1 <= g0 + 2 or (s.x1 <= g1 - 2 and s.x1 <= mid_x)]
    right = [s for s in group if s not in left and (s.x0 >= g1 - 2 or (s.x0 >= g0 + 2 and s.x0 >= mid_x))]
    mid = [s for s in group if s not in left and s not in right]
    if mid:
        return [("F", group)]  # 有片段跨过栏沟：整宽行（图注、作者行、通栏标题）
    if left and right:
        # 按栏沟两侧紧挨着的两个片段里较小的字号比：右栏是 14pt 的粗体标题、左栏是 10pt 正文时，
        # 两栏之间的空隙（10pt）必须算作“两栏”，不能用最大字号把它当成跨栏的连续文字
        lead = min(right, key=lambda s: s.x0)
        tail = max(left, key=lambda s: s.x1)
        size = min(lead.size, tail.size)
        gap = lead.x0 - tail.x1
        if gap >= max(6.0, 0.9 * size):
            return [("L", left), ("R", right)]
        return [("F", group)]  # 两侧文字几乎相连：跨栏的连续文字
    return [("L" if left else "R", group)]


def _split_wide_gaps(group: list[Span], size_gap: float = 3.0) -> list[list[Span]]:
    parts: list[list[Span]] = []
    for sp in sorted(group, key=lambda s: s.x0):
        if parts and sp.x0 - max(c.x1 for c in parts[-1]) > size_gap * max(sp.size, 6):
            parts.append([sp])
        else:
            if not parts:
                parts.append([])
            parts[-1].append(sp)
    return parts


def _split_mixed_far(group: list[Span]) -> list[list[Span]]:
    """同一基线附近、隔得很远、字号又明显不同的两段（左边 12pt 的章节标题，右边 9pt 的图里图例）不是同一行：
    基线只差一点点会被聚到一起，必须按空隙拆开。上下标紧挨着正文，不受影响。"""
    ordered = sorted(group, key=lambda s: s.x0)
    parts: list[list[Span]] = [[ordered[0]]] if ordered else []
    for sp in ordered[1:]:
        prev = parts[-1]
        gap = sp.x0 - max(c.x1 for c in prev)
        left_size = max(c.size for c in prev)
        if gap > 4.0 * max(sp.size, left_size) and min(sp.size, left_size) / max(sp.size, left_size) < 0.85:
            parts.append([sp])
        else:
            prev.append(sp)
    return parts


def _fix_raised_radicals(spans: list[Span]) -> None:
    """“√s”（质心能量）里的根号字形被排版成一个单独的、画在上一行里的大号字符（它的底边在 s 的上方），
    抽出来就落在上一行的末尾（“presented √ from searches … at s = 7”）。根号右边紧挨着、下一行的那个字母才是它的被开方式：把根号挪到那一行。"""
    for r in spans:
        if r.text.strip() != "√":
            continue
        target = None
        for s in spans:
            if s is r or not s.text.strip() or s.text.strip() == "√":
                continue
            below = s.baseline - r.baseline
            if 0.5 * s.size < below < 2.2 * s.size and -1.0 <= s.x0 - r.x1 <= 3.0 and s.size <= 1.2 * r.size:
                if target is None or s.baseline < target.baseline:
                    target = s
        if target is not None:
            shift = target.baseline - r.baseline
            r.baseline += shift
            r.y0 += shift
            r.y1 += shift


def _fix_tall_operators(spans: list[Span]) -> None:
    """行内的积分号、求和号是一个比正文高两倍多的大字形，原点画在上一行和下一行之间；按它自己的基线聚类，会把上下两行的词串成一行
    （“DefineRM λ(gij) = inf …”）。字形右边紧挨着的正文片段所在的基线，才是它的基线：挪过去。"""
    for r in spans:
        t = r.text.strip()
        if len(t) != 1 or r.size < 8 or (r.y1 - r.y0) <= 1.8 * r.size:
            continue
        mid = (r.y0 + r.y1) / 2
        best = None
        for o in spans:
            if o is r or not o.text.strip() or o.size < 0.8 * r.size or (o.y1 - o.y0) > 1.8 * o.size:
                continue
            if not (-1.0 <= o.x0 - r.x1 <= 14.0):
                continue
            d = abs(o.baseline - mid)
            if d <= 0.45 * (r.y1 - r.y0) and (best is None or d < best[0]):
                best = (d, o)
        if best is not None:
            shift = best[1].baseline - r.baseline
            r.baseline += shift
            r.y0 += shift
            r.y1 += shift


def _unmerge_columns(group: list[Span], gutter: tuple[float, float] | None) -> list[list[Span]]:
    """右栏的显示公式（求和号、分式的上下标，基线参差）会把左栏相邻的几行也聚成一个“行”，再按横坐标排序，左栏几行的词就交错在一起
    （“Trainingby the factDeepthat…”）。这个聚簇如果两栏各自单独聚类时，某一栏其实是好几行，就按栏拆开重聚。
    整宽的行（有片段跨栏沟）、左右各只有一行的普通行，都原样保留。"""
    if gutter is None:
        return [group]
    g0, g1 = gutter
    left = [s for s in group if s.x1 <= g0 + 2]
    right = [s for s in group if s.x0 >= g1 - 2]
    if not left or not right or len(left) + len(right) != len(group):
        return [group]
    lc, rc = _cluster(left), _cluster(right)
    if len(lc) > 1 or len(rc) > 1:
        return lc + rc
    return [group]


def _split_stacked(group: list[Span]) -> list[list[Span]]:
    """上下两行正文被一个跨行的小字号片段（“inf”下面的 “M”、大号求和号的上下限）串成了一个簇，再按横坐标排序，两行的词就交错在一起
    （“infimumis just theis takenlowestover…”）。簇里正文字号的长词片段落在两条以上相距超过 0.7 个字高的基线上、
    每条基线上都有成句的文字（≥8 个字母、≥2 个片段）时，按基线拆成几行，小字号片段归到最近的基线。"""
    if len(group) < 6:
        return [group]
    top = max(s.size for s in group)
    big = [s for s in group if s.size >= 0.85 * top and sum(c.isalpha() for c in s.text) >= 3]
    if len(big) < 4:
        return [group]
    rows: list[list[Span]] = []
    for sp in sorted(big, key=lambda s: s.baseline):
        if rows and abs(sp.baseline - rows[-1][-1].baseline) <= 0.7 * top:
            rows[-1].append(sp)
        else:
            rows.append([sp])
    rows = [r for r in rows if len(r) >= 2 and sum(sum(c.isalpha() for c in s.text) for s in r) >= 8]
    if len(rows) < 2:
        return [group]
    base = [sum(s.baseline for s in r) / len(r) for r in rows]
    out: list[list[Span]] = [[] for _ in rows]
    for sp in group:
        i = min(range(len(rows)), key=lambda k: abs(sp.baseline - base[k]))
        out[i].append(sp)
    return [g for g in out if g]


def _attach_orphan_scripts(clusters: list[list[Span]]) -> list[list[Span]]:
    """c_a^{(j)} 这类“上标压在下标上面”的组合：上标 “(j)” 离正文基线太远、旁边紧挨着的是下标（也是小字号），按两两相邻的规则并不进正文行，
    于是自成一行，一句话被它截成三段。整个簇都是小字号的几个字符、而且紧挨着（横向 ≤4pt、竖向不到一个字高）另一个簇里的某个片段时，并进那个簇。"""
    if len(clusters) < 2:
        return clusters
    out = list(clusters)
    for cl in clusters:
        size = max(s.size for s in cl)
        if sum(len(s.text.strip()) for s in cl) > 6:
            continue
        big = [c for c in out if c is not cl and max(s.size for s in c) >= 1.25 * size]
        if not big:
            continue
        top, bot = min(s.y0 for s in cl), max(s.y1 for s in cl)
        x0, x1 = min(s.x0 for s in cl), max(s.x1 for s in cl)
        best = None
        for c in big:
            for t in c:
                near_x = t.x1 >= x0 - 4.0 and t.x0 <= x1 + 4.0
                gap_y = max(t.y0 - bot, top - t.y1, 0.0)
                if near_x and gap_y <= 0.6 * t.size and (best is None or gap_y < best[0]):
                    best = (gap_y, c)
        if best is not None:
            best[1].extend(cl)
            out = [c for c in out if c is not cl]
    return out


def detect_columns(spans: list[Span], page_h: float | None = None, loose: bool = False) -> list[tuple[float, float]]:
    """三栏页面（Cell 的评述/预览：三栏等宽，各约 150pt）的两条栏沟 [(g0, g1), (g0, g1)]；不是三栏返回 []。
    只用正文字号的片段数覆盖度（图注、图里的字、标题、提要横跨栏沟，会把空档“填”起来）；两条沟之间、两侧的三栏宽度必须接近（≤1.2 倍），
    否则是图表或别的版式，不当三栏。
    loose=True：宽松档，给栏宽不等的三栏页（Cell：左栏窄、中栏宽——图和下面的文字占两栏宽——、右栏窄）：空档的覆盖度阈值放到 30%
    （图下面的文字、图注里个别长行会伸进栏沟），三栏的宽度比不限，但两条沟都不能宽过 32pt（更宽的是图表里的空白），
    而且三栏里每一栏都要有至少 12 行成段的文字。"""
    if page_h:
        spans = [s for s in spans if s.y1 >= 0.08 * page_h and s.y0 <= 0.92 * page_h]
    if len(spans) < 30:
        return []
    left = min(s.x0 for s in spans)
    right = max(s.x1 for s in spans)
    span_w = right - left
    if span_w < 300:
        return []
    sizes: dict[float, int] = {}
    for s in spans:
        k = round(s.size * 2) / 2
        sizes[k] = sizes.get(k, 0) + len(s.text.strip())
    tot = sum(sizes.values()) or 1
    allowed = [k for k, v in sizes.items() if v >= 0.15 * tot] or [0.0]
    narrow = [s for s in spans if (s.x1 - s.x0) < 0.4 * span_w and s.text.strip() and any(abs(s.size - k) <= 0.8 for k in allowed)]
    if len(narrow) < 30:
        return []
    nbins = int(span_w) + 2
    cov = [0] * (nbins + 1)
    for sp in narrow:
        cov[max(0, int(sp.x0 - left))] += 1
        cov[min(nbins, int(sp.x1 - left) + 1)] -= 1
    run = 0
    cover: list[int] = []
    for d in cov[:nbins]:
        run += d
        cover.append(run)
    thresh = max(2.0, (0.30 if loose else 0.12) * max(cover))
    lo, hi = int(0.12 * span_w), int(0.88 * span_w)
    runs: list[tuple[float, float]] = []
    i = lo
    while i < hi:
        if cover[i] <= thresh:
            j = i
            while j < hi and cover[j] <= thresh:
                j += 1
            if j - i >= 8:
                runs.append((left + i, left + j))
            i = j
        else:
            i += 1
    texts = [s for s in spans if s.text.strip()]
    good: list[tuple[float, float]] = []
    for g0, g1 in runs:
        if sum(1 for s in texts if s.x0 < g0 - 1 and s.x1 > g1 + 1) > 0.35 * len(texts):
            continue
        lefts = [s.x1 for s in narrow if s.x1 <= g0 + 2]
        rights = [s.x0 for s in narrow if s.x0 >= g1 - 2]
        if len(lefts) < 12 or len(rights) < 12:
            continue
        t0, t1 = max(lefts), min(rights)  # 收紧到两侧文字的真实边缘
        if t1 - t0 < 4:
            continue
        side_w: dict[int, list[float]] = {}
        for sp in texts:
            c = side_w.setdefault(round(sp.baseline / 3.0), [0.0, 0.0])
            if sp.x1 <= t0 + 1:
                c[0] += sp.x1 - sp.x0
            elif sp.x0 >= t1 - 1:
                c[1] += sp.x1 - sp.x0
        if sum(1 for a, _ in side_w.values() if a >= 40) < 8 or sum(1 for _, b in side_w.values() if b >= 40) < 8:
            continue
        good.append((t0, t1))
    good.sort()
    out: list[tuple[float, float]] = []
    for g in good:
        if not out or (g[0] + g[1]) / 2 - (out[-1][0] + out[-1][1]) / 2 >= 0.2 * span_w:
            out.append(g)
    if loose:
        # 宽松档：中栏很宽时，图注、正文行会在中栏里留出几条“假沟”：去掉过宽的空白，取最左、最右两条，它们才是整页的栏界
        cand = [g for g in good if g[1] - g[0] <= 32]
        out = [cand[0], cand[-1]] if len(cand) >= 2 else []
        if out and (out[1][0] + out[1][1]) / 2 - (out[0][0] + out[0][1]) / 2 < 0.35 * span_w:
            out = []
        if out:  # 重新收紧：图注比下面的正文伸得更远，原来的沟左边缘会压在图注上；以右边一侧文字的起点为准，往左取最近的文字边缘
            tight = []
            for g0, g1 in out:
                mid = (g0 + g1) / 2
                r_e = min((s.x0 for s in narrow if s.x0 >= mid), default=g1)
                l_e = max((s.x1 for s in narrow if s.x1 <= r_e - 6), default=g0)
                tight.append((l_e, r_e))
            out = tight
    if len(out) != 2:
        return []
    widths = [out[0][0] - left, out[1][0] - out[0][1], right - out[1][1]]
    if min(widths) <= 0:
        return []
    if loose:
        if any(g[1] - g[0] > 32 for g in out):
            return []
        for lo_x, hi_x in ((left, out[0][0]), (out[0][1], out[1][0]), (out[1][1], right)):
            rows = {round(s.baseline / 3.0) for s in narrow if s.x0 >= lo_x - 1 and s.x1 <= hi_x + 1}
            if len(rows) < 12:
                return []
    elif max(widths) / min(widths) > 1.2:
        return []
    return out


def lower_band_start(spans: list[Span], cols: list[tuple[float, float]], page_h: float | None = None) -> float | None:
    """宽松三栏页里，图下面常换成另一套栏界（左栏在图下面变宽，图上面的两条沟被文字跨过）：
    返回跨过这两条沟的文字最早出现的 y（图下面那一带从这里开始）；跨沟的行不到 6 条说明上下栏界一致，返回 None。"""
    ys = sorted(s.y0 for s in spans if s.text.strip() and any(s.x0 < g0 - 1 and s.x1 > g1 + 1 for g0, g1 in cols))
    if page_h:
        ys = [y for y in ys if y <= 0.92 * page_h]
    if len(ys) >= 6:
        return ys[0] - 2
    # 退一步：每个词都很窄，没有哪个词整个跨过上半区的栏沟，但下半区换了栏界，词就压进了上半区栏沟的竖带里。
    # 每条竖带单独算，取较晚的（图注、通栏小标题常常伸进某一条竖带，会把起点拉得过早）
    found: list[float] = []
    for g0, g1 in cols:
        over = sorted(s.y0 for s in spans if s.text.strip() and s.x0 < g1 - 1 and s.x1 > g0 + 1)
        if page_h:
            over = [y for y in over if y <= 0.92 * page_h]
        for i in range(0, min(3, len(over) - 6) + 1):
            if len(over) - i >= 6 and page_h and over[i] >= 0.55 * page_h:
                found.append(over[i] - 2)
                break
    return max(found) if found else None


def columns_from_prior(spans: list[Span], prior: list[list[tuple[float, float]]], page_h: float | None = None) -> list[tuple[float, float]]:
    """下半区只有几行字（不够 detect_columns 的行数门槛）时，借用同一份文档里别的三栏页检测出的栏沟位置做先验：
    在先验位置 ±14pt 内，必须真有一条 ≥6pt、几乎没有词压着（至多 1 个零星的词）的空白竖带，三栏里每栏至少 5 行、栏宽都为正，才采用。"""
    texts = [s for s in spans if s.text.strip() and (not page_h or s.y1 <= 0.92 * page_h)]  # 页脚（刊名、页码）不参与
    if len(texts) < 24:
        return []
    left, right = min(s.x0 for s in texts), max(s.x1 for s in texts)

    def cross(x: float) -> int:
        return sum(1 for s in texts if s.x0 < x + 0.5 and s.x1 > x - 0.5)

    for pair in prior:
        out: list[tuple[float, float]] = []
        for p0, p1 in pair:
            best: tuple[float, float] | None = None
            x = p0 - 14.0
            while x <= p1 + 14.0:
                if cross(x) <= 1:
                    j = x
                    while j <= p1 + 14.0 and cross(j) <= 1:
                        j += 0.5
                    if best is None or j - x > best[1] - best[0]:
                        best = (x, j)
                    x = j
                else:
                    x += 0.5
            if best is None or best[1] - best[0] < 6.0:
                out = []
                break
            # 收紧到两侧文字的真实边缘
            l_e = max((s.x1 for s in texts if s.x1 <= best[0] + 1), default=best[0])
            r_e = min((s.x0 for s in texts if s.x0 >= best[1] - 1), default=best[1])
            out.append((l_e, r_e))
        if len(out) != 2 or out[0][1] >= out[1][0]:
            continue
        ok = True
        for lo_x, hi_x in ((left, out[0][0]), (out[0][1], out[1][0]), (out[1][1], right)):
            rows = {round(s.baseline / 3.0) for s in texts if s.x0 >= lo_x - 1 and s.x1 <= hi_x + 1}
            if hi_x - lo_x <= 20 or len(rows) < 5:
                ok = False
        if ok:
            return out
    return []


def refine_gutters(spans: list[Span], cols: list[tuple[float, float]], page_h: float | None = None, reach: float = 24.0) -> list[tuple[float, float]]:
    """宽松检测出的栏沟有时落在中栏文字内部（图占着中栏时，只看得到图两边的词）。
    图下面中栏有正文了，就在原位置 ±reach 内重新找一条 ≥8pt、至多 1 个零星词压着的空白竖带，收紧到两侧文字边缘；找不到就保留原样。"""
    texts = [s for s in spans if s.text.strip() and (not page_h or s.y1 <= 0.92 * page_h)]
    if len(texts) < 24:
        return cols

    def cross(x: float) -> int:
        return sum(1 for s in texts if s.x0 < x + 0.5 and s.x1 > x - 0.5)

    out: list[tuple[float, float]] = []
    for g0, g1 in cols:
        best: tuple[float, float] | None = None
        x = g0 - reach
        while x <= g1 + reach:
            if cross(x) <= 1:
                j = x
                while j <= g1 + reach and cross(j) <= 1:
                    j += 0.5
                if best is None or j - x > best[1] - best[0]:
                    best = (x, j)
                x = j
            else:
                x += 0.5
        if best is None or best[1] - best[0] < 8.0:
            out.append((g0, g1))
            continue
        l_e = max((s.x1 for s in texts if s.x1 <= best[0] + 1), default=best[0])
        r_e = min((s.x0 for s in texts if s.x0 >= best[1] - 1), default=best[1])
        out.append((l_e, r_e) if l_e < r_e else (g0, g1))
    return out


def _split_by_gutters(group: list[Span], gutters: list[tuple[float, float]]) -> list[tuple[str, list[Span]]]:
    """按多条栏沟（从左到右）把一条视觉行拆成若干段 [(cls, 片段)]，cls ∈ L/M/R/F：
    某条栏沟被片段跨过（标题、通栏行）的那一段是 F，其余按所在的栏给 L/M/R。"""
    parts = [group]
    for g in gutters:
        new: list[list[Span]] = []
        for p in parts:
            res = _split_by_gutter(p, g)
            if len(res) == 2:
                new.extend(r[1] for r in res)
            else:
                new.append(p)
        parts = new
    out: list[tuple[str, list[Span]]] = []
    for p in parts:
        if any(s.x1 > g[0] + 2 and s.x0 < g[1] - 2 for g in gutters for s in p):
            out.append(("F", p))
            continue
        cx = (min(s.x0 for s in p) + max(s.x1 for s in p)) / 2
        k = sum(1 for g in gutters if (g[0] + g[1]) / 2 < cx)
        out.append((("L", "M", "R")[min(k, 2)], p))
    return out


def build_lines(spans: list[Span], gutter: tuple[float, float] | None, body: float, page_h: float | None = None,
                gutter2: tuple[float, float] | None = None) -> list[Line]:
    """gutter2：三栏页面的第二条栏沟（见 detect_columns），此时 gutter 是第一条。"""
    # 首字下沉：单个大号字母，并入其右侧缩进行的开头
    _fix_raised_radicals(spans)
    _fix_tall_operators(spans)
    caps = [s for s in spans if len(s.text.strip()) == 1 and s.text.strip().isalpha() and s.size >= 2.2 * body]
    spans = [s for s in spans if s not in caps]
    lines: list[Line] = []
    clusters: list[list[Span]] = []
    for group in _cluster(spans):
        for part in ([group] if gutter2 is not None else _unmerge_columns(group, gutter)):
            clusters.extend(_split_stacked(part))
    clusters = _attach_orphan_scripts(clusters)
    for group in clusters:
        in_band = bool(page_h) and (max(s.y1 for s in group) < 0.085 * page_h or min(s.y0 for s in group) > 0.915 * page_h)
        groups = _split_wide_gaps(group) if in_band else _split_mixed_far(group)  # 页眉里左右两个标签不应粘成一行
        for g in groups:
            for cls, part in (_split_by_gutters(g, [gutter, gutter2]) if gutter2 is not None and gutter is not None else _split_by_gutter(g, gutter)):
                for s in part:
                    s.cls = cls
                lines.append(_line_from(part))
    for cap in caps:
        letter = cap.text.strip()
        target = None
        for ln in sorted(lines, key=lambda l: l.y0):
            if cap.x1 - 3 <= ln.x0 <= cap.x1 + 16 and cap.y0 - 3 <= ln.y0 <= cap.y1:
                target = ln  # 必须是首字母正右侧紧邻的缩进行，而不是别的栏
                break
        if target is not None:
            target.text = letter + target.text
            target.x0 = min(target.x0, cap.x0)
    return lines


def detect_side_band(spans: list[Span], page_h: float | None = None) -> tuple[float, float, float, float] | None:
    """页面中段的一条窄侧栏（Frontiers：Edited by / Reviewed by / Correspondence），它右边是一栏很宽的摘要，上面是通栏的标题作者，
    下面才是两栏正文。侧栏和摘要各排各的行距，同一个视觉行里常常混着两边的词，整页又看不出栏沟。
    找法：左侧 40% 里找一条竖带，在一段不短的纵向范围内没有任何文字穿过，而且这段范围的上方、下方都有文字横着穿过它（被通栏的行夹住——
    整页贯通的竖带是普通的栏沟，不归这里管），两边在这段范围里都有成段的文字。返回 (竖带左, 竖带右, 范围上, 范围下)。"""
    use = [s for s in spans if s.text.strip() and (not page_h or (s.y1 >= 0.05 * page_h and s.y0 <= 0.95 * page_h))]
    if len(use) < 60:
        return None
    left = min(s.x0 for s in use)
    right = max(s.x1 for s in use)
    top = min(s.y0 for s in use)
    bot = max(s.y1 for s in use)
    span_w = right - left
    if span_w < 250 or bot - top < 300:
        return None
    best: tuple[float, float, float, float] | None = None
    best_len = 0.0
    x = left + 0.08 * span_w
    cands: list[tuple[float, float, float]] = []  # (x, run_lo, run_hi)
    while x <= left + 0.4 * span_w:
        ivs = sorted((s.y0, s.y1) for s in use if s.x0 < x < s.x1)
        # 穿过这条竖线的文字把纵向分成若干段空档：取最长且上下都被文字夹住的那一段
        edges = [top] + [v for iv in ivs for v in iv] + [bot]
        lo_best = hi_best = 0.0
        cur_hi = ivs[0][1] if ivs else None
        prev_end = None
        for (y0, y1) in ivs:
            if prev_end is not None and y0 - prev_end > hi_best - lo_best:
                lo_best, hi_best = prev_end, y0
            prev_end = max(prev_end, y1) if prev_end is not None else y1
        if hi_best - lo_best >= 110:
            cands.append((x, lo_best, hi_best))
        x += 1.0
    # 只留空档最长的那几条竖线（侧栏的行长短不一，侧栏内部的竖线也有一段空档，但比真正的竖带短得多）
    if cands:
        longest = max(c[2] - c[1] for c in cands)
        cands = [c for c in cands if c[2] - c[1] >= 0.85 * longest]
    # 合并相邻、纵向范围重合的竖线成一条带
    groups: list[list[tuple[float, float, float]]] = []
    for c in cands:
        if groups and c[0] - groups[-1][-1][0] <= 1.5 and min(c[2], groups[-1][-1][2]) - max(c[1], groups[-1][-1][1]) >= 100:
            groups[-1].append(c)
        else:
            groups.append([c])
    for g in groups:
        g0, g1 = g[0][0], g[-1][0]
        if g1 - g0 < 6:
            continue
        y_lo, y_hi = max(c[1] for c in g), min(c[2] for c in g)
        if y_hi - y_lo < 110 or y_hi - y_lo > 0.8 * (bot - top):
            continue
        mid = (g0 + g1) / 2
        above = any(s.x0 < mid - 1 and s.x1 > mid + 1 and s.y1 <= y_lo + 2 for s in use)
        below = any(s.x0 < mid - 1 and s.x1 > mid + 1 and s.y0 >= y_hi - 2 for s in use)
        if not (above and below):
            continue
        # 下面两栏正文的右栏第一行常常比左栏高一点，落在空档的下沿里：右边的行必须从竖带右缘起笔才算这一栏宽正文，范围收到最后一条这样的行
        starts = [s for s in use if y_lo <= (s.y0 + s.y1) / 2 <= y_hi and s.x0 >= g1 - 2]
        row_min: dict[int, float] = {}
        for sp in starts:
            k = round(sp.baseline / 3.0)
            row_min[k] = min(row_min.get(k, 1e9), sp.x0)
        good = {k for k, v in row_min.items() if v <= g1 + 8}
        if len(good) < 6:
            continue
        gy = [(sp.y0, sp.y1) for sp in starts if round(sp.baseline / 3.0) in good]
        y_lo, y_hi = max(y_lo, min(v[0] for v in gy) - 2), min(y_hi, max(v[1] for v in gy) + 2)
        if y_hi - y_lo < 60:
            continue
        inside = [s for s in use if y_lo <= (s.y0 + s.y1) / 2 <= y_hi]
        lft = [s for s in inside if s.x1 <= g0 + 2]
        rgt = [s for s in inside if s.x0 >= g1 - 2]
        if len(lft) < 12 or len(rgt) < 30 or len(lft) + len(rgt) < 0.9 * len(inside):
            continue
        lrows = {round(s.baseline / 3.0) for s in lft}
        rrows = {round(s.baseline / 3.0) for s in rgt}
        if len(lrows) < 6 or len(rrows) < 6:
            continue
        # 右边必须是密排的正文（词间空隙小），否则是表格的列
        rext = max(s.x1 for s in rgt) - min(s.x0 for s in rgt)
        if rext < 0.4 * span_w or sum(s.x1 - s.x0 for s in rgt) < 0.45 * rext * len(rrows):
            continue
        if (y_hi - y_lo) > best_len:
            best_len = y_hi - y_lo
            best = (g0, g1, y_lo, y_hi)
    return best


def normalize_classes(lines: list[Line]) -> None:
    """整宽（F）段落里的首/末短行完全落在栏沟一侧，会被分成 L/R 类并与段落断开。
    若同一水平带里另一栏没有文字，且紧邻的整宽行与它左对齐，就并回 F 类。"""
    full = [l for l in lines if l.cls == "F"]
    if not full:
        return
    others = {"L": [l for l in lines if l.cls == "R"], "R": [l for l in lines if l.cls == "L"]}
    for ln in lines:
        if ln.cls not in ("L", "R"):
            continue
        opposite = others[ln.cls]
        if any(min(o.y1, ln.y1) - max(o.y0, ln.y0) > 0.3 * (ln.y1 - ln.y0) for o in opposite):
            continue  # 另一栏在同一高度也有文字：这是真正的两栏
        for f in full:
            near = abs(f.baseline - ln.baseline) <= 2.6 * max(ln.size, f.size)
            if near and (abs(f.x0 - ln.x0) <= 3.0 or (ln.cls == "R" and abs(f.x1 - ln.x1) <= 3.0)):
                ln.cls = "F"
                break


def reading_order(lines: list[Line]) -> list[Line]:
    """整宽行作分隔，段内先左栏后右栏。"""
    ordered: list[Line] = []
    left: list[Line] = []
    right: list[Line] = []
    side: list[Line] = []
    mid_col: list[Line] = []  # 三栏页面的中栏
    wide: list[Line] = []

    page_w = (max(l.x1 for l in lines) - min(l.x0 for l in lines)) if lines else 0.0

    def head_run(col: list[Line]) -> list[Line]:
        run: list[Line] = []
        for l in sorted(col, key=lambda l: (l.y0, l.x0)):
            # 粗体的短行是左右两栏各自的章节标题（“1 Introduction | 2 Method”），不是并排的作者
            if l.width <= 0.4 * page_w and len(l.text.split()) <= 8 and l.bold < 0.6 and not NUMBERED_HEAD_RE.match(l.text) and not l.text.rstrip().endswith((".", ",", ";", ":")):
                run.append(l)
            else:
                break
        return run

    def flush() -> None:
        ordered.extend(sorted(side, key=lambda l: (l.y0, l.x0)))
        ordered.extend(sorted(wide, key=lambda l: (l.y0, l.x0)))
        side.clear()
        wide.clear()
        lft = sorted(left, key=lambda l: (l.y0, l.x0))
        rgt = sorted(right, key=lambda l: (l.y0, l.x0))
        # 作者并排排成左右两格（“Sergey Ioffe | Christian Szegedy”）：两格各是几行很短的姓名、单位。
        # 按“先左栏后右栏”排，右边的作者就跑到左栏正文（摘要、引言）后面去了；两边顶上都是这样的短行、并且同一高度起笔时，先放这两小块
        lr, rr = head_run(lft), head_run(rgt)
        if lr and rr and len(lr) < len(lft) and abs(rr[0].y0 - lr[0].y0) < max(rr[0].size, lr[0].size):
            lr = [l for l in lr if l.y0 <= rr[-1].y1]
            lft = lr + [l for l in lft if l not in lr]
            rgt_head = rr
            ordered.extend(lr)
            ordered.extend(rgt_head)
            lft = [l for l in lft if l not in lr]
            rgt = [l for l in rgt if l not in rgt_head]
        ordered.extend(lft)
        ordered.extend(sorted(mid_col, key=lambda l: (l.y0, l.x0)))
        mid_col.clear()
        ordered.extend(rgt)
        left.clear()
        right.clear()

    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        if ln.cls == "F" and ln.in_figure:
            # 表格/插图里横跨栏沟的格子不是正文的通栏行，不能当分隔：否则右栏顶上的标题被提到左栏表格之前、右栏其余的行又落到整个左栏之后
            left.append(ln)
        elif ln.cls == "F":
            flush()
            ordered.append(ln)
        elif ln.cls == "M":
            mid_col.append(ln)
        elif ln.cls == "S":
            side.append(ln)
        elif ln.cls == "W":
            wide.append(ln)
        elif ln.cls == "L":
            left.append(ln)
        else:
            right.append(ln)
    flush()
    return ordered


# ---------------------------------------------------------------- 分段
# 本篇文档里专门用来排小节标题的字体（PLOS ONE 的标题只比正文大 1 磅、字体名是混淆过的 AdvP41461E，字体标志位里也没有“粗体”）。
# 由 pdf_parser 在解析开始前设置、结束后清空（解析是串行的）
HEADING_FONTS: frozenset[str] = frozenset()
# 图里面板标签用的字体（Cell 的 Arial / Arial-BoldMT）：这个字体排的短行成堆、散落在图里，不挨着栏的左边缘，也不居中。它排的无编号短行不是标题
LABEL_FONTS: frozenset[str] = frozenset()


def line_font(ln: Line) -> str:
    counts: dict[str, int] = {}
    for sp in ln.spans:
        n = len(sp.text.strip())
        if n:
            counts[sp.font] = counts.get(sp.font, 0) + n
    return max(counts, key=counts.get) if counts else ""


def _in_heading_font(ln: Line) -> bool:
    return bool(HEADING_FONTS) and line_font(ln) in HEADING_FONTS


def detect_heading_fonts(lines: list[Line], body: float) -> frozenset[str]:
    """找出“用来排标题”的字体：不是正文字体、字号不小于正文，这个字体里有至少 4 行是“孤立的短行”——
    单行、不超过 12 个词、首字母大写、不带句末标点，并且上一行或下一行（同一栏里紧挨着的）换成了别的字体（标题夹在正文中间）。
    斜体的整段文字、图注里的零散词，行与行之间字体不变，不算孤立。"""
    use = [l for l in lines if not l.in_figure and l.text.strip()]
    fonts = {id(l): line_font(l) for l in use}
    by_font: dict[str, int] = {}
    for l in use:
        by_font[fonts[id(l)]] = by_font.get(fonts[id(l)], 0) + len(l.text)
    if not by_font:
        return frozenset()
    body_font = max(by_font, key=by_font.get)
    iso: dict[str, int] = {}
    for cls in {l.cls for l in use}:
        col = sorted((l for l in use if l.cls == cls), key=lambda l: l.y0)
        for i, l in enumerate(col):
            f = fonts[id(l)]
            if f == body_font or l.size < 0.9 * body:
                continue
            t = l.text.strip()
            if not (3 <= len(t) <= 90 and (t[:1].isupper() or t[:1].isdigit()) and not t.endswith((".", ",", ";", ":")) and len(t.split()) <= 12 and l.math < 0.3):
                continue
            prev_f = fonts[id(col[i - 1])] if i > 0 and l.y0 - col[i - 1].y1 < 2.0 * l.size else None
            next_f = fonts[id(col[i + 1])] if i + 1 < len(col) and col[i + 1].y0 - l.y1 < 2.0 * l.size else None
            if (prev_f is not None and prev_f != f) or (next_f is not None and next_f != f):
                iso[f] = iso.get(f, 0) + 1
    return frozenset(f for f, n in iso.items() if n >= 4)


_LOOSE_NUMBER = re.compile(r"\s*(?:[A-Z]\.\d{1,2}(?:\.\d{1,2})*\.?|\d{1,2}(?:\.\d{1,2})*\.?|[A-Z]\.)\s+\S")


def detect_label_fonts(lines: list[Line]) -> frozenset[str]:
    """找出“面板标签字体”：不是正文字体，有 ≥8 行短行（≤60 字符、≤8 词），而且其中绝大多数既不贴着正文栏的左边缘、也不居中——
    标题要么顶着栏的左边缘、要么居中，图里的标签（Control、PTPN12、Days Post-infection）散在各处。"""
    use = [l for l in lines if not l.in_figure and l.text.strip()]
    fonts = {id(l): line_font(l) for l in use}
    chars: dict[str, int] = {}
    for l in use:
        chars[fonts[id(l)]] = chars.get(fonts[id(l)], 0) + len(l.text)
    if not chars:
        return frozenset()
    body_font = max(chars, key=chars.get)
    body_lines = [l for l in use if fonts[id(l)] == body_font]
    edges: dict[int, int] = {}
    for l in body_lines:
        if len(l.text) >= 30:
            edges[round(l.x0)] = edges.get(round(l.x0), 0) + 1
    top = max(edges.values()) if edges else 0
    margins = [x for x, n in edges.items() if n >= max(5, 0.1 * top)]
    mid = (min(l.x0 for l in body_lines) + max(l.x1 for l in body_lines)) / 2
    short: dict[str, int] = {}
    ordered: dict[str, int] = {}
    for l in use:
        f = fonts[id(l)]
        t = l.text.strip()
        if f == body_font or len(t) > 60 or len(t.split()) > 8:
            continue
        short[f] = short.get(f, 0) + 1
        if any(abs(l.x0 - m) <= 2.0 for m in margins) or abs((l.x0 + l.x1) / 2 - mid) <= 4.0:
            ordered[f] = ordered.get(f, 0) + 1
    return frozenset(f for f, n in short.items() if n >= 8 and ordered.get(f, 0) < 0.3 * n)


def _label_like(ln: Line) -> bool:
    """面板标签字体排的、或者一行里字号参差（面板字母 “A” + 标签）的无编号短行：不是标题。"""
    t = ln.text
    if sum(1 for w in t.split() if re.fullmatch(r"[-+−]?\d+(?:\.\d+)?", w)) >= 2:
        return True  # 两个以上的纯数字词（“21 22 23Mb time after IR”“A -20 min 5 min 20 min”）：坐标轴刻度，不是标题
    m = re.match(r"\s*(\d+)", t)
    if (NUMBERED_HEAD_RE.match(t) or _LOOSE_NUMBER.match(t)) and not (m and int(m.group(1)) > 30):  # “40 Acq Test”不是第 40 章
        return False
    if LABEL_FONTS and line_font(ln) in LABEL_FONTS:
        return True
    sizes = [sp.size for sp in ln.spans if len(sp.text.strip()) >= 2]
    return len(sizes) >= 2 and max(sizes) > 1.15 * min(sizes)


def _is_heading_line(ln: Line, col_width: float, body: float, prev: "Line | None" = None) -> bool:
    t = ln.text
    if not (2 <= len(t) <= 110):
        return False
    if CAPTION_START_RE.match(t):
        return False
    if _label_like(ln):
        return False
    if ln.spans and all(sp.font == "ocr" for sp in ln.spans) and OCR_SECTION_RE.match(t) and ln.width <= 0.6 * col_width:
        return True  # 扫描页没有字体信息（粗体、字号都没有）：常见章节名单独成行就是标题
    if CN_HEAD_RE.match(t):  # 中文公文/论文的“一、”“（一）”“第一章”：短、不以句号收尾、没占满一栏
        return len(t) <= 36 and ln.width <= 0.7 * col_width and not t.rstrip().endswith(("。", "；", "，", "：", ".", ";", ","))
    numbered_bold = bool(NUMBERED_HEAD_RE.match(t)) and (ln.bold >= 0.85 or ln.size >= body * 1.1) and len(re.findall(r"[A-Za-z]{3,}", t)) >= 2
    if not numbered_bold and any(c in MATH_CHARS or c in "∗=≜" for c in t):
        return False  # 含数学符号的行是公式碎片，不是标题（带编号的整行粗体小节标题除外：“4.1 Global Optimality of pg = pdata”）
    core = re.sub(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-Z]\.)\s+", "", t)  # 编号（“3.3.1 ”）不算进“字母占比”，否则短标题会被当成图内数字
    if not re.search(r"[A-Za-z]{3,}", core) and not re.search(r"[一-鿿]{2,}", core):
        return False  # 一个 3 个字母以上的词都没有（“2 S s T s 2”）：公式碎片
    letters = sum(c.isalpha() for c in core)
    if letters < 0.6 * len(re.sub(r"\s", "", core)) or t[:1].islower():
        return False  # 图内数字/坐标刻度，或上一行的续行
    if prev is not None and prev.bold >= 0.85 and ln.bold >= 0.85 and prev.width >= 0.9 * col_width and abs(prev.size - ln.size) < 0.6:
        return False  # 粗体整宽段落（如摘要）的最后一行
    # 明显大于正文的粗体行（有的模板把标题拉满整栏，字间距撑得很大，宽度接近栏宽）也算短标题
    short = ln.width <= 0.78 * col_width or (ln.size >= body * 1.3 and ln.bold >= 0.85 and len(t) <= 70)
    if NUMBERED_HEAD_RE.match(t) and core.isupper() and ln.width <= 0.95 * col_width and not t.endswith(TERMINAL) and len(t) <= 80:
        return True  # 带编号的全大写标题（不是粗体、字号和正文一样）
    if HEADING_FONTS and (ln.width <= 0.92 * col_width or (NUMBERED_HEAD_RE.match(t) and ln.width <= 1.03 * col_width)) and line_font(ln) in HEADING_FONTS and not t.endswith((".", ",", ";")) and "," not in t and not re.match(r"(?i)\s*(?:key\s*words?|index terms|abbreviations?)\b", t):
        return True  # 标题字体里的短行；关键词行（带逗号）不是标题
    # 参考文献一节常常整节排成小字号（Springer 的 7.5pt），标题也跟着变小：名字对得上、又是粗体，就是标题
    if ln.bold >= 0.85 and short and re.match(r"(?i)^(?:\d+\.?\s*)?(?:references|bibliography|literature cited|参考文献)\s*$", t):
        return True
    # IEEE 等把标题排成小型大写（首字母 10pt、其余 8pt，和正文一样大、不加粗、居中）：独占一行的 REFERENCES 不靠字体判断
    if short and (t.isupper() or t.istitle()) and re.match(r"(?i)^(?:\d+\.?\s*|[IVX]+\.\s*)?(?:references|bibliography|literature cited|参考文献)\s*$", t):
        return True
    if ln.bold >= 0.85 and short and len(t) <= 90 and ln.size >= body * 0.97:
        return True
    if ln.size >= body * 1.18 and short and not t.endswith((".", ",")):
        return True
    if NUMBERED_HEAD_RE.match(t) and short and not t.endswith(TERMINAL) and len(t) <= 80:
        return True
    # 整行粗体、带编号的小节标题，用小写词写的长标题（“5.1 Alternate layer weighting schemes”“5.3 What … representations?”）
    if (
        NUMBERED_HEAD_RE.match(t) and ln.bold >= 0.85 and ln.size >= body * 0.97 and ln.width <= 1.03 * col_width
        and len(t) <= 90 and not t.endswith((".", ",", ";", ":"))
    ):
        return True
    # 带编号、整行都是斜体/粗体、每个实词首字母大写的小节标题，可以更长（占到栏宽的 96%）
    if NUMBERED_HEAD_RE.match(t) and ln.width <= 1.03 * col_width and not t.endswith(TERMINAL) and len(t) <= 90 and max(ln.italic, ln.bold) >= 0.85:
        words = [w for w in t.split()[1:] if w[:1].isalpha() and len(w) > 3]
        if words and sum(w[:1].isupper() for w in words) >= 0.7 * len(words):
            return True
    return False


def _leading_table(lines: list[Line]) -> dict[float, float]:
    """按字号桶估计正文行距：只取同字号相邻行的基线差，样本不足时回退到 1.2×字号。"""
    buckets: dict[float, list[float]] = {}
    for a, b in zip(lines, lines[1:]):
        if a.cls == b.cls and abs(a.size - b.size) < 0.3 and 0 < b.baseline - a.baseline < 2.0 * a.size:
            buckets.setdefault(round(a.size * 2) / 2, []).append(b.baseline - a.baseline)
    return {k: statistics.median(v) for k, v in buckets.items() if len(v) >= 3}


def _leading_for(table: dict[float, float], size: float) -> float:
    key = round(size * 2) / 2
    for k in (key, key - 0.5, key + 0.5):
        if k in table:
            return table[k]
    return 1.2 * size


_CAPS_RUN_IN_RE = re.compile(r"^(?:\d{1,2}\.?\s+)?([A-Z][A-Z0-9 ,\-:&']{10,78}[A-Z0-9])\s+(?=[A-Z][a-z])")


def _run_in(first: Line) -> tuple[str | None, int]:
    """行首的粗体前导（行内小标题）。返回 (标题文字, 标题所占字符数)。"""
    spans = sorted(first.spans, key=lambda s: s.x0)
    lead = ""
    consumed = 0
    for s in spans:
        if not s.bold:
            break
        lead += s.text
        consumed += len(s.text)
    lead_clean = lead.strip()
    rest = first.text[len(lead_clean):].strip() if first.text.startswith(lead_clean) else ""
    if 3 <= len(lead_clean) <= 80 and rest and (lead_clean.rstrip().endswith((".", ":")) or NUMBERED_HEAD_RE.match(lead_clean)) and not CAPTION_START_RE.match(lead_clean):  # 带编号的粗体小标题（“A.2 Importance of γ in Eqn. (1)”）后面直接接正文，可以不带句号
        return lead_clean.rstrip(" .:"), len(lead_clean)
    # 全大写的小标题直接接着正文、不加句号（Nature 系列：“MATERIALS THAT SHOULD NOT EXIST More than 70 years ago …”），字体可能加粗也可能不加粗
    m = _CAPS_RUN_IN_RE.match(first.text)
    if m:
        cand = m.group(1).strip()
        if len(cand.split()) >= 2 and max(len(w) for w in cand.split()) >= 4 and not CAPTION_START_RE.match(cand):
            return cand, len(m.group(1))
    return None, 0


_ALGO_STEP_RE = re.compile(r"^\s*(\d{1,3})\s*:\s*\S")


def _algo_step_ids(lines: list[Line]) -> set[int]:
    """伪代码框里的步骤行（“5: H ← IsMaxVolume(H)”）：行首是 “N:”，并且同一栏里还有编号差 1 的相邻步骤。
    这样普通正文里偶然以 “3: ” 开头的行不会被误认。"""
    cand: dict[tuple[str, int], list[Line]] = {}
    for l in lines:
        m = _ALGO_STEP_RE.match(l.text)
        if m and not l.in_figure:
            cand.setdefault((l.cls, int(m.group(1))), []).append(l)
    out: set[int] = set()
    for (cls, n), ls in cand.items():
        if (cls, n - 1) in cand or (cls, n + 1) in cand:
            out.update(id(l) for l in ls)
    return out


def _merge_algo_fragments(lines: list[Line], step_ids: set[int]) -> list[Line]:
    """步骤行里的上标（H∗、y_r^(∗)、T∗ 里的星号）是小字号的片段，抽出来成了单独的小行，后面被当成独立的“公式块”。
    小字号、在某个步骤行的基线上方不到 0.8 个字号、横向落在这一行范围内的片段，并回那一行。"""
    if not step_ids:
        return lines
    steps = [l for l in lines if id(l) in step_ids]
    merged: dict[int, list[Span]] = {}
    gone: set[int] = set()
    for f in lines:
        if id(f) in step_ids or f.in_figure or f.size > 0.85 * steps[0].size:
            continue
        best = None
        for st in steps:
            rise = st.baseline - f.baseline
            if st.cls == f.cls and -0.3 * st.size <= rise <= 0.8 * st.size and st.x0 - 2 <= f.x0 and f.x1 <= st.x1 + 12:
                if best is None or rise < best[0]:
                    best = (rise, st)
        if best is not None:
            merged.setdefault(id(best[1]), []).extend(f.spans)
            gone.add(id(f))
    out: list[Line] = []
    for l in lines:
        if id(l) in gone:
            continue
        if id(l) in merged:
            nl_ = _line_from(list(l.spans) + merged[id(l)])
            nl_.cls, nl_.region, nl_.role, nl_.in_figure = l.cls, l.region, l.role, l.in_figure
            step_ids.discard(id(l))
            step_ids.add(id(nl_))
            out.append(nl_)
        else:
            out.append(l)
    return out


def _equation_blocks(lines: list[Line], page_no: int, body: float, skip: set[int] | None = None) -> tuple[list[Line], list[Block]]:
    """以公式编号（(1)、ð1Þ）所在行为锚点，把垂直邻近的数学碎片归并成一个 formula 块。"""
    skip = skip or set()
    anchors = [l for l in lines if id(l) not in skip and not l.in_figure and EQ_NUMBER_RE.search(l.text) and len(l.text) <= 140
               and (l.math >= 0.2 or len(l.text) <= 60)]
    taken: set[int] = set()
    blocks: list[Block] = []
    for a in anchors:
        if id(a) in taken:
            continue
        lead = 1.25 * a.size
        group = [a]
        for l in lines:
            if l is a or id(l) in taken or id(l) in skip or l.in_figure or l.cls != a.cls and a.cls != "F" and l.cls != "F":
                continue
            # 紧挨着的两个编号公式（(33)(34)、(35)(36)）：每一行归给离它最近的编号，不能整块并成一个公式、丢掉前一个编号
            if any(o is not a and id(o) not in taken and o.cls == a.cls and abs(o.baseline - l.baseline) < abs(a.baseline - l.baseline)
                   or (o is l and o is not a) for o in anchors):
                continue
            if not (a.baseline - 2.4 * lead <= l.baseline <= a.baseline + 1.9 * lead):
                continue
            small_fragment = l.size <= 0.85 * a.size and len(l.text) <= 30
            if l.math < 0.2 and len(l.text) > 10 and not small_fragment:
                continue  # 非数学的文字行（段落尾巴）不属于公式
            if l.x1 < a.x0 - 8 or l.x0 > a.x1 + 8:
                continue
            group.append(l)
        if len(group) == 1 and a.math < 0.2 and not re.search(r"[=≤≥<>≈≡∈]", a.text):
            continue  # 单独一行、数学符号很少、也没有等号/不等号：是句末带着“(3)”的正文，不是公式
        for l in group:
            taken.add(id(l))
        group.sort(key=lambda l: (round(l.baseline / 3), l.x0))
        text = clean_text(" ".join(l.text for l in group))
        blk = Block(page_no, min(l.x0 for l in group), min(l.y0 for l in group), max(l.x1 for l in group),
                    max(l.y1 for l in group), text, a.size, 0.0, len(group), a.cls, "formula", lines=group)
        m = EQ_NUMBER_RE.search(a.text)
        blk.eq_number = m.group(1) if m else None
        blocks.append(blk)
    return [l for l in lines if id(l) not in taken], blocks


_ENDS_WITH_LOCATOR = re.compile(r"(?:doi:\s*\S+|https?://\S+|\d{1,5}\s*[–-]\s*\d{1,5})$", re.I)  # 条目以 DOI、网址、页码结尾（Frontiers 的参考文献不以句号收尾）


def _next_word_fits(prev: Line, ln: Line, right: float) -> bool:
    """下一行的第一个词能不能放进上一行剩下的空白里（按平均字宽估算）。"""
    words = ln.text.split()
    if not words or not prev.text:
        return True
    avg = prev.width / max(1, len(prev.text))
    return prev.x1 + (len(words[0]) + 1) * avg <= right + 1.0


def segment_paragraphs(lines: list[Line], page_no: int, body: float, *, in_references: bool = False) -> list[Block]:
    """按阅读顺序排好的行切段落。公式行成组为 formula；标题行独立；图注独立。"""
    if not lines:
        return []
    step_ids = _algo_step_ids(lines)
    lines = _merge_algo_fragments(lines, step_ids)
    lines, eq_blocks = _equation_blocks(lines, page_no, body, step_ids)
    col_extent: dict[str, tuple[float, float]] = {}
    for cls in ("F", "L", "M", "R", "S", "W"):
        grp = [l for l in lines if l.cls == cls and not l.in_figure]
        if grp:
            col_extent[cls] = (min(l.x0 for l in grp), max(l.x1 for l in grp))
    leading_tab = _leading_table(lines)
    page_text_w = (max(l.x1 for l in lines) - min(l.x0 for l in lines)) if lines else 1.0
    col_w_of = {id(l): max(1.0, col_extent[l.cls][1] - col_extent[l.cls][0], 0.45 * page_text_w)
                for l in lines if l.cls in col_extent}

    blocks: list[Block] = []
    cur: list[Line] = []

    def finish() -> None:
        nonlocal cur
        if not cur:
            return
        blk = _make_block(cur, page_no, body)
        if id(cur[0]) in step_ids and blk.kind == "formula":
            blk.kind, blk.eq_number = "paragraph", None  # 伪代码的步骤行里数学符号再多，也是算法文字，不是显示公式
        if blk.role == "heading" and blk.kind == "paragraph" and len(cur) <= 3 and len(blk.text) <= 160:
            blk.heading_hint = True  # 视觉划分说这是小节标题（哪怕字体、大小规则认不出来）
            blk.run_in = None
        elif blk.role in ("caption", "figure", "table", "formula"):
            pass  # 视觉划分说这不是标题：规则不再猜
        elif (
            len(cur) == 2 and blk.kind == "paragraph" and (blk.bold >= 0.85 or all(_in_heading_font(l_) for l_ in cur)) and NUMBERED_HEAD_RE.match(cur[0].text) and blk.size >= body * 1.0
            and len(blk.text) <= 120 and not blk.text.rstrip().endswith(TERMINAL) and abs(cur[0].size - cur[1].size) < 0.5
        ):
            blk.heading_hint = True  # 折成两行的带编号粗体标题（“3. Factorizing Convolutions with Large Filter Size”）
            blk.run_in = None
        elif len(cur) == 1 and blk.kind == "paragraph" and _is_heading_line(cur[0], col_w_of.get(id(cur[0]), 1e9), body):
            h = cur[0]
            # 表头的各个格子（粗体大写的短词）和别的格子排在同一条基线上：真正的标题独占一行
            side_by_side = any(
                o is not h and o.region == h.region and abs(o.baseline - h.baseline) < 0.35 * h.size and (o.x0 > h.x1 + 3 or o.x1 < h.x0 - 3)
                and ((o.cls == h.cls and not o.in_figure) or (o.in_figure and o.cls in (h.cls, "F") and o.bold >= 0.85 and o.width <= 0.4 * page_text_w))
                for o in lines
            )
            if not side_by_side:
                blk.heading_hint = True
                blk.run_in = None
        blocks.append(blk)
        cur = []

    numbered_refs = in_references and sum(1 for l in lines if REF_START_RE.match(l.text)) >= 3
    prev: Line | None = None
    prev_prev: Line | None = None
    for ln in lines:
        if ln.in_figure:
            finish()
            prev = prev_prev = None
            continue
        left, right = col_extent.get(ln.cls, (ln.x0, ln.x1))
        col_w = max(1.0, right - left, 0.45 * page_text_w)
        brk = False
        if prev is None or cur == []:
            brk = True
        else:
            dy = ln.baseline - prev.baseline
            lead = _leading_for(leading_tab, ln.size)
            is_math, prev_math = ln.math >= 0.45 and not _prosey(ln), prev.math >= 0.45 and not _prosey(prev)
            if ln.region != prev.region:
                brk = True  # 视觉划分里不同的区域永远是不同的块
            elif id(ln) in step_ids or (id(prev) in step_ids and is_math != prev_math and False):
                brk = True  # 伪代码：每个“N:”步骤一块
            elif ln.region >= 0 and ln.role == "formula":
                brk = False  # 一个公式区域（分子、分母、上下限各占一行）整块是一个公式
            elif ln.cls != prev.cls:
                brk = True
            elif abs(dy) < 0.3 * lead and ln.x0 > prev.x1 + 1:
                brk = True  # 同一基线上并排的两段文字（页眉左右两个标签、并排的图注），不是同一段落的续行
            elif (
                len(cur) == 1 and _in_heading_font(prev) and _in_heading_font(ln) and abs(prev.size - ln.size) < 0.5
                and 0 < dy < 1.6 * lead and NUMBERED_HEAD_RE.match(prev.text) and not NUMBERED_HEAD_RE.match(ln.text) and len(ln.text.split()) <= 4 and not prev.text.rstrip().endswith(TERMINAL)
            ):
                brk = False  # 标题字体的两行标题（“3.3 Upsampling Is (Fractionally Strided)” / “Convolution”）
            elif (
                len(cur) == 1 and prev.text.endswith("-") and prev.bold >= 0.85 and ln.bold >= 0.85 and abs(prev.size - ln.size) < 0.5
                and 0 < dy < 1.6 * lead and NUMBERED_HEAD_RE.match(prev.text) and len(ln.text.split()) <= 3
            ):
                brk = False  # 标题折行后半截（“3.2. … Asymmetric Convo-” / “lutions”）：并回标题
            elif _is_heading_line(ln, col_w, body, prev) or _is_heading_line(prev, col_w, body, prev_prev):
                brk = True
            elif CAPTION_START_RE.match(ln.text):
                brk = True
            elif in_references and cur and ln.x0 - cur[0].x0 > 0.5 * ln.size and not REF_START_RE.match(ln.text) and dy < 1.6 * lead:
                brk = False  # 悬挂缩进：参考文献第一行顶格、折下来的行缩进，缩进的行一定是上一条的续行
            elif numbered_refs:
                # 编号的参考文献（[1] … / 1. …）：只有以编号开头的行才是新条目，其余都是上一条折下来的续行
                # （“Proc. IEEE Int.” 换行后 “Conf. Robot. Automat.” 以大写开头、上一行以句号结尾，按普通段落规则会被切开）
                brk = bool(REF_START_RE.match(ln.text)) or dy > 2.6 * lead
            elif (
                in_references and AUTHOR_START_RE.match(ln.text) and (prev.text.rstrip().endswith(".") or _ENDS_WITH_LOCATOR.search(prev.text.rstrip()) or prev.x0 - ln.x0 > 0.5 * ln.size)
                and _YEAR_IN_ENTRY.search(" ".join(l.text for l in cur))
            ):
                brk = True  # 没有悬挂缩进的作者-年份条目：上一条已经写到句号、里面出现过年份，这一行又以“姓, 缩写.”开头，就是新的一条
            elif is_math != prev_math:
                brk = True
            elif is_math and prev_math:
                brk = dy > 1.9 * lead  # 分式/上下标的多行碎片属于同一个公式
            elif dy > 1.55 * lead or dy < -0.2 * lead:
                brk = True
            elif abs(ln.size - prev.size) / max(prev.size, 1) > 0.18:
                brk = True
            elif ln.size < 0.9 * prev.size and not is_math:
                brk = True  # 字号明显变小的一行是另一块内容（摘要后面紧跟的小号字声明、脚注），同一段的续行字号不会变
            elif in_references and REF_START_RE.match(ln.text):
                brk = True
            elif ln.x0 - min(l.x0 for l in cur) > 0.8 * ln.size and (prev.text.endswith(TERMINAL) or prev.x1 < max(l.x1 for l in cur) - 3 * prev.size):
                # 首行缩进：以“这一段自己的左边”为基准，不是整页最左边——比正文缩进一块的摘要、引文块不能每一行都算新段落
                brk = True
            elif prev.x1 < right - 4.5 * prev.size and prev.text.endswith(TERMINAL) and ln.text[:1].isupper() and _next_word_fits(prev, ln, right):
                brk = True  # 上一行明显未写满且句末（下一行的第一个词放得下，没放上来才说明是段尾；右边不齐的文字里，放不下的词只好折到下一行）
            elif (
                (prev.x1 - prev.x0) < 0.72 * (ln.x1 - ln.x0) and (ln.x1 - ln.x0) > 150
                and abs((prev.x0 + prev.x1) / 2 - (ln.x0 + ln.x1) / 2) < 8 and prev.x0 - ln.x0 > 0.06 * (ln.x1 - ln.x0)
            ):
                brk = True  # 居中的短行（单位、收稿日期）后面接着一整段通栏文字（摘要）：不是同一段。段落内部的短行是靠左的末行，中心对不上
        if brk:
            finish()
        cur.append(ln)
        prev_prev, prev = prev, ln
    finish()
    # 把公式块按位置插回阅读顺序
    for eb in sorted(eq_blocks, key=lambda b: b.y0):
        idx = next((i for i, b in enumerate(blocks) if (b.col == eb.col or b.col == "F" or eb.col == "F") and b.y0 > eb.y0 + 0.5 * (eb.y1 - eb.y0) * 0), None)
        if idx is None:
            blocks.append(eb)
        else:
            blocks.insert(idx, eb)
    return blocks


_PROSE_WORD = re.compile(r"[A-Za-z]{3,}")


def _prosey(ln: Line) -> bool:
    """这一行虽然数学符号占大半，但开头或中间夹着连续的英文正文（“points are defined as B ≜ {…”）：是一句话里的行内公式，
    不是独立公式。不这样判断，整行会被切成“公式块”，换行后剩下的 “1}, for simplicity…” 就成了下一段的开头。"""
    words = _PROSE_WORD.findall(" ".join(s.text for s in ln.spans if not s.math))
    return len(words) >= 3


def split_side_captions(lines: list[Line], page_no: int, body: float) -> tuple[list[Line], list[Block]]:
    """文字绕排的图（wrapfigure）：图和图注排在页面一侧，正文排在另一侧，同一基线上的左右两段被合成了一行
    （“…inter- Figure 2: PaLM 540B uses chain-of…”），图注和正文就这样缠在一起。
    找到行里“左边有正文、右边隔着大空隙才开始图注”的片段，把这一栏（往下连续的行里 x 对得上的片段）整体抽出来单独成图注块，
    左边的正文留在原行里。返回 (剩下的行, 抽出的图注块)。"""
    ordered = sorted(lines, key=lambda l: (l.y0, l.x0))
    removed: dict[int, list[Span]] = {}  # id(行) → 要抽走的片段
    caps: list[list[Line]] = []
    claimed: set[int] = set()
    for i, ln in enumerate(ordered):
        if id(ln) in claimed:
            continue
        sp = sorted(ln.spans, key=lambda s: s.x0)
        start = None
        for k in range(1, len(sp)):
            if is_caption(" ".join(x.text for x in sp[k:k + 4])) and sp[k].x0 - sp[k - 1].x1 >= 0.7 * ln.size and len(" ".join(s.text for s in sp[:k]).split()) >= 3:
                start = k
                break
        if start is None:
            continue
        cx = sp[start].x0
        col: list[Line] = []
        last_y1 = ln.y0
        for l2 in ordered[i:]:
            if l2 is not ln and (l2.y0 - last_y1 > 0.9 * max(l2.size, 8) or id(l2) in claimed):
                break
            take = [s for s in l2.spans if s.x0 >= cx - 3 and (l2 is not ln or s.x0 >= cx - 3)]
            left = [s for s in l2.spans if s.x0 < cx - 3]
            if not take or (l2 is not ln and not left):
                if l2 is not ln:
                    break
                continue
            removed.setdefault(id(l2), []).extend(take)
            col.append(_line_from(take))
            last_y1 = l2.y1
            claimed.add(id(l2))
        if len(col) >= 1:
            caps.append(col)
    if not removed:
        return lines, []
    out: list[Line] = []
    for ln in lines:
        gone = removed.get(id(ln))
        if not gone:
            out.append(ln)
            continue
        keep = [s for s in ln.spans if not any(s is g for g in gone)]
        if keep:
            out.append(_line_from(keep))
    blocks = [_make_block(c, page_no, body) for c in caps]
    for b in blocks:
        b.kind = "caption"
    return out, blocks


SOFT_HYPHEN = ""  # 行尾折行的标记：待定“去掉连字符”还是“保留连字符”


def _make_block(lines: list[Line], page_no: int, body: float) -> Block:
    text_parts: list[str] = []
    for ln in lines:
        t = ln.text
        if not text_parts:
            text_parts.append(t)
        elif text_parts[-1].endswith("-") and len(text_parts[-1]) > 1 and text_parts[-1][-2].isalpha() and t[:1].islower():
            last = text_parts[-1].split(" ")[-1]
            # 行尾的 “chain-of-” 本身已经是带连字符的复合词：连字符是词的一部分，保留；“param-” 这种断词才去掉
            # 断词还是复合词，行尾的连字符本身分不出来（task-/specific 是复合词，classi-/fication 是断词）：先放一个软连字符，
            # 整篇解析完以后再按全文的用词决定（pdf_parser._resolve_soft_hyphens）
            text_parts[-1] = text_parts[-1] + t if "-" in last[:-1] else text_parts[-1][:-1] + SOFT_HYPHEN + t
        elif is_cjk(text_parts[-1][-1:]) and is_cjk(t[:1]):
            text_parts[-1] += t
        else:
            text_parts.append(t)
    text = clean_text(" ".join(text_parts))
    chars = max(1, sum(len(l.text) for l in lines))
    size = sum(l.size * len(l.text) for l in lines) / chars
    bold = sum(l.bold * len(l.text) for l in lines) / chars
    math = sum(l.math * len(l.text) for l in lines) / chars
    blk = Block(
        page_no, min(l.x0 for l in lines), min(l.y0 for l in lines), max(l.x1 for l in lines), max(l.y1 for l in lines),
        text, size, bold, len(lines), lines[0].cls, lines=lines, role=lines[0].role,
    )
    if lines[0].role == "formula" and len(text) <= 500:
        blk.kind = "formula"  # 视觉划分说这是一个公式区域：整块当公式，不管字体判断
        m = EQ_NUMBER_RE.search(text)
        if m:
            blk.eq_number = m.group(1)
    elif lines[0].role == "caption":
        blk.kind = "caption"
    elif math >= 0.45 and not (len(lines) == 1 and _prosey(lines[0])):
        blk.kind = "formula"
        m = EQ_NUMBER_RE.search(text)
        if m:
            blk.eq_number = m.group(1)
    elif CAPTION_START_RE.match(text):
        blk.kind = "caption"
    if blk.kind == "paragraph":
        lead, n = _run_in(lines[0])
        t0 = lines[0].text.strip()
        if (
            not lead and len(lines) >= 3 and not t0.endswith("-") and t0.isupper() and 10 <= len(t0) <= 80 and len(t0.split()) >= 2
            and not t0.endswith((".", ",", ";", ":")) and not CAPTION_START_RE.match(t0) and lines[1].text[:1].isupper()
        ):
            lead, n = t0, len(lines[0].text.rstrip())  # 全大写的小标题独占段落的第一行，正文接在下一行（Nature Materials 预印本）
        if lead:
            blk.run_in = lead
            rest = lines[0].text[n:].strip()
            body_text = " ".join([rest, *[l.text for l in lines[1:]]]).strip()
            blk.text = clean_text(body_text) if body_text else blk.text
    return blk
