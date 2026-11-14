from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi import Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .. import reader
from ..ai import passage_insight  # noqa: F401  （导入时注册学习分析的缓存表）
from ..ai import glossary, passage_rects as rects_mod
from ..context import AppContext
from ..library import filters, processing, profile, reparse, repo, stats
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(prefix="/papers", tags=["papers"])


class PaperPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    favorite: bool | None = None
    finished: bool | None = None  # 标记 / 取消“已读完”
    title: str | None = None
    alias: str | None = None  # 备注名；空字符串清除
    reading_page: int | None = None
    read_progress: float | None = None
    reading_state: dict | None = None
    touch: bool | None = None


class LocateBody(BaseModel):
    text: str
    page: int | None = None


class AttachBody(BaseModel):
    path: str


@router.get("")
def list_papers(
    q: str | None = None,
    tag: str | None = None,
    view: Literal["all", "recent", "favorite"] = "all",
    sort: Literal["recent", "title", "author", "year", "imported", "relevance", "manual"] = "imported",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    # ---- 多特征筛选（全部可选，彼此 AND；旧参数不变）----
    tags: str | None = Query(None, description="多个标签 id，逗号分隔"),
    tag_mode: Literal["any", "all"] = "any",
    folder: str | None = Query(None, description="文件夹 id；none 表示未分类"),
    subfolders: bool = True,
    note_q: str | None = Query(None, description="在笔记（含笔记引用的原文）里搜索"),
    annotation_q: str | None = Query(None, description="在批注与高亮文字里搜索"),
    text_q: str | None = Query(None, description="在论文正文里搜索"),
    card_q: str | None = Query(None, description="在 AI 研究卡片里搜索"),
    year_from: int | None = None,
    year_to: int | None = None,
    author: str | None = None,
    venue: str | None = None,
    language: str | None = None,
    read: str | None = Query(None, description="unread | reading | done"),
    parse: str | None = Query(None, description="ready | parsing | failed | metadata_only | ocr"),
    has_notes: bool | None = None,
    has_annotations: bool | None = None,
    has_supplement: bool | None = None,
    has_code: bool | None = None,
    favorite: bool | None = None,
    finished: bool | None = None,
    imported_from: str | None = None,
    imported_to: str | None = None,
    ctx: AppContext = Depends(get_ctx),
):
    if view == "recent" and sort == "imported":
        sort = "recent"
    filters_ = {
        "tag_ids": [t for t in (tags or "").split(",") if t.strip()], "tag_mode": tag_mode, "folder": folder, "subfolders": subfolders,
        "note_q": note_q, "annotation_q": annotation_q, "text_q": text_q, "card_q": card_q, "year_from": year_from,
        "year_to": year_to, "author": author, "venue": venue, "language": language, "read": read, "parse": parse,
        "has_notes": has_notes, "has_annotations": has_annotations, "has_supplement": has_supplement, "has_code": has_code,
        "favorite": favorite, "finished": finished, "imported_from": imported_from, "imported_to": imported_to,
    }
    items, total = repo.list_papers(ctx, q=q, view=view, sort=sort, order=order, limit=limit, offset=offset, tag=tag, filters_=filters_)
    processing.attach_summaries(ctx, items)
    return {"items": items, "total": total}


@router.get("/stats")
def library_stats(ctx: AppContext = Depends(get_ctx)):
    """文献库的数量统计：总数、读到哪了、按年份/期刊/文件夹/主题分布、每月导入、笔记和 AI 对话数量。"""
    return stats.library_stats(ctx)


@router.get("/facets")
def paper_facets(ctx: AppContext = Depends(get_ctx)):
    """筛选面板的可选值与计数（年份分布、作者、来源、标签、语言、解析/阅读状态、各类标记）。"""
    with ctx.db.session() as conn:
        return filters.facets(conn)


@router.get("/{paper_id}/processing")
def paper_processing(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """导入后自动处理的进度：{overall: processing|waiting|attention|done, label, steps:{analysis,formulas,tables,index}, reasons}。
    只有 overall=done 才能说“已完成”；公式和表格没处理好之前不会是 done。完整检测表格（比列表里的轻量版本慢一点）。"""
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
    return processing.summary(ctx, paper_id, detailed=True)


@router.post("/{paper_id}/processing")
def start_paper_processing(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """启动还没做的自动步骤（公式识别、表格还原、语义索引），幂等。没配置视觉模型/API 的步骤不启动，状态里写明原因。"""
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
    started = processing.start_auto(ctx, paper_id)
    return {"started": {k: v for k, v in started.items() if v}, "processing": processing.summary(ctx, paper_id, detailed=True)}


class ReparseBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    force: bool = False  # 新解析的段落数少得可疑（<40%）时默认中止；确认要替换时传 true


@router.get("/{paper_id}/reparse")
def reparse_status(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """重新解析的状态：{can_reparse, parser_version, current_version, outdated, active_job_id, backup:{created_at, expires_at, stats}|null}。
    backup 不为空表示可以撤销最近一次重新解析（保留 7 天或到下次重新解析）。"""
    return reparse.status(ctx, paper_id)


@router.post("/{paper_id}/reparse")
def start_reparse(paper_id: str, body: ReparseBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """用当前版本的解析器重新解析这篇论文（及其补充材料）：后台任务，可取消；失败或取消时旧数据完全不变。
    笔记、批注、书签、卡片、思维导图等用户数据会重新映射到新段落，映射不上的保留原文引用并标为“位置已失效”。"""
    return {"job_id": reparse.enqueue(ctx, paper_id, force=bool(body and body.force))}


@router.post("/{paper_id}/reparse/undo")
def undo_reparse(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """撤销最近一次重新解析：旧的解析数据与缓存原样恢复，用户数据恢复成改写前的值。"""
    if reparse.status(ctx, paper_id)["active_job_id"]:
        raise ApiError("conflict", "正在重新解析，请等它结束或先取消任务", 409)
    return reparse.undo(ctx, paper_id)


@router.get("/{paper_id}")
def get_paper(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return repo.get_detail(ctx, paper_id)


class ReorderBody(BaseModel):
    ids: list[str]


@router.post("/reorder")
def reorder(body: ReorderBody, ctx: AppContext = Depends(get_ctx)):
    """手动排序：按给出的顺序重新排这批论文。"""
    return repo.reorder_papers(ctx, body.ids)


@router.patch("/{paper_id}")
def patch_paper(paper_id: str, body: PaperPatch, ctx: AppContext = Depends(get_ctx)):
    return repo.update_paper(ctx, paper_id, body.model_dump(exclude_unset=True))


@router.delete("/{paper_id}")
def delete_paper(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return repo.delete_paper(ctx, paper_id)


@router.get("/{paper_id}/profile")
def paper_profile(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """详情页汇总：页数、图表公式数量、笔记标注数量、目录、关键词。"""
    return profile.paper_profile(ctx, paper_id)


@router.get("/{paper_id}/cover")
def paper_cover(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """第一页缩略图（PNG），用作书架上的封面。"""
    return Response(profile.cover_png(ctx, paper_id), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/file")
def paper_file(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    detail = repo.get_detail(ctx, paper_id)
    path = ctx.paths.paper_dir(paper_id) / "original.pdf"
    if not detail["has_pdf"] or not path.is_file():
        raise ApiError("not_found", "该论文没有可用的 PDF 文件", 404)
    return FileResponse(path, media_type="application/pdf", filename="original.pdf", content_disposition_type="inline")


@router.get("/{paper_id}/outline")
def outline(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return reader.get_outline(ctx, paper_id)


@router.get("/{paper_id}/passages")
def passages_for_page(paper_id: str, page: int = Query(..., ge=1), ctx: AppContext = Depends(get_ctx)):
    return reader.page_passages(ctx, paper_id, page)


@router.get("/{paper_id}/text")
def paper_text(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return reader.full_text(ctx, paper_id)


@router.get("/{paper_id}/passages/{ord_}")
def one_passage(paper_id: str, ord_: int, ctx: AppContext = Depends(get_ctx)):
    return reader.get_passage(ctx, paper_id, ord_)


class InsightBody(BaseModel):
    force: bool = False


@router.get("/{paper_id}/passages/{ord_}/insight")
def passage_insight_cached(paper_id: str, ord_: int, ctx: AppContext = Depends(get_ctx)):
    """已经分析过的就直接给（不调用模型）；没有就告诉前端还没有。"""
    return passage_insight.get_cached(ctx, paper_id, ord_) or {"cached": False, "insight": None}


@router.post("/{paper_id}/passages/{ord_}/insight")
async def passage_insight_run(paper_id: str, ord_: int, body: InsightBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """这一段在整篇论证里的作用、该带走什么、怎么承上启下、读的时候留意什么（只在用户选中这一段时才调用）。"""
    return await passage_insight.analyze(ctx, paper_id, ord_, force=bool(body and body.force))


@router.get("/{paper_id}/glossary")
def paper_glossary(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """术语表（导入后已在后台做好）和生成状态。"""
    return glossary.get_glossary(ctx, paper_id)


@router.post("/{paper_id}/glossary/start")
def paper_glossary_start(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """还没有术语表（比如之前模型出错）就补做；已经有或正在做就什么也不会发生。"""
    return {"job_id": glossary.enqueue(ctx, paper_id)}


@router.get("/{paper_id}/passages/{ord_}/rects")
def passage_rects(paper_id: str, ord_: int, ctx: AppContext = Depends(get_ctx)):
    return rects_mod.passage_rects(ctx, paper_id, ord_)


@router.post("/{paper_id}/locate")
def locate(paper_id: str, body: LocateBody, ctx: AppContext = Depends(get_ctx)):
    return {"matches": reader.locate(ctx, paper_id, body.text, body.page)}


@router.get("/{paper_id}/search")
def search(paper_id: str, q: str = Query(..., min_length=1), limit: int = Query(30, ge=1, le=100),
           ctx: AppContext = Depends(get_ctx)):
    return {"items": reader.search(ctx, paper_id, q, limit)}


@router.post("/{paper_id}/fetch_fulltext")
def fetch_fulltext(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """只有题录的论文：自动去找开放获取的全文并附加（后台任务）。"""
    detail = repo.get_detail(ctx, paper_id)
    if detail["has_pdf"]:
        raise ApiError("conflict", "该论文已有 PDF 文件", 409)
    return {"job_id": ctx.jobs.submit("fetch_fulltext", f"获取全文：{detail['title']}", {"paper_id": paper_id}, paper_id)}


@router.post("/{paper_id}/attach_pdf")
def attach_pdf(paper_id: str, body: AttachBody, ctx: AppContext = Depends(get_ctx)):
    detail = repo.get_detail(ctx, paper_id)
    if detail["has_pdf"]:
        raise ApiError("conflict", "该论文已有 PDF 文件", 409)
    p = Path(body.path)
    job_id = ctx.jobs.submit("attach_pdf", f"附加 PDF：{p.name}", {"paper_id": paper_id, "path": str(p)}, paper_id)
    return {"job_id": job_id}
