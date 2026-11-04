"""论文推荐：研究方向推荐、个性化推荐、随机探索（只限白名单顶刊顶会）、相似论文。"""
from __future__ import annotations

import asyncio
import random
import re
from collections import Counter
from datetime import date

from ..ai.providers import LLMError
from ..context import AppContext
from ..llmjson import ask_json, llm_available
from ..util import ApiError, loads
from . import sources as S
from .dedupe import merge_works
from .models import Work
from .planner import Plan, make_plan
from .ranking import (
    STOP, domain_group, impact_score, quality_score, query_terms, recency_score, rule_reason, score_works,
)
from .search import (
    _related_candidates, http_client, mark_in_library, run_search,
)
from .roadmap import build_roadmap
from .venue_sources import resolve_many
from .venues import match_venue, venues_for_field

# OpenAlex 的 26 个一级学科（全学科随机探索按学科均匀抽样，而不是按论文数量，避免被医学/计算机淹没）
FIELDS = {
    11: ("Agricultural and Biological Sciences", "农业与生物科学"), 12: ("Arts and Humanities", "艺术与人文"),
    13: ("Biochemistry, Genetics and Molecular Biology", "生物化学、遗传学与分子生物学"),
    14: ("Business, Management and Accounting", "商业、管理与会计"), 15: ("Chemical Engineering", "化学工程"),
    16: ("Chemistry", "化学"), 17: ("Computer Science", "计算机科学"), 18: ("Decision Sciences", "决策科学"),
    19: ("Earth and Planetary Sciences", "地球与行星科学"), 20: ("Economics, Econometrics and Finance", "经济学与金融"),
    21: ("Energy", "能源"), 22: ("Engineering", "工程学"), 23: ("Environmental Science", "环境科学"),
    24: ("Immunology and Microbiology", "免疫学与微生物学"), 25: ("Materials Science", "材料科学"),
    26: ("Mathematics", "数学"), 27: ("Medicine", "医学"), 28: ("Neuroscience", "神经科学"), 29: ("Nursing", "护理学"),
    30: ("Pharmacology, Toxicology and Pharmaceutics", "药理学与毒理学"), 31: ("Physics and Astronomy", "物理与天文学"),
    32: ("Psychology", "心理学"), 33: ("Social Sciences", "社会科学"), 34: ("Veterinary", "兽医学"),
    35: ("Dentistry", "牙医学"), 36: ("Health Professions", "健康专业"),
}
FIELD_ZH = {en: zh for en, zh in FIELDS.values()}

SURVEY_RE = re.compile(r"\b(survey|review|overview|tutorial|perspective|roadmap|state[- ]of[- ]the[- ]art)\b|综述|述评", re.I)


# ======================= 研究方向推荐 =======================
LEVELS = ("any", "peer", "core", "top")
LEVEL_ZH = {"any": "不限", "peer": "已发表（同行评审）", "core": "核心来源及以上", "top": "仅顶刊顶会"}


def passes_filters(w: dict, year_from: int | None, year_to: int | None, level: str) -> bool:
    """推荐结果的用户筛选：年份区间与来源水平。水平只认可核对的事实：名单匹配、OpenAlex 核心来源标记、同行评审标记。"""
    y = w.get("year")
    if (year_from or year_to) and not y:
        return False
    if year_from and y < year_from or year_to and y > year_to:
        return False
    if level == "any":
        return True
    if w.get("status") == "preprint":
        return False
    v = match_venue(w.get("venue"), w.get("venue_type"), loose=False)
    if v is not None:
        w["top_venue"] = {"id": v.id, "name": w.get("venue") or v.name, "short": v.short, "kind": v.kind,
                          "category": v.category, "label": v.label}
    if level == "top":
        return v is not None and v.level == "top"
    if level == "core":
        return v is not None or bool(w.get("core_venue"))
    return v is not None or w.get("peer_reviewed") is True or bool(w.get("core_venue"))  # peer


async def recommend_direction(app: AppContext, topic: str, use_ai: bool = True, *, year_from: int | None = None,
                              year_to: int | None = None, level: str = "any", exclude: list[str] | None = None) -> dict:
    topic = topic.strip()
    if not topic:
        raise ApiError("bad_request", "请输入研究主题")
    level = level if level in LEVELS else "any"
    skip = set(exclude or [])
    filtered = bool(year_from or year_to) or level != "any"
    plan = await make_plan(app, topic, "comprehensive", use_ai)
    # 有筛选或在“再推荐”时多取一些候选：筛掉一部分、排除已看过的之后仍要有足够的结果
    big = 2 if filtered or skip else 1
    base = await run_search(app, topic, mode="comprehensive", limit=30 * big, use_ai=use_ai, plan=plan, year_from=year_from, year_to=year_to)
    recent = await run_search(app, topic, mode="recent", limit=12 * big, use_ai=use_ai, plan=plan, year_from=year_from, year_to=year_to)
    impact = await run_search(app, topic, mode="impact", limit=12 * big, use_ai=use_ai, plan=plan, year_from=year_from, year_to=year_to)
    seen: set[str] = set(skip)
    pool: list[dict] = []
    for r in (base, impact, recent):
        for w in r["results"]:
            if w["key"] not in seen and passes_filters(w, year_from, year_to, level):
                seen.add(w["key"])
                pool.append(w)
    year = date.today().year
    surveys = [w for w in pool if SURVEY_RE.search(w["title"]) or w.get("work_type") == "review"][:6]
    ids = {w["key"] for w in surveys}
    classics = sorted(
        [w for w in pool if w["key"] not in ids and w["year"] and year - w["year"] >= 4 and
         (w["percentile"] or 0) >= 0.9 or (w["fwci"] or 0) >= 3 and w["key"] not in ids and w["year"] and year - w["year"] >= 4],
        key=lambda w: -w["breakdown"].get("impact", 0))[:6]
    ids |= {w["key"] for w in classics}
    latest = sorted([w for w in pool if w["key"] not in ids and w["year"] and year - w["year"] <= 2],
                    key=lambda w: -w["score"])[:6]
    # 学习路线：AI 顺着方向排出由浅入深的必读论文（逐篇回 OpenAlex 核对过），放在最前面；后面的分组去掉已在路线里的
    notes: list[str] = []
    road: dict = {"stages": []}
    if use_ai and not skip and llm_available(app):  # “再推荐”时不重排路线（已经在界面上了）
        road = await build_roadmap(app, topic, notes)
    on_road = {w["key"] for st in road["stages"] for w in st["items"]}
    rest = [
        {"id": "surveys", "title": "更多综述与导览", "hint": "先读它们建立全局认识", "items": [w for w in surveys if w["key"] not in on_road]},
        {"id": "classics", "title": "更多领域经典", "hint": "发表时间较早、同领域同年份引用位于前列", "items": [w for w in classics if w["key"] not in on_road]},
        {"id": "recent", "title": "近期进展", "hint": "近两年发表、质量较好", "items": [w for w in latest if w["key"] not in on_road]},
    ]
    if not road["stages"]:  # 没有路线（AI 没开或失败）：保持原来的标题
        rest[0]["title"], rest[1]["title"] = "综述与导览", "领域经典"
    return {
        "topic": topic, "plan": base["plan"],
        "groups": road["stages"] + [g for g in rest if g["items"]],
        "sources": base["sources"], "warnings": list(dict.fromkeys(base["warnings"] + recent["warnings"] + impact["warnings"] + notes)),
        "ai": base["ai"],
    }


# ======================= 个性化推荐 =======================
INTEREST_SYSTEM = (
    "你是科研阅读助手。根据用户文献库中的论文标题（带“收藏/最近读”标记）、知识库主题和已声明的兴趣方向，"
    "概括他的研究兴趣，并生成 3-4 条用于发现“值得继续读的新论文”的英文检索串。\n"
    '只输出 JSON：{"interests": ["中文兴趣方向，最多 5 条"], "queries": [{"query": "英文检索串", "why": "为什么这条检索串适合该用户（中文一句话，引用具体的库中论文或主题）"}]}'
)


def _library_signals(app: AppContext) -> dict:
    with app.db.session() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
        where = "WHERE COALESCE(role,'paper')='paper'" if "role" in cols else ""
        papers = conn.execute(
            f"SELECT id, title, abstract, year, favorite, last_opened_at, doi, arxiv_id FROM papers {where} "
            "ORDER BY favorite DESC, COALESCE(last_opened_at, imported_at) DESC LIMIT 200"
        ).fetchall()
        topics: list[str] = []
        interests: list[str] = []
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "kb_topics" in tables:
            topics = [r["name"] for r in conn.execute("SELECT name FROM kb_topics ORDER BY updated_at DESC LIMIT 12").fetchall()]
        if "profile_meta" in tables:
            row = conn.execute("SELECT value_json FROM profile_meta WHERE key='interests'").fetchone()
            interests = loads(row["value_json"], []) if row else []
    papers = [dict(p) for p in papers]
    try:
        # 读过不等于感兴趣：按“笔记、重点、收藏、提问、读得深不深”的投入分排序，而不是按打开时间；用户标记“不是我的兴趣”的主题不参与
        from ..knowledge import interest

        sc = interest.paper_scores(app)
        papers.sort(key=lambda p: -sc.get(p["id"], {}).get("score", 0.0))  # 稳定排序：同分保持收藏、最近打开在前
        ranked = [i["name"] for i in interest.topic_interests(app, include_read_only=True)]
        with app.db.session() as conn:
            muted = interest._muted(app)
            names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM kb_topics").fetchall()} if "kb_topics" in tables else {}
        muted_names = {names[i] for i in muted if i in names}
        topics = (ranked + [t for t in topics if t not in ranked])
        topics = [t for t in topics if t not in muted_names][:12]
    except Exception:  # noqa: BLE001 - 兴趣推测只是排序依据，出错时退回原来的顺序
        pass
    return {"papers": papers[:40], "topics": topics, "interests": interests}


def _top_terms(titles: list[str], n: int = 6) -> list[str]:
    c: Counter = Counter()
    for t in titles:
        for w in set(query_terms(t)):
            if re.match(r"[a-z]", w) and len(w) > 3 and w not in STOP:
                c[w] += 1
    return [w for w, _ in c.most_common(n)]


async def recommend_personal(app: AppContext, limit: int = 15, use_ai: bool = True, *, year_from: int | None = None,
                             year_to: int | None = None, level: str = "any", exclude: list[str] | None = None) -> dict:
    level = level if level in LEVELS else "any"
    skip = set(exclude or [])
    big = 2 if skip or level != "any" or year_from or year_to else 1
    sig = _library_signals(app)
    if not sig["papers"] and not sig["topics"] and not sig["interests"]:
        raise ApiError("bad_request", "文献库和知识库里还没有内容。请先导入几篇论文，或使用“研究方向推荐”。")
    titles = [p["title"] for p in sig["papers"] if p["title"]]
    queries: list[dict] = []
    interests: list[str] = list(sig["interests"])
    ai_used = False
    if use_ai and llm_available(app):
        lines = [f"- {p['title']}" + ("（收藏）" if p["favorite"] else "") for p in sig["papers"][:20]]
        user = ("文献库论文：\n" + "\n".join(lines) + ("\n知识库主题：" + "、".join(sig["topics"]) if sig["topics"] else "")
                + ("\n用户声明的兴趣方向：" + "、".join(sig["interests"]) if sig["interests"] else ""))
        try:
            data = await ask_json(app, system=INTEREST_SYSTEM, user=user, task_type="discovery_interest", tier="fast",
                                  max_tokens=900, summary=f"发送了文献库中 {len(lines)} 篇论文的标题与知识库主题，用于概括研究兴趣")
            if isinstance(data, dict):
                interests = [str(x) for x in data.get("interests") or []][:5] or interests
                queries = [{"query": str(q.get("query", "")).strip(), "why": str(q.get("why", "")).strip()}
                           for q in data.get("queries") or [] if isinstance(q, dict) and str(q.get("query", "")).strip()][:4]
                ai_used = bool(queries)
        except LLMError:
            pass
    if not queries:
        terms = _top_terms(titles, 6)
        if sig["topics"]:
            terms = [t.lower() for t in sig["topics"][:2] if re.match(r"[A-Za-z]", t)] + terms
        if not terms:
            raise ApiError("bad_request", "无法从现有内容中提取检索主题。请先配置模型，或使用“研究方向推荐”。")
        queries = [{"query": " ".join(terms[:3]), "why": f"根据文献库标题里最常出现的术语：{'、'.join(terms[:3])}"}]
        if len(terms) > 3:
            queries.append({"query": " ".join(terms[3:6]), "why": f"文献库中另一组高频术语：{'、'.join(terms[3:6])}"})
    pool: list[dict] = []
    warnings: list[str] = []
    sources: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        mode = "recent" if len(pool) % 2 else "comprehensive"
        res = await run_search(app, q["query"], mode=mode, limit=10 * big, use_ai=use_ai, year_from=year_from, year_to=year_to)
        warnings += res["warnings"]
        sources = res["sources"] or sources
        for w in res["results"]:
            if w["key"] in seen or w["key"] in skip or w.get("in_library") or not passes_filters(w, year_from, year_to, level):
                continue
            seen.add(w["key"])
            w["from_query"] = q["query"]
            w["query_why"] = q["why"]
            # 理由里补充可核对的“与你文献库中哪篇相关”
            w["related_library"] = _closest_library_paper(w, sig["papers"])
            if w["related_library"]:
                w["reason"] = f"与你库中《{w['related_library']['title'][:50]}》共享术语：{'、'.join(w['related_library']['shared'])}；" + w["reason"]
            pool.append(w)
    pool.sort(key=lambda w: -w["score"])
    return {"interests": interests, "queries": queries, "results": pool[:limit], "sources": sources,
            "warnings": list(dict.fromkeys(warnings)), "ai_used": ai_used}


def _closest_library_paper(w: dict, papers: list[dict]) -> dict | None:
    wt = set(query_terms(w["title"] + " " + (w.get("abstract") or "")[:300]))
    best, best_shared = None, []
    for p in papers:
        pt = set(query_terms(p["title"] or ""))
        shared = sorted(t for t in wt & pt if re.match(r"[a-z]", t) and len(t) > 3)
        if len(shared) > len(best_shared):
            best, best_shared = p, shared
    if best and len(best_shared) >= 2:
        return {"paper_id": best["id"], "title": best["title"], "shared": best_shared[:4]}
    return None


# ======================= 随机探索 =======================
SUMMARY_SYSTEM = (
    "你是科研导读助手。根据下面一篇论文的标题和摘要，用中文概括，不得编造摘要里没有的信息。只输出 JSON：\n"
    '{"research_question": "这篇论文要解决的研究问题（一句话）", "contribution": "核心贡献（一到两句）",'
    ' "why_read": "为什么值得读（一句话，只依据摘要和下面给出的质量事实）"}'
)


def _quality_facts(w: Work) -> str:
    bits = []
    if w.venue:
        bits.append(f"发表于《{w.venue}》" + ("，核心来源" if w.core_venue else ""))
    if w.percentile is not None:
        bits.append(f"同领域同年份引用百分位 {w.percentile * 100:.0f}")
    elif w.fwci is not None:
        bits.append(f"FWCI {w.fwci:.1f}")
    if w.year:
        bits.append(f"{w.year} 年")
    return "；".join(bits)


RANDOM_BATCH = 6  # 每次随机取几个白名单来源一起检索（全部来源逐个试太慢，也会让结果集中在作品多的来源）


def clean_topic(topic: str | None) -> str | None:
    """用户研究方向进入 OpenAlex 过滤条件前的清洗：去掉会破坏过滤语法的字符，限长；清洗后为空就不用。"""
    t = re.sub(r"[,|:<>!\\\"]+", " ", topic or "")
    t = re.sub(r"\s+", " ", t).strip()[:60]
    return t or None


def random_filters(field_id: int | None, year_from: int, year_to: int, fwci_min: float | None, source_ids: list[str],
                   topic: str | None = None) -> list[str]:
    """随机探索的 OpenAlex 过滤条件：直接按白名单来源 id 检索，再加论文类型/摘要/撤稿/语言/年份（和学科、FWCI）。"""
    f = [
        "type:article", "has_abstract:true", "is_retracted:false", "language:en", f"publication_year:{year_from}-{year_to}",
        "primary_location.source.id:" + "|".join(source_ids),
    ]
    if field_id is not None:
        f.append(f"primary_topic.field.id:fields/{field_id}")
    if topic:
        f.append(f"title_and_abstract.search:{topic}")
    if fwci_min is not None:
        f.append(f"fwci:>{fwci_min}")
    return f


def score_for_exploration(w: Work, now_year: int) -> None:
    """随机探索不看与查询的相关性，只看质量、影响力（按学科年份归一）与近期价值。"""
    group = domain_group(w)
    q = quality_score(w)
    imp, imp_note = impact_score(w, now_year)
    rec = recency_score(w, now_year, group)
    w.score = 0.35 * q + 0.40 * imp + 0.25 * rec
    w.breakdown = {"quality": q, "impact": imp, "recency": rec}
    w.rank_note = f"质量 {q:.2f} · 影响力 {imp:.2f} · 时效 {rec:.2f}"


_POOL_CACHE: dict[tuple, tuple[int | None, int | None]] = {}


async def _pool_counts(client, field_id: int | None, year_from: int, year_to: int, fwci_min: float | None,
                       source_ids: list[str]) -> tuple[int | None, int | None]:
    """(这些来源在该年份区间的全部作品数, 通过全部质量条件的作品数)——如实反映筛选的选择性。同一参数只查一次。"""
    key = (field_id, year_from, year_to, fwci_min, tuple(sorted(source_ids)))
    if key not in _POOL_CACHE:
        base = [f"publication_year:{year_from}-{year_to}", "primary_location.source.id:" + "|".join(source_ids)]
        if field_id is not None:
            base.append(f"primary_topic.field.id:fields/{field_id}")
        total, passed = await asyncio.gather(
            S.count_openalex(client, base), S.count_openalex(client, random_filters(field_id, year_from, year_to, fwci_min, source_ids)))
        _POOL_CACHE[key] = (total, passed)
    return _POOL_CACHE[key]


def confirm_top_venue(works: list[Work]) -> tuple[list[Work], int]:
    """逐篇核对来源：只有来源名称与白名单精确对应（且不是预印本）的才保留，并写上确认信息。返回 (保留的, 被丢弃的数量)。"""
    kept: list[Work] = []
    for w in works:
        v = match_venue(w.venue, w.venue_type, loose=False)
        if v is None or v.level != "top" or w.status == "preprint":
            continue
        w.top_venue = {"id": v.id, "name": w.venue or v.name, "short": v.short, "kind": v.kind, "category": v.category, "label": v.label}
        kept.append(w)
    return kept, len(works) - len(kept)


VENUE_POLICY = ("只显示来源可确认的论文：来源名称必须与收录名单精确对应；来源缺失、预印本、workshop 和同名但非主会/主刊的一律不显示。")


async def random_explore(
    app: AppContext, *, field_id: int | None = None, exclude_keys: list[str] | None = None,
    seen_fields: list[int] | None = None, count: int = 1, seed: int | None = None, use_ai: bool = True,
    topic: str | None = None, year_from: int | None = None, year_to: int | None = None,
) -> dict:
    """随机探索：只返回发表在白名单顶刊/顶会上的论文。`seen_fields` 仅为兼容旧客户端而保留，已不再使用。"""
    count = max(1, min(3, count))
    rng = random.Random(seed)
    now = date.today().year
    y_to = min(max(year_to or now - 1, 1990), now)
    y_from = min(max(year_from or y_to - 9, 1990), y_to)
    exclude = set(exclude_keys or [])
    if field_id is not None and field_id not in FIELDS:
        field_id = None
    topic = clean_topic(topic)
    if topic:
        field_id = None  # 按研究方向探索时不再限定一级学科：方向本身已经是更精确的范围
    candidates = venues_for_field(field_id)
    if not candidates:
        raise ApiError("not_found", "这个学科暂时没有收录的顶刊顶会，请换一个学科或选择“全部学科”。", 404)
    order = rng.sample(candidates, len(candidates))
    stats: dict = {
        "field": FIELDS[field_id][0] if field_id is not None else None,
        "field_zh": FIELDS[field_id][1] if field_id is not None else None, "field_id": field_id,
        "topic": topic, "venues": [], "venue_pool": len(candidates), "venue_policy": VENUE_POLICY,
        "pool_total": None, "pool_passed": None, "sampled": 0, "dropped_unconfirmed": 0, "after_exclusions": 0, "thresholds": {},
    }
    works: list[Work] = []
    async with http_client(app) as client:
        resolved_any = False
        for start in range(0, min(len(order), RANDOM_BATCH * 2), RANDOM_BATCH):  # 最多试两批来源
            resolved = await resolve_many(app, client, order[start:start + RANDOM_BATCH])
            if not resolved:
                continue
            resolved_any = True
            sids = [sid for _, sid in resolved]
            for fwci_min in (3.0, 1.5, None):  # 质量门槛逐级放宽，直到样本足够
                flt = random_filters(field_id, y_from, y_to, fwci_min, sids, topic)
                try:
                    raw = await S.fetch_openalex(client, None, limit=40, filters=flt, sample=40, seed=rng.randint(1, 10**6))
                except S.SourceError as exc:
                    raise ApiError("network", f"随机探索需要 OpenAlex：{exc}", 502) from exc
                works, dropped = confirm_top_venue(raw)
                stats.update(sampled=len(raw), dropped_unconfirmed=dropped)
                stats["thresholds"] = {"fwci_min": fwci_min, "years": f"{y_from}-{y_to}", "type": "journal/conference article",
                                       "venue": f"白名单顶刊顶会，本次从 {len(candidates)} 个来源中随机取 {len(resolved)} 个",
                                       "has_abstract": True, "not_retracted": True}
                if len(works) >= 8:
                    break
            if works:
                stats["venues"] = [{"id": v.id, "name": v.name, "short": v.short, "kind": v.kind, "category": v.category} for v, _ in resolved]
                stats["pool_total"], stats["pool_passed"] = await _pool_counts(
                    client, field_id, y_from, y_to, stats["thresholds"]["fwci_min"], sids)
                break
        if not resolved_any:
            raise ApiError("network", "暂时无法在 OpenAlex 里确认这些顶刊顶会的来源，请稍后再试。", 502)
    works = merge_works(works)
    mark_in_library(app, works)
    works = [w for w in works if w.key not in exclude and not w.in_library and w.abstract and len(w.abstract) > 200]
    stats["after_exclusions"] = len(works)
    if not works:
        raise ApiError("not_found", "这次没有抽到来源可确认、且你没看过的顶刊顶会论文，请再点一次。", 404)
    for w in works:
        score_for_exploration(w, now)
    # 样本已通过全部筛选；这里按综合得分加权随机挑选（不是取最高分），保证“随机”又偏向更好的论文
    picked: list[Work] = []
    pool = list(works)
    while pool and len(picked) < count:
        weights = [(w.score + 0.05) ** 2 for w in pool]
        choice = rng.choices(pool, weights=weights, k=1)[0]
        picked.append(choice)
        pool.remove(choice)
    items = [await _explain(app, w, use_ai) for w in picked]
    return {"items": items, "filter": stats, "ai": use_ai and llm_available(app)}


async def _explain(app: AppContext, w: Work, use_ai: bool) -> dict:
    d = w.to_dict()
    d["field_zh"] = FIELD_ZH.get(w.field_name or "", w.field_name)
    d["quality_facts"] = _quality_facts(w)
    d["summary"] = None
    if use_ai and llm_available(app):
        try:
            data = await ask_json(
                app, system=SUMMARY_SYSTEM, user=f"标题：{w.title}\n摘要：{(w.abstract or '')[:1500]}\n质量事实：{d['quality_facts']}",
                task_type="discovery_summary", tier="fast", max_tokens=500,
                summary="发送了一篇公开论文的标题与摘要，用于生成导读（不含用户内容）")
            if isinstance(data, dict) and data.get("research_question"):
                d["summary"] = {"research_question": str(data.get("research_question", "")), "contribution": str(data.get("contribution", "")),
                                "why_read": str(data.get("why_read", "")), "source": "AI 根据摘要概括"}
        except LLMError:
            pass
    if not d["summary"]:
        d["summary"] = None
    d["reason"] = d["quality_facts"] and f"质量筛选依据：{d['quality_facts']}" or ""
    d["reason_source"] = "rule"
    return d


# ======================= 相似论文 =======================
async def similar_works(app: AppContext, *, doi: str | None, title: str | None, limit: int = 10) -> dict:
    async with http_client(app) as client:
        try:
            seed, cands, relation = await _related_candidates(client, {"doi": doi, "title": title}, Plan(query=title or "", english_query=title or ""))
        except S.SourceError as exc:
            raise ApiError("network", f"无法获取相似论文：{exc}", 502) from exc
    if seed is None:
        raise ApiError("not_found", "没有找到这篇论文的相关记录。", 404)
    works = merge_works(cands)
    mark_in_library(app, works)
    score_works(works, query=f"{seed.title}. {(seed.abstract or '')[:300]}", mode="related")
    for w in works:
        w.relation = relation.get(w.key, "")
        w.reason, w.reason_source = (w.relation + "；" + rule_reason(w, seed.title)).strip("；"), "rule"
    works.sort(key=lambda w: -w.score)
    return {"seed": {"title": seed.title, "doi": seed.doi}, "results": [w.to_dict() for w in works[:limit]]}
