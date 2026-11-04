"""论文记录的查询与更新。"""
from __future__ import annotations

import shutil
import sqlite3
from typing import Any

from ..context import AppContext
from ..knowledge import store as kb_store  # 标签就是知识库主题；同时确保主题表已注册
from ..materials import schema as _materials_schema  # noqa: F401  确保补充材料的表/列已注册
from ..parsing.pdf_parser import PARSER_VERSION
from ..util import ApiError, dumps, loads, utcnow
from ..db import register_column
from . import filters, folders  # folders 在导入时注册 papers.folder_id 与文件夹/智能视图的表

# 备注名：用户给论文起的名字，显示在标题的位置，原标题保留不动
register_column("paper_alias", "papers", "alias", "TEXT")

SUMMARY_SQL = """
SELECT p.*, COALESCE(pa.status, 'none') AS analysis_status,
  (SELECT COUNT(*) FROM papers c WHERE c.parent_id = p.id)
  + (SELECT COUNT(*) FROM supplementary_assets a WHERE a.paper_id = p.id AND a.child_paper_id IS NULL) AS supplement_count
FROM papers p LEFT JOIN paper_analysis pa ON pa.paper_id = p.id
"""


def row_to_summary(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "alias": row["alias"] or None,
        "authors": loads(row["authors_json"], []),
        "year": row["year"],
        "venue": row["venue"],
        "doi": row["doi"],
        "has_pdf": bool(row["has_pdf"]),
        "status": row["status"],
        "parse_error": row["parse_error"],
        "page_count": row["page_count"],
        "language": row["language"],
        "favorite": bool(row["favorite"]),
        "reading_page": row["reading_page"],
        "read_progress": row["read_progress"],
        "last_opened_at": row["last_opened_at"],
        "imported_at": row["imported_at"],
        "source_folder": row["source_folder"],
        "analysis_status": row["analysis_status"],
        # 补充材料：role='supplement' 的论文是某篇论文的子论文，不出现在文献库列表里
        "role": row["role"] or "paper",
        "parent_id": row["parent_id"],
        "supplement_count": row["supplement_count"],
        "folder_id": row["folder_id"],
        # 解析器版本：库里的版本低于当前版本且有 PDF 时，提示“有新版解析，可重新解析”
        "parser_version": row["parser_version"],
        "parser_outdated": bool(row["has_pdf"]) and (row["parser_version"] or 1) < PARSER_VERSION,
    }


def row_to_detail(row: sqlite3.Row) -> dict:
    d = row_to_summary(row)
    d.update(
        {
            "abstract": row["abstract"],
            "source_url": row["source_url"],
            "original_path": row["original_path"],
            "arxiv_id": row["arxiv_id"],
            "metadata_source": row["metadata_source"],
            "reading_state": loads(row["reading_state_json"]),
            "access_info": loads(row["access_info_json"], []),
        }
    )
    return d


def get_row(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    row = conn.execute(SUMMARY_SQL + " WHERE p.id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "论文不存在", 404)
    return row


def get_detail(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        d = row_to_detail(get_row(conn, paper_id))
        d["tags"] = kb_store.paper_tags(conn, [paper_id])[paper_id]
        if d["parent_id"]:
            parent = conn.execute("SELECT title FROM papers WHERE id = ?", (d["parent_id"],)).fetchone()
            d["parent_title"] = parent["title"] if parent else None
        return d


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_papers(
    app: AppContext, *, q: str | None, view: str, sort: str, order: str, limit: int, offset: int, tag: str | None = None,
    filters_: dict[str, Any] | None = None,
) -> tuple[list[dict], int]:
    """文献列表。旧参数（q/view/sort/tag）保持不变；`filters_` 里是多特征筛选条件（见 library/filters.py），全部 AND。

    有笔记/批注/正文/卡片关键词时，每篇论文带 `matches`（命中片段）；列表项都带 `folder_id`、`folder_path`。
    """
    f: dict[str, Any] = dict(filters_ or {})
    if q and q.strip():
        f["q"] = q
    if tag:
        f["tag"] = tag
    if view == "favorite":
        f["favorite"] = True
    elif view == "recent":
        f["recent"] = True
    sort_sql = {
        "recent": "(p.last_opened_at IS NULL), p.last_opened_at {o}, p.imported_at DESC",
        "title": "lower(p.title) {o}",
        "author": "lower(p.authors_json) {o}",
        "year": "(p.year IS NULL), p.year {o}",
        "imported": "p.imported_at {o}",
        "relevance": None,
    }
    if sort not in sort_sql:
        raise ApiError("bad_request", f"不支持的排序方式：{sort}")
    direction = "ASC" if order == "asc" else "DESC"
    order_args: list[Any] = []
    if sort == "relevance":
        order_sql, order_args = filters.relevance_sql(f)
    else:
        order_sql = sort_sql[sort].format(o=direction)
    with app.db.session() as conn:
        where, args = filters.build_where(conn, f)
        clause = " WHERE " + " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM papers p{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"{SUMMARY_SQL}{clause} ORDER BY {order_sql} LIMIT ? OFFSET ?", (*args, *order_args, limit, offset)
        ).fetchall()
        items = [row_to_summary(r) for r in rows]
        ids = [i["id"] for i in items]
        tags = kb_store.paper_tags(conn, ids)
        paths = folders.folder_paths(conn)
        for i in items:
            i["tags"] = tags[i["id"]]
            i["folder_path"] = paths.get(i["folder_id"]) if i["folder_id"] else None
        if any(f.get(k) for k in filters.TEXT_KEYS):
            matches = filters.collect_matches(conn, ids, f)
            for i in items:
                if i["id"] in matches:
                    i["matches"] = matches[i["id"]]
    return items, total


def update_paper(app: AppContext, paper_id: str, fields: dict[str, Any]) -> dict:
    sets, args = [], []
    if "favorite" in fields and fields["favorite"] is not None:
        sets.append("favorite = ?")
        args.append(1 if fields["favorite"] else 0)
    if fields.get("title") is not None:
        title = str(fields["title"]).strip()
        if not title:
            raise ApiError("bad_request", "标题不能为空")
        sets.append("title = ?")
        args.append(title[:500])
    if "alias" in fields and fields["alias"] is not None:
        alias = " ".join(str(fields["alias"]).split())[:120]
        sets.append("alias = ?")
        args.append(alias or None)  # 空字符串表示清除备注名
    if fields.get("reading_page") is not None:
        sets.append("reading_page = ?")
        args.append(max(1, int(fields["reading_page"])))
    if fields.get("read_progress") is not None:
        sets.append("read_progress = ?")
        args.append(max(0.0, min(1.0, float(fields["read_progress"]))))
    if fields.get("reading_state") is not None:
        state = dumps(fields["reading_state"])
        if len(state) > 8192:
            raise ApiError("bad_request", "阅读状态数据过大")
        sets.append("reading_state_json = ?")
        args.append(state)
    if fields.get("touch"):
        sets.append("last_opened_at = ?")
        args.append(utcnow())
    with app.db.session() as conn:
        get_row(conn, paper_id)
        if sets:
            conn.execute(f"UPDATE papers SET {', '.join(sets)} WHERE id = ?", (*args, paper_id))
        return row_to_detail(get_row(conn, paper_id))


def purge_paper_rows(conn: sqlite3.Connection, ids: tuple[str, ...] | list[str]) -> None:
    """清理论文在“没有外键级联”的表里留下的记录（调用方负责事务）。

    - 译文缓存、知识候选、标签关联、任务（及其日志）：随论文一起删除；
    - 用户已保存的知识条目：保留内容，只清掉指向这篇论文的来源（用户自己的知识不该因为删论文而消失）；
    - 隐私日志与用量统计：保留记录（它们记录“发送过什么、花了多少”），只清掉论文引用。
    """
    for pid in ids:
        conn.execute("DELETE FROM translations WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM kb_candidates WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM kb_paper_topics WHERE paper_id = ?", (pid,))
        conn.execute("UPDATE kb_items SET source_paper_id = NULL WHERE source_paper_id = ?", (pid,))
        conn.execute("UPDATE ai_usage SET paper_id = NULL WHERE paper_id = ?", (pid,))
        conn.execute("UPDATE ai_requests SET paper_id = NULL WHERE paper_id = ?", (pid,))
        conn.execute("DELETE FROM jobs WHERE paper_id = ?", (pid,))  # job_logs 随任务级联删除


def cancel_paper_jobs(app: AppContext, ids: tuple[str, ...] | list[str]) -> None:
    """删除前先取消这些论文上还在跑/排队的后台任务，避免任务继续往已删除的论文里写数据。"""
    with app.db.session() as conn:
        active = [
            r["id"] for pid in ids for r in conn.execute(
                "SELECT id FROM jobs WHERE paper_id = ? AND status IN ('pending','running','waiting_approval','paused')", (pid,)
            ).fetchall()
        ]
    for job_id in active:
        try:
            app.jobs.cancel(job_id)
        except Exception:  # noqa: BLE001  取消失败不应阻止删除；任务行随后会被一并清掉
            pass


def purge_paper_files(app: AppContext, ids: tuple[str, ...] | list[str]) -> list[str]:
    """删除论文在 Marginalia 管理目录里的副本（PDF、渲染缓存、代码仓库等）；绝不触碰用户的原始文件。

    Windows 上文件被占用时会稍等重试；仍删不掉不让整个删除失败（数据库记录已清干净），
    而是返回提示，让用户知道有残留文件。
    """
    import time

    root = app.paths.papers_dir.resolve()
    warnings: list[str] = []
    for pid in ids:
        target = app.paths.paper_dir(pid).resolve()
        if target.parent != root or not target.exists():
            continue
        for attempt in range(4):
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                break
            time.sleep(0.25 * (attempt + 1))
        if target.exists():
            warnings.append(f"论文已从文献库删除，但文件夹 {target.name} 里还有文件被其它程序占用，未能清理，可稍后手动删除。")
    return warnings


def delete_paper(app: AppContext, paper_id: str) -> dict:
    """删除论文及其全部关联数据（分析卡片、笔记、批注、索引、译文、思维导图、代码、任务、标签关联等）。

    数据库部分在一个事务里原子完成，任何一步出错都整体回滚并给出可读的原因；补充材料子论文一并删除。
    """
    with app.db.session() as conn:
        get_row(conn, paper_id)
        children = [r["id"] for r in conn.execute("SELECT id FROM papers WHERE parent_id = ?", (paper_id,)).fetchall()]
    ids = (paper_id, *children)
    cancel_paper_jobs(app, ids)
    try:
        with app.db.session() as conn:
            purge_paper_rows(conn, ids)
            conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))  # 子论文与补充材料记录随外键级联删除
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise ApiError("delete_failed", "删除失败：数据库正忙，请稍等几秒再试。", 409) from exc
        raise ApiError("delete_failed", f"删除失败：数据库出错，论文没有被改动，请重试。", 500) from exc
    except sqlite3.Error as exc:
        raise ApiError("delete_failed", f"删除失败：数据库出错，论文没有被改动，请重试。", 500) from exc
    warnings = purge_paper_files(app, ids)
    return {"ok": True, "deleted_supplements": len(children), "warnings": warnings}
