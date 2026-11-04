"""AI 概括参考文献：只依据（a）联网查到的摘要，（b）本文里引用它的句子；没有摘要就拒绝，不凭标题臆测。"""
from __future__ import annotations

from typing import AsyncIterator

from ..ai import usage as usage_log
from ..ai.providers import LLMError, Usage
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService, llm_error_event
from ..context import AppContext
from ..util import ApiError
from . import resolve as resolver
from . import store

Event = tuple[str, dict]

SYSTEM = """你是论文阅读助手。用户在读论文 A，想知道论文 A 引用的某一篇参考文献 B 做了什么。
你只能依据下面给出的两类材料回答，不得使用其它知识，不得根据标题猜测：
【B 的摘要】来自 OpenAlex/Crossref/Semantic Scholar 的摘要原文；
【A 里引用 B 的句子】A 正文中引用 B 的位置，带页码。
输出两个部分，用中文，使用下面的标题：
## 这篇参考文献做了什么
2–4 句，只概括摘要里明确写了的内容（问题、方法、结果）。每个结论句末标注依据：（摘要）。
## 与本文的关系
说明 A 在哪里、为了什么引用 B：只依据“引用句”，每条句末标注（本文第 N 页）。引用句里没有说明用途时，写“引用处没有说明用途”，不要推测。
若摘要信息不足以支撑某个结论，就不要写这个结论。不要出现英文原句的大段照抄，用你自己的话概括。"""


def build_messages(ref: dict, res: dict, contexts: list[dict], paper_title: str) -> list[dict]:
    ctx_lines = "\n".join(f"- （本文第 {c['page']} 页）{c['sentence']}" for c in contexts) or "（没有找到本文引用它的句子）"
    user = (
        f"论文 A：{paper_title}\n\n"
        f"参考文献 B（原文条目）：{ref['raw'][:400]}\n"
        f"参考文献 B（查到的题录）：{res.get('title', '')}；{', '.join((res.get('authors') or [])[:6])}；{res.get('year') or ''}；{res.get('venue') or ''}\n\n"
        f"【B 的摘要】（来源：{res.get('abstract_source')}）\n{res['abstract'][:3500]}\n\n"
        f"【A 里引用 B 的句子】\n{ctx_lines}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


async def summarize_events(app: AppContext, paper_id: str, index: int) -> AsyncIterator[Event]:
    try:
        svc = AIService(app)
        paper = svc._paper(paper_id)
        ref = store.get_reference(app, paper_id, index)
        res = await resolver.resolve(app, paper_id, index)
        link = res.get("open_access_url") or res.get("url") or ref.get("url") or ""
        if not res.get("found"):
            yield "done", {"summary": None, "reason": "not_found", "message": "没有查到这篇参考文献，无法概括。" + (res.get("reason") or ""),
                           "link": ref.get("url") or "", "match": None, "model": None}
            return
        if not (res.get("abstract") or "").strip():
            yield "done", {"summary": None, "reason": "no_abstract", "message": "没有可用摘要，无法概括。可以去查看原文。" + (res.get("abstract_reason") or ""),
                           "link": link, "match": res.get("match"), "model": None}
            return
        provider, cfg = svc.provider()
        choice = choose_model(TaskType.SUMMARIZE, cfg)
        contexts = store.citing_contexts(app, paper_id, index)
        messages = build_messages(ref, res, contexts, paper["title"])
        sent = len(res["abstract"][:3500]) + sum(len(c["sentence"]) for c in contexts)
        usage_log.record_request(
            app, task_type=TaskType.SUMMARIZE.value, model=choice.model, paper_id=paper_id, passage_ords=[c["ord"] for c in contexts],
            chars_sent=sent, includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False,
                                       "abstract": True, "citing_context": bool(contexts)},
            summary=f"发送了参考文献 [{index}] 的摘要（{len(res['abstract'][:3500])} 字）和本文 {len(contexts)} 处引用句，用于概括",
        )
        match = res.get("match") or {}
        notes = []
        if match.get("level") == "low":
            notes.append("查到的题录与原文参考文献相似度不高，可能不是同一篇，请对照原文确认后再参考下面的概括。")
        yield "meta", {"model": choice.model, "match": match, "notes": notes, "basis": {"abstract_source": res.get("abstract_source"), "contexts": [{"ord": c["ord"], "page": c["page"]} for c in contexts]}}
        acc = {"text": "", "usage": Usage()}
        async for ev in svc._stream(provider, choice.model, messages, cfg, acc):
            yield ev
        out = acc["text"].strip()
        if not out:
            raise LLMError("provider_error", "模型没有返回概括，请重试。")
        usage = svc._log_usage(TaskType.SUMMARIZE, choice.model, acc["usage"], messages, out, paper_id)
        yield "done", {
            "summary": out, "model": choice.model, "usage": usage, "match": match, "notes": notes, "link": link,
            "label": "AI 概括，依据：摘要 + 本文引用处",
            "basis": {"abstract_source": res.get("abstract_source"), "contexts": [{"ord": c["ord"], "page": c["page"]} for c in contexts]},
        }
    except LLMError as err:
        yield llm_error_event(err)
    except ApiError as err:
        yield "error", {"code": err.code, "message": err.message}
