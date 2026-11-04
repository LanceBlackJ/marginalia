"""补充材料的后台任务：添加本地文件、下载（含大文件审批）、自动寻找、AI 分类、数据集解释、zip 安全解压。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..context import AppContext
from ..jobs import JobContext, JobManager
from ..util import ApiError
from . import analyze, discover as discover_mod, store
from . import files as F
from .process import process_file


def register(manager: JobManager) -> None:
    manager.register("supplement_add", handle_add)
    manager.register("supplement_download", handle_download)
    manager.register("supplement_discover", handle_discover)
    manager.register("supplement_analyze", handle_analyze)
    manager.register("supplement_explain", handle_explain)
    manager.register("supplement_extract", handle_extract)


def _fail(app: AppContext, asset_id: str, message: str) -> None:
    store.update_asset(app, asset_id, download_status="failed", download_error=message)


# ---- 本地文件 ----
def handle_add(ctx: JobContext) -> dict:
    items = ctx.payload.get("items") or []
    results = []
    for i, it in enumerate(items):
        ctx.check_cancelled()
        path = Path(it["path"])
        ctx.progress(i / max(1, len(items)), f"处理 {path.name}（{i + 1}/{len(items)}）")
        store.update_asset(ctx.app, it["asset_id"], job_id=ctx.job_id)
        try:
            process_file(ctx.app, it["asset_id"], path, log=ctx.log)
            results.append({"asset_id": it["asset_id"], "status": "done", "message": None})
        except ApiError as exc:
            ctx.log(f"{path.name} 失败：{exc.message}")
            _fail(ctx.app, it["asset_id"], exc.message)
            results.append({"asset_id": it["asset_id"], "status": "failed", "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"{path.name} 出错：{type(exc).__name__}: {exc}")
            _fail(ctx.app, it["asset_id"], f"处理出错：{type(exc).__name__}")
            results.append({"asset_id": it["asset_id"], "status": "failed", "message": str(exc)[:200]})
    return {"items": results}


# ---- 下载（URL / 自动寻找确认后的候选）----
def _defer_for_approval(ctx: JobContext, asset: dict, size: int | None) -> dict:
    """文件超过自动下载上限（或需要确认的类别）：新建一个“等待批准”任务，当前任务结束。"""
    payload = {**ctx.payload, "needs_approval": True, "approved": False}
    job_id = ctx.app.jobs.submit_waiting(
        "supplement_download", f"批准下载：{asset['filename']}", payload, asset["paper_id"],
        message=f"等待批准：{asset['filename']}（{_fmt_size(size)}）",
    )
    store.update_asset(ctx.app, asset["id"], download_status="waiting_approval", job_id=job_id, size=size, download_error=None)
    ctx.log(f"文件较大（{_fmt_size(size)}），已转为等待你批准")
    return {"deferred": True, "approval_job_id": job_id}


def _fmt_size(size: int | None) -> str:
    if not size:
        return "大小未知"
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def handle_download(ctx: JobContext) -> dict:
    asset_id, url = ctx.payload["asset_id"], ctx.payload["url"]
    asset = store.get_asset(ctx.app, asset_id)
    approved = bool(ctx.payload.get("approved"))
    if ctx.payload.get("needs_approval") and not approved:
        raise ApiError("conflict", "这个下载需要先得到你的批准", 409)
    store.update_asset(ctx.app, asset_id, download_status="downloading", download_error=None, job_id=ctx.job_id)
    limit = F.MAX_FILE_BYTES if approved else F.AUTO_DOWNLOAD_LIMIT
    with ctx.app.http(timeout=120) as client, tempfile.TemporaryDirectory(prefix="pd_supp_") as tmp:
        size = asset["size"] or F.probe_size(client, url)
        if not approved and (size or 0) > F.AUTO_DOWNLOAD_LIMIT:
            return _defer_for_approval(ctx, asset, size)
        dest = Path(tmp) / F.sanitize_filename(asset["filename"])
        ctx.log(f"下载 {url}")
        try:
            F.download_file(
                client, url, dest, max_bytes=limit,
                on_progress=lambda done, total: ctx.progress((done / total * 0.8) if total else None, f"已下载 {_fmt_size(done)}"),
            )
        except F.TooLarge as exc:
            if not approved:  # 事先不知道大小，下载时才发现超过自动上限：转为等待批准
                return _defer_for_approval(ctx, asset, exc.size)
            _fail(ctx.app, asset_id, str(exc))
            raise ApiError("bad_request", str(exc)) from exc
        except F.DownloadError as exc:
            _fail(ctx.app, asset_id, str(exc))
            raise ApiError("network", str(exc)) from exc
        ctx.check_cancelled()
        ctx.progress(0.85, "解析与分析")
        try:
            process_file(ctx.app, asset_id, dest, source_url=url, log=ctx.log)
        except ApiError as exc:
            _fail(ctx.app, asset_id, exc.message)
            raise
    return {"asset_id": asset_id}


# ---- 自动寻找 ----
def handle_discover(ctx: JobContext) -> dict:
    paper_id = ctx.payload["paper_id"]
    with ctx.app.db.session() as conn:
        p = conn.execute("SELECT title, doi, arxiv_id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        rows = conn.execute(
            "SELECT text FROM passages WHERE paper_id = ? AND (lower(text) LIKE '%github%' OR lower(text) LIKE '%code availab%' "
            "OR lower(text) LIKE '%supplementary%') ORDER BY ord LIMIT 60", (paper_id,),
        ).fetchall()
    body = "\n".join(r["text"] for r in rows)
    ctx.progress(0.2, "查找出版商页面、Crossref、arXiv 与代码仓库")
    with ctx.app.http(timeout=15) as client:
        result = discover_mod.discover(client, doi=p["doi"], arxiv_id=p["arxiv_id"], body_text=body)
    linked = store.list_assets(ctx.app, paper_id)
    urls = {a["source_url"] for a in linked if a["source_url"]}
    for c in result["candidates"]:
        c["already_linked"] = c["url"] in urls
    ctx.log(f"找到 {len(result['candidates'])} 个候选；提示 {len(result['notes'])} 条")
    return result


# ---- AI ----
def handle_analyze(ctx: JobContext) -> dict:
    ctx.progress(0.2, "AI 分类与概述")
    asset = ctx.run_async(analyze.ai_classify(ctx.app, ctx.payload["asset_id"], ctx.log))
    return {"asset": asset}


def handle_explain(ctx: JobContext) -> dict:
    ctx.progress(0.2, "AI 解释数据集")
    out = ctx.run_async(analyze.ai_explain_dataset(ctx.app, ctx.payload["asset_id"], ctx.log))
    return {"model": out["model"], "verification": out["verification"]["status"]}


# ---- zip 安全解压（仅在用户明确要求时）----
def handle_extract(ctx: JobContext) -> dict:
    asset_id = ctx.payload["asset_id"]
    with ctx.app.db.session() as conn:
        row = store.get_row(conn, asset_id)
    src = store.file_path(ctx.app, row)
    if src is None or not src.is_file() or F.ext_of(row["filename"]) != ".zip":
        raise ApiError("bad_request", "只能解压已下载的 zip 文件")
    dest = src.parent / f"{asset_id}_extracted"
    ctx.progress(0.3, "检查并解压（不会执行任何文件）")
    try:
        written = F.safe_extract_zip(src, dest)
    except F.UnsafeArchive as exc:
        raise ApiError("bad_request", f"拒绝解压：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError("bad_request", f"解压失败：{type(exc).__name__}") from exc
    rel = dest.relative_to(ctx.app.paths.data_dir).as_posix()
    store.set_analysis_part(ctx.app, asset_id, extracted={"dir": rel, "files": len(written)})
    ctx.log(f"已解压 {len(written)} 个文件到 {rel}")
    return {"files": len(written), "dir": rel}
