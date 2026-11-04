"""参考文献：列表、正文引用标记、联网查题录与摘要、AI 概括（契约见 docs/API-references.md）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..references import resolve as resolver
from ..references import store, summarize
from .ai import sse_response
from .deps import get_ctx

router = APIRouter(tags=["references"])


@router.get("/papers/{paper_id}/references")
def list_references(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """解析出的参考文献：{style: numeric|author-year|unknown, count, items:[{index, ord, page, raw, authors, title, year, venue, doi, arxiv, url, merged, messy}]}。"""
    return store.load_references(ctx, paper_id)


@router.get("/papers/{paper_id}/citations")
def list_citations(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """正文里能映射到参考文献的引用标记：{style, ref_count, items:[{ord, markers:[{kind, text, start, end, refs}]}]}。"""
    return store.load_citations(ctx, paper_id)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    force: bool = False


@router.post("/papers/{paper_id}/references/{index}/resolve")
async def resolve_reference(paper_id: str, index: int, body: ResolveBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """联网查这条参考文献的题录与摘要（只发题录信息，不发论文正文）；结果缓存，force=true 重查。"""
    return await resolver.resolve(ctx, paper_id, index, force=bool(body and body.force))


@router.get("/papers/{paper_id}/references/{index}/contexts")
def reference_contexts(paper_id: str, index: int, ctx: AppContext = Depends(get_ctx)):
    """本文里引用这条参考文献的句子：{items:[{ord, page, sentence}]}。"""
    store.get_reference(ctx, paper_id, index)
    return {"items": store.citing_contexts(ctx, paper_id, index, limit=8)}


class SummarizeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    index: int


@router.post("/ai/summarize-reference")
async def summarize_reference(body: SummarizeBody, ctx: AppContext = Depends(get_ctx)):
    """AI 概括这条参考文献做了什么（SSE：meta → delta… → done；没有摘要时直接 done 且 summary=null）。"""
    return sse_response(summarize.summarize_events(ctx, body.paper_id, body.index))
