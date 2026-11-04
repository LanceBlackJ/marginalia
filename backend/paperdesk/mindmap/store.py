"""思维导图的持久化：节点表、生成状态表、节点与解释卡片的关联，以及节点的增删改查。

证据只存“段落序号 / 图表 id / 公式 id”，读取时再从数据库解析出页码、章节、真实原文片段和区域，
所以返回给前端的 quote 永远来自论文本身，而不是模型生成的文字。
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

from ..ai import evidence as ev_lib
from ..context import AppContext
from ..db import register_schema
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import tree as T

register_schema("mindmap", """
CREATE TABLE IF NOT EXISTS mindmap_nodes (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  view TEXT NOT NULL,                              -- logic | method | experiment
  parent_id TEXT REFERENCES mindmap_nodes(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'topic',
  evidence_json TEXT NOT NULL DEFAULT '{}',        -- {ords:[段落序号], figure_ids:[], equation_ids:[], derived:bool}
  origin TEXT NOT NULL DEFAULT 'ai',               -- ai | user
  user_edited INTEGER NOT NULL DEFAULT 0,          -- 用户改过/移动过/新建的节点：重新生成时永远保留
  collapsed INTEGER NOT NULL DEFAULT 0,
  x REAL, y REAL,                                  -- 用户拖动后的位置；为空则自动布局
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mindmap_nodes_view ON mindmap_nodes(paper_id, view, parent_id, ord);

CREATE TABLE IF NOT EXISTS mindmap_runs (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  view TEXT NOT NULL,
  status TEXT NOT NULL,                            -- pending | running | ready | failed
  job_id TEXT,
  scope TEXT,                                      -- 为空表示整张图；否则是正在重新生成的分支根节点 id
  model TEXT,
  error TEXT,
  verification_json TEXT,
  generated_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, view)
);

CREATE TABLE IF NOT EXISTS mindmap_node_cards (
  node_id TEXT NOT NULL REFERENCES mindmap_nodes(id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES insight_cards(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (node_id, card_id)
);
""")

MAX_USER_DEPTH = 8
QUOTE_LIST = 200
QUOTE_DETAIL = 360


# ---------------------------------------------------------------- 基础读写
def _ensure_paper(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "论文不存在", 404)
    return row


def check_view(view: str) -> str:
    if view not in T.VIEWS:
        raise ApiError("bad_request", f"视角必须是 {', '.join(T.VIEWS)} 之一")
    return view


def _evidence(row: sqlite3.Row) -> dict:
    d = loads(row["evidence_json"], {}) or {}
    return {"ords": [o for o in d.get("ords", []) if isinstance(o, int)],
            "figure_ids": [o for o in d.get("figure_ids", []) if isinstance(o, int)],
            "equation_ids": [o for o in d.get("equation_ids", []) if isinstance(o, int)],
            "derived": bool(d.get("derived"))}


def evidence_json(ords: list[int], figure_ids: list[int], equation_ids: list[int], derived: bool = False) -> str:
    return dumps({"ords": ords, "figure_ids": figure_ids, "equation_ids": equation_ids, "derived": derived})


def to_old(r: sqlite3.Row) -> T.OldNode:
    e = _evidence(r)
    return T.OldNode(
        id=r["id"], parent_id=r["parent_id"], ord=r["ord"], title=r["title"], summary=r["summary"], kind=r["kind"],
        ords=e["ords"], figure_ids=e["figure_ids"], equation_ids=e["equation_ids"],
        user_edited=bool(r["user_edited"]), origin=r["origin"],
    )


def subtree_ids(conn: sqlite3.Connection, node_id: str) -> list[str]:
    rows = conn.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM mindmap_nodes WHERE id = ? "
        "UNION ALL SELECT n.id FROM mindmap_nodes n JOIN sub ON n.parent_id = sub.id) SELECT id FROM sub", (node_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def load_old(conn: sqlite3.Connection, paper_id: str, view: str, scope_root_id: str | None = None) -> list[T.OldNode]:
    rows = conn.execute("SELECT * FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, view)).fetchall()
    nodes = [to_old(r) for r in rows]
    if scope_root_id is None:
        return nodes
    keep = set(subtree_ids(conn, scope_root_id))
    return [n for n in nodes if n.id in keep]


def apply_plan(conn: sqlite3.Connection, paper_id: str, view: str, plan: T.MergePlan) -> None:
    """在一个事务里执行合并计划（由调用方的 db.session 提交；中途出错整体回滚，旧图不受影响）。"""
    now = utcnow()
    for nid in plan.deletes:
        conn.execute("DELETE FROM mindmap_nodes WHERE id = ?", (nid,))
    for nid, f in plan.updates.items():
        conn.execute(
            "UPDATE mindmap_nodes SET title=?, summary=?, kind=?, evidence_json=?, updated_at=? WHERE id=?",
            (f["title"], f["summary"], f["kind"], evidence_json(f["ords"], f["figure_ids"], f["equation_ids"], f["derived"]), now, nid),
        )
    for f in plan.inserts:  # 先父后子（merge_trees 按前序产生）
        conn.execute(
            "INSERT INTO mindmap_nodes(id, paper_id, view, parent_id, ord, title, summary, kind, evidence_json, origin, "
            "user_edited, collapsed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'ai',0,0,?,?)",
            (f["id"], paper_id, view, f["parent_id"], 0, f["title"], f["summary"], f["kind"],
             evidence_json(f["ords"], f["figure_ids"], f["equation_ids"], f["derived"]), now, now),
        )
    for parent_id, ids in plan.order.items():
        for i, nid in enumerate(ids):
            conn.execute("UPDATE mindmap_nodes SET ord = ?, parent_id = ? WHERE id = ?", (i, parent_id, nid))


# ---------------------------------------------------------------- 生成状态
def get_run(conn: sqlite3.Connection, paper_id: str, view: str) -> dict:
    r = conn.execute("SELECT * FROM mindmap_runs WHERE paper_id = ? AND view = ?", (paper_id, view)).fetchone()
    if r is None:
        return {"status": "none", "job_id": None, "scope": None, "model": None, "error": None,
                "verification": None, "generated_at": None, "updated_at": None}
    return {"status": r["status"], "job_id": r["job_id"], "scope": r["scope"], "model": r["model"], "error": r["error"],
            "verification": loads(r["verification_json"]), "generated_at": r["generated_at"], "updated_at": r["updated_at"]}


def upsert_run(app: AppContext, paper_id: str, view: str, status: str, **fields: Any) -> None:
    """原子 upsert：入队线程与任务线程可能同时写同一行。"""
    cols = ["paper_id", "view", "status", "updated_at", *fields]
    vals = [paper_id, view, status, utcnow(), *fields.values()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols[2:])
    with app.db.session() as conn:
        conn.execute(
            f"INSERT INTO mindmap_runs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(paper_id, view) DO UPDATE SET {updates}", vals,
        )


# ---------------------------------------------------------------- 证据解析
def resolve_refs(conn: sqlite3.Connection, paper_id: str, ords: set[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    items = sorted(ords)
    for i in range(0, len(items), 400):
        chunk = items[i : i + 400]
        rows = conn.execute(
            "SELECT p.ord, p.page, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title FROM passages p "
            f"LEFT JOIN sections s ON s.id = p.section_id WHERE p.paper_id = ? AND p.ord IN ({','.join('?' * len(chunk))})",
            (paper_id, *chunk),
        ).fetchall()
        for r in rows:
            out[r["ord"]] = {"ord": r["ord"], "page": r["page"], "section_title": r["section_title"], "text": r["text"],
                             "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]]}
    return out


def ref_items(passages: dict[int, dict], ords: list[int], hint: str, limit: int) -> list[dict]:
    items = []
    for o in ords:
        p = passages.get(o)
        if not p:
            continue  # 论文被重新解析、段落已不存在：不展示失效的依据
        items.append({"ord": o, "page": p["page"], "section_title": p["section_title"],
                      "quote": ev_lib.best_excerpt(p["text"], hint, limit), "bbox": p["bbox"],
                      "kind": "paper_text", "ref": f"P{o}"})
    return items


def resolve_entities(conn: sqlite3.Connection, paper_id: str, fig_ids: list[int], eq_ids: list[int]) -> tuple[list[dict], list[dict]]:
    figs: list[dict] = []
    eqs: list[dict] = []
    if fig_ids:
        rows = conn.execute(
            "SELECT f.*, c.text AS caption FROM figures f LEFT JOIN passages c ON c.paper_id = f.paper_id AND c.ord = f.caption_ord "
            f"WHERE f.paper_id = ? AND f.id IN ({','.join('?' * len(fig_ids))})", (paper_id, *fig_ids),
        ).fetchall()
        by = {r["id"]: r for r in rows}
        figs = [{"id": r["id"], "page": r["page"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]], "kind": r["kind"],
                 "label": r["label"], "caption": (r["caption"] or "")[:200]} for i in fig_ids if (r := by.get(i))]
    if eq_ids:
        rows = conn.execute(
            f"SELECT * FROM equations WHERE paper_id = ? AND id IN ({','.join('?' * len(eq_ids))})", (paper_id, *eq_ids),
        ).fetchall()
        by = {r["id"]: r for r in rows}
        eqs = [{"id": r["id"], "page": r["page"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]], "number": r["number"]}
               for i in eq_ids if (r := by.get(i))]
    return figs, eqs


def _preorder(rows: list[sqlite3.Row]) -> list[tuple[sqlite3.Row, int]]:
    kids: dict[str | None, list[sqlite3.Row]] = {}
    for r in sorted(rows, key=lambda x: (x["ord"], x["created_at"], x["id"])):
        kids.setdefault(r["parent_id"], []).append(r)
    out: list[tuple[sqlite3.Row, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for r in kids.get(parent, []):
            out.append((r, depth))
            walk(r["id"], depth + 1)

    walk(None, 1)
    return out


def _node_dict(r: sqlite3.Row, depth: int, child_count: int, passages: dict[int, dict], quote_limit: int) -> dict:
    e = _evidence(r)
    return {
        "id": r["id"], "paper_id": r["paper_id"], "view": r["view"], "parent_id": r["parent_id"], "ord": r["ord"],
        "depth": depth, "child_count": child_count, "title": r["title"], "summary": r["summary"], "kind": r["kind"],
        "kind_label": T.KIND_LABELS.get(r["kind"], "要点"), "origin": r["origin"], "user_edited": bool(r["user_edited"]),
        "collapsed": bool(r["collapsed"]), "x": r["x"], "y": r["y"], "evidence": e,
        "refs": ref_items(passages, e["ords"], f"{r['title']} {r['summary']}", quote_limit),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def get_tree(app: AppContext, paper_id: str, view: str) -> dict:
    check_view(view)
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        rows = conn.execute("SELECT * FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, view)).fetchall()
        run = get_run(conn, paper_id, view)
        ordered = _preorder(rows)
        all_ords = {o for r, _ in ordered for o in _evidence(r)["ords"]}
        passages = resolve_refs(conn, paper_id, all_ords)
    counts: dict[str, int] = {}
    for r, _ in ordered:
        if r["parent_id"]:
            counts[r["parent_id"]] = counts.get(r["parent_id"], 0) + 1
    nodes = [_node_dict(r, d, counts.get(r["id"], 0), passages, QUOTE_LIST) for r, d in ordered]
    status = run["status"]
    if status == "none" and nodes:
        status = "ready"
    return {
        "paper_id": paper_id, "view": view, "view_label": T.VIEW_LABELS[view], "status": status, "job_id": run["job_id"],
        "scope": run["scope"], "model": run["model"], "error": run["error"], "verification": run["verification"],
        "generated_at": run["generated_at"], "nodes": nodes,
        "stats": {"count": len(nodes), "max_depth": max((d for _, d in ordered), default=0),
                  "user_nodes": sum(1 for r, _ in ordered if r["origin"] == "user"),
                  "edited_nodes": sum(1 for r, _ in ordered if r["user_edited"])},
    }


def overview(app: AppContext, paper_id: str) -> dict:
    """三个视角的生成状态与节点数，供入口按钮/空状态判断。"""
    out = {}
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        for v in T.VIEWS:
            n = conn.execute("SELECT COUNT(*) FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, v)).fetchone()[0]
            run = get_run(conn, paper_id, v)
            st = run["status"] if run["status"] != "none" or not n else "ready"
            out[v] = {"status": st, "count": n, "job_id": run["job_id"], "generated_at": run["generated_at"]}
    return out


# ---------------------------------------------------------------- 节点详情
def _node_row(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM mindmap_nodes WHERE id = ?", (node_id,)).fetchone()
    if r is None:
        raise ApiError("not_found", "思维导图节点不存在", 404)
    return r


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def get_node_detail(app: AppContext, node_id: str) -> dict:
    with app.db.session() as conn:
        r = _node_row(conn, node_id)
        paper_id = r["paper_id"]
        e = _evidence(r)
        passages = resolve_refs(conn, paper_id, set(e["ords"]))
        n_children = conn.execute("SELECT COUNT(*) FROM mindmap_nodes WHERE parent_id = ?", (node_id,)).fetchone()[0]
        # 路径（根 → 当前）
        path: list[dict] = []
        cur: sqlite3.Row | None = r
        depth = 0
        while cur is not None:
            depth += 1
            if cur["id"] != node_id:
                path.append({"id": cur["id"], "title": cur["title"]})
            cur = conn.execute("SELECT * FROM mindmap_nodes WHERE id = ?", (cur["parent_id"],)).fetchone() if cur["parent_id"] else None
        path.reverse()
        node = _node_dict(r, depth, n_children, passages, QUOTE_DETAIL)
        figs, eqs = resolve_entities(conn, paper_id, e["figure_ids"], e["equation_ids"])

        # 解释卡片：显式关联（点“解释这个节点”生成的）+ 引用了同一段原文的已保存卡片
        cards: list[dict] = []
        seen: set[str] = set()
        for c in conn.execute(
            "SELECT c.* FROM mindmap_node_cards l JOIN insight_cards c ON c.id = l.card_id WHERE l.node_id = ? "
            "AND c.status = 'complete' ORDER BY c.created_at DESC", (node_id,),
        ).fetchall():
            cards.append(_card_brief(c, "explain"))
            seen.add(c["id"])
        if e["ords"]:
            ord_set = set(e["ords"])
            for c in conn.execute(
                "SELECT * FROM insight_cards WHERE paper_id = ? AND status = 'complete' AND saved = 1 ORDER BY created_at DESC LIMIT 200",
                (paper_id,),
            ).fetchall():
                if c["id"] in seen or len(cards) >= 12:
                    continue
                if any(isinstance(x, dict) and x.get("ord") in ord_set for x in (loads(c["evidence_json"], []) or [])):
                    cards.append(_card_brief(c, "evidence"))
                    seen.add(c["id"])

        notes = []
        if e["ords"] or seen:
            for n in conn.execute("SELECT * FROM notes WHERE paper_id = ? ORDER BY updated_at DESC", (paper_id,)).fetchall():
                if (n["passage_ord"] in e["ords"]) or (n["card_id"] and n["card_id"] in seen):
                    notes.append({"id": n["id"], "text": n["text"][:300], "page": n["page"], "passage_ord": n["passage_ord"],
                                  "card_id": n["card_id"]})
                if len(notes) >= 10:
                    break

        code = []
        if e["ords"] and _table_exists(conn, "paper_code_links"):  # F2 的论文↔代码关联：模块引用的原文段落与本节点依据有交集才算相关
            ord_set = set(e["ords"])
            for l in conn.execute(
                "SELECT * FROM paper_code_links WHERE paper_id = ? AND status != 'rejected' ORDER BY (status != 'confirmed')", (paper_id,),
            ).fetchall():
                mod = loads(l["module_json"], {}) or {}
                if ord_set & {o for o in mod.get("passage_ords", []) if isinstance(o, int)}:
                    code.append({"id": l["id"], "repo_id": l["repo_id"], "module": mod.get("name"), "path": l["path"],
                                 "line_start": l["line_start"], "line_end": l["line_end"], "confidence": l["confidence"],
                                 "status": l["status"]})
                if len(code) >= 6:
                    break

        children = [{"id": c["id"], "title": c["title"], "kind": c["kind"]} for c in conn.execute(
            "SELECT id, title, kind FROM mindmap_nodes WHERE parent_id = ? ORDER BY ord", (node_id,)).fetchall()]
    node.update({"path": path, "figures": figs, "equations": eqs, "cards": cards, "notes": notes, "code": code, "children": children})
    return node


def _card_brief(c: sqlite3.Row, relation: str) -> dict:
    ans = loads(c["answer_json"], {}) or {}
    return {"id": c["id"], "title": c["object_label"] or c["question"], "question": c["question"], "relation": relation,
            "direct": (ans.get("direct") or "")[:160], "saved": bool(c["saved"]), "created_at": c["created_at"]}


def link_card(app: AppContext, node_id: str, card_id: str) -> None:
    with app.db.session() as conn:
        conn.execute("INSERT OR IGNORE INTO mindmap_node_cards(node_id, card_id, created_at) VALUES (?,?,?)",
                     (node_id, card_id, utcnow()))


# ---------------------------------------------------------------- 编辑
def _set_order(conn: sqlite3.Connection, parent_id: str | None, ids: list[str]) -> None:
    for i, nid in enumerate(ids):
        conn.execute("UPDATE mindmap_nodes SET ord = ?, parent_id = ? WHERE id = ?", (i, parent_id, nid))


def _children_ids(conn: sqlite3.Connection, parent_id: str) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM mindmap_nodes WHERE parent_id = ? ORDER BY ord, created_at", (parent_id,)).fetchall()]


def _depth(conn: sqlite3.Connection, node_id: str) -> int:
    d, cur = 0, node_id
    while cur:
        d += 1
        r = conn.execute("SELECT parent_id FROM mindmap_nodes WHERE id = ?", (cur,)).fetchone()
        cur = r["parent_id"] if r else None
        if d > 64:
            break
    return d


def _height(conn: sqlite3.Connection, node_id: str) -> int:
    kids = conn.execute("SELECT id FROM mindmap_nodes WHERE parent_id = ?", (node_id,)).fetchall()
    return 1 + max((_height(conn, k["id"]) for k in kids), default=0)


def _clean_title(title: Any) -> str:
    t = T.clean_text(title)
    if not t:
        raise ApiError("bad_request", "节点标题不能为空")
    return T.clip(t, T.USER_TITLE_MAX)


def patch_node(app: AppContext, node_id: str, changes: dict) -> dict:
    """changes：title / summary / kind / collapsed / x / y / parent_id + index（移动）。
    改了内容（标题、摘要、类型）或位置层级（移动）的节点会标记为 user_edited；折叠与坐标只是视图状态，不算编辑。"""
    now = utcnow()
    with app.db.session() as conn:
        r = _node_row(conn, node_id)
        sets: list[str] = []
        args: list[Any] = []
        edited = False
        if "title" in changes:
            sets.append("title = ?")
            args.append(_clean_title(changes["title"]))
            edited = True
        if "summary" in changes:
            sets.append("summary = ?")
            args.append(T.clip(T.clean_text(changes["summary"]), T.USER_SUMMARY_MAX))
            edited = True
        if "kind" in changes:
            k = changes["kind"]
            if k not in T.KIND_LABELS or k == "root":
                raise ApiError("bad_request", "不支持的节点类型")
            sets.append("kind = ?")
            args.append(k)
            edited = True
        if "collapsed" in changes:
            sets.append("collapsed = ?")
            args.append(int(bool(changes["collapsed"])))
        for axis in ("x", "y"):
            if axis in changes:
                v = changes[axis]
                if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)):
                    raise ApiError("bad_request", "坐标必须是有限数字或 null")
                sets.append(f"{axis} = ?")
                args.append(v)
        if "parent_id" in changes and changes["parent_id"] != r["parent_id"] or "index" in changes and "parent_id" in changes:
            _move(conn, r, changes["parent_id"], changes.get("index"))
            edited = True
        if edited:
            sets.append("user_edited = 1")
        if sets:
            sets.append("updated_at = ?")
            conn.execute(f"UPDATE mindmap_nodes SET {', '.join(sets)} WHERE id = ?", (*args, now, node_id))
    return get_node_detail(app, node_id)


def _move(conn: sqlite3.Connection, node: sqlite3.Row, new_parent_id: str | None, index: int | None) -> None:
    if node["parent_id"] is None:
        raise ApiError("bad_request", "根节点不能移动")
    if not new_parent_id:
        raise ApiError("bad_request", "需要指定新的父节点")
    parent = _node_row(conn, new_parent_id)
    if parent["paper_id"] != node["paper_id"] or parent["view"] != node["view"]:
        raise ApiError("bad_request", "只能在同一张思维导图内移动节点")
    if new_parent_id in subtree_ids(conn, node["id"]):
        raise ApiError("bad_request", "不能把节点移动到它自己的下级")
    if _depth(conn, new_parent_id) + _height(conn, node["id"]) > MAX_USER_DEPTH:
        raise ApiError("bad_request", f"层级最多 {MAX_USER_DEPTH} 层")
    old_parent = node["parent_id"]
    siblings = [i for i in _children_ids(conn, new_parent_id) if i != node["id"]]
    pos = len(siblings) if index is None else max(0, min(int(index), len(siblings)))
    siblings.insert(pos, node["id"])
    _set_order(conn, new_parent_id, siblings)
    if old_parent != new_parent_id and old_parent:
        _set_order(conn, old_parent, _children_ids(conn, old_parent))


def add_node(app: AppContext, *, parent_id: str, title: str, summary: str = "", kind: str = "topic", index: int | None = None) -> dict:
    now = utcnow()
    nid = new_id()
    title = _clean_title(title)
    if kind not in T.KIND_LABELS or kind == "root":
        kind = "topic"
    with app.db.session() as conn:
        parent = _node_row(conn, parent_id)
        if _depth(conn, parent_id) + 1 > MAX_USER_DEPTH:
            raise ApiError("bad_request", f"层级最多 {MAX_USER_DEPTH} 层")
        conn.execute(
            "INSERT INTO mindmap_nodes(id, paper_id, view, parent_id, ord, title, summary, kind, evidence_json, origin, "
            "user_edited, collapsed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'user',1,0,?,?)",
            (nid, parent["paper_id"], parent["view"], parent_id, 0, title, T.clip(T.clean_text(summary), T.USER_SUMMARY_MAX), kind,
             evidence_json([], [], []), now, now),
        )
        sibs = [i for i in _children_ids(conn, parent_id) if i != nid]
        pos = len(sibs) if index is None else max(0, min(int(index), len(sibs)))
        sibs.insert(pos, nid)
        _set_order(conn, parent_id, sibs)
        conn.execute("UPDATE mindmap_nodes SET collapsed = 0 WHERE id = ?", (parent_id,))  # 新增后展开父节点，用户才看得到
    return get_node_detail(app, nid)


def add_sibling(app: AppContext, node_id: str, *, title: str, summary: str = "", kind: str = "topic") -> dict:
    with app.db.session() as conn:
        r = _node_row(conn, node_id)
        if r["parent_id"] is None:
            raise ApiError("bad_request", "根节点没有同级节点，请添加子节点")
        idx = _children_ids(conn, r["parent_id"]).index(node_id) + 1
        parent_id = r["parent_id"]
    return add_node(app, parent_id=parent_id, title=title, summary=summary, kind=kind, index=idx)


_NODE_COLS = ("id", "paper_id", "view", "parent_id", "ord", "title", "summary", "kind", "evidence_json", "origin",
              "user_edited", "collapsed", "x", "y", "created_at", "updated_at")
MAX_RESTORE_NODES = 3000


def delete_node(app: AppContext, node_id: str) -> dict:
    """删除节点及其整棵子树。返回的 snapshot 包含被删节点的完整数据，可交给 restore_nodes 原样恢复（用于撤销）。"""
    with app.db.session() as conn:
        r = _node_row(conn, node_id)
        if r["parent_id"] is None:
            raise ApiError("bad_request", "根节点不能删除；如需重做请重新生成这张思维导图")
        ids = subtree_ids(conn, node_id)
        marks = ",".join("?" * len(ids))
        rows = [{c: row[c] for c in _NODE_COLS} for row in
                conn.execute(f"SELECT * FROM mindmap_nodes WHERE id IN ({marks})", ids).fetchall()]
        conn.execute("DELETE FROM mindmap_nodes WHERE id = ?", (node_id,))
        _set_order(conn, r["parent_id"], _children_ids(conn, r["parent_id"]))
    return {"ok": True, "removed": len(ids), "parent_id": r["parent_id"],
            "snapshot": {"parent_id": r["parent_id"], "index": r["ord"], "rows": rows}}


def restore_nodes(app: AppContext, snapshot: dict) -> dict:
    """把 delete_node 返回的 snapshot 原样恢复（同样的 id、内容、位置和顺序）。父节点必须还在、节点 id 不能已存在。"""
    parent_id = snapshot.get("parent_id")
    rows = snapshot.get("rows")
    index = snapshot.get("index")
    if not isinstance(parent_id, str) or not isinstance(rows, list) or not rows or len(rows) > MAX_RESTORE_NODES:
        raise ApiError("bad_request", "恢复数据无效")
    ids = {r.get("id") for r in rows if isinstance(r, dict)}
    if len(ids) != len(rows) or not all(isinstance(i, str) and i for i in ids):
        raise ApiError("bad_request", "恢复数据无效")
    tops = [r for r in rows if r.get("parent_id") == parent_id]
    if len(tops) != 1 or any(r.get("parent_id") not in ids | {parent_id} for r in rows):
        raise ApiError("bad_request", "恢复数据无效：必须恰好是一棵挂在原父节点下的子树")
    with app.db.session() as conn:
        parent = _node_row(conn, parent_id)
        existing = conn.execute(f"SELECT id FROM mindmap_nodes WHERE id IN ({','.join('?' * len(ids))})", list(ids)).fetchone()
        if existing:
            raise ApiError("conflict", "节点已经存在，无法恢复", 409)
        # 父节点在前、子节点在后插入（外键要求）
        order, done = [], {parent_id}
        pending = list(rows)
        while pending:
            progressed = [r for r in pending if r["parent_id"] in done]
            if not progressed:
                raise ApiError("bad_request", "恢复数据无效：节点关系不完整")
            for r in progressed:
                order.append(r)
                done.add(r["id"])
            pending = [r for r in pending if r["id"] not in done]
        for r in order:
            conn.execute(
                "INSERT INTO mindmap_nodes(id, paper_id, view, parent_id, ord, title, summary, kind, evidence_json, origin, "
                "user_edited, collapsed, x, y, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], parent["paper_id"], parent["view"], r["parent_id"], int(r.get("ord") or 0),
                 str(r.get("title") or "")[:200], str(r.get("summary") or "")[:2000], str(r.get("kind") or "topic"),
                 str(r.get("evidence_json") or "{}"), str(r.get("origin") or "user"), int(bool(r.get("user_edited"))),
                 int(bool(r.get("collapsed"))), r.get("x") if isinstance(r.get("x"), (int, float)) else None,
                 r.get("y") if isinstance(r.get("y"), (int, float)) else None, str(r.get("created_at") or utcnow()), utcnow()),
            )
        top_id = tops[0]["id"]
        sibs = [i for i in _children_ids(conn, parent_id) if i != top_id]
        at = index if isinstance(index, int) and 0 <= index <= len(sibs) else len(sibs)
        sibs.insert(at, top_id)
        _set_order(conn, parent_id, sibs)
    return {"ok": True, "restored": len(rows), "id": top_id}


def save_positions(app: AppContext, paper_id: str, view: str, items: list[dict]) -> int:
    check_view(view)
    n = 0
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        for it in items:
            x, y = it.get("x"), it.get("y")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
                continue
            cur = conn.execute("UPDATE mindmap_nodes SET x = ?, y = ? WHERE id = ? AND paper_id = ? AND view = ?",
                               (x, y, it.get("id"), paper_id, view))
            n += cur.rowcount
    return n


def reset_positions(app: AppContext, paper_id: str, view: str) -> int:
    check_view(view)
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        return conn.execute("UPDATE mindmap_nodes SET x = NULL, y = NULL WHERE paper_id = ? AND view = ?", (paper_id, view)).rowcount


def set_collapsed(app: AppContext, paper_id: str, view: str, collapsed: bool, min_depth: int = 1) -> int:
    """批量折叠/展开（“全部展开 / 收起到第二层”）。collapsed=True 时折叠所有深度 ≥ min_depth 且有子节点的节点。"""
    check_view(view)
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        rows = conn.execute("SELECT * FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, view)).fetchall()
        n = 0
        for r, depth in _preorder(rows):
            has_kids = any(x["parent_id"] == r["id"] for x in rows)
            want = int(bool(collapsed and depth >= min_depth and has_kids))
            if r["collapsed"] != want:
                conn.execute("UPDATE mindmap_nodes SET collapsed = ? WHERE id = ?", (want, r["id"]))
                n += 1
        return n


# ---------------------------------------------------------------- 搜索与导出
def search(app: AppContext, paper_id: str, q: str, view: str | None = None, limit: int = 50) -> list[dict]:
    q = T.clean_text(q).casefold()
    if not q:
        raise ApiError("bad_request", "请输入搜索关键词")
    views = [check_view(view)] if view else list(T.VIEWS)
    hits: list[dict] = []
    with app.db.session() as conn:
        _ensure_paper(conn, paper_id)
        for v in views:
            rows = conn.execute("SELECT * FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, v)).fetchall()
            by = {r["id"]: r for r in rows}
            for r, _ in _preorder(rows):
                in_title = q in r["title"].casefold()
                in_summary = q in r["summary"].casefold()
                if not (in_title or in_summary):
                    continue
                path, cur = [], by.get(r["parent_id"]) if r["parent_id"] else None
                while cur is not None:
                    path.append(cur["title"])
                    cur = by.get(cur["parent_id"]) if cur["parent_id"] else None
                path.reverse()
                hits.append({"id": r["id"], "view": v, "title": r["title"], "summary": r["summary"], "kind": r["kind"],
                             "kind_label": T.KIND_LABELS.get(r["kind"], "要点"), "path": path,
                             "match": "title" if in_title else "summary"})
    hits.sort(key=lambda h: (h["match"] != "title", h["view"] != (view or h["view"])))
    return hits[:limit]


def export_tree(app: AppContext, paper_id: str, view: str, fmt: str) -> tuple[str, str, str]:
    """返回 (内容, MIME, 建议文件名)。"""
    tree = get_tree(app, paper_id, view)
    if not tree["nodes"]:
        raise ApiError("not_found", "这个视角还没有思维导图可导出", 404)
    with app.db.session() as conn:
        title = _ensure_paper(conn, paper_id)["title"] or "paper"
    safe = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in title)[:60].strip() or "paper"
    if fmt == "markdown":
        return T.export_markdown(title, tree["view_label"], tree["nodes"]), "text/markdown; charset=utf-8", f"{safe}-{view}.md"
    if fmt == "json":
        body = {"paper": {"id": paper_id, "title": title}, "view": view, "view_label": tree["view_label"],
                "generated_at": tree["generated_at"], "model": tree["model"], "nodes": tree["nodes"]}
        import json

        return json.dumps(body, ensure_ascii=False, indent=2), "application/json; charset=utf-8", f"{safe}-{view}.json"
    raise ApiError("bad_request", "format 必须是 markdown 或 json")
