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

_SURNAME = r"[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]*(?: [\w’'\-À-ÿ]+){0,2}?"
_INITIALS = r"(?:[A-ZÀ-Þ]\.[ \-]?){1,4}"
_AUTHOR = re.compile(rf"\s*({_SURNAME}),\s*({_INITIALS})")
_SEP = re.compile(r"\s*(?:,\s*(?:&\s*|and\s+)?|&\s*|and\s+)")
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


def detect_style(texts: list[str]) -> str:
    """'numeric'：多数条目以编号开头；'author-year'：以“姓氏, 名首字母”开头；否则 'unknown'。"""
    numbered = sum(1 for t in texts if _NUM_PREFIX.match(t))
    author_first = sum(1 for t in texts if re.match(rf"^\s*{_SURNAME},\s*{_INITIALS}", t))
    if numbered >= max(2, 0.3 * len(texts)):
        return "numeric"
    if author_first >= max(2, 0.3 * len(texts)):
        return "author-year"
    return "unknown"


def _is_wide(bbox: list[float] | None, page_w: float | None) -> bool:
    """一个段落的宽度超过版心的 70%：双栏论文里这通常意味着两栏的行被合并到了一起。"""
    return bool(bbox and page_w and (bbox[2] - bbox[0]) > 0.7 * page_w)


def split_entries(passages: list[dict], page_widths: dict[int, float] | None = None) -> tuple[str, list[RefEntry]]:
    """passages：按段落序号排好的 reference 段落 [{ord, page, text, bbox}]。返回（编号风格, 条目列表）。"""
    page_widths = page_widths or {}
    texts = [_clean(p["text"]) for p in passages]
    style = detect_style(texts)
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
        if style == "author-year":
            starts_new = bool(re.match(rf"^\s*{_SURNAME},\s*{_INITIALS}", text))
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

    authors, pos = _parse_authors(raw)
    if not authors:
        authors, pos = _parse_initials_first(raw)
    rest = raw[pos:].lstrip(" .,:;")
    if style == "author-year":  # “Smith, J. & Lee, K. (2020). Title. Venue.”
        ym = re.match(r"\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?[.,]?\s*", rest)
        if ym:
            rest = rest[ym.end():]
    e.authors = authors if len(authors) >= 1 else []
    if not authors or rest[:3] in ("In ", "in "):
        return
    end = _title_end(rest)
    if end < 0:
        return
    title = rest[: end + (1 if rest[end] in "?!" else 0)].strip(" .")
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
