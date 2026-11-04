"""检索编排：规划 → 并行查询多来源 → 去重/版本关联 → 文库匹配 → 语义（bge-m3）+ LLM 重排 → 综合排序 → 推荐理由。

任一来源失败都只降级并在响应的 sources/warnings 里告知，不影响其它来源。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date

import httpx

from ..ai.providers import LLMError
from ..context import AppContext, redact_url_keys
from ..llmjson import ask_json, llm_available
from ..util import ApiError
from . import sources as S
from .dedupe import merge_works
from .embed import cosine, embed_texts, sim_to_unit
from .models import Work, norm_arxiv, norm_doi, norm_title
from .planner import Plan, choose_sources, make_plan
from .ranking import diversify, query_terms, rule_reason, score_works

MODES = ("comprehensive", "recent", "impact", "related", "cross")
# 各模式要求的最低词面相关性（命中查询词的比例）：按引用/时间排序的模式最容易混入“热门但无关”的论文，门槛更高
RELEVANCE_FLOOR = {"recent": 0.34, "impact": 0.34, "cross": 0.2, "related": 0.2}
SOURCE_LABELS = {"openalex": "OpenAlex", "crossref": "Crossref", "s2": "Semantic Scholar", "arxiv": "arXiv",
                 "europepmc": "Europe PMC"}
GENERIC_REASONS = ("与你的兴趣相关", "与您的兴趣相关", "值得一读", "非常相关")

RERANK_SYSTEM = (
    "你是学术检索助手。给定用户的检索问题和若干候选论文（标题+摘要片段），为每篇评估与问题的相关程度，并写一句具体的推荐理由。\n"
    "要求：理由必须引用论文摘要里的具体内容（方法、研究对象或结论），不能写“与你的兴趣相关”之类的空话，"
    "不得编造摘要里没有的信息；摘要缺失时写“仅有标题，相关性依据有限”。\n"
    '输出一个 JSON 对象：{"results": [{"i": 序号, "rel": 0-10 的整数, "reason": "一句话中文理由，不超过 60 字"}, ...]}，'
    '必须覆盖所有候选论文（每篇一项）。只输出 JSON。'
)


def http_client(app: AppContext, timeout: float = 20.0) -> httpx.AsyncClient:
    return app.async_http(timeout)


async def _run_source(name: str, coro) -> tuple[list[Work], dict]:
    t0 = time.perf_counter()
    try:
        works = await coro
        for i, w in enumerate(works):
            w.source_rank[SOURCE_LABELS[name]] = i
        return works, {"name": SOURCE_LABELS[name], "ok": True, "count": len(works), "ms": int((time.perf_counter() - t0) * 1000), "error": None}
    except S.SourceError as exc:
        return [], {"name": SOURCE_LABELS[name], "ok": False, "count": 0, "ms": int((time.perf_counter() - t0) * 1000), "error": redact_url_keys(exc)}
    except Exception as exc:  # noqa: BLE001  任何来源的意外错误都不能拖垮整个检索
        return [], {"name": SOURCE_LABELS[name], "ok": False, "count": 0, "ms": int((time.perf_counter() - t0) * 1000),
                    "error": f"{SOURCE_LABELS[name]} 出错"}


def library_index(app: AppContext) -> dict:
    with app.db.session() as conn:
        rows = conn.execute("SELECT id, title, doi, arxiv_id FROM papers WHERE COALESCE(role,'paper') = 'paper'").fetchall() \
            if _has_role(app) else conn.execute("SELECT id, title, doi, arxiv_id FROM papers").fetchall()
    idx: dict = {"doi": {}, "arxiv": {}, "title": {}}
    for r in rows:
        if r["doi"]:
            idx["doi"][norm_doi(r["doi"])] = r["id"]
        if r["arxiv_id"]:
            idx["arxiv"][norm_arxiv(r["arxiv_id"])] = r["id"]
        if r["title"]:
            idx["title"][norm_title(r["title"])] = r["id"]
    return idx


def _has_role(app: AppContext) -> bool:
    with app.db.session() as conn:
        return "role" in {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}


def mark_in_library(app: AppContext, works: list[Work]) -> None:
    idx = library_index(app)
    for w in works:
        ids = [idx["doi"].get(w.doi)] if w.doi else []
        for v in w.versions:
            if v.get("doi"):
                ids.append(idx["doi"].get(norm_doi(v["doi"])))
            if v.get("arxiv_id"):
                ids.append(idx["arxiv"].get(norm_arxiv(v["arxiv_id"])))
        if w.arxiv_id:
            ids.append(idx["arxiv"].get(w.arxiv_id))
        ids.append(idx["title"].get(norm_title(w.title)) if len(norm_title(w.title)) > 12 else None)
        w.in_library = next((i for i in ids if i), None)


def _fmt_for_llm(i: int, w: Work) -> str:
    abs_ = (w.abstract or "")[:450]
    return f"[{i}] {w.title}（{w.year or '年份未知'}，{w.venue or '来源未知'}）摘要：{abs_ or '（无摘要）'}"


async def llm_rerank(app: AppContext, query: str, works: list[Work]) -> dict[str, tuple[float, str]]:
    """一次调用同时给出相关度(0-10)与具体理由。返回 {key: (rel01, reason)}；失败返回空。"""
    if not works or not llm_available(app):
        return {}
    user = f"检索问题：{query}\n\n候选论文：\n" + "\n".join(_fmt_for_llm(i, w) for i, w in enumerate(works))
    try:
        data = await ask_json(
            app, system=RERANK_SYSTEM, user=user, task_type="discovery_rerank", tier="fast", max_tokens=2000,
            summary=f"发送了检索问题与 {len(works)} 篇候选论文的标题和摘要片段（均为公开元数据）",
        )
    except LLMError:
        return {}
    out: dict[str, tuple[float, str]] = {}
    if isinstance(data, dict):
        data = data.get("results") or data.get("items") or []
    for item in data if isinstance(data, list) else []:
        try:
            i = int(item["i"])
            rel = max(0, min(10, float(item.get("rel", 5)))) / 10
        except (KeyError, TypeError, ValueError):
            continue
        reason = str(item.get("reason") or "").strip()
        if 0 <= i < len(works):
            if any(g in reason for g in GENERIC_REASONS) or len(reason) < 8:
                reason = ""
            out[works[i].key] = (rel, reason[:120])
    return out


async def semantic_scores(app: AppContext, query: str, works: list[Work]) -> dict[str, float] | None:
    texts = [query] + [f"{w.title}. {(w.abstract or '')[:600]}" for w in works]
    vecs = await embed_texts(app, texts)
    if not vecs:
        return None
    return {w.key: sim_to_unit(cosine(vecs[0], vecs[i + 1])) for i, w in enumerate(works)}


# ---------------- 各模式的来源查询 ----------------
def _filters_for(mode: str) -> tuple[str | None, str | None, int | None]:
    """返回 (OpenAlex 排序, Crossref/EPMC 排序, 年份下限)。"""
    this_year = date.today().year
    if mode == "recent":
        return "publication_date:desc", "date", this_year - 2
    if mode == "impact":
        return "cited_by_count:desc", "cited", None
    return None, None, None


async def _gather_sources(app: AppContext, client: httpx.AsyncClient, plan: Plan, mode: str, names: list[str],
                          year_from: int | None, year_to: int | None, limit: int) -> tuple[list[Work], list[dict]]:
    oa_sort, other_sort, mode_year = _filters_for(mode)
    year_from = year_from or mode_year
    q = plan.english_query or plan.query
    tasks: dict[str, list] = {}
    pool = max(limit * 2, 30)
    if "openalex" in names:
        tasks.setdefault("openalex", []).append(S.fetch_openalex(client, q, limit=pool, sort=oa_sort, year_from=year_from, year_to=year_to))
        if mode in ("impact", "recent"):  # 影响力/最新模式：再取一份按相关性的，避免只有“被引高/最新但不太相关”的
            tasks["openalex"].append(S.fetch_openalex(client, q, limit=pool, year_from=year_from, year_to=year_to))
        elif mode == "comprehensive" and plan.alt_queries:
            tasks["openalex"].append(S.fetch_openalex(client, plan.alt_queries[0], limit=pool // 2, year_from=year_from, year_to=year_to))
    if "crossref" in names:
        tasks["crossref"] = [S.fetch_crossref(client, q, limit=pool // 2, sort=other_sort, year_from=year_from, year_to=year_to)]
        if mode == "recent":
            tasks["crossref"].append(S.fetch_crossref(client, q, limit=pool // 2, year_from=year_from, year_to=year_to))
    if "s2" in names:
        tasks["s2"] = [S.fetch_s2(client, q, limit=pool // 2, year_from=year_from)]
    if "arxiv" in names:
        terms = plan.keywords[:4] or query_terms(q)[:4]
        tasks["arxiv"] = [S.fetch_arxiv(client, terms, limit=pool // 2, sort="date" if mode == "recent" else "relevance")]
    if "europepmc" in names:
        tasks["europepmc"] = [S.fetch_epmc(client, q, limit=pool // 2, sort=other_sort, year_from=year_from)]

    async def one(name: str, coros: list) -> tuple[list[Work], dict]:
        results = await asyncio.gather(*(_run_source(name, c) for c in coros))
        works = [w for ws, _ in results for w in ws]
        reports = [r for _, r in results]
        ok = [r for r in reports if r["ok"]]
        rep = {"name": SOURCE_LABELS[name], "ok": bool(ok), "count": sum(r["count"] for r in reports),
               "ms": max(r["ms"] for r in reports), "error": None if ok else reports[0]["error"]}
        return works, rep

    done = await asyncio.gather(*(one(n, c) for n, c in tasks.items()))
    works = [w for ws, _ in done for w in ws]
    return works, [r for _, r in done]


async def _related_candidates(client: httpx.AsyncClient, seed: dict | None, plan: Plan) -> tuple[Work | None, list[Work], dict[str, str]]:
    """相关工作：以一篇论文为基准，取 OpenAlex 的 related / 参考文献 / 施引文献。"""
    seed_work: Work | None = None
    if seed and seed.get("doi"):
        seed_work = await S.fetch_openalex_by_doi(client, norm_doi(seed["doi"]) or "")
    if seed_work is None:
        hits = await S.fetch_openalex(client, (seed or {}).get("title") or plan.english_query or plan.query, limit=5)
        seed_work = hits[0] if hits else None
    if seed_work is None or not seed_work.openalex_id:
        return None, [], {}
    relation: dict[str, str] = {}
    ids = seed_work.related_works[:20]
    refs = seed_work.referenced_works[:25]
    fetched = await S.fetch_openalex_by_ids(client, ids + refs)
    cited_by = await S.fetch_openalex_citing(client, seed_work.openalex_id, 25)
    ref_set = set(refs)
    out: list[Work] = []
    for w in fetched:
        relation[w.key] = "被该论文引用" if w.openalex_id in ref_set else "OpenAlex 判定的相关工作"
        out.append(w)
    for w in cited_by:
        relation.setdefault(w.key, "引用了该论文")
        out.append(w)
    return seed_work, out, relation


async def _cross_candidates(client: httpx.AsyncClient, plan: Plan, base: list[Work], pool: int, year_from: int | None) -> list[Work]:
    """跨学科：找出基础结果的主导学科，用类比检索串在“其它学科”里检索。"""
    counts: dict[str, int] = {}
    for w in base:
        if w.field_id:
            counts[w.field_id] = counts.get(w.field_id, 0) + 1
    dominant = max(counts, key=counts.get) if counts else None
    queries = plan.analogies or plan.alt_queries[:2] or [plan.english_query or plan.query]
    out: list[Work] = []
    for q in queries[:3]:
        flt = [f"primary_topic.field.id:!fields/{dominant}"] if dominant else []
        try:
            out.extend(await S.fetch_openalex(client, q, limit=pool // 2, filters=flt, year_from=year_from))
        except S.SourceError:
            continue
    return out


# ---------------- 主入口 ----------------
async def run_search(
    app: AppContext, query: str, *, mode: str = "comprehensive", year_from: int | None = None, year_to: int | None = None,
    limit: int = 20, seed: dict | None = None, use_ai: bool = True, plan: Plan | None = None,
) -> dict:
    query = (query or "").strip()
    if mode not in MODES:
        raise ApiError("bad_request", f"不支持的检索模式：{mode}")
    if not query and not seed:
        raise ApiError("bad_request", "请输入检索内容")
    limit = max(1, min(100, limit))  # 每个来源取多少（决定候选池大小）；去重排序后的结果不再截断
    plan = plan or await make_plan(app, query or (seed or {}).get("title") or "", mode, use_ai)
    names = choose_sources(plan, mode)
    # 相关工作模式以论文而不是文字检索：没有输入框文字时，“检索词不含英文”之类的提示没有意义
    warnings: list[str] = [] if (mode == "related" and not query) else list(plan.notes)
    reports: list[dict] = []
    seed_info = None
    relation: dict[str, str] = {}

    async with http_client(app) as client:
        if mode == "related":
            try:
                seed_work, cands, relation = await _related_candidates(client, seed, plan)
            except S.SourceError as exc:
                raise ApiError("network", f"无法获取相关工作：{redact_url_keys(exc)}", 502) from exc
            if seed_work is None:
                raise ApiError("not_found", "没有找到可作为基准的论文，请换一个更具体的标题或 DOI。", 404)
            seed_info = {"title": seed_work.title, "doi": seed_work.doi, "year": seed_work.year, "openalex_id": seed_work.openalex_id}
            reports.append({"name": "OpenAlex 相关工作", "ok": True, "count": len(cands), "ms": 0, "error": None})
            works = cands
            query_for_rank = f"{seed_work.title}. {(seed_work.abstract or '')[:300]}"
        else:
            works, reports = await _gather_sources(app, client, plan, mode, names, year_from, year_to, limit)
            query_for_rank = (plan.english_query + " " + " ".join(plan.keywords)).strip() or query
            if mode == "cross":
                base_sorted = sorted([w for w in works if w.field_id], key=lambda w: -(w.citations or 0))[:20]
                extra = await _cross_candidates(client, plan, base_sorted or works, max(limit * 2, 30), year_from)
                reports.append({"name": "OpenAlex 跨学科检索", "ok": bool(extra), "count": len(extra), "ms": 0,
                                "error": None if extra else "没有找到其它学科的类比结果"})
                works = extra + works  # 类比结果优先参与排序
                if not plan.analogies:
                    warnings.append("跨学科探索需要 AI 提出其它学科的类比概念；当前没有使用 AI，只是排除了主导学科后重新检索，结果可能与综合搜索接近。")
    failed = [r for r in reports if not r["ok"]]
    for r in failed:
        warnings.append(f"{r['name']}：{r['error']}")
    if reports and not any(r["ok"] for r in reports):
        raise ApiError("network", "所有学术数据源都不可用，请检查网络后重试。", 502)

    total = len(works)
    merged = merge_works(works)
    for w in merged:
        if w.key in relation:
            w.relation = relation[w.key]
    mark_in_library(app, merged)

    # 排序：先用词面+来源名次粗排，对前 40 做语义相似度，再对前 15 做 LLM 重排，最后综合评分
    rank_query = query_for_rank
    score_works(merged, query=rank_query, mode=mode)
    merged.sort(key=lambda w: -w.score)
    ai_flags = {"planner": plan.ai_used, "embeddings": False, "rerank": False}
    semantic = await semantic_scores(app, rank_query, merged[:40]) if use_ai else None
    if semantic:
        ai_flags["embeddings"] = True
        score_works(merged, query=rank_query, semantic=semantic, mode=mode)
        merged.sort(key=lambda w: -w.score)
    elif use_ai and llm_available(app):
        warnings.append("向量嵌入不可用，已使用词面匹配做相关性。")

    llm = await llm_rerank(app, query or rank_query, merged[:15]) if use_ai else {}
    if llm:
        ai_flags["rerank"] = True
        score_works(merged, query=rank_query, semantic=semantic, mode=mode,
                    llm_relevance={k: v[0] for k, v in llm.items()})
    if mode in RELEVANCE_FLOOR:
        # 影响力/最新/跨学科/相关工作模式下过滤相关性过低的候选，避免“很多人引用但与问题无关”“新但无关”的结果。
        # 闸门只看词面/语义相关性（不含来源名次先验，那个先验对“按引用/时间排序”的结果没有意义）
        floor = RELEVANCE_FLOOR[mode]
        before_floor = merged
        merged = [w for w in merged if w.breakdown.get("lexical", 0) >= floor or w.breakdown.get("semantic", 0) >= 0.4 or w.key in relation]
        if before_floor and not merged:
            # 闸门把候选全部过滤掉：宁可给出“相关性不高”的结果并写明，也不返回空
            merged = before_floor
            warnings.append("没有候选通过相关性筛选，已改为按综合评分显示前几条，相关性可能不高，请自行判断。")
    ranked = diversify(merged, limit) if mode == "cross" else sorted(merged, key=lambda w: -w.score)
    for w in ranked:
        reason = llm.get(w.key, (0, ""))[1]
        if reason:
            w.reason, w.reason_source = reason, "ai"
        else:
            w.reason, w.reason_source = rule_reason(w, query), "rule"
        if w.relation and w.reason_source == "rule":
            w.reason = f"{w.relation}；" + w.reason
    return {
        "query": query, "mode": mode, "plan": plan.to_dict(), "results": [w.to_dict() for w in ranked],
        "sources": reports, "warnings": warnings, "ai": ai_flags, "total_candidates": total,
        "after_dedupe": len(merged), "seed": seed_info,
    }
