"""论文结构实体：图表区域、公式区域，以及区域/整页截图（阶段二的公式、框架图、表格理解的基础）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..context import AppContext
from ..library import repo
from ..parsing.render import RenderError, region_kind, render_clip, render_svg
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(prefix="/papers", tags=["structure"])


def _pdf_path(ctx: AppContext, paper_id: str):
    detail = repo.get_detail(ctx, paper_id)
    path = ctx.paths.paper_dir(paper_id) / "original.pdf"
    if not detail["has_pdf"] or not path.is_file():
        raise ApiError("not_found", "该论文没有可用的 PDF 文件", 404)
    return path


@router.get("/{paper_id}/figures")
def list_figures(paper_id: str, page: int | None = Query(default=None, ge=1), kind: str | None = None,
                 ctx: AppContext = Depends(get_ctx)):
    """图/表区域：{id,page,bbox,kind(figure|table),label,caption_ord,caption,inner_text}。"""
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
        sql = ("SELECT f.*, c.text AS caption, i.text AS inner_text FROM figures f "
               "LEFT JOIN passages c ON c.paper_id = f.paper_id AND c.ord = f.caption_ord "
               "LEFT JOIN passages i ON i.paper_id = f.paper_id AND i.ord = f.inner_ord WHERE f.paper_id = ?")
        args: list = [paper_id]
        if page:
            sql += " AND f.page = ?"
            args.append(page)
        if kind:
            sql += " AND f.kind = ?"
            args.append(kind)
        rows = conn.execute(sql + " ORDER BY f.page, f.y0, f.x0", args).fetchall()
    return {"items": [
        {"id": r["id"], "page": r["page"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]], "kind": r["kind"],
         "label": r["label"], "caption_ord": r["caption_ord"], "caption": r["caption"], "inner_text": r["inner_text"]}
        for r in rows
    ]}


@router.get("/{paper_id}/equations")
def list_equations(paper_id: str, page: int | None = Query(default=None, ge=1), ctx: AppContext = Depends(get_ctx)):
    """显示公式区域：{id,page,bbox,number,text,passage_ord}。text 来自 PDF 文字层，数学字体可能乱码，
    需要精确内容时请渲染区域截图交给视觉模型。"""
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
        sql = "SELECT * FROM equations WHERE paper_id = ?"
        args: list = [paper_id]
        if page:
            sql += " AND page = ?"
            args.append(page)
        rows = conn.execute(sql + " ORDER BY page, y0, x0", args).fetchall()
    return {"items": [
        {"id": r["id"], "page": r["page"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]], "number": r["number"],
         "text": r["text"], "passage_ord": r["passage_ord"]}
        for r in rows
    ]}


@router.get("/{paper_id}/regions/image")
def region_image(
    paper_id: str, page: int = Query(..., ge=1), x0: float | None = None, y0: float | None = None,
    x1: float | None = None, y1: float | None = None, dpi: int = Query(150, ge=50, le=300),
    ctx: AppContext = Depends(get_ctx),
):
    """渲染某页的一个区域（PDF point，左上角原点）为 PNG；不给坐标则渲染整页。"""
    path = _pdf_path(ctx, paper_id)
    coords = (x0, y0, x1, y1)
    if any(c is not None for c in coords) and any(c is None for c in coords):
        raise ApiError("bad_request", "区域坐标需要同时给出 x0,y0,x1,y1")
    bbox = None if x0 is None else (x0, y0, x1, y1)  # type: ignore[arg-type]
    try:
        png = render_clip(path, page, bbox, dpi)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/regions/svg")
def region_svg(
    paper_id: str, page: int = Query(..., ge=1), x0: float = Query(...), y0: float = Query(...), x1: float = Query(...), y1: float = Query(...),
    ctx: AppContext = Depends(get_ctx),
):
    """把某页的一个区域导出成矢量图（SVG）：线条和文字是矢量，嵌入的位图保持原始分辨率。导出不了（太复杂等）返回 400，客户端改用截图。"""
    path = _pdf_path(ctx, paper_id)
    try:
        svg = render_svg(path, page, (x0, y0, x1, y1))
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc
    return Response(content=svg.encode("utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/regions/kind")
def region_info(
    paper_id: str, page: int = Query(..., ge=1), x0: float = Query(...), y0: float = Query(...), x1: float = Query(...), y1: float = Query(...),
    ctx: AppContext = Depends(get_ctx),
):
    """这块区域在 PDF 里是矢量还是位图（位图的像素尺寸和等效 dpi），给界面标注“放大会不会糊”。"""
    try:
        return region_kind(_pdf_path(ctx, paper_id), page, (x0, y0, x1, y1))
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc


def _svg_response(path, page: int, bbox: tuple[float, float, float, float]) -> Response:
    try:
        svg = render_svg(path, page, bbox, pad=6.0)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc
    return Response(content=svg.encode("utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/equations/{equation_id}/image")
def equation_image(paper_id: str, equation_id: int, dpi: int = Query(200, ge=50, le=300), vector: bool = Query(False), ctx: AppContext = Depends(get_ctx)):
    path = _pdf_path(ctx, paper_id)
    with ctx.db.session() as conn:
        r = conn.execute("SELECT page, x0, y0, x1, y1 FROM equations WHERE id = ? AND paper_id = ?", (equation_id, paper_id)).fetchone()
    if r is None:
        raise ApiError("not_found", "公式不存在", 404)
    if vector:  # 矢量图：放大不会糊；导不出来（太复杂等）时调用方退回位图
        return _svg_response(path, r["page"], (r["x0"], r["y0"], r["x1"], r["y1"]))
    try:
        png = render_clip(path, r["page"], (r["x0"], r["y0"], r["x1"], r["y1"]), dpi, pad=6.0)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/figures/{figure_id}/image")
def figure_image(paper_id: str, figure_id: int, dpi: int = Query(150, ge=50, le=300), vector: bool = Query(False), ctx: AppContext = Depends(get_ctx)):
    path = _pdf_path(ctx, paper_id)
    with ctx.db.session() as conn:
        r = conn.execute("SELECT page, x0, y0, x1, y1 FROM figures WHERE id = ? AND paper_id = ?", (figure_id, paper_id)).fetchone()
    if r is None:
        raise ApiError("not_found", "图表不存在", 404)
    if vector:
        return _svg_response(path, r["page"], (r["x0"], r["y0"], r["x1"], r["y1"]))
    try:
        png = render_clip(path, r["page"], (r["x0"], r["y0"], r["x1"], r["y1"]), dpi, pad=6.0)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
