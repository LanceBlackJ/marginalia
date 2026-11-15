"""综述空间 API。综述论文是独立的逻辑集合，不混入普通文献库。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..library import reviews
from .deps import get_ctx

router = APIRouter(prefix="/reviews", tags=["reviews"])


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    description: str | None = None


class CategoryBody(BaseModel):
    name: str


class AddPapersBody(BaseModel):
    paper_ids: list[str]
    category_id: str | None = None


class ReviewPaperPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category_id: str | None = None
    screening: str | None = None
    exclusion_reason: str | None = None


@router.get("")
def list_reviews(ctx: AppContext = Depends(get_ctx)):
    return reviews.list_projects(ctx)


@router.post("")
def create_review(body: ProjectBody, ctx: AppContext = Depends(get_ctx)):
    return reviews.create_project(ctx, body.name or "", body.description or "")


@router.patch("/{review_id}")
def patch_review(review_id: str, body: ProjectBody, ctx: AppContext = Depends(get_ctx)):
    return reviews.update_project(ctx, review_id, name=body.name, description=body.description)


@router.delete("/{review_id}")
def delete_review(review_id: str, ctx: AppContext = Depends(get_ctx)):
    reviews.delete_project(ctx, review_id)
    return {"ok": True}


@router.get("/{review_id}/categories")
def list_categories(review_id: str, ctx: AppContext = Depends(get_ctx)):
    return reviews.list_categories(ctx, review_id)


@router.post("/{review_id}/categories")
def create_category(review_id: str, body: CategoryBody, ctx: AppContext = Depends(get_ctx)):
    return reviews.create_category(ctx, review_id, body.name)


@router.post("/{review_id}/papers")
def add_papers(review_id: str, body: AddPapersBody, ctx: AppContext = Depends(get_ctx)):
    return reviews.add_papers(ctx, review_id, body.paper_ids, body.category_id)


@router.patch("/{review_id}/papers/{paper_id}")
def patch_review_paper(review_id: str, paper_id: str, body: ReviewPaperPatch, ctx: AppContext = Depends(get_ctx)):
    return reviews.update_paper(
        ctx, review_id, paper_id, category_id=body.category_id, screening=body.screening, exclusion_reason=body.exclusion_reason
    )


@router.delete("/{review_id}/papers/{paper_id}")
def delete_review_paper(review_id: str, paper_id: str, ctx: AppContext = Depends(get_ctx)):
    reviews.remove_paper(ctx, review_id, paper_id)
    return {"ok": True}
