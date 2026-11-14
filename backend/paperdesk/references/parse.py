"""参考文献解析：把解析阶段得到的 kind='reference' 段落整理成一条条参考文献，并保守地抽取题录字段。

原则：解析不出的字段留空，绝不编造；双栏交错、一条被拆成几段、行首编号乱序这类脏数据尽量按编号锚点整理，
整理不干净的条目标记 `messy`，让界面提示“请对照原文”。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# 行首编号：“12. ”“[12] ”“12 Author,”（后一种要求紧跟“姓氏,”的形式，避免把正文数字当编号）
_NUM_PREFIX = re.compile(r"^\s*(?:\[(\d{1,3})\]\s*|(\d{1,3})\.\s+|(\d{1,3})\s+(?=[A-ZÀ-Þ][\w’'\-À-ÿ]+,))")
# 参考文献后面的“致谢/声明/出版说明”等：不属于任何一条参考文献
_BACKMATTER = re.compile(
    r"(Publisher[’']s note|We are grateful|This work is (?:partially )?supported|The authors declare|Supplementary information"
    r"|Correspondence and requests|Reprints and permissions|Author contributions|Competing interests|Additional information"
    r"|Acknowledg|designed and managed the project|Scholarship offered by|Foundation Grant|published maps)",
    re.I,
)
# 一条参考文献“写完了”的标志：以带年份的括号结尾，如 “(2002).” “(ACM, 2012).”
_COMPLETE = re.compile(r"\((?:[^()]*?[\s,])?\d{4}[a-z]?\)\.?\s*$")

# 姓氏前的小写前缀（van der Maaten, de Jong）：APA 里很常见
_PARTICLE = r"(?:(?:van|von|de|der|den|di|da|del|della|la|le|ten|ter|bin|al|el|du|dos|das)\s+){0,2}"
_SURNAME = _PARTICLE + r"[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]*(?: [\w’'\-À-ÿ]+){0,2}?"
_INITIALS = r"(?:[A-ZÀ-Þ]\.[ \-]?){1,4}"
_AUTHOR = re.compile(rf"\s*({_SURNAME}),\s*({_INITIALS})")
_SEP = re.compile(r"\s*(?:,\s*(?:(?:\.\.\.|…)\s*)?(?:&\s*|and\s+)?|(?:\.\.\.|…)\s*|&\s*|and\s+)")
# APA 的条目开头：作者（个人或机构）后面紧跟“(年份).”，如 “Smith, J. (2020).”“OpenAI. (2023).”“World Health Organization. (2020, March 5).”
_APA_START = re.compile(r"^\s*[A-ZÀ-Þa-z][^()]{1,300}?\.?\s*\(\s*(?:19|20)\d{2}[a-z]?(?:\s*,\s*[A-Za-z]+\.?(?:\s*\d{1,2})?)?\s*\)\s*\.")
_APA_ORG = re.compile(r"^\s*([A-ZÀ-Þ][^()]{1,100}?)\.?\s*\(\s*(?:19|20)\d{2}[a-z]?[^()]*\)\s*\.")
_ETAL = re.compile(r"\s*,?\s*et al\.?")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ARXIV_NEW = re.compile(r"(?:arxiv[.:/ ]*\s*(?:abs/)?\s*|abs/\s*)(\d{4}\.\d{4,5})(v\d+)?", re.I)
_ARXIV_OLD = re.compile(r"arxiv[:/ ]\s*([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", re.I)
_YEAR_PAREN = re.compile(r"\((?:[^()]*?[\s,])?((?:19|20)\d{2})[a-z]?\)")
_YEAR_ANY = re.compile(r"(?<![\d–\-.])((?:19|20)\d{2})(?![\d–\-]|\.\d)")


@dataclass
class RefEntry:
    index: int
    ord: int
    page: int
    ords: list[int]
    raw: str
    authors: list[str] = field(default_factory=list)
    title: str = ""
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv: str = ""
    url: str = ""
    merged: bool = False  # 双栏被合并成一行/一条被拆成几段，文字可能夹杂别的条目的片段
    messy: bool = False  # 抽出的字段不可靠


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("­", "")).strip()


def _has_unrecoverable_text(text: str) -> bool:
    """PDF 字体映射损坏时会产生 replacement character；不要据此编造字段。"""
    return "�" in text or "\ufffd" in text


def detect_style(texts: list[str]) -> str:
    """'numeric'：多数条目以编号开头；'author-year'：以“姓氏, 名首字母”开头；否则 'unknown'。"""
    numbered = sum(1 for t in texts if _NUM_PREFIX.match(t))
    author_first = sum(1 for t in texts if re.match(rf"^\s*{_SURNAME},\s*{_INITIALS}", t) or _APA_START.match(t))
    if numbered >= max(2, 0.3 * len(texts)):
        return "numeric"
    if author_first >= max(2, 0.3 * len(texts)):
        return "author-year"
    if _is_acl(texts):
        return "author-year"
    return "unknown"


# ACL / NLP 论文常见的“全名 + 年份 + 句点”格式：Jacob Devlin, Ming-Wei Chang, and Kristina Toutanova. 2019. BERT: …（没有括号，换行后年份常在下一行行首）
_ACL_YEAR = re.compile(r"[A-Za-z’'\-]\.?\s+(?:19|20)\d{2}[a-z]?\.\s+[A-Z0-9]")
_ACL_YEAR_LINE = re.compile(r"^\s*(?:19|20)\d{2}[a-z]?\.\s")
_ACL_SPLIT = re.compile(r"^(.{3,400}?[A-Za-z’'\-])\.\s+((?:19|20)\d{2})[a-z]?\.\s*(.*)$", re.S)
_NAME_PARTICLES = {"van", "von", "de", "der", "den", "di", "da", "del", "della", "la", "le", "ten", "ter", "bin", "al", "el", "du", "dos", "das"}


def _is_acl(texts: list[str]) -> bool:
    n = sum(1 for t in texts if _ACL_YEAR.search(t[:260]) or _ACL_YEAR_LINE.match(t))
    return n >= max(3, 0.3 * len(texts))


def _full_name_to_surname_initials(name: str) -> str:
    """“Ming-Wei Chang”→“Chang, M.-W.”；“Inigo Lopez- Gazpio”→“Lopez-Gazpio, I.”；“Rie Kubota Ando”→“Ando, R. K.”；“Jan van der Berg”→“van der Berg, J.”"""
    toks = re.sub(r"-\s+", "-", name.strip()).split()
    if len(toks) < 2:
        return name.strip()
    sur = [toks.pop()]
    while toks and toks[-1].lower() in _NAME_PARTICLES:
        sur.insert(0, toks.pop())
    if not toks:
        return " ".join(sur)
    initials = " ".join("-".join(f"{part[0]}." for part in t.split("-") if part) for t in toks if t[0].isalpha())
    return f"{' '.join(sur)}, {initials}"


def _parse_acl(e: "RefEntry") -> bool:
    m = _ACL_SPLIT.match(e.raw)
    if not m:
        return False
    names = [n.strip() for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1)) if n.strip()]
    if not names or any(len(n.split()) > 5 or any(ch.isdigit() for ch in n) for n in names):
        return False
    e.authors = [_full_name_to_surname_initials(n) if len(n.split()) >= 2 else n for n in names]
    e.year = int(m.group(2))
    rest = m.group(3).strip()
    end = _title_end_with_subtitle(rest)
    if end >= 0:
        title = rest[: end + (1 if rest[end] in "?!" else 0)].strip(" .")
        if len(title) >= 8:
            e.title = title
            e.venue = rest[end + 1:].strip(" .,;")
    return True


def _is_wide(bbox: list[float] | None, page_w: float | None) -> bool:
    """一个段落的宽度超过版心的 70%：双栏论文里这通常意味着两栏的行被合并到了一起。"""
    return bool(bbox and page_w and (bbox[2] - bbox[0]) > 0.7 * page_w)


def split_entries(passages: list[dict], page_widths: dict[int, float] | None = None) -> tuple[str, list[RefEntry]]:
    """passages：按段落序号排好的 reference 段落 [{ord, page, text, bbox}]。返回（编号风格, 条目列表）。"""
    page_widths = page_widths or {}
    texts = [_clean(p["text"]) for p in passages]
    style = detect_style(texts)
    surname_first = sum(1 for t in texts if re.match(rf"^\s*{_SURNAME},\s*{_INITIALS}", t) or _APA_START.match(t))
    acl = style == "author-year" and surname_first < max(2, 0.3 * len(texts))  # 全名格式：靠“首行顶格、续行缩进”分条
    margins: dict[tuple[int, int], float] = {}
    if acl:
        for p in passages:
            if p.get("bbox"):
                key = (p["page"], int(p["bbox"][0] // ((page_widths.get(p["page"]) or 600) * 0.3)))
                margins[key] = min(margins.get(key, 1e9), p["bbox"][0])
    entries: list[RefEntry] = []
    cur: RefEntry | None = None
    seq = 0
    for p, text in zip(passages, texts):
        if not text:
            continue
        bm = _BACKMATTER.search(text)
        if bm:
            text = text[: bm.start()].strip()  # 把混进来的致谢/声明部分切掉
            if not text:
                continue
        wide = _is_wide(p.get("bbox"), page_widths.get(p["page"]))
        m = _NUM_PREFIX.match(text) if style == "numeric" else None
        starts_new = bool(m)
        if acl:
            bb = p.get("bbox")
            key = (p["page"], int(bb[0] // ((page_widths.get(p["page"]) or 600) * 0.3))) if bb else None
            starts_new = bool(bb) and bb[0] <= margins.get(key, bb[0]) + 3.0 and not _ACL_YEAR_LINE.match(text)
        elif style == "author-year":
            starts_new = bool(re.match(rf"^\s*{_SURNAME},\s*{_INITIALS}", text) or _APA_START.match(text))
        if style == "unknown":
            starts_new = cur is None or bool(_COMPLETE.search(cur.raw))
        if starts_new:
            seq += 1
            idx = int(next(g for g in m.groups() if g)) if m else seq
            body = text[m.end():] if m else text
            cur = RefEntry(index=idx, ord=p["ord"], page=p["page"], ords=[p["ord"]], raw=body.strip(), merged=wide)
            entries.append(cur)
        elif cur is not None and not _COMPLETE.search(cur.raw) and len(text) < 400:
            cur.raw = f"{cur.raw} {text}".strip()
            cur.ords.append(p["ord"])
            cur.merged = cur.merged or wide
    # 同一编号只保留第一条，并按编号排序
    seen: set[int] = set()
    uniq = []
    for e in entries:
        if e.index in seen:
            continue
        seen.add(e.index)
        uniq.append(e)
    uniq.sort(key=lambda e: e.index)
    for e in uniq:
        parse_fields(e, style)
        if _has_unrecoverable_text(e.raw):
            e.messy = True
            # raw 保留给用户对照；损坏字段不进入翻译或联网匹配。
            e.authors = []
            e.title = ""
            e.venue = ""
    if acl:
        # 全名格式的每一条都有“. 年份.”：没有年份的是附录等混进来的正文段落，不是参考文献；去掉后重新编号，保持连续
        uniq = [e for e in uniq if e.year]
        for i, e in enumerate(uniq, 1):
            e.index = i
    return style, uniq


def _year(raw: str) -> int | None:
    last = None
    for m in _YEAR_PAREN.finditer(raw):
        last = int(m.group(1))
    if last is None:
        for m in _YEAR_ANY.finditer(raw):
            last = int(m.group(1))
    if last is not None and not (1900 <= last <= date.today().year + 1):
        return None
    return last


def _parse_authors(text: str) -> tuple[list[str], int]:
    authors: list[str] = []
    pos = 0
    while True:
        m = _AUTHOR.match(text, pos)
        if not m:
            break
        authors.append(f"{m.group(1).strip()}, {m.group(2).strip()}")
        pos = m.end()
        et = _ETAL.match(text, pos)
        if et:
            authors.append("et al.")
            pos = et.end()
            break
        sep = _SEP.match(text, pos)
        if not sep:
            break
        pos = sep.end()
    return authors, pos


_INIT_FIRST = re.compile(r"\s*(?:and\s+|&\s*)?((?:[A-Z]\.\s?){1,3}(?:[a-z]+\s)?[A-Z][\w'’\-]+(?:\s[A-Z][\w'’\-]+)?)\s*(?:,|$)")


_INIT_FIRST_ETAL = re.compile(r"\s*((?:[A-Z]\.\s?){1,3}[A-Z][\w'’\-]+)\s+et al\.?\s*,?")
# IEEE：标题用引号括起来，逗号在引号里面：“Differentiable collision-free parametric corridors,” in Proc. …
_QUOTED_TITLE = re.compile(r"[“\"]([^”\"]{8,}?)[,.]?[”\"]\s*,?\s*(.*)$", re.S)


def _parse_initials_first(text: str) -> tuple[list[str], int]:
    """“Y. Dong, X. Zhu, Z. Pan, Title. Venue (2024)”：名字首字母在前的作者表（Springer 等）。"""
    authors: list[str] = []
    pos = 0
    while True:
        m = _INIT_FIRST.match(text, pos)
        if not m:
            break
        authors.append(m.group(1).strip())
        pos = m.end()
    et = _INIT_FIRST_ETAL.match(text, pos)  # “…, R. Julian et al., Title.”：最后一位作者后面直接跟 et al.
    if et:
        authors += [et.group(1).strip(), "et al."]
        pos = et.end()
    return (authors, pos) if len(authors) >= 2 else ([], 0)


def _title_end(rest: str) -> int:
    """标题结束的位置（句末标点的下标）：括号内部的句点不算，如 “Network Science (Cambridge Univ. Press, 2016)”。"""
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch in ".?!" and depth == 0:
            nxt = rest[i + 1] if i + 1 < len(rest) else ""
            if nxt == "" or nxt.isspace() or nxt.isupper():
                return i
    return -1


_VENUE_START = re.compile(
    r"(?:in|proceedings|journal|transactions|trans\.|conference|arxiv|preprint|nature|science|ieee|acm|advances|annual|workshop|symposium|"
    r"international|findings|biorxiv|technical report|ph\.?d|thesis|book|chapter|vol\.?)\b", re.I)
_FUNCTION_WORDS = frozenset("a an the of for and in on with to via at by from as or is are".split())


def _title_end_with_subtitle(rest: str) -> int:
    """_title_end 的升级版：标题以问号/感叹号结尾、后面紧跟一句像副标题的话时（“Did Aristotle Use a Laptop? A question answering
    benchmark with implicit reasoning strategies. TACL.”），标题要带上副标题，否则副标题会被当成出处、拿去检索就配错论文。
    副标题的特征：句子式（至少两个小写的实词）、不以 In / Proceedings / Journal 之类的出处词开头；期刊名是首字母大写的几个词，不会满足。"""
    end = _title_end(rest)
    if end < 0 or rest[end] not in "?!":
        return end
    after = rest[end + 1:]
    tail = after.lstrip()
    if not tail[:1].isupper() or _VENUE_START.match(tail):
        return end
    e2 = _title_end(tail)
    if e2 < 0 or tail[e2] != ".":
        return end
    words = tail[:e2].split()
    content = [w for w in words[1:] if w[:1].islower() and w.lower() not in _FUNCTION_WORDS]
    if len(words) >= 4 and len(content) >= 2:
        return end + 1 + (len(after) - len(tail)) + e2
    return end


def parse_fields(e: RefEntry, style: str) -> None:
    raw = e.raw
    m = _DOI.search(raw.replace("doi.org/ ", "doi.org/"))
    if m:
        e.doi = m.group(1).rstrip(".,;)").lower()
    am = _ARXIV_NEW.search(raw)
    if am:
        e.arxiv = am.group(1)
    else:
        ao = _ARXIV_OLD.search(raw)
        if ao:
            e.arxiv = ao.group(1)
    if e.doi:
        e.url = f"https://doi.org/{e.doi}"
    elif e.arxiv:
        e.url = f"https://arxiv.org/abs/{e.arxiv}"
    else:
        um = re.search(r"https?://\S+", raw)
        if um:
            e.url = um.group(0).rstrip(".,;)")
    e.year = _year(raw)
    if style == "author-year":
        fm = _YEAR_PAREN.search(raw)
        if fm and 1900 <= int(fm.group(1)) <= date.today().year + 1:
            e.year = int(fm.group(1))  # APA：年份紧跟在作者后面；条目里后面出现的括号年份（会议年份等）不算

    if style == "author-year" and _parse_acl(e):
        return
    authors, pos = _parse_authors(raw)
    if not authors:
        authors, pos = _parse_initials_first(raw)
    if not authors and style == "author-year":  # 机构作者：“OpenAI. (2023).”“World Health Organization. (2020).”
        om = _APA_ORG.match(raw)
        if om:
            authors, pos = [om.group(1).strip(" .,")], om.end(1)
    rest = raw[pos:].lstrip(" .,:;")
    if style == "author-year":  # “Smith, J. & Lee, K. (2020). Title. Venue.”
        ym = re.match(r"\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?[.,]?\s*", rest)
        if ym:
            rest = rest[ym.end():]
    e.authors = authors if len(authors) >= 1 else []
    if not authors or rest[:3] in ("In ", "in "):
        return
    qm = _QUOTED_TITLE.match(rest)
    if qm:
        e.title = qm.group(1).strip(" ,.")
        e.venue = qm.group(2).strip(" .,;")
        e.messy = e.merged
        return
    end = _title_end_with_subtitle(rest)
    if end < 0:
        return
    title = rest[: end + (1 if rest[end] in "?!" else 0)].strip(" .")
    title = re.sub(r"\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)$", "", title)  # Science 格式：“Title (2023). arXiv…”，纯年份括号在句号前；带出版社的括号是书名的一部分，不动
    venue = rest[end + 1:].strip(" .,;")
    # 标题里夹着页码区间或括号年份，说明这一条被别的栏的文字弄乱了：不报告标题，只留原文
    if len(title) < 8 or re.search(r"\d{3,}[–\-]\d{2,}|\(\w*,?\s*\d{4}\)", title):
        e.messy = True
        return
    e.title = title
    e.venue = re.sub(r"\s*\((?:[^()]*?[\s,])?\d{4}[a-z]?\)\.?\s*$", "", venue).strip(" .,;")
    if e.merged:
        e.messy = True


def entry_to_dict(e: RefEntry) -> dict:
    return {
        "index": e.index, "ord": e.ord, "page": e.page, "ords": e.ords, "raw": e.raw, "authors": e.authors,
        "title": e.title, "year": e.year, "venue": e.venue, "doi": e.doi, "arxiv": e.arxiv, "url": e.url,
        "merged": e.merged, "messy": e.messy,
    }


def first_author_surname(e: dict) -> str:
    """第一作者姓氏（用于作者-年份引用的匹配），小写、去掉变音符号以外的标点。"""
    for a in e.get("authors") or []:
        if a and a != "et al.":
            return re.sub(r"[^\w\-]", "", a.split(",")[0]).lower()
    return ""
