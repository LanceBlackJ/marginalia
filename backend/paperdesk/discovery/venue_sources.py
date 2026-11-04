"""白名单来源 → OpenAlex source id 的解析与缓存。

不手写 source id：按名称在 OpenAlex /sources 里检索，只接受“名称归一化后与白名单别名完全相等”的结果
（同名里取作品数最多的一个），结果缓存在本地库里，之后随机探索直接按 id 过滤检索。
解析不到的来源本次就不用（如实告知），不会放宽匹配。
"""
from __future__ import annotations

import asyncio

import httpx

from ..context import AppContext
from ..db import register_schema
from ..util import utcnow
from . import sources as S
from .venues import Venue, match_venue

register_schema("discovery_venues", """
CREATE TABLE IF NOT EXISTS venue_sources (
  venue_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  resolved_at TEXT NOT NULL
);
""")

_MISSING: set[str] = set()  # 本进程内已确认“在 OpenAlex 里解析不到”的来源，避免每次点击都重复检索


def _short(source_id: str) -> str:
    return source_id.rsplit("/", 1)[-1]


def _cached(app: AppContext, venue_id: str) -> str | None:
    with app.db.session() as conn:
        row = conn.execute("SELECT source_id FROM venue_sources WHERE venue_id = ?", (venue_id,)).fetchone()
    return row["source_id"] if row else None


def pick_source(venue: Venue, results: list[dict]) -> dict | None:
    """从 /sources 的检索结果里挑出名称与该白名单项精确对应的来源（同名取作品数最多的）。"""
    ok = [r for r in results if (m := match_venue(r.get("display_name"), r.get("type"))) is not None and m.id == venue.id]
    if not ok:
        return None
    return max(ok, key=lambda r: r.get("works_count") or 0)


async def resolve_one(app: AppContext, client: httpx.AsyncClient, venue: Venue) -> str | None:
    hit = _cached(app, venue.id)
    if hit:
        return hit
    if venue.id in _MISSING:
        return None
    picked = None
    for term in venue.search_terms:  # 先用正式名称，找不到再试别名/缩写
        try:
            results = await S.fetch_openalex_sources(client, term)
        except S.SourceError:
            return None  # 网络/限流：这次不用它，也不记成“不存在”
        picked = pick_source(venue, results)
        if picked is not None:
            break
    if picked is None:
        _MISSING.add(venue.id)
        return None
    sid = _short(picked["id"])
    with app.db.session() as conn:
        conn.execute("INSERT OR REPLACE INTO venue_sources(venue_id, source_id, display_name, resolved_at) VALUES (?,?,?,?)",
                     (venue.id, sid, picked.get("display_name") or venue.name, utcnow()))
    return sid


async def resolve_many(app: AppContext, client: httpx.AsyncClient, venues: list[Venue]) -> list[tuple[Venue, str]]:
    ids = await asyncio.gather(*(resolve_one(app, client, v) for v in venues))
    return [(v, sid) for v, sid in zip(venues, ids) if sid]
