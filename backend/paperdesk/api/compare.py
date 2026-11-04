"""多论文比较 API。契约见 docs/API-discovery.md。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..compare import store
from ..compare.engine import preflight
from ..context import AppContext
from ..jobs import JobManager
from .deps import get_ctx

router = APIRouter(prefix="/compare", tags=["compare"])


def register_jobs(manager: JobManager) -> None:
    store.register_jobs(manager)


class CompareBody(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=8)
    mode: Literal["quick", "method", "lineage"] = "quick"
    title: str | None = None


@router.get("/preflight")
def check(paper_ids: str = Query(..., description="逗号分隔的论文 id"), mode: str = "quick", ctx: AppContext = Depends(get_ctx)):
    return preflight(ctx, [p for p in paper_ids.split(",") if p], mode)


@router.post("")
def create(body: CompareBody, ctx: AppContext = Depends(get_ctx)):
    return store.create_comparison(ctx, body.paper_ids, body.mode, body.title)


@router.get("")
def list_(ctx: AppContext = Depends(get_ctx)):
    return {"items": store.list_comparisons(ctx)}


@router.get("/{cid}")
def get(cid: str, ctx: AppContext = Depends(get_ctx)):
    return store.get_comparison(ctx, cid)


@router.delete("/{cid}")
def delete(cid: str, ctx: AppContext = Depends(get_ctx)):
    store.delete_comparison(ctx, cid)
    return {"ok": True}


@router.post("/{cid}/rerun")
def rerun(cid: str, ctx: AppContext = Depends(get_ctx)):
    row = store.get_comparison(ctx, cid)
    job_id = ctx.jobs.submit("paper_compare", f"{row['mode_label']}（{len(row['paper_ids'])} 篇）", {"comparison_id": cid}, None)
    with ctx.db.session() as conn:
        conn.execute("UPDATE comparisons SET job_id = ?, status = 'pending', error = NULL WHERE id = ?", (job_id, cid))
    return {"id": cid, "job_id": job_id}
