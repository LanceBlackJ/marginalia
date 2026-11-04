from __future__ import annotations

import json

import httpx
import pytest

from paperdesk.ai import evidence, retrieval
from paperdesk.ai.providers import (
    AnthropicProvider, LLMError, OpenAICompatibleProvider, build_provider,
)
from paperdesk.ai.router import TaskType, can_escalate, choose_model, classify
from paperdesk.util import ApiError


# ---------------- 路由 ----------------
def test_classify_and_choose_model():
    assert classify("explain", "", 50) == TaskType.EXPLAIN
    assert classify("free", "为什么作者要提出这个模块？", 0) == TaskType.MOTIVATION
    assert classify("free", "这个公式是怎么推导的", 0) == TaskType.FORMULA
    assert classify("free", "表 2 的消融实验说明了什么", 0) == TaskType.EXPERIMENT
    assert classify("free", "What is attention?", 0) == TaskType.TERM
    assert classify("free", "随便聊聊这篇文章的整体情况以及它和其他工作的关系是什么样的", 0) == TaskType.FREE_QA

    cfg = {"fast_model": "f", "strong_model": "s", "mode": "auto"}
    assert choose_model(TaskType.TRANSLATE, cfg).model == "f"
    assert choose_model(TaskType.MOTIVATION, cfg).model == "s"
    assert choose_model(TaskType.TRANSLATE, {**cfg, "mode": "strong"}).model == "s"
    assert choose_model(TaskType.MOTIVATION, {**cfg, "mode": "fast"}).model == "f"
    assert choose_model(TaskType.EXPLAIN, cfg, escalate=True).model == "s"
    # 只配了一个模型时，所有任务都用它
    assert choose_model(TaskType.MOTIVATION, {"fast_model": "only", "strong_model": "", "mode": "auto"}).model == "only"
    with pytest.raises(ApiError) as e:
        choose_model(TaskType.EXPLAIN, {"fast_model": "", "strong_model": "", "mode": "auto"})
    assert e.value.code == "not_configured"
    assert can_escalate(cfg, choose_model(TaskType.EXPLAIN, cfg))
    assert not can_escalate({**cfg, "mode": "fast"}, choose_model(TaskType.EXPLAIN, {**cfg, "mode": "fast"}))


# ---------------- 提供商适配 ----------------
def _provider(handler, cls=OpenAICompatibleProvider, retries=2):
    return cls("https://x.test/v1", "sk-secret", timeout=5, max_retries=retries, transport=httpx.MockTransport(handler))


def _sse_ok(text: str, pt: int = 1, ct: int = 1) -> httpx.Response:
    """一个正常结束的 OpenAI 兼容流式响应。"""
    frames = [
        json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False),
        json.dumps({"choices": [], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}),
        "[DONE]",
    ]
    body = "".join(f"data: {f}\n\n" for f in frames)
    return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})


async def test_openai_retries_on_429_then_succeeds(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, text="slow down")
        return _sse_ok("hi", 3, 1)

    p = _provider(handler)
    monkeypatch.setattr(p, "_sleep_backoff", lambda attempt: _noop())
    res = await p.complete("m", [{"role": "user", "content": "x"}])
    assert res.text == "hi" and res.usage.prompt_tokens == 3 and len(calls) == 3


async def _noop():
    return None


async def test_error_mapping_never_leaks_key():
    p = _provider(lambda req: httpx.Response(401, text="bad key sk-secret"))
    with pytest.raises(LLMError) as e:
        await p.complete("m", [{"role": "user", "content": "x"}])
    assert e.value.code == "auth_failed" and "sk-secret" not in e.value.message

    p = _provider(lambda req: httpx.Response(400, text="maximum context length is 8192 tokens"))
    with pytest.raises(LLMError) as e:
        await p.complete("m", [{"role": "user", "content": "x"}])
    assert e.value.code == "context_too_long"

    def boom(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMError) as e:
        await _provider(boom, retries=0).complete("m", [{"role": "user", "content": "x"}])
    assert e.value.code == "network"


async def test_openai_adapts_unsupported_params():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, text="Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        if "temperature" in body:
            return httpx.Response(400, text="'temperature' does not support 0.2 with this model")
        return _sse_ok("ok")

    res = await _provider(handler).complete("o-model", [{"role": "user", "content": "x"}], temperature=0.2, max_tokens=50)
    assert res.text == "ok"
    assert "max_completion_tokens" in seen[-1] and "max_tokens" not in seen[-1] and "temperature" not in seen[-1]


async def test_openai_stream_parses_deltas_and_usage():
    sse = (
        'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    p = _provider(lambda req: httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"}))
    text, usage = "", None
    async for ch in p.stream("m", [{"role": "user", "content": "x"}]):
        text += ch.text
        usage = ch.usage or usage
    assert text == "Hello" and usage.prompt_tokens == 7 and usage.completion_tokens == 2


async def test_anthropic_request_shape_and_stream():
    seen = {}

    def handler(req):
        seen["url"], seen["headers"], seen["body"] = str(req.url), dict(req.headers), json.loads(req.content)
        sse = (
            'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":11,"output_tokens":1}}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你"}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}\n\n'
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n'
        )
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    p = AnthropicProvider("https://api.anthropic.com", "sk-ant", timeout=5, max_retries=0, transport=httpx.MockTransport(handler))
    text, pt, ct = "", 0, 0
    async for ch in p.stream("claude-x", [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}], max_tokens=100):
        text += ch.text
        if ch.usage:
            pt, ct = max(pt, ch.usage.prompt_tokens), max(ct, ch.usage.completion_tokens)
    assert text == "你好" and (pt, ct) == (11, 5)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant" and seen["headers"]["anthropic-version"]
    assert seen["body"]["system"] == "SYS" and seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert build_provider("anthropic", "https://a", "k", timeout=1, max_retries=0).__class__ is AnthropicProvider


# ---------------- 证据核实 ----------------
def _pack(ords):
    ps = [retrieval.CtxPassage(o, 1 + o // 3, "paragraph", "3 Method", f"Passage {o}. The module uses attention over neighbours.", [0, 0, 10, 10]) for o in ords]
    return retrieval.ContextPack("T", 2023, [], [], ps, None, [], [])


def test_evidence_parsing_and_hallucinated_citation_detection():
    md = (
        "## 直接回答\n模块使用注意力 [P3, P5]，并且效果更好 [P99]。\n"
        "## 详细解释\n更多细节 [P5]。\n"
        "## 依据\n- [P3] 说明了机制\n- [P99] 说明了效果\n"
        "## AI 推断\n- 作者可能想避免偏差（依据：[P3][P5]）\n"
        "## 后续问题\n- 为什么不用单一信号？\n- 消融结果如何？"
    )
    parsed = evidence.parse_answer(md, _pack([3, 5, 7]))
    assert "[P3][P5]" in parsed.answer["direct"]  # 引用被规范化
    assert [e["ord"] for e in parsed.evidence] == [3, 5]  # 编造的 P99 不会进入证据
    assert parsed.verification["status"] == "partial" and parsed.verification["invalid_citations"] == [99]
    assert all("Passage" in e["quote"] for e in parsed.evidence)  # quote 来自真实段落
    assert parsed.answer["inferences"][0]["basis_ords"] == [3, 5]
    assert "依据" not in parsed.answer["inferences"][0]["text"]
    assert len(parsed.answer["follow_ups"]) == 2
    assert [n["ord"] for n in parsed.answer["evidence_notes"]] == [3]  # P99 的说明被丢弃


def test_evidence_no_citation_and_unstructured_output():
    parsed = evidence.parse_answer("就是这样的。", _pack([1, 2]))
    assert parsed.answer["direct"] == "就是这样的。"
    assert parsed.verification["status"] == "no_evidence" and parsed.evidence == []
    # 没有可引用的上下文时，不应误报“缺少依据”
    empty = evidence.parse_answer("## 直接回答\n摘录里没有足够信息确认。", _pack([]))
    assert empty.verification["status"] != "no_evidence"


def test_best_excerpt_prefers_relevant_sentence():
    text = ("Intro sentence about nothing. " * 12) + "The temporal module attends to load history of each node. " + ("Other filler. " * 12)
    out = evidence.best_excerpt(text, "temporal module load history", limit=140)
    assert "temporal module" in out and len(out) <= 142


# ---------------- 检索 ----------------
def test_retrieval_combines_selection_search_and_frame(imported):
    api, pid = imported
    sel = {"text": "The routing score of node i is defined as", "page": 2}
    pack = retrieval.build_context(api.ctx, pid, question="how is the routing score combined with attention", selection=sel,
                                   task=TaskType.METHOD, budget_chars=6000)
    texts = " ".join(p.text for p in pack.passages)
    assert pack.selection_ords and "routing score of node i" in texts
    reasons = {r for p in pack.passages for r in p.reasons}
    assert {"selection", "search"} <= reasons
    assert any("Dual Attention Routing" in p.text and "frame" in p.reasons for p in pack.passages)  # 摘要作为全局框架
    assert "Method" in pack.render_outline()
    assert all(p.kind != "reference" for p in pack.passages)
    assert pack.ords == sorted(pack.ords)

    # 预算约束
    small = retrieval.build_context(api.ctx, pid, question="attention", selection=None, task=TaskType.FREE_QA, budget_chars=600)
    assert small.chars_sent < 1500

    # 图/表引用解析：问“Figure 1”应带上图注
    fig = retrieval.build_context(api.ctx, pid, question="解释 Figure 1 的内容", selection=None, task=TaskType.FIGURE)
    assert any(p.kind == "caption" and p.text.startswith("Figure 1") for p in fig.passages)

    # 中文问题 + 模型给出的英文检索词
    zh = retrieval.build_context(api.ctx, pid, question="时间注意力模块是做什么的", selection=None, task=TaskType.METHOD,
                                 extra_terms=["temporal attention", "load history"])
    assert any("temporal" in p.text.lower() for p in zh.passages)


# ---------------- 设置 / 密钥 ----------------
def test_ai_settings_validation_and_secret_handling(api):
    s = api.get("/settings/ai").json()
    assert s["configured"] is False and s["has_api_key"] is False and s["mode"] == "auto"
    assert api.put("/settings/ai", {"mode": "weird"}).status_code == 400
    assert api.put("/settings/ai", {"base_url": "ftp://x"}).status_code == 400
    s = api.put("/settings/ai", {"fast_model": " gpt-fast ", "base_url": "https://api.example.com/v1/", "timeout_s": 9999,
                                  "price_per_mtok": {"gpt-fast": {"input": 1, "output": 2}}}).json()
    assert s["fast_model"] == "gpt-fast" and s["base_url"] == "https://api.example.com/v1" and s["timeout_s"] == 600
    assert s["configured"] is False  # 还没有 Key
    assert api.put("/settings/ai/secret", {"api_key": "  "}).status_code == 400
    api.put("/settings/ai/secret", {"api_key": "sk-very-secret"})
    s = api.get("/settings/ai").json()
    assert s["has_api_key"] and s["configured"]
    assert "sk-very-secret" not in json.dumps(s)
    # 绝不落盘
    with api.ctx.db.session() as conn:
        dump = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM settings"))
    assert "sk-very-secret" not in dump
    assert api.delete("/settings/ai/secret").json()["has_api_key"] is False
    assert api.get("/settings/ai").json()["configured"] is False


def test_connection_test(imported):
    api, _ = imported
    r = api.post("/settings/ai/test").json()
    assert r["fast"]["ok"] and r["fast"]["model"] == "fast-m" and r["strong"]["ok"] and r["strong"]["model"] == "strong-m"
    api.fake.fail_next.append((401, "nope"))
    r = api.post("/settings/ai/test").json()
    assert r["fast"]["ok"] is False and "API Key" in r["fast"]["error"]


# ---------------- 问答 ----------------
def test_ask_without_configuration_returns_guiding_error(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "这篇论文做了什么？"})
    assert [n for n, _ in ev] == ["error"] and ev[0][1]["code"] == "no_api_key"
    api.put("/settings/ai/secret", {"api_key": "k"})
    api.put("/settings/ai", {"fast_model": "", "strong_model": ""})  # 默认模型被用户清空后，应提示去配置模型名
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "这篇论文做了什么？"})
    assert ev[0][1]["code"] == "not_configured"
    assert api.post("/ai/ask", {"paper_id": pid, "question": "  "}).status_code == 400


def test_ask_flow_produces_verified_card(imported):
    api, pid = imported
    sel = {"text": "The routing score of node i is defined as s_i = softmax", "page": 2}
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "为什么作者要这样组合两种注意力？", "intent": "motivation", "selection": sel})
    names = [n for n, _ in ev]
    assert names[0] == "meta" and names[-1] == "done" and names.count("delta") > 3
    meta, card = ev[0][1], ev[-1][1]["card"]
    assert meta["task_type"] == "design_motivation" and meta["model"] == "strong-m" and meta["escalated"] is False
    assert meta["context"]["passage_count"] == len(meta["context"]["passage_ords"]) > 0
    assert meta["context"]["includes"] == {"question": True, "selection": True, "notes": False, "supplements": False, "code": False, "web": False, "attachments": 0}
    streamed = "".join(d["text"] for n, d in ev if n == "delta")
    assert streamed == card["raw_markdown"]

    assert card["status"] == "complete" and card["saved"] is True and card["id"] == meta["card_id"]
    assert card["verification"]["status"] == "verified" and card["verification"]["invalid_citations"] == []
    assert card["evidence"] and all(e["quote"] and len(e["bbox"]) == 4 and e["page"] >= 1 for e in card["evidence"])
    assert card["answer"]["inferences"] and card["answer"]["follow_ups"]
    assert card["selection"]["passage_ords"], "选中文字应被映射到真实段落"
    assert card["usage"]["prompt_tokens"] > 0

    # 发给模型的内容：系统约束 + 论文摘录，不含其它论文/笔记
    sent = api.fake.requests[-1]["body"]
    user = [m for m in sent["messages"] if m["role"] == "user"][0]["content"]
    assert "【论文摘录】" in user and "【用户选中的内容】" in user and "[P" in user
    assert api.fake.requests[-1]["headers"]["authorization"] == "Bearer sk-test-secret"

    # 隐私日志与用量
    priv = api.get("/privacy/requests").json()["items"]
    assert priv[0]["passage_count"] == meta["context"]["passage_count"] and priv[0]["paper_title"]
    assert "原文" in priv[0]["summary"]
    usage = api.get("/usage").json()
    assert usage["calls"] >= 1 and usage["prompt_tokens"] > 0 and usage["estimated_cost"] is None

    # 持久化：卡片、分析链
    assert api.get(f"/papers/{pid}/cards").json()["items"][0]["id"] == card["id"]
    chains = api.get(f"/papers/{pid}/chains").json()["items"]
    assert len(chains) == 1 and chains[0]["card_count"] == 1 and chains[0]["id"] == card["chain_id"]


def test_invalid_citation_is_flagged_not_trusted(imported):
    api, pid = imported
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "INVALID_CITE 这个方法是什么", "intent": "free"})
    card = ev[-1][1]["card"]
    assert card["verification"]["status"] == "partial" and card["verification"]["invalid_citations"] == [9999]
    assert 9999 not in [e["ord"] for e in card["evidence"]]


def test_chain_grouping_and_all_conversations_saved_by_default(imported):
    api, pid = imported
    sel = {"text": "The routing score of node i is defined as", "page": 2}
    # 和 AI 的对话默认都保存（包括“解释”这类选中文字后的快捷提问）
    c1 = api.sse("/ai/ask", {"paper_id": pid, "intent": "explain", "selection": sel})[-1][1]["card"]
    assert c1["saved"] is True and c1["task_type"] == "explain"
    assert len(api.get(f"/papers/{pid}/chains").json()["items"]) == 1
    # 调用方明确不保存时才是临时卡片，不产生分析链条目
    tmp = api.sse("/ai/ask", {"paper_id": pid, "intent": "term", "selection": {"text": "routing score", "page": 2}, "save": False})[-1][1]["card"]
    assert tmp["saved"] is False
    # 同一研究对象的深入提问归入同一条链
    c2 = api.sse("/ai/ask", {"paper_id": pid, "intent": "formula", "question": "解释这个公式", "selection": sel})[-1][1]["card"]
    c3 = api.sse("/ai/ask", {"paper_id": pid, "intent": "motivation", "question": "为什么这样设计", "selection": sel})[-1][1]["card"]
    assert c1["chain_id"] == c2["chain_id"] == c3["chain_id"]
    chains = api.get(f"/papers/{pid}/chains").json()["items"]
    assert len(chains) == 1 and chains[0]["card_count"] == 3
    # 自由提问进入“自由提问”链；重命名与删除
    free = api.sse("/ai/ask", {"paper_id": pid, "question": "这篇论文整体讲了什么，和已有工作有何不同？"})[-1][1]["card"]
    assert free["chain_id"] != c1["chain_id"]
    assert api.patch(f"/chains/{free['chain_id']}", {"title": "我的问题"}).json()["title"] == "我的问题"
    assert api.delete(f"/cards/{free['id']}").json()["ok"]
    assert all(c["id"] != free["chain_id"] for c in api.get(f"/papers/{pid}/chains").json()["items"])
    assert api.delete(f"/chains/{c1['chain_id']}").json()["ok"]
    assert [c for c in api.get(f"/papers/{pid}/cards").json()["items"] if c["saved"]] == []


def test_fast_model_without_evidence_escalates_to_strong(imported):
    api, pid = imported
    api.put("/settings/ai", {"fast_model": "weak-fast"})
    sel = {"text": "The routing score of node i is defined as", "page": 2}
    ev = api.sse("/ai/ask", {"paper_id": pid, "intent": "explain", "question": "NOCITE 解释一下", "selection": sel})
    names = [n for n, _ in ev]
    assert "escalate" in names and names[-1] == "done"
    esc = dict(ev)["escalate"]
    assert esc["model"] == "strong-m" and "依据" in esc["reason"]
    card = ev[-1][1]["card"]
    assert card["model"] == "strong-m" and card["verification"]["status"] == "verified"
    # 升级后两次调用都被计入用量
    models = {m["model"] for m in api.get("/usage").json()["by_model"]}
    assert {"weak-fast", "strong-m"} <= models


def test_cjk_question_uses_fast_query_planning(imported):
    api, pid = imported
    api.fake.requests.clear()
    api.sse("/ai/ask", {"paper_id": pid, "question": "时间注意力模块的作用是什么？"})
    first = api.fake.requests[0]["body"]
    assert first["model"] == "fast-m" and "检索关键词生成器" in first["messages"][0]["content"]
    assert api.get("/usage").json()["by_task_type"]  # 检索词规划也计入用量
    assert any(t["task_type"] == "query_plan" for t in api.get("/usage").json()["by_task_type"])


def test_provider_error_is_reported_and_card_cleaned_up(imported):
    api, pid = imported
    api.put("/settings/ai", {"max_retries": 0})
    api.fake.fail_next.append((429, "rate limit"))
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "attention", "intent": "free"})
    assert ev[-1][0] == "error" and ev[-1][1]["code"] in ("rate_limited",)
    assert api.get(f"/papers/{pid}/cards").json()["items"] == []
    assert api.get(f"/papers/{pid}/chains").json()["items"] == []


def test_scanned_pdf_ask_explains_limitation(api, scanned_pdf):
    pid = api.import_pdf(scanned_pdf)["paper_id"]
    api.configure_ai()
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "总结一下"})
    assert ev[0][0] == "error" and "没有可检索的文字" in ev[0][1]["message"]


# ---------------- 翻译 ----------------
def test_translate_stream_and_cache(imported):
    api, pid = imported
    text = "The topology module computes attention weights over neighbouring nodes."
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": text, "page": 1})
    assert [n for n, _ in ev][0] == "meta" and ev[0][1]["cached"] is False and ev[0][1]["model"] == "fast-m"
    done = ev[-1][1]
    assert ev[-1][0] == "done" and done["translation"] == "【译文】" + text
    assert "".join(d["text"] for n, d in ev if n == "delta") == done["translation"]
    n_calls = len(api.fake.requests)
    ev2 = api.sse("/ai/translate", {"paper_id": pid, "text": text, "page": 1})
    assert [n for n, _ in ev2] == ["meta", "done"] and ev2[1][1]["cached"] is True
    assert len(api.fake.requests) == n_calls  # 命中缓存不再调用模型
    ev3 = api.sse("/ai/translate", {"paper_id": pid, "text": text, "force": True})
    assert ev3[-1][1]["cached"] is False and len(api.fake.requests) == n_calls + 1
    # 翻译不创建研究卡片
    assert api.get(f"/papers/{pid}/cards").json()["items"] == []
    assert api.sse("/ai/translate", {"paper_id": pid, "text": "这已经是中文了，不需要翻译。"})[0][1]["code"] == "bad_request"
    sent = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "translate" and i["passage_count"] == 0 for i in sent)


def test_translate_without_a_library_paper_works_and_is_saved_locally(imported):
    """发现页里的论文还没进文献库：只翻译这段文字，不关联任何论文；译文存在本机缓存里，翻译过的不再调用模型。"""
    api, _pid = imported
    text = "The emergence of AI agents represents a significant milestone in artificial intelligence."
    ev = api.sse("/ai/translate", {"text": text, "context_title": "AI Agents and Agentic Systems"})
    assert ev[0][0] == "meta" and ev[-1][0] == "done" and ev[-1][1]["translation"] == "【译文】" + text
    n_calls = len(api.fake.requests)
    ev2 = api.sse("/ai/translate", {"text": text})
    assert ev2[-1][1]["cached"] is True and len(api.fake.requests) == n_calls  # 翻译过的直接用存下来的
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0
    sent = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "translate" and i["paper_id"] is None for i in sent)  # 发送记录照常写
    assert api.sse("/ai/translate", {"text": "这已经是中文了，不需要翻译。"})[0][1]["code"] == "bad_request"


# ---------------- 论文整体分析 ----------------
def test_paper_analysis_job(imported):
    api, pid = imported
    assert api.get(f"/papers/{pid}/analysis").json()["status"] == "none"
    job_id = api.post(f"/papers/{pid}/analysis/run").json()["job_id"]
    job = api.wait_job(job_id)
    assert job["status"] == "completed", job
    a = api.get(f"/papers/{pid}/analysis").json()
    assert a["status"] == "ready" and a["model"] == "strong-m"
    assert set(a["content"]) == {"background", "motivation", "challenges", "method", "modules",
                                 "experiment_design", "results", "conclusion", "limitations"}
    assert a["content"]["method"]["evidence_ords"] and a["evidence"]
    assert a["verification"]["status"] == "verified"
    assert a["content"]["limitations"]["evidence_ords"] == []  # 推断类字段不强行附证据
    assert api.get(f"/papers/{pid}").json()["analysis_status"] == "ready"


def test_analysis_requires_configuration_and_text(api, sample_pdf, scanned_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    r = api.post(f"/papers/{pid}/analysis/run")
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"
    api.configure_ai()
    sid = api.import_pdf(scanned_pdf)["paper_id"]
    assert api.post(f"/papers/{sid}/analysis/run").status_code == 400


def test_auto_analyze_after_import_when_configured(api, sample_pdf):
    api.configure_ai()
    item = api.import_pdf(sample_pdf)
    pid = item["paper_id"]
    import time

    deadline = time.time() + 20
    status = "none"
    while time.time() < deadline:
        status = api.get(f"/papers/{pid}").json()["analysis_status"]
        if status in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert status == "ready"
    api.put("/settings/ai", {"auto_analyze": False})


def test_analysis_failure_is_recorded(imported):
    api, pid = imported
    api.put("/settings/ai", {"max_retries": 0})
    api.fake.fail_next.append((401, "bad key"))
    job = api.wait_job(api.post(f"/papers/{pid}/analysis/run").json()["job_id"])
    assert job["status"] == "failed" and "API Key" in job["error"]
    a = api.get(f"/papers/{pid}/analysis").json()
    assert a["status"] == "failed" and a["content"] is None and a["error"]


# ---------------- 真实网关暴露的兼容问题 ----------------
def test_think_filter_handles_tags_split_across_chunks():
    from paperdesk.ai.providers import ThinkFilter, strip_think

    def run(chunks):
        f = ThinkFilter()
        out = "".join(f.feed(c) for c in chunks) + f.flush()
        return out

    assert run(["<think>想一想</think>\n\n答案是 2。"]) == "答案是 2。"
    # 标签被拆成多块
    assert run(["<thi", "nk>内部", "推理</th", "ink>\n结", "果"]) == "结果"
    # 没有思考块时原样输出（包含看起来像标签前缀的普通文本）
    assert run(["a < b ", "and <t", "ag> c"]) == "a < b and <tag> c"
    # 思考块未闭合：不泄露思考内容
    assert run(["前文<think>一直在想"]) == "前文"
    # 只有结束标签的模板：非流式路径可以剥离
    assert strip_think("推理过程…</think>最终答案") == "最终答案"
    assert strip_think("<think>x</think>\n\n正文") == "正文"


async def test_complete_aggregates_a_stream_and_filters_think():
    """complete() 内部走流式再聚合（部分网关的非流式路由会挂起），并过滤 <think> 内容。"""
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"<think>先想"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"想</think>"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":4}}\n\n'
        "data: [DONE]\n\n"
    )
    p = _provider(lambda req: httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"}))
    res = await p.complete("m", [{"role": "user", "content": "x"}])
    assert res.text == "OK" and res.usage.prompt_tokens == 9 and res.usage.completion_tokens == 4


async def test_stream_hides_think_blocks_and_ignores_reasoning_content():
    sse = (
        'data: {"choices":[{"delta":{"reasoning_content":"内部推理不应出现"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"<think>推理</think>"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"正文"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
        "data: [DONE]\n\n"
    )
    p = _provider(lambda req: httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"}))
    text, usage = "", None
    async for ch in p.stream("m", [{"role": "user", "content": "x"}]):
        text += ch.text
        usage = ch.usage or usage
    assert text == "正文" and usage.completion_tokens == 3


# ---------------- 真实模型暴露的输出变体 ----------------
def test_citation_format_variants_are_normalized():
    cases = {
        "[依据：P5]": "[P5]", "（依据：P5）": "[P5]", "【P3、P7】": "[P3][P7]", "[来源：P3, P7]": "[P3][P7]",
        "[P3, P7]": "[P3][P7]", "[P3][P7]": "[P3][P7]", "(P12)": "[P12]", "[P3 和 P7]": "[P3][P7]",
    }
    for raw, expected in cases.items():
        assert evidence.normalize_cites(f"x {raw} y") == f"x {expected} y", raw
    assert evidence.normalize_cites("普通括号（见图 3）和 [12] 不受影响") == "普通括号（见图 3）和 [12] 不受影响"


def test_inference_basis_is_parsed_from_variant_citations():
    md = ("## 直接回答\n结论 [P3]。\n## AI 推断\n"
          "- 推断内容：作者可能想避免偏差，因为信号互补 [依据：P3]。\n"
          "- 另一个推断（依据：P3、P5）\n")
    parsed = evidence.parse_answer(md, _pack([3, 5]))
    inf = parsed.answer["inferences"]
    assert [i["basis_ords"] for i in inf] == [[3], [3, 5]]
    assert inf[0]["text"] == "作者可能想避免偏差，因为信号互补。"  # 去掉模板前缀和句末引用，保留句号
    assert "依据" not in inf[1]["text"] and "[P" not in inf[1]["text"]


def test_honest_refusal_is_not_flagged_as_missing_evidence():
    refusal = evidence.parse_answer("## 直接回答\n摘录中没有足够信息确认论文训练时使用的 GPU 数量。", _pack([1, 2]))
    assert refusal.verification["status"] == "unverified" and not refusal.evidence
    assert "没有作事实断言" in refusal.verification["notes"][0]
    # 带事实断言却没有依据的回答仍然要警告
    bold = evidence.parse_answer("## 直接回答\n该方法在所有数据集上都取得了最好的结果。", _pack([1, 2]))
    assert bold.verification["status"] == "no_evidence"


def test_analysis_retries_when_model_echoes_field_descriptions(imported):
    api, pid = imported
    api.fake.analysis_mode = "echo_once"
    job = api.wait_job(api.post(f"/papers/{pid}/analysis/run").json()["job_id"])
    assert job["status"] == "completed", job
    a = api.get(f"/papers/{pid}/analysis").json()
    assert api.fake.analysis_calls == 2  # 第一次照抄说明 → 要求重写 → 第二次有效
    assert a["status"] == "ready" and a["verification"]["status"] == "verified"
    assert all("作者研究什么问题" not in v["text"] and len(v["text"]) > 20 for v in a["content"].values() if v["evidence_ords"])
    detail = api.get(f"/jobs/{job['id']}").json()
    assert any("没有给出有效内容" in line for line in detail["log"])


def test_analysis_marks_fields_the_model_never_filled(imported):
    api, pid = imported
    api.fake.analysis_mode = "echo_always"
    job = api.wait_job(api.post(f"/papers/{pid}/analysis/run").json()["job_id"])
    assert job["status"] == "completed"
    a = api.get(f"/papers/{pid}/analysis").json()
    # 不把照抄的说明当成分析结果展示，而是明确告知并给出降级的核实状态
    assert all("没有为该项给出有效内容" in v["text"] for k, v in a["content"].items() if k != "limitations")
    assert a["verification"]["status"] in ("partial", "no_evidence")
    assert any("没有给出有效内容" in n for n in a["verification"]["notes"])


async def test_complete_always_requests_streaming_and_forwards_json_mode():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return _sse_ok('{"a": 1}', 4, 2)

    res = await _provider(handler).complete("m", [{"role": "user", "content": "x"}], json_mode=True, max_tokens=50)
    assert seen["stream"] is True and seen["response_format"] == {"type": "json_object"}
    assert res.text == '{"a": 1}' and res.usage.prompt_tokens == 4


async def test_stream_splits_only_on_newline_and_reports_dropped_frames():
    """U+2028 / U+0085 出现在数据帧内容里时不能把一帧切成两半；无法解析的帧要计数而不是静默丢弃。"""
    good = json.dumps({"choices": [{"delta": {"content": "甲 乙\u0085丙"}}]}, ensure_ascii=False)
    body = (
        f"data: {good}\n\n"
        "data: {this is not json}\n\n"
        'data: {"choices":[{"delta":{"content":"丁"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    p = _provider(lambda req: httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"}))
    text, dropped = "", 0
    async for ch in p.stream("m", [{"role": "user", "content": "x"}]):
        text += ch.text
        dropped += ch.dropped
    assert text == "甲 乙\u0085丙丁"  # 特殊分隔符原样保留，没有被切断
    assert dropped == 1


def test_dropped_frames_downgrade_verification_and_tell_the_user(imported, monkeypatch):
    api, pid = imported
    from paperdesk.ai import providers

    orig = providers.LLMProvider.stream

    async def lossy(self, *a, **kw):
        async for ch in orig(self, *a, **kw):
            yield ch
        yield providers.StreamChunk(dropped=2)

    monkeypatch.setattr(providers.LLMProvider, "stream", lossy)
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "为什么这样设计", "intent": "motivation",
                              "selection": {"text": "The routing score of node i is defined as", "page": 2}})
    card = ev[-1][1]["card"]
    assert card["verification"]["status"] == "partial"
    assert any("2 个数据块无法解析" in n for n in card["verification"]["notes"])


def test_integrity_check_flags_text_the_model_dropped():
    # 真实网关上观察到的缺词输出
    glitch = "## 直接回答\n作者明确表示，他们将拓扑注意力输出 $ a_i $ $ W_t a_i + W_h h_i $ [P5]。"
    parsed = evidence.parse_answer(glitch, _pack([5]))
    assert parsed.verification["status"] == "partial"
    assert any("可能有缺失" in n and "紧挨着" in n for n in parsed.verification["notes"])
    # 完整的公式文字不应被误报
    ok = "## 直接回答\n拓扑输出 $ a_i $ 与时间输出 $ h_i $ 相加得到 $$ s_i = W_t a_i + W_h h_i $$ [P5]。**互补**。"
    assert evidence.parse_answer(ok, _pack([5])).verification["status"] == "verified"
    assert evidence.integrity_issues("只有一个 $ 号，公式 $x$ 完整") == ["公式定界符 $ 不成对"]


def test_inference_cleanup_never_eats_explanatory_text():
    """真实模型写过：（依据：[P1][P2] 中反复出现“load”但无公式）——括号里还有解释，不能被清理掉。"""
    md = ("## 直接回答\n结论 [P1]。\n## AI 推断\n"
          "- 无法确认具体形式（依据：[P1][P2] 中反复出现“load”但无公式）。\n"
          "- 作者可能想避免偏差。（依据：[P1]）\n"
          "- 另一个推断；依据：[P1][P2]。\n"
          "---\n")
    inf = evidence.parse_answer(md, _pack([1, 2])).answer["inferences"]
    assert inf[0]["text"] == "无法确认具体形式（依据：[P1][P2] 中反复出现“load”但无公式）。"
    assert inf[0]["basis_ords"] == [1, 2]
    assert inf[1]["text"] == "作者可能想避免偏差。" and inf[1]["basis_ords"] == [1]
    assert inf[2]["text"] == "另一个推断。" and inf[2]["basis_ords"] == [1, 2]
    assert len(inf) == 3  # 分隔线 --- 没有变成一条推断


def test_section_separator_lines_are_dropped():
    parsed = evidence.parse_answer("## 直接回答\n答案 [P1]。\n\n---\n## 详细解释\n展开 [P1]。\n---", _pack([1]))
    assert parsed.answer["direct"] == "答案 [P1]。" and parsed.answer["detail"] == "展开 [P1]。"


def test_recommended_models_are_the_default_and_only_the_key_is_missing(api):
    s = api.get("/settings/ai").json()
    assert s["fast_model"] == "DeepSeek-V4-Flash-0731-W8A8" and s["strong_model"] == "MiniMax-M2.7-bf16"
    assert s["configured"] is False and s["has_api_key"] is False  # 只差 Key（和 API 地址）
    api.put("/settings/ai/secret", {"api_key": "k"})
    assert api.get("/settings/ai").json()["configured"] is True
    # 用户改过或清空后，以用户的为准，不会被默认值覆盖
    api.put("/settings/ai", {"fast_model": "my-fast", "strong_model": ""})
    s = api.get("/settings/ai").json()
    assert s["fast_model"] == "my-fast" and s["strong_model"] == ""


def test_ask_carries_previous_turns(imported):
    api, pid = imported
    api.sse("/ai/ask", {"paper_id": pid, "question": "第一个问题：拓扑注意力是什么", "intent": "free"})
    api.sse("/ai/ask", {"paper_id": pid, "question": "那为什么呢", "intent": "free"})
    msgs = api.fake.requests[-1]["body"]["messages"]
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system" and roles[-1] == "user" and "assistant" in roles  # 前一轮的问答带在新问题前面
    assert any("第一个问题" in str(m["content"]) for m in msgs[:-1] if m["role"] == "user")
    assert "那为什么呢" in str(msgs[-1]["content"])
    priv = api.get("/privacy/requests").json()["items"]
    assert any("前 1 轮对话" in i["summary"] for i in priv)


def test_ask_outside_paper_uses_own_knowledge_and_web_sources(imported, monkeypatch):
    from paperdesk.ai import websearch

    async def fake_search(app, question, selection_text=""):
        return [{"n": 1, "title": "Cascading failure", "url": "https://en.wikipedia.org/wiki/Cascading_failure",
                 "source": "维基百科", "snippet": "A cascading failure is a process in a system of interconnected parts."}]

    monkeypatch.setattr(websearch, "search", fake_search)
    api, pid = imported
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "TRIGGER_OUTSIDE 什么是级联失效", "intent": "free", "web": True})
    card = ev[-1][1]["card"]
    assert card["answer"]["outside"] and "[W1]" in card["answer"]["outside"]
    assert [w["n"] for w in card["answer"]["web"]] == [1]  # 编造的 [W9] 不算数，只留真实存在的资料
    assert card["answer"]["web"][0]["url"].startswith("https://en.wikipedia.org/")
    assert card["evidence"] == [] and any("不是论文原文" in n for n in card["verification"]["notes"])
    sent = str(api.fake.requests[-1]["body"]["messages"])
    assert "【网络资料】" in sent and "Cascading failure" in sent
    priv = api.get("/privacy/requests").json()["items"]
    assert any("联网检索" in i["summary"] for i in priv)


def test_web_search_is_off_unless_asked(imported, monkeypatch):
    from paperdesk.ai import websearch

    called = []

    async def fake_search(*a, **k):
        called.append(1)
        return []

    monkeypatch.setattr(websearch, "search", fake_search)
    api, pid = imported
    api.sse("/ai/ask", {"paper_id": pid, "question": "拓扑注意力是什么", "intent": "free"})
    assert not called
    assert "来自公开网站的检索结果" not in str(api.fake.requests[-1]["body"]["messages"])  # 没开联网：请求里不带网络资料


def test_conversations_are_independent_within_and_across_papers(imported):
    api, pid = imported
    c1 = api.sse("/ai/ask", {"paper_id": pid, "question": "对话一的问题ALPHA", "intent": "free"})[-1][1]["card"]["conversation_id"]
    c2 = api.post(f"/papers/{pid}/conversations", {}).json()["id"]
    assert c2 != c1
    # 对话二里提问：前文里没有对话一的内容
    api.sse("/ai/ask", {"paper_id": pid, "question": "对话二的问题BETA", "intent": "free", "conversation_id": c2})
    msgs = api.fake.requests[-1]["body"]["messages"]
    assert not any("ALPHA" in str(m["content"]) for m in msgs) and "assistant" not in [m["role"] for m in msgs]
    # 回到对话一继续问：带着对话一自己的前文，没有对话二的
    api.sse("/ai/ask", {"paper_id": pid, "question": "继续问", "intent": "free", "conversation_id": c1})
    msgs = api.fake.requests[-1]["body"]["messages"]
    assert any("ALPHA" in str(m["content"]) for m in msgs) and not any("BETA" in str(m["content"]) for m in msgs)
    convs = {c["id"]: c for c in api.get(f"/papers/{pid}/conversations").json()["items"]}
    assert convs[c1]["card_count"] == 2 and convs[c2]["card_count"] == 1
    assert convs[c1]["title"].startswith("对话一的问题") or convs[c1]["title"] == "对话 1"  # 默认名被第一个问题顶替
    assert [c["question"] for c in api.get(f"/papers/{pid}/cards?conversation_id={c2}").json()["items"]] == ["对话二的问题BETA"]
    # 删除对话：里面没保存的卡片一起删
    api.delete(f"/conversations/{c2}")
    assert api.get(f"/papers/{pid}/cards?conversation_id={c2}").json()["items"] == []


def test_web_query_drops_question_filler_and_wikimedia_gets_a_user_agent():
    from paperdesk.ai import websearch

    assert websearch._query("什么是级联失效", "") == "级联失效"
    assert websearch._query("How does layer normalization work", "") == "layer normalization work"
    assert websearch._query("为什么呢", "multi-head attention") == "multi-head attention"  # 太短的追问：选中的文字才是主题
    assert "Marginalia" in websearch.UA["User-Agent"] and "http" in websearch.UA["User-Agent"]


def test_stopping_generation_keeps_what_was_already_written(imported):
    import asyncio

    from paperdesk.ai.service import AIService, AskRequest

    api, pid = imported

    async def run():
        gen = AIService(api.app.state.ctx).ask_events(AskRequest(paper_id=pid, question="拓扑注意力是什么", intent="free"))
        n = 0
        async for name, _data in gen:
            if name == "delta":
                n += 1
                if n == 3:
                    break
        await gen.aclose()  # 用户点了“停止生成”

    asyncio.run(run())
    items = api.get(f"/papers/{pid}/cards").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "complete"
    assert items[0]["answer"]["direct"].strip() and items[0]["raw_markdown"].strip()  # 已经输出的内容留下了
    assert any("中途停止" in n for n in items[0]["verification"]["notes"])


def test_when_the_paper_lacks_it_the_backend_adds_an_outside_answer(imported, monkeypatch):
    from paperdesk.ai import websearch

    async def fake_search(app, question, selection_text=""):
        return [{"n": 1, "title": "Agent generation", "url": "https://example.org/a", "source": "OpenAlex", "snippet": "LLMs can generate agents."}]

    monkeypatch.setattr(websearch, "search", fake_search)
    api, pid = imported
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "TRIGGER_NOOUTSIDE LLM 是怎么批量生成 Agent 的", "intent": "free", "web": True})
    assert any(n == "stage" for n, _ in ev)  # 告诉用户正在补充
    card = ev[-1][1]["card"]
    assert "补充回答" in card["answer"]["outside"] and "[P3]" not in card["answer"]["outside"]
    assert [w["n"] for w in card["answer"]["web"]] == [1]
    assert any("不是论文原文" in n for n in card["verification"]["notes"])


def _b64(b: bytes) -> str:
    import base64

    return base64.b64encode(b).decode("ascii")


def test_text_and_pdf_attachments_are_sent_as_text_and_named_on_the_card(imported):
    import fitz

    api, pid = imported
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Appendix table: accuracy 91.2 percent", fontsize=12)
    pdf = doc.tobytes()
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "结合附件说说", "intent": "free", "attachments": [
        {"name": "notes.txt", "mime": "text/plain", "data": _b64("我的实验记录：学习率 3e-4".encode("utf-8"))},
        {"name": "extra.pdf", "mime": "application/pdf", "data": _b64(pdf)},
    ]})
    card = ev[-1][1]["card"]
    assert [a["name"] for a in card["attachments"]] == ["notes.txt", "extra.pdf"]
    sent = str(api.fake.requests[-1]["body"]["messages"])
    assert "学习率 3e-4" in sent and "accuracy 91.2" in sent  # 文本和 PDF 文字都随提问发出
    assert api.fake.requests[-1]["body"]["model"] == "strong-m"  # 没有图片：照常用文字模型
    assert any("附带 2 个文件" in i["summary"] for i in api.get("/privacy/requests").json()["items"])


def test_image_attachment_switches_to_the_vision_model(imported):
    api, pid = imported
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "这张截图里是什么", "intent": "free", "attachments": [
        {"name": "shot.png", "mime": "image/png", "data": _b64(png)}]})
    assert ev[-1][0] == "done"
    body = api.fake.requests[-1]["body"]
    vision = api.get("/settings/ai").json()["vision_model"]
    assert body["model"] == vision  # 图片交给视觉模型
    last = body["messages"][-1]["content"]
    assert isinstance(last, list) and any(p.get("type") == "image_url" for p in last)


def test_bad_attachments_are_rejected_with_a_reason(imported):
    api, pid = imported
    base = {"paper_id": pid, "question": "x", "intent": "free"}
    ev = api.sse("/ai/ask", {**base, "attachments": [{"name": "a.bin", "mime": "application/octet-stream", "data": _b64(b"\xff\xfe\x00\x01")}]})
    assert ev[-1][0] == "error" and "不支持" in ev[-1][1]["message"]
    ev = api.sse("/ai/ask", {**base, "attachments": [{"name": f"f{i}.txt", "mime": "text/plain", "data": _b64(b"x")} for i in range(5)]})
    assert ev[-1][0] == "error" and "最多" in ev[-1][1]["message"]
    assert api.get(f"/papers/{pid}/cards").json()["items"] == []  # 被拒绝的提问不留下卡片


def _first_paragraph_ord(api, pid):
    for page in (1, 2, 3):
        for p in api.get(f"/papers/{pid}/passages?page={page}").json()["items"]:
            if p["kind"] == "paragraph" and len(p["text"]) > 80:
                return p["ord"], p["text"]
    raise AssertionError("no paragraph")


def test_passage_insight_is_verified_cached_and_reader_oriented(imported):
    api, pid = imported
    ord_, text = _first_paragraph_ord(api, pid)
    before = len(api.fake.requests)
    r = api.post(f"/papers/{pid}/passages/{ord_}/insight", {}).json()
    ins = r["insight"]
    assert r["cached"] is False and r["passage"]["ord"] == ord_ and r["passage"]["bbox"]
    assert ins["role"] == "其他"  # 模型给了不在列表里的角色：退回“其他”，不把任意文字当标签
    assert ins["does"] and ins["takeaway"] and ins["from"] and ins["to"]
    assert ins["key_sentence"] and " ".join(ins["key_sentence"].split()) in " ".join(text.split())  # 关键句是这一段里的原话
    assert [w for w in ins["watch"]] == ins["watch"][:3] and len(ins["watch"]) == 3  # 最多 3 条
    assert all(s["ord"] != 99999 for s in ins["see"])  # 编造的对照段落被丢掉
    sent = str(api.fake.requests[-1]["body"]["messages"])
    assert "【摘要】" in sent or "【章节结构】" in sent  # 带上整篇的位置信息，作用才说得具体
    assert any("解释这一段的作用" in i["summary"] for i in api.get("/privacy/requests").json()["items"])
    # 再点开：直接用缓存，不再调用模型
    n = len(api.fake.requests)
    again = api.post(f"/papers/{pid}/passages/{ord_}/insight", {}).json()
    assert again["cached"] is True and len(api.fake.requests) == n and len(api.fake.requests) > before
    assert api.get(f"/papers/{pid}/passages/{ord_}/insight").json()["cached"] is True
    assert len(api.post(f"/papers/{pid}/passages/{ord_}/insight", {"force": True}).json()["insight"]["watch"]) == 3
    assert len(api.fake.requests) == n + 1  # 重新分析才会再调用
