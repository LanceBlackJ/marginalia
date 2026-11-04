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
    r"(math|symbol|stix|cmmi|cmsy|cmex|cmmib|cmbsy|msam|msbm|mnsymbol|extension|mtextra|euclid|"
    r"lmmath|latinmodern-math|cambria\s*math)|-(?:regular|italic|it)\d+(?:\+\d+)?$",
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


def is_caption(text: str) -> bool:
    """图表标题：`Figure 1:` / `Table 2.` / `Fig. 1 | …` / `Fig. 3 Overview…`；排除正文里的 “Table 1 shows …”。"""
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
NUMBERED_HEAD_RE = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-Z]\.)\s+[A-Z一-鿿]")
REF_START_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s+\S")
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


def clean_text(text: str) -> str:
    text = _LIG_RE.sub(lambda m: LIGATURES[m.group(0)], text)
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


def extract_spans(page: pymupdf.Page) -> list[Span]:
    data = page.get_text("dict")
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
                x0, y0, x1, y1 = sp["bbox"]
                font = sp["font"]
                if text.strip() == "I" and (x1 - x0) < 0.45 * float(sp["size"]) and MATH_FONT_RE.search(font):
                    continue  # 自定义数学字体里的装饰字形（竖线/覆盖符），不是文字
                out.append(
                    Span(
                        x0, y0, x1, y1, sp["origin"][1], text, float(sp["size"]),
                        bool(sp["flags"] & 16) or "bold" in font.lower() or "semibold" in font.lower()
                        or bool(re.search(r"-(bd|bol)\b", font.lower())),
                        bool(sp["flags"] & 2) or bool(re.search(r"italic|-it\d*$|oblique", font.lower())),
                        font, bool(MATH_FONT_RE.search(font)) and not set(text.strip()) <= _LIG_CHARS,  # 单独排在子集字体里的连字（ﬁ）不是数学
                    )
                )
    return out


def body_font_size(all_spans: list[list[Span]]) -> float:
    counter: dict[float, int] = {}
    for spans in all_spans:
        for s in spans:
            k = round(s.size * 2) / 2
            counter[k] = counter.get(k, 0) + len(s.text)
    return max(counter, key=counter.get) if counter else 10.0


# ---------------------------------------------------------------- 栏检测
def detect_gutter(spans: list[Span], page_h: float | None = None) -> tuple[float, float] | None:
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
    narrow = [s for s in spans if (s.x1 - s.x0) < 0.55 * span_w]
    if len(narrow) < 8:
        return None
    mid = (left + right) / 2
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
    lo, hi = int(max(0, (mid - left) - 0.14 * span_w)), int(min(nbins, (mid - left) + 0.14 * span_w))
    best: tuple[int, int] | None = None
    i = lo
    while i < hi:
        if cover[i] <= thresh:
            j = i
            while j < hi and cover[j] <= thresh:
                j += 1
            if j - i >= 5 and (best is None or j - i > best[1] - best[0]):
                best = (i, j)
            i = j
        else:
            i += 1
    if best is None:
        return None
    g0, g1 = left + best[0], left + best[1]
    n_left = sum(1 for sp in narrow if sp.x1 <= g0 + 2)
    n_right = sum(1 for sp in narrow if sp.x0 >= g1 - 2)
    if n_left < max(6, 0.25 * len(narrow)) or n_right < max(6, 0.25 * len(narrow)):
        return None
    return (g0, g1)


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


def _line_from(spans: list[Span]) -> Line:
    spans = sorted(spans, key=lambda s: s.x0)
    main = max(spans, key=lambda s: (s.size, len(s.text)))
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


def _cluster(group: list[Span]) -> list[list[Span]]:
    """按基线（或垂直范围大幅重叠）把片段聚成视觉行。"""
    group = sorted(group, key=lambda s: (s.baseline, s.x0))
    out: list[list[Span]] = []
    cur: list[Span] = []
    ref_base = ref_size = 0.0
    for sp in group:
        same = False
        if cur:
            same = abs(sp.baseline - ref_base) <= 0.42 * max(ref_size, sp.size)
            if not same:
                top = min(c.y0 for c in cur)
                bot = max(c.y1 for c in cur)
                ov = min(bot, sp.y1) - max(top, sp.y0)
                # 行内的大号求和号等会把基线抬高 6 左右（字号 8）：竖向大幅重叠时容忍到 0.85 倍字号；
                # 相邻两行的基线距至少是 1.1 倍字号，而且几乎不重叠，所以不会把两行并成一行
                same = ov >= 0.65 * min(bot - top, sp.y1 - sp.y0) and abs(sp.baseline - ref_base) <= 0.85 * max(ref_size, sp.size)
        if same:
            cur.append(sp)
            if sp.size >= ref_size:
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
    left = [s for s in group if s.x1 <= g0 + 2]
    right = [s for s in group if s.x0 >= g1 - 2]
    mid = [s for s in group if s not in left and s not in right]
    if mid:
        return [("F", group)]  # 有片段跨过栏沟：整宽行（图注、作者行、通栏标题）
    if left and right:
        size = max(s.size for s in group)
        gap = min(s.x0 for s in right) - max(s.x1 for s in left)
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


def build_lines(spans: list[Span], gutter: tuple[float, float] | None, body: float, page_h: float | None = None) -> list[Line]:
    # 首字下沉：单个大号字母，并入其右侧缩进行的开头
    caps = [s for s in spans if len(s.text.strip()) == 1 and s.text.strip().isalpha() and s.size >= 2.2 * body]
    spans = [s for s in spans if s not in caps]
    lines: list[Line] = []
    for group in _cluster(spans):
        in_band = bool(page_h) and (max(s.y1 for s in group) < 0.085 * page_h or min(s.y0 for s in group) > 0.915 * page_h)
        groups = _split_wide_gaps(group) if in_band else [group]  # 页眉里左右两个标签不应粘成一行
        for g in groups:
            for cls, part in _split_by_gutter(g, gutter):
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

    def flush() -> None:
        ordered.extend(sorted(left, key=lambda l: (l.y0, l.x0)))
        ordered.extend(sorted(right, key=lambda l: (l.y0, l.x0)))
        left.clear()
        right.clear()

    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        if ln.cls == "F":
            flush()
            ordered.append(ln)
        elif ln.cls == "L":
            left.append(ln)
        else:
            right.append(ln)
    flush()
    return ordered


# ---------------------------------------------------------------- 分段
def _is_heading_line(ln: Line, col_width: float, body: float, prev: "Line | None" = None) -> bool:
    t = ln.text
    if not (2 <= len(t) <= 110):
        return False
    if CAPTION_START_RE.match(t):
        return False
    letters = sum(c.isalpha() for c in t)
    if letters < 0.6 * len(t.replace(" ", "")) or t[:1].islower():
        return False  # 图内数字/坐标刻度，或上一行的续行
    if prev is not None and prev.bold >= 0.85 and ln.bold >= 0.85 and prev.width >= 0.9 * col_width and abs(prev.size - ln.size) < 0.6:
        return False  # 粗体整宽段落（如摘要）的最后一行
    short = ln.width <= 0.78 * col_width
    # 参考文献一节常常整节排成小字号（Springer 的 7.5pt），标题也跟着变小：名字对得上、又是粗体，就是标题
    if ln.bold >= 0.85 and short and re.match(r"(?i)^(?:\d+\.?\s*)?(?:references|bibliography|literature cited|参考文献)\s*$", t):
        return True
    if ln.bold >= 0.85 and short and len(t) <= 90 and ln.size >= body * 0.97:
        return True
    if ln.size >= body * 1.18 and short and not t.endswith((".", ",")):
        return True
    if NUMBERED_HEAD_RE.match(t) and short and not t.endswith(TERMINAL) and len(t) <= 80:
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
    if 3 <= len(lead_clean) <= 80 and rest and lead_clean.rstrip().endswith((".", ":")) and not CAPTION_START_RE.match(lead_clean):
        return lead_clean.rstrip(" .:"), len(lead_clean)
    return None, 0


def _equation_blocks(lines: list[Line], page_no: int, body: float) -> tuple[list[Line], list[Block]]:
    """以公式编号（(1)、ð1Þ）所在行为锚点，把垂直邻近的数学碎片归并成一个 formula 块。"""
    anchors = [l for l in lines if not l.in_figure and EQ_NUMBER_RE.search(l.text) and len(l.text) <= 140
               and (l.math >= 0.2 or len(l.text) <= 60)]
    taken: set[int] = set()
    blocks: list[Block] = []
    for a in anchors:
        if id(a) in taken:
            continue
        lead = 1.25 * a.size
        group = [a]
        for l in lines:
            if l is a or id(l) in taken or l.in_figure or l.cls != a.cls and a.cls != "F" and l.cls != "F":
                continue
            if not (a.baseline - 2.4 * lead <= l.baseline <= a.baseline + 1.9 * lead):
                continue
            small_fragment = l.size <= 0.85 * a.size and len(l.text) <= 30
            if l.math < 0.2 and len(l.text) > 10 and not small_fragment:
                continue  # 非数学的文字行（段落尾巴）不属于公式
            if l.x1 < a.x0 - 8 or l.x0 > a.x1 + 8:
                continue
            group.append(l)
        if len(group) == 1 and a.math < 0.2:
            continue
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


def segment_paragraphs(lines: list[Line], page_no: int, body: float, *, in_references: bool = False) -> list[Block]:
    """按阅读顺序排好的行切段落。公式行成组为 formula；标题行独立；图注独立。"""
    if not lines:
        return []
    lines, eq_blocks = _equation_blocks(lines, page_no, body)
    col_extent: dict[str, tuple[float, float]] = {}
    for cls in ("F", "L", "R"):
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
        if len(cur) == 1 and blk.kind == "paragraph" and _is_heading_line(cur[0], col_w_of.get(id(cur[0]), 1e9), body):
            blk.heading_hint = True
            blk.run_in = None
        blocks.append(blk)
        cur = []

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
            is_math, prev_math = ln.math >= 0.45, prev.math >= 0.45
            if ln.cls != prev.cls:
                brk = True
            elif abs(dy) < 0.3 * lead and ln.x0 > prev.x1 + 1:
                brk = True  # 同一基线上并排的两段文字（页眉左右两个标签、并排的图注），不是同一段落的续行
            elif _is_heading_line(ln, col_w, body, prev) or _is_heading_line(prev, col_w, body, prev_prev):
                brk = True
            elif CAPTION_START_RE.match(ln.text):
                brk = True
            elif is_math != prev_math:
                brk = True
            elif is_math and prev_math:
                brk = dy > 1.9 * lead  # 分式/上下标的多行碎片属于同一个公式
            elif dy > 1.55 * lead or dy < -0.2 * lead:
                brk = True
            elif abs(ln.size - prev.size) / max(prev.size, 1) > 0.18:
                brk = True
            elif in_references and REF_START_RE.match(ln.text):
                brk = True
            elif ln.x0 - min(l.x0 for l in cur) > 0.8 * ln.size and (prev.text.endswith(TERMINAL) or prev.x1 < max(l.x1 for l in cur) - 3 * prev.size):
                # 首行缩进：以“这一段自己的左边”为基准，不是整页最左边——比正文缩进一块的摘要、引文块不能每一行都算新段落
                brk = True
            elif prev.x1 < right - 4.5 * prev.size and prev.text.endswith(TERMINAL) and ln.text[:1].isupper():
                brk = True  # 上一行明显未写满且句末
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


def _make_block(lines: list[Line], page_no: int, body: float) -> Block:
    text_parts: list[str] = []
    for ln in lines:
        t = ln.text
        if not text_parts:
            text_parts.append(t)
        elif text_parts[-1].endswith("-") and len(text_parts[-1]) > 1 and text_parts[-1][-2].isalpha() and t[:1].islower():
            text_parts[-1] = text_parts[-1][:-1] + t
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
        text, size, bold, len(lines), lines[0].cls, lines=lines,
    )
    if math >= 0.45:
        blk.kind = "formula"
        m = EQ_NUMBER_RE.search(text)
        if m:
            blk.eq_number = m.group(1)
    elif CAPTION_START_RE.match(text):
        blk.kind = "caption"
    if blk.kind == "paragraph":
        lead, n = _run_in(lines[0])
        if lead:
            blk.run_in = lead
            rest = lines[0].text[n:].strip()
            body_text = " ".join([rest, *[l.text for l in lines[1:]]]).strip()
            blk.text = clean_text(body_text) if body_text else blk.text
    return blk
