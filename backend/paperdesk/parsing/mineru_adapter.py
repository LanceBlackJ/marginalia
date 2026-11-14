"""把 MinerU 的 middle_json 转成 ParsedDoc（与规则解析的产物同形，下游入库、阅读器、重新解析映射都不用改）。

MinerU 的块 bbox 是 0~1 的归一化坐标；这里乘以页面尺寸（PDF 点，显示方向，左上原点）。
块类型：doc_title / paragraph_title / text / ref_text / equation / image / chart / table / code，
以及要丢弃的 header / footer / page_number / aside_text。
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from . import inline_math, reflow
from .metadata import extract_metadata
from .pdf_parser import (
    ParsedDoc, ParsedEquation, ParsedFigure, ParsedPassage, ParsedSection, PdfError, detect_language,
)

DROP = {"header", "footer", "page_number", "aside_text", "page_footnote_noise"}
FIG_LABEL = re.compile(
    r"(?i)^\s*((?:extended data |supplementary )?(?:fig(?:ure)?\.?|table|tab\.|algorithm|图|表)\s*[\dA-Z一二三四五六七八九十]+(?:\.\d+)?)")
REF_HEAD = re.compile(r"(?i)^\s*(?:\d+[.\s]*|[IVX]+[.\s]+)?(references?|bibliography|参考文献|literature cited)\s*$")
REF_START = re.compile(r"^\s*(?:\[\d+\]|\d+[.)]\s|\(\d+\))")
TAG_RE = re.compile(r"\\tag\*?\{([^}]*)\}")


def _safe_equation_latex(text: str) -> str | None:
    """MinerU 的公式只在明显通过结构闸门时进入“已识别”缓存。

    文字层/中间 JSON 偶尔会把 `tanh` 拆成 `tan h`、把 10 拆成 `1 0`；这类结果必须交给带截图的公式识别任务，不能标成 high。
    """
    s = re.sub(r"\s+", " ", text or "").strip()
    if not s or "?" in s or re.search(r"\\(?:tan|sin|cos|log)\s+[A-Za-z]", s):
        return None
    if re.search(r"(?<![A-Za-z])\d\s+\d(?![A-Za-z])", s):
        return None
    depth = 0
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return None
    return s if depth == 0 else None
AFFIL_RE = re.compile(
    r"(?i)^\s*(?:\d+\s*[A-Z].{0,120}?(?:universit|institut|college|department|school|laborator|center|centre|academy|hospital|company|inc\.)|[∗*†‡]|check for updates|received\b|accepted\b|published\b|correspondence|e-?mail|"
    r"\S+@\S+|digital object identifier|©|copyright)")
KEYWORDS_RE = re.compile(r"(?i)^\s*(?:index terms|key\s*words?|关键词)")
ROMAN = re.compile(r"^[IVX]+\.\s")
NUM_HEAD = re.compile(r"^(\d+(?:\.\d+){0,4})[.\s]")
LETTER_HEAD = re.compile(r"^[A-Z]\.\s")
CJK_NUM = re.compile(r"^[一二三四五六七八九十]+、")
CJK_SUB = re.compile(r"^[（(][一二三四五六七八九十]+[）)]")


CAPTION_HEAD = re.compile(r"(?i)^\s*(?:supplementary\s+)?(?:tables?|tab\.|fig(?:ure)?s?\.?|表|图)\s*S?\d+")


def _plain(spans, math: list[dict] | None = None) -> str:
    """spans → 文字；行内公式直接把 LaTeX 放进文字里，同时登记到 math（src 与 tex 相同，阅读器按 src 替换渲染）。"""
    if isinstance(spans, str):
        return spans
    out: list[str] = []
    for s in spans or []:
        if isinstance(s, str):
            out.append(s)
            continue
        t = s.get("type")
        c = s.get("content")
        if t == "equation_inline":
            tex = inline_math.normalize_tex(c or "")
            if tex:
                out.append(tex)
                if math is not None and not any(m["src"] == tex for m in math):
                    math.append({"src": tex, "tex": tex})
        elif t == "hyperlink":
            out.append(_plain(c, math) if c else (s.get("url") or ""))
        elif isinstance(c, list):
            out.append(_plain(c, math))
        elif isinstance(c, str):
            out.append(c)
    return "".join(out)


def _clean(t: str) -> str:
    t = inline_math.normalize_math_text(t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", t)).strip()


def _cell_clean(t: str) -> str:
    """清理表格单元格但保留 HTML/视觉排版中的换行。"""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in re.split(r"\r?\n", t)]
    return "\n".join(line for line in lines if line)


def _head_space(t: str) -> str:
    """表头里换行的样本数：“Crime(829)” → “Crime (829)”（只在表头里做，正文里的 f(2) 不动）。"""
    return re.sub(r"(?<=[A-Za-z])\((?=[\d,.]+\))", " (", t)


class _Grid(HTMLParser):
    """把 MinerU 的 HTML 表格摊成二维格子：colspan/rowspan 占的格子留空，这样表头和数据列对得上。"""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._pend: dict[int, int] = {}  # 列 -> 还要往下占几行
        self._cell = None
        self._span = (1, 1)
        self.spans: list[tuple[int, int, int, int]] = []  # (行, 列, 跨几行, 跨几列)：只记真正合并了的格子

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("sup", "sub") and self._cell is not None:
            self._cell.append(("^{" if tag == "sup" else "_{"))  # 上标、下标：保留成 ^{3} 的写法，前端把参考文献角标渲染成可点的上标
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")
        elif tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            def num(k):
                try:
                    return max(1, min(40, int(a.get(k) or 1)))
                except ValueError:
                    return 1
            self._span = (num("colspan"), num("rowspan"))
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("sup", "sub") and self._cell is not None:
            self._cell.append("}")
        elif tag in ("td", "th") and self._cell is not None and self.rows:
            row = self.rows[-1]
            while self._pend.get(len(row), 0) > 0:
                self._pend[len(row)] -= 1
                row.append("")
            cs, rs = self._span
            if cs > 1 or rs > 1:
                self.spans.append((len(self.rows) - 1, len(row), rs, cs))
            row.append(_cell_clean("".join(self._cell)))
            for k in range(1, cs):
                row.append("")
            if rs > 1:
                for k in range(cs):
                    self._pend[len(row) - cs + k] = rs - 1
            self._cell = None


def _html_rows_spans(h: str) -> tuple[list[list[str]], list[tuple[int, int, int, int]]]:
    """表格 HTML → (二维格子, 合并单元格)。全空的行去掉，合并单元格的行号跟着改。"""
    h = re.sub(r"<eq>(.*?)</eq>", r"\1", h, flags=re.S)
    g = _Grid()
    g.feed(h)
    keep = [i for i, r in enumerate(g.rows) if any(c for c in r)]
    remap = {old: new for new, old in enumerate(keep)}
    spans = [(remap[r], c, rs, cs) for r, c, rs, cs in g.spans if r in remap]
    return [g.rows[i] for i in keep], spans


def _html_rows(h: str) -> list[list[str]]:
    return _html_rows_spans(h)[0]


def _html_text(h: str) -> str:
    rows = _html_rows(h)
    if not rows:
        h = re.sub(r"</t[dh]>", " | ", h)
        h = re.sub(r"</tr>", "\n", h)
        return html.unescape(re.sub(r"<[^>]+>", "", h)).strip()
    return "\n".join(" | ".join(html.unescape(c) for c in r) + " |" for r in rows)


_NUM_CELL = re.compile(r"^[\s\W]*[-+]?\d[\d.,%/·×x±\s()*†‡§-]*$")


def _is_header_row(row: list[str]) -> bool:
    cells = [c for c in row if c.strip()]
    if not cells:
        return False
    nums = sum(1 for c in cells if _NUM_CELL.match(c))
    return nums / len(cells) < 0.25


def grid_data(rows: list[list[str]], notes: str = "", spans: list[tuple[int, int, int, int]] | None = None) -> dict | None:
    """MinerU 的表格 HTML（合并单元格已展开成二维格子）→ 表格数据 {columns, rows, notes, head, spans}。
    开头连续的“不是数字”的行是表头：多行表头（上一行是 “Analytical method” 跨几列，下一行是各列名）按列合并成一个列名（给复制、翻译用）；
    同时保留表头的原始结构 head 和正文里的合并单元格 spans，前端据此画出真正合并的单元格。"""
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        return None
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    if n < 2:
        return None
    k = 0
    # 第一列空着：上一行的表头单元格跨了行（rowspan），这一行还是表头（“(829)”“(1176)”这类样本数也算）
    while k < min(3, len(rows) - 1) and (_is_header_row(rows[k]) or (k >= 1 and not rows[k][0].strip())):
        k += 1
    k = max(1, k)
    head = [[_head_space(c) for c in r] for r in rows[:k]]
    for r in head[:-1]:  # 跨列的上层表头：空格子沿用左边的（第一列除外）
        for j in range(1, n):
            if not r[j] and r[j - 1]:
                r[j] = r[j - 1]
    cols = []
    for j in range(n):
        parts: list[str] = []
        for r in head:
            if r[j] and r[j] not in parts:
                parts.append(r[j])
        cols.append(" · ".join(parts))
    out = {"title": "", "columns": cols, "rows": rows[k:], "notes": notes, "unverified_cells": [], "verified_ratio": None,
           "warnings": [], "unreadable": 0}
    sp = [(r, c, rs, cs) for r, c, rs, cs in (spans or []) if r < len(rows) and c < n]
    if sp:
        covered = {(r + dr, c + dc) for r, c, rs, cs in sp for dr in range(rs) for dc in range(cs) if dr or dc}
        origin = {(r, c): (rs, cs) for r, c, rs, cs in sp}
        out["head"] = [
            [None if (i, j) in covered else {"t": rows[i][j], "rs": min(origin.get((i, j), (1, 1))[0], k - i), "cs": min(origin.get((i, j), (1, 1))[1], n - j)}
             for j in range(n)]
            for i in range(k)
        ]
        out["spans"] = [[r - k, c, min(rs, len(rows) - r), min(cs, n - c)] for r, c, rs, cs in sp if r >= k]
    return out


def _split_footnote(rows: list[list[str]]) -> tuple[list[list[str]], str]:
    """表格最后若有只有一个格子、字很多的行，那是脚注，不是数据。"""
    foot = []
    while len(rows) > 2 and sum(1 for c in rows[-1] if c) == 1 and max(len(c) for c in rows[-1]) > 60:
        foot.insert(0, next(c for c in rows[-1] if c))
        rows = rows[:-1]
    return rows, " ".join(foot)


def _level(title: str, mineru_level: int | None) -> int:
    t = title.strip()
    m = NUM_HEAD.match(t)
    if m:
        return min(4, m.group(1).count(".") + 1)
    if ROMAN.match(t) or CJK_NUM.match(t):
        return 1
    if CJK_SUB.match(t):
        return 2
    if LETTER_HEAD.match(t):
        return 2
    return 1


def _box(b: dict, w: float, h: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = b.get("bbox") or (0, 0, 0, 0)
    return (round(x0 * w, 2), round(y0 * h, 2), round(x1 * w, 2), round(y1 * h, 2))


def _union(boxes):
    boxes = [x for x in boxes if x]
    if not boxes:
        return None
    return (min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))


def build(mid: dict, pages: list[tuple[float, float]], meta_raw: dict | None = None) -> ParsedDoc:
    """mid: MinerU middle_json；pages: 每页（显示方向）宽高，PDF 点；meta_raw: PyMuPDF 的文档元数据。"""
    mpages = mid.get("pages") or []
    if not mpages:
        raise PdfError("MinerU 没有返回任何页面")
    sections: list[ParsedSection] = []
    passages: list[ParsedPassage] = []
    figures: list[ParsedFigure] = []
    equations: list[ParsedEquation] = []
    title_text = ""
    front_extra: list[str] = []

    state = {"sec": -1, "in_refs": False, "refs_sec": None, "seen_heading": False, "after_abstract": False}

    def add(page, kind, text, bbox, math=None, sec="cur"):
        text = text.strip()
        if not text or bbox is None:
            return None
        so = state["sec"] if sec == "cur" else sec
        p = ParsedPassage(len(passages), page, kind, text, bbox, so, "mineru", [], math or [])
        passages.append(p)
        return p

    def caption_of(content: list, key: str, w, h):
        parts, boxes = [], []
        for sb in content or []:
            if isinstance(sb, dict) and sb.get("type") == key:
                parts.append(_clean(_plain(sb.get("content"))))
                boxes.append(_box(sb, w, h))
        return " ".join(x for x in parts if x), _union(boxes)

    for pi, mp in enumerate(mpages):
        pno = int(mp.get("page_idx", pi)) + 1
        if pno - 1 >= len(pages):
            continue
        w, h = pages[pno - 1]
        for b in mp.get("blocks") or []:
            t = b.get("type")
            c = b.get("content")
            box = _box(b, w, h)
            if t in DROP:
                if pno == 1 and t in ("header", "footer"):
                    front_extra.append(_clean(_plain(c)))
                continue
            if t == "doc_title":
                tt = _clean(_plain(c))
                if tt and not title_text:
                    title_text = tt
                    if not state["seen_heading"]:
                        # 标题本身算首部信息的一部分，不另起章节
                        pass
                    continue
                t = "paragraph_title"
            if t == "paragraph_title":
                tt = _clean(_plain(c))
                if not tt:
                    continue
                if not title_text and not sections and pno == 1 and b.get("level") == 1:
                    title_text = tt
                    continue
                if CAPTION_HEAD.match(tt):  # “Table S3 …”“Figure 2 …”被识别成标题：这是图表题注，不进目录
                    add(pno, "paragraph", tt, box)
                    continue
                state["seen_heading"] = True
                state["in_refs"] = bool(REF_HEAD.match(tt))
                s = ParsedSection(len(sections), _level(tt, b.get("level")), tt, pno, "detected")
                sections.append(s)
                state["sec"] = s.ord
                if state["in_refs"]:
                    state["refs_sec"] = s.ord
                continue
            if t == "ref_text":
                add(pno, "reference", _clean(_plain(c)), box)
                continue
            if t == "page_footnote":
                tx = _clean(_plain(c))
                if pno == 1:  # 首页脚注（单位、收稿日期、DOI）归首部信息
                    front_extra.append(tx)
                    add(pno, "front", tx, box, sec=-1)
                else:
                    add(pno, "paragraph", tx, box)
                continue
            if t in ("equation", "interline_equation"):
                latex = (c if isinstance(c, str) else _plain(c)).strip()
                m = TAG_RE.search(latex)
                number = m.group(1).strip().strip("()") if m else None
                body = TAG_RE.sub("", latex).strip().rstrip(",.").strip()
                p = add(pno, "formula", body or latex, box)
                if p is not None:
                    equations.append(ParsedEquation(pno, box, number, p.text, p.ord, latex=_safe_equation_latex(body or latex)))
                continue
            if t in ("image", "chart"):
                kind_key = "image_caption" if t == "image" else "chart_caption"
                sub = c if isinstance(c, list) else []
                cap, cbox = caption_of(sub, kind_key, w, h)
                if cap and len(cap) <= 3 and not FIG_LABEL.match(cap):
                    cap, cbox = "", None  # 子图字母（“b”），不是图注
                foot, fbox = caption_of(sub, "image_footnote" if t == "image" else "chart_footnote", w, h)
                body_boxes = [_box(sb, w, h) for sb in sub if isinstance(sb, dict) and sb.get("type") in ("image_body", "chart_body")]
                fbox_body = _union(body_boxes) or box
                cp = add(pno, "caption", (cap + (" " + foot if foot else "")).strip(), _union([cbox, fbox]) if cap or foot else None) if (cap or foot) else None
                m = FIG_LABEL.match(cap) if cap else None
                figures.append(ParsedFigure(pno, fbox_body, "figure", m.group(1).strip() if m else None,
                                            cp.ord if cp else None))
                continue
            if t == "table":
                sub = c if isinstance(c, list) else []
                cap, cbox = caption_of(sub, "table_caption", w, h)
                foot, fbox = caption_of(sub, "table_footnote", w, h)
                bodies = [sb for sb in sub if isinstance(sb, dict) and sb.get("type") == "table_body"]
                tbox = _union([_box(sb, w, h) for sb in bodies]) or box
                cp = add(pno, "caption", (cap + (" " + foot if foot else "")).strip(), _union([cbox, fbox])) if (cap or foot) else None
                inner = None
                grid = None
                for sb in bodies:
                    if isinstance(sb.get("content"), str):
                        h_rows, h_spans = _html_rows_spans(sb["content"])
                        rows, extra = _split_footnote(h_rows)
                        grid = grid or grid_data(rows, extra, h_spans)
                        tx = _html_text(sb["content"]) if not rows else "\n".join(
                            " | ".join(html.unescape(c) for c in r) + " |" for r in rows)
                        if extra and cp is not None and extra not in cp.text:
                            cp.text = (cp.text + " " + extra).strip()
                    else:
                        tx = _clean(_plain(sb.get("content")))
                    ip = add(pno, "figure_text", tx, _box(sb, w, h), sec=state["sec"])
                    inner = inner or ip
                m = FIG_LABEL.match(cap) if cap else None
                figures.append(ParsedFigure(pno, tbox, "table", m.group(1).strip() if m else None,
                                            cp.ord if cp else None, inner.ord if inner else None, grid))
                continue
            if t in ("code", "algorithm"):
                sub = c if isinstance(c, list) else []
                txt = "\n".join(_plain(sb.get("content")) for sb in sub if isinstance(sb, dict)) or _plain(c)
                add(pno, "figure_text", txt, box)
                continue
            # text / list / 其他有文字的块
            math: list[dict] = []
            if isinstance(c, list) and c and isinstance(c[0], dict) and c[0].get("type") not in (
                    "text", "equation_inline", "hyperlink"):
                tx = _clean(" ".join(_plain(x.get("content"), math) for x in c if isinstance(x, dict)))
            else:
                tx = _clean(_plain(c, math))
            if not tx:
                continue
            if state["in_refs"] and (REF_START.match(tx) or t == "ref_text"):
                add(pno, "reference", tx, box, math, sec=state["refs_sec"])
            elif pno == 1 and AFFIL_RE.match(tx):
                add(pno, "front", tx, box, math, sec=-1)  # 单位、通讯作者、“Check for updates”
            elif not state["seen_heading"]:
                words = len(tx.split())
                if state["after_abstract"]:
                    if words >= 40 and not KEYWORDS_RE.match(tx):
                        # 没有标题的引言（Nature 系）：自动补一个“正文”章节，不让它混进首部信息
                        s = ParsedSection(len(sections), 1, "正文", pno, "detected")
                        sections.append(s)
                        state["sec"] = s.ord
                        state["seen_heading"] = True
                        add(pno, "paragraph", tx, box, math)
                    else:
                        add(pno, "front", tx, box, math, sec=-1)
                elif words >= 60:
                    # 标题/作者之后第一段长文字是摘要（可能没有“Abstract”标题）
                    s = ParsedSection(len(sections), 1, "Abstract", pno, "detected")
                    sections.append(s)
                    state["sec"] = s.ord
                    state["after_abstract"] = True
                    add(pno, "paragraph", re.sub(r"(?i)^\s*abstract[\s:：.—-]*", "", tx), box, math)
                else:
                    add(pno, "front", tx, box, math, sec=-1)
            else:
                add(pno, "paragraph", tx, box, math)

    if not passages:
        raise PdfError("MinerU 没有解析出任何文字")

    # 首部信息章节（放最前，序号整体后移）
    if any(p.section_ord == -1 for p in passages):
        for s in sections:
            s.ord += 1
        sections.insert(0, ParsedSection(0, 1, "首部信息", 1, "detected"))
        for p in passages:
            if p.section_ord == -1:
                p.section_ord = 0
            elif p.section_ord is not None:
                p.section_ord += 1

    _merge_panels(figures)
    reflow.reflow(passages, figures, equations)

    # 元数据
    abstract = _find_abstract(passages, sections)
    mu_authors = list(((mid.get("metadata") or {}).get("document") or {}).get("authors") or [])
    front_texts = [p.text for p in passages if p.page == 1 and p.kind in ("front", "paragraph")][:12]
    author_texts = [p.text for p in passages if p.kind == "front"][:6]
    md = extract_metadata(meta_raw or {}, title_text, author_texts,
                          " ".join(front_extra + front_texts), abstract)
    if not md.authors and mu_authors:
        md.authors = mu_authors
    if not md.title:
        md.title = ((mid.get("metadata") or {}).get("document") or {}).get("title") or ""
    full_text = " ".join(p.text for p in passages[:200])
    return ParsedDoc(
        page_count=len(pages), pages=list(pages), sections=sections, passages=passages, metadata=md,
        language=detect_language(full_text), has_text_layer=True, outline_source="mineru",
        figures=figures, equations=equations, warnings=[],
    )


def _merge_panels(figures: list[ParsedFigure]) -> None:
    """MinerU 把一张图的子图拆成多个没有图注的 image 块：并入同页的图注图（并集框）。没有图注可并的保留。"""
    keep: list[ParsedFigure] = []
    for page in sorted({f.page for f in figures}):
        pend: list[ParsedFigure] = []
        last_cap: ParsedFigure | None = None
        for f in [x for x in figures if x.page == page]:
            if f.kind != "figure" or f.caption_ord is not None:
                if f.kind == "figure" and pend:
                    f.bbox = _union([f.bbox] + [p.bbox for p in pend])
                    pend = []
                keep.append(f)
                last_cap = f if f.kind == "figure" else last_cap
            else:
                pend.append(f)
        if pend:
            if last_cap is not None:
                last_cap.bbox = _union([last_cap.bbox] + [p.bbox for p in pend])
            else:
                keep.extend(pend)
    figures[:] = keep


def _find_abstract(passages: list[ParsedPassage], sections: list[ParsedSection]) -> str | None:
    for s in sections:
        if re.match(r"(?i)^\s*(abstract|摘\s*要)\s*$", s.title):
            txt = " ".join(p.text for p in passages if p.section_ord == s.ord and p.kind == "paragraph")
            txt = re.sub(r"(?i)^\s*abstract[\s:：.—-]*", "", txt)
            return txt.strip()[:3000] or None
    for p in passages:
        if p.page == 1 and re.match(r"(?i)^\s*abstract\b", p.text):
            return re.sub(r"(?i)^\s*abstract[\s:：.—-]*", "", p.text).strip()[:3000] or None
    return None
