"""论文文件夹（纵向归档）与智能视图（保存下来的筛选条件）。

文件夹与标签的分工：一篇论文只能放在一个文件夹里（像资料柜的一格），但可以同时有多个标签（像贴在上面的便利贴）。
- 删除文件夹不会删除论文：它和所有子文件夹一起被移除，里面的论文回到“未分类”；
- 补充材料子论文跟随主论文，不单独放进文件夹。
"""
from __future__ import annotations

import re
import sqlite3

from ..context import AppContext
from ..db import register_column, register_schema
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import filters

MAX_DEPTH = 6
MAX_NAME = 60

register_column("folders", "papers", "folder_id", "TEXT")
register_schema("folders", """
CREATE TABLE IF NOT EXISTS folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id TEXT,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id, sort);
CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder_id);
CREATE TABLE IF NOT EXISTS smart_views (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  query_json TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id);
CREATE INDEX IF NOT EXISTS idx_annotations_paper ON annotations(paper_id);
""")


# ---------------------------------------------------------------- 文件夹
def _clean_name(name: str | None) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n:
        raise ApiError("bad_request", "文件夹名称不能为空")
    if len(n) > MAX_NAME:
        raise ApiError("bad_request", f"文件夹名称不能超过 {MAX_NAME} 个字符")
    return n


def _all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name, parent_id, sort, created_at FROM folders ORDER BY sort, lower(name)").fetchall()


def _depth(rows: dict[str, str | None], fid: str | None) -> int:
    d = 0
    while fid is not None and d < 50:
        fid = rows.get(fid)
        d += 1
    return d


def _height(children: dict[str | None, list[str]], fid: str) -> int:
    kids = children.get(fid, [])
    return 1 + (max(_height(children, k) for k in kids) if kids else 0)


def _row(conn: sqlite3.Connection, fid: str) -> sqlite3.Row:
    r = conn.execute("SELECT id, name, parent_id, sort, created_at FROM folders WHERE id = ?", (fid,)).fetchone()
    if r is None:
        raise ApiError("not_found", "文件夹不存在", 404)
    return r


def _assert_unique(conn: sqlite3.Connection, name: str, parent_id: str | None, exclude: str | None = None) -> None:
    for r in conn.execute("SELECT id, name FROM folders WHERE parent_id IS ?", (parent_id,)):
        if r["id"] != exclude and r["name"].lower() == name.lower():
            raise ApiError("conflict", f"这个位置已经有一个叫“{r['name']}”的文件夹", 409)


def list_folders(app: AppContext) -> dict:
    """文件夹平铺列表（前端按 parent_id 组树）与系统视图的论文数。"""
    with app.db.session() as conn:
        rows = _all(conn)
        direct = {
            r["folder_id"]: r["n"]
            for r in conn.execute(
                "SELECT folder_id, COUNT(*) AS n FROM papers WHERE COALESCE(role,'paper')='paper' AND folder_id IS NOT NULL GROUP BY folder_id"
            )
        }
        children: dict[str | None, list[str]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])

        def total(fid: str) -> int:
            return direct.get(fid, 0) + sum(total(k) for k in children.get(fid, []))

        items = [
            {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "sort": r["sort"],
             "paper_count": direct.get(r["id"], 0), "total_count": total(r["id"])}
            for r in rows
        ]
        base = "COALESCE(role,'paper') = 'paper'"
        counts = {
            "all": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base}").fetchone()[0],
            "unfiled": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND folder_id IS NULL").fetchone()[0],
            "favorite": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND favorite = 1").fetchone()[0],
            "recent": conn.execute(f"SELECT COUNT(*) FROM papers WHERE {base} AND last_opened_at IS NOT NULL").fetchone()[0],
        }
    return {"items": items, "counts": counts}


def folder_paths(conn: sqlite3.Connection) -> dict[str, str]:
    """{folder_id: "上级 / 本级"}，列表里显示论文所在位置用。"""
    rows = {r["id"]: (r["name"], r["parent_id"]) for r in conn.execute("SELECT id, name, parent_id FROM folders")}
    out: dict[str, str] = {}
    for fid in rows:
        names, cur, guard = [], fid, 0
        while cur is not None and cur in rows and guard < 50:
            names.append(rows[cur][0])
            cur = rows[cur][1]
            guard += 1
        out[fid] = " / ".join(reversed(names))
    return out


def create_folder(app: AppContext, name: str, parent_id: str | None = None) -> dict:
    name = _clean_name(name)
    with app.db.session() as conn:
        if parent_id is not None:
            _row(conn, parent_id)
            parents = {r["id"]: r["parent_id"] for r in _all(conn)}
            if _depth(parents, parent_id) + 1 >= MAX_DEPTH:
                raise ApiError("bad_request", f"文件夹最多嵌套 {MAX_DEPTH} 层")
        _assert_unique(conn, name, parent_id)
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM folders WHERE parent_id IS ?", (parent_id,)).fetchone()[0]
        fid = new_id()
        conn.execute("INSERT INTO folders(id, name, parent_id, sort, created_at) VALUES (?,?,?,?,?)", (fid, name, parent_id, sort, utcnow()))
    return {"id": fid, "name": name, "parent_id": parent_id, "sort": sort, "paper_count": 0, "total_count": 0}


def update_folder(app: AppContext, fid: str, *, name: str | None = None, parent_id: str | None = None, move: bool = False,
                  sort: int | None = None) -> dict:
    """改名、移动（move=True 时按 parent_id 移动，None 表示移到最上层）、调整顺序。不能把文件夹移进它自己的子孙里。"""
    with app.db.session() as conn:
        cur = _row(conn, fid)
        new_name = _clean_name(name) if name is not None else cur["name"]
        new_parent = parent_id if move else cur["parent_id"]
        if move and new_parent is not None:
            _row(conn, new_parent)
            if new_parent in filters.subtree_ids(conn, fid):
                raise ApiError("bad_request", "不能把文件夹移到它自己或它的子文件夹里")
            parents = {r["id"]: r["parent_id"] for r in _all(conn)}
            children: dict[str | None, list[str]] = {}
            for k, v in parents.items():
                children.setdefault(v, []).append(k)
            if _depth(parents, new_parent) + _height(children, fid) >= MAX_DEPTH:
                raise ApiError("bad_request", f"文件夹最多嵌套 {MAX_DEPTH} 层")
        if new_name != cur["name"] or new_parent != cur["parent_id"]:
            _assert_unique(conn, new_name, new_parent, exclude=fid)
        new_sort = cur["sort"]
        if move and new_parent != cur["parent_id"] and sort is None:
            new_sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM folders WHERE parent_id IS ?", (new_parent,)).fetchone()[0]
        if sort is not None:
            new_sort = int(sort)
        conn.execute("UPDATE folders SET name = ?, parent_id = ?, sort = ? WHERE id = ?", (new_name, new_parent, new_sort, fid))
    return {"id": fid, "name": new_name, "parent_id": new_parent, "sort": new_sort}


def reorder(app: AppContext, parent_id: str | None, ids: list[str]) -> None:
    """把同一级文件夹按 ids 的顺序重新编号；ids 必须恰好是该级下的全部文件夹。"""
    with app.db.session() as conn:
        existing = [r["id"] for r in conn.execute("SELECT id FROM folders WHERE parent_id IS ?", (parent_id,))]
        if sorted(existing) != sorted(ids):
            raise ApiError("bad_request", "排序列表必须包含这一级的全部文件夹，且不能有重复")
        for i, fid in enumerate(ids):
            conn.execute("UPDATE folders SET sort = ? WHERE id = ?", (i, fid))


def delete_folder(app: AppContext, fid: str) -> dict:
    """删除文件夹及其全部子文件夹；里面的论文回到“未分类”，论文本身不受影响。"""
    with app.db.session() as conn:
        _row(conn, fid)
        ids = filters.subtree_ids(conn, fid)
        marks = ",".join("?" * len(ids))
        moved = conn.execute(f"UPDATE papers SET folder_id = NULL WHERE folder_id IN ({marks})", ids).rowcount
        conn.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
    return {"ok": True, "deleted_folders": len(ids), "moved_papers": moved}


def move_papers(app: AppContext, paper_ids: list[str], folder_id: str | None) -> dict:
    """把论文移入文件夹（folder_id=None 表示移回“未分类”）。补充材料子论文不能单独移动，会被跳过并计数。"""
    if not paper_ids:
        raise ApiError("bad_request", "没有选择论文")
    ids = list(dict.fromkeys(paper_ids))
    with app.db.session() as conn:
        if folder_id is not None:
            _row(conn, folder_id)
        marks = ",".join("?" * len(ids))
        found = {r["id"]: r["role"] for r in conn.execute(f"SELECT id, COALESCE(role,'paper') AS role FROM papers WHERE id IN ({marks})", ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ApiError("not_found", f"有 {len(missing)} 篇论文不存在", 404)
        movable = [i for i in ids if found[i] == "paper"]
        if movable:
            m2 = ",".join("?" * len(movable))
            conn.execute(f"UPDATE papers SET folder_id = ? WHERE id IN ({m2})", (folder_id, *movable))
    return {"ok": True, "moved": len(movable), "skipped_supplements": len(ids) - len(movable)}


# ---------------------------------------------------------------- 智能视图
def list_views(app: AppContext) -> list[dict]:
    with app.db.session() as conn:
        return [
            {"id": r["id"], "name": r["name"], "query": loads(r["query_json"], {}), "sort": r["sort"]}
            for r in conn.execute("SELECT * FROM smart_views ORDER BY sort, created_at")
        ]


def create_view(app: AppContext, name: str, query: dict) -> dict:
    name = _clean_name(name)
    if not isinstance(query, dict) or not query:
        raise ApiError("bad_request", "没有可保存的筛选条件")
    clean = filters.normalize(query)
    with app.db.session() as conn:
        for r in conn.execute("SELECT name FROM smart_views"):
            if r["name"].lower() == name.lower():
                raise ApiError("conflict", f"已经有一个叫“{r['name']}”的智能视图", 409)
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM smart_views").fetchone()[0]
        vid = new_id()
        conn.execute("INSERT INTO smart_views(id, name, query_json, sort, created_at) VALUES (?,?,?,?,?)", (vid, name, dumps(clean), sort, utcnow()))
    return {"id": vid, "name": name, "query": clean, "sort": sort}


def delete_view(app: AppContext, vid: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM smart_views WHERE id = ?", (vid,)).rowcount == 0:
            raise ApiError("not_found", "智能视图不存在", 404)
