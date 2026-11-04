"""去重与版本关联：同一篇研究的预印本、正式发表版、不同来源的记录合并为一条。

判定用并查集：DOI 相同 / arXiv 号相同 / OpenAlex id 相同 / 来源声明的版本 DOI 相同 / 标题高度相似且作者年份吻合。
合并后以“正式发表”版本为主记录，预印本作为 `versions` 保留，来源与链接全部保留。
"""
from __future__ import annotations

from .models import Work, first_surname, norm_arxiv, norm_doi, norm_title, title_similarity

STATUS_ORDER = {"published": 3, "accepted": 2, "other": 1, "preprint": 0}
SIMILAR_TITLE = 0.86


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _same_work_by_title(a: Work, b: Work) -> bool:
    if not a.title or not b.title:
        return False
    if norm_title(a.title) == norm_title(b.title) and len(norm_title(a.title)) > 12:
        return True
    if title_similarity(a.title, b.title) < SIMILAR_TITLE:
        return False
    # 标题相似时还要求作者或年份吻合，避免把系列论文（Part I / Part II）合并
    same_author = bool(first_surname(a.authors)) and first_surname(a.authors) == first_surname(b.authors)
    close_year = a.year is None or b.year is None or abs(a.year - b.year) <= 2
    return same_author and close_year


def merge_works(works: list[Work]) -> list[Work]:
    n = len(works)
    uf = _UF(n)
    by_key: dict[str, int] = {}

    def link(key: str | None, i: int) -> None:
        if not key:
            return
        if key in by_key:
            uf.union(by_key[key], i)
        else:
            by_key[key] = i

    for i, w in enumerate(works):
        link("doi:" + w.doi if w.doi else None, i)
        link("arxiv:" + w.arxiv_id if w.arxiv_id else None, i)
        link("oa:" + w.openalex_id if w.openalex_id else None, i)
        for v in w.versions:
            link("doi:" + norm_doi(v.get("doi")) if v.get("doi") else None, i)
            link("arxiv:" + norm_arxiv(v.get("arxiv_id")) if v.get("arxiv_id") else None, i)
    # 标题近似（O(n^2)，候选数量有限）
    for i in range(n):
        for j in range(i + 1, n):
            if uf.find(i) != uf.find(j) and _same_work_by_title(works[i], works[j]):
                uf.union(i, j)

    clusters: dict[int, list[Work]] = {}
    order: list[int] = []
    for i, w in enumerate(works):
        r = uf.find(i)
        if r not in clusters:
            clusters[r] = []
            order.append(r)
        clusters[r].append(w)
    return [_merge_cluster(clusters[r]) for r in order]


def _pick_primary(items: list[Work]) -> Work:
    def rank(w: Work) -> tuple:
        return (STATUS_ORDER.get(w.status, 0), bool(w.doi and not (w.doi or "").startswith("10.48550/")),
                bool(w.abstract), w.citations or 0)

    return max(items, key=rank)


def is_truncated(text: str | None) -> bool:
    """摘要被数据源截断了（以省略号结尾）。有些出版商不允许数据库转发完整摘要，只给开头一段。"""
    t = (text or "").rstrip()
    return t.endswith(("…", "...")) or t.endswith(("…", " ..."))


def best_abstract(items: list[Work]) -> str | None:
    """同一篇论文的各来源给了不同的摘要：选没被截断的，都完整就选最长的。"""
    cands = [w.abstract for w in items if w.abstract]
    if not cands:
        return None
    return max(cands, key=lambda a: (not is_truncated(a), len(a)))


def _merge_cluster(items: list[Work]) -> Work:
    if len(items) == 1:
        return items[0]
    primary = _pick_primary(items)
    merged = Work(**{**primary.__dict__})  # 浅拷贝后再合并集合字段
    merged.sources = list(primary.sources)
    merged.versions = list(primary.versions)
    merged.authors = list(primary.authors)
    merged.topics = list(primary.topics)
    merged.source_rank = dict(primary.source_rank)
    seen_versions = {(v.get("kind"), v.get("doi"), v.get("arxiv_id"), v.get("url")) for v in merged.versions}
    for w in items:
        if w is primary:
            continue
        # 其它记录的来源
        for s in w.sources:
            if s not in merged.sources:
                merged.sources.append(s)
        for src, rk in w.source_rank.items():
            merged.source_rank[src] = min(rk, merged.source_rank.get(src, rk))
        # 补全缺失字段
        for attr in ("abstract", "venue", "venue_type", "doi", "arxiv_id", "pmid", "url", "pdf_url", "year", "date",
                     "field_name", "field_id", "domain", "openalex_id", "open_access", "core_venue", "percentile",
                     "fwci", "work_type"):
            if getattr(merged, attr) in (None, "") and getattr(w, attr) not in (None, ""):
                setattr(merged, attr, getattr(w, attr))
        if not merged.authors and w.authors:
            merged.authors = list(w.authors)
        if w.citations is not None:
            merged.citations = max(merged.citations or 0, w.citations)
        if not merged.referenced_works:
            merged.referenced_works = w.referenced_works
        if not merged.related_works:
            merged.related_works = w.related_works
        for t in w.topics:
            if t not in merged.topics:
                merged.topics.append(t)
        # 不同发表状态 → 作为“其它版本”保留
        if w.status != merged.status or (w.doi and w.doi != merged.doi):
            v = {"kind": w.status if w.status in ("preprint", "published", "accepted") else "other",
                 "doi": w.doi, "arxiv_id": w.arxiv_id, "url": w.url, "venue": w.venue}
            sig = (v["kind"], v["doi"], v["arxiv_id"], v["url"])
            if sig not in seen_versions:
                seen_versions.add(sig)
                merged.versions.append(v)
        for v in w.versions:
            sig = (v.get("kind"), v.get("doi"), v.get("arxiv_id"), v.get("url"))
            if sig not in seen_versions:
                seen_versions.add(sig)
                merged.versions.append(v)
    # 版本里不应包含主记录自己
    merged.versions = [v for v in merged.versions
                       if not ((v.get("doi") and v.get("doi") == merged.doi) or
                               (v.get("arxiv_id") and v.get("arxiv_id") == merged.arxiv_id and merged.status == "preprint"))]
    # 主记录是正式发表而存在预印本版本时，补充一句说明
    if merged.status == "published" and any(v.get("kind") == "preprint" for v in merged.versions):
        merged.status_evidence += "；另有预印本版本"
    merged.abstract = best_abstract(items) or merged.abstract  # 主记录的摘要被截断时，换成别的来源给的完整版
    return merged
