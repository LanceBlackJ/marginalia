"""可替换的模型提供商适配层：OpenAI 兼容接口与 Anthropic Messages 接口。

统一接口：complete()（一次性）与 stream()（流式）。错误统一为 LLMError，
消息中不会包含 API Key。支持 429/5xx/超时的指数退避重试（流式只在首包前重试）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from ..context import USER_AGENT

log = logging.getLogger("paperdesk.llm")


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retriable: bool = False, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable
        self.status = status


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False


@dataclass
class LLMResult:
    text: str
    usage: Usage
    model: str
    finish_reason: str | None = None


@dataclass
class StreamChunk:
    text: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None
    dropped: int = 0  # 无法解析而被丢弃的数据帧数量（只在流末尾的收尾块里给出）
    notice: str | None = None  # 给界面看的提示（例如强模型不可用、已改用快速模型）；没有文字内容


async def iter_sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    """只按 \\n 切行。httpx 的 aiter_lines 底层用 str.splitlines，会在 U+2028/U+0085 等字符处把一帧切成两半。"""
    buf = ""
    async for text in response.aiter_text():
        buf += text
        while (i := buf.find("\n")) >= 0:
            line, buf = buf[:i], buf[i + 1:]
            yield line.rstrip("\r")
    if buf:
        yield buf


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    return int(cjk * 1.1 + (len(text) - cjk) / 3.8) + 1


# ---- 多模态消息 ----
# 中性内容格式：content 可以是 str，或由下列片段组成的 list：
#   {"type": "text", "text": "…"}
#   {"type": "image", "mime": "image/png", "data": "<base64>"}
#   {"type": "image_url", "image_url": {"url": "data:image/png;base64,…"}}   （OpenAI 风格，直接透传/转换）
IMAGE_TOKENS = 1100  # 每张图的粗略 token 估算（服务端不返回用量时使用）


def image_part(png: bytes, mime: str = "image/png") -> dict:
    import base64

    return {"type": "image", "mime": mime, "data": base64.b64encode(png).decode("ascii")}


def content_text(content) -> str:
    """消息内容里的文字部分（图片忽略）。"""
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def count_images(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            n += sum(1 for p in c if isinstance(p, dict) and p.get("type") in ("image", "image_url"))
    return n


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(content_text(m.get("content"))) for m in messages) + IMAGE_TOKENS * count_images(messages)


_DATA_URL_RE = re.compile(r"data:([\w/+.-]+);base64,(.*)$", re.S)


def _to_openai_content(content):
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif t == "image":
            out.append({"type": "image_url", "image_url": {"url": f"data:{p.get('mime', 'image/png')};base64,{p['data']}"}})
        elif t == "image_url":
            out.append(p)
    return out


def _to_anthropic_content(content):
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        t = p.get("type")
        if t == "text":
            out.append({"type": "text", "text": p.get("text", "")})
        elif t == "image":
            out.append({"type": "image", "source": {"type": "base64", "media_type": p.get("mime", "image/png"), "data": p["data"]}})
        elif t == "image_url":
            m = _DATA_URL_RE.match((p.get("image_url") or {}).get("url", ""))
            if m:
                out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    return out


THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


def strip_think(text: str) -> str:
    """去掉推理模型混在正文里的 <think>…</think>（也处理只有结束标签的情况）。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if THINK_CLOSE in text and THINK_OPEN not in text:
        text = text.split(THINK_CLOSE)[-1]
    return text.lstrip("\n")


def _partial_suffix(buf: str, tag: str) -> int:
    """buf 末尾有多少个字符可能是 tag 的开头（标签被拆在两个数据块里）。"""
    for n in range(min(len(tag) - 1, len(buf)), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0


class ThinkFilter:
    """流式过滤 <think>…</think>：标签可能被拆在多个数据块里。"""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False
        self._started = False  # 是否已经输出过正文（用于吞掉思考块后紧跟的换行）

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = ""
        while True:
            if self.inside:
                i = self.buf.find(THINK_CLOSE)
                if i < 0:
                    keep = _partial_suffix(self.buf, THINK_CLOSE)
                    self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                    return out
                self.buf = self.buf[i + len(THINK_CLOSE):]
                self.inside = False
                if not self._started:
                    self.buf = self.buf.lstrip("\n")
            else:
                i = self.buf.find(THINK_OPEN)
                if i < 0:
                    keep = _partial_suffix(self.buf, THINK_OPEN)
                    emit = self.buf[: len(self.buf) - keep]
                    self.buf = self.buf[len(self.buf) - keep:]
                    if emit.strip():
                        self._started = True
                    return out + emit
                out += self.buf[:i]
                if self.buf[:i].strip():
                    self._started = True
                self.buf = self.buf[i + len(THINK_OPEN):]
                self.inside = True

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf = ""
        return out


def _scrub(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***")
    return text[:300]


def _map_status(status: int, body: str) -> LLMError:
    low = body.lower()
    if status in (401, 403):
        return LLMError("auth_failed", "API Key 无效或没有访问该模型的权限，请检查设置。", status=401)
    if status == 404:
        return LLMError("provider_error", "接口地址或模型名称不存在，请检查 API 地址与模型名称。")
    if status == 429:
        return LLMError("rate_limited", "请求过于频繁或额度不足，请稍后重试。", retriable=True, status=429)
    if status == 400 and any(k in low for k in ("context length", "context_length", "too long", "maximum context", "too many tokens")):
        return LLMError("context_too_long", "发送的内容超过了模型的上下文长度。", status=400)
    if status >= 500:
        return LLMError("provider_error", f"模型服务暂时不可用，请稍后重试。", retriable=True)
    return LLMError("provider_error", f"模型服务返回错误 {status}：{body[:200]}")


class LLMProvider:
    def __init__(
        self, base_url: str, api_key: str, *, timeout: float = 60, max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.transport = transport
        # 模型回退：{原模型: 备用模型}。重试耗尽后仍遇到 5xx/超时/连接错误、且还没吐出任何内容时，改用备用模型再试一次。
        # 认证失败、参数错误这类不可重试的错误不回退。每次回退都记在 fallback_events 里，调用方必须如实告知用户。
        self.fallbacks: dict[str, str] = {}
        self.fallback_events: list[dict] = []

    def _fallback_for(self, model: str, err: LLMError) -> str | None:
        fb = self.fallbacks.get(model)
        if not fb or fb == model or not err.retriable:
            return None
        # 只对“服务暂时不可用”回退：5xx（provider_error）、超时、连接错误。限流（429）、认证失败、参数错误都不回退，
        # 换一个模型不能解决它们，还会把真正的原因掩盖掉。
        if err.code in ("timeout", "network") or (err.code == "provider_error" and err.status >= 500):
            return fb
        return None

    # 子类实现
    def _url(self) -> str: ...
    def _headers(self) -> dict: ...
    def _payload(self, model, messages, *, temperature, max_tokens, json_mode, stream) -> dict: ...
    def _parse_stream_event(self, event: str | None, data: dict) -> StreamChunk | None: ...

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)), transport=self.transport,
            headers={"User-Agent": USER_AGENT},
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        await asyncio.sleep(min(8.0, 0.8 * (2 ** attempt)))

    def _transport_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMError("timeout", "请求模型超时，请稍后重试或在设置中调大超时时间。", retriable=True, status=504)
        return LLMError("network", "无法连接到模型服务，请检查网络与 API 地址。", retriable=True, status=502)

    def _adapt_payload(self, payload: dict, body: str) -> bool:
        """遇到参数不兼容（如新模型不支持 max_tokens / temperature）时调整一次。"""
        return False

    async def complete(
        self, model: str, messages: list[dict], *, temperature: float | None = None,
        max_tokens: int | None = None, json_mode: bool = False,
    ) -> LLMResult:
        """一次性返回结果。内部走流式再聚合：部分网关的非流式路由会挂起（实测），
        而流式能在首包阶段就暴露问题，并且统一了重试与参数兼容逻辑。"""
        text, finish = "", None
        usage = Usage()
        used = model
        async for ch in self.stream(model, messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode):
            text += ch.text
            finish = ch.finish_reason or finish
            if ch.notice and self.fallback_events:
                used = self.fallback_events[-1]["to"]  # 实际回答的模型（发生回退时是备用模型）
            if ch.usage:
                usage.prompt_tokens = max(usage.prompt_tokens, ch.usage.prompt_tokens)
                usage.completion_tokens = max(usage.completion_tokens, ch.usage.completion_tokens)
        text = strip_think(text)
        if not usage.prompt_tokens and not usage.completion_tokens:
            usage = Usage(0, estimate_tokens(text), True)
        return LLMResult(text, usage, used, finish)

    async def stream(
        self, model: str, messages: list[dict], *, temperature: float | None = None,
        max_tokens: int | None = None, json_mode: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        """流式生成；带模型回退（见 fallbacks）。回退发生时先给出一个只有 notice 的块，再从备用模型开始输出。"""
        started = False
        failure: LLMError | None = None
        try:
            async for ch in self._stream_once(model, messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode):
                started = True
                yield ch
            return
        except LLMError as err:
            if started or self._fallback_for(model, err) is None:
                raise
            failure = err
        fb = self._fallback_for(model, failure)
        assert fb is not None
        note = f"模型 {model} 暂时不可用，本次由 {fb} 回答。"
        self.fallback_events.append({"from": model, "to": fb, "reason": failure.message, "message": note})
        log.warning("model fallback %s -> %s: %s", model, fb, failure.message)
        yield StreamChunk(notice=note)
        async for ch in self._stream_once(fb, messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode):
            yield ch

    async def _stream_once(
        self, model: str, messages: list[dict], *, temperature: float | None = None,
        max_tokens: int | None = None, json_mode: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._payload(model, messages, temperature=temperature, max_tokens=max_tokens,
                                json_mode=json_mode, stream=True)
        adapted = 0
        attempt = 0
        while True:
            started = False
            try:
                async with self._client() as client:
                    async with client.stream("POST", self._url(), headers=self._headers(), json=payload) as r:
                        if r.status_code != 200:
                            body = _scrub((await r.aread()).decode("utf-8", "replace"), self.api_key)
                            if r.status_code == 400 and adapted < 3 and self._adapt_payload(payload, body):
                                adapted += 1
                                continue
                            raise _map_status(r.status_code, body)
                        event_name: str | None = None
                        think = ThinkFilter()
                        dropped = 0
                        async for line in iter_sse_lines(r):
                            line = line.strip()
                            if not line:
                                event_name = None
                                continue
                            if line.startswith("event:"):
                                event_name = line[6:].strip()
                                continue
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                data = json.loads(raw)
                            except ValueError:
                                dropped += 1  # 绝不静默丢弃：计数、记录，并在结尾告知调用方
                                log.warning("dropped malformed SSE frame (%d chars): %r", len(raw), raw[:80])
                                continue
                            chunk = self._parse_stream_event(event_name, data)
                            if chunk is None:
                                continue
                            if chunk.text:
                                chunk.text = think.feed(chunk.text)
                                if not chunk.text and chunk.usage is None and chunk.finish_reason is None:
                                    continue  # 整块都在思考标签内
                            started = True
                            yield chunk
                        tail = think.flush()
                        if tail or dropped:
                            yield StreamChunk(text=tail, dropped=dropped)
                        return
            except LLMError as err:
                if not err.retriable or attempt >= self.max_retries or started:
                    raise
            except httpx.HTTPError as exc:
                err = self._transport_error(exc)
                if started or attempt >= self.max_retries:
                    raise err from exc
            attempt += 1
            await self._sleep_backoff(attempt - 1)


class OpenAICompatibleProvider(LLMProvider):
    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, model, messages, *, temperature, max_tokens, json_mode, stream) -> dict:
        wire = [{**m, "content": _to_openai_content(m["content"])} for m in messages]
        p: dict = {"model": model, "messages": wire, "stream": stream}
        if temperature is not None:
            p["temperature"] = temperature
        if max_tokens:
            p["max_tokens"] = max_tokens
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        if stream:
            p["stream_options"] = {"include_usage": True}
        return p

    def _adapt_payload(self, payload: dict, body: str) -> bool:
        low = body.lower()
        changed = False
        if "max_completion_tokens" in low and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
            changed = True
        if "temperature" in low and "temperature" in payload:
            payload.pop("temperature")
            changed = True
        if "response_format" in low and "response_format" in payload:
            payload.pop("response_format")
            changed = True
        if "stream_options" in low and "stream_options" in payload:
            payload.pop("stream_options")
            changed = True
        return changed

    @staticmethod
    def _usage(u: dict | None) -> Usage | None:
        if not u:
            return None
        return Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))

    def _parse_stream_event(self, event, data):
        usage = self._usage(data.get("usage"))
        choices = data.get("choices") or []
        text = ""
        finish = None
        if choices:
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            finish = choices[0].get("finish_reason")
        if not text and usage is None and finish is None:
            return None
        return StreamChunk(text=text, usage=usage, finish_reason=finish)


class AnthropicProvider(LLMProvider):
    def _url(self) -> str:
        base = self.base_url
        return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def _payload(self, model, messages, *, temperature, max_tokens, json_mode, stream) -> dict:
        system = "\n\n".join(content_text(m["content"]) for m in messages if m["role"] == "system")
        rest = [{"role": m["role"], "content": _to_anthropic_content(m["content"])} for m in messages if m["role"] != "system"]
        p: dict = {"model": model, "messages": rest, "max_tokens": max_tokens or 4096, "stream": stream}
        if system:
            p["system"] = system
        if temperature is not None:
            p["temperature"] = temperature
        return p

    def _adapt_payload(self, payload: dict, body: str) -> bool:
        if "temperature" in body.lower() and "temperature" in payload:
            payload.pop("temperature")
            return True
        return False

    def _parse_stream_event(self, event, data):
        t = data.get("type") or event
        if t == "message_start":
            u = (data.get("message") or {}).get("usage") or {}
            return StreamChunk(usage=Usage(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)))
        if t == "content_block_delta":
            d = data.get("delta") or {}
            if d.get("type") == "text_delta":
                return StreamChunk(text=d.get("text", ""))
            return None
        if t == "message_delta":
            u = data.get("usage") or {}
            return StreamChunk(
                usage=Usage(0, int(u.get("output_tokens") or 0)), finish_reason=(data.get("delta") or {}).get("stop_reason")
            )
        if t == "error":
            msg = (data.get("error") or {}).get("message", "模型服务返回错误")
            raise LLMError("provider_error", msg[:200], retriable=(data.get("error") or {}).get("type") == "overloaded_error")
        return None


def build_provider(
    kind: str, base_url: str, api_key: str, *, timeout: float, max_retries: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMProvider:
    cls = AnthropicProvider if kind == "anthropic" else OpenAICompatibleProvider
    return cls(base_url, api_key, timeout=timeout, max_retries=max_retries, transport=transport)
