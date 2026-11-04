"""后台任务：获取（GitHub）与导入（zip/文件夹）代码仓库并分析。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..jobs import JobContext, JobManager
from ..util import ApiError
from . import fetch, service
from .links import classify


def register(manager: JobManager) -> None:
    manager.register("code_fetch", handle_code_fetch)
    manager.register("code_import", handle_code_import)
    manager.register("code_reanalyze", handle_code_reanalyze)
    register_ai(manager)


def handle_code_fetch(ctx: JobContext) -> dict:
    """下载并分析 GitHub（或直链 zip）仓库。payload: {paper_id, repo_id, url, sha?, branch?, requires_approval?, approved?}"""
    p = ctx.payload
    app = ctx.app
    repo = service.get_repo(app, p["repo_id"])
    if p.get("requires_approval") and not p.get("approved"):
        raise ApiError("approval_required", "该仓库较大，需要先确认后才会下载。")
    try:
        service.set_status(app, repo["id"], "downloading")
        url = p["url"]
        kind, _supported = classify(url)
        with app.http(timeout=120) as client, tempfile.TemporaryDirectory(prefix="pd_code_") as tmp:
            zip_path = Path(tmp) / "repo.zip"
            if kind == "github":
                zip_url = fetch.codeload_url(repo["owner"], repo["name"], repo["commit_sha"])
            else:
                zip_url = url
            ctx.progress(0.1, "下载代码压缩包")
            ctx.log(f"下载 {zip_url}")
            size = fetch.download_zip(client, zip_url, zip_path)
            ctx.log(f"下载完成：{size // 1024} KB")
            ctx.check_cancelled()
            ctx.progress(0.4, "解压并分析")
            from ..util import utcnow
            service.set_status(app, repo["id"], "downloading", downloaded_at=utcnow())
            overview = service.ingest_zip(app, service.get_repo(app, repo["id"]), zip_path, ctx.log)
        ctx.progress(1.0, "完成")
        return {"repo_id": repo["id"], "overview": overview}
    except Exception as exc:
        msg = exc.message if isinstance(exc, ApiError) else f"{type(exc).__name__}: {exc}"
        service.set_status(app, repo["id"], "failed", msg[:400])
        raise


def handle_code_import(ctx: JobContext) -> dict:
    """导入本地 zip 或文件夹。payload: {paper_id, repo_id, path, kind: zip|folder}"""
    p = ctx.payload
    app = ctx.app
    repo = service.get_repo(app, p["repo_id"])
    src = Path(p["path"])
    try:
        service.set_status(app, repo["id"], "scanning")
        if p["kind"] == "zip":
            if not src.is_file():
                raise ApiError("bad_request", "找不到该 zip 文件")
            overview = service.ingest_zip(app, repo, src, ctx.log)
        else:
            if not src.is_dir():
                raise ApiError("bad_request", "找不到该文件夹")
            overview = service.ingest_folder(app, repo, src, ctx.log)
        ctx.progress(1.0, "完成")
        return {"repo_id": repo["id"], "overview": overview}
    except Exception as exc:
        msg = exc.message if isinstance(exc, ApiError) else f"{type(exc).__name__}: {exc}"
        service.set_status(app, repo["id"], "failed", msg[:400])
        raise


def handle_code_reanalyze(ctx: JobContext) -> dict:
    ctx.progress(0.1, "重新分析本地代码")
    overview = service.reanalyze(ctx.app, ctx.payload["repo_id"], ctx.log)
    ctx.progress(1.0, "完成")
    return {"repo_id": ctx.payload["repo_id"], "overview": overview}


def _run_async(coro_factory, ctx: JobContext):
    import asyncio

    from ..ai.providers import LLMError
    from ..jobs import JobCancelled

    try:
        return ctx.run_async(coro_factory())
    except (ApiError, JobCancelled):
        raise
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err


def handle_code_associate(ctx: JobContext) -> dict:
    from . import associate

    ctx.progress(0.05, "提取论文方法模块")
    return _run_async(lambda: associate.associate(ctx.app, ctx.payload["repo_id"], ctx, use_ai=bool(ctx.payload.get("ai", True))), ctx)


def handle_code_consistency(ctx: JobContext) -> dict:
    from . import consistency

    ctx.progress(0.1, "抽取论文与代码的参数")
    report = _run_async(lambda: consistency.run_check(ctx.app, ctx.payload["repo_id"], ctx), ctx)
    return {"summary": report["summary"], "ai_used": report["ai_used"]}


def register_ai(manager: JobManager) -> None:
    manager.register("code_associate", handle_code_associate)
    manager.register("code_consistency", handle_code_consistency)
