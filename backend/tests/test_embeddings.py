from __future__ import annotations

from paperdesk.ai import embeddings, retrieval
from paperdesk.ai.router import TaskType


def _index(api, pid):
    job_id = api.post(f"/papers/{pid}/index").json()["job_id"]
    assert job_id, "应当创建建立索引的后台任务"
    job = api.wait_job(job_id)
    assert job["status"] == "completed", job
    return job


def test_index_covers_body_text_only_and_is_idempotent(imported):
    api, pid = imported
    st = api.get(f"/papers/{pid}/index").json()
    assert st["model"] == "bge-m3" and st["indexed"] == 0 and not st["complete"] and st["total"] > 0
    _index(api, pid)
    st = api.get(f"/papers/{pid}/index").json()
    assert st["complete"] and st["indexed"] == st["total"]
    with api.ctx.db.session() as conn:
        kinds = {r[0] for r in conn.execute(
            "SELECT p.kind FROM passage_embeddings e JOIN passages p ON p.paper_id = e.paper_id AND p.ord = e.ord")}
    assert kinds <= {"paragraph", "caption", "table"}  # 参考文献/图内文字/公式不建索引
    assert api.post(f"/papers/{pid}/index").json()["job_id"] is None  # 已完成：不重复建
    # 发往云端的内容记入隐私日志
    priv = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "embedding" and "语义索引" in i["summary"] for i in priv)


def test_vector_search_finds_the_matching_passage(imported):
    api, pid = imported
    _index(api, pid)
    q = embeddings_vec("topology attention module computes attention weights over neighbouring nodes")
    hits = embeddings.vector_search(api.ctx, pid, q, k=3)
    assert hits and hits[0].score > 0.5
    top = retrieval.reader.get_passage(api.ctx, pid, hits[0].ord)
    assert "topology attention" in top["text"].lower()


def embeddings_vec(text):
    from fakellm import FakeLLM

    return FakeLLM.embed(text)


def test_ask_with_index_uses_query_vector_and_skips_keyword_planning(imported):
    api, pid = imported
    _index(api, pid)
    api.fake.requests.clear()
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "时间注意力模块 temporal attention module 的作用是什么？"})
    assert ev[-1][0] == "done"
    sent = [r["body"] for r in api.fake.requests]
    assert any("input" in b for b in sent)  # 嵌入了查询
    assert not any("检索关键词生成器" in str(b.get("messages")) for b in sent)  # 不再额外调模型生成英文检索词
    pack_reasons = ev[0][1]["context"]
    assert pack_reasons["passage_count"] > 0
    priv = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "embedding" and "语义检索" in i["summary"] for i in priv)


def test_semantic_hits_are_marked_and_fused_with_keyword_hits(imported):
    api, pid = imported
    _index(api, pid)
    q = "temporal attention module attends to the recent load history of each node"
    pack = retrieval.build_context(api.ctx, pid, question=q, selection=None, task=TaskType.METHOD,
                                   query_vec=embeddings_vec(q))
    both = [p for p in pack.passages if {"search", "semantic"} <= p.reasons]
    assert both and "temporal" in both[0].text.lower()


def test_falls_back_to_keywords_when_disabled_or_failing(imported):
    api, pid = imported
    _index(api, pid)
    q = "时间注意力模块是做什么的？"
    # 关闭语义检索：回到“模型生成英文检索词”的旧路径
    api.put("/settings/ai", {"semantic_retrieval": False})
    api.fake.requests.clear()
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": q})
    assert ev[-1][0] == "done"
    assert any("检索关键词生成器" in str(r["body"].get("messages")) for r in api.fake.requests)
    # 开启但嵌入接口出错：同样降级，问答不受影响
    api.put("/settings/ai", {"semantic_retrieval": True, "max_retries": 0})
    api.fake.requests.clear()
    api.fake.fail_next.append((500, "embedding backend down"))
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": q})
    assert ev[-1][0] == "done" and ev[-1][1]["card"]["status"] == "complete"
    assert any("检索关键词生成器" in str(r["body"].get("messages")) for r in api.fake.requests)


def test_auto_index_after_import_when_configured(api, sample_pdf):
    api.configure_ai()
    pid = api.import_pdf(sample_pdf)["paper_id"]
    import time

    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        st = api.get(f"/papers/{pid}/index").json()
        if st["complete"]:
            break
        time.sleep(0.1)
    assert st.get("complete"), st
    api.put("/settings/ai", {"auto_analyze": False})


def test_anthropic_provider_does_not_support_embeddings_and_ask_still_works(imported):
    api, pid = imported
    _index(api, pid)
    import asyncio

    from paperdesk.ai.providers import AnthropicProvider, LLMError

    prov = AnthropicProvider("https://api.anthropic.com", "k", timeout=5, max_retries=0)
    try:
        asyncio.run(embeddings.embed_batch(prov, "m", ["x"]))
        raise AssertionError("应当不支持")
    except LLMError as err:
        assert err.code == "not_supported"
    cfg = {"semantic_retrieval": True, "embedding_model": "bge-m3"}
    assert asyncio.run(embeddings.embed_query(api.ctx, prov, cfg, pid, "hello")) is None  # 降级而不是抛错


def test_too_short_passages_do_not_keep_the_index_incomplete(imported):
    api, pid = imported
    with api.ctx.db.session() as conn:  # 插入一个过短的段落：不应计入“应索引”总数
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)",
                     (pid, 9999, 1, "paragraph", "short", ))
    _index(api, pid)
    st = api.get(f"/papers/{pid}/index").json()
    assert st["complete"] and st["indexed"] == st["total"]
