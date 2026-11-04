"""补充材料记录（supplementary_assets）的读写。"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ..context import AppContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import files as F

CATEGORIES = ["方法补充", "公式推导", "实验配置", "额外实验", "数据集", "代码", "其他"]


def _summary_of_analysis(kind: str, analysis: dict | None) -> dict | None:
    """列表里只带轻量摘要，完整分析结果通过专门接口读取。"""
    if not analysis:
        return None
    if analysis.get("kind") == "error":
        return {"kind": "error", "error": analysis.get("error")}
    if analysis.get("kind") == "table":
        sheets = analysis.get("sheets") or []
        return {"kind": "table", "sheets": len(sheets),
                "rows": sum(s.get("rows") or 0 for s in sheets),
                "columns": sum(len(s.get("columns") or []) for s in sheets),
                "partial": any(s.get("partial") for s in sheets), "has_explanation": bool(analysis.get("explanation"))}
    if analysis.get("kind") == "archive":
        return {"kind": "archive", "total_files": analysis.get("total_files"), "suspicious": len(analysis.get("suspicious") or [])}
    if analysis.get("kind") == "document":
        return {"kind": "document", "paragraphs": analysis.get("paragraphs"), "headings": len(analysis.get("headings") or [])}
    return {"kind": analysis.get("kind")}


def asset_dict(row: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict:
    analysis = loads(row["analysis_json"])
    child = None
    if row["child_paper_id"] and conn is not None:
        c = conn.execute("SELECT page_count, status, title FROM papers WHERE id = ?", (row["child_paper_id"],)).fetchone()
        if c:
            child = {"page_count": c["page_count"], "status": c["status"], "title": c["title"]}
    return {
        "id": row["id"], "paper_id": row["paper_id"], "filename": row["filename"], "mime": row["mime"],
        "size": row["size"], "kind": F.kind_of(row["filename"]), "source": row["source"],
        "source_url": row["source_url"], "category": row["category"], "category_source": row["category_source"],
        "summary": row["summary"], "match_status": row["match_status"], "match_basis": row["match_basis"],
        "download_status": row["download_status"], "download_error": row["download_error"],
        "analysis_status": row["analysis_status"], "analysis": _summary_of_analysis(F.kind_of(row["filename"]), analysis),
        "child_paper_id": row["child_paper_id"], "child": child, "job_id": row["job_id"],
        "created_at": row["created_at"],
    }


def get_row(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM supplementary_assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "补充材料不存在", 404)
    return row


def get_asset(app: AppContext, asset_id: str) -> dict:
    with app.db.session() as conn:
        return asset_dict(get_row(conn, asset_id), conn)


def list_assets(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM supplementary_assets WHERE paper_id = ? ORDER BY created_at, rowid", (paper_id,)
        ).fetchall()
        return [asset_dict(r, conn) for r in rows]


def asset_dir(app: AppContext, paper_id: str) -> Path:
    return app.paths.paper_dir(paper_id) / "supplements"


def create_asset(
    app: AppContext, *, paper_id: str, filename: str, source: str, source_url: str | None = None,
    size: int | None = None, category: str = "其他", category_source: str = "rule", match_status: str = "用户确认",
    match_basis: str | None = None, download_status: str = "pending", job_id: str | None = None,
) -> str:
    asset_id = new_id()
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO supplementary_assets(id, paper_id, filename, mime, size, source, source_url, category, "
            "category_source, match_status, match_basis, download_status, job_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, paper_id, F.sanitize_filename(filename), F.mime_of(filename), size, source, source_url,
             category, category_source, match_status, match_basis, download_status, job_id, utcnow()),
        )
    return asset_id


def update_asset(app: AppContext, asset_id: str, **fields) -> None:
    allowed = {"filename", "mime", "size", "rel_path", "source_url", "category", "category_source", "summary",
               "match_status", "match_basis", "download_status", "download_error", "analysis_status", "analysis_json",
               "child_paper_id", "job_id", "sha256"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(k)
        if k == "analysis_json" and not isinstance(v, (str, type(None))):
            v = dumps(v)
        sets.append(f"{k} = ?")
        args.append(v)
    if not sets:
        return
    with app.db.session() as conn:
        conn.execute(f"UPDATE supplementary_assets SET {', '.join(sets)} WHERE id = ?", (*args, asset_id))


def link_job(app: AppContext, asset_id: str, job_id: str) -> None:
    """提交任务后记录 job_id。任务可能已经开始并写入了自己的 job_id（例如转成“等待批准”的新任务），所以只在尚未记录时写入。"""
    with app.db.session() as conn:
        conn.execute("UPDATE supplementary_assets SET job_id = ? WHERE id = ? AND job_id IS NULL", (job_id, asset_id))


def swap_job(app: AppContext, asset_id: str, old: str, new: str) -> None:
    """重试后把 job_id 从旧任务换成新任务；若任务已经自己写入了新 id 则不动。"""
    with app.db.session() as conn:
        conn.execute("UPDATE supplementary_assets SET job_id = ? WHERE id = ? AND job_id = ?", (new, asset_id, old))


def get_analysis(app: AppContext, asset_id: str) -> dict | None:
    with app.db.session() as conn:
        return loads(get_row(conn, asset_id)["analysis_json"])


def set_analysis_part(app: AppContext, asset_id: str, **parts) -> None:
    """合并更新 analysis_json 的顶层字段（如 explanation）。"""
    with app.db.session() as conn:
        cur = loads(get_row(conn, asset_id)["analysis_json"], {}) or {}
        cur.update(parts)
        conn.execute("UPDATE supplementary_assets SET analysis_json = ? WHERE id = ?", (dumps(cur), asset_id))


def file_path(app: AppContext, row: sqlite3.Row | dict) -> Path | None:
    rel = row["rel_path"]
    if not rel:
        return None
    p = (app.paths.data_dir / rel).resolve()
    root = app.paths.data_dir.resolve()
    if root not in p.parents:  # 防御：记录被篡改时不允许越出数据目录
        return None
    return p


def delete_asset(app: AppContext, asset_id: str) -> None:
    """删除补充材料记录与 Marginalia 内的副本（含 PDF 子论文）；绝不触碰用户原文件。"""
    from ..library import repo  # 延迟导入：library.repo 依赖本模块的表注册

    with app.db.session() as conn:
        row = get_row(conn, asset_id)
        child = row["child_paper_id"]
        path = file_path(app, row)
    if child:
        repo.cancel_paper_jobs(app, [child])
    with app.db.session() as conn:
        conn.execute("DELETE FROM supplementary_assets WHERE id = ?", (asset_id,))
        if child:
            repo.purge_paper_rows(conn, [child])
            conn.execute("DELETE FROM papers WHERE id = ?", (child,))
    if child:
        repo.purge_paper_files(app, [child])
    elif path and path.is_file():
        path.unlink(missing_ok=True)
        extracted = path.parent / f"{asset_id}_extracted"
        if extracted.is_dir():
            shutil.rmtree(extracted, ignore_errors=True)
