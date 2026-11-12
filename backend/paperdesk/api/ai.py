"""AI 设置与 SSE 调用端点。"""
from __future__ import annotations

import json
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..ai import analysis, embeddings, fulltranslate, glossary
from ..ai import settings as ai_settings
from ..ai.providers import LLMError
from ..ai.service import AIService, AskRequest
from ..context import AppContext
from ..jobs import JobManager
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(tags=["ai"])


def register_jobs(manager: JobManager) -> None:
    analysis.register(manager)
    embeddings.register(manager)
    fulltranslate.register(manager)
    glossary.register(manager)


# ---- 设置 ----
@router.get("/settings/ai")
def get_ai_settings(ctx: AppContext = Depends(get_ctx)):
    return ai_settings.public_settings(ctx)


@router.put("/settings/ai")
def put_ai_settings(body: dict, ctx: AppContext = Depends(get_ctx)):
    return ai_settings.save_settings(ctx, body)


class SecretBody(BaseModel):
    api_key: str


@router.put("/settings/ai/secret")
def put_secret(body: SecretBody, ctx: AppContext = Depends(get_ctx)):
    key = body.api_key.strip()
    if not key:
        raise ApiError("bad_request", "API Key 不能为空")
    ctx.secrets.set(ai_settings.SECRET_NAME, key)
    return {"ok": True, "has_api_key": True}


@router.delete("/settings/ai/secret")
def delete_secret(ctx: AppContext = Depends(get_ctx)):
    ctx.secrets.clear(ai_settings.SECRET_NAME)
    return {"ok": True, "has_api_key": False}


@router.post("/settings/ai/test")
async def test_connection(ctx: AppContext = Depends(get_ctx)):
    try:
        return await AIService(ctx).test_connection()
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err


# ---- SSE ----
def sse_frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def sse_response(events: AsyncIterator[tuple[str, dict]]) -> StreamingResponse:
    async def gen():
        try:
            async for name, data in events:
                yield sse_frame(name, data)
        finally:
            await events.aclose()  # 客户端断开时让服务端生成器执行清理

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


class TranslateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str | None = None  # 论文不在文献库里（发现页的摘要）时不给；这时不缓存译文
    context_title: str | None = Field(default=None, max_length=300)  # 没有 paper_id 时用来帮助翻译的论文标题
    text: str
    page: int | None = None
    passage_ord: int | None = None
    force: bool = False
    # 这段文字里解析时还原出的行内公式 [{src, tex}]：翻译时当占位符保护起来，译文里放回原来的 src，前端才能把它渲染成公式
    math: list[dict] | None = Field(default=None, max_length=200)


@router.post("/ai/translate")
async def translate(body: TranslateBody, ctx: AppContext = Depends(get_ctx)):
    return sse_response(AIService(ctx).translate_events(body.paper_id, body.text, body.page, body.passage_ord, body.force, body.context_title, body.math))


class LookupBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    text: str = Field(max_length=20000)


@router.post("/ai/translate/lookup")
def translate_lookup(body: LookupBody, ctx: AppContext = Depends(get_ctx)):
    """翻译过的文字（比如摘要）直接取回译文，不调用模型：打开页面时自动显示以前翻译好的。没翻译过返回 null。"""
    return {"translation": AIService(ctx).cached_translation(body.paper_id, body.text)}


class SelectionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    page: int | None = None
    passage_ords: list[int] | None = None


class AttachmentBody(BaseModel):
    name: str = "附件"
    mime: str = ""
    data: str  # base64（不含 data: 前缀）


class AskBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    question: str = ""
    intent: Literal["free", "explain", "summarize", "term", "motivation", "formula", "figure", "experiment"] = "free"
    selection: SelectionBody | None = None
    chain_id: str | None = None
    save: bool | None = None
    web: bool = False
    conversation_id: str | None = None
    attachments: list[AttachmentBody] = []
    edit_card_id: str | None = None  # 修改这张卡片的提问并重新回答（只能是对话里最后一张）


@router.post("/ai/ask")
async def ask(body: AskBody, ctx: AppContext = Depends(get_ctx)):
    if not body.question.strip() and not (body.selection and body.selection.text.strip()):
        raise ApiError("bad_request", "请输入问题，或先选中一段内容")
    req = AskRequest(
        paper_id=body.paper_id, question=body.question, intent=body.intent,
        selection=body.selection.model_dump() if body.selection else None, chain_id=body.chain_id, save=body.save, web=body.web, conversation_id=body.conversation_id,
        attachments=[a.model_dump() for a in body.attachments], edit_card_id=body.edit_card_id,
    )
    return sse_response(AIService(ctx).ask_events(req))


# ---- 全文翻译 ----
@router.get("/papers/{paper_id}/translation")
def full_translation(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """全文翻译进度与已有译文：{total, done, complete, models, items:[{ord, translation}]}。"""
    return fulltranslate.status(ctx, paper_id)


@router.post("/papers/{paper_id}/translation")
def start_full_translation(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """创建全文翻译后台任务；已全部翻完时返回 job_id=null。发送原文前已记入隐私日志。"""
    from ..library import repo

    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
    return {"job_id": fulltranslate.enqueue(ctx, paper_id)}


# ---- 语义索引 ----
@router.get("/papers/{paper_id}/index")
def semantic_index_status(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """语义（向量）索引状态：{model, indexed, total, complete}。"""
    from ..library import repo

    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
    return embeddings.index_status(ctx, paper_id)


@router.post("/papers/{paper_id}/index")
def build_semantic_index(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    """创建建立语义索引的后台任务；已完成或未配置嵌入模型时返回 job_id=null。"""
    from ..library import repo

    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)
    return {"job_id": embeddings.enqueue_embedding(ctx, paper_id)}
