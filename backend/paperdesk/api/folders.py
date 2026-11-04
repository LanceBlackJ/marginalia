"""文库的文件夹、批量移动、智能视图与建议归类（契约见 docs/API.md「文件夹与筛选」）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..library import folders
from .deps import get_ctx

router = APIRouter(tags=["folders"])


class FolderCreate(BaseModel):
    name: str
    parent_id: str | None = None


class FolderPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    parent_id: str | None = None  # 只有请求里带了这个字段才会移动（null = 移到最上层）
    sort: int | None = None


class ReorderBody(BaseModel):
    parent_id: str | None = None
    ids: list[str]


class MoveBody(BaseModel):
    paper_ids: list[str]
    folder_id: str | None = None  # null = 移回“未分类”


class ViewBody(BaseModel):
    name: str
    query: dict


class IgnoreBody(BaseModel):
    paper_id: str
    folder_id: str


@router.get("/folders")
def list_folders(ctx: AppContext = Depends(get_ctx)):
    return folders.list_folders(ctx)


@router.post("/folders")
def create_folder(body: FolderCreate, ctx: AppContext = Depends(get_ctx)):
    return folders.create_folder(ctx, body.name, body.parent_id)


@router.post("/folders/reorder")
def reorder(body: ReorderBody, ctx: AppContext = Depends(get_ctx)):
    folders.reorder(ctx, body.parent_id, body.ids)
    return {"ok": True}


@router.patch("/folders/{fid}")
def patch_folder(fid: str, body: FolderPatch, ctx: AppContext = Depends(get_ctx)):
    sent = body.model_fields_set
    return folders.update_folder(ctx, fid, name=body.name, parent_id=body.parent_id, move="parent_id" in sent, sort=body.sort)


@router.delete("/folders/{fid}")
def delete_folder(fid: str, ctx: AppContext = Depends(get_ctx)):
    return folders.delete_folder(ctx, fid)


@router.post("/papers/move")
def move_papers(body: MoveBody, ctx: AppContext = Depends(get_ctx)):
    """把一批论文移进某个文件夹（或移回未分类）。"""
    return folders.move_papers(ctx, body.paper_ids, body.folder_id)


@router.get("/folders-suggestions")
def suggestions(limit: int = 30, ctx: AppContext = Depends(get_ctx)):
    """给未分类论文的归类建议：{method: embedding|keywords|none, items:[{paper_id, folder_id, score, confidence, basis}]}。"""
    return folders.suggestions(ctx, max(1, min(limit, 100)))


@router.post("/folders-suggestions/ignore")
def ignore(body: IgnoreBody, ctx: AppContext = Depends(get_ctx)):
    folders.ignore_suggestion(ctx, body.paper_id, body.folder_id)
    return {"ok": True}


@router.get("/smart-views")
def list_views(ctx: AppContext = Depends(get_ctx)):
    return {"items": folders.list_views(ctx)}


@router.post("/smart-views")
def create_view(body: ViewBody, ctx: AppContext = Depends(get_ctx)):
    return folders.create_view(ctx, body.name, body.query)


@router.delete("/smart-views/{vid}")
def delete_view(vid: str, ctx: AppContext = Depends(get_ctx)):
    folders.delete_view(ctx, vid)
    return {"ok": True}
