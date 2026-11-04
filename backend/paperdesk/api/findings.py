"""主动研究提示：扫描（后台任务）、列表、标记、删除。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..insights import findings
from ..jobs import JobManager
from .deps import get_ctx

router = APIRouter(tags=["findings"])


def register_jobs(manager: JobManager) -> None:
    findings.register(manager)


class FindingPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_state: str | None = None  # new | read | ignored | useful
    verified: bool | None = None  # 用户核对原文后标记“已核查”；false 回到后端给出的状态


@router.get("/papers/{paper_id}/findings")
def list_findings(paper_id: str, include_ignored: bool = False, ctx: AppContext = Depends(get_ctx)):
    findings.reconcile(ctx, paper_id)
    return findings.list_findings(ctx, paper_id, include_ignored)


@router.post("/papers/{paper_id}/findings")
def scan_findings(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """开始扫描（后台任务，会调用云端模型）。已有扫描在进行时返回同一个任务。"""
    return {"job_id": findings.enqueue_scan(ctx, paper_id)}


@router.patch("/papers/{paper_id}/findings/{finding_id}")
def patch_finding(paper_id: str, finding_id: str, body: FindingPatch, ctx: AppContext = Depends(get_ctx)):
    return findings.patch_finding(ctx, paper_id, finding_id, user_state=body.user_state, verified=body.verified)


@router.delete("/papers/{paper_id}/findings/{finding_id}")
def delete_finding(paper_id: str, finding_id: str, ctx: AppContext = Depends(get_ctx)):
    findings.delete_finding(ctx, paper_id, finding_id)
    return {"ok": True}


@router.delete("/papers/{paper_id}/findings")
def clear_findings(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"removed": findings.clear_findings(ctx, paper_id)}
