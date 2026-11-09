"""AI 调度中心：意图识别 → 上下文检索 → 模型路由 → 流式生成 → 证据核实 → 必要时升级模型。

对外产出 (事件名, 数据) 元组的异步迭代器，由 API 层转换为 SSE。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

from ..context import AppContext
from ..materials import grants as supp_grants
from ..materials import retrieval as supp_retrieval
from ..util import ApiError, utcnow
from . import attachments as attach_mod, cards, embeddings, evidence, prompts, retrieval, textcache, usage as usage_log, websearch
from .providers import LLMError, LLMProvider, Usage, build_provider, estimate_messages_tokens, estimate_tokens
from .router import TaskType, can_escalate, choose_model, classify
from .settings import SECRET_NAME, load_settings

Event = tuple[str, dict]
QUICK_INTENTS = {"explain", "summarize", "term"}
MAX_TRANSLATE_CHARS = 8000
MAX_THINKING_CHARS = 30000  # 卡片里最多保存这么多思考文字


@dataclass
class AskRequest:
    paper_id: str
    question: str = ""
    intent: str = "free"
    selection: dict | None = None
    chain_id: str | None = None
    save: bool | None = None
    # 调用方指定“对象”（例如思维导图节点），用于把卡片归到固定的分析链；不填则按选中内容推断
    object_type: str | None = None
    object_key: str | None = None
    object_label: str | None = None
    web: bool = False  # 论文里没有时，是否联网查资料补充
    conversation_id: str | None = None  # 属于哪个对话；不填就用最近的一个
    attachments: list[dict] | None = None  # 截图/图片、PDF、文本文件：[{name, mime, data(base64)}]
    edit_card_id: str | None = None  # 修改并重新回答这张卡片（只能是对话里最后一张）：原地改写，失败时还原


def llm_error_event(err: LLMError, card_id: str | None = None) -> Event:
    data = {"code": err.code, "message": err.message}
    if card_id:
        data["card_id"] = card_id
    return "error", data


class AIService:
    def __init__(self, app: AppContext):
        self.app = app

    # ---- 基础 ----
    def provider(self, cfg: dict | None = None) -> tuple[LLMProvider, dict]:
        cfg = cfg or load_settings(self.app)
        key = self.app.secrets.get(SECRET_NAME)
        if not key:
            raise LLMError("no_api_key", "尚未保存 API Key，请先在设置 → AI 模型中填写。", status=400)
        if not (cfg["fast_model"] or cfg["strong_model"]):
            raise LLMError("not_configured", "尚未配置模型名称，请先在设置 → AI 模型中填写。", status=400)
        prov = build_provider(
            cfg["provider_kind"], cfg["base_url"], key, timeout=cfg["timeout_s"], max_retries=cfg["max_retries"],
            transport=self.app.async_transport,
        )
        # 强模型暂时不可用（5xx/超时/连接错误）时自动改用快速模型，并如实告知；两个模型相同或缺一个时不回退
        if cfg["strong_model"] and cfg["fast_model"] and cfg["strong_model"] != cfg["fast_model"]:
            prov.fallbacks = {cfg["strong_model"]: cfg["fast_model"]}
        return prov, cfg

    def _paper(self, paper_id: str):
        with self.app.db.session() as conn:
            row = conn.execute(
                "SELECT id, title, language, has_pdf, "
                "(SELECT COUNT(*) FROM passages WHERE paper_id = papers.id) AS n_passages FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()
        if row is None:
            raise ApiError("not_found", "论文不存在", 404)
        return row

    def _log_usage(self, task: TaskType | str, model: str, u: Usage, messages: list[dict], text: str,
                   paper_id: str | None, job_id: str | None = None) -> dict:
        pt, ct, est = u.prompt_tokens, u.completion_tokens, u.estimated
        if not pt and not ct:
            pt = estimate_messages_tokens(messages)
            ct = estimate_tokens(text)
            est = True
        usage_log.record_usage(self.app, task.value if isinstance(task, TaskType) else task, model, pt, ct,
                               estimated=est, paper_id=paper_id, job_id=job_id)
        return {"prompt_tokens": pt, "completion_tokens": ct}

    async def _stream(self, provider: LLMProvider, model: str, messages: list[dict], cfg: dict, acc: dict
                      ) -> AsyncIterator[Event]:
        """流式生成；累计文本与用量到 acc（text / usage）。"""
        u = Usage()
        acc["dropped"] = 0
        async for ch in provider.stream(model, messages, temperature=cfg["temperature"], max_tokens=cfg["max_output_tokens"]):
            if ch.usage:
                u.prompt_tokens = max(u.prompt_tokens, ch.usage.prompt_tokens)
                u.completion_tokens = max(u.completion_tokens, ch.usage.completion_tokens)
            acc["dropped"] += ch.dropped
            if ch.thinking:
                acc["thinking"] = (acc.get("thinking") or "") + ch.thinking
                yield "thinking", {"text": ch.thinking}
            if ch.notice and provider.fallback_events:  # 强模型不可用，已自动改用快速模型：告知界面，并记下实际回答的模型
                acc["fallback"] = provider.fallback_events[-1]
                yield "stage", {"message": ch.notice}
            if ch.text:
                acc["text"] += ch.text
                yield "delta", {"text": ch.text}
        acc["usage"] = u

    # ---- 翻译 ----
    async def translate_events(self, paper_id: str | None, text: str, page: int | None, passage_ord: int | None,
                               force: bool = False, context_title: str | None = None) -> AsyncIterator[Event]:
        try:
            # 论文在文献库里：照常（带缓存、按位置找章节）；不在（发现页里的摘要）：只翻译这段文字，不缓存、不关联论文
            paper = self._paper(paper_id) if paper_id else {"title": (context_title or "").strip()}
            text = (text or "").strip()
            if not text:
                raise ApiError("bad_request", "没有要翻译的文字")
            if len(text) > MAX_TRANSLATE_CHARS:
                raise ApiError("bad_request", f"选中内容超过 {MAX_TRANSLATE_CHARS} 字，请分段翻译")
            letters = len(re.findall(r"[A-Za-z一-鿿]", text))
            if letters and len(re.findall(r"[一-鿿]", text)) / letters > 0.5:
                raise ApiError("bad_request", "选中内容已经是中文，无需翻译")
            provider, cfg = self.provider()
            choice = choose_model(TaskType.TRANSLATE, cfg)
            digest = hashlib.sha256(retrieval.reader.normalize(text).encode()).hexdigest()[:32]
            if not paper_id and not force:  # 发现页里的摘要：也存下来，翻译过就不再调用模型
                zh = textcache.get(self.app, "text", text)
                if zh:
                    yield "meta", {"model": "", "cached": True}
                    yield "done", {"translation": zh, "model": "", "cached": True, "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
                    return
            if paper_id and not force:
                with self.app.db.session() as conn:
                    hit = conn.execute(
                        "SELECT translation, model FROM translations WHERE paper_id=? AND text_hash=? AND target='zh'",
                        (paper_id, digest),
                    ).fetchone()
                if hit:
                    yield "meta", {"model": hit["model"], "cached": True}
                    yield "done", {"translation": hit["translation"], "model": hit["model"], "cached": True,
                                   "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
                    return
            section_title = None
            matches = (retrieval.reader.locate(self.app, paper_id, text, page) if (passage_ord is None) else []) if paper_id else []
            if paper_id and passage_ord is not None:
                section_title = retrieval.reader.get_passage(self.app, paper_id, passage_ord)["section_title"]
            elif matches:
                section_title = matches[0]["section_title"]
            messages = prompts.build_translate_messages(text, section_title, paper["title"])
            usage_log.record_request(
                self.app, task_type=TaskType.TRANSLATE.value, model=choice.model, paper_id=paper_id, passage_ords=[],
                chars_sent=len(text), includes={"question": False, "selection": True, "notes": False, "supplements": False, "code": False},
                summary=f"发送了选中的 {len(text)} 个字符用于翻译",
            )
            yield "meta", {"model": choice.model, "cached": False}
            acc = {"text": "", "usage": Usage()}
            async for ev in self._stream(provider, choice.model, messages, cfg, acc):
                yield ev
            out = acc["text"].strip()
            if not out:
                raise LLMError("provider_error", "模型没有返回译文，请重试。")
            u = self._log_usage(TaskType.TRANSLATE, choice.model, acc["usage"], messages, out, paper_id)
            if paper_id:
                with self.app.db.session() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO translations(paper_id, text_hash, target, source_text, translation, model, created_at) "
                        "VALUES (?,?, 'zh', ?,?,?,?)",
                        (paper_id, digest, text, out, choice.model, utcnow()),
                    )
            else:
                textcache.put_many(self.app, {textcache.key("text", text): out})
            yield "done", {"translation": out, "model": choice.model, "cached": False, "usage": u}
        except LLMError as err:
            yield llm_error_event(err)
        except ApiError as err:
            yield "error", {"code": err.code, "message": err.message}

    def cached_translation(self, paper_id: str, text: str) -> str | None:
        """这段文字在这篇论文里翻译过的话，直接给出译文（不调用模型，也不记用量）。"""
        text = (text or "").strip()
        if not text:
            return None
        digest = hashlib.sha256(retrieval.reader.normalize(text).encode()).hexdigest()[:32]
        with self.app.db.session() as conn:
            hit = conn.execute("SELECT translation FROM translations WHERE paper_id=? AND text_hash=? AND target='zh'", (paper_id, digest)).fetchone()
        return hit["translation"] if hit else None

    # ---- 研究问答 ----
    async def _plan_terms(self, provider: LLMProvider, cfg: dict, question: str, sel_text: str, title: str,
                          paper_id: str) -> list[str]:
        choice = choose_model(TaskType.QUERY_PLAN, {**cfg, "mode": "fast"})
        messages = prompts.build_query_plan_messages(question, sel_text, title)
        res = await provider.complete(choice.model, messages, temperature=0, max_tokens=200)
        self._log_usage(TaskType.QUERY_PLAN, choice.model, res.usage, messages, res.text, paper_id)
        m = re.search(r"\[.*\]", res.text, re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except ValueError:
            return []
        return [str(t).strip().lower() for t in arr if isinstance(t, str) and 2 <= len(t) <= 60][:8]

    async def _plan_web(self, provider: LLMProvider, cfg: dict, question: str, sel_text: str, title: str, paper_id: str) -> dict:
        """联网前让模型把问题整理成检索词（放在论文所在领域里理解）；失败就退回直接截取问题。"""
        choice = choose_model(TaskType.QUERY_PLAN, {**cfg, "mode": "fast"})
        messages = prompts.build_web_plan_messages(question, sel_text, title)
        res = await provider.complete(choice.model, messages, temperature=0, max_tokens=120)
        self._log_usage(TaskType.QUERY_PLAN, choice.model, res.usage, messages, res.text, paper_id)
        m = re.search(r"\{.*\}", res.text, re.S)
        if not m:
            return {}
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return {}
        return {k: str(d.get(k) or "").strip()[:80] for k in ("zh", "term", "en")}

    @staticmethod
    def _needs_escalation(parsed: evidence.ParsedAnswer, pack: retrieval.ContextPack) -> str | None:
        if not parsed.answer["direct"].strip():
            return "快速模型没有按要求的格式回答"
        if pack.passages and parsed.verification["status"] == "no_evidence":
            return "快速模型的回答缺少可核实的原文依据"
        return None

    async def ask_events(self, req: AskRequest) -> AsyncIterator[Event]:
        card_id: str | None = None
        snapshot: dict | None = None  # 修改重答时，这张卡片修改前的完整内容（失败/取消时还原）
        created_chain = False
        acc = {"text": "", "usage": Usage()}
        finalized = False
        try:
            paper = self._paper(req.paper_id)
            if not paper["n_passages"]:
                raise ApiError("bad_request", "这篇论文没有可检索的文字内容，暂时无法使用 AI 问答。")
            provider, cfg = self.provider()
            sel = req.selection if req.selection and (req.selection.get("text") or "").strip() else None
            sel_text = (sel or {}).get("text", "")
            task = classify(req.intent, req.question, len(sel_text))
            prepared = attach_mod.prepare(req.attachments)
            # 带图片的提问整个交给视觉模型（看得见图），没配视觉模型就如实说明
            choice = choose_model(TaskType.VISION, cfg) if prepared.images else choose_model(task, cfg)
            question = req.question.strip()
            # 思维导图节点等调用方（带 object_key）不是聊天：不属于任何对话（空串，不会被当成旧卡片收进“对话 1”），也不带前文
            if req.object_key:
                conv_id = ""
            else:
                with self.app.db.session() as conn:
                    conv_id = cards.resolve_conversation(conn, req.paper_id, req.conversation_id)
            if req.edit_card_id and not req.object_key:
                with self.app.db.session() as conn:
                    snapshot = cards.edit_target(conn, req.paper_id, req.edit_card_id)  # 不是最后一张、还在生成等情况在这里报错
                conv_id = snapshot["conversation_id"]
            history = cards.recent_history(self.app, conv_id, exclude_id=req.edit_card_id if snapshot else None)  # 这个对话的前几轮问答：新问题常是接着问的
            # 很短的追问（“为什么呢”“它是什么意思”）单独检索找不到东西：把上一个问题接上一起检索
            rq = question
            if history and len(question) < 20 and not sel_text:
                rq = f"{history[-1]['question']} {question}".strip()

            # 语义向量检索（多语言）：有索引时直接用问题向量检索，中文问题不再需要额外调模型生成英文检索词
            query_vec = await embeddings.embed_query(self.app, provider, cfg, req.paper_id, f"{rq}\n{sel_text[:400]}".strip())
            extra_terms: list[str] = []
            if query_vec is None and retrieval.has_cjk(rq) and paper["language"] != "zh":
                try:
                    extra_terms = await self._plan_terms(provider, cfg, rq, sel_text, paper["title"], req.paper_id)
                except LLMError:
                    extra_terms = []  # 检索词扩展只是增强，失败不影响主流程
            budget = 24000 if choice.tier == "strong" else 14000
            # 补充材料：只有用户授权后才会被检索和发送；未授权时只判断“这次是否需要”，并以事件提示用户
            supp_docs = supp_retrieval.list_docs(self.app, req.paper_id) if supp_grants.is_granted(self.app, req.paper_id) else []
            pack = retrieval.build_context(
                self.app, req.paper_id, question=rq or sel_text, selection=sel, task=task,
                budget_chars=budget, extra_terms=extra_terms,
                include_references=bool(re.search(r"参考文献|reference|citation|引用", question, re.I)),
                supplements=supp_docs, query_vec=query_vec,
            )
            web_items: list[dict] = []
            if req.web:
                try:
                    try:
                        web_plan = await self._plan_web(provider, cfg, rq, sel_text, paper["title"], req.paper_id)
                    except Exception:  # noqa: BLE001  整理检索词失败就直接用问题原文
                        web_plan = {}
                    web_items = await websearch.search(self.app, rq, sel_text, web_plan)
                except Exception:  # noqa: BLE001  联网只是补充：查不到不影响回答
                    web_items = []
            needs_supp = None if supp_docs else supp_retrieval.needs_permission(
                self.app, req.paper_id, question, sel_text, pack)

            # 和 AI 的对话默认都保存（包括选中文字后的“解释/总结/术语”），在论文详情页里能看到；调用方明确不要保存时才不存
            saved = req.save if req.save is not None else True
            sel_store = None
            if sel:
                sel_store = {"text": sel_text[:2000], "page": sel.get("page"), "passage_ords": pack.selection_ords}
            otype, okey, olabel = cards.object_key_for(sel, pack.selection_ords)
            if req.object_key:
                otype, okey, olabel = req.object_type or otype, req.object_key, req.object_label or olabel
            card_id = req.edit_card_id if snapshot else cards.new_id()
            with self.app.db.session() as conn:
                if snapshot:  # 修改重答：沿用原来的卡片和分析链，只换问题、清掉旧回答
                    chain_id = snapshot["chain_id"]
                    row = conn.execute("SELECT title FROM insight_chains WHERE id = ?", (chain_id,)).fetchone()
                    chain_title = row["title"] if row else ""
                    cards.reset_card_for_edit(
                        conn, card_id, question=question, intent=req.intent, selection=sel_store, task_type=task.value,
                        model=choice.model, attachments=prepared.names,
                    )
                else:
                    before = conn.execute("SELECT COUNT(*) FROM insight_chains WHERE paper_id = ?", (req.paper_id,)).fetchone()[0]
                    chain_id, chain_title = cards.resolve_chain(conn, req.paper_id, req.chain_id, otype, okey, olabel)
                    after = conn.execute("SELECT COUNT(*) FROM insight_chains WHERE paper_id = ?", (req.paper_id,)).fetchone()[0]
                    created_chain = after > before
                    cards.create_card(
                        conn, card_id=card_id, paper_id=req.paper_id, chain_id=chain_id, object_type=otype,
                        object_label=olabel, selection=sel_store, question=question, intent=req.intent,
                        task_type=task.value, model=choice.model, saved=saved, conversation_id=conv_id,
                        attachments=prepared.names,
                    )
                cards.touch_conversation(conn, conv_id, question or sel_text)

            n_supp = len(pack.supp_refs)
            includes = {"question": bool(question), "selection": bool(sel), "notes": False, "supplements": n_supp > 0, "code": False, "web": bool(req.web), "attachments": len(prepared.names)}
            summary = f"发送了 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）及章节大纲"
            if n_supp:
                summary += f"，其中 {n_supp} 段来自补充材料"
            if prepared:
                summary += f"，并附带 {len(prepared.names)} 个文件" + (f"（其中 {len(prepared.images)} 张图片发给了视觉模型）" if prepared.images else "")
            if history:
                summary += f"，并带上前 {len(history)} 轮对话的要点"
            if req.web:
                summary += f"；联网检索：把问题发送到维基百科和 OpenAlex，得到 {len(web_items)} 条资料"

            async def send_and_stream(ch) -> AsyncIterator[Event]:
                messages = prompts.build_ask_messages(pack, question, task, history, web_items, prepared)
                usage_log.record_request(
                    self.app, task_type=task.value, model=ch.model, paper_id=req.paper_id, passage_ords=pack.ords,
                    chars_sent=pack.chars_sent, includes=includes, summary=summary,
                )
                acc["text"], acc["usage"], acc["thinking"] = "", Usage(), ""
                acc.pop("fallback", None)
                async for ev in self._stream(provider, ch.model, messages, cfg, acc):
                    yield ev
                if not acc["text"].strip():
                    # 思考型模型把输出额度全花在内部思考上，正文一个字没写：放宽额度重来一次（用量照实累计）
                    first = acc["usage"]
                    limit = int(cfg["max_output_tokens"] or 4096)
                    boosted = {**cfg, "max_output_tokens": min(32000, max(16000, 4 * limit))}
                    if boosted["max_output_tokens"] <= limit:  # 额度本来就很大了，再试也一样
                        raise LLMError("provider_error", "模型没有给出回答（输出额度可能被内部思考用完了）。请重试，或在设置里换用不带长思考的模型。")
                    yield "stage", {"message": "模型把输出额度都用在了思考上，没有给出回答，正在放宽额度重试……"}
                    async for ev in self._stream(provider, ch.model, messages, boosted, acc):
                        yield ev
                    acc["usage"] = Usage(first.prompt_tokens + acc["usage"].prompt_tokens, first.completion_tokens + acc["usage"].completion_tokens)
                    if not acc["text"].strip():
                        raise LLMError("provider_error", "模型没有给出回答（输出额度可能被内部思考用完了）。请重试，或在设置里换用不带长思考的模型 / 调大输出上限。")
                acc["messages"] = messages

            yield "meta", {
                "card_id": card_id, "chain_id": chain_id, "chain_title": chain_title, "conversation_id": conv_id, "task_type": task.value,
                "model": choice.model, "escalated": False,
                "context": {"passage_ords": pack.ords, "passage_count": len(pack.passages), "chars_sent": pack.chars_sent,
                            "includes": includes,
                            "supplement_docs": [{"n": d["n"], "paper_id": d["paper_id"], "asset_id": d["asset_id"],
                                                 "label": d["label"]} for d in pack.supp_docs],
                            "supplement_passages": [{"doc": d, "ord": o} for d, o in pack.supp_refs]},
            }
            if needs_supp:
                yield "needs_supplement_permission", {"card_id": card_id, **needs_supp}
            used_model = choice.model
            fallback_notes: list[str] = []
            async for ev in send_and_stream(choice):
                yield ev
            if acc.get("fallback"):  # 强模型暂时不可用：回答实际来自备用（快速）模型，如实记录
                used_model = acc["fallback"]["to"]
                fallback_notes.append(acc["fallback"]["message"])
            total_usage = self._log_usage(task, used_model, acc["usage"], acc["messages"], acc["text"], req.paper_id)
            parsed = evidence.parse_answer(acc["text"], pack, task, web_items)

            reason = self._needs_escalation(parsed, pack) if can_escalate(cfg, choice) else None
            if reason:
                strong = choose_model(task, cfg, escalate=True)
                yield "escalate", {"reason": reason, "model": strong.model}
                async for ev in send_and_stream(strong):
                    yield ev
                used_model = strong.model
                if acc.get("fallback"):
                    used_model = acc["fallback"]["to"]
                    fallback_notes.append(acc["fallback"]["message"])
                u2 = self._log_usage(task, used_model, acc["usage"], acc["messages"], acc["text"], req.paper_id)
                total_usage = {k: total_usage[k] + u2[k] for k in total_usage}
                parsed = evidence.parse_answer(acc["text"], pack, task, web_items)

            # 论文里没有这一点、模型又没另写“论文之外”：不能就这样停在“论文没有解释”，补一轮用自己的知识和网络资料回答
            if not parsed.answer.get("outside") and evidence.INSUFFICIENT_RE.search(parsed.answer.get("direct") or ""):
                yield "stage", {"message": "论文里没有写这一点，正在用 AI 自己的知识" + ("和联网查到的资料" if web_items else "") + "补充……"}
                try:
                    extra = await self._outside_answer(provider, cfg, choice, task, question, sel_text, paper["title"], history, web_items, req.paper_id)
                except LLMError:
                    extra = ""
                if extra:
                    parsed.answer["outside"] = extra
                    parsed.answer["web"] = websearch.cited_web(extra, web_items)
                    parsed.verification["notes"].append(
                        "“论文之外”的内容来自模型自身知识" + ("和联网查到的资料" if parsed.answer["web"] else "") + "，不是论文原文，请自行判断。")

            for note in dict.fromkeys(fallback_notes):
                parsed.verification["notes"].append(note)

            if acc.get("dropped"):  # 有数据帧无法解析被丢弃：回答可能缺了一段，必须如实告知
                parsed.verification["notes"].append(
                    f"流式输出中有 {acc['dropped']} 个数据块无法解析，回答内容可能不完整，建议重新生成。")
                if parsed.verification["status"] in ("verified", "unverified"):
                    parsed.verification["status"] = "partial"

            if acc.get("thinking"):  # 模型的思考过程跟着卡片保存，界面里可以展开看
                parsed.answer["thinking"] = acc["thinking"][:MAX_THINKING_CHARS]
            with self.app.db.session() as conn:
                cards.finalize_card(
                    conn, card_id, status="complete", model=used_model, raw=acc["text"], answer=parsed.answer,
                    evidence=parsed.evidence, verification=parsed.verification, usage=total_usage, task_type=task.value,
                )
            finalized = True
            yield "done", {"card": cards.get_card(self.app, card_id)}
        except LLMError as err:
            yield llm_error_event(err, card_id)
            await self._abandon_card(card_id, acc["text"], created_chain, req.paper_id, failed=True, snapshot=snapshot)
            finalized = True
        except ApiError as err:
            yield "error", {"code": err.code, "message": err.message, **({"card_id": card_id} if card_id else {})}
            await self._abandon_card(card_id, acc["text"], created_chain, req.paper_id, failed=True, snapshot=snapshot)
            finalized = True
        finally:
            if card_id and not finalized:  # 客户端中断（GeneratorExit / CancelledError）：用户点了“停止生成”
                partial = acc["text"]
                if partial.strip():
                    # 已经输出的内容要留下：当作一张卡片保存，如实标注是中途停止的
                    try:
                        stopped = evidence.parse_answer(partial, pack, task, web_items)
                        stopped.verification["notes"].append("你中途停止了生成，这条回答可能不完整。")
                        if acc.get("thinking"):
                            stopped.answer["thinking"] = acc["thinking"][:MAX_THINKING_CHARS]
                        if stopped.verification["status"] == "verified":
                            stopped.verification["status"] = "partial"
                        with self.app.db.session() as conn:
                            cards.finalize_card(
                                conn, card_id, status="complete", model=choice.model, raw=partial, answer=stopped.answer,
                                evidence=stopped.evidence, verification=stopped.verification,
                                usage={"prompt_tokens": 0, "completion_tokens": 0}, task_type=task.value,
                            )
                    except Exception:  # noqa: BLE001  解析失败也不能丢掉已经输出的文字
                        await self._abandon_card(card_id, partial, created_chain, req.paper_id, failed=True, snapshot=snapshot)
                else:
                    await self._abandon_card(card_id, "", created_chain, req.paper_id, failed=False, snapshot=snapshot)

    async def _outside_answer(self, provider, cfg, choice, task, question: str, sel_text: str, title: str,
                              history: list[dict], web_items: list[dict], paper_id: str) -> str:
        """论文里没有的内容：直接用模型自己的知识（和联网资料）回答，不带论文摘录。"""
        msgs: list[dict] = [{"role": "system", "content": prompts.OUTSIDE_SYSTEM}]
        for h in history:
            msgs.append({"role": "user", "content": h["question"] or "（只选中了文字）"})
            msgs.append({"role": "assistant", "content": h["answer"]})
        parts = [f"【正在读的论文】{title}"]
        if web_items:
            parts.append("【网络资料】\n" + websearch.render_for_prompt(web_items))
        if sel_text.strip():
            parts.append(f"【用户选中的内容】\n{sel_text.strip()[:1500]}")
        parts.append(f"【问题】{question or sel_text[:200]}")
        msgs.append({"role": "user", "content": "\n\n".join(parts)})
        res = await provider.complete(choice.model, msgs, temperature=cfg["temperature"], max_tokens=cfg["max_output_tokens"])
        self._log_usage(task, choice.model, res.usage, msgs, res.text, paper_id)
        return re.sub(r"\[P\d+\]", "", res.text or "").strip()

    async def _abandon_card(self, card_id: str | None, partial: str, created_chain: bool, paper_id: str, *, failed: bool,
                            snapshot: dict | None = None) -> None:
        if not card_id:
            return
        with self.app.db.session() as conn:
            if snapshot:  # 修改重答没成：原来的问题和回答原样还原，不留半截内容，也不删卡片
                cards.restore_card(conn, snapshot)
            elif failed and partial.strip():
                cards.finalize_card(conn, card_id, status="failed", model="", raw=partial, answer=None, evidence=[],
                                    verification=None, usage={"prompt_tokens": 0, "completion_tokens": 0}, task_type="")
                conn.execute("UPDATE insight_cards SET saved = 0 WHERE id = ?", (card_id,))
            else:
                conn.execute("DELETE FROM insight_cards WHERE id = ?", (card_id,))
                conn.execute("DELETE FROM insight_chains WHERE paper_id = ? AND id NOT IN (SELECT DISTINCT chain_id FROM insight_cards)", (paper_id,))

    # ---- 连接测试 ----
    async def test_connection(self) -> dict:
        provider, cfg = self.provider()
        provider.fallbacks = {}  # 连接测试要如实报告每个模型自己的状态，不能被回退掩盖
        out: dict = {"fast": None, "strong": None, "vision": None}
        results: dict[str, dict] = {}
        for tier in ("fast", "strong", "vision"):
            model = cfg[f"{tier}_model"]
            if not model:
                continue
            if model in results:
                out[tier] = results[model]
                continue
            t0 = time.perf_counter()
            try:
                if tier == "vision":  # 视觉模型：发一张小图，确认图片输入可用
                    from .providers import image_part
                    from ..vision.images import tiny_test_png

                    msg = [{"role": "user", "content": [
                        {"type": "text", "text": "The image is a plain gray square. Reply with the single word: OK"},
                        image_part(tiny_test_png())]}]
                else:
                    msg = [{"role": "user", "content": "Reply with the single word: OK"}]
                await provider.complete(model, msg, temperature=0, max_tokens=16)
                r = {"ok": True, "model": model, "latency_ms": int((time.perf_counter() - t0) * 1000), "error": None}
            except LLMError as err:
                r = {"ok": False, "model": model, "latency_ms": int((time.perf_counter() - t0) * 1000), "error": err.message}
            results[model] = r
            out[tier] = r
        return out
