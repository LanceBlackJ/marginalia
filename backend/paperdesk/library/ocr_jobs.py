"""扫描版 PDF 的后台 OCR 任务：导入后立刻可读，文字在后台由本地 OCR 识别。

要点：
- 全部在本机完成，不发往云端；每页约 10 秒（CPU），长文档在任务页能看到进度，可随时取消；
- 识别结果先全部放在内存里，最后在一个事务里整体替换这篇论文的段落/章节/图表数据——
  取消或失败时库里不会留下半截数据，论文仍保持“扫描版、暂无文字层”的原状；
- 识别出来的段落标注 source='ocr'，界面应提示“可能有识别错误”。
"""
from __future__ import annotations

from pathlib import Path

from ..context import AppContext
from ..jobs import JobCancelled, JobContext, JobManager
from ..parsing import ocr
from ..parsing.pdf_parser import parse_pdf
from ..util import ApiError
from .importer import _store_parsed

JOB_TYPE = "ocr_paper"
SECONDS_PER_PAGE = 10


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle_ocr_paper)


def enqueue(app: AppContext, paper_id: str, pages: int) -> str:
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    return app.jobs.submit(JOB_TYPE, f"识别扫描页文字：{title[:40]}（{pages} 页）", {"paper_id": paper_id}, paper_id)


def _set_note(app: AppContext, paper_id: str, note: str | None) -> None:
    with app.db.session() as conn:
        conn.execute("UPDATE papers SET parse_error = ? WHERE id = ?", (note, paper_id))


def handle_ocr_paper(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id = ctx.payload["paper_id"]
    path = app.paths.paper_dir(paper_id) / "original.pdf"
    if not path.exists():
        raise ApiError("not_found", "找不到这篇论文的 PDF 文件")
    if not ocr.available():
        _set_note(app, paper_id, "没有安装本地 OCR 组件（rapidocr-onnxruntime），无法识别扫描页的文字。")
        raise ApiError("not_configured", "没有安装本地 OCR 组件（rapidocr-onnxruntime）。")
    try:
        pages = parse_pdf(path).needs_ocr
        if not pages:
            return {"pages": 0, "chars": 0}
        spans: dict[int, list] = {}
        for i, pno in enumerate(pages, 1):
            ctx.check_cancelled()
            spans[pno] = ocr.ocr_page(path, pno)
            ctx.progress(i / len(pages), f"已识别 {i}/{len(pages)} 页")
        ctx.check_cancelled()
        parsed = parse_pdf(path, ocr_pages=spans)
        chars = sum(len(p.text) for p in parsed.passages if p.source == "ocr")
        if not parsed.has_text_layer:
            _set_note(app, paper_id, "已用本地 OCR 尝试识别，没有识别到可用的文字。可以直接阅读原文。")
            return {"pages": len(pages), "chars": 0}
        warning = "；".join(parsed.warnings) or None
        with app.db.session() as conn:  # 一个事务里整体替换：失败时回滚，不留半截数据
            for table in ("sections", "passages", "page_info", "figures", "equations"):
                conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))
            _store_parsed(conn, paper_id, parsed)
            conn.execute(
                "UPDATE papers SET language = ?, parse_error = ?, abstract = COALESCE(abstract, ?) WHERE id = ?",
                (parsed.language, warning, parsed.metadata.abstract, paper_id),
            )
            _fill_metadata_from_ocr(conn, paper_id, parsed)
        _after_text_ready(ctx, paper_id)
        return {"pages": len(pages), "chars": chars}
    except JobCancelled:
        _set_note(app, paper_id, "扫描页文字识别已取消，可在任务页重试。")
        raise
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        _set_note(app, paper_id, f"扫描页文字识别没有完成：{exc}。可在任务页重试。")
        raise


def _fill_metadata_from_ocr(conn, paper_id: str, parsed) -> None:
    """扫描版导入时没有文字层，标题只能拿文件名顶替、作者年份是空的；识别出文字后把它们补上。
    只补空缺和“文件名顶替的标题”，Crossref 的题录或用户自己改过的值不动。"""
    from .reparse import _refill_metadata

    _refill_metadata(conn, paper_id, parsed)
    row = conn.execute("SELECT title, original_path, metadata_source FROM papers WHERE id = ?", (paper_id,)).fetchone()
    title = parsed.metadata.title
    if row and row["metadata_source"] == "pdf" and title and row["original_path"]:
        stem = Path(row["original_path"]).stem.strip()
        if stem and (row["title"] or "").strip() == stem and title.strip() != stem:
            conn.execute("UPDATE papers SET title = ? WHERE id = ?", (title.strip(), paper_id))


def _after_text_ready(ctx: JobContext, paper_id: str) -> None:
    """有了文字就和普通导入一样：已配置嵌入模型则建立语义索引，开了自动分析则分析。"""
    from ..ai import settings as ai_settings

    app = ctx.app
    cfg = ai_settings.public_settings(app)
    if not cfg["configured"]:
        return
    from ..ai import embeddings

    try:
        embeddings.enqueue_embedding(app, paper_id)
    except ApiError:
        pass
    if cfg["auto_analyze"]:
        from ..ai import glossary
        from ..ai.analysis import enqueue_analysis

        try:
            enqueue_analysis(app, paper_id)
        except ApiError:
            pass
        try:
            glossary.enqueue(app, paper_id)
        except ApiError:
            pass
