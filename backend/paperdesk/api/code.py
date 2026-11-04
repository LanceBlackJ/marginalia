"""代码理解的 HTTP 接口（契约见 docs/API-code.md）。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from ..code import service
from ..code import jobs as code_jobs
from ..code.fetch import parse_github_url, resolve_github
from ..code.flow import build_flow
from ..code.links import classify, find_code_links
from ..context import AppContext
from ..jobs import JobManager
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(tags=["code"])


def register_jobs(manager: JobManager) -> None:
    code_jobs.register(manager)


class UrlBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str
    branch: str | None = None


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    kind: Literal["zip", "folder", "auto"] = "auto"
    authored_by: Literal["author", "user", "self"] = "user"


def _paper(ctx: AppContext, paper_id: str):
    with ctx.db.session() as conn:
        row = conn.execute("SELECT id, year, title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "论文不存在", 404)
    return row


def _info_dict(info) -> dict:
    return {
        "owner": info.owner, "name": info.name, "url": info.url, "default_branch": info.default_branch, "branch": info.branch,
        "size_bytes": info.size_bytes, "commit_sha": info.commit_sha, "commit_date": info.commit_date, "description": info.description,
        "archived": info.archived, "requires_approval": info.requires_approval, "too_large": info.too_large, "warnings": info.warnings,
    }


@router.get("/papers/{paper_id}/code/sources")
def code_sources(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """论文全文里声明的代码/数据链接（含出处页码）。论文没有声明时返回空列表，绝不虚构。"""
    return {"items": find_code_links(ctx, paper_id)}


@router.post("/papers/{paper_id}/code/resolve")
def code_resolve(paper_id: str, body: UrlBody, ctx: AppContext = Depends(get_ctx)):
    paper = _paper(ctx, paper_id)
    ref = parse_github_url(body.url)
    if ref is None:
        kind, _ = classify(body.url)
        raise ApiError("unsupported", "暂时只支持获取 GitHub 仓库。其它来源请手动下载压缩包，再用“导入本地 zip/文件夹”。")
    if body.branch:
        ref.branch = body.branch
    with ctx.http(timeout=20) as client:
        info = resolve_github(client, ref, paper_year=paper["year"])
    return _info_dict(info)


@router.post("/papers/{paper_id}/code/fetch")
def code_fetch(paper_id: str, body: UrlBody, ctx: AppContext = Depends(get_ctx)):
    paper = _paper(ctx, paper_id)
    url = body.url.strip()
    ref = parse_github_url(url)
    kind, _supported = classify(url)
    if ref is None and not url.lower().split("?")[0].endswith(".zip"):
        raise ApiError("unsupported", "暂时只支持直接获取 GitHub 仓库或 zip 直链。其它来源请手动下载后用“导入本地 zip/文件夹”。")
    declared = {l["url"].lower().rstrip("/") for l in find_code_links(ctx, paper_id)}
    authored_by = "author" if url.lower().rstrip("/") in declared else "user"
    if ref is not None:
        if body.branch:
            ref.branch = body.branch
        with ctx.http(timeout=20) as client:
            info = resolve_github(client, ref, paper_year=paper["year"])
        if info.too_large:
            raise ApiError("too_large", f"该仓库约 {info.size_bytes // 1024 // 1024} MB，超过 300 MB 的上限，不会下载。请只下载需要的部分后用本地导入。")
        repo_id = service.create_repo(
            ctx, paper_id, origin="github", authored_by=authored_by, url=info.url, owner=info.owner, name=info.name, branch=info.branch,
            commit_sha=info.commit_sha, commit_date=info.commit_date, warnings=info.warnings,
        )
        payload = {"paper_id": paper_id, "repo_id": repo_id, "url": info.url, "requires_approval": info.requires_approval}
        title = f"获取代码：{info.owner}/{info.name}"
        if info.requires_approval:
            job_id = ctx.jobs.submit_waiting(
                "code_fetch", title, payload, paper_id,
                message=f"仓库约 {info.size_bytes // 1024 // 1024} MB，需要你确认后才下载",
            )
        else:
            job_id = ctx.jobs.submit("code_fetch", title, payload, paper_id)
        return {"repo_id": repo_id, "job_id": job_id, "requires_approval": info.requires_approval, "info": _info_dict(info)}
    repo_id = service.create_repo(ctx, paper_id, origin="zip", authored_by=authored_by, url=url, name=url.rsplit("/", 1)[-1])
    job_id = ctx.jobs.submit("code_fetch", f"获取代码：{url[-50:]}", {"paper_id": paper_id, "repo_id": repo_id, "url": url}, paper_id)
    return {"repo_id": repo_id, "job_id": job_id, "requires_approval": False, "info": None}


@router.post("/papers/{paper_id}/code/import")
def code_import(paper_id: str, body: ImportBody, ctx: AppContext = Depends(get_ctx)):
    _paper(ctx, paper_id)
    src = Path(body.path)
    kind = body.kind
    if kind == "auto":
        kind = "zip" if src.is_file() and src.suffix.lower() == ".zip" else "folder" if src.is_dir() else "zip"
    if kind == "zip" and not src.is_file():
        raise ApiError("bad_request", "找不到该 zip 文件")
    if kind == "folder" and not src.is_dir():
        raise ApiError("bad_request", "找不到该文件夹")
    repo_id = service.create_repo(ctx, paper_id, origin=kind, authored_by=body.authored_by, name=src.stem if kind == "zip" else src.name)
    job_id = ctx.jobs.submit("code_import", f"导入代码：{src.name}", {"paper_id": paper_id, "repo_id": repo_id, "path": str(src), "kind": kind}, paper_id)
    return {"repo_id": repo_id, "job_id": job_id}


@router.get("/papers/{paper_id}/code/repos")
def list_repos(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _paper(ctx, paper_id)
    return {"items": service.list_repos(ctx, paper_id)}


@router.get("/code/repos/{repo_id}")
def get_repo(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    return service.get_repo(ctx, repo_id)


@router.post("/code/repos/{repo_id}/reanalyze")
def reanalyze_repo(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    """不重新下载，用最新的解析器重新分析本地副本。"""
    repo = service.get_repo(ctx, repo_id)
    job_id = ctx.jobs.submit("code_reanalyze", f"重新分析：{repo['name'] or repo_id[:8]}", {"repo_id": repo_id, "paper_id": repo["paper_id"]}, repo["paper_id"])
    return {"job_id": job_id}


@router.delete("/code/repos/{repo_id}")
def delete_repo(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    service.delete_repo(ctx, repo_id)
    return {"ok": True}


@router.get("/code/repos/{repo_id}/tree")
def repo_tree(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return service.file_tree(ctx, repo_id)


@router.get("/code/repos/{repo_id}/file")
def repo_file(repo_id: str, path: str = Query(...), ctx: AppContext = Depends(get_ctx)):
    return service.read_file(ctx, repo_id, path)


@router.get("/code/repos/{repo_id}/symbols")
def repo_symbols(repo_id: str, q: str | None = None, kind: str | None = None, limit: int = Query(100, ge=1, le=500),
                 ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return {"items": service.search_symbols(ctx, repo_id, q, kind, limit)}


@router.get("/code/repos/{repo_id}/symbols/{symbol_id}/graph")
def symbol_graph(repo_id: str, symbol_id: int, ctx: AppContext = Depends(get_ctx)):
    return service.symbol_graph(ctx, repo_id, symbol_id)


@router.get("/code/repos/{repo_id}/flow")
def repo_flow(repo_id: str, symbol_id: int | None = None, max_depth: int = Query(4, ge=1, le=8), ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return build_flow(ctx, repo_id, symbol_id, max_depth=max_depth)


# ---------------- AI：解释 / 命名 / 关联 / 一致性 ----------------
from ..code import annotate, associate, consistency, explain  # noqa: E402
from .ai import sse_response  # noqa: E402


class ExplainBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repo_id: str
    kind: Literal["file", "repo", "module", "function", "class", "algorithm", "dataflow", "variable", "calls", "line_range"] = "function"
    path: str | None = None
    symbol_id: int | None = None
    symbol: str | None = None
    variable: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    question: str = ""


@router.post("/code/explain")
async def code_explain(body: ExplainBody, ctx: AppContext = Depends(get_ctx)):
    """SSE：meta → delta* → (escalate) → done{card} | error。证据为真实 path:行号，后端已核实。"""
    target = {k: v for k, v in body.model_dump().items() if k in ("kind", "path", "symbol_id", "symbol", "variable", "line_start", "line_end") and v is not None}
    return sse_response(explain.CodeExplainer(ctx).explain_events(explain.ExplainRequest(body.repo_id, target, body.question)))


@router.get("/code/repos/{repo_id}/cards")
def code_cards(repo_id: str, path: str | None = None, symbol_id: int | None = None, ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return {"items": explain.list_cards(ctx, repo_id, path, symbol_id)}


@router.delete("/code/cards/{card_id}")
def delete_code_card(card_id: str, ctx: AppContext = Depends(get_ctx)):
    explain.delete_card(ctx, card_id)
    return {"ok": True}


class AnnotateBody(BaseModel):
    symbol_id: int | None = None


@router.post("/code/repos/{repo_id}/flow/annotate")
async def flow_annotate(repo_id: str, body: AnnotateBody, ctx: AppContext = Depends(get_ctx)):
    """让 AI 给流程节点起名并写一句话说明（结构仍来自静态分析，结果标 ai=true）。"""
    return await annotate.annotate_flow(ctx, repo_id, body.symbol_id)


@router.get("/papers/{paper_id}/code/modules")
def paper_modules(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """论文里的方法模块（按章节，确定性提取，不调用 AI）。"""
    _paper(ctx, paper_id)
    return {"items": associate.deterministic_modules(ctx, paper_id)}


class AssociateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ai: bool = True  # False：只做本地词法匹配，不调用模型（首次打开映射页自动生成时用）


@router.post("/code/repos/{repo_id}/associate")
def code_associate(repo_id: str, body: AssociateBody | None = None, ctx: AppContext = Depends(get_ctx)):
    repo = service.get_repo(ctx, repo_id)
    if repo["status"] != "ready":
        raise ApiError("conflict", "代码仓库还没有分析完成。", 409)
    return {"job_id": ctx.jobs.submit("code_associate", "论文方法 ↔ 代码关联", {"repo_id": repo_id, "ai": body.ai if body else True}, repo["paper_id"])}


@router.get("/code/repos/{repo_id}/links")
def code_links(repo_id: str, status: Literal["suggested", "confirmed", "rejected"] | None = None, ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return {"items": associate.list_links(ctx, repo_id, status)}


class LinkPatch(BaseModel):
    status: Literal["suggested", "confirmed", "rejected"]


@router.patch("/code/links/{link_id}")
def patch_link(link_id: str, body: LinkPatch, ctx: AppContext = Depends(get_ctx)):
    return associate.set_link_status(ctx, link_id, body.status)


class ManualLink(BaseModel):
    module_name: str
    symbol_id: int
    passage_ords: list[int] | None = None


@router.post("/code/repos/{repo_id}/links")
def add_link(repo_id: str, body: ManualLink, ctx: AppContext = Depends(get_ctx)):
    return associate.add_manual_link(ctx, repo_id, body.module_name, body.symbol_id, body.passage_ords)


@router.get("/code/symbols/{symbol_id}/paper")
def symbol_paper(symbol_id: int, ctx: AppContext = Depends(get_ctx)):
    """反向：函数 → 论文里的相关段落/已建立的关联。"""
    return associate.paper_for_symbol(ctx, symbol_id)


@router.post("/code/repos/{repo_id}/consistency")
def code_consistency(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    repo = service.get_repo(ctx, repo_id)
    if repo["status"] != "ready":
        raise ApiError("conflict", "代码仓库还没有分析完成。", 409)
    return {"job_id": ctx.jobs.submit("code_consistency", "论文参数 ↔ 代码参数一致性检查", {"repo_id": repo_id}, repo["paper_id"])}


@router.get("/code/repos/{repo_id}/consistency")
def consistency_report(repo_id: str, ctx: AppContext = Depends(get_ctx)):
    service.get_repo(ctx, repo_id)
    return consistency.get_report(ctx, repo_id)
