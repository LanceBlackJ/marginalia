"""读完之后的自测（选择题）API：只有用户点“出题”才会调用模型。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..ai import quiz
from ..context import AppContext
from .deps import get_ctx

router = APIRouter(tags=["quiz"])


class QuizCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    count: int = Field(default=5, ge=quiz.MIN_COUNT, le=quiz.MAX_COUNT)
    kind: str = Field(default='mixed', pattern='^(mixed|choice|judge)$')


class AnswersBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answers: dict[int, int] = Field(default_factory=dict)


@router.get("/papers/{paper_id}/quizzes")
def list_quizzes(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"items": quiz.list_sets(ctx, paper_id)}


@router.post("/papers/{paper_id}/quizzes")
async def create_quiz(paper_id: str, body: QuizCreate | None = None, ctx: AppContext = Depends(get_ctx)):
    """让 AI 按这篇论文出一套题（选择题、判断题）（调用云端模型；发送的正文字数记入隐私日志）。"""
    return await quiz.generate(ctx, paper_id, body.count if body else 5, body.kind if body else 'mixed')


@router.put("/quizzes/{quiz_id}/answers")
def save_answers(quiz_id: int, body: AnswersBody, ctx: AppContext = Depends(get_ctx)):
    return quiz.save_answers(ctx, quiz_id, body.answers)


@router.delete("/quizzes/{quiz_id}")
def delete_quiz(quiz_id: int, ctx: AppContext = Depends(get_ctx)):
    quiz.delete_set(ctx, quiz_id)
    return {"ok": True}
