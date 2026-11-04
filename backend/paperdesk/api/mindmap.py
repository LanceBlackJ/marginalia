"""AI 思维导图：三种视角的生成、读取、编辑、搜索、导出，以及节点解释（SSE）。"""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..mindmap import explain, generate, store
from ..mindmap import tree as T
from ..jobs import JobManager
from ..util import ApiError
from .ai import sse_response
from .deps import get_ctx

router = APIRouter(tags=["mindmap"])

ViewName = Literal["logic", "method", "experiment"]


def register_jobs(manager: JobManager) -> None:
    generate.register(manager)


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    view: Literal["logic", "method", "experiment", "all"] = "logic"
    branch_id: str | None = None


class PositionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    x: float
    y: float


class PositionsBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    view: ViewName
    items: list[PositionItem]


class CollapseBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    view: ViewName
    collapsed: bool
    min_depth: int = 2


class NodePatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = None
    summary: str | None = None
    kind: str | None = None
    collapsed: bool | None = None
    x: float | None = None
    y: float | None = None
    parent_id: str | None = None
    index: int | None = None


class NewNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    summary: str = ""
    kind: str = "topic"
    index: int | None = None


class ExplainBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str | None = None


@router.get("/papers/{paper_id}/mindmap/overview")
def mindmap_overview(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    for v in T.VIEWS:
        generate.reconcile(ctx, paper_id, v)
    return {"views": store.overview(ctx, paper_id), "kinds": T.KIND_LABELS}


@router.get("/papers/{paper_id}/mindmap")
def get_mindmap(paper_id: str, view: ViewName = "logic", ctx: AppContext = Depends(get_ctx)):
    generate.reconcile(ctx, paper_id, view)
    return store.get_tree(ctx, paper_id, view)


@router.post("/papers/{paper_id}/mindmap/generate")
def generate_mindmap(paper_id: str, body: GenerateBody, ctx: AppContext = Depends(get_ctx)):
    if body.view == "all":
        if body.branch_id:
            raise ApiError("bad_request", "重新生成单个分支时必须指定具体的视角")
        jobs = {v: generate.enqueue(ctx, paper_id, v) for v in T.VIEWS}
        return {"job_id": next(iter(jobs.values())), "jobs": jobs}
    job_id = generate.enqueue(ctx, paper_id, body.view, body.branch_id)
    return {"job_id": job_id, "jobs": {body.view: job_id}}


@router.get("/papers/{paper_id}/mindmap/search")
def search_mindmap(paper_id: str, q: str = Query(..., min_length=1), view: ViewName | None = None, ctx: AppContext = Depends(get_ctx)):
    return {"items": store.search(ctx, paper_id, q, view)}


@router.get("/papers/{paper_id}/mindmap/export")
def export_mindmap(paper_id: str, view: ViewName = "logic", format: Literal["markdown", "json"] = "markdown",
                   ctx: AppContext = Depends(get_ctx)):
    content, mime, filename = store.export_tree(ctx, paper_id, view, format)
    return Response(content=content.encode("utf-8"), media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.put("/papers/{paper_id}/mindmap/positions")
def put_positions(paper_id: str, body: PositionsBody, ctx: AppContext = Depends(get_ctx)):
    return {"updated": store.save_positions(ctx, paper_id, body.view, [i.model_dump() for i in body.items])}


@router.delete("/papers/{paper_id}/mindmap/positions")
def reset_positions(paper_id: str, view: ViewName, ctx: AppContext = Depends(get_ctx)):
    return {"updated": store.reset_positions(ctx, paper_id, view)}


@router.post("/papers/{paper_id}/mindmap/collapse")
def collapse_all(paper_id: str, body: CollapseBody, ctx: AppContext = Depends(get_ctx)):
    return {"updated": store.set_collapsed(ctx, paper_id, body.view, body.collapsed, max(1, body.min_depth))}


@router.get("/mindmap/nodes/{node_id}")
def get_node(node_id: str, ctx: AppContext = Depends(get_ctx)):
    return store.get_node_detail(ctx, node_id)


@router.patch("/mindmap/nodes/{node_id}")
def patch_node(node_id: str, body: NodePatch, ctx: AppContext = Depends(get_ctx)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError("bad_request", "没有要修改的内容")
    return store.patch_node(ctx, node_id, changes)


@router.post("/mindmap/nodes/{node_id}/children")
def add_child(node_id: str, body: NewNode, ctx: AppContext = Depends(get_ctx)):
    return store.add_node(ctx, parent_id=node_id, title=body.title, summary=body.summary, kind=body.kind, index=body.index)


@router.post("/mindmap/nodes/{node_id}/siblings")
def add_sibling(node_id: str, body: NewNode, ctx: AppContext = Depends(get_ctx)):
    return store.add_sibling(ctx, node_id, title=body.title, summary=body.summary, kind=body.kind)


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    snapshot: dict


@router.post("/mindmap/nodes/restore")
def restore_nodes(body: RestoreBody, ctx: AppContext = Depends(get_ctx)):
    """恢复刚删掉的节点子树（撤销删除 / 重做新增）：用删除接口返回的 snapshot，原样恢复 id、内容与顺序。"""
    return store.restore_nodes(ctx, body.snapshot)


@router.delete("/mindmap/nodes/{node_id}")
def delete_node(node_id: str, ctx: AppContext = Depends(get_ctx)):
    return store.delete_node(ctx, node_id)


@router.post("/mindmap/nodes/{node_id}/explain")
async def explain_node(node_id: str, body: ExplainBody | None = None, ctx: AppContext = Depends(get_ctx)):
    return sse_response(explain.explain_events(ctx, node_id, body.question if body else None))
