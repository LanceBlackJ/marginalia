from __future__ import annotations


def _translate(api, pid):
    job_id = api.post(f"/papers/{pid}/translation").json()["job_id"]
    assert job_id, "应当创建全文翻译的后台任务"
    job = api.wait_job(job_id)
    assert job["status"] == "completed", job
    return job


def test_full_translation_covers_body_text_and_reuses_cache(imported):
    api, pid = imported
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["total"] > 0 and st["done"] == 0 and not st["complete"] and st["items"] == []
    job = _translate(api, pid)
    assert job["result"]["failed"] == 0
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["complete"] and st["done"] == st["total"]
    assert all(i["translation"].startswith("【译文】") for i in st["items"])
    # 只翻译正文段落和图表标题：参考文献、公式、图内文字不在其中
    with api.ctx.db.session() as conn:
        kinds = {r["ord"]: r["kind"] for r in conn.execute("SELECT ord, kind FROM passages WHERE paper_id = ?", (pid,))}
    assert {kinds[i["ord"]] for i in st["items"]} <= {"paragraph", "caption"}
    # 已经翻完：不再重复创建任务，也不再发送原文
    api.fake.requests.clear()
    assert api.post(f"/papers/{pid}/translation").json()["job_id"] is None
    assert not api.fake.requests
    # 发往云端的原文记入隐私日志
    priv = api.get("/privacy/requests").json()["items"]
    assert any(i["task_type"] == "translate" and "全文翻译" in i["summary"] for i in priv)


def test_segment_dropped_by_the_model_is_retranslated_individually(imported):
    api, pid = imported
    api.fake.translate_mode = "DROP_SEGMENT"
    try:
        job = _translate(api, pid)
    finally:
        api.fake.translate_mode = ""
    assert job["result"]["failed"] == 0
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["complete"] and st["done"] == st["total"]
    # 漏掉的段落是单独发请求补译的：请求数多于“按批发送”的数量
    batch_calls = [r for r in api.fake.requests if "###1###" in str(r["body"].get("messages"))]
    single_calls = [r for r in api.fake.requests if "###1###" not in str(r["body"].get("messages")) and "论文翻译助手" in str(r["body"].get("messages"))]
    assert batch_calls and single_calls


def test_translation_shares_cache_with_selection_translation(imported):
    api, pid = imported
    _translate(api, pid)
    item = api.get(f"/papers/{pid}/translation").json()["items"][0]
    text = api.get(f"/papers/{pid}/passages/{item['ord']}").json()["text"]
    api.fake.requests.clear()
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": text})
    assert ev[-1][0] == "done" and ev[-1][1]["cached"] is True
    assert not api.fake.requests  # 划词翻译直接命中全文翻译留下的缓存


def test_failure_without_api_key_is_reported(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    job_id = api.post(f"/papers/{pid}/translation").json()["job_id"]
    assert job_id
    job = api.wait_job(job_id)
    assert job["status"] == "failed" and job["error"]


# ---------------- 翻译范围：只翻译句子类段落 ----------------
def test_skip_reason_is_conservative_and_explainable():
    from paperdesk.ai.fulltranslate import skip_reason

    region = [(100.0, 100.0, 300.0, 300.0)]
    sentence = "The network is a common data structure to describe interactive systems."
    assert skip_reason(sentence, "paragraph", (50, 50), region) is None
    assert skip_reason("Number of nodes", "paragraph", (50, 50), region) == "太短"
    assert skip_reason("Weighted-average-connectivity ANC", "paragraph", (50, 50), region) == "不足 4 个词（标签类文字）"
    assert skip_reason("0.10 0.25 0.50 0.75 1.00", "paragraph", (50, 50), region) == "数字或符号为主"
    assert skip_reason("Weighted ANC ANC ANC", "paragraph", (200, 200), region) == "位于图区域内（坐标轴或图例文字）"
    assert skip_reason("Weighted ANC ANC ANC", "paragraph", (500, 500), region) is None  # 不在图区域内：照常翻译
    assert skip_reason(sentence, "caption", (200, 200), region) is None  # 图注在图旁边也翻译
    assert skip_reason("太短", "paragraph", None, []) == "太短"
    assert skip_reason("这是一段已经是中文的句子，不需要再翻译成中文了。", "paragraph", None, []) == "已是中文"


def test_filtered_fragments_are_not_counted_or_translated(imported):
    api, pid = imported
    base = api.get(f"/papers/{pid}/translation").json()
    with api.ctx.db.session() as conn:
        page = conn.execute("SELECT MIN(page) FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        top = conn.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO figures(paper_id, page, x0, y0, x1, y1, kind, label) VALUES (?, ?, 0, 0, 500, 500, 'figure', 'Fig. X')", (pid, page))
        for i, text in enumerate(["Number of nodes N", "Weighted ANC for graphs in the plot area", "0.10 0.20 0.30 0.40 0.50 0.60"]):
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,10,10,60,20)",
                         (pid, top + i, page, "paragraph", text))
    after = api.get(f"/papers/{pid}/translation").json()
    # 新插入的三段（标签、图区域内的坐标轴文字、纯数字）都不计入“应翻译”；原有的段落若落在这张图里也会被过滤，只会更少
    assert after["total"] <= base["total"] and after["skipped"] >= 3
    api.fake.requests.clear()
    job = api.wait_job(api.post(f"/papers/{pid}/translation").json()["job_id"])
    assert job["status"] == "completed"
    sent = str(api.fake.requests)
    assert "Number of nodes N" not in sent and "Weighted ANC for graphs" not in sent and "0.10 0.20" not in sent


def test_translate_prompt_keeps_names_in_english():
    from paperdesk.ai import prompts

    assert "人名" in prompts.TRANSLATE_SYSTEM and "保持英文原文" in prompts.TRANSLATE_SYSTEM


def test_table_cells_are_translated_and_returned_per_table(imported):
    import json

    api, pid = imported
    data = {"columns": ["Method", "Accuracy", "Training Data"], "rows": [["Our model", "0.91", "Public web text"], ["Baseline", "0.85", "Public web text"]], "warnings": []}
    with api.ctx.db.session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO table_analysis(paper_id, caption_ord, status, source, data_json, updated_at) VALUES (?, 99999, 'ready', 'layout', ?, '2026-01-01T00:00:00Z')",
            (pid, json.dumps(data)),
        )
    from paperdesk.ai.fulltranslate import _heading_texts

    extra = len(_heading_texts(api.ctx, pid))  # 标题和表格单元格一起算
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["cells_total"] == 6 + extra and st["cells_done"] == 0 and st["tables"] == {}  # 数字格不翻译；重复的“Public web text”只算一次
    job = _translate(api, pid)
    assert job["result"]["table_cells"] == 6 + extra
    st = api.get(f"/papers/{pid}/translation").json()
    t = st["tables"]["99999"]
    assert st["cells_done"] == st["cells_total"] == 6 + extra
    assert t["columns"][0].startswith("【译文】") and t["columns"][1].startswith("【译文】")  # 列名 Accuracy 会翻译
    assert t["rows"][0][1] is None and t["rows"][0][2].startswith("【译文】") and t["rows"][1][2] == t["rows"][0][2]


def test_translated_text_is_remembered_and_looked_up_without_calling_the_model(imported):
    api, pid = imported
    text = "Graph attention networks operate on graph-structured data and attend over neighbours."
    assert api.post("/ai/translate/lookup", {"paper_id": pid, "text": text}).json() == {"translation": None}
    ev = api.sse("/ai/translate", {"paper_id": pid, "text": text})
    assert ev[-1][0] == "done" and ev[-1][1]["cached"] is False
    api.fake.requests.clear()
    got = api.post("/ai/translate/lookup", {"paper_id": pid, "text": text}).json()["translation"]
    assert got == ev[-1][1]["translation"] and not api.fake.requests


def test_translation_of_a_text_outside_the_library_is_also_saved(imported):
    api, _pid = imported
    text = "Large language models are rapidly advancing medical artificial intelligence in many ways."
    first = api.sse("/ai/translate", {"text": text, "context_title": "A survey"})
    assert first[-1][0] == "done" and first[-1][1]["cached"] is False
    api.fake.requests.clear()
    second = api.sse("/ai/translate", {"text": text, "context_title": "A survey"})
    assert second[-1][0] == "done" and second[-1][1]["cached"] is True and second[-1][1]["translation"] == first[-1][1]["translation"]
    assert not api.fake.requests  # 翻译过的直接用存下来的，不再调用模型


def test_paper_and_section_titles_are_translated_too(imported):
    api, pid = imported
    st = api.get(f"/papers/{pid}/translation").json()
    assert st["headings"] == {} and st["cells_total"] > 0  # 标题和表格单元格一起算
    _translate(api, pid)
    st = api.get(f"/papers/{pid}/translation").json()
    with api.ctx.db.session() as conn:
        paper_title = conn.execute("SELECT title FROM papers WHERE id = ?", (pid,)).fetchone()["title"]
        sections = [r["title"] for r in conn.execute("SELECT title FROM sections WHERE paper_id = ?", (pid,))]
    assert st["headings"], "标题应该被翻译"
    assert all(v.startswith("【译文】") for v in st["headings"].values())
    assert all(k in {paper_title, *sections} for k in st["headings"])  # 键是原文标题
    assert "Abstract" not in st["headings"]  # 固定的小节名不翻
    assert st["cells_done"] == st["cells_total"]
