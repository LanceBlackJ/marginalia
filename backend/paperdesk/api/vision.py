"""公式与图表理解的 HTTP 接口（契约见 docs/API-vision.md）。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

from .ai import sse_response
from ..ai.providers import LLMError
from ..context import AppContext
from ..jobs import JobManager
from ..library import repo
from ..util import ApiError
from ..vision import explain, figure_qa, figures, formula, formula_batch, table_batch
from .deps import get_ctx

router = APIRouter(tags=["vision"])


def register_jobs(manager: JobManager) -> None:
    figures.register(manager)
    formula_batch.register(manager)
    table_batch.register(manager)


def _ensure_paper(ctx: AppContext, paper_id: str) -> None:
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)


# ---- 公式 ----
@router.get("/papers/{paper_id}/equations-latex")
def equations_latex_status(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """整篇论文已识别的公式 LaTeX（只读缓存，不调用模型）：{total, done, failed, complete, items}。"""
    _ensure_paper(ctx, paper_id)
    return formula_batch.status(ctx, paper_id)


@router.post("/papers/{paper_id}/equations-latex")
def start_equations_latex(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """后台识别整篇论文里还没识别的公式（截图发给视觉模型，记入隐私日志）；没有要识别的返回 job_id=null。"""
    _ensure_paper(ctx, paper_id)
    return {"job_id": formula_batch.enqueue(ctx, paper_id)}


@router.get("/papers/{paper_id}/equations/{equation_id}/latex")
def get_equation_latex(paper_id: str, equation_id: int, ctx: AppContext = Depends(get_ctx)):
    """已缓存的 LaTeX 转写；没有则 status=none（不会调用模型）。"""
    _ensure_paper(ctx, paper_id)
    formula.load_equation(ctx, paper_id, equation_id)
    hit = formula.get_cached(ctx, equation_id)
    return {**hit, "cached": True} if hit else {"equation_id": equation_id, "latex": None, "confidence": None, "status": "none",
                                                  "warnings": [], "error": None, "model": None, "cached": False}


class TranscribeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    force: bool = False


@router.post("/papers/{paper_id}/equations/{equation_id}/latex")
async def transcribe_equation(paper_id: str, equation_id: int, body: TranscribeBody | None = None,
                              ctx: AppContext = Depends(get_ctx)):
    """把公式区域截图发给视觉模型，返回 LaTeX（结果缓存；force=true 重新识别）。"""
    _ensure_paper(ctx, paper_id)
    try:
        return await formula.transcribe_equation(ctx, paper_id, equation_id, force=bool(body and body.force))
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err


class LocateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int | None = None
    text: str | None = None
    bbox: list[float] | None = None


@router.post("/papers/{paper_id}/equations/locate")
def locate_equation(paper_id: str, body: LocateBody, ctx: AppContext = Depends(get_ctx)):
    """把阅读器里选中的区域/文字映射到最近的公式：{equation, candidates}；找不到时 equation=null。"""
    _ensure_paper(ctx, paper_id)
    return formula.locate_equation(ctx, paper_id, page=body.page, text=body.text, bbox=body.bbox)


class ExplainBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    equation_id: int
    question: str = ""
    chain_id: str | None = None
    save: bool | None = None
    refresh_latex: bool = False


class ExplainFigureBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    figure_id: int
    question: str = ""
    chain_id: str | None = None
    conversation_id: str | None = None
    save: bool | None = None


@router.post("/ai/explain-figure")
async def explain_figure(body: ExplainFigureBody, ctx: AppContext = Depends(get_ctx)):
    """SSE：stage → meta → delta → done{card}（或 error）。图/表的 AI 解析放进右侧对话，卡片 object_type 为 figure。"""
    _ensure_paper(ctx, body.paper_id)
    return sse_response(figure_qa.explain_figure_events(
        ctx, body.paper_id, body.figure_id, question=body.question, chain_id=body.chain_id,
        conversation_id=body.conversation_id, save=True if body.save is None else body.save,
    ))


@router.post("/ai/explain-equation")
async def explain_equation(body: ExplainBody, ctx: AppContext = Depends(get_ctx)):
    """SSE：stage → latex → meta → delta* → done{card}（或 error）。卡片 object_type 为 equation，answer 里带 symbols/relations/derivation。"""
    _ensure_paper(ctx, body.paper_id)
    return sse_response(explain.explain_equation_events(
        ctx, body.paper_id, body.equation_id, question=body.question, chain_id=body.chain_id,
        save=True if body.save is None else body.save, refresh_latex=body.refresh_latex,
    ))


# ---- 表格（从图注推出位置，整篇批量识别） ----
@router.get("/papers/{paper_id}/tables-analysis")
def tables_status(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """整篇论文的表格与识别结果（只读缓存，不调用模型）：{total, ready, failed, running, complete, items}。"""
    _ensure_paper(ctx, paper_id)
    return table_batch.status(ctx, paper_id)


class TablesBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    force: bool = False
    #: 只处理这些表题段落序号的表；不给就处理所有还没识别的表
    ords: list[int] | None = None
    #: auto：先文字建表、再文字层坐标还原，还原不了才用视觉模型；vision：强制用视觉模型（文字层还原得不对时换个办法再试）
    method: Literal["auto", "vision"] = "auto"


@router.post("/papers/{paper_id}/tables-analysis")
def start_tables(paper_id: str, body: TablesBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """后台识别还没识别的表格（截图发给视觉模型，记入隐私日志）。没有要识别的返回 job_id=null；没有 API Key 或没配置视觉模型时报可读错误。"""
    _ensure_paper(ctx, paper_id)
    return {"job_id": table_batch.start(ctx, paper_id, force=bool(body and body.force), only=body.ords if body else None, method=body.method if body else "auto")}


@router.get("/papers/{paper_id}/tables/{caption_ord}/image")
def table_image(paper_id: str, caption_ord: int, dpi: int = Query(150, ge=72, le=300), vector: bool = Query(False), ctx: AppContext = Depends(get_ctx)):
    """表格区域的图（本地渲染，不调用模型），供“对照原图”。vector=true 时给矢量图（SVG），放大不糊。"""
    _ensure_paper(ctx, paper_id)
    if vector:
        return Response(content=table_batch.image_svg(ctx, paper_id, caption_ord).encode("utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})
    return Response(content=table_batch.image_png(ctx, paper_id, caption_ord, dpi), media_type="image/png")


# ---- 图表 ----
class AnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    force: bool = False
    kind: Literal["auto", "framework", "chart", "table"] = "auto"


@router.get("/papers/{paper_id}/vision/status")
def vision_status(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """已有的公式转写与图表分析状态（用于在 PDF 上标注哪些已处理）。"""
    _ensure_paper(ctx, paper_id)
    return figures.list_status(ctx, paper_id)


@router.get("/papers/{paper_id}/vision/figures")
def vision_figures(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """逻辑图列表：解析器切碎的子图区域已按图注归并。{items:[{id,member_ids,page,bbox,kind,label,caption,analysis_status,analysis_kind}]}"""
    _ensure_paper(ctx, paper_id)
    return {"items": figures.list_figures(ctx, paper_id)}


@router.get("/papers/{paper_id}/figures/{figure_id}/analysis")
def get_figure_analysis(paper_id: str, figure_id: int, ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    return figures.get_analysis(ctx, paper_id, figure_id)


@router.post("/papers/{paper_id}/figures/{figure_id}/analyze")
def analyze_figure(paper_id: str, figure_id: int, body: AnalyzeBody | None = None, ctx: AppContext = Depends(get_ctx)):
    """创建图表理解后台任务 → {job_id, status}。已有结果且未 force 时 job_id=null、status=ready。"""
    _ensure_paper(ctx, paper_id)
    b = body or AnalyzeBody()
    return figures.enqueue(ctx, paper_id, figure_id, force=b.force, kind=None if b.kind == "auto" else b.kind)


@router.delete("/papers/{paper_id}/figures/{figure_id}/analysis", status_code=204)
def delete_figure_analysis(paper_id: str, figure_id: int, ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    figures.delete_analysis(ctx, paper_id, figure_id)


class FigureAskBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str


@router.post("/papers/{paper_id}/figures/{figure_id}/ask")
async def ask_figure(paper_id: str, figure_id: int, body: FigureAskBody, ctx: AppContext = Depends(get_ctx)):
    """向视觉模型追问这张图（会发送图的截图）：{answerable, answer, seen, numbers[{value,how}], warnings}。
    图里看不出的内容 answerable=false，answer 以“图中看不出”开头。"""
    _ensure_paper(ctx, paper_id)
    return await figure_qa.ask_figure(ctx, paper_id, figure_id, body.question)
