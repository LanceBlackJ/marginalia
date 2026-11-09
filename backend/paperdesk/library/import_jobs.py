"""导入相关的后台任务处理器：本地文件/文件夹、DOI、链接、附加 PDF。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..ai import settings as ai_settings
from ..jobs import JobContext, JobManager
from ..util import ApiError, new_id, normalize_doi
from . import ocr_jobs, online
from .importer import ImportOutcome, create_metadata_only, import_pdf_file
from . import repo


def register(manager: JobManager) -> None:
    from . import reparse

    ocr_jobs.register(manager)
    reparse.register(manager)
    manager.register("import", handle_import)
    manager.register("import_doi", handle_import_doi)
    manager.register("import_url", handle_import_url)
    manager.register("attach_pdf", handle_attach_pdf)
    manager.register("fetch_fulltext", handle_fetch_fulltext)


def _maybe_auto_analyze(ctx: JobContext, paper_id: str, outcome: ImportOutcome) -> None:
    app = ctx.app
    if not outcome.parsed_has_text:
        return
    cfg = ai_settings.public_settings(app)
    if cfg["configured"]:
        from ..ai import embeddings

        try:  # 语义索引成本很低，且让后续问答更准（中文问题可直接检索英文论文）：配置了嵌入模型就自动建立
            embeddings.enqueue_embedding(app, paper_id)
        except ApiError:
            pass
    if cfg["auto_analyze"] and cfg["configured"]:
        from ..ai.analysis import enqueue_analysis

        try:
            enqueue_analysis(app, paper_id)
        except ApiError:
            pass
    if cfg["auto_analyze"] and cfg["configured"]:
        # 公式和表格也在导入后自动处理（不等用户打开页面）：没处理好之前，界面不会说“已完成”
        from . import processing

        try:
            processing.start_auto(app, paper_id)
        except ApiError:
            pass


def _item(inp: str, outcome: ImportOutcome) -> dict:
    item = {"input": inp, "status": outcome.status, "paper_id": outcome.paper_id, "message": outcome.message}
    if outcome.ocr_job_id:  # 扫描版：文字正在后台由本地 OCR 识别
        item.update({"ocr_job_id": outcome.ocr_job_id, "ocr_pages": outcome.ocr_pages})
    if outcome.can_attach_as_supplement:
        item.update({"can_attach_as_supplement": True, "target_paper_id": outcome.target_paper_id,
                     "supplement_hint": outcome.supplement_hint})
    return item


def handle_import(ctx: JobContext) -> dict:
    entries = ctx.payload.get("items") or []
    items = []
    for i, entry in enumerate(entries):
        ctx.check_cancelled()
        path = Path(entry["path"])
        ctx.progress(i / max(1, len(entries)), f"正在导入 {path.name}（{i + 1}/{len(entries)}）")
        try:
            out = import_pdf_file(ctx.app, path, source_folder=entry.get("source_folder"), log=ctx.log)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"导入失败 {path.name}: {type(exc).__name__}: {exc}")
            out = ImportOutcome("failed", message=f"导入出错：{exc}")
        if out.status == "imported" and out.paper_id:
            _maybe_auto_analyze(ctx, out.paper_id, out)
        elif out.status != "imported":
            ctx.log(f"{path.name}: {out.status} — {out.message}")
        items.append(_item(str(path), out))
    return {"items": items}


def _download_and_import(
    ctx: JobContext, resolved: online.ResolvedPaper, *, source_url: str | None
) -> tuple[ImportOutcome | None, str | None]:
    """依次尝试开放获取 PDF 链接。返回 (导入结果, 最后一次下载失败原因)；全部失败时结果为 None。"""
    last_error = None
    with ctx.app.http(timeout=60) as client, tempfile.TemporaryDirectory(prefix="pd_dl_") as tmp:
        for url in resolved.pdf_urls:
            ctx.check_cancelled()
            dest = Path(tmp) / f"{new_id()}.pdf"
            ctx.log(f"下载 {url}")
            try:
                online.download_pdf(client, url, dest)
            except online.DownloadError as exc:
                last_error = str(exc)
                ctx.log(f"下载失败：{exc}")
                continue
            overrides = {**resolved.metadata}
            if resolved.doi:
                overrides["doi"] = resolved.doi
            if resolved.arxiv_id:
                overrides["arxiv_id"] = resolved.arxiv_id
            outcome = import_pdf_file(
                ctx.app, dest, source_url=source_url or url, overrides=overrides,
                override_source=resolved.source, keep_original_path=False, log=ctx.log,
            )
            return outcome, None
    if last_error:
        ctx.log(f"所有开放获取链接均不可用：{last_error}")
    return None, last_error


def _existing_by_doi(ctx: JobContext, doi: str) -> dict | None:
    with ctx.app.db.session() as conn:
        row = conn.execute(
            "SELECT id, title, has_pdf FROM papers WHERE doi = ? AND COALESCE(role, 'paper') = 'paper' LIMIT 1", (doi,)
        ).fetchone()
    return dict(row) if row else None


def handle_import_doi(ctx: JobContext) -> dict:
    dois = ctx.payload.get("dois") or []
    metadata_only = bool(ctx.payload.get("metadata_only"))
    items = []
    for i, raw in enumerate(dois):
        ctx.check_cancelled()
        ctx.progress(i / max(1, len(dois)), f"解析 DOI：{raw}（{i + 1}/{len(dois)}）")
        doi = normalize_doi(raw)
        if not doi:
            items.append({"input": raw, "status": "failed", "paper_id": None, "message": "无法识别的 DOI 格式"})
            continue
        existing = _existing_by_doi(ctx, doi)
        if existing:
            items.append({"input": raw, "status": "duplicate", "paper_id": existing["id"],
                          "message": f"已在文献库中：{existing['title']}"})
            continue
        with ctx.app.http(timeout=15) as client:
            resolved = online.resolve_doi(client, doi)
        if not resolved.metadata:
            msg = "；".join(resolved.errors) or "未能获取该 DOI 的元数据"
            items.append({"input": raw, "status": "failed", "paper_id": None, "message": msg})
            continue
        if metadata_only:  # 用户只要题录：不下载任何全文
            paper_id = create_metadata_only(
                ctx.app, doi=doi, arxiv_id=None, metadata=resolved.metadata,
                access_info=resolved.access_info, source=resolved.source,
            )
            items.append({"input": raw, "status": "metadata_only", "paper_id": paper_id,
                          "message": "按你的选择只保存了题录，没有下载全文。之后可以在论文页面用“附加 PDF”补上。"})
            continue
        out, dl_error = (
            _download_and_import(ctx, resolved, source_url=f"https://doi.org/{doi}") if resolved.pdf_urls else (None, None)
        )
        if out is not None and out.status in ("imported", "duplicate"):
            if out.status == "imported" and out.paper_id:
                _maybe_auto_analyze(ctx, out.paper_id, out)
            items.append(_item(raw, out))
            continue
        # 没有可用的开放获取全文：保留题录，并告知可用来源
        paper_id = create_metadata_only(
            ctx.app, doi=doi, arxiv_id=None, metadata=resolved.metadata,
            access_info=resolved.access_info, source=resolved.source,
        )
        if out is not None:
            hint = out.message or "全文无法解析"
        elif dl_error:
            hint = f"找到的开放获取链接无法使用：{dl_error}"
        else:
            hint = "未找到开放获取的全文"
        items.append({"input": raw, "status": "metadata_only", "paper_id": paper_id, "needs_manual_download": True,
                      "message": f"{hint}。已保存题录，请通过出版商页面或所在机构获取 PDF 后在论文页面附加。"})
    return {"items": items}


def handle_import_url(ctx: JobContext) -> dict:
    url = str(ctx.payload.get("url") or "").strip()
    ctx.progress(0.1, "解析链接")
    arxiv_id = online.parse_arxiv_url(url)
    doi = normalize_doi(url) if "doi.org" in url.lower() else None
    with ctx.app.http(timeout=15) as client:
        if arxiv_id:
            resolved = online.fetch_arxiv(client, arxiv_id)
        elif doi:
            resolved = online.resolve_doi(client, doi)
        elif url.lower().split("?")[0].endswith(".pdf"):
            resolved = online.ResolvedPaper(pdf_urls=[url], source="pdf")
        else:
            return {"items": [{"input": url, "status": "failed", "paper_id": None,
                               "message": "暂不支持该链接。目前支持 arXiv、doi.org 和直链 PDF；其它网站请下载 PDF 后导入。"}]}
    if resolved.doi:
        existing = _existing_by_doi(ctx, resolved.doi)
        if existing and existing["has_pdf"]:
            return {"items": [{"input": url, "status": "duplicate", "paper_id": existing["id"],
                               "message": f"已在文献库中：{existing['title']}"}]}
    if ctx.payload.get("metadata_only"):  # 用户只要题录：不下载 PDF
        if not resolved.metadata:
            return {"items": [{"input": url, "status": "failed", "paper_id": None,
                               "message": "这个链接解析不出题录，无法“仅保存元数据”。请改用 DOI 或 arXiv 链接，或直接导入 PDF。"}]}
        paper_id = create_metadata_only(
            ctx.app, doi=resolved.doi, arxiv_id=resolved.arxiv_id, metadata=resolved.metadata,
            access_info=resolved.access_info, source=resolved.source, source_url=url,
        )
        return {"items": [{"input": url, "status": "metadata_only", "paper_id": paper_id,
                           "message": "按你的选择只保存了题录，没有下载全文。之后可以在论文页面用“附加 PDF”补上。"}]}
    out, dl_error = _download_and_import(ctx, resolved, source_url=url) if resolved.pdf_urls else (None, None)
    if out is not None:
        if out.status == "imported" and out.paper_id:
            _maybe_auto_analyze(ctx, out.paper_id, out)
        return {"items": [_item(url, out)]}
    if resolved.metadata:
        paper_id = create_metadata_only(
            ctx.app, doi=resolved.doi, arxiv_id=resolved.arxiv_id, metadata=resolved.metadata,
            access_info=resolved.access_info, source=resolved.source, source_url=url,
        )
        reason = f"（{dl_error}）" if dl_error else ""
        return {"items": [{"input": url, "status": "metadata_only", "paper_id": paper_id, "needs_manual_download": True,
                           "message": f"未能下载全文{reason}，已保存题录；请自行获取 PDF 后在论文页面附加。"}]}
    return {"items": [{"input": url, "status": "failed", "paper_id": None,
                       "message": dl_error or "；".join(resolved.errors) or "无法下载该链接指向的 PDF"}]}


def handle_fetch_fulltext(ctx: JobContext) -> dict:
    """只有题录的论文：按 arXiv 编号 / DOI 去找开放获取的全文，下载后附加到这篇论文上（不新建论文）。"""
    paper_id = ctx.payload["paper_id"]
    with ctx.app.db.session() as conn:
        row = repo.get_row(conn, paper_id)
    if row["has_pdf"]:
        raise ApiError("conflict", "该论文已有 PDF 文件")
    doi = normalize_doi(row["doi"] or "") if row["doi"] else None
    arxiv_id = row["arxiv_id"]
    if not arxiv_id and doi and doi.lower().startswith("10.48550/arxiv."):
        arxiv_id = doi.split("arxiv.", 1)[1]  # arXiv 自己的 DOI：编号就在里面
    urls: list[str] = []
    with ctx.app.http(timeout=30) as client:
        if arxiv_id:
            urls.append(f"https://arxiv.org/pdf/{arxiv_id}")
        if doi:
            ctx.progress(0.1, "查找开放获取的全文")
            try:
                urls += [u for u in online.resolve_doi(client, doi).pdf_urls if u not in urls]
            except Exception as exc:  # noqa: BLE001  数据源不可用只是少几个候选
                ctx.log(f"查询开放获取链接失败：{exc}")
    if not urls:
        raise ApiError("not_found", "没有找到开放获取的全文。可以在内置浏览器里打开出版商页面下载（需要登录就直接登录），或者选择已下载的 PDF 附加。")
    last = None
    with ctx.app.http(timeout=60) as client, tempfile.TemporaryDirectory(prefix="pd_fulltext_") as tmp:
        for i, url in enumerate(urls):
            ctx.check_cancelled()
            ctx.progress(0.3 + 0.5 * i / len(urls), f"下载 {url}")
            dest = Path(tmp) / f"{new_id()}.pdf"
            try:
                online.download_pdf(client, url, dest)
            except online.DownloadError as exc:
                last = str(exc)
                ctx.log(f"下载失败：{exc}")
                continue
            out = import_pdf_file(ctx.app, dest, attach_to=paper_id, log=ctx.log)
            if out.status == "failed":
                last = out.message or "解析失败"
                continue
            if out.status == "duplicate":
                raise ApiError("conflict", out.message or "该 PDF 已存在于文献库")
            _maybe_auto_analyze(ctx, paper_id, out)
            return {"items": [_item(url, out)]}
    raise ApiError("not_found", f"找到的开放获取链接都没能下载成功（{last or '未知原因'}）。可以在内置浏览器里打开出版商页面下载，或选择已下载的 PDF 附加。")


def handle_attach_pdf(ctx: JobContext) -> dict:
    paper_id = ctx.payload["paper_id"]
    path = Path(ctx.payload["path"])
    with ctx.app.db.session() as conn:
        row = repo.get_row(conn, paper_id)
    if row["has_pdf"]:
        raise ApiError("conflict", "该论文已有 PDF 文件")
    ctx.progress(0.2, f"解析 {path.name}")
    out = import_pdf_file(ctx.app, path, attach_to=paper_id, log=ctx.log)
    if out.status == "failed":
        raise ApiError("bad_request", out.message or "附加 PDF 失败")
    if out.status == "duplicate":
        raise ApiError("conflict", out.message or "该 PDF 已存在于文献库")
    _maybe_auto_analyze(ctx, paper_id, out)
    return {"items": [_item(str(path), out)]}
