"""仓库的创建、导入（zip/文件夹/GitHub）、读取与查询。"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Callable

from ..context import AppContext
from ..util import ApiError, dumps, loads, new_id, utcnow
from .analyze import analyze_repo
from .safeio import ExtractReport, FileEntry, copy_folder, safe_extract_zip

MAX_VIEW_BYTES = 600_000


def repo_root(app: AppContext, paper_id: str, repo_id: str) -> Path:
    return app.paths.paper_dir(paper_id) / "code" / repo_id


def _row_to_dict(r) -> dict:
    return {
        "id": r["id"], "paper_id": r["paper_id"], "origin": r["origin"], "authored_by": r["authored_by"], "url": r["url"],
        "owner": r["owner"], "name": r["name"], "branch": r["branch"], "commit_sha": r["commit_sha"], "commit_date": r["commit_date"],
        "downloaded_at": r["downloaded_at"], "size_bytes": r["size_bytes"], "status": r["status"], "error": r["error"],
        "warnings": loads(r["warnings_json"], []), "overview": loads(r["overview_json"]), "created_at": r["created_at"],
    }


def create_repo(app: AppContext, paper_id: str, *, origin: str, authored_by: str = "author", url: str | None = None,
                owner: str | None = None, name: str | None = None, branch: str | None = None, commit_sha: str | None = None,
                commit_date: str | None = None, warnings: list[str] | None = None, status: str = "pending") -> str:
    repo_id = new_id()
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        conn.execute(
            "INSERT INTO code_repos(id, paper_id, origin, authored_by, url, owner, name, branch, commit_sha, commit_date, "
            "warnings_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (repo_id, paper_id, origin, authored_by, url, owner, name, branch, commit_sha, commit_date, dumps(warnings or []), status, utcnow()),
        )
    return repo_id


def set_status(app: AppContext, repo_id: str, status: str, error: str | None = None, **fields) -> None:
    sets, args = ["status = ?", "error = ?"], [status, error]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        args.append(dumps(v) if k.endswith("_json") else v)
    with app.db.session() as conn:
        conn.execute(f"UPDATE code_repos SET {', '.join(sets)} WHERE id = ?", (*args, repo_id))


def get_repo(app: AppContext, repo_id: str) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM code_repos WHERE id = ?", (repo_id,)).fetchone()
    if r is None:
        raise ApiError("not_found", "代码仓库不存在", 404)
    return _row_to_dict(r)


def list_repos(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT * FROM code_repos WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_repo(app: AppContext, repo_id: str) -> None:
    repo = get_repo(app, repo_id)
    with app.db.session() as conn:
        conn.execute("DELETE FROM code_repos WHERE id = ?", (repo_id,))
    root = repo_root(app, repo["paper_id"], repo_id).resolve()
    base = (app.paths.paper_dir(repo["paper_id"]) / "code").resolve()
    if root.parent == base and root.exists():
        shutil.rmtree(root, ignore_errors=True)


def finish_ingest(app: AppContext, repo: dict, report: ExtractReport, log: Callable[[str], None]) -> dict:
    """解压/复制完成后：分析并标记就绪。"""
    root = repo_root(app, repo["paper_id"], repo["id"])
    set_status(app, repo["id"], "scanning", size_bytes=report.total_bytes)
    overview = analyze_repo(app, repo["id"], root, report.files, log)
    warnings = list(repo["warnings"]) + report.warnings
    set_status(app, repo["id"], "ready", None, warnings_json=warnings)
    return overview


def reanalyze(app: AppContext, repo_id: str, log: Callable[[str], None]) -> dict:
    """用当前版本的解析器重新分析已落盘的代码（解析器升级后无需重新下载）；论文↔代码的关联按 路径+符号名 迁移。"""
    repo = get_repo(app, repo_id)
    root = repo_root(app, repo["paper_id"], repo_id)
    if not root.is_dir():
        raise ApiError("not_found", "本地代码副本不存在，请重新获取")
    with app.db.session() as conn:
        entries = [FileEntry(r["path"], r["size"], r["skipped"]) for r in conn.execute("SELECT path, size, skipped FROM code_files WHERE repo_id = ?", (repo_id,))]
        old = {r["id"]: (r["path"], r["qualname"], r["kind"]) for r in conn.execute("SELECT id, path, qualname, kind FROM code_symbols WHERE repo_id = ?", (repo_id,))}
        links = [(r["id"], old.get(r["symbol_id"])) for r in conn.execute("SELECT id, symbol_id FROM paper_code_links WHERE repo_id = ? AND symbol_id IS NOT NULL", (repo_id,))]
    set_status(app, repo_id, "scanning")
    try:
        overview = analyze_repo(app, repo_id, root, entries, log)
    except Exception as exc:  # noqa: BLE001
        set_status(app, repo_id, "failed", f"{type(exc).__name__}: {exc}"[:400])
        raise
    set_status(app, repo_id, "ready", None)
    with app.db.session() as conn:
        new = {(r["path"], r["qualname"], r["kind"]): r["id"] for r in conn.execute("SELECT id, path, qualname, kind FROM code_symbols WHERE repo_id = ?", (repo_id,))}
        for link_id, key in links:
            if key and key in new:
                conn.execute("UPDATE paper_code_links SET symbol_id = ? WHERE id = ?", (new[key], link_id))
    return overview


def ingest_zip(app: AppContext, repo: dict, zip_path: Path, log: Callable[[str], None]) -> dict:
    root = repo_root(app, repo["paper_id"], repo["id"])
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    log("安全解压 …")
    report = safe_extract_zip(zip_path, root)
    log(f"解压完成：{len(report.files)} 个文件（{report.total_bytes // 1024} KB）")
    return finish_ingest(app, repo, report, log)


def ingest_folder(app: AppContext, repo: dict, folder: Path, log: Callable[[str], None]) -> dict:
    root = repo_root(app, repo["paper_id"], repo["id"])
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    log("复制文件夹…")
    report = copy_folder(folder, root)
    return finish_ingest(app, repo, report, log)


# ---- 浏览 ----
def clean_rel_path(path: str) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ApiError("bad_request", "路径不合法")
    return str(p)


def read_file(app: AppContext, repo_id: str, path: str) -> dict:
    repo = get_repo(app, repo_id)
    rel = clean_rel_path(path)
    with app.db.session() as conn:
        f = conn.execute("SELECT * FROM code_files WHERE repo_id = ? AND path = ?", (repo_id, rel)).fetchone()
        syms = conn.execute(
            "SELECT id, path, name, qualname, kind, parent, line_start, line_end, signature, doc FROM code_symbols WHERE repo_id = ? AND path = ? ORDER BY line_start",
            (repo_id, rel),
        ).fetchall()
    if f is None:
        raise ApiError("not_found", "文件不存在", 404)
    base = {"path": rel, "language": f["language"], "size": f["size"], "lines": f["lines"], "role": f["role"], "skipped": f["skipped"],
            "symbols": [dict(s) for s in syms]}
    if f["skipped"]:
        reason = {"binary": "二进制文件，未提取内容", "too_large": "文件过大，未提取内容"}.get(f["skipped"], "未提取内容")
        return {**base, "content": None, "truncated": False, "note": reason}
    full = (repo_root(app, repo["paper_id"], repo_id) / rel).resolve()
    root = repo_root(app, repo["paper_id"], repo_id).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ApiError("bad_request", "路径不合法") from exc
    try:
        data = full.read_bytes()
    except OSError as exc:
        raise ApiError("not_found", "文件内容不可读", 404) from exc
    truncated = len(data) > MAX_VIEW_BYTES
    text = data[:MAX_VIEW_BYTES].decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # CRLF/CR -> LF，行号不变
    return {**base, "content": text, "truncated": truncated, "note": "文件过长，只显示前 600 KB" if truncated else None}


def file_lines(app: AppContext, repo_id: str, path: str) -> list[str] | None:
    """用于证据核实：返回真实文件的行列表；不存在/二进制返回 None。"""
    try:
        r = read_file(app, repo_id, path)
    except ApiError:
        return None
    return None if r["content"] is None else r["content"].split("\n")


def file_tree(app: AppContext, repo_id: str) -> dict:
    with app.db.session() as conn:
        rows = conn.execute("SELECT path, language, size, lines, role, skipped FROM code_files WHERE repo_id = ? ORDER BY path", (repo_id,)).fetchall()
    return {"files": [dict(r) for r in rows]}


def search_symbols(app: AppContext, repo_id: str, q: str | None, kind: str | None, limit: int = 100) -> list[dict]:
    where, args = ["repo_id = ?"], [repo_id]
    if q:
        where.append("(lower(name) LIKE ? OR lower(qualname) LIKE ?)")
        args += [f"%{q.lower()}%"] * 2
    if kind:
        where.append("kind = ?")
        args.append(kind)
    with app.db.session() as conn:
        rows = conn.execute(
            f"SELECT id, path, name, qualname, kind, parent, line_start, line_end, signature, doc FROM code_symbols WHERE {' AND '.join(where)} "
            "ORDER BY (kind IN ('module','main')), path, line_start LIMIT ?", (*args, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def symbol_graph(app: AppContext, repo_id: str, symbol_id: int) -> dict:
    with app.db.session() as conn:
        s = conn.execute("SELECT * FROM code_symbols WHERE id = ? AND repo_id = ?", (symbol_id, repo_id)).fetchone()
        if s is None:
            raise ApiError("not_found", "符号不存在", 404)
        callees = conn.execute(
            "SELECT c.line, c.callee_name, c.certainty, c.callee_id, t.qualname, t.path, t.line_start, t.line_end FROM code_calls c "
            "LEFT JOIN code_symbols t ON t.id = c.callee_id WHERE c.repo_id = ? AND c.caller_id = ? ORDER BY c.line", (repo_id, symbol_id),
        ).fetchall()
        callers = conn.execute(
            "SELECT c.line, c.certainty, c.caller_id, p.qualname, p.path, p.line_start, p.line_end, p.kind FROM code_calls c "
            "JOIN code_symbols p ON p.id = c.caller_id WHERE c.repo_id = ? AND c.callee_id = ? ORDER BY p.path, c.line", (repo_id, symbol_id),
        ).fetchall()
    return {
        "symbol": dict(s),
        "callees": [dict(r) for r in callees],
        "callers": [{**dict(r), "qualname": "__main__" if r["kind"] == "main" else r["qualname"]} for r in callers],
    }
