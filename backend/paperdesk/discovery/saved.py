"""“收藏”的检索结果（还没有导入文献库的论文）。只保存真实数据源返回的元数据。"""
from __future__ import annotations

from ..context import AppContext
from ..db import register_schema
from ..util import ApiError, dumps, loads, utcnow

register_schema("discovery_saved", """
CREATE TABLE IF NOT EXISTS discovery_saved (
  key TEXT PRIMARY KEY,
  work_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
""")


def list_saved(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT work_json, created_at FROM discovery_saved ORDER BY created_at DESC").fetchall()
    return [{**loads(r["work_json"], {}), "saved_at": r["created_at"]} for r in rows]


def save_work(app: AppContext, work: dict) -> dict:
    key = work.get("key")
    if not key or not work.get("title"):
        raise ApiError("bad_request", "缺少论文标识或标题")
    slim = {k: work.get(k) for k in (
        "key", "title", "authors", "year", "venue", "doi", "arxiv_id", "abstract", "url", "pdf_url", "status", "status_label",
        "sources", "citations", "fwci", "percentile", "field", "field_zh", "reason", "summary", "rank_note", "versions")}
    with app.db.session() as conn:
        conn.execute("INSERT OR REPLACE INTO discovery_saved(key, work_json, created_at) VALUES (?,?,?)",
                     (key, dumps(slim), utcnow()))
    return slim


def remove_saved(app: AppContext, key: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM discovery_saved WHERE key = ?", (key,))
    if cur.rowcount == 0:
        raise ApiError("not_found", "没有收藏这篇论文", 404)
