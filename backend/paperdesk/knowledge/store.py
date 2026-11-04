"""主题知识库 / 个人知识画像的数据层。

核心约束（说明书 §14）：
  · AI 只能提出“候选”，用户确认后才进入知识库 / 画像；
  · 知识条目与关系必须带可核实的证据（证据的 quote 一律从数据库真实段落重建，客户端不能伪造）；
  · AI 不得仅凭阅读过论文就认定用户已掌握某概念，画像里只有用户确认过的状态。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..context import AppContext
from ..db import register_schema
from ..util import ApiError, dumps, loads, new_id, utcnow

register_schema("knowledge", """
CREATE TABLE IF NOT EXISTS kb_topics (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',          -- manual | ai | auto
  status TEXT NOT NULL DEFAULT 'active',          -- active | suggested
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'other',
  source_paper_id TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  card_id TEXT,
  note_id TEXT,
  origin TEXT NOT NULL DEFAULT 'manual',          -- candidate | manual
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_item_topics (
  item_id TEXT NOT NULL REFERENCES kb_items(id) ON DELETE CASCADE,
  topic_id TEXT NOT NULL REFERENCES kb_topics(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, topic_id)
);
CREATE TABLE IF NOT EXISTS kb_candidates (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  origin_kind TEXT NOT NULL DEFAULT 'analysis',   -- analysis | card | note
  origin_ref TEXT,
  status TEXT NOT NULL DEFAULT 'pending',         -- pending | accepted | rejected
  item_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_candidates_paper ON kb_candidates(paper_id, status);
CREATE TABLE IF NOT EXISTS kb_relations (
  id TEXT PRIMARY KEY,
  from_kind TEXT NOT NULL,                        -- item | paper
  from_id TEXT NOT NULL,
  to_kind TEXT NOT NULL,
  to_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL,
  ai_inferred INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'confirmed',       -- confirmed | suggested
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_paper_topics (
  paper_id TEXT NOT NULL,
  topic_id TEXT NOT NULL REFERENCES kb_topics(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'confirmed',       -- confirmed | suggested
  PRIMARY KEY (paper_id, topic_id)
);
CREATE TABLE IF NOT EXISTS profile_meta (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_concepts (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,                            -- mastered | learning | unsure | unknown
  note TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'user',            -- user | suggestion
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_suggestions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  basis TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',         -- pending | accepted | dismissed
  created_at TEXT NOT NULL
);
""")

KINDS = ("method", "finding", "concept", "formula", "limitation", "other")
RELATIONS = {"uses_method": "采用的方法", "differs_from": "与…的区别", "improves": "改进了", "inherits": "继承自", "contradicts": "结论相反", "supports": "互相印证"}
STATES = {"mastered": "已确认掌握", "learning": "正在学习", "unsure": "可能不熟悉", "unknown": "尚未了解"}
DEPTHS = ("brief", "standard", "deep")


# ---------------- 证据（从数据库重建，不信任客户端的 quote） ----------------
def build_evidence(app: AppContext, refs: list[dict]) -> list[dict]:
    """refs: [{paper_id, ord}] 或 [{kind:'note', note_id}]。返回带真实 quote/页码/bbox 的证据；找不到的直接丢弃。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    with app.db.session() as conn:
        for r in refs:
            if not isinstance(r, dict):
                continue
            if r.get("kind") == "note" and r.get("note_id") is not None:
                n = conn.execute("SELECT n.*, p.title AS paper_title FROM notes n JOIN papers p ON p.id = n.paper_id WHERE n.id = ?",
                                 (r["note_id"],)).fetchone()
                if n and ("note", n["id"]) not in seen:
                    seen.add(("note", n["id"]))
                    out.append({"kind": "note", "note_id": n["id"], "paper_id": n["paper_id"], "paper_title": n["paper_title"],
                                "ord": n["passage_ord"], "page": n["page"], "quote": (n["quote"] or n["text"])[:400],
                                "section_title": "我的笔记", "bbox": None})
                continue
            pid, ord_ = r.get("paper_id"), r.get("ord")
            if pid is None or ord_ is None or (pid, ord_) in seen:
                continue
            row = conn.execute(
                "SELECT p.ord, p.page, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title, pa.title AS paper_title "
                "FROM passages p LEFT JOIN sections s ON s.id = p.section_id JOIN papers pa ON pa.id = p.paper_id "
                "WHERE p.paper_id = ? AND p.ord = ?", (pid, int(ord_))).fetchone()
            if row:
                seen.add((pid, ord_))
                out.append({"kind": "paper_text", "paper_id": pid, "paper_title": row["paper_title"], "ord": row["ord"],
                            "page": row["page"], "section_title": row["section_title"], "quote": row["text"][:400],
                            "bbox": [row["x0"], row["y0"], row["x1"], row["y1"]]})
    return out


# ---------------- 主题 ----------------
def _topic(r: sqlite3.Row, n_items: int = 0, n_papers: int = 0) -> dict:
    return {"id": r["id"], "name": r["name"], "description": r["description"], "source": r["source"], "status": r["status"],
            "item_count": n_items, "paper_count": n_papers, "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_topics(app: AppContext, status: str = "active") -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM kb_topics WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        out = []
        for r in rows:
            n_items = conn.execute("SELECT COUNT(*) FROM kb_item_topics WHERE topic_id = ?", (r["id"],)).fetchone()[0]
            n_papers = conn.execute("SELECT COUNT(*) FROM kb_paper_topics WHERE topic_id = ? AND status = 'confirmed'", (r["id"],)).fetchone()[0]
            out.append(_topic(r, n_items, n_papers))
    return out


def create_topic(app: AppContext, name: str, description: str = "", source: str = "manual", status: str = "active") -> dict:
    name = name.strip()
    if not name:
        raise ApiError("bad_request", "主题名称不能为空")
    tid, now = new_id(), utcnow()
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM kb_topics WHERE lower(name) = lower(?) AND status = 'active'", (name,)).fetchone() and status == "active":
            raise ApiError("conflict", "已经有同名的主题", 409)
        conn.execute("INSERT INTO kb_topics(id, name, description, source, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (tid, name[:120], description[:1000], source, status, now, now))
        return _topic(conn.execute("SELECT * FROM kb_topics WHERE id = ?", (tid,)).fetchone())


def update_topic(app: AppContext, tid: str, name: str | None, description: str | None) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM kb_topics WHERE id = ?", (tid,)).fetchone()
        if r is None:
            raise ApiError("not_found", "主题不存在", 404)
        conn.execute("UPDATE kb_topics SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                     ((name or r["name"]).strip()[:120], (description if description is not None else r["description"])[:1000], utcnow(), tid))
        return _topic(conn.execute("SELECT * FROM kb_topics WHERE id = ?", (tid,)).fetchone())


def delete_topic(app: AppContext, tid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM kb_topics WHERE id = ?", (tid,)).rowcount == 0:
            raise ApiError("not_found", "主题不存在", 404)
        # 只属于这个主题的条目保留为“未归类”，不删除用户确认过的知识
        conn.execute("DELETE FROM kb_paper_topics WHERE topic_id = ?", (tid,))


def _touch_topic(conn: sqlite3.Connection, tid: str) -> None:
    conn.execute("UPDATE kb_topics SET updated_at = ? WHERE id = ?", (utcnow(), tid))


# ---------------- 知识条目 ----------------
def _item(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    topics = [t["topic_id"] for t in conn.execute("SELECT topic_id FROM kb_item_topics WHERE item_id = ?", (r["id"],)).fetchall()]
    paper = conn.execute("SELECT id, title, year, doi FROM papers WHERE id = ?", (r["source_paper_id"],)).fetchone() if r["source_paper_id"] else None
    return {"id": r["id"], "title": r["title"], "content": r["content"], "kind": r["kind"], "topic_ids": topics,
            "source_paper": dict(paper) if paper else None, "evidence": loads(r["evidence_json"], []),
            "card_id": r["card_id"], "note_id": r["note_id"], "origin": r["origin"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_items(app: AppContext, topic_id: str | None = None, paper_id: str | None = None, q: str | None = None,
               unassigned: bool = False) -> list[dict]:
    sql, args = "SELECT i.* FROM kb_items i", []
    where = []
    if topic_id:
        sql += " JOIN kb_item_topics it ON it.item_id = i.id"
        where.append("it.topic_id = ?")
        args.append(topic_id)
    if unassigned:
        where.append("i.id NOT IN (SELECT item_id FROM kb_item_topics)")
    if paper_id:
        where.append("i.source_paper_id = ?")
        args.append(paper_id)
    if q:
        where.append("(lower(i.title) LIKE ? OR lower(i.content) LIKE ?)")
        args += [f"%{q.lower()}%"] * 2
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.updated_at DESC"
    with app.db.session() as conn:
        return [_item(conn, r) for r in conn.execute(sql, args).fetchall()]


def get_item(app: AppContext, iid: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM kb_items WHERE id = ?", (iid,)).fetchone()
        if r is None:
            raise ApiError("not_found", "知识条目不存在", 404)
        d = _item(conn, r)
        d["relations"] = [_relation(x) for x in conn.execute(
            "SELECT * FROM kb_relations WHERE (from_kind='item' AND from_id=?) OR (to_kind='item' AND to_id=?) ORDER BY created_at",
            (iid, iid)).fetchall()]
        d["cards"] = _linked_cards(conn, r)
        d["notes"] = _linked_notes(conn, r)
    return d


def _linked_cards(conn, r) -> list[dict]:
    ids = {r["card_id"]} - {None}
    for e in loads(r["evidence_json"], []):
        if e.get("card_id"):
            ids.add(e["card_id"])
    out = []
    for cid in ids:
        c = conn.execute("SELECT id, paper_id, question, object_label FROM insight_cards WHERE id = ?", (cid,)).fetchone()
        if c:
            out.append(dict(c))
    return out


def _linked_notes(conn, r) -> list[dict]:
    ids = {r["note_id"]} - {None}
    for e in loads(r["evidence_json"], []):
        if e.get("kind") == "note" and e.get("note_id") is not None:
            ids.add(e["note_id"])
    out = []
    for nid in ids:
        n = conn.execute("SELECT id, paper_id, page, text FROM notes WHERE id = ?", (nid,)).fetchone()
        if n:
            out.append(dict(n))
    return out


def create_item(app: AppContext, *, title: str, content: str, kind: str = "other", topic_ids: list[str] | None = None,
                source_paper_id: str | None = None, evidence_refs: list[dict] | None = None, card_id: str | None = None,
                note_id: str | None = None, origin: str = "manual") -> dict:
    title = title.strip()
    if not title:
        raise ApiError("bad_request", "标题不能为空")
    if kind not in KINDS:
        kind = "other"
    evidence = build_evidence(app, evidence_refs or [])
    if source_paper_id and not evidence and origin == "manual" and evidence_refs:
        raise ApiError("bad_request", "给出的证据在论文中找不到，请重新选择")
    iid, now = new_id(), utcnow()
    with app.db.session() as conn:
        if source_paper_id and conn.execute("SELECT 1 FROM papers WHERE id = ?", (source_paper_id,)).fetchone() is None:
            raise ApiError("not_found", "来源论文不存在", 404)
        conn.execute(
            "INSERT INTO kb_items(id, title, content, kind, source_paper_id, evidence_json, card_id, note_id, origin, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (iid, title[:200], content[:6000], kind, source_paper_id, dumps(evidence), card_id, note_id, origin, now, now))
        for tid in topic_ids or []:
            if conn.execute("SELECT 1 FROM kb_topics WHERE id = ?", (tid,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO kb_item_topics(item_id, topic_id) VALUES (?,?)", (iid, tid))
                _touch_topic(conn, tid)
    return get_item(app, iid)


def update_item(app: AppContext, iid: str, fields: dict) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM kb_items WHERE id = ?", (iid,)).fetchone()
        if r is None:
            raise ApiError("not_found", "知识条目不存在", 404)
        title = (fields.get("title") or r["title"]).strip()[:200]
        content = fields["content"][:6000] if fields.get("content") is not None else r["content"]
        kind = fields.get("kind") if fields.get("kind") in KINDS else r["kind"]
        conn.execute("UPDATE kb_items SET title=?, content=?, kind=?, updated_at=? WHERE id=?", (title, content, kind, utcnow(), iid))
        if fields.get("topic_ids") is not None:
            conn.execute("DELETE FROM kb_item_topics WHERE item_id = ?", (iid,))
            for tid in fields["topic_ids"]:
                if conn.execute("SELECT 1 FROM kb_topics WHERE id = ?", (tid,)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO kb_item_topics(item_id, topic_id) VALUES (?,?)", (iid, tid))
                    _touch_topic(conn, tid)
    return get_item(app, iid)


def delete_item(app: AppContext, iid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM kb_items WHERE id = ?", (iid,)).rowcount == 0:
            raise ApiError("not_found", "知识条目不存在", 404)
        conn.execute("DELETE FROM kb_relations WHERE (from_kind='item' AND from_id=?) OR (to_kind='item' AND to_id=?)", (iid, iid))


# ---------------- 候选 ----------------
def _cand(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "paper_id": r["paper_id"], "kind": r["kind"], "title": r["title"], "content": r["content"],
            "evidence": loads(r["evidence_json"], []), "origin_kind": r["origin_kind"], "origin_ref": r["origin_ref"],
            "status": r["status"], "item_id": r["item_id"], "created_at": r["created_at"]}


def list_candidates(app: AppContext, paper_id: str | None = None, status: str = "pending") -> list[dict]:
    with app.db.session() as conn:
        if paper_id:
            rows = conn.execute("SELECT * FROM kb_candidates WHERE paper_id = ? AND status = ? ORDER BY created_at", (paper_id, status)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM kb_candidates WHERE status = ? ORDER BY created_at DESC LIMIT 200", (status,)).fetchall()
        out = []
        for r in rows:
            d = _cand(r)
            p = conn.execute("SELECT title FROM papers WHERE id = ?", (r["paper_id"],)).fetchone()
            d["paper_title"] = p["title"] if p else None
            out.append(d)
    return out


def add_candidate(app: AppContext, *, paper_id: str, kind: str, title: str, content: str, evidence: list[dict],
                  origin_kind: str, origin_ref: str | None) -> str | None:
    """写入候选；与已有条目/候选标题重复则跳过。"""
    from ..discovery.models import norm_title

    nt = norm_title(title)
    with app.db.session() as conn:
        for r in conn.execute("SELECT title FROM kb_candidates WHERE paper_id = ?", (paper_id,)).fetchall():
            if norm_title(r["title"]) == nt:
                return None
        for r in conn.execute("SELECT title FROM kb_items WHERE source_paper_id = ?", (paper_id,)).fetchall():
            if norm_title(r["title"]) == nt:
                return None
        cid = new_id()
        conn.execute("INSERT INTO kb_candidates(id, paper_id, kind, title, content, evidence_json, origin_kind, origin_ref, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, paper_id, kind if kind in KINDS else "other", title[:200], content[:3000], dumps(evidence), origin_kind, origin_ref, utcnow()))
    return cid


def accept_candidate(app: AppContext, cid: str, *, topic_id: str | None, new_topic: str | None, title: str | None, content: str | None,
                     kind: str | None) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM kb_candidates WHERE id = ?", (cid,)).fetchone()
    if r is None:
        raise ApiError("not_found", "候选不存在", 404)
    if r["status"] != "pending":
        raise ApiError("conflict", "该候选已经处理过了", 409)
    topic_ids = [topic_id] if topic_id else []
    if new_topic:
        topic_ids.append(create_topic(app, new_topic, source="manual")["id"])
    refs = [{"paper_id": e.get("paper_id"), "ord": e.get("ord")} if e.get("kind") != "note" else {"kind": "note", "note_id": e.get("note_id")}
            for e in loads(r["evidence_json"], [])]
    item = create_item(app, title=title or r["title"], content=content if content is not None else r["content"], kind=kind or r["kind"],
                       topic_ids=topic_ids, source_paper_id=r["paper_id"], evidence_refs=refs, origin="candidate",
                       card_id=r["origin_ref"] if r["origin_kind"] == "card" else None,
                       note_id=r["origin_ref"] if r["origin_kind"] == "note" else None)
    with app.db.session() as conn:
        conn.execute("UPDATE kb_candidates SET status='accepted', item_id=? WHERE id=?", (item["id"], cid))
    return item


def reject_candidate(app: AppContext, cid: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("UPDATE kb_candidates SET status='rejected' WHERE id=? AND status='pending'", (cid,))
    if cur.rowcount == 0:
        raise ApiError("not_found", "候选不存在或已处理", 404)


# ---------------- 关系 ----------------
def _relation(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "from_kind": r["from_kind"], "from_id": r["from_id"], "to_kind": r["to_kind"], "to_id": r["to_id"],
            "relation": r["relation"], "relation_label": RELATIONS.get(r["relation"], r["relation"]), "description": r["description"],
            "evidence": loads(r["evidence_json"], []), "ai_inferred": bool(r["ai_inferred"]), "status": r["status"], "created_at": r["created_at"]}


def create_relation(app: AppContext, *, from_kind: str, from_id: str, to_kind: str, to_id: str, relation: str, description: str,
                    evidence_refs: list[dict], ai_inferred: bool = False, status: str = "confirmed") -> dict:
    if relation not in RELATIONS:
        raise ApiError("bad_request", "不支持的关系类型")
    evidence = build_evidence(app, evidence_refs)
    if not evidence:
        raise ApiError("bad_request", "建立学术关系必须提供至少一条能在论文中核实的证据")
    rid = new_id()
    with app.db.session() as conn:
        for kind, id_ in ((from_kind, from_id), (to_kind, to_id)):
            tbl = "kb_items" if kind == "item" else "papers"
            if conn.execute(f"SELECT 1 FROM {tbl} WHERE id = ?", (id_,)).fetchone() is None:
                raise ApiError("not_found", "关系的一端不存在", 404)
        conn.execute("INSERT INTO kb_relations(id, from_kind, from_id, to_kind, to_id, relation, description, evidence_json, ai_inferred, status, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, from_kind, from_id, to_kind, to_id, relation, description[:500], dumps(evidence), int(ai_inferred), status, utcnow()))
        return _relation(conn.execute("SELECT * FROM kb_relations WHERE id = ?", (rid,)).fetchone())


def list_relations(app: AppContext, topic_id: str | None = None, status: str | None = None) -> list[dict]:
    with app.db.session() as conn:
        sql, args = "SELECT * FROM kb_relations", []
        where = []
        if status:
            where.append("status = ?")
            args.append(status)
        if topic_id:
            where.append("((from_kind='item' AND from_id IN (SELECT item_id FROM kb_item_topics WHERE topic_id=?)) "
                         "OR (to_kind='item' AND to_id IN (SELECT item_id FROM kb_item_topics WHERE topic_id=?)))")
            args += [topic_id, topic_id]
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [_relation(r) for r in conn.execute(sql + " ORDER BY created_at DESC", args).fetchall()]


def set_relation_status(app: AppContext, rid: str, status: str) -> dict:
    with app.db.session() as conn:
        if conn.execute("UPDATE kb_relations SET status = ? WHERE id = ?", (status, rid)).rowcount == 0:
            raise ApiError("not_found", "关系不存在", 404)
        return _relation(conn.execute("SELECT * FROM kb_relations WHERE id = ?", (rid,)).fetchone())


def delete_relation(app: AppContext, rid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM kb_relations WHERE id = ?", (rid,)).rowcount == 0:
            raise ApiError("not_found", "关系不存在", 404)


# ---------------- 论文 ↔ 主题 ----------------
def set_paper_topic(app: AppContext, paper_id: str, topic_id: str, *, source: str = "manual", status: str = "confirmed") -> None:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        if conn.execute("SELECT 1 FROM kb_topics WHERE id = ?", (topic_id,)).fetchone() is None:
            raise ApiError("not_found", "主题不存在", 404)
        conn.execute("INSERT INTO kb_paper_topics(paper_id, topic_id, source, status) VALUES (?,?,?,?) "
                     "ON CONFLICT(paper_id, topic_id) DO UPDATE SET status = excluded.status, source = excluded.source",
                     (paper_id, topic_id, source, status))
        _touch_topic(conn, topic_id)


TAG_MAX = 30


def paper_tags(conn: sqlite3.Connection, paper_ids: list[str]) -> dict[str, list[dict]]:
    """一次查询取出多篇论文的标签（已确认的主题），按名称排序。"""
    out: dict[str, list[dict]] = {pid: [] for pid in paper_ids}
    if not paper_ids:
        return out
    marks = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT kp.paper_id, t.id, t.name FROM kb_paper_topics kp JOIN kb_topics t ON t.id = kp.topic_id "
        f"WHERE kp.status = 'confirmed' AND t.status = 'active' AND kp.paper_id IN ({marks}) ORDER BY lower(t.name)",
        paper_ids,
    ).fetchall()
    for r in rows:
        out[r["paper_id"]].append({"id": r["id"], "name": r["name"]})
    return out


def add_paper_tag(app: AppContext, paper_id: str, name: str) -> dict:
    """给论文加标签：同名（不区分大小写）的标签已存在就复用，否则新建；重复添加不报错。"""
    name = " ".join((name or "").split())
    if not name:
        raise ApiError("bad_request", "标签不能为空")
    if len(name) > TAG_MAX:
        raise ApiError("bad_request", f"标签最多 {TAG_MAX} 个字符")
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        row = conn.execute("SELECT id, name FROM kb_topics WHERE lower(name) = lower(?) AND status = 'active'", (name,)).fetchone()
    tag = {"id": row["id"], "name": row["name"]} if row else None
    if tag is None:
        created = create_topic(app, name)
        tag = {"id": created["id"], "name": created["name"]}
    set_paper_topic(app, paper_id, tag["id"])
    return tag


def remove_paper_topic(app: AppContext, paper_id: str, topic_id: str) -> None:
    with app.db.session() as conn:
        conn.execute("DELETE FROM kb_paper_topics WHERE paper_id = ? AND topic_id = ?", (paper_id, topic_id))


def topic_papers(app: AppContext, topic_id: str, status: str = "confirmed") -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.id, p.title, p.year, p.doi, kp.source FROM kb_paper_topics kp JOIN papers p ON p.id = kp.paper_id "
            "WHERE kp.topic_id = ? AND kp.status = ? ORDER BY p.title", (topic_id, status)).fetchall()
    return [dict(r) for r in rows]


# ---------------- 画像 ----------------
def get_meta(app: AppContext) -> dict:
    with app.db.session() as conn:
        rows = conn.execute("SELECT key, value_json FROM profile_meta").fetchall()
    m = {r["key"]: loads(r["value_json"]) for r in rows}
    return {"interests": m.get("interests") or [], "explanation_depth": m.get("explanation_depth"),
            "use_in_prompts": bool(m.get("use_in_prompts", False))}


def put_meta(app: AppContext, fields: dict) -> dict:
    with app.db.session() as conn:
        for k in ("interests", "explanation_depth", "use_in_prompts"):
            if k not in fields:
                continue
            v = fields[k]
            if k == "explanation_depth" and v is not None and v not in DEPTHS:
                raise ApiError("bad_request", "解释深度只能是 brief / standard / deep")
            if k == "interests":
                v = [str(x).strip()[:60] for x in (v or []) if str(x).strip()][:20]
            if k == "use_in_prompts":
                v = bool(v)
            conn.execute("INSERT INTO profile_meta(key, value_json) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                         (k, dumps(v)))
    return get_meta(app)


def _concept(r) -> dict:
    return {"id": r["id"], "name": r["name"], "state": r["state"], "state_label": STATES.get(r["state"], r["state"]), "note": r["note"],
            "origin": r["origin"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_concepts(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        return [_concept(r) for r in conn.execute("SELECT * FROM profile_concepts ORDER BY updated_at DESC").fetchall()]


def upsert_concept(app: AppContext, name: str, state: str, note: str = "", origin: str = "user") -> dict:
    name = name.strip()
    if not name:
        raise ApiError("bad_request", "概念名称不能为空")
    if state not in STATES:
        raise ApiError("bad_request", "状态必须是 mastered / learning / unsure / unknown")
    now = utcnow()
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO profile_concepts(id, name, state, note, origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET state = excluded.state, note = excluded.note, updated_at = excluded.updated_at",
            (new_id(), name[:120], state, note[:500], origin, now, now))
        return _concept(conn.execute("SELECT * FROM profile_concepts WHERE name = ?", (name[:120],)).fetchone())


def update_concept(app: AppContext, cid: str, state: str | None, note: str | None, name: str | None) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM profile_concepts WHERE id = ?", (cid,)).fetchone()
        if r is None:
            raise ApiError("not_found", "概念不存在", 404)
        if state is not None and state not in STATES:
            raise ApiError("bad_request", "状态不正确")
        conn.execute("UPDATE profile_concepts SET state=?, note=?, name=?, updated_at=? WHERE id=?",
                     (state or r["state"], note if note is not None else r["note"], (name or r["name"]).strip()[:120], utcnow(), cid))
        return _concept(conn.execute("SELECT * FROM profile_concepts WHERE id = ?", (cid,)).fetchone())


def delete_concept(app: AppContext, cid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM profile_concepts WHERE id = ?", (cid,)).rowcount == 0:
            raise ApiError("not_found", "概念不存在", 404)


def list_suggestions(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM profile_suggestions WHERE status = 'pending' ORDER BY created_at DESC").fetchall()
    return [{"id": r["id"], "name": r["name"], "state": r["state"], "state_label": STATES.get(r["state"]), "basis": r["basis"],
             "evidence": loads(r["evidence_json"], []), "created_at": r["created_at"]} for r in rows]
