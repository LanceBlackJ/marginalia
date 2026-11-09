"""学术发现与推荐 API。契约见 docs/API-discovery.md。"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..context import AppContext
from ..discovery import saved
from ..discovery.codefinder import find_code
from ..discovery.recommend import (
    FIELDS, random_explore, recommend_direction, recommend_personal, similar_works,
)
from ..discovery.advanced import Row, run_advanced
from ..discovery.search import run_search
from .deps import get_ctx

router = APIRouter(prefix="/discovery", tags=["discovery"])


class SeedBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    doi: str | None = None
    title: str | None = None
    paper_id: str | None = None


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str = ""
    mode: Literal["comprehensive", "recent", "impact", "related", "cross"] = "comprehensive"
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    limit: int = Field(default=20, ge=1, le=100)
    seed: SeedBody | None = None
    ai: bool = True


def _resolve_seed(ctx: AppContext, seed: SeedBody | None) -> dict | None:
    if seed is None:
        return None
    d = seed.model_dump()
    if d.get("paper_id"):
        with ctx.db.session() as conn:
            row = conn.execute("SELECT title, doi FROM papers WHERE id = ?", (d["paper_id"],)).fetchone()
        if row:
            d["title"], d["doi"] = d.get("title") or row["title"], d.get("doi") or row["doi"]
    return d


class AdvRowBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field: Literal["all", "title", "author", "abstract", "tak", "venue", "doi"] = "all"
    term: str = Field(default="", max_length=300)
    op: Literal["and", "or", "not"] = "and"
    exact: bool = False


class AdvancedBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rows: list[AdvRowBody] = Field(default_factory=list, max_length=8)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["relevance", "cited", "date"] = "relevance"
    limit: int = Field(default=50, ge=1, le=200)


@router.post("/advanced")
async def advanced(body: AdvancedBody, ctx: AppContext = Depends(get_ctx)):
    """Scopus 式的字段检索：逐行 字段+检索词，行间 且/或/非；标题可精确匹配。只查 OpenAlex 的公开元数据，不用 AI。"""
    return await run_advanced(
        ctx, [Row(r.field, r.term, r.op, r.exact) for r in body.rows], year_from=body.year_from, year_to=body.year_to, sort=body.sort, limit=body.limit,
    )


@router.post("/search")
async def search(body: SearchBody, ctx: AppContext = Depends(get_ctx)):
    return await run_search(
        ctx, body.query, mode=body.mode, year_from=body.year_from, year_to=body.year_to, limit=body.limit,
        seed=_resolve_seed(ctx, body.seed), use_ai=body.ai,
    )


class DirectionBody(BaseModel):
    topic: str
    ai: bool = True
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    level: Literal["any", "peer", "core", "top"] = "any"
    exclude: list[str] = Field(default_factory=list, max_length=400)  # “再推荐一些”：已经显示过的论文


@router.post("/recommend/direction")
async def direction(body: DirectionBody, ctx: AppContext = Depends(get_ctx)):
    return await recommend_direction(ctx, body.topic, body.ai, year_from=body.year_from, year_to=body.year_to,
                                     level=body.level, exclude=body.exclude)


class PersonalBody(BaseModel):
    limit: int = Field(default=15, ge=1, le=30)
    ai: bool = True
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    level: Literal["any", "peer", "core", "top"] = "any"
    exclude: list[str] = Field(default_factory=list, max_length=400)


@router.post("/recommend/personal")
async def personal(body: PersonalBody, ctx: AppContext = Depends(get_ctx)):
    return await recommend_personal(ctx, body.limit, body.ai, year_from=body.year_from, year_to=body.year_to,
                                    level=body.level, exclude=body.exclude)


class RandomBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field_id: int | None = None
    exclude: list[str] = []
    seen_fields: list[int] = []
    count: int = Field(default=1, ge=1, le=3)
    seed: int | None = None
    ai: bool = True
    topic: str | None = Field(default=None, max_length=120)  # 用户研究画像里的一个方向（自由文本）
    year_from: int | None = Field(default=None, ge=1990, le=2100)
    year_to: int | None = Field(default=None, ge=1990, le=2100)


@router.post("/random")
async def random_(body: RandomBody, ctx: AppContext = Depends(get_ctx)):
    return await random_explore(ctx, field_id=body.field_id, exclude_keys=body.exclude, seen_fields=body.seen_fields,
                                count=body.count, seed=body.seed, use_ai=body.ai, topic=body.topic,
                                year_from=body.year_from, year_to=body.year_to)


@router.get("/fields")
def fields():
    from ..discovery.venues import venues_for_field

    return {"items": [{"id": k, "name": v[0], "name_zh": v[1], "venue_count": len(venues_for_field(k))} for k, v in FIELDS.items()]}


@router.get("/venues")
def venues():
    """随机探索收录的顶刊顶会名单（可审阅）：{count, policy, items:[{id,name,short,kind,category,fields}]}。"""
    from ..discovery.recommend import VENUE_POLICY
    from ..discovery.venues import public_list

    from ..discovery.venue_info import INTRO
    from ..discovery.venues import BY_ID

    items = public_list()
    for i in items:
        i["intro"] = INTRO.get(i["id"]) or BY_ID[i["id"]].intro
    return {"count": len(items), "policy": VENUE_POLICY, "items": items}


class SimilarBody(BaseModel):
    doi: str | None = None
    title: str | None = None
    limit: int = Field(default=10, ge=1, le=25)


@router.post("/similar")
async def similar(body: SimilarBody, ctx: AppContext = Depends(get_ctx)):
    return await similar_works(ctx, doi=body.doi, title=body.title, limit=body.limit)


class CodeBody(BaseModel):
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


@router.post("/find-code")
async def find_code_(body: CodeBody, ctx: AppContext = Depends(get_ctx)):
    return await find_code(ctx, title=body.title, doi=body.doi, arxiv_id=body.arxiv_id)


@router.get("/saved")
def list_saved(ctx: AppContext = Depends(get_ctx)):
    return {"items": saved.list_saved(ctx)}


@router.post("/saved")
def save(body: dict, ctx: AppContext = Depends(get_ctx)):
    return saved.save_work(ctx, body)


@router.delete("/saved/{key:path}")
def unsave(key: str, ctx: AppContext = Depends(get_ctx)):
    saved.remove_saved(ctx, key)
    return {"ok": True}


# ---------------- 文库匹配 / 设置 / 来源说明 ----------------
class MatchItem(BaseModel):
    key: str
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None


class MatchBody(BaseModel):
    items: list[MatchItem] = Field(max_length=100)


@router.post("/library-match")
def library_match(body: MatchBody, ctx: AppContext = Depends(get_ctx)):
    """导入完成后刷新“已在文献库”状态（按 DOI / arXiv / 标题匹配）。"""
    from ..discovery.models import Work
    from ..discovery.search import mark_in_library

    works = [Work(title=i.title or "", doi=i.doi, arxiv_id=i.arxiv_id) for i in body.items]
    mark_in_library(ctx, works)
    return {"matches": {i.key: w.in_library for i, w in zip(body.items, works) if w.in_library}}


class DiscoverySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    use_embeddings: bool | None = None
    embedding_model: str | None = None


@router.get("/settings")
def get_settings(ctx: AppContext = Depends(get_ctx)):
    from ..discovery.embed import discovery_settings

    return discovery_settings(ctx)


@router.put("/settings")
def put_settings(body: DiscoverySettings, ctx: AppContext = Depends(get_ctx)):
    from ..discovery.embed import discovery_settings
    from ..util import dumps

    cur = discovery_settings(ctx)
    upd = body.model_dump(exclude_none=True)
    if "embedding_model" in upd:
        upd["embedding_model"] = upd["embedding_model"].strip() or cur["embedding_model"]
    cur.update(upd)
    with ctx.db.session() as conn:
        conn.execute("INSERT INTO settings(key, value_json) VALUES ('discovery', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json", (dumps(cur),))
    return cur


SOURCES = [
    {"id": "openalex", "name": "OpenAlex", "scope": "全学科", "gives": "题录、摘要、引用与领域归一化指标、版本关联、参考文献",
     "note": "无 Key 时共享每日免费额度；用于核心检索与排序。"},
    {"id": "crossref", "name": "Crossref", "scope": "全学科", "gives": "题录、DOI、预印本/正式版本关联、发表状态",
     "note": "公共 API，礼貌限速。"},
    {"id": "s2", "name": "Semantic Scholar", "scope": "全学科，计算机强", "gives": "摘要、引用、外部 ID",
     "note": "未配置 API Key 时共享额度很小，经常被限流；被限流时自动跳过。"},
    {"id": "arxiv", "name": "arXiv", "scope": "物理/数学/计算机/统计/生物定量等预印本", "gives": "预印本、journal_ref/DOI、接收备注",
     "note": "预印本未经同行评审，质量分较低。"},
    {"id": "europepmc", "name": "Europe PMC", "scope": "生物医学", "gives": "医学/生命科学论文与预印本、引用",
     "note": "仅在检索被判定为生物医学时参与。"},
    {"id": "github", "name": "GitHub", "scope": "代码仓库", "gives": "“查找代码”的候选仓库",
     "note": "未登录时每分钟约 10 次搜索；只有 README 中出现论文标题/arXiv 编号/DOI 才标为高匹配。"},
]


@router.get("/venue-info")
async def venue_info_(name: str, type: str | None = None, ctx: AppContext = Depends(get_ctx)):
    """某个期刊/会议的介绍：名单内来源的简介 + OpenAlex 的真实统计。"""
    from ..discovery.recommend import http_client
    from ..discovery.venue_info import venue_info

    async with http_client(ctx) as client:
        return await venue_info(client, name.strip()[:200], type)


class TranslateItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(max_length=400)
    text: str = Field(max_length=12000)


class TranslateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["title", "abstract"] = "title"
    items: list[TranslateItem] = Field(default_factory=list, max_length=30)


@router.post("/translate")
async def translate_works(body: TranslateBody, ctx: AppContext = Depends(get_ctx)):
    """找到的论文自动配中文：标题/摘要译成中文（只发公开的题录文字，结果缓存）。没配置模型或译不出来的条目不在结果里。"""
    from ..discovery.translate import translate_texts

    return {"items": await translate_texts(ctx, body.kind, [i.model_dump() for i in body.items])}


@router.get("/library-venues")
def library_venues(ctx: AppContext = Depends(get_ctx)):
    """我文献库里的论文都发表在哪些期刊/会议上：每个来源的论文数、最早最晚年份；名录里有的带上分类和档次。"""
    from ..discovery.venues import match_venue

    with ctx.db.session() as conn:
        rows = conn.execute(
            "SELECT TRIM(venue) AS venue, COUNT(*) AS n, MIN(year) AS y0, MAX(year) AS y1 FROM papers "
            "WHERE COALESCE(role, 'paper') = 'paper' AND TRIM(COALESCE(venue, '')) != '' GROUP BY TRIM(venue) ORDER BY n DESC, venue LIMIT 300"
        ).fetchall()
    items = []
    for r in rows:
        v = match_venue(r["venue"])
        items.append({
            "name": r["venue"], "count": r["n"], "year_from": r["y0"], "year_to": r["y1"],
            "id": v.id if v else None, "label": v.label if v else None, "level": v.level if v else None,
            "kind": v.kind if v else None, "category": v.category if v else None, "short": v.short if v else None,
        })
    return {"items": items, "papers": sum(i["count"] for i in items)}


@router.get("/venue-page")
async def venue_page(name: str | None = None, id: str | None = None, type: str | None = None, oa: str | None = None,
                     works: bool = True, ctx: AppContext = Depends(get_ctx)):
    """某个期刊/会议的页面：介绍（名录里的简介 + OpenAlex 统计）+ 这个来源上被引最多的和最新的论文。
    用名录里的 id、OpenAlex 来源 id（oa）或来源名称（含缩写）都可以；works=false 只取介绍（很快），论文列表另取，避免整页等。
    论文都是 OpenAlex 里“主要发表位置”确切是这个来源的，查不到就不显示，不编造。"""
    from ..discovery import sources as S
    from ..discovery.recommend import http_client
    from ..discovery.search import mark_in_library
    from ..discovery.venue_info import venue_info
    from ..discovery.venues import BY_ID
    from ..util import ApiError

    v = BY_ID.get(id) if id else None
    if v is None and not (name and name.strip()) and not oa:
        raise ApiError("bad_request", "需要来源名称、OpenAlex 来源 id 或名录里的 id")
    title = v.name if v else (name or oa or "").strip()[:200]
    found: dict[str, list] = {"cited": [], "recent": []}
    errors: list[str] = []
    async with http_client(ctx) as client:
        info = await venue_info(client, title, v.kind if v and type is None else type, openalex_id=oa)
        if info.get("listed") and v is None:
            v = BY_ID.get(info["listed"]["id"])  # 用缩写或 OpenAlex id 找到的，按正式名称对上名录
        title = (info.get("stats") or {}).get("name") or title
        sid = ((info.get("stats") or {}).get("openalex_id") or "").rsplit("/", 1)[-1]
        if works and sid:
            base = [f"primary_location.source.id:{sid}", "type:article", "has_abstract:true", "is_retracted:false"]

            async def one(key: str, sort: str) -> None:
                try:
                    found[key] = await S.fetch_openalex(client, None, limit=12, sort=sort, filters=base)
                except S.SourceError as exc:
                    errors.append(str(exc))

            await asyncio.gather(one("cited", "cited_by_count:desc"), one("recent", "publication_date:desc"))
        elif works:
            errors.append("OpenAlex 里没有找到与这个名称完全对应的来源，所以没有论文列表。")
    for lst in found.values():
        mark_in_library(ctx, lst)
    return {
        "name": title, "id": v.id if v else None, "kind": (v.kind if v else None) or ((info.get("stats") or {}).get("type")),
        "category": v.category if v else None, "level": v.level if v else None, "label": v.label if v else None,
        "short": v.short if v else None, "info": info, "errors": errors,
        "works": {k: [w.to_dict() for w in lst] for k, lst in found.items()},
    }


@router.get("/venue-search")
async def venue_search(q: str, ctx: AppContext = Depends(get_ctx)):
    """在 OpenAlex 里按名称或缩写检索期刊/会议：名录里没有的也能找到，点开就是它的页面。"""
    from ..discovery.recommend import http_client
    from ..discovery.venue_info import search_sources
    import httpx

    text = q.strip()[:120]
    if len(text) < 2:
        return {"items": []}
    try:
        async with http_client(ctx) as client:
            return {"items": await search_sources(client, text)}
    except httpx.HTTPError:
        return {"items": [], "error": "暂时连不上 OpenAlex"}


@router.get("/sources")
def sources():
    return {"items": SOURCES}
