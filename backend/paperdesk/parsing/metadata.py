"""从 PDF 元数据与首页版面推断论文题录（标题、作者、年份、DOI、摘要）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..util import DOI_RE, normalize_doi

AFFIL_WORDS = re.compile(
    r"universit|institut|department|laborator|observatoi|observator|school|college|academy|inc\.|corp|ltd|@|\blab\b|\bcentre\b|\bcenter\b|"
    r"大学|学院|研究所|研究院|实验室|公司|学校|系\b",
    re.I,
)
ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
YEAR_PATTERNS = [
    re.compile(r"(?:©|\(c\)|copyright)\s*(?:by\s+)?(19\d{2}|20\d{2})", re.I),
    re.compile(r"(?:published|accepted|received|submitted)[^.\n]{0,40}?(19\d{2}|20\d{2})", re.I),
    re.compile(r"\b(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(19\d{2}|20\d{2})\b"),
]
VENUE_RE = re.compile(
    r"((?:Proceedings of|Journal of|Transactions on|IEEE Transactions|Conference on|Annual Meeting of)[^.\n]{5,90})", re.I
)
BAD_TITLE = re.compile(r"^(microsoft word|untitled|\w+\.(?:docx?|tex|dvi|pdf|indd)$)", re.I)


@dataclass
class PdfMetadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    source: str = "pdf"


def _sensible_title(t: str | None) -> bool:
    if not t:
        return False
    t = t.strip()
    return len(t) >= 8 and not BAD_TITLE.match(t) and not re.fullmatch(r"[\d\W]+", t)


NOT_NAME_ZH = re.compile(r"[年卷期第页号]|简介|概述|概念|目录|背景|历史|方法|结论|附录|基金|作者|通信|收稿|摘要|关键词|引言|大学|学院|研究所|研究院|实验室|公司|期刊|学报|综述|人工|智能|中国|学校|中心|医院")
NOT_NAME = re.compile(r"\b(open access|visual geometry|review|article|research|group|journal|letter|abstract|preprint|vol|no)\b", re.I)
_PARTICLES = {"van", "der", "den", "de", "del", "della", "di", "da", "dos", "du", "la", "le", "von", "bin", "al", "el", "ten", "ter"}


def _name_words_ok(words: list[str]) -> bool:
    return 2 <= len(words) <= 5 and all(w[0].isupper() or w.lower() in _PARTICLES for w in words if w[0].isalpha())


def _pair_up(words: list[str]) -> list[str] | None:
    """几位作者并排在同一行、只隔着空格（“Jonathan Ho Ajay Jain Pieter Abbeel”）：按“名 姓”两两分开。
    姓前的 van/der/de 这样的小品词跟着姓走；有首字母缩写（“Q.”）或词数对不上时不猜，返回 None。"""
    units: list[str] = []
    for w in words:
        if units and units[-1].split()[-1].lower() in _PARTICLES:
            units[-1] += " " + w
        elif w.lower() in _PARTICLES:
            units.append(w)
        else:
            units.append(w)
    if len(units) < 4 or len(units) % 2 or any(re.fullmatch(r"[A-Z]\.?", u) for u in units):
        return None
    if not all(u.split()[-1][0].isupper() for u in units):
        return None
    return [f"{units[i]} {units[i + 1]}" for i in range(0, len(units), 2)]


def _title_words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", t.lower())


def _is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(any(b == w or b.startswith(w) for b in it) for w in small)  # 词可以只读到一半（“intraspeci”→“intraspecific”）


def split_authors(text: str) -> list[str]:
    if re.search(r"[。；：]\s*$", text.strip()) or re.search(r"[一-鿿]{12,}", text):
        return []  # 以句号/分号/冒号收尾、或连着十几个汉字没有分隔：是正文句子，不是作者行
    text = re.sub(r"\bet\s+al\b\.?", " ", text)  # “B. P. Abbott et al.”：合作组论文只写第一作者
    text = re.sub(r"[∗*†‡§¶♯#\d⋆★☯✉+]+", " ", text)
    text = re.sub(r"\s+(and|&)\s+", ",", text)
    out: list[str] = []
    # 纯中文作者行常用空格分隔（“张三 李四 王五”）
    separators = r"[,;，；、\s]+" if not re.search(r"[A-Za-z]", text) else r"[,;，；、]"
    for part in re.split(separators, text):
        name = re.sub(r"\s+", " ", part).strip(" .")
        if not name:
            continue
        if "⃝" in name or "©" in name:
            continue  # “⃝c ESO 2019”：版权符号被拆成组合圈 + c，不是人名
        words = name.split()
        if re.match(r"The\s", name) and not re.search(r"(?i)collaboration|consortium|group|team", name):
            continue  # “The Swiss AI Lab” 这类单位名不是人名
        if re.fullmatch(r"[A-Z]{2,}(?:\s*-\s*[A-Z]*)+", name):
            continue  # 报告编号（“CERN-PH-EP-2012-218” 去掉数字后剩下的 “CERN-PH-EP- -”），不是名字
        if re.fullmatch(r"[一-鿿]{2,4}", name):
            if not NOT_NAME_ZH.search(name):
                out.append(name)
        elif AFFIL_WORDS.search(name) or NOT_NAME.search(name):
            continue
        elif len(words) >= 4 and (pairs := _pair_up(words)) is not None:
            out.extend(pairs)
        elif _name_words_ok(words):
            out.append(name)
    return out[:25]


def _names_in_block(t: str) -> list[str]:
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    two = re.match(r"^((?:[A-Z]\.|[A-Z][a-z]+)\s+[A-Z][\w'’\-]+)\s*[∗*†‡+]*\s*(?:&|and)\s+((?:[A-Z]\.|[A-Z][a-z]+)\s+[A-Z][\w'’\-]+)(?=\s|$)", lines[0]) if len(lines) == 1 else None
    if two and AFFIL_WORDS.search(lines[0][two.end():]):
        return [two.group(1), two.group(2)]  # “Karen Simonyan & Andrew Zisserman Visual Geometry Group, …”：两位作者后面直接接着单位
    if AFFIL_WORDS.search(t) and len(lines) == 1:
        # 作者和单位排在同一行（“Gao Huang  Zhuang Liu  Cornell University  Tsinghua University”）：取单位之前的名字
        m = AFFIL_WORDS.search(lines[0])
        head = lines[0][: m.start()].split()
        cut = next((i for i, w in enumerate(head) if re.match(r"\d+[A-Z]", w)), None)
        if cut is not None:
            head = head[:cut] + ['_']  # 作者行后面直接接着“1Aerosol Physics Laboratory…”这样的单位编号：名字到此为止
        return split_authors(" ".join(head[:-1]))
    if AFFIL_WORDS.search(t):
        # 作者网格：每位作者一块，“姓名 / 单位 / 邮箱”分行排。单位行和邮箱行不要，第一行当姓名
        if len(lines) >= 2 and not AFFIL_WORDS.search(lines[0]):
            return split_authors(lines[0])
        return []
    return split_authors(t)


_ADDRESS_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b|\b\d{1,5}\s+[A-Z][a-z]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Pkwy|Parkway|Blvd|Boulevard|Dr|Drive)\b|[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")
_ORG_NAME_RE = re.compile(
    r"(?i)(?:^|\s)(?:leading edge|in this issue|previews?|snapshot|errata|correction|member|fellow|ieee|acm|senior|google|brain|deepmind|microsoft|facebook|openai|amazon|nvidia|ibm|baidu|tencent|alibaba|research|labs?|"
    r"cancer|hospital|hutchinson|foundation|society|pkwy|parkway|avenue|boulevard)(?:\s|$)")


def guess_authors(author_texts: list[str]) -> list[str]:
    authors: list[str] = []
    for t in author_texts:
        if len(t) > 2500 or "abstract" in t.lower():
            continue
        if re.match(r"(?i)\s*(?:received|accepted|published|revised|submitted)\s*[:：]", t):
            continue  # 收稿日期行（“Received: 27 April 2020; Accepted: …”）
        if re.match(r"(?i)\s*(?:associate\s+|action\s+|handling\s+|academic\s+)?editors?\b", t):
            continue  # “Editor: Yoshua Bengio”：编辑不是作者
        if t.strip().isupper() and re.search(r"ORIGINAL|ARTICLE|PAPER|REVIEW|LETTER|RESEARCH", t):
            continue  # 页眉里的栏目名（“BIOINFORMATICS ORIGINAL PAPER”）
        mails = list(re.finditer(r"\S+@\S+", t))
        if len(mails) >= 3:
            # “姓名 邮箱 姓名 邮箱 …”（JMLR）：每个邮箱前面的那几个词是名字；最后一个邮箱后面是单位，不看
            names: list[str] = []
            prev_end = 0
            for m in mails:
                words = t[prev_end:m.start()].split()[-4:]
                got_ = split_authors(" ".join(words))
                names.extend(got_[-1:] if got_ else [])
                prev_end = m.end()
            if len(names) >= 3:
                return list(dict.fromkeys(names))[:25]  # 邮箱对应的名字就是完整的作者名单，后面零散的单名行（编辑）不再加
        if _ADDRESS_RE.search(t) and (not AFFIL_WORDS.search(t) or t.lstrip()[:1].isdigit()) and len(re.findall(r"[a-zà-ÿ]\d{1,2}(?:,|\s)", t)) < 2:
            continue  # 通信地址（“Google, 1600 Amphitheatre Pkwy, Mountain View, CA 94043”）：按逗号一拆全成了“人名”
        got = _names_in_block(t)
        # 作者名后面紧跟 ∗ † ‡（共同第一作者、通讯作者）的几位并排：名字、单位、邮箱各占一行，被拼成一段
        # （“Ashish Vaswani∗ Noam Shazeer∗ Niki Parmar∗ Jakob Uszkoreit∗ Google Brain Google Brain …”）：标记就是名字的结束。
        # 只在上面的办法没有把这些带标记的名字都认出来的时候才用，认全了就保持原结果（还能带上没有标记的作者）
        starred = re.findall(r"((?:[A-ZÀ-ÞŁŠ-Ž][\w.'’\-]*\s+){1,3}[A-ZÀ-ÞŁŠ-Ž][\w.'’\-]+)\s*[∗*†‡§¶+]", t)
        starred = [re.sub(r"\d+$", "", n) for n in starred]  # 单位编号写在星号前（“David Silver1*”）：去掉名字后面的数字
        if len(starred) >= 2 and not any(AFFIL_WORDS.search(n) for n in starred) and not all(n in got for n in starred):
            got = starred
        authors.extend(got)
    # 报告编号（“CERN-PH-EP/2012-220”“CMS-HIG-12-028”）、日期不是人名：带斜杠、数字，或字母不到六成的都丢掉
    authors = [a.strip("() ") for a in authors]
    authors = [a for a in authors if not _ORG_NAME_RE.search(a) and ":" not in a]  # 带冒号的是“Academic Editor: Paul”这类标签  # “Google Brain”“Fred Hutchinson Cancer”：单位名，不是人
    authors = [a for a in authors if a and not re.search(r"[/\@\d]", a) and sum(c.isalpha() for c in a) >= 0.7 * len(a.replace(" ", ""))]
    seen = set()
    uniq = []
    for a in authors:
        if a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq[:25]


def extract_metadata(
    meta_raw: dict,
    title_text: str,
    author_texts: list[str],
    front_text: str,
    abstract_text: str | None,
) -> PdfMetadata:
    md = PdfMetadata()
    if meta_raw.get("title") and "&" in meta_raw["title"]:
        import html

        meta_raw = {**meta_raw, "title": html.unescape(meta_raw["title"])}  # Cell 的 PDF 元数据里标题带 HTML 实体（PGC-1&alpha;）
    layout_title = re.sub(r"\s+", " ", title_text or "").strip()
    pdf_title = (meta_raw.get("title") or "").strip()
    if _sensible_title(layout_title):
        md.title = layout_title
        # 版面里读出来的标题缺了几个词（连字、单字母词），而 PDF 元数据里的标题是完整的：它们是同一个标题，用完整的
        if _sensible_title(pdf_title) and _is_subsequence(_title_words(layout_title), _title_words(pdf_title))                 and len(_title_words(pdf_title)) > len(_title_words(layout_title)):
            md.title = re.sub(r"\s+", " ", pdf_title)
    elif _sensible_title(pdf_title):
        md.title = pdf_title

    raw_author = (meta_raw.get("author") or "").strip()
    from_meta = split_authors(raw_author) if raw_author and not AFFIL_WORDS.search(raw_author) else []
    from_layout = guess_authors(author_texts)
    # PDF 元数据里常常只有第一作者：版面解析出更完整的名单时优先采用
    md.authors = from_layout if len(from_layout) >= len(from_meta) else from_meta

    haystack = " ".join(filter(None, [meta_raw.get("subject"), meta_raw.get("keywords"), front_text]))
    m = DOI_RE.search(haystack)
    if m:
        md.doi = normalize_doi(m.group(0))
    am = ARXIV_RE.search(front_text or "") or ARXIV_RE.search(haystack)
    if am:
        md.arxiv_id = am.group(1)

    year = None
    for pat in YEAR_PATTERNS:
        ym = pat.search(front_text or "")
        if ym:
            year = int(ym.group(1))
            break
    if year is None and md.arxiv_id and re.match(r"\d{2}\d{2}\.", md.arxiv_id):
        year = 2000 + int(md.arxiv_id[:2])
    if year is None:
        cd = re.match(r"D:(\d{4})", meta_raw.get("creationDate") or "")
        if cd and 1990 <= int(cd.group(1)) <= 2100:
            year = int(cd.group(1))
    md.year = year

    vm = VENUE_RE.search(front_text or "")
    if vm:
        venue = re.sub(r"\s+", " ", vm.group(1)).strip()
        # “Transactions on Intelligent Systems, 2025, 20(3): 530-547”：来源只要刊名，年份、卷期、页码不属于它
        venue = re.sub(r"[,，;；]\s*(?:19|20)\d{2}\b.*$", "", venue).strip(" ,，;；.")
        md.venue = venue or None
    md.abstract = abstract_text.strip() if abstract_text else None
    return md
