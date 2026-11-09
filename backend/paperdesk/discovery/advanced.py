"""Scopus 式的高级检索：按字段（标题/作者/摘要/期刊/DOI…）逐行写检索式，行与行用 且 / 或 / 非 连接。

语义：从上到下读，“或”把检索式分成几个分支，分支内“且 / 非”依次收窄；各分支分别去 OpenAlex 取，再合并去重。
- 标题、摘要、标题+摘要 的 “非” 直接写进 OpenAlex 的检索式；作者、期刊的 “非” 在取回的结果上本地过滤；
- “标题 + 精确匹配”：整句加引号检索，再核对标题是否逐字相同（忽略大小写和标点），完全相同的排在最前并标出——用来直接找一篇已知标题的论文。
不使用 AI，只查公开元数据。
"""
from __future__ import annotations

import asyncio
import difflib
import re
from dataclasses import dataclass

from ..context import AppContext, redact_url_keys
from ..util import ApiError
from . import sources as S
from .dedupe import merge_works
from .models import Work, norm_doi, norm_title
from .search import http_client, mark_in_library

FIELDS = ("all", "title", "author", "abstract", "tak", "venue", "doi")
FIELD_LABEL = {"all": "全部字段", "title": "标题", "author": "作者", "abstract": "摘要", "tak": "标题/摘要", "venue": "期刊/会议", "doi": "DOI"}
SEARCH_FILTER = {"title": "title.search", "abstract": "abstract.search", "tak": "title_and_abstract.search", "author": "raw_author_name.search"}
TEXT_NOT_FIELDS = {"title", "abstract", "tak"}  # “非”能直接写进检索式的字段
MAX_ROWS = 8
NEAR_TITLE = 0.82  # 标题相似度（0–1）达到这个值，认为是“要找的那篇”


@dataclass
class Row:
    field: str
    term: str
    op: str = "and"  # and | or | not（第一行的 op 不起作用）
    exact: bool = False


def clean_term(term: str) -> str:
    # 去掉会破坏过滤器语法的字符（逗号分隔过滤器、竖线表示“或”）
    return re.sub(r"[,|]+", " ", term or "").strip()


def _quote(row: Row) -> str:
    t = clean_term(row.term).replace('"', "")
    if row.exact:
        return f'"{t}"'
    return f"({t})" if " " in t else t


def branches(rows: list[Row]) -> list[list[Row]]:
    """按 “或” 切成分支；分支内第一行当作 “且”。"""
    out: list[list[Row]] = []
    cur: list[Row] = []
    for i, r in enumerate(rows):
        if i > 0 and r.op == "or" and cur:
            out.append(cur)
            cur = []
        cur.append(Row(r.field, r.term, "and" if r.op == "or" else (r.op if i > 0 else "and"), r.exact))
    if cur:
        out.append(cur)
    return out


async def _venue_filter(client, names: list[str]) -> tuple[str | None, list[str]]:
    """期刊/会议名 → OpenAlex 来源 id（取最匹配的几个）。"""
    ids: list[str] = []
    for n in names:
        try:
            hits = await S.fetch_openalex_sources(client, n, limit=5)
        except S.SourceError:
            continue
        ids += [h["id"].rsplit("/", 1)[-1] for h in hits if h.get("id")][:3]
    ids = list(dict.fromkeys(ids))
    return ("primary_location.source.id:" + "|".join(ids[:8])) if ids else None, ids


def _local_text(w: Work, field: str) -> str:
    if field == "title":
        return w.title
    if field == "author":
        return " ".join(w.authors)
    if field == "abstract":
        return w.abstract or ""
    if field == "venue":
        return w.venue or ""
    if field == "doi":
        return w.doi or ""
    if field == "tak":
        return f"{w.title} {w.abstract or ''}"
    return f"{w.title} {w.abstract or ''} {' '.join(w.authors)} {w.venue or ''}"


def _norm_text(s: str) -> str:
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


async def _run_branch(client, rows: list[Row], year_from, year_to, sort, limit) -> tuple[list[Work], list[Row], str | None]:
    """返回 (取回的论文, 需要在本地过滤的“非”行, 提示)。"""
    pos = [r for r in rows if r.op != "not" and clean_term(r.term)]
    neg = [r for r in rows if r.op == "not" and clean_term(r.term)]
    if not pos:
        raise ApiError("bad_request", "每个“或”分支至少要有一个肯定的检索词（只有“非”没法检索）。")
    filters: list[str] = []
    query: str | None = None
    note: str | None = None
    local_neg: list[Row] = []
    by_field: dict[str, list[Row]] = {}
    for r in pos:
        by_field.setdefault(r.field, []).append(r)
    for field_, rs in by_field.items():
        if field_ in SEARCH_FILTER:
            expr = " AND ".join(_quote(r) for r in rs)
            if field_ in TEXT_NOT_FIELDS:
                for n in neg:
                    if n.field == field_:
                        expr += f' NOT "{clean_term(n.term)}"'
            filters.append(f"{SEARCH_FILTER[field_]}:{expr}")
        elif field_ == "all":
            query = " AND ".join(_quote(r) for r in rs)
        elif field_ == "doi":
            dois = [norm_doi(clean_term(r.term)) for r in rs]
            dois = [d for d in dois if d]
            if dois:
                filters.append("doi:" + "|".join(f"https://doi.org/{d}" for d in dois))
        elif field_ == "venue":
            flt, ids = await _venue_filter(client, [clean_term(r.term) for r in rs])
            if flt:
                filters.append(flt)
            else:
                note = f"没有找到名为“{'、'.join(clean_term(r.term) for r in rs)}”的期刊或会议。"
    for n in neg:
        if n.field in TEXT_NOT_FIELDS and any(n.field == f for f in by_field):
            continue  # 已写进检索式
        local_neg.append(n)
    if not filters and not query:
        raise ApiError("bad_request", note or "没有可用的检索条件。")
    works = await S.fetch_openalex(client, query, limit=limit, sort=sort, year_from=year_from, year_to=year_to, filters=filters)
    # 找一篇标题大致记得的论文：“title.search” 要求每个词都在标题里，多打、少打、打错一个词（your / you）就一篇也没有；
    # 再按相关度取一份（OpenAlex 会做词干和近似），后面按标题相似度排序
    for r in by_field.get("title", []):
        if not r.exact and len(clean_term(r.term).split()) >= 3:
            others = [f for f in filters if not f.startswith("title.search:")]
            term = clean_term(r.term)
            try:
                works += await S.fetch_openalex(client, term, limit=25, year_from=year_from, year_to=year_to, filters=others)
            except S.SourceError:
                pass
            # 还是没有相似的标题：多半有个词打错了（your / you）。每次去掉一个词再按标题检索，错的那个词不在时就能找到
            words = term.split()
            if 3 <= len(words) <= 8 and max((_sim(term, w.title) for w in works), default=0) < 0.95:
                tries = [" ".join(words[:i] + words[i + 1:]) for i in range(len(words))]
                got = await asyncio.gather(*(S.fetch_openalex(client, None, limit=10, year_from=year_from, year_to=year_to,
                                                              filters=[*others, f"title.search:({t})"]) for t in tries), return_exceptions=True)
                for g in got:
                    if isinstance(g, list):
                        works += g
    return works, local_neg, note


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def _excluded(w: Work, neg: list[Row]) -> bool:
    for n in neg:
        t = _norm_text(clean_term(n.term))
        if t and t in _norm_text(_local_text(w, n.field)):
            return True
    return False


async def run_advanced(app: AppContext, rows: list[Row], *, year_from: int | None = None, year_to: int | None = None,
                       sort: str = "relevance", limit: int = 50) -> dict:
    rows = [r for r in rows if r.field in FIELDS and clean_term(r.term)][:MAX_ROWS]
    if not rows:
        raise ApiError("bad_request", "请至少输入一个检索词。")
    limit = max(1, min(200, limit))
    oa_sort = {"cited": "cited_by_count:desc", "date": "publication_date:desc", "relevance": None}.get(sort)
    warnings: list[str] = []
    parts = branches(rows)
    async with http_client(app) as client:
        try:
            got = await asyncio.gather(*(_run_branch(client, b, year_from, year_to, oa_sort, limit) for b in parts))
        except S.SourceError as exc:
            raise ApiError("network", f"OpenAlex 检索失败：{redact_url_keys(exc)}", 502) from exc
    works: list[Work] = []
    for ws, neg, note in got:
        if note:
            warnings.append(note)
        works += [w for w in ws if not _excluded(w, neg)]
    merged = merge_works(works)
    mark_in_library(app, merged)
    # 标题“精确匹配”：核对标题逐字相同的排最前并标出
    exact_titles = [norm_title(clean_term(r.term)) for r in rows if r.field == "title" and r.exact and r.op != "not"]
    for w in merged:
        if exact_titles and norm_title(w.title) in exact_titles:
            w.reason, w.reason_source = "标题与检索词完全一致", "rule"
            w.score = 1e9 + (w.citations or 0)  # 完全相同的标题里，被引多的（通常是正式版本）排前面
    near_titles = [norm_title(clean_term(r.term)) for r in rows if r.field == "title" and not r.exact and r.op != "not" and len(clean_term(r.term).split()) >= 3]
    for w in merged:
        if w.score >= 1e9 or not near_titles:
            continue
        sim = max(difflib.SequenceMatcher(None, t, norm_title(w.title)).ratio() for t in near_titles)
        if sim >= NEAR_TITLE:
            w.reason, w.reason_source = f"标题与检索词高度相似（{round(sim * 100)}%）", "rule"
            w.score = 1e8 + sim * 1e6 + min(w.citations or 0, 9e5) / 10
    if oa_sort is None:
        merged.sort(key=lambda w: -w.score)  # 没有精确匹配时 score 都是 0，稳定排序保持 OpenAlex 的相关度顺序
    elif sort == "cited":
        merged.sort(key=lambda w: (-w.score, -(w.citations or 0)))
    else:
        merged.sort(key=lambda w: (-w.score, -(w.year or 0)))
    return {
        "rows": [{"field": r.field, "term": r.term, "op": r.op, "exact": r.exact} for r in rows], "branches": len(parts),
        "results": [w.to_dict() for w in merged], "total_candidates": sum(len(ws) for ws, _, _ in got), "after_dedupe": len(merged),
        "warnings": warnings, "exact_matches": sum(1 for w in merged if w.score >= 1e9),
    }
