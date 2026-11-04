"""PDF 导入：校验 → 哈希去重 → 解析 → 复制到 Marginalia 管理目录 → 入库。

原则：只复制、不移动、不删除用户的原始文件；DOI/哈希重复不会产生重复记录。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..context import AppContext
from ..db import register_column, register_schema
from ..parsing.pdf_parser import PARSER_VERSION, ParsedDoc, PdfError, parse_pdf
from ..util import dumps, new_id, utcnow
from . import online

# 段落的文字来源：text = PDF 文字层；ocr = 扫描页的本地 OCR（可能有识别错误，界面要提示）
register_column("ocr", "passages", "source", "TEXT NOT NULL DEFAULT 'text'")
register_column("math", "passages", "math_json", "TEXT")  # 行内公式 [{src, tex}]（JSON）；旧论文重新解析后才有
register_column("bold", "passages", "bold_json", "TEXT")  # 原文加粗的文字片段（JSON 数组）；旧论文重新解析后才有
# 解析器版本（见 parsing.pdf_parser.PARSER_VERSION）：旧库里的论文是 1，提示“有新版解析，可重新解析”
register_column("reparse", "papers", "parser_version", "INTEGER NOT NULL DEFAULT 1")

# 解析器产出的图表/公式实体（阶段二的图表与公式理解会用到：区域坐标 → 截图 → 视觉模型）
register_schema("parsing_entities", """
CREATE TABLE IF NOT EXISTS figures (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'figure',
  label TEXT,
  caption_ord INTEGER,
  inner_ord INTEGER
);
CREATE INDEX IF NOT EXISTS idx_figures_paper ON figures(paper_id, page);
CREATE TABLE IF NOT EXISTS equations (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  number TEXT,
  text TEXT NOT NULL DEFAULT '',
  passage_ord INTEGER
);
CREATE INDEX IF NOT EXISTS idx_equations_paper ON equations(paper_id, page);
""")

MAX_LOCAL_BYTES = 500 * 1024 * 1024


@dataclass
class ImportOutcome:
    status: str  # imported | duplicate | failed
    paper_id: str | None = None
    message: str | None = None
    parsed_has_text: bool = False
    # 与已有论文同 DOI 但文件不同：很可能是它的补充材料，让用户一键“作为补充材料添加”
    can_attach_as_supplement: bool = False
    target_paper_id: str | None = None
    supplement_hint: bool = False  # 文件名/标题/首页含 Supplementary 等字样，或页数远多于正文
    ocr_job_id: str | None = None  # 扫描版：后台本地 OCR 任务（识别完成前没有文字层）
    ocr_pages: int = 0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9一-鿿]{2,}", (text or "").lower())}


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_doi_match(app: AppContext, doi: str | None, title: str) -> tuple[str, int, str] | None:
    """按 DOI 找已有论文；为避免把“引用的 DOI”误当作自身 DOI，要求标题也相近。"""
    if not doi:
        return None
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT id, has_pdf, title FROM papers WHERE doi = ? AND COALESCE(role, 'paper') = 'paper'", (doi,)
        ).fetchall()
    for r in rows:
        if not title or not r["title"] or title_similarity(title, r["title"]) >= 0.5:
            return r["id"], r["has_pdf"], r["title"]
    return None


_SUPP_WORDS = re.compile(r"supplement|supporting information|extended data|appendix|补充|附录|supp[._-]", re.I)


def looks_like_supplement(filename: str, parsed: ParsedDoc, existing_pages: int | None) -> bool:
    """文件名 / 标题 / 首页文字含 Supplementary 等字样，或页数远多于已有论文，则很可能是补充材料。"""
    first_page = " ".join(p.text for p in parsed.passages if p.page == 1)[:1500]
    if _SUPP_WORDS.search(f"{filename} {parsed.metadata.title or ''} {first_page}"):
        return True
    return bool(existing_pages and parsed.page_count >= max(2 * existing_pages, existing_pages + 8))


def validate_pdf_file(src: Path) -> str | None:
    """返回错误说明；合法返回 None。"""
    if not src.is_file():
        return "文件不存在或无法访问"
    if src.suffix.lower() != ".pdf":
        return "不是 PDF 文件"
    size = src.stat().st_size
    if size == 0:
        return "文件为空"
    if size > MAX_LOCAL_BYTES:
        return f"文件过大（{size // 1024 // 1024} MB，上限 {MAX_LOCAL_BYTES // 1024 // 1024} MB）"
    with open(src, "rb") as f:
        if b"%PDF-" not in f.read(1024):
            return "文件内容不是有效的 PDF"
    return None


def import_pdf_file(
    app: AppContext,
    src: Path,
    *,
    source_folder: str | None = None,
    source_url: str | None = None,
    overrides: dict | None = None,
    override_source: str | None = None,
    attach_to: str | None = None,
    enrich: bool = True,
    keep_original_path: bool = True,
    log: Callable[[str], None] | None = None,
) -> ImportOutcome:
    say = log or (lambda _m: None)
    err = validate_pdf_file(src)
    if err:
        return ImportOutcome("failed", message=err)

    digest = sha256_file(src)
    with app.db.session() as conn:
        dup = conn.execute("SELECT id, title FROM papers WHERE file_hash = ?", (digest,)).fetchone()
    if dup:
        with app.db.session() as conn:
            info = conn.execute("SELECT role, parent_id FROM papers WHERE id = ?", (dup["id"],)).fetchone()
        if info and info["role"] == "supplement":
            return ImportOutcome("duplicate", info["parent_id"] or dup["id"], f"这个文件已经作为补充材料添加过了：{dup['title'] or src.name}")
        return ImportOutcome("duplicate", dup["id"], f"已在文献库中：{dup['title'] or src.name}")

    say(f"解析 {src.name} …")
    try:
        parsed = parse_pdf(src)
    except PdfError as exc:
        return ImportOutcome("failed", message=str(exc))

    meta = dict(
        title=parsed.metadata.title, authors=parsed.metadata.authors, year=parsed.metadata.year,
        venue=parsed.metadata.venue, doi=parsed.metadata.doi, arxiv_id=parsed.metadata.arxiv_id,
        abstract=parsed.metadata.abstract,
    )
    metadata_source = "pdf"
    if overrides:
        for k, v in overrides.items():
            if v:
                meta[k] = v
        metadata_source = override_source or "crossref"
    elif enrich and meta["doi"]:
        try:
            with app.http(timeout=8) as client:
                cr = online.fetch_crossref(client, meta["doi"])
            # 标题相近才采纳，避免把引用文献的 DOI 当成自身
            if cr and (not meta["title"] or title_similarity(cr["title"], meta["title"]) >= 0.5):
                for k in ("title", "authors", "year", "venue", "abstract"):
                    if cr.get(k):
                        meta[k] = cr[k]
                metadata_source = "crossref"
                say("已通过 Crossref 补全题录")
        except (online.NotFound, Exception):  # noqa: BLE001  联网补全仅为尽力而为
            pass
    title = (meta["title"] or "").strip() or src.stem

    if attach_to is None:
        match = _find_doi_match(app, meta["doi"], title)
        if match:
            existing_id, has_pdf, existing_title = match
            if has_pdf:
                with app.db.session() as conn:
                    pc = conn.execute("SELECT page_count FROM papers WHERE id = ?", (existing_id,)).fetchone()
                return ImportOutcome(
                    "duplicate", existing_id,
                    f"相同 DOI 的论文已存在：{existing_title}。如果这个文件是它的补充材料，可以作为补充材料添加。",
                    can_attach_as_supplement=True, target_paper_id=existing_id,
                    supplement_hint=looks_like_supplement(src.name, parsed, pc["page_count"] if pc else None),
                )
            attach_to = existing_id
            say("匹配到已有的无全文记录，将把 PDF 附加到该论文")

    paper_id = attach_to or new_id()
    pdir = app.paths.paper_dir(paper_id)
    pdir.mkdir(parents=True, exist_ok=True)
    dest = pdir / "original.pdf"
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return ImportOutcome("failed", message=f"复制文件失败：{exc.strerror or exc}")

    from ..parsing import ocr  # 局部导入：ocr_jobs 反过来要用本模块的 _store_parsed
    from . import ocr_jobs

    will_ocr = bool(parsed.needs_ocr) and ocr.available()
    if parsed.needs_ocr:
        n = len(parsed.needs_ocr)
        parsed.warnings = [w for w in parsed.warnings if "尚未识别" not in w and "未检测到文字层" not in w]
        parsed.warnings.append(
            f"这是扫描版 PDF（{n} 页没有文字层）：正在后台用本地 OCR 识别文字（约每页 {ocr_jobs.SECONDS_PER_PAGE} 秒，不会上传到云端），"
            "识别完成前全文检索与 AI 分析暂不可用；识别出的文字可能有错误，请以原文为准。"
            if will_ocr else
            f"这是扫描版 PDF（{n} 页没有文字层），但本机没有安装本地 OCR 组件，无法识别文字。可以阅读原文。"
        )
    warning = "；".join(parsed.warnings) or None
    now = utcnow()
    try:
        with app.db.session() as conn:
            if attach_to:
                conn.execute("DELETE FROM sections WHERE paper_id = ?", (paper_id,))
                conn.execute("DELETE FROM passages WHERE paper_id = ?", (paper_id,))
                conn.execute("DELETE FROM page_info WHERE paper_id = ?", (paper_id,))
                conn.execute("DELETE FROM figures WHERE paper_id = ?", (paper_id,))
                conn.execute("DELETE FROM equations WHERE paper_id = ?", (paper_id,))
                conn.execute("DELETE FROM file_assets WHERE paper_id = ?", (paper_id,))
                row = conn.execute("SELECT title, authors_json, year, venue, abstract FROM papers WHERE id=?", (paper_id,)).fetchone()
                # 已有记录（如来自 DOI 的元数据）优先
                if row and row["title"] and not overrides:
                    title = row["title"]
                conn.execute(
                    "UPDATE papers SET title=?, authors_json=?, year=COALESCE(year, ?), venue=COALESCE(venue, ?), "
                    "doi=COALESCE(doi, ?), arxiv_id=COALESCE(arxiv_id, ?), abstract=COALESCE(abstract, ?), "
                    "has_pdf=1, file_hash=?, status='ready', parse_error=?, page_count=?, language=?, "
                    "original_path=?, source_folder=?, source_url=COALESCE(?, source_url) WHERE id=?",
                    (
                        title,
                        row["authors_json"] if row and row["authors_json"] not in (None, "[]") else dumps(meta["authors"]),
                        meta["year"], meta["venue"], meta["doi"], meta["arxiv_id"], meta["abstract"],
                        digest, warning, parsed.page_count, parsed.language,
                        str(src) if keep_original_path else None, source_folder, source_url, paper_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO papers(id, title, authors_json, year, venue, doi, arxiv_id, abstract, source_url, "
                    "original_path, source_folder, has_pdf, file_hash, status, parse_error, page_count, language, "
                    "imported_at, metadata_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)",
                    (
                        paper_id, title, dumps(meta["authors"]), meta["year"], meta["venue"], meta["doi"],
                        meta["arxiv_id"], meta["abstract"], source_url,
                        str(src) if keep_original_path else None, source_folder, digest, "ready", warning,
                        parsed.page_count, parsed.language, now, metadata_source,
                    ),
                )
            _store_parsed(conn, paper_id, parsed)
            conn.execute(
                "INSERT INTO file_assets(id, paper_id, kind, rel_path, sha256, size, original_path, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), paper_id, "original_pdf", f"papers/{paper_id}/original.pdf", digest,
                 dest.stat().st_size, str(src) if keep_original_path else None, now),
            )
    except Exception:
        if not attach_to:
            shutil.rmtree(pdir, ignore_errors=True)
        raise

    (pdir / "metadata.json").write_text(
        dumps({"id": paper_id, "title": title, **{k: meta[k] for k in ("authors", "year", "venue", "doi", "arxiv_id")},
               "source_url": source_url, "original_path": str(src) if keep_original_path else None,
               "sha256": digest, "imported_at": now, "metadata_source": metadata_source}),
        encoding="utf-8",
    )
    say(f"已导入：{title}（{parsed.page_count} 页，{len(parsed.passages)} 个段落）")
    ocr_job = None
    if will_ocr:
        ocr_job = ocr_jobs.enqueue(app, paper_id, len(parsed.needs_ocr))
        say(f"这是扫描版：已开始在后台用本地 OCR 识别 {len(parsed.needs_ocr)} 页的文字")
    return ImportOutcome("imported", paper_id, warning, parsed_has_text=parsed.has_text_layer,
                         ocr_job_id=ocr_job, ocr_pages=len(parsed.needs_ocr) if will_ocr else 0)


def _store_parsed(conn, paper_id: str, parsed: ParsedDoc) -> None:
    conn.executemany(
        "INSERT INTO page_info(paper_id, page, width, height) VALUES (?,?,?,?)",
        [(paper_id, i + 1, w, h) for i, (w, h) in enumerate(parsed.pages)],
    )
    section_ids: dict[int, int] = {}
    for s in parsed.sections:
        cur = conn.execute(
            "INSERT INTO sections(paper_id, ord, level, title, page, source) VALUES (?,?,?,?,?,?)",
            (paper_id, s.ord, s.level, s.title, s.page, s.source),
        )
        section_ids[s.ord] = cur.lastrowid
    conn.executemany(
        "INSERT INTO passages(paper_id, ord, page, section_id, kind, text, x0, y0, x1, y1, source, bold_json, math_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (paper_id, p.ord, p.page, section_ids.get(p.section_ord) if p.section_ord is not None else None,
             p.kind, p.text, *p.bbox, p.source, json.dumps(p.bold, ensure_ascii=False) if p.bold else None,
             json.dumps(p.math, ensure_ascii=False) if p.math else None)
            for p in parsed.passages
        ],
    )
    conn.executemany(
        "INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label, caption_ord, inner_ord) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(paper_id, f.page, *f.bbox, f.kind, f.label, f.caption_ord, f.inner_ord) for f in parsed.figures],
    )
    conn.executemany(
        "INSERT INTO equations(paper_id, page, x0, y0, x1, y1, number, text, passage_ord) VALUES (?,?,?,?,?,?,?,?,?)",
        [(paper_id, e.page, *e.bbox, e.number, e.text, e.passage_ord) for e in parsed.equations],
    )
    conn.execute("UPDATE papers SET parser_version = ? WHERE id = ?", (PARSER_VERSION, paper_id))


def create_metadata_only(
    app: AppContext, *, doi: str | None, arxiv_id: str | None, metadata: dict, access_info: list[dict],
    source: str, source_url: str | None = None,
) -> str:
    paper_id = new_id()
    with app.db.session() as conn:
        conn.execute(
            "INSERT INTO papers(id, title, authors_json, year, venue, doi, arxiv_id, abstract, source_url, has_pdf, "
            "status, imported_at, metadata_source, access_info_json) VALUES (?,?,?,?,?,?,?,?,?,0,'metadata_only',?,?,?)",
            (
                paper_id, metadata.get("title") or doi or arxiv_id or "未命名论文", dumps(metadata.get("authors") or []),
                metadata.get("year"), metadata.get("venue"), doi, arxiv_id, metadata.get("abstract"),
                source_url or (access_info[0]["url"] if access_info else None), utcnow(), source, dumps(access_info),
            ),
        )
    return paper_id
