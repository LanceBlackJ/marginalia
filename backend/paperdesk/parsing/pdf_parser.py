"""PDF 全文解析：以文字片段为原子重组版面（行→栏→段落），识别页眉页脚、图表区域、公式、章节与元数据，
保留页码与坐标。

这是尽力而为的启发式解析（不依赖 OCR）。对没有文字层的扫描版 PDF 会明确返回
`has_text_layer=False`，而不是伪造内容。
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import datablocks, inline_math, layout, reflow
from .figures import Region, detect_regions, regions_from_text_clusters
from .layout import LIGATURES, Block, Line, clean_text, is_cjk  # noqa: F401  (LIGATURES 供外部引用)
from .metadata import PdfMetadata, extract_metadata

is_caption = layout.is_caption

# 解析器版本：解析结果的结构或归类规则有实质改进时加 1。导入/重新解析时记入 papers.parser_version，
# 库里版本低于这个值的论文会提示“有新版解析，可重新解析”。
#   1 = 最初版本（段落按版面重组、图区域按图片/矢量块检测）
#   2 = 图内数据归类（datablocks）、图内表格还原、扫描页 OCR、更准的图区域外框与公式分组
#   3 = 记录原文加粗的文字片段（文字视图还原加粗）、还原行内公式（文字视图渲染）
#   4 = 图多的页面也能分栏（回退到全文档的栏沟）、通栏大图的版面区向页边扩展（图例和坐标刻度归入图内文字）
#   5 = 行内的求和（带上下限）还原成公式、行内求和号不再被切成单独一行；行首片段的结尾空格不再丢（“chooseM”“then-step”）
PARSER_VERSION = 13
COVER_RE = re.compile(r"引用本文\s*[:：]|您可能感兴趣的其他文章|在线阅读\s*View online")


NUM_HEADING_RE = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?=[A-Z一-鿿])")
ROMAN_HEADING_RE = re.compile(r"^(?P<num>[IVX]{1,5})\.\s+[A-Z]")
LETTER_HEADING_RE = re.compile(r"^(?P<num>[A-Z])\.\s+[A-Z]")
KNOWN_SECTIONS = {
    "abstract", "introduction", "background", "related work", "related works", "preliminaries",
    "preliminary", "method", "methods", "methodology", "approach", "model", "framework",
    "experiments", "experiment", "experimental setup", "evaluation", "results", "results and discussion",
    "discussion", "analysis", "ablation study", "conclusion", "conclusions", "limitations",
    "future work", "acknowledgments", "acknowledgements", "references", "bibliography",
    "appendix", "supplementary material", "materials and methods", "data", "datasets",
    "online methods", "code availability", "data availability", "author contributions", "competing interests",
    "additional information", "reporting summary", "reporting summary",
    "摘要", "引言", "绪论", "相关工作", "方法", "实验", "结果", "讨论", "结论", "参考文献", "附录", "致谢",
}
REFERENCES_RE = re.compile(r"^(?:\d+\.?\s*)?(references|bibliography|literature cited|参\s*考\s*文\s*献)\s*$", re.I)
APPENDIX_RE = re.compile(r"appendix|supplement|附录|methods|online methods", re.I)
# 附录标题常常只是字母编号（“A. MassiveText”“B. Model Card”）：参考文献之后遇到这样的标题，参考文献就结束了
APPENDIX_HEAD_RE = re.compile(r"^\s*[A-Z]\.\s+[A-Z][a-z]")
SENTENCE_END = tuple(".?!:;。？！：；")


def _appendix_head(title: str) -> bool:
    """“A. MassiveText”这样的附录标题；参考文献折行出来的半条（“Y. Bengio. Maxout networks. In ICML, 2013.”）也以“字母. 大写”开头，但带年份和更多句号。"""
    return bool(APPENDIX_HEAD_RE.match(title)) and title.count(". ") <= 1 and not re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", title)
CJK_RE = re.compile(r"[一-鿿]")


@dataclass
class ParsedSection:
    ord: int
    level: int
    title: str
    page: int
    source: str = "detected"


@dataclass
class ParsedPassage:
    ord: int
    page: int
    kind: str  # paragraph | caption | reference | formula | figure_text | table
    text: str
    bbox: tuple[float, float, float, float]
    section_ord: int | None = None
    source: str = "text"  # text（PDF 文字层）| ocr（扫描页的本地 OCR，可能有识别错误）
    bold: list[str] = field(default_factory=list)  # 原文里加粗的文字片段（按出现顺序），文字视图据此还原加粗
    math: list[dict] = field(default_factory=list)  # 行内公式 [{src, tex}]：src 是正文里这段公式的文字，tex 是还原出的 LaTeX


@dataclass
class ParsedFigure:
    page: int
    bbox: tuple[float, float, float, float]
    kind: str  # figure | table
    label: str | None
    caption_ord: int | None
    inner_ord: int | None = None


@dataclass
class ParsedEquation:
    page: int
    bbox: tuple[float, float, float, float]
    number: str | None
    text: str
    passage_ord: int


@dataclass
class ParsedDoc:
    page_count: int
    pages: list[tuple[float, float]]
    sections: list[ParsedSection]
    passages: list[ParsedPassage]
    metadata: PdfMetadata
    language: str | None
    has_text_layer: bool
    outline_source: str = "detected"
    warnings: list[str] = field(default_factory=list)
    figures: list[ParsedFigure] = field(default_factory=list)
    equations: list[ParsedEquation] = field(default_factory=list)
    needs_ocr: list[int] = field(default_factory=list)  # 还没有文字层、需要 OCR 的扫描页（1 起）
    ocr_pages: list[int] = field(default_factory=list)  # 文字来自 OCR 的页


class PdfError(Exception):
    """PDF 无法打开或无法解析（损坏、加密等），message 可直接展示给用户。"""


# ---------------------------------------------------------------- 工具
@dataclass
class _Page:
    no: int
    native_w: float
    native_h: float
    rot: pymupdf.Matrix
    disp_w: float
    disp_h: float
    spans: list
    lines: list[Line] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)


def _to_display(rot: pymupdf.Matrix, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    r = pymupdf.Rect(bbox) * rot
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


def bold_runs(b: Block) -> list[str]:
    """块里原文加粗的文字片段，按出现顺序。相邻的加粗文字合成一段（含换行续接），数学符号不算。"""
    runs: list[str] = []
    cur = ""

    def flush() -> None:
        nonlocal cur
        t = clean_text(cur).strip()
        if len(t) >= 2 and any(ch.isalnum() for ch in t):
            runs.append(t)
        cur = ""

    for ln in b.lines or []:
        for s in sorted(ln.spans, key=lambda x: x.x0):
            t = s.text.strip()
            if not t:
                continue
            if s.bold and not s.math:
                if cur.endswith("-") and t[:1].islower():
                    cur = cur[:-1] + t
                else:
                    cur = f"{cur} {t}".strip()
            else:
                flush()
    flush()
    return runs


def math_runs(b: Block) -> list[dict]:
    """块里的行内公式：[{src, tex}]，按出现顺序。每一簇都要由字体、字号、基线位置完整还原，认不全的不要。"""
    out: list[dict] = []
    for ln in b.lines or []:
        spans = sorted(ln.spans, key=lambda x: x.x0)
        for cluster in inline_math.line_clusters(spans, ln.size, ln.baseline):
            tex = inline_math.build_tex(cluster, ln.size, ln.baseline)
            if tex is None:
                continue
            src = layout._line_from(cluster).text.strip()
            if tex.count("(") - tex.count(")") == 1:
                # 右括号在后面那段正文里（“c(v_k) denotes”）：把它并进来
                i = spans.index(cluster[-1])
                if i + 1 < len(spans) and spans[i + 1].text.startswith(")"):
                    tex, src = tex + ")", src + ")"
            if len(src) <= 2:
                # 单个字母的公式如果和前后的文字粘在一起（“i”后面紧跟“th”），正文里按词边界找不到它，
                # 前端可能错配到后面别处的同一个字母：这种不收，保持原文字
                i0, i1 = spans.index(cluster[0]), spans.index(cluster[-1])
                before = spans[i0 - 1] if i0 > 0 else None
                after = spans[i1 + 1] if i1 + 1 < len(spans) else None
                size = max(cluster[0].size, 1.0)
                if before is not None and before.text[-1:].isalnum() and cluster[0].x0 - before.x1 < 0.25 * size:
                    continue
                if after is not None and after.text[:1].isalnum() and after.x0 - cluster[-1].x1 < 0.25 * size:
                    continue
            if src and tex.count("(") == tex.count(")"):
                out.append({"src": src, "tex": tex})
    return out


def _split_long(text: str, limit: int = 1600, target: int = 1000) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > target:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for c in chunks:
        while len(c) > limit:
            out.append(c[:target])
            c = c[target:]
        out.append(c)
    return out


def detect_language(text: str) -> str | None:
    sample = text[:20000]
    letters = sum(ch.isalpha() for ch in sample)
    if letters < 50:
        return None
    cjk = len(CJK_RE.findall(sample))
    return "zh" if cjk / letters > 0.3 else "en"


# ---------------------------------------------------------------- 页眉页脚（行级）
def _line_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"[^a-z0-9一-鿿]+", "", text.lower()))


def _segments(line: Line) -> list[tuple[str, float, float]]:
    """按水平大间隙把一行拆成若干段（页眉常把左右两个标签放在同一基线上）。"""
    spans = sorted(line.spans, key=lambda s: s.x0)
    segs: list[list] = []
    for s in spans:
        if segs and s.x0 - segs[-1][2] <= 2.5 * max(s.size, 6):
            segs[-1][0] += " " + s.text.strip()
            segs[-1][2] = s.x1
        else:
            segs.append([s.text.strip(), s.x0, s.x1])
    return [(t, a, b) for t, a, b in segs]


def _remove_furniture(pages: list[_Page]) -> None:
    n = len(pages)
    counts: Counter = Counter()
    for pg in pages:
        seen: set[str] = set()
        for ln in pg.lines:
            if ln.y1 < 0.085 * pg.native_h or ln.y0 > 0.915 * pg.native_h:
                full = _line_key(ln.text)
                if len(full) >= 3:
                    seen.add(full)
                for text, _a, _b in _segments(ln):
                    k = _line_key(text)
                    if len(k) >= 3:
                        seen.add(k)
        counts.update(seen)
    boiler = {k for k, c in counts.items() if n >= 4 and c >= max(3, int(0.3 * n))}
    boiler_long = [k for k in boiler if len(k) >= 10]

    def is_boiler(k: str) -> bool:
        return k in boiler or (len(k) >= 10 and any(k in b or b in k for b in boiler_long))
    for pg in pages:
        keep: list[Line] = []
        for ln in pg.lines:
            in_band = ln.y1 < 0.085 * pg.native_h or ln.y0 > 0.915 * pg.native_h
            if in_band:
                segs = _segments(ln)
                keys = [_line_key(t) for t, _a, _b in segs]
                if re.fullmatch(r"[\divxlc\-–—\s/|]{1,12}", ln.text.lower()):
                    continue  # 页码
                if is_boiler(_line_key(ln.text)):
                    continue
                if any(is_boiler(k) for k in keys) and all(is_boiler(k) or len(k) < 4 for k in keys):
                    continue
            keep.append(ln)
        pg.lines = keep


# ---------------------------------------------------------------- 主流程
_PARSE_LOCK = threading.Lock()


def parse_pdf(path: Path, ocr_pages: dict[int, list[layout.Span]] | None = None) -> ParsedDoc:
    """解析 PDF。ocr_pages：{页码(1 起): OCR 得到的行级 Span}，这些页用 OCR 文字代替（空）文字层。"""
    # PyMuPDF/MuPDF 不支持多线程并发使用；导入任务可能在多个线程里同时运行，因此解析必须串行。
    with _PARSE_LOCK:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise PdfError("无法打开该 PDF 文件，文件可能已损坏。") from exc
        try:
            if doc.needs_pass:
                raise PdfError("该 PDF 已加密，请先解除密码保护后再导入。")
            if doc.page_count == 0:
                raise PdfError("该 PDF 没有任何页面。")
            return _parse_document(doc, ocr_pages)
        finally:
            doc.close()


def _scan_pages(doc: pymupdf.Document, pages: list["_Page"], done: set[int]) -> list[int]:
    """没有文字层、页面主要是一张大图的页（扫描页）。已经有 OCR 文字的页不再算。"""
    out = []
    for pg in pages:
        if pg.no in done or sum(len(s.text.strip()) for s in pg.spans) >= 25:
            continue
        page = doc[pg.no - 1]
        area = max(1.0, page.rect.width * page.rect.height)
        covered = 0.0
        try:
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"]) & page.rect
                if not r.is_empty:
                    covered += r.width * r.height
        except Exception:  # noqa: BLE001  取不到图片信息就当作不是扫描页
            continue
        if covered / area >= 0.45:
            out.append(pg.no)
    return out


def _parse_document(doc: pymupdf.Document, ocr_pages: dict[int, list[layout.Span]] | None = None) -> ParsedDoc:
    n = doc.page_count
    ocr_pages = ocr_pages or {}
    pages: list[_Page] = []
    for i in range(n):
        page = doc[i]
        native = page.rect * page.derotation_matrix
        spans = ocr_pages[i + 1] if (i + 1) in ocr_pages else layout.extract_spans(page)
        pages.append(
            _Page(i + 1, native.width, native.height, page.rotation_matrix, round(page.rect.width, 2),
                  round(page.rect.height, 2), spans)
        )
    page_sizes = [(p.disp_w, p.disp_h) for p in pages]
    total_chars = sum(len(s.text) for p in pages for s in p.spans)
    has_text = total_chars >= 80 * max(1, n) * 0.25 and total_chars > 200
    meta_raw = doc.metadata or {}
    toc = doc.get_toc(simple=True) or []
    ocr_done = {p for p in ocr_pages if 1 <= p <= n}
    needs_ocr = _scan_pages(doc, pages, ocr_done)

    if not has_text:
        md = extract_metadata(meta_raw, "", [], "", None)
        return ParsedDoc(
            page_count=n, pages=page_sizes, sections=[], passages=[], metadata=md, language=None,
            has_text_layer=False, outline_source="none", needs_ocr=needs_ocr, ocr_pages=sorted(ocr_done),
            warnings=["未检测到文字层，可能是扫描版 PDF。可以阅读原文，但全文检索与 AI 分析暂不可用。"],
        )

    body = layout.body_font_size([p.spans for p in pages])
    # 栏结构在整份文档里通常一致：文字太少的页面（末页、图多的页）回退到全文档级的栏沟
    for i, pg in enumerate(pages):
        # OCR 页整页就是一张扫描图，不能当作“图区域”（否则所有文字都会被判成图内文字）：改用密集短行聚类找图
        pg.regions = (regions_from_text_clusters(pg.spans, pg.native_w, pg.native_h) if pg.no in ocr_done
                      else detect_regions(doc[i], pg.native_w, pg.native_h))

    def outside_figures(pg: _Page) -> list:
        # 图里的坐标轴标签/图例横跨页面中线，会干扰栏检测
        return [s for s in pg.spans if not any(r.contains_point((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2) for r in pg.regions)]

    doc_gutter = layout.detect_gutter([s for p in pages for s in outside_figures(p)], pages[0].native_h)
    for pg in pages:
        usable = outside_figures(pg)
        gutter = layout.detect_gutter(usable, pg.native_h) if len(usable) >= 30 else doc_gutter
        if gutter is None and doc_gutter is not None:
            # 图多、整宽内容多的页面单独检测不到栏沟，但整份文档的栏结构是一致的：用全文档的栏沟。
            # 只有两侧文字之间有明显空隙、且没有文字跨过栏沟的行才会被分成左右栏，单栏的行不受影响
            gutter = doc_gutter
        pg.lines = layout.build_lines(list(pg.spans), gutter, body, pg.native_h)
        _flag_figure_lines(pg, body)
    _remove_furniture(pages)

    blocks: list[Block] = []
    figure_blocks: dict[tuple[int, int], Block] = {}
    refs_active = False
    for pg in pages:
        in_refs = refs_active or any(REFERENCES_RE.match(l.text.strip()) for l in pg.lines if l.bold > 0.6 or l.size > body)
        layout.normalize_classes(pg.lines)
        ordered = layout.reading_order([l for l in pg.lines])
        page_blocks = layout.segment_paragraphs(ordered, pg.no, body, in_references=in_refs)
        for ri, reg in enumerate(pg.regions):
            inner = sorted((l for l in pg.lines if l.in_figure and _nearest_region(pg, l) is reg),
                           key=lambda l: (round(l.y0), l.x0))
            if inner:
                text = clean_text(" ".join(l.text for l in inner))
                x0 = min(reg.x0, min(l.x0 for l in inner)); y0 = min(reg.y0, min(l.y0 for l in inner))
                x1 = max(reg.x1, max(l.x1 for l in inner)); y1 = max(reg.y1, max(l.y1 for l in inner))
                fb = Block(pg.no, x0, y0, x1, y1, text, body, 0.0, len(inner), "F", "figure_text")
                figure_blocks[(pg.no, ri)] = fb
                page_blocks.append(fb)
        page_blocks.sort(key=lambda b: (b.y0 if b.kind == "figure_text" else 0))  # 稳定排序占位：保持分段顺序
        blocks.extend(_order_with_figures(page_blocks, pg))
        for b in page_blocks:
            if b.heading_hint and REFERENCES_RE.match(b.text.strip()):
                refs_active = True
            elif b.heading_hint and refs_active and (APPENDIX_RE.search(b.text) or _appendix_head(b.text)):
                refs_active = False

    return _assemble(doc, pages, blocks, body, meta_raw, toc, page_sizes, ocr_done, needs_ocr)


def _flag_figure_lines(pg: _Page, body: float) -> None:
    if not pg.regions:
        return
    total = sum(len(l.text) for l in pg.lines) or 1
    flagged = 0
    for ln in pg.lines:
        cx, cy = (ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2
        inside = any(r.contains_point(cx, cy) for r in pg.regions)
        near = False
        if not inside and ln.size <= 0.97 * body and len(ln.text) <= 70 and len(ln.text.split()) <= 10                 and not layout.is_caption(ln.text):
            near = any(r.x0 - 4 <= cx <= r.x1 + 4 and (r.y0 - 16 <= cy <= r.y0 or r.y1 <= cy <= r.y1 + 18) for r in pg.regions)
        if (inside and len(ln.text) <= 110 and len(ln.text.split()) <= 18) or near:
            ln.in_figure = True
            flagged += len(ln.text)
    flagged_lines = [l for l in pg.lines if l.in_figure]
    avg = sum(len(l.text) for l in flagged_lines) / max(1, len(flagged_lines))
    if flagged / total > 0.6 and avg > 55:  # 被标成“图”的行平均很长：其实是带边框的正文页，别当图内文字
        for ln in pg.lines:
            ln.in_figure = False
        pg.regions = []


def _nearest_region(pg: _Page, ln: Line) -> Region | None:
    cx, cy = (ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2
    best, best_d = None, 1e9
    for r in pg.regions:
        dx = max(r.x0 - cx, 0, cx - r.x1)
        dy = max(r.y0 - cy, 0, cy - r.y1)
        d = dx * dx + dy * dy
        if d < best_d:
            best, best_d = r, d
    return best


def _order_with_figures(page_blocks: list[Block], pg: _Page) -> list[Block]:
    """图内文字块按其区域在页面中的垂直位置，插入正文块序列。"""
    normal = [b for b in page_blocks if b.kind != "figure_text"]
    figs = [b for b in page_blocks if b.kind == "figure_text"]
    if not figs:
        return normal
    out: list[Block] = []
    pending = sorted(figs, key=lambda b: b.y0)
    for b in normal:
        while pending and pending[0].y0 <= b.y0 and b.col == "F":
            out.append(pending.pop(0))
        out.append(b)
    out.extend(pending)
    return out


# ---------------------------------------------------------------- 组装：标题、章节、段落、实体
def _heading_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".:：")


def _named(text: str) -> bool:
    low = re.sub(r"^[\dIVXA-Z]{1,5}[.\s]+", "", text.lower()).strip(" .:：")
    return low in KNOWN_SECTIONS or text.lower().strip(" .:：") in KNOWN_SECTIONS


def _heading_level_numbered(text: str) -> tuple[int, str]:
    m = NUM_HEADING_RE.match(text)
    if m:
        return m.group("num").count(".") + 1, "num"
    if ROMAN_HEADING_RE.match(text):
        return 1, "roman"
    if LETTER_HEADING_RE.match(text):
        return 2, "letter"
    return 0, ""


def _is_heading_block(b: Block, body: float) -> bool:
    t = b.text.strip()
    if not b.heading_hint or len(t) < 3 or len(t) > 160:
        return False
    if is_caption(t) or re.fullmatch(r"[\d\W]+", t):
        return False
    words = t.split()
    if t.endswith(".") and len(words) > 12:
        return False
    depth, _ = _heading_level_numbered(t)
    return bool(b.bold >= 0.85 or depth or _named(t) or b.size >= body * 1.15)


_AFFIL_RE = re.compile(r"universit|institut|department|college|school of|laborator|hospital|academy|大学|学院|研究所|实验室|e-?mail", re.I)


FRONT_NOTE_RE = re.compile(r"(?i)correspondence|licensed under|creative commons|©|\(c\)\s*\d{4}|received\s*:|accepted\s*:|published\s*:|"
                           r"corresponding author|equal contribution|these authors contributed|e-?mail\s*:|@[\w.-]+\.[a-z]{2,}")


def _looks_like_affiliation(text: str) -> bool:
    return len(_AFFIL_RE.findall(text)) >= 2 or bool(re.match(r"\s*\d[A-Z]", text))


def _assemble(doc, pages: list[_Page], blocks: list[Block], body: float, meta_raw: dict, toc, page_sizes,
              ocr_done: set[int], needs_ocr: list[int]) -> ParsedDoc:
    page_by_no = {p.no: p for p in pages}
    # 知网/万方导出的 PDF 前面带一张“引用本文 / 您可能感兴趣的其他文章”封面：真正的首页是第 2 页；封面文字只留给题录（刊名、DOI），不进正文
    cover_text = ""
    fno = 1
    if len(pages) > 1:
        c1 = " ".join(b.text for b in blocks if b.page == 1)
        if COVER_RE.search(c1):
            fno = 2
            cover_text = c1
            blocks = [b for b in blocks if b.page != 1]
    first = pages[fno - 1]

    # --- 标题：首页上半部分里字号最大的非页眉段落 ---
    p1 = [b for b in blocks if b.page == fno]
    p1_sizes = sorted({round(b.size, 1) for b in p1 if b.kind == "paragraph"}, reverse=True)
    # 正文字号量得偏大时（BERT：正文 10、量出来 11，标题 14.3 < 1.35×11）：首页上最大、比第二大明显大一截的字号也当标题
    big_gap = len(p1_sizes) >= 2 and p1_sizes[0] - p1_sizes[1] >= 1.5
    cand = [b for b in p1 if b.kind == "paragraph" and b.y0 < 0.5 * first.native_h and b.nlines <= 4
            and (b.size >= body * 1.35 or (big_gap and b.size >= body * 1.25 and round(b.size, 1) == p1_sizes[0]))]
    title_blocks: list[Block] = []
    if cand:
        top = max(b.size for b in cand)
        title_blocks = sorted((b for b in cand if abs(b.size - top) <= 1.0), key=lambda b: (b.y0, b.x0))
        # 同样大小的字还出现在页面更靠下的地方（中文会议论文里的作者名、小标题）：只取紧挨着的那一组，不然它们会把标题和作者之间的内容（中文摘要）都吞掉
        grouped: list[Block] = []
        for b in title_blocks:
            if grouped and b.y0 - max(g.y1 for g in grouped) > 2.5 * b.size:
                break
            grouped.append(b)
        title_blocks = grouped
    title_ids = {id(b) for b in title_blocks}

    # --- 首部信息（作者/单位）与摘要 ---
    front_ids: set[int] = set()
    abstract_block: Block | None = None
    abstract_label_block: Block | None = None
    if title_blocks:
        t_end = max(b.y1 for b in title_blocks)
        started = False
        for b in p1:
            if id(b) in title_ids:
                started = True
                continue
            if not started or b.kind != "paragraph":
                continue
            if b.y0 < t_end - 2:
                continue
            if _looks_like_affiliation(b.text):
                front_ids.add(id(b))
                continue
            if _looks_like_names(b.text) and b.y0 < 0.45 * first.native_h:
                front_ids.add(id(b))  # 几位作者并排的一行（“Jonathan Ho  Ajay Jain  Pieter Abbeel”）：不是标题或章节
                continue
            labeled = bool(re.match(r"(?i)\s*(abstract|摘\s*要)", b.text))
            # 没写“摘要”二字、但后面紧跟“关键词：”的中文长段落就是中文摘要（中文会议论文常这样排版）
            nxt_para = next((c for c in p1[p1.index(b) + 1:] if c.kind == "paragraph"), None)
            zh_abs = (
                not labeled and len(b.text) >= 100 and nxt_para is not None and bool(re.match(r"\s*关键\s*词", nxt_para.text))
                and len(re.findall(r"[一-鿿]", b.text)) / max(1, len(b.text)) > 0.5
            )
            if labeled or zh_abs or (len(b.text) >= 350 and not b.run_in):
                if labeled or zh_abs or b.col == "F" or b.bold >= 0.6:
                    abstract_block = b
                    # “Abstract”单独占一行、正文在下一块：摘要是后面那一块，标签块只作章节标题
                    if labeled and len(re.sub(r"(?i)^\s*(abstract|摘\s*要)[\s:：.—-]*", "", b.text).strip()) < 30:
                        nxt = next((c for c in p1[p1.index(b) + 1:] if c.kind == "paragraph" and len(c.text) >= 80
                                    and not re.match(r"(?i)\s*(keywords?|关键词)", c.text)), None)
                        if nxt is not None and nxt.y0 >= b.y0 - 2:
                            abstract_label_block = b
                            abstract_block = nxt
                break
            if b.heading_hint and _named(b.text):
                break
            front_ids.add(id(b))
    # 标题上方的短横幅（REVIEW、Open Access、刊名）不是章节
    if title_blocks:
        t_top = min(b.y0 for b in title_blocks)
        for b in p1:
            if b.kind == "paragraph" and b.y1 <= t_top + 1 and len(b.text.strip()) <= 40 and id(b) not in title_ids:
                front_ids.add(id(b))
    # 首页页眉带里的横幅（刊名、SUPPLEMENTARY INFORMATION 等）不是标题或章节
    for b in p1:
        if b.kind == "paragraph" and b.y1 < 0.1 * first.native_h and id(b) not in title_ids:
            front_ids.add(id(b))
    # 页脚小字号文本（单位、通讯作者）也归入首部信息
    for b in p1:
        if b.kind == "paragraph" and b.size < 0.88 * body and b.y0 > 0.8 * first.native_h:
            front_ids.add(id(b))

    # 首页底部/页边的作者信息：通讯作者、单位、版权与许可、收稿日期。它们不是正文，单独存成 front，阅读时放在“作者与版权信息”里
    front_notes: set[int] = set()
    for b in p1:
        t = b.text.strip()
        if b.kind != "paragraph" or len(t) < 3 or id(b) in title_ids or b is abstract_block or b is abstract_label_block:
            continue
        below_abstract = abstract_block is not None and b.y0 > abstract_block.y1 + 2
        if id(b) in front_ids:
            if b.y0 > 0.8 * first.native_h or b.size < 0.88 * body or below_abstract or FRONT_NOTE_RE.search(t):
                front_notes.add(id(b))
        elif len(t) <= 600 and b.y0 > 0.5 * first.native_h and not b.heading_hint and (
                FRONT_NOTE_RE.search(t) or (b.size < 0.88 * body and b.y0 > 0.7 * first.native_h)):
            front_notes.add(id(b))

    # --- 标题块与章节 ---
    standalone_sizes = sorted({round(b.size * 2) / 2 for b in blocks if id(b) not in front_ids and id(b) not in title_ids
                               and _is_heading_block(b, body) and not _heading_level_numbered(b.text.strip())[0]}, reverse=True)
    size_rank = {s: i + 1 for i, s in enumerate(standalone_sizes)}
    heading_blocks = [b for b in blocks if id(b) not in front_ids and id(b) not in title_ids and b is not abstract_block and b is not abstract_label_block
                      and _is_heading_block(b, body)]
    max_level = 0
    style_order: list[str] = []
    for b in heading_blocks:
        depth, kind = _heading_level_numbered(b.text.strip())
        style = kind if kind != "num" or depth > 1 else "num1"
        if style in ("roman", "letter", "num1") and style not in style_order:
            style_order.append(style)
    # 同时出现多种单级编号风格（I. / A. / 1.）时，按首次出现的先后确定嵌套层级
    nested = len(style_order) > 1
    for b in heading_blocks:
        depth, kind = _heading_level_numbered(b.text.strip())
        style = kind if kind != "num" or depth > 1 else "num1"
        if nested and style in style_order:
            b.level = style_order.index(style) + 1
        elif depth and kind == "num" and depth > 1 and nested:
            b.level = min(4, len(style_order) + depth - 1)
        else:
            b.level = depth or min(3, size_rank.get(round(b.size * 2) / 2, 1))
        max_level = max(max_level, b.level)
    for b in blocks:
        if b.run_in and not b.heading_hint:
            b.level = min(4, max(max_level, 1) + 1) if heading_blocks else 2

    n_headings = len(heading_blocks) + sum(1 for b in blocks if b.run_in and id(b) not in front_ids)
    use_toc = len(heading_blocks) < 3 and len(toc) >= 3

    sections: list[ParsedSection] = []
    passages: list[ParsedPassage] = []
    figures: list[ParsedFigure] = []
    equations: list[ParsedEquation] = []
    outline_source = "detected"
    if use_toc:
        outline_source = "toc"
        for lvl, title, pg in toc:
            sections.append(ParsedSection(len(sections), max(1, min(lvl, 4)), clean_text(str(title)), max(1, pg), "toc"))

    front_section: int | None = None
    cur_section: int | None = None
    in_refs = False
    after_abstract = False
    figure_passage_of: dict[int, int] = {}

    def new_section(level: int, title: str, page: int) -> int:
        sections.append(ParsedSection(len(sections), max(1, min(level, 4)), title, page, "detected"))
        return len(sections) - 1

    def add_passage(kind: str, text: str, b: Block, section: int | None) -> int:
        bbox = _to_display(page_by_no[b.page].rot, (b.x0, b.y0, b.x1, b.y1))
        first_ord = len(passages)
        runs = bold_runs(b) if kind in ("paragraph", "caption") else []
        maths = math_runs(b) if kind in ("paragraph", "caption") else []
        for chunk in _split_long(text):
            passages.append(ParsedPassage(len(passages), b.page, kind, chunk, bbox, section, bold=[r for r in runs if r in chunk],
                                          math=[m for m in maths if m["src"] in chunk]))
        return first_ord

    for b in blocks:
        pn = b.page
        if use_toc:
            idx = [s.ord for s in sections if s.page <= pn]
            cur_section = idx[-1] if idx else None
        if id(b) in title_ids or b is abstract_label_block:
            continue
        text = b.text.strip()
        if id(b) in front_notes and id(b) not in front_ids:
            add_passage("front", text, b, -1 if not use_toc else cur_section)
            continue
        if id(b) in front_ids:
            if front_section is None and not use_toc:
                front_section = -1  # 稍后统一创建“首部信息”
            if len(text) >= 3:
                add_passage("front" if id(b) in front_notes else "paragraph", text, b, -1 if not use_toc else cur_section)
            continue
        if b is abstract_block:
            if not use_toc:
                cur_section = new_section(1, "Abstract", pn)
            add_passage("paragraph", re.sub(r"(?i)^\s*(abstract|摘\s*要)[\s:：.—-]*", "", text), b, cur_section)
            after_abstract = True
            in_refs = False
            continue
        if not use_toc and _is_heading_block(b, body) and b.level and not (
            in_refs and not (REFERENCES_RE.match(_heading_title(text)) or APPENDIX_RE.search(_heading_title(text)) or _appendix_head(_heading_title(text)))
        ):  # 参考文献里折行的半条（“Y. Bengio. Maxout networks. In ICML, 2013.”）不是标题，别把后面的条目都截成正文
            title = _heading_title(text)
            cur_section = new_section(b.level, title, pn)
            after_abstract = False
            if REFERENCES_RE.match(title):
                in_refs = True
            elif in_refs and (APPENDIX_RE.search(title) or _appendix_head(title)):
                in_refs = False
            continue
        if b.kind in ("formula", "figure_text") or len(text) >= 3 or b.kind == "caption":
            pass
        else:
            continue
        if not use_toc and after_abstract and not b.run_in:
            cur_section = new_section(1, "正文", pn)
            after_abstract = False
        if b.run_in and not use_toc and not in_refs:  # 参考文献里的行首短句（“Y. Bengio. Maxout networks.”）不是小节标题
            cur_section = new_section(b.level or 2, _heading_title(b.run_in), pn)
            after_abstract = False
        if text == "" or (re.fullmatch(r"[\d\W]+", text) and b.kind != "formula"):
            continue
        if b.kind == "formula":
            o = add_passage("formula", text, b, cur_section)
            equations.append(ParsedEquation(pn, _to_display(page_by_no[pn].rot, (b.x0, b.y0, b.x1, b.y1)), b.eq_number, text, o))
        elif b.kind == "figure_text":
            figure_passage_of[id(b)] = add_passage("figure_text", text, b, cur_section)
        elif b.kind == "caption":
            add_passage("caption", text, b, cur_section)
        else:
            add_passage("reference" if in_refs else "paragraph", text, b, cur_section)

    # 首部信息章节（放在最前，序号整体后移）
    if any(p.section_ord == -1 for p in passages):
        for s in sections:
            s.ord += 1
        sections.insert(0, ParsedSection(0, 1, "首部信息", 1, "detected"))
        for p in passages:
            if p.section_ord == -1:
                p.section_ord = 0
            elif p.section_ord is not None:
                p.section_ord += 1

    # 图/表实体：把图区域与最近的图注关联
    cap_passages = [p for p in passages if p.kind == "caption"]
    for pg in pages:
        for ri, reg in enumerate(pg.regions):
            disp = _to_display(pg.rot, (reg.x0, reg.y0, reg.x1, reg.y1))
            best, best_d = None, 80.0
            for p in cap_passages:
                if p.page != pg.no:
                    continue
                # 图区域和图注紧挨着时，边界可能差零点几个点甚至略有重叠（Springer 的图注就贴在图下沿），留 8pt 容差
                below = p.bbox[1] - disp[3]
                above = disp[1] - p.bbox[3]
                d = max(below, 0.0) if -8.0 <= below < best_d else (max(above, 0.0) if -8.0 <= above < best_d else None)
                overlap = min(p.bbox[2], disp[2]) - max(p.bbox[0], disp[0])
                if d is not None and overlap > 20 and d < best_d:
                    best, best_d = p, d
            kind = "table" if best and re.match(r"(?i)\s*(table|表)", best.text) else "figure"
            label = None
            if best:
                m = re.match(r"(?i)\s*((?:extended data |supplementary )?(?:fig(?:ure)?\.?|table|algorithm|图|表)\s*[\dA-Z一二三四五六七八九十]+)", best.text)
                label = m.group(1).strip() if m else None
            figures.append(ParsedFigure(pg.no, disp, kind, label, best.ord if best else None))

    # 图版面里的数字堆砌/标签堆砌/子图标题不是正文：改成 figure_text（不进入正文阅读流，也不参与正文检索）
    datablocks.reclassify(passages, figures, lambda o, k: getattr(o, k), lambda o, v: setattr(o, "kind", v))
    # 图、表不切断正文，并紧跟在首次提到它的段落后面（会重新编号：ord 就是阅读顺序）
    reflow.reflow(passages, figures, equations)

    full_text = " ".join(p.text for p in passages[:200])
    title_text = " ".join(b.text for b in sorted(title_blocks, key=lambda b: (b.y0, b.x0)))
    author_texts = [b.text for b in p1 if id(b) in front_ids and (b.size >= 0.88 * body or _looks_like_names(b.text)) and len(b.text) <= 2500][:40]
    abstract = None
    if abstract_block is not None:
        abstract = re.sub(r"(?i)^\s*(abstract|摘\s*要)[\s:：.—-]*", "", abstract_block.text).strip()[:3000]
    else:
        abstract = _find_abstract(passages, sections)
    # 摘要常被排成每行一个“段落”（居中、窄栏的 arXiv 版式）：只取到一行就太短了，改用“摘要”章节下全部的段落
    if abstract_block is not None:
        alt = _find_abstract(passages, sections)
        zh = lambda t: len(re.findall(r"[一-鿿]", t)) / max(1, len(t))  # noqa: E731
        keep_zh = bool(abstract) and zh(abstract) > 0.5 and zh(alt or "") < 0.2  # 中文摘要已经取到：别被后面更长的英文摘要顶掉
        if alt and not keep_zh and (len(alt) > len(abstract or "") + 40 or _looks_like_names((abstract or "")[:400])):
            abstract = alt
    if abstract and _looks_like_names(abstract[:400]):
        abstract = None  # 取到的是作者名单：宁可没有摘要，也不把名单当摘要
    md = extract_metadata(
        meta_raw, title_text, author_texts, cover_text + " " + " ".join(p.text for p in passages if p.page <= fno + 1), abstract,
    )
    for p in passages:
        if p.page in ocr_done:
            p.source = "ocr"
    warnings = []
    if ocr_done:
        warnings.append(f"其中 {len(ocr_done)} 页的文字由本地 OCR 识别，可能有识别错误，请以原文为准。")
    if needs_ocr:
        warnings.append(f"另有 {len(needs_ocr)} 页是没有文字层的扫描页，尚未识别。")
    return ParsedDoc(
        page_count=doc.page_count, pages=page_sizes, sections=sections, passages=passages, metadata=md,
        language=detect_language(full_text), has_text_layer=True,
        outline_source=outline_source if sections else "none", figures=figures, equations=equations,
        needs_ocr=needs_ocr, ocr_pages=sorted(ocr_done), warnings=warnings,
    )


def _looks_like_names(text: str) -> bool:
    """一段文字是不是作者名单/单位/邮箱：几乎每个词都大写开头、没有小写开头的普通词，或者含邮箱。"""
    words = text.split()
    if "@" in text and len(words) <= 40:
        return True
    if len(words) < 4:
        return False
    # 逗号隔开的一串姓名（“Jack W. Rae, Sebastian Borgeaud, Diego de Las Casas, …”）：每一段 2-5 个词、首尾词大写开头
    parts = [p.strip() for p in re.split(r"\s*(?:,|;|\band\b|&)\s*", re.sub(r"[∗*†‡§¶⋆★☯✉]+", "", text)) if p.strip()]
    if len(parts) >= 4:
        ok = sum(1 for p in parts if 2 <= len(p.split()) <= 5 and p.split()[0][:1].isupper() and p.split()[-1][:1].isupper())
        if ok / len(parts) >= 0.85:
            return True
    cap = sum(1 for w in words if w[:1].isupper() or w[:1] in "∗†‡*")
    return cap / len(words) > 0.8 and not re.search(r"[a-z]{3,} [a-z]{3,} [a-z]{3,}", text)


def _find_abstract(passages: list[ParsedPassage], sections: list[ParsedSection]) -> str | None:
    for s in sections:
        if re.fullmatch(r"(?:\d+\.?\s*)?(abstract|摘\s*要)\s*", s.title.strip().lower()):
            texts = [p.text for p in passages if p.section_ord == s.ord and p.kind == "paragraph"
                     and not re.match(r"(?i)\s*(keywords?|key\s*words|index terms|关键词|关键字)\b", p.text)]
            while texts and _looks_like_names(texts[0]):  # 摘要前面混进来的作者名单、单位、邮箱不是摘要
                texts = texts[1:]
            if texts:
                joined = " ".join(texts)
                joined = re.sub(r"(?<=[a-z])- (?=[a-z])", "", joined)  # 行末连字符的折行接回去
                return joined[:3000]
    for p in passages:
        if p.page <= 2 and p.kind == "paragraph" and re.match(r"(?i)(abstract[\s.:—-]|摘\s*要)", p.text):
            return re.sub(r"(?i)^(abstract[\s.:—-]*|摘\s*要[\s:：.]*)", "", p.text)[:3000]
    return None
