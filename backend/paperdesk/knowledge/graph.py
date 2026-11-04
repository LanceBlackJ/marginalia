"""知识库的关系图数据与批量操作。

关系图：节点 = 知识条目 / 主题 / 论文；边 = 用户已确认的学术关系（实线）、AI 建议但未确认的关系（虚线，标“待确认”）、
以及条目归属主题、条目来自论文这两种结构线（细线，不是学术关系）。
批量操作：多选条目批量加入/移出主题、批量删除；批量合并主题（条目和论文都并入目标主题，被并入的主题删除）。
"""
from __future__ import annotations

from ..context import AppContext
from ..util import ApiError, utcnow
from . import store


def build_graph(app: AppContext, topic_id: str | None = None, kinds: list[str] | None = None) -> dict:
    items = store.list_items(app, topic_id=topic_id)
    if kinds:
        items = [i for i in items if i["kind"] in kinds]
    item_ids = {i["id"] for i in items}
    topics = {t["id"]: t for t in store.list_topics(app)}
    rels = store.list_relations(app, topic_id=topic_id)
    paper_ids: set[str] = {i["source_paper"]["id"] for i in items if i["source_paper"]}
    keep = []
    for r in rels:
        ends = [(r["from_kind"], r["from_id"]), (r["to_kind"], r["to_id"])]
        if all((k == "item" and i in item_ids) or k == "paper" for k, i in ends) and any(k == "item" for k, _ in ends):
            keep.append(r)
            paper_ids.update(i for k, i in ends if k == "paper")
    with app.db.session() as conn:
        papers = {r["id"]: r for r in conn.execute(
            f"SELECT id, title, year FROM papers WHERE id IN ({','.join('?' * len(paper_ids))})", list(paper_ids)).fetchall()} if paper_ids else {}
    nodes, edges = [], []
    used_topics: set[str] = set()
    for i in items:
        nodes.append({"id": f"item:{i['id']}", "type": "item", "label": i["title"], "kind": i["kind"], "ref": i["id"]})
        for tid in i["topic_ids"]:
            if tid in topics and (not topic_id or tid == topic_id):
                used_topics.add(tid)
                edges.append({"id": f"in:{i['id']}:{tid}", "source": f"item:{i['id']}", "target": f"topic:{tid}", "type": "member",
                              "label": "", "status": "confirmed"})
        if i["source_paper"] and i["source_paper"]["id"] in papers:
            edges.append({"id": f"from:{i['id']}", "source": f"item:{i['id']}", "target": f"paper:{i['source_paper']['id']}", "type": "source",
                          "label": "", "status": "confirmed"})
    for tid in used_topics:
        nodes.append({"id": f"topic:{tid}", "type": "topic", "label": topics[tid]["name"], "kind": None, "ref": tid})
    for pid, p in papers.items():
        nodes.append({"id": f"paper:{pid}", "type": "paper", "label": p["title"], "kind": None, "ref": pid, "year": p["year"]})
    for r in keep:
        edges.append({"id": f"rel:{r['id']}", "source": f"{r['from_kind']}:{r['from_id']}", "target": f"{r['to_kind']}:{r['to_id']}", "type": "relation",
                      "label": r["relation_label"], "status": r["status"], "ai_inferred": r["ai_inferred"], "ref": r["id"],
                      "description": r["description"]})
    return {"nodes": nodes, "edges": edges, "counts": {"items": len(items), "relations": len(keep),
                                                      "suggested": sum(1 for r in keep if r["status"] == "suggested")}}


def bulk_items(app: AppContext, item_ids: list[str], action: str, topic_id: str | None = None) -> dict:
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise ApiError("bad_request", "没有选中任何条目")
    with app.db.session() as conn:
        existing = [r[0] for r in conn.execute(f"SELECT id FROM kb_items WHERE id IN ({','.join('?' * len(ids))})", ids)]
        if action in ("add_topic", "remove_topic"):
            if not topic_id or conn.execute("SELECT 1 FROM kb_topics WHERE id = ?", (topic_id,)).fetchone() is None:
                raise ApiError("not_found", "主题不存在", 404)
        if action == "add_topic":
            for iid in existing:
                conn.execute("INSERT OR IGNORE INTO kb_item_topics(item_id, topic_id) VALUES (?,?)", (iid, topic_id))
            conn.execute("UPDATE kb_topics SET updated_at = ? WHERE id = ?", (utcnow(), topic_id))
        elif action == "remove_topic":
            for iid in existing:
                conn.execute("DELETE FROM kb_item_topics WHERE item_id = ? AND topic_id = ?", (iid, topic_id))
        elif action == "delete":
            for iid in existing:
                conn.execute("DELETE FROM kb_items WHERE id = ?", (iid,))
                conn.execute("DELETE FROM kb_relations WHERE (from_kind='item' AND from_id=?) OR (to_kind='item' AND to_id=?)", (iid, iid))
        else:
            raise ApiError("bad_request", "不支持的批量操作")
    return {"affected": len(existing), "skipped": len(ids) - len(existing)}


def merge_topics(app: AppContext, source_ids: list[str], target_id: str) -> dict:
    sources = [s for s in dict.fromkeys(source_ids) if s != target_id]
    if not sources:
        raise ApiError("bad_request", "请选择要并入的其它主题")
    with app.db.session() as conn:
        for tid in [target_id, *sources]:
            if conn.execute("SELECT 1 FROM kb_topics WHERE id = ?", (tid,)).fetchone() is None:
                raise ApiError("not_found", "主题不存在", 404)
        moved_items = moved_papers = 0
        for sid in sources:
            for (iid,) in conn.execute("SELECT item_id FROM kb_item_topics WHERE topic_id = ?", (sid,)).fetchall():
                cur = conn.execute("INSERT OR IGNORE INTO kb_item_topics(item_id, topic_id) VALUES (?,?)", (iid, target_id))
                moved_items += cur.rowcount
            for r in conn.execute("SELECT paper_id, source, status FROM kb_paper_topics WHERE topic_id = ?", (sid,)).fetchall():
                cur = conn.execute("INSERT OR IGNORE INTO kb_paper_topics(paper_id, topic_id, source, status) VALUES (?,?,?,?)",
                                   (r["paper_id"], target_id, r["source"], r["status"]))
                moved_papers += cur.rowcount
            conn.execute("DELETE FROM kb_paper_topics WHERE topic_id = ?", (sid,))
            conn.execute("DELETE FROM kb_item_topics WHERE topic_id = ?", (sid,))
            conn.execute("DELETE FROM kb_topics WHERE id = ?", (sid,))
        conn.execute("UPDATE kb_topics SET updated_at = ? WHERE id = ?", (utcnow(), target_id))
    target = next(t for t in store.list_topics(app) if t["id"] == target_id)
    return {"topic": target, "merged": len(sources), "moved_items": moved_items, "moved_papers": moved_papers}
