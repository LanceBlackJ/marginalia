from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..context import AppContext
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(status: str | None = None, type: str | None = None, limit: int = Query(50, ge=1, le=200),
              ctx: AppContext = Depends(get_ctx)):
    return {"items": ctx.jobs.list(status, type, limit)}


@router.delete("")
def clear_finished_jobs(ctx: AppContext = Depends(get_ctx)):
    """清空所有已结束的任务记录；进行中的任务不受影响。"""
    return {"deleted": ctx.jobs.clear_finished()}


@router.delete("/{job_id}")
def delete_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    """删除一条已结束的任务记录；进行中的任务返回 409。"""
    ctx.jobs.delete(job_id)
    return {"ok": True}


@router.get("/{job_id}")
def get_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    job = ctx.jobs.get(job_id, with_log=True)
    if job is None:
        raise ApiError("not_found", "任务不存在", 404)
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.jobs.cancel(job_id)


@router.post("/{job_id}/retry")
def retry_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"job_id": ctx.jobs.retry(job_id)}


@router.post("/{job_id}/approve")
def approve_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    """批准等待批准（waiting_approval）的任务，例如大文件下载。"""
    return ctx.jobs.approve(job_id)


@router.post("/{job_id}/reject")
def reject_job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.jobs.reject(job_id)
