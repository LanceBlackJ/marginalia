"""统一的检索结果模型与文本归一化工具。所有文献都来自真实数据源的返回，绝不由模型生成。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# 状态：正式发表 / 预印本 / 已接收待出版 / 其它可确认状态
STATUS_LABELS = {"published": "正式发表", "preprint": "预印本", "accepted": "已接收待出版", "other": "状态未知"}

# 已知的预印本服务器 DOI 前缀
PREPRINT_DOI_PREFIXES = (
    "10.48550/",  # arXiv
    "10.1101/",  # bioRxiv / medRxiv
    "10.31219/",  # OSF Preprints
    "10.2139/",  # SSRN
    "10.20944/",  # Preprints.org
    "10.21203/",  # Research Square
    "10.26434/",  # ChemRxiv
    "10.31234/",  # PsyArXiv
    "10.31235/",  # SocArXiv
    "10.22541/",  # Authorea
    "10.36227/",  # TechRxiv
)


@dataclass
class Work:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    date: str | None = None
    venue: str | None = None
    venue_type: str | None = None  # journal | conference | repository | book series | other
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    status: str = "other"
    status_evidence: str = ""
    peer_reviewed: bool | None = None
    core_venue: bool | None = None
    citations: int | None = None
    fwci: float | None = None
    percentile: float | None = None  # 0–1，同领域同年份引用归一化百分位
    field_name: str | None = None
    field_id: str | None = None
    domain: str | None = None
    topics: list[str] = field(default_factory=list)
    open_access: bool | None = None
    work_type: str | None = None
    openalex_id: str | None = None
    referenced_works: list[str] = field(default_factory=list)
    related_works: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # [{name, id, url}]
    versions: list[dict] = field(default_factory=list)  # 其它版本（预印本/正式发表）
    source_rank: dict[str, int] = field(default_factory=dict)  # 各来源给出的名次（越小越靠前）
    in_library: str | None = None
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    rank_note: str = ""
    reason: str = ""
    reason_source: str = ""  # ai | rule
    relevance_note: str = ""
    relation: str = ""  # 相关工作模式：与基准论文的关系（被引用/施引/相关）
    top_venue: dict | None = None  # 随机探索：来源经白名单确认后的信息 {id, name, short, kind, category, label}

    @property
    def key(self) -> str:
        """稳定标识：优先 DOI，其次 arXiv，再次 OpenAlex，最后标题哈希。"""
        if self.doi:
            return "doi:" + self.doi.lower()
        if self.arxiv_id:
            return "arxiv:" + self.arxiv_id.lower()
        if self.openalex_id:
            return "oa:" + self.openalex_id.rsplit("/", 1)[-1]
        return "t:" + hashlib.sha1(norm_title(self.title).encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        from .kind import classify

        kind = classify(self.title, self.abstract, self.work_type, self.venue)
        return {
            "kind": kind["kind"], "kind_label": kind["label"], "kind_evidence": kind["evidence"],
            "key": self.key, "title": self.title, "authors": self.authors, "year": self.year, "date": self.date,
            "venue": self.venue, "venue_type": self.venue_type, "doi": self.doi, "arxiv_id": self.arxiv_id,
            "pmid": self.pmid, "abstract": self.abstract, "url": self.url, "pdf_url": self.pdf_url,
            "status": self.status, "status_label": STATUS_LABELS.get(self.status, "状态未知"),
            "status_evidence": self.status_evidence, "peer_reviewed": self.peer_reviewed,
            "core_venue": self.core_venue, "citations": self.citations, "fwci": self.fwci,
            "percentile": self.percentile, "field": self.field_name, "domain": self.domain, "topics": self.topics[:6],
            "open_access": self.open_access, "work_type": self.work_type, "openalex_id": self.openalex_id,
            "sources": self.sources, "versions": self.versions, "in_library": self.in_library,
            "score": round(self.score, 4), "breakdown": {k: round(v, 3) for k, v in self.breakdown.items()},
            "rank_note": self.rank_note, "reason": self.reason, "reason_source": self.reason_source,
            "relevance_note": self.relevance_note, "relation": self.relation, "top_venue": self.top_venue,
        }


# ---------------- 归一化 ----------------
_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"^\s*abstract[:.\s]+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def norm_title(title: str | None) -> str:
    t = unicodedata.normalize("NFKD", title or "").lower()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9一-鿿]+", "", t)


def title_tokens(title: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", title or "").lower()
    # 数字与单个罗马数字也要保留：否则 “Part 1” 与 “Part 2” 这类系列标题会被当成同一篇
    return {w for w in re.findall(r"[a-z0-9]{2,}|\d|[ivx]|[一-鿿]", t)}


def title_similarity(a: str | None, b: str | None) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.I)
    return d.lower().rstrip(".,;") or None


_OLD_ARCHIVES = (r"astro-ph|cond-mat|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|math-ph|nlin|nucl-ex|nucl-th|physics|"
                 r"quant-ph|math|cs|q-bio|q-fin|stat|solv-int|adap-org|alg-geom|chao-dyn|cmp-lg|comp-gas|dg-ga|"
                 r"funct-an|patt-sol|q-alg|supr-con|acc-phys|ao-sci|atom-ph|bayes-an|chem-ph|mtrl-th|plasm-ph")
_ARXIV_ID = rf"(?:\d{{4}}\.\d{{4,5}}|(?:{_OLD_ARCHIVES})(?:\.[A-Z]{{2}})?/\d{{7}})"


def norm_arxiv(value: str | None) -> str | None:
    """只在有明确 arXiv 上下文时识别：arxiv.org 链接、`arXiv:` 前缀，或整串就是编号。
    （不能在任意文本里找“7 位数字”，否则会把普通链接里的数字误认成旧式 arXiv 编号。）"""
    if not value:
        return None
    v = value.strip()
    m = re.search(rf"arxiv\.org/(?:abs|pdf)/({_ARXIV_ID})(?:v\d+)?", v, re.I)
    if not m:
        m = re.search(rf"arxiv:\s*({_ARXIV_ID})(?:v\d+)?", v, re.I)
    if not m:
        m = re.fullmatch(rf"({_ARXIV_ID})(?:v\d+)?", v, re.I)
    return m.group(1).lower() if m else None


def is_preprint_doi(doi: str | None) -> bool:
    return bool(doi) and doi.lower().startswith(PREPRINT_DOI_PREFIXES)


def first_surname(authors: list[str]) -> str:
    if not authors:
        return ""
    parts = authors[0].replace(",", " ").split()
    return norm_title(parts[-1]) if parts else ""


def inverted_index_to_text(inv: dict[str, list[int]] | None) -> str | None:
    """OpenAlex 的摘要是倒排索引，需要还原。"""
    if not inv:
        return None
    pos: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return clean_text(" ".join(w for _, w in pos))


def decide_status(
    *, doi: str | None, work_type: str | None, venue_type: str | None, version: str | None,
    journal_ref: bool = False, accepted_signal: bool = False, source_hint: str | None = None,
) -> tuple[str, str]:
    """判定发表状态，并给出判定依据（只在有明确信号时才下结论）。"""
    if source_hint == "preprint":
        return "preprint", "来自预印本服务器"
    if is_preprint_doi(doi):
        return "preprint", f"DOI 前缀 {doi.split('/')[0]} 属于预印本服务器"
    if (work_type or "").lower() in ("preprint", "posted-content"):
        return "preprint", "数据源标注类型为预印本"
    if venue_type == "repository" and version in (None, "submittedVersion"):
        return "preprint", "发表于机构/预印本仓库"
    if accepted_signal:
        return "accepted", "数据源标明已接收但尚无卷期/页码"
    if venue_type in ("journal", "conference", "book series") or (work_type or "").lower() in (
        "article", "journal-article", "proceedings-article", "book-chapter", "review", "book"
    ):
        if journal_ref or venue_type or version == "publishedVersion":
            return "published", "发表于期刊或会议"
    return "other", "数据源没有提供足以判断发表状态的信息"
