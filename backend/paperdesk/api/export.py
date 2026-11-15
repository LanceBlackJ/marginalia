"""导出 API：引文（BibTeX/RIS/CSL JSON/GB-T 7714/APA）、阅读成果合集（Markdown / Word）。契约见 docs/API-export.md。"""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..context import AppContext
from ..knowledge import bundle
from ..library import citations, reviews
from .deps import get_ctx

router = APIRouter(tags=["export"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/export/formats")
def formats():
    return {"citations": [{"id": k, "label": v[0], "ext": v[1]} for k, v in citations.FORMATS.items()],
            "bundle": [{"id": "md", "label": "Markdown", "ext": "md"}, {"id": "docx", "label": "Word", "ext": "docx"}]}


class CitationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: Literal["bibtex", "ris", "csl", "gbt7714", "apa", "mla", "ieee"]
    paper_ids: list[str] | None = None  # 选中的论文
    tag_id: str | None = None           # 或某个标签下的论文；都不给则导出全库
    review_id: str | None = None        # 或某个综述中的全部论文


@router.post("/export/citations")
def export_citations(body: CitationBody, ctx: AppContext = Depends(get_ctx)):
    if body.review_id and (body.paper_ids or body.tag_id):
        from ..util import ApiError
        raise ApiError("bad_request", "综述导出不能同时指定论文或标签")
    paper_ids = reviews.paper_ids(ctx, body.review_id) if body.review_id else body.paper_ids
    return citations.export_citations(ctx, body.format, paper_ids=paper_ids, tag_id=body.tag_id)


class BundleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: Literal["md", "docx"] = "md"
    paper_ids: list[str] | None = None
    review_id: str | None = None
    notes: bool = True
    annotations: bool = True
    cards: bool = True
    items: bool = True


@router.post("/export/bundle")
def export_bundle(body: BundleBody, ctx: AppContext = Depends(get_ctx)):
    """笔记、批注、AI 卡片（标明 AI 生成）、知识条目的合集。md 返回 JSON {filename, content}；docx 直接返回文件字节。"""
    if body.review_id and body.paper_ids:
        from ..util import ApiError
        raise ApiError("bad_request", "综述导出不能同时指定论文")
    paper_ids = reviews.paper_ids(ctx, body.review_id) if body.review_id else body.paper_ids
    doc = bundle.build(ctx, paper_ids, notes=body.notes, annotations=body.annotations, cards=body.cards, items=body.items)
    if body.format == "docx":
        data = bundle.to_docx(doc)
        name = "Marginalia_阅读成果.docx"
        return Response(content=data, media_type=DOCX_MIME,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "X-Export-Count": str(sum(doc['counts'].values()))})
    return {"filename": "Marginalia_阅读成果.md", "content": bundle.to_markdown(doc), "count": sum(doc["counts"].values()), "counts": doc["counts"]}
