"""思维导图与主动研究提示共用的“强模型返回结构化 JSON”调用：

每次发送都登记“发送给云端的内容”（隐私记录）与用量；输出先做 JSON 解析再交给调用方校验，
校验不通过时把具体原因告诉模型、让它重写一次（最多 attempts 次），仍然失败就明确报错，不会保存半成品。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..ai import usage as usage_log
from ..ai.providers import LLMError, LLMProvider
from ..ai.service import AIService
from ..context import AppContext
from ..jobs import JobContext
from ..llmjson import extract_json, pick_model
from ..util import ApiError

# provider 自带的重试只等 1~2 秒，网关繁忙（502/503/限流）时通常不够；这类“可重试”的错误再各等一段时间重来一轮，
# 总共最多 1 + len(OUTER_RETRY_DELAYS) 轮（每轮内部仍按设置里的 max_retries 重试）。测试里会改成 0。
OUTER_RETRY_DELAYS: tuple[float, ...] = (8.0, 20.0)


async def _pause(ctx: JobContext, seconds: float) -> None:
    """等待 seconds 秒，每秒检查一次是否被用户取消。"""
    end = asyncio.get_running_loop().time() + seconds
    while (left := end - asyncio.get_running_loop().time()) > 0:
        ctx.check_cancelled()
        await asyncio.sleep(min(1.0, left))


async def _complete_with_patience(ctx: JobContext, provider: LLMProvider, model: str, messages: list[dict], max_tokens: int):
    for round_, delay in enumerate((0.0, *OUTER_RETRY_DELAYS)):
        if delay:
            await _pause(ctx, delay)
        try:
            return await provider.complete(model, messages, temperature=0.2, max_tokens=max_tokens, json_mode=True)
        except LLMError as err:
            if not err.retriable or round_ >= len(OUTER_RETRY_DELAYS):
                raise
            ctx.log(f"{err.message} 稍等 {OUTER_RETRY_DELAYS[round_]:.0f} 秒后再试（第 {round_ + 2} 轮）。")


async def call_json(
    app: AppContext, ctx: JobContext, provider: LLMProvider, cfg: dict, messages: list[dict], *, task_type: str,
    paper_id: str, pack_ords: list[int], summary: str, includes: dict,
    validate: Callable[[Any], Any], max_tokens: int, attempts: int = 2, tier: str = "strong",
) -> tuple[Any, str, int]:
    """返回 (validate 的结果, 实际使用的模型, 用了几次调用)。validate 抛 ValueError 表示输出不可用（消息会反馈给模型）。"""
    service = AIService(app)
    model = pick_model(cfg, tier)
    last = "输出不可用"
    for attempt in range(1, attempts + 1):
        ctx.check_cancelled()
        usage_log.record_request(
            app, task_type=task_type, model=model, paper_id=paper_id, passage_ords=pack_ords,
            chars_sent=sum(len(m["content"]) for m in messages),
            includes=includes, summary=summary + (f"（第 {attempt} 次尝试）" if attempt > 1 else ""),
        )
        res = await _complete_with_patience(ctx, provider, model, messages, max_tokens)
        service._log_usage(task_type, model, res.usage, messages, res.text, paper_id, ctx.job_id)
        ctx.log(f"模型 {model} 输出 {len(res.text)} 字，前 300 字：{' '.join(res.text[:300].split())}")
        try:
            data = extract_json(res.text)
        except ValueError:
            reason = "输出不是合法的 JSON" if res.text.strip() else "输出为空"
            ctx.log(f"第 {attempt} 次输出未通过校验：{reason}")
            last = reason
        else:
            try:
                return validate(data), model, attempt
            except ValueError as exc:
                reason = str(exc) or "输出不可用"
                ctx.log(f"第 {attempt} 次输出未通过校验：{reason}")
                last = reason
        messages = [*messages, {"role": "assistant", "content": res.text[:3000] or "（空）"},
                    {"role": "user", "content": f"上一次输出不可用：{last}\n请重新输出完整的 JSON，只输出 JSON，不要任何其它文字。"}]
    raise ApiError("provider_error", f"模型连续 {attempts} 次返回的结果都没有通过校验：{last}。请稍后重试，或在设置中换用更强的模型。")
