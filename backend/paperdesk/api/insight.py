"""研究卡片、分析链与论文整体分析。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..ai import analysis, cards
from ..context import AppContext
from .deps import get_ctx

router = APIRouter(tags=["insight"])


class CardPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saved: bool | None = None
    chain_id: str | None = None


class ChainPatch(BaseModel):
    title: str


def _ensure_paper(ctx: AppContext, paper_id: str) -> None:
    from ..library import repo

    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)


@router.get("/papers/{paper_id}/chains")
def chains(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    return {"items": cards.list_chains(ctx, paper_id)}


@router.get("/papers/{paper_id}/cards")
def list_cards(paper_id: str, chain_id: str | None = None, saved: bool | None = None, conversation_id: str | None = None,
               ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    return {"items": cards.list_cards(ctx, paper_id, chain_id, saved, conversation_id)}


class ConversationBody(BaseModel):
    title: str | None = None


@router.get("/papers/{paper_id}/conversations")
def conversations(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    return {"items": cards.list_conversations(ctx, paper_id)}


@router.post("/papers/{paper_id}/conversations")
def new_conversation(paper_id: str, body: ConversationBody | None = None, ctx: AppContext = Depends(get_ctx)):
    _ensure_paper(ctx, paper_id)
    return cards.create_conversation(ctx, paper_id, body.title if body else None)


@router.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: str, body: ChainPatch, ctx: AppContext = Depends(get_ctx)):
    return cards.patch_conversation(ctx, conversation_id, body.title)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, ctx: AppContext = Depends(get_ctx)):
    cards.delete_conversation(ctx, conversation_id)
    return {"ok": True}


@router.patch("/cards/{card_id}")
def patch_card(card_id: str, body: CardPatch, ctx: AppContext = Depends(get_ctx)):
    return cards.patch_card(ctx, card_id, body.saved, body.chain_id)


@router.delete("/cards/{card_id}")
def delete_card(card_id: str, ctx: AppContext = Depends(get_ctx)):
    cards.delete_card(ctx, card_id)
    return {"ok": True}


@router.patch("/chains/{chain_id}")
def patch_chain(chain_id: str, body: ChainPatch, ctx: AppContext = Depends(get_ctx)):
    return cards.patch_chain(ctx, chain_id, body.title)


@router.delete("/chains/{chain_id}")
def delete_chain(chain_id: str, ctx: AppContext = Depends(get_ctx)):
    cards.delete_chain(ctx, chain_id)
    return {"ok": True}


@router.get("/papers/{paper_id}/analysis")
def get_analysis(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return analysis.get_analysis(ctx, paper_id)


@router.post("/papers/{paper_id}/analysis/run")
def run_analysis(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"job_id": analysis.enqueue_analysis(ctx, paper_id)}
