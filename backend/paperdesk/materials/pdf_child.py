"""PDF 补充材料 → 子论文行（papers.role='supplement'）。复用整套解析/阅读器/全文检索，但不出现在文献库列表里。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from ..context import AppContext
from ..library.importer import _store_parsed, sha256_file, title_similarity, validate_pdf_file
from ..parsing.pdf_parser import PdfError, ParsedDoc, parse_pdf
from ..util import ApiError, dumps, new_id, utcnow


def import_supplement_pdf(
    app: AppContext, parent_id: str, src: Path, *, original_name: str | None = None,
    source_url: str | None = None, log: Callable[[str], None] | None = None,
) -> tuple[str, ParsedDoc, str]:
    """把 PDF 作为主论文的补充材料入库，返回 (子论文 id, 解析结果, sha256)。"""
    say = log or (lambda _m: None)
    err = validate_pdf_file(src) if src.suffix.lower() == ".pdf" else "不是 PDF 文件"
    if err:
        raise ApiError("bad_request", err)
    digest = sha256_file(src)
    with app.db.session() as conn:
        dup = conn.execute(
            "SELECT id, title, role, parent_id FROM papers WHERE file_hash = ?", (digest,)
        ).fetchone()
        parent = conn.execute("SELECT title FROM papers WHERE id = ?", (parent_id,)).fetchone()
    if parent is None:
        raise ApiError("not_found", "主论文不存在", 404)
    if dup:
        if dup["role"] == "supplement":
            raise ApiError("conflict", "这个文件已经作为补充材料添加过了", 409)
        raise ApiError("conflict", f"这个文件已作为独立论文存在于文献库：{dup['title'][:40]}。可以在文献库里把它转为补充材料。", 409)
    say(f"解析 {src.name}（{src.stat().st_size // 1024 // 1024} MB）…")
    try:
        parsed = parse_pdf(src)
    except PdfError as exc:
        raise ApiError("bad_request", str(exc)) from exc

    stem = Path(original_name or src.name).stem
    title = (parsed.metadata.title or "").strip()
    if not title or title_similarity(title, parent["title"]) >= 0.8:
        title = stem  # 补充材料常与正文同标题，改用文件名区分
    child_id = new_id()
    pdir = app.paths.paper_dir(child_id)
    pdir.mkdir(parents=True, exist_ok=True)
    dest = pdir / "original.pdf"
    shutil.copy2(src, dest)
    now = utcnow()
    warning = "；".join(parsed.warnings) or None
    try:
        with app.db.session() as conn:
            conn.execute(
                "INSERT INTO papers(id, title, authors_json, year, venue, doi, arxiv_id, abstract, source_url, original_path, "
                "has_pdf, file_hash, status, parse_error, page_count, language, imported_at, metadata_source, role, parent_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
                (child_id, title[:500], dumps(parsed.metadata.authors), parsed.metadata.year, parsed.metadata.venue,
                 parsed.metadata.doi, parsed.metadata.arxiv_id, parsed.metadata.abstract, source_url,
                 str(src) if not source_url else None, digest, "ready", warning, parsed.page_count, parsed.language,
                 now, "pdf", "supplement", parent_id),
            )
            _store_parsed(conn, child_id, parsed)
    except Exception:
        shutil.rmtree(pdir, ignore_errors=True)
        raise
    say(f"已解析：{parsed.page_count} 页，{len(parsed.passages)} 个段落，{len(parsed.sections)} 个章节")
    return child_id, parsed, digest


def convert_existing_to_supplement(app: AppContext, parent_id: str, paper_id: str) -> str:
    """把文献库里已有的独立论文转为主论文的补充材料（保留已有解析与阅读进度）。"""
    if parent_id == paper_id:
        raise ApiError("bad_request", "不能把论文设为自己的补充材料")
    with app.db.session() as conn:
        row = conn.execute("SELECT id, role, has_pdf FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if row is None:
            raise ApiError("not_found", "要转换的论文不存在", 404)
        if row["role"] == "supplement":
            raise ApiError("conflict", "该论文已经是某篇论文的补充材料", 409)
        if not row["has_pdf"]:
            raise ApiError("bad_request", "该论文没有 PDF 文件，无法作为补充材料")
        if conn.execute("SELECT 1 FROM papers WHERE parent_id = ? LIMIT 1", (paper_id,)).fetchone():
            raise ApiError("conflict", "该论文自己带有补充材料，不能再作为别人的补充材料", 409)
        conn.execute("UPDATE papers SET role='supplement', parent_id=? WHERE id=?", (parent_id, paper_id))
    return paper_id
