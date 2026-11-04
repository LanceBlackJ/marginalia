from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import __version__
from ..ai import usage
from ..context import OPENALEX_KEY, S2_KEY, AppContext
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(tags=["system"])


def _dir_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@router.get("/system/info")
def system_info(ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        count = conn.execute("SELECT COUNT(*) FROM papers WHERE COALESCE(role, 'paper') = 'paper'").fetchone()[0]
    return {
        "version": __version__,
        "data_dir": str(ctx.paths.data_dir),
        "db_path": str(ctx.paths.db_path),
        "papers_dir": str(ctx.paths.papers_dir),
        "paper_count": count,
        "disk_usage_bytes": _dir_size(ctx.paths.data_dir),
    }


@router.get("/usage")
def get_usage(month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"), ctx: AppContext = Depends(get_ctx)):
    return usage.month_summary(ctx, month)


@router.get("/privacy/requests")
def privacy_requests(
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1, description="游标：只返回 id 小于它的记录（取更早的一页）"),
    task_type: str | None = Query(None, max_length=64, description="按任务类型筛选"),
    ctx: AppContext = Depends(get_ctx),
):
    """发送记录，新的在前；旧参数 `limit` 不变，新增游标分页，并返回 has_more、total、task_types。"""
    return usage.recent_requests(ctx, limit, before_id, task_type)


# ---- OpenAlex API key（可选）：只保存在后端内存，只发给 api.openalex.org ----
class OpenAlexKeyBody(BaseModel):
    api_key: str


@router.get("/settings/openalex-key")
def openalex_key_status(ctx: AppContext = Depends(get_ctx)):
    return {"has_key": ctx.secrets.has(OPENALEX_KEY)}


@router.put("/settings/openalex-key")
def put_openalex_key(body: OpenAlexKeyBody, ctx: AppContext = Depends(get_ctx)):
    key = body.api_key.strip()
    if not key:
        raise ApiError("bad_request", "OpenAlex API Key 不能为空")
    ctx.secrets.set(OPENALEX_KEY, key)
    return {"has_key": True}


# ---- Semantic Scholar API Key（可选，免费申请）：只保存在后端内存，只发给 api.semanticscholar.org ----
@router.get("/settings/s2-key")
def s2_key_status(ctx: AppContext = Depends(get_ctx)):
    return {"has_key": ctx.secrets.has(S2_KEY)}


@router.put("/settings/s2-key")
def put_s2_key(body: OpenAlexKeyBody, ctx: AppContext = Depends(get_ctx)):
    key = body.api_key.strip()
    if not key or len(key) > 512:
        raise ApiError("bad_request", "Semantic Scholar API Key 格式不正确")
    ctx.secrets.set(S2_KEY, key)
    return {"has_key": True}


@router.delete("/settings/s2-key")
def delete_s2_key(ctx: AppContext = Depends(get_ctx)):
    ctx.secrets.clear(S2_KEY)
    return {"has_key": False}


@router.delete("/settings/openalex-key")
def delete_openalex_key(ctx: AppContext = Depends(get_ctx)):
    ctx.secrets.clear(OPENALEX_KEY)
    return {"has_key": False}
