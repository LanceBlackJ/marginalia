"""发现/比较/知识库共用的“调用模型并解析 JSON”辅助。

统一处理：模型路由（快速/强）、用量记录、发送内容的隐私记录、<think> 过滤后的 JSON 提取与一次重试。
失败一律抛 LLMError 或返回 None（由调用方决定降级），不会泄露 API Key。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .ai import usage as usage_log
from .ai.providers import LLMError, estimate_tokens
from .ai.service import AIService
from .context import AppContext

_JSON_RE = re.compile(r"[\[{].*[\]}]", re.S)


def llm_available(app: AppContext) -> bool:
    try:
        AIService(app).provider()
        return True
    except LLMError:
        return False


def extract_json(text: str) -> Any:
    """从模型输出中取出第一个完整 JSON（对象或数组）。找不到抛 ValueError。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no json")
    blob = m.group(0)
    # 从后往前收缩到能解析为止（模型有时在 JSON 后追加说明文字）
    for end in range(len(blob), 1, -1):
        if blob[end - 1] in "]}":
            try:
                return json.loads(blob[:end])
            except ValueError:
                continue
    raise ValueError("bad json")


def repair_truncated_json(text: str) -> Any:
    """输出被长度限制截断的 JSON：丢掉不完整的最后一部分、补全括号后解析。无法修复抛 ValueError。

    只在“最后一个逗号 / 最后一个已闭合的嵌套值”处截断，所以丢失的只是末尾的若干完整元素之后的内容，
    不会把半截字符串或半截数字当作完整数据。调用方必须把“被截断过”告诉用户。
    """
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no json")
    s = s[min(starts):]
    stack: list[str] = []
    cands: list[tuple[int, str]] = []  # (截断位置, 需要补的右括号)
    in_str = esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            if not stack:
                return json.loads(s[: i + 1])  # 其实是完整的
            cands.append((i + 1, "".join(reversed(stack))))
        elif ch == "," and stack:
            cands.append((i, "".join(reversed(stack))))
    for cut, closers in reversed(cands[-400:]):
        try:
            return json.loads(s[:cut] + closers)
        except ValueError:
            continue
    raise ValueError("bad json")


def pick_model(cfg: dict, tier: str) -> str:
    fast, strong = cfg.get("fast_model") or "", cfg.get("strong_model") or ""
    model = (strong or fast) if tier == "strong" else (fast or strong)
    if cfg.get("mode") == "fast":
        model = fast or strong
    elif cfg.get("mode") == "strong":
        model = strong or fast
    return model


async def ask_json(
    app: AppContext, *, system: str, user: str, task_type: str, tier: str = "fast", max_tokens: int = 1800,
    includes: dict | None = None, summary: str = "", paper_id: str | None = None, job_id: str | None = None,
    retries: int = 1, notes: list[str] | None = None,
) -> Any:
    """调用模型，要求只输出 JSON；解析失败会让模型重输出一次。LLMError 向上抛出。
    强模型暂时不可用而改用快速模型时，把如实说明追加到 notes（调用方应展示给用户）。"""
    provider, cfg = AIService(app).provider()
    model = pick_model(cfg, tier)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    usage_log.record_request(
        app, task_type=task_type, model=model, paper_id=paper_id, passage_ords=[], chars_sent=len(system) + len(user),
        includes=includes or {"question": True, "selection": False, "notes": False, "supplements": False, "code": False},
        summary=summary or f"发送了约 {len(user):,} 个字符的文本（{task_type}）",
    )
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        res = await provider.complete(model, messages, temperature=0.1, max_tokens=max_tokens, json_mode=True)
        pt, ct = res.usage.prompt_tokens, res.usage.completion_tokens
        est = res.usage.estimated
        if not pt and not ct:
            pt, ct, est = sum(estimate_tokens(m["content"]) for m in messages), estimate_tokens(res.text), True
        usage_log.record_usage(app, task_type, res.model, pt, ct, estimated=est, paper_id=paper_id, job_id=job_id)
        if notes is not None:
            for fb in provider.fallback_events:
                if fb["message"] not in notes:
                    notes.append(fb["message"])
        try:
            return extract_json(res.text)
        except ValueError as exc:
            last_err = exc
            messages = [*messages, {"role": "assistant", "content": res.text[:3000]},
                        {"role": "user", "content": "上一次输出不是合法 JSON。请只输出一个 JSON，不要任何其它文字。"}]
    raise LLMError("provider_error", "模型返回的内容无法解析为 JSON，请稍后重试。") from last_err
