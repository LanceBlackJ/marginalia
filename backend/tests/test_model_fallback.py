from __future__ import annotations

import asyncio

from paperdesk.ai.providers import LLMError, OpenAICompatibleProvider
from paperdesk.llmjson import ask_json


def _strong_mode(api):
    api.configure_ai(max_retries=0, mode="strong", auto_analyze=False)


def test_ask_falls_back_to_fast_model_when_strong_is_down_and_says_so(imported):
    api, pid = imported
    _strong_mode(api)
    api.fake.fail_models["strong-m"] = 503
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "拓扑注意力模块是怎么工作的？请详细解释它的设计", "intent": "free"})
    names = [n for n, _ in ev]
    assert names[-1] == "done", ev[-1]
    stage = [d["message"] for n, d in ev if n == "stage"]
    assert any("strong-m" in m and "fast-m" in m and "暂时不可用" in m for m in stage)  # 流式过程中就告知
    card = ev[-1][1]["card"]
    assert card["model"] == "fast-m" and card["status"] == "complete"
    assert any("strong-m" in n and "fast-m" in n for n in card["verification"]["notes"])  # 卡片里留有如实说明
    assert card["evidence"]  # 引用核实规则不变：回答仍然带着经过核实的证据
    # 用量记在实际回答的模型名下，而不是强模型
    models = {m["model"] for m in api.get("/usage").json()["by_model"]}
    assert "fast-m" in models


def test_no_fallback_when_both_models_fail_or_error_is_not_retriable(imported):
    api, pid = imported
    _strong_mode(api)
    api.fake.fail_models.update({"strong-m": 503, "fast-m": 503})
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "拓扑注意力模块是怎么工作的？请详细解释它的设计", "intent": "free"})
    assert ev[-1][0] == "error" and ev[-1][1]["code"] == "provider_error"  # 快速模型也失败才报错
    api.fake.fail_models.clear()
    api.fake.fail_models["strong-m"] = 401  # 认证失败：不是服务暂时不可用，不能靠换模型掩盖
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "拓扑注意力模块是怎么工作的？请详细解释它的设计", "intent": "free"})
    assert ev[-1][0] == "error" and ev[-1][1]["code"] == "auth_failed"
    assert not [d for n, d in ev if n == "stage"]


def test_paper_analysis_records_the_fallback(imported):
    api, pid = imported
    _strong_mode(api)
    api.fake.fail_models["strong-m"] = 503
    job = api.wait_job(api.post(f"/papers/{pid}/analysis/run").json()["job_id"])
    assert job["status"] == "completed", job
    a = api.get(f"/papers/{pid}/analysis").json()
    assert a["status"] == "ready" and a["model"] == "fast-m"
    assert any("strong-m" in n and "fast-m" in n for n in a["verification"]["notes"])


def test_ask_json_reports_fallback_in_notes_for_compare_and_knowledge(imported):
    api, _pid = imported
    _strong_mode(api)
    api.fake.fail_models["strong-m"] = 503
    notes: list[str] = []
    data = asyncio.run(ask_json(api.ctx, system="你是检索关键词生成器", user="attention", task_type="t", tier="strong", notes=notes))
    assert data == ["attention", "routing", "cascade failure"]
    assert len(notes) == 1 and "strong-m" in notes[0] and "fast-m" in notes[0]


def test_connection_test_reports_the_strong_model_honestly(imported):
    api, _pid = imported
    api.fake.fail_models["strong-m"] = 503
    api.put("/settings/ai", {"max_retries": 0})
    r = api.post("/settings/ai/test").json()
    assert r["fast"]["ok"] is True and r["strong"]["ok"] is False  # 不能被回退掩盖


def test_provider_fallback_only_before_any_output():
    """已经开始输出内容之后失败，不能悄悄换模型（会让回答前后来自两个模型）。"""
    import httpx

    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(req.content)["model"]
        calls.append(model)
        if model == "strong":
            first = 'data: {"choices":[{"delta":{"content":"半截"}}]}\n\n'.encode("utf-8")

            class _Broken(httpx.AsyncByteStream):  # 先吐出一段内容，再在传输途中断开
                async def __aiter__(self):
                    yield first
                    raise httpx.ReadError("connection reset")

            return httpx.Response(200, stream=_Broken(), headers={"content-type": "text/event-stream"})
        ok = 'data: {"choices":[{"delta":{"content":"备用"}}]}\n\ndata: [DONE]\n\n'.encode("utf-8")
        return httpx.Response(200, content=ok, headers={"content-type": "text/event-stream"})

    prov = OpenAICompatibleProvider("https://x.test/v1", "k", timeout=5, max_retries=0, transport=httpx.MockTransport(handler))
    prov.fallbacks = {"strong": "fast"}
    try:
        asyncio.run(prov.complete("strong", [{"role": "user", "content": "hi"}]))
        raise AssertionError("应当抛出传输错误，而不是悄悄换模型")
    except LLMError:
        pass
    assert calls == ["strong"] and not prov.fallback_events  # 已经输出过内容：不回退
