"""补充材料 API：列表/添加/自动寻找/下载审批/授权/分类/数据集查看与解释。详见 docs/API-materials.md。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..context import AppContext
from ..library import repo
from ..materials import analyze, classify, datasets, grants, store
from ..materials import files as F
from ..materials.pdf_child import convert_existing_to_supplement
from ..materials.process import text_sample_for
from ..util import ApiError, loads
from .deps import get_ctx

router = APIRouter(tags=["supplements"])

BLOCKED_EXT = {".exe", ".dll", ".msi", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".lnk", ".jar", ".apk"}


def _main_paper(ctx: AppContext, paper_id: str) -> dict:
    d = repo.get_detail(ctx, paper_id)
    if d.get("role") == "supplement":
        raise ApiError("bad_request", "补充材料本身不能再添加补充材料，请回到主论文操作")
    return d


def _valid_category(c: str | None) -> str | None:
    if c is None:
        return None
    if c not in store.CATEGORIES:
        raise ApiError("bad_request", f"类别必须是：{'、'.join(store.CATEGORIES)}")
    return c


# ---------------- 列表与授权 ----------------
@router.get("/papers/{paper_id}/supplements")
def list_supplements(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    return {"items": store.list_assets(ctx, paper_id), "authorization": grants.state(ctx, paper_id),
            "categories": store.CATEGORIES}


class AuthBody(BaseModel):
    """授权参数。`granted` 是文档里的写法，`allow` 是早期实现的写法，两者等价；都不给（或为 null）= 重置。
    `ttl_seconds` 可选：这次授权的有效期（60 秒到 7 天），默认 8 小时。未知参数一律报 400，不再被静默忽略。"""

    model_config = ConfigDict(extra="forbid")
    allow: bool | None = None
    granted: bool | None = None
    ttl_seconds: int | None = Field(default=None, ge=60, le=7 * 24 * 3600)


@router.get("/papers/{paper_id}/supplements/authorization")
def get_authorization(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    return grants.state(ctx, paper_id)


@router.post("/papers/{paper_id}/supplements/authorization")
def set_authorization(paper_id: str, body: AuthBody, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    if body.allow is not None and body.granted is not None and body.allow != body.granted:
        raise ApiError("bad_request", "allow 与 granted 的值冲突：它们是同一个意思，请只给一个。")
    decision = body.granted if body.granted is not None else body.allow
    return grants.set_decision(ctx, paper_id, decision, ttl_seconds=body.ttl_seconds)


# ---------------- 添加 ----------------
class FilesBody(BaseModel):
    paths: list[str]
    category: str | None = None


class UrlBody(BaseModel):
    url: str
    category: str | None = None


class RemoteItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str
    filename: str | None = None
    size: int | None = None
    source: str = "auto"
    basis: str | None = None
    category: str | None = None


class LinkRemoteBody(BaseModel):
    items: list[RemoteItem]


class AttachPaperBody(BaseModel):
    paper_id: str


@router.post("/papers/{paper_id}/supplements/files")
def add_local_files(paper_id: str, body: FilesBody, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    cat = _valid_category(body.category)
    paths = [p for p in body.paths if p and p.strip()]
    if not paths:
        raise ApiError("bad_request", "没有选择任何文件")
    if len(paths) > 200:
        raise ApiError("bad_request", "一次最多添加 200 个文件")
    items, asset_ids = [], []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            raise ApiError("bad_request", f"文件不存在或无法访问：{p.name}")
        if p.suffix.lower() in BLOCKED_EXT:
            raise ApiError("bad_request", f"不接受可执行文件：{p.name}。Marginalia 不会运行补充材料里的程序。")
        aid = store.create_asset(
            ctx, paper_id=paper_id, filename=p.name, source="local", size=p.stat().st_size,
            category=cat or "其他", category_source="user" if cat else "rule", match_status="用户确认",
            match_basis="用户手动添加", download_status="pending",
        )
        asset_ids.append(aid)
        items.append({"asset_id": aid, "path": str(p)})
    title = f"添加补充材料：{Path(paths[0]).name}" if len(paths) == 1 else f"添加 {len(paths)} 个补充材料"
    job_id = ctx.jobs.submit("supplement_add", title, {"paper_id": paper_id, "items": items}, paper_id)
    for aid in asset_ids:
        store.link_job(ctx, aid, job_id)  # 任务可能已经开始，不能覆盖它自己写入的 job_id
    return {"job_id": job_id, "asset_ids": asset_ids}


@router.post("/papers/{paper_id}/supplements/url")
def add_by_url(paper_id: str, body: UrlBody, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError("bad_request", "请输入以 http:// 或 https:// 开头的链接")
    return _link_remote(ctx, paper_id, [RemoteItem(url=url, source="url", basis="用户输入的链接", category=body.category)])


@router.post("/papers/{paper_id}/supplements/link-remote")
def link_remote(paper_id: str, body: LinkRemoteBody, ctx: AppContext = Depends(get_ctx)):
    """用户在“自动寻找”候选里确认后调用：这一步才建立关联并开始（或等待批准后）下载。"""
    _main_paper(ctx, paper_id)
    if not body.items:
        raise ApiError("bad_request", "没有选择任何候选")
    return _link_remote(ctx, paper_id, body.items[:40])


def _link_remote(ctx: AppContext, paper_id: str, items: list[RemoteItem]) -> dict:
    created = []
    with ctx.db.session() as conn:
        existing = {r["source_url"] for r in conn.execute(
            "SELECT source_url FROM supplementary_assets WHERE paper_id = ?", (paper_id,)) if r["source_url"]}
    for it in items:
        if not it.url.lower().startswith(("http://", "https://")):
            raise ApiError("bad_request", f"无效链接：{it.url[:80]}")
        if it.url in existing:
            continue
        cat = _valid_category(it.category)
        name = F.sanitize_filename(it.filename or it.url.rsplit("/", 1)[-1].split("?")[0] or "download")
        if Path(name).suffix.lower() in BLOCKED_EXT:
            raise ApiError("bad_request", f"不接受可执行文件：{name}")
        guess = classify.rule_category(name, "")[0]
        category = cat or guess
        # 需要先批准：已知超过 20MB，或是代码压缩包/数据集且大小未知（说明书 §9.4）
        needs_approval = bool((it.size or 0) > F.AUTO_DOWNLOAD_LIMIT or (it.size is None and category in ("代码", "数据集")
                                                                         and F.kind_of(name) == "archive"))
        aid = store.create_asset(
            ctx, paper_id=paper_id, filename=name, source="url" if it.source == "url" else "auto", source_url=it.url,
            size=it.size, category=category, category_source="user" if cat else "rule", match_status="用户确认",
            match_basis=it.basis, download_status="waiting_approval" if needs_approval else "pending",
        )
        payload = {"asset_id": aid, "url": it.url, "needs_approval": needs_approval, "approved": False}
        if needs_approval:
            job_id = ctx.jobs.submit_waiting(
                "supplement_download", f"批准下载：{name}", payload, paper_id,
                message=f"等待批准：{name}（{_size_label(it.size)}）")
        else:
            job_id = ctx.jobs.submit("supplement_download", f"下载补充材料：{name}", payload, paper_id)
        store.link_job(ctx, aid, job_id)
        created.append(aid)
    return {"items": [store.get_asset(ctx, a) for a in created], "skipped_existing": len(items) - len(created)}


def _size_label(size: int | None) -> str:
    if not size:
        return "大小未知"
    return f"{size / 1024 / 1024:.1f} MB"


@router.post("/papers/{paper_id}/supplements/attach-paper")
def attach_existing_paper(paper_id: str, body: AttachPaperBody, ctx: AppContext = Depends(get_ctx)):
    """把文献库里已有的独立论文（例如之前单独导入的补充材料 PDF）转为这篇论文的补充材料。"""
    _main_paper(ctx, paper_id)
    child_id = convert_existing_to_supplement(ctx, paper_id, body.paper_id)
    with ctx.db.session() as conn:
        c = conn.execute("SELECT title, file_hash FROM papers WHERE id = ?", (child_id,)).fetchone()
    name = F.sanitize_filename(c["title"] or "supplement") + ".pdf"
    pdf = ctx.paths.paper_dir(child_id) / "original.pdf"
    aid = store.create_asset(ctx, paper_id=paper_id, filename=name, source="local", size=pdf.stat().st_size if pdf.exists() else None,
                             match_status="用户确认", match_basis="从文献库转入", download_status="done")
    store.update_asset(ctx, aid, child_paper_id=child_id, rel_path=f"papers/{child_id}/original.pdf",
                       analysis_status="ready", sha256=c["file_hash"])
    cat, why = classify.rule_category(name, text_sample_for(ctx, aid))
    store.update_asset(ctx, aid, category=cat, summary=f"规则分类：{why}")
    return store.get_asset(ctx, aid)


@router.post("/papers/{paper_id}/supplements/discover")
def discover_supplements(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    d = _main_paper(ctx, paper_id)
    job_id = ctx.jobs.submit("supplement_discover", f"寻找补充材料：{d['title']}", {"paper_id": paper_id}, paper_id)
    return {"job_id": job_id}


class RecommendBody(BaseModel):
    question: str = ""


@router.post("/papers/{paper_id}/supplements/recommend")
async def recommend(paper_id: str, body: RecommendBody, ctx: AppContext = Depends(get_ctx)):
    _main_paper(ctx, paper_id)
    return await analyze.ai_recommend(ctx, paper_id, body.question)


# ---------------- 单个补充材料 ----------------
class AssetPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str | None = None
    filename: str | None = None
    summary: str | None = None
    match_status: str | None = None


@router.patch("/supplements/{asset_id}")
def patch_asset(asset_id: str, body: AssetPatch, ctx: AppContext = Depends(get_ctx)):
    fields: dict = {}
    if body.category is not None:
        fields.update(category=_valid_category(body.category), category_source="user")
    if body.filename is not None:
        fields["filename"] = F.sanitize_filename(body.filename)
    if body.summary is not None:
        fields["summary"] = body.summary.strip()[:300]
    if body.match_status is not None:
        if body.match_status not in ("用户确认", "待确认"):
            raise ApiError("bad_request", "match_status 只能是“用户确认”或“待确认”")
        fields["match_status"] = body.match_status
    store.get_asset(ctx, asset_id)
    store.update_asset(ctx, asset_id, **fields)
    return store.get_asset(ctx, asset_id)


@router.delete("/supplements/{asset_id}")
def delete_asset(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    store.get_asset(ctx, asset_id)
    store.delete_asset(ctx, asset_id)
    return {"ok": True}


@router.post("/supplements/{asset_id}/approve")
def approve_download(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    if a["download_status"] != "waiting_approval" or not a["job_id"]:
        raise ApiError("conflict", "这个补充材料当前没有等待批准的下载", 409)
    # 先改状态再放行任务：任务可能很快跑完，不能让后写入的 pending 覆盖掉它的终态
    store.update_asset(ctx, asset_id, download_status="pending")
    try:
        ctx.jobs.approve(a["job_id"])
    except Exception:
        store.update_asset(ctx, asset_id, download_status="waiting_approval")
        raise
    return store.get_asset(ctx, asset_id)


@router.post("/supplements/{asset_id}/reject")
def reject_download(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    if a["download_status"] != "waiting_approval" or not a["job_id"]:
        raise ApiError("conflict", "这个补充材料当前没有等待批准的下载", 409)
    ctx.jobs.reject(a["job_id"], "你拒绝了这次下载")
    store.update_asset(ctx, asset_id, download_status="rejected", download_error="你拒绝了这次下载")
    return store.get_asset(ctx, asset_id)


@router.post("/supplements/{asset_id}/retry")
def retry_download(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    """失败 / 被拒绝的材料重新处理。重试被拒绝的大文件会再次进入“等待批准”，不会绕过审批。"""
    a = store.get_asset(ctx, asset_id)
    if a["download_status"] not in ("failed", "rejected") or not a["job_id"]:
        raise ApiError("conflict", "只有失败或被拒绝的材料可以重试", 409)
    if a["download_status"] == "rejected":
        # 被拒绝的大文件：重新放回“等待批准”，仍需要你点批准才会下载
        with ctx.db.session() as conn:
            row = conn.execute("SELECT payload_json, title FROM jobs WHERE id = ?", (a["job_id"],)).fetchone()
        if row is None:
            raise ApiError("conflict", "原来的任务已不存在，请删除后重新添加", 409)
        payload = {**loads(row["payload_json"], {}), "needs_approval": True, "approved": False}
        new_job = ctx.jobs.submit_waiting("supplement_download", row["title"], payload, a["paper_id"],
                                          message=f"等待批准：{a['filename']}")
        store.update_asset(ctx, asset_id, download_status="waiting_approval", download_error=None, job_id=new_job)
    else:
        # 先改状态再提交任务（同 approve：避免任务先跑完、状态被后写入的 pending 覆盖）
        store.update_asset(ctx, asset_id, download_status="pending", download_error=None)
        try:
            new_job = ctx.jobs.retry(a["job_id"])
        except Exception:
            store.update_asset(ctx, asset_id, download_status=a["download_status"], download_error=a["download_error"])
            raise
        store.swap_job(ctx, asset_id, a["job_id"], new_job)
    return store.get_asset(ctx, asset_id)


@router.get("/supplements/{asset_id}/file")
def asset_file(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        row = store.get_row(conn, asset_id)
    path = store.file_path(ctx, row)
    if path is None or not path.is_file():
        raise ApiError("not_found", "文件不存在或尚未下载完成", 404)
    inline = F.kind_of(row["filename"]) == "pdf"
    return FileResponse(path, media_type=row["mime"] or "application/octet-stream", filename=row["filename"],
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/supplements/{asset_id}/analysis")
def asset_analysis(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    return {"asset": a, "analysis": store.get_analysis(ctx, asset_id)}


@router.get("/supplements/{asset_id}/table")
def asset_table(asset_id: str, sheet: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        row = store.get_row(conn, asset_id)
    profile = store.get_analysis(ctx, asset_id)
    path = store.file_path(ctx, row)
    if not profile or profile.get("kind") != "table" or path is None or not path.is_file():
        raise ApiError("bad_request", "这个文件不是已分析的表格数据")
    return datasets.read_page(path, profile, sheet, offset, limit)


@router.post("/supplements/{asset_id}/analyze")
def ai_analyze(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    analyze.require_grant(ctx, a["paper_id"])
    if a["download_status"] != "done":
        raise ApiError("conflict", "文件还没有准备好", 409)
    return {"job_id": ctx.jobs.submit("supplement_analyze", f"AI 分析：{a['filename']}", {"asset_id": asset_id}, a["paper_id"])}


@router.post("/supplements/{asset_id}/explain")
def ai_explain(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    analyze.require_grant(ctx, a["paper_id"])
    if a["kind"] != "table" or a["download_status"] != "done":
        raise ApiError("bad_request", "只有已分析的数据集文件可以让 AI 解释")
    return {"job_id": ctx.jobs.submit("supplement_explain", f"解释数据集：{a['filename']}", {"asset_id": asset_id}, a["paper_id"])}


@router.post("/supplements/{asset_id}/extract")
def extract_zip(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    a = store.get_asset(ctx, asset_id)
    if F.ext_of(a["filename"]) != ".zip" or a["download_status"] != "done":
        raise ApiError("bad_request", "只能解压已下载完成的 zip 文件")
    return {"job_id": ctx.jobs.submit("supplement_extract", f"解压：{a['filename']}", {"asset_id": asset_id}, a["paper_id"])}
