"""综述空间：把综述用到的论文与普通文献库做逻辑隔离。"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..context import AppContext
from ..db import register_schema
from ..util import ApiError, new_id, utcnow


register_schema(
    "reviews",
    """
    CREATE TABLE IF NOT EXISTS review_projects (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      sort INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS review_categories (
      id TEXT PRIMARY KEY,
      review_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      sort INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(review_id, name)
    );
    CREATE TABLE IF NOT EXISTS review_papers (
      review_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
      paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
      category_id TEXT NOT NULL REFERENCES review_categories(id) ON DELETE RESTRICT,
      screening TEXT NOT NULL DEFAULT 'screening',
      exclusion_reason TEXT NOT NULL DEFAULT '',
      added_at TEXT NOT NULL,
      PRIMARY KEY(review_id, paper_id)
    );
    CREATE INDEX IF NOT EXISTS idx_review_categories_review ON review_categories(review_id, sort, name);
    CREATE INDEX IF NOT EXISTS idx_review_papers_review ON review_papers(review_id, category_id, added_at);
    CREATE INDEX IF NOT EXISTS idx_review_papers_paper ON review_papers(paper_id);
    """,
)


def _project_row(row: sqlite3.Row, category_count: int | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "sort": row["sort"],
        "created_at": row["created_at"],
        "paper_count": row["paper_count"] if "paper_count" in row.keys() else 0,
        "category_count": category_count if category_count is not None else 0,
    }


def _category_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "review_id": row["review_id"],
        "name": row["name"],
        "sort": row["sort"],
        "created_at": row["created_at"],
        "paper_count": row["paper_count"] if "paper_count" in row.keys() else 0,
    }


def _get_project(conn: sqlite3.Connection, review_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM review_projects WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "综述不存在", 404)
    return row


def _get_category(conn: sqlite3.Connection, review_id: str, category_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM review_categories WHERE id = ? AND review_id = ?", (category_id, review_id)
    ).fetchone()
    if row is None:
        raise ApiError("not_found", "综述分类不存在", 404)
    return row


def list_projects(app: AppContext) -> dict[str, list[dict[str, Any]]]:
    with app.db.session() as conn:
        rows = conn.execute(
            """
            SELECT r.*, COUNT(DISTINCT rp.paper_id) AS paper_count,
                   COUNT(DISTINCT rc.id) AS category_count
            FROM review_projects r
            LEFT JOIN review_papers rp ON rp.review_id = r.id
            LEFT JOIN review_categories rc ON rc.review_id = r.id
            GROUP BY r.id
            ORDER BY r.sort, lower(r.name), r.created_at
            """
        ).fetchall()
        return {"items": [_project_row(row, row["category_count"]) for row in rows]}


def create_project(app: AppContext, name: str, description: str = "") -> dict[str, Any]:
    clean = " ".join(name.split()).strip()
    if not clean:
        raise ApiError("bad_request", "综述名称不能为空")
    now = utcnow()
    review_id = new_id()
    category_id = new_id()
    with app.db.session() as conn:
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM review_projects").fetchone()[0]
        conn.execute(
            "INSERT INTO review_projects(id, name, description, sort, created_at) VALUES (?, ?, ?, ?, ?)",
            (review_id, clean[:120], description.strip()[:1000], sort, now),
        )
        # 每个综述都有默认分类，保证新加入的论文不会出现“无分类”状态。
        conn.execute(
            "INSERT INTO review_categories(id, review_id, name, sort, created_at) VALUES (?, ?, ?, ?, ?)",
            (category_id, review_id, "待分类", 0, now),
        )
        row = conn.execute(
            "SELECT r.*, 0 AS paper_count, 1 AS category_count FROM review_projects r WHERE r.id = ?", (review_id,)
        ).fetchone()
        result = _project_row(row, 1)
        result["default_category_id"] = category_id
        return result


def update_project(app: AppContext, review_id: str, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    with app.db.session() as conn:
        _get_project(conn, review_id)
        sets: list[str] = []
        args: list[Any] = []
        if name is not None:
            clean = " ".join(name.split()).strip()
            if not clean:
                raise ApiError("bad_request", "综述名称不能为空")
            sets.append("name = ?")
            args.append(clean[:120])
        if description is not None:
            sets.append("description = ?")
            args.append(description.strip()[:1000])
        if sets:
            conn.execute(f"UPDATE review_projects SET {', '.join(sets)} WHERE id = ?", (*args, review_id))
        row = conn.execute(
            """
            SELECT r.*, COUNT(DISTINCT rp.paper_id) AS paper_count,
                   COUNT(DISTINCT rc.id) AS category_count
            FROM review_projects r
            LEFT JOIN review_papers rp ON rp.review_id = r.id
            LEFT JOIN review_categories rc ON rc.review_id = r.id
            WHERE r.id = ? GROUP BY r.id
            """,
            (review_id,),
        ).fetchone()
        return _project_row(row, row["category_count"])


def delete_project(app: AppContext, review_id: str) -> None:
    with app.db.session() as conn:
        _get_project(conn, review_id)
        conn.execute("DELETE FROM review_projects WHERE id = ?", (review_id,))


def list_categories(app: AppContext, review_id: str) -> dict[str, list[dict[str, Any]]]:
    with app.db.session() as conn:
        _get_project(conn, review_id)
        rows = conn.execute(
            """
            SELECT c.*, COUNT(rp.paper_id) AS paper_count
            FROM review_categories c
            LEFT JOIN review_papers rp ON rp.category_id = c.id AND rp.review_id = c.review_id
            WHERE c.review_id = ?
            GROUP BY c.id
            ORDER BY c.sort, lower(c.name), c.created_at
            """,
            (review_id,),
        ).fetchall()
        return {"items": [_category_row(row) for row in rows]}


def create_category(app: AppContext, review_id: str, name: str) -> dict[str, Any]:
    clean = " ".join(name.split()).strip()
    if not clean:
        raise ApiError("bad_request", "分类名称不能为空")
    with app.db.session() as conn:
        _get_project(conn, review_id)
        if conn.execute("SELECT 1 FROM review_categories WHERE review_id = ? AND name = ?", (review_id, clean)).fetchone():
            raise ApiError("conflict", "这个综述里已经有同名分类", 409)
        category_id = new_id()
        sort = conn.execute(
            "SELECT COALESCE(MAX(sort), -1) + 1 FROM review_categories WHERE review_id = ?", (review_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO review_categories(id, review_id, name, sort, created_at) VALUES (?, ?, ?, ?, ?)",
            (category_id, review_id, clean[:80], sort, utcnow()),
        )
        row = conn.execute("SELECT *, 0 AS paper_count FROM review_categories WHERE id = ?", (category_id,)).fetchone()
        return _category_row(row)


def add_papers(app: AppContext, review_id: str, paper_ids: list[str], category_id: str | None = None) -> dict[str, Any]:
    ids = list(dict.fromkeys(paper_ids))
    if not ids:
        return {"added": 0, "existing": 0, "missing": []}
    now = utcnow()
    with app.db.session() as conn:
        _get_project(conn, review_id)
        if category_id is None:
            category_id = conn.execute(
                "SELECT id FROM review_categories WHERE review_id = ? ORDER BY sort, created_at LIMIT 1", (review_id,)
            ).fetchone()[0]
        _get_category(conn, review_id, category_id)
        existing = {r[0] for r in conn.execute(
            f"SELECT paper_id FROM review_papers WHERE review_id = ? AND paper_id IN ({','.join('?' for _ in ids)})",
            (review_id, *ids),
        ).fetchall()}
        available = {r[0] for r in conn.execute(
            f"SELECT id FROM papers WHERE id IN ({','.join('?' for _ in ids)})", ids
        ).fetchall()}
        missing = [pid for pid in ids if pid not in available]
        for pid in ids:
            if pid in existing or pid not in available:
                continue
            conn.execute(
                "INSERT INTO review_papers(review_id, paper_id, category_id, screening, exclusion_reason, added_at) VALUES (?, ?, ?, 'screening', '', ?)",
                (review_id, pid, category_id, now),
            )
        return {"added": len([pid for pid in ids if pid in available and pid not in existing]), "existing": len(existing), "missing": missing}


def update_paper(app: AppContext, review_id: str, paper_id: str, *, category_id: str | None = None,
                 screening: str | None = None, exclusion_reason: str | None = None) -> dict[str, Any]:
    allowed = {"screening", "included", "excluded", "reading"}
    with app.db.session() as conn:
        _get_project(conn, review_id)
        row = conn.execute("SELECT * FROM review_papers WHERE review_id = ? AND paper_id = ?", (review_id, paper_id)).fetchone()
        if row is None:
            raise ApiError("not_found", "这篇论文不在当前综述中", 404)
        if category_id is not None:
            _get_category(conn, review_id, category_id)
        if screening is not None and screening not in allowed:
            raise ApiError("bad_request", "不支持的筛选状态")
        sets: list[str] = []
        args: list[Any] = []
        if category_id is not None:
            sets.append("category_id = ?")
            args.append(category_id)
        if screening is not None:
            sets.append("screening = ?")
            args.append(screening)
        if exclusion_reason is not None:
            sets.append("exclusion_reason = ?")
            args.append(exclusion_reason.strip()[:500])
        if sets:
            conn.execute(f"UPDATE review_papers SET {', '.join(sets)} WHERE review_id = ? AND paper_id = ?", (*args, review_id, paper_id))
        return dict(conn.execute("SELECT * FROM review_papers WHERE review_id = ? AND paper_id = ?", (review_id, paper_id)).fetchone())


def remove_paper(app: AppContext, review_id: str, paper_id: str) -> None:
    with app.db.session() as conn:
        _get_project(conn, review_id)
        cur = conn.execute("DELETE FROM review_papers WHERE review_id = ? AND paper_id = ?", (review_id, paper_id))
        if cur.rowcount == 0:
            raise ApiError("not_found", "这篇论文不在当前综述中", 404)


def review_filter_sql(scope: str, review_id: str | None, category_id: str | None) -> tuple[str, list[Any]]:
    """返回文献列表需要追加的隔离条件。默认 library 只看未加入任何综述的论文。"""
    if scope == "library":
        return "NOT EXISTS (SELECT 1 FROM review_papers rp0 WHERE rp0.paper_id = p.id)", []
    if scope != "review" or not review_id:
        raise ApiError("bad_request", "综述列表缺少 review_id")
    where = "EXISTS (SELECT 1 FROM review_papers rp0 WHERE rp0.paper_id = p.id AND rp0.review_id = ?"
    args: list[Any] = [review_id]
    if category_id:
        where += " AND rp0.category_id = ?"
        args.append(category_id)
    return where + ")", args


def paper_membership(app: AppContext, review_id: str, paper_id: str) -> dict[str, Any] | None:
    with app.db.session() as conn:
        _get_project(conn, review_id)
        row = conn.execute(
            """
            SELECT rp.*, rc.name AS category_name
            FROM review_papers rp JOIN review_categories rc ON rc.id = rp.category_id
            WHERE rp.review_id = ? AND rp.paper_id = ?
            """,
            (review_id, paper_id),
        ).fetchone()
        return dict(row) if row else None


def paper_ids(app: AppContext, review_id: str) -> list[str]:
    """按综述加入顺序返回全部论文，供综述整体导出使用。"""
    with app.db.session() as conn:
        _get_project(conn, review_id)
        return [r["paper_id"] for r in conn.execute(
            "SELECT paper_id FROM review_papers WHERE review_id = ? ORDER BY added_at, paper_id", (review_id,)
        ).fetchall()]
