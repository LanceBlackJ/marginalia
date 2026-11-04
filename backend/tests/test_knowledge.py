from __future__ import annotations

import json
import re

import pytest

import pdfgen
from f3_helpers import FakeWorld, make_world_api
from paperdesk.knowledge import extract, store


def ask_handler(world: FakeWorld):
    def h(system: str, user: str, body: dict):
        if "论文阅读与研究助手" not in system:
            return None
        ords = [int(x) for x in re.findall(r"^\[P(\d+)\]", user, re.M)]
        a = ords[0] if ords else 0
        return f"## 直接回答\n该模块通过双重注意力分配负载 [P{a}]。\n## 依据\n- [P{a}] 说明了机制\n"
    world.llm_handlers.append(h)


@pytest.fixture
def kb(tmp_path, sample_pdf):
    world = FakeWorld()
    ask_handler(world)
    api = make_world_api(tmp_path, world)
    with api.c:
        api.put("/settings/ai", {"auto_analyze": False})  # 避免导入后自动分析任务与测试里手写的分析结果抢同一行
        pid = api.import_pdf(sample_pdf)["paper_id"]
        yield api, world, pid


def passage_ords(api, pid, n=4):
    with api.ctx.db.session() as conn:
        return [r["ord"] for r in conn.execute(
            "SELECT ord FROM passages WHERE paper_id = ? AND kind = 'paragraph' ORDER BY ord LIMIT ?", (pid, n)).fetchall()]


def put_analysis(api, pid, ords):
    content = {"method": {"text": f"DAR 由拓扑注意力和时间注意力两个模块组成 [P{ords[1]}]", "evidence_ords": [ords[1]]},
               "results": {"text": f"移除时间模块使恢复时间增加 15% [P{ords[2]}]", "evidence_ords": [ords[2]]},
               "limitations": {"text": "（推断）假设负载测量准确", "evidence_ords": []}}
    with api.ctx.db.session() as conn:
        conn.execute("INSERT OR REPLACE INTO paper_analysis(paper_id, status, model, content_json, evidence_json, verification_json, updated_at) "
                     "VALUES (?, 'ready', 'm', ?, '[]', '{}', '2026-01-01T00:00:00Z')", (pid, json.dumps(content, ensure_ascii=False)))


# ---------------- 主题与条目 ----------------
def test_topics_crud_and_unique_names(kb):
    api, _, pid = kb
    t = api.post("/kb/topics", {"name": "级联失效", "description": "网络级联失效与恢复"}).json()
    assert api.post("/kb/topics", {"name": "级联失效"}).status_code == 409
    assert api.post("/kb/topics", {"name": "  "}).status_code == 400
    assert api.patch(f"/kb/topics/{t['id']}", {"description": "更新"}).json()["description"] == "更新"
    assert api.post(f"/kb/topics/{t['id']}/papers", {"paper_id": pid}).json()["ok"]
    assert api.get(f"/kb/topics/{t['id']}/papers").json()["items"][0]["id"] == pid
    assert api.get("/kb/topics").json()["items"][0]["paper_count"] == 1
    # 删除主题不会删掉用户确认过的知识条目
    item = api.post("/kb/items", {"title": "条目", "content": "内容", "topic_ids": [t["id"]]}).json()
    assert api.delete(f"/kb/topics/{t['id']}").json()["ok"]
    assert api.get(f"/kb/items/{item['id']}").json()["topic_ids"] == []
    assert [i["id"] for i in api.get("/kb/items", params={"unassigned": True}).json()["items"]] == [item["id"]]


def test_item_evidence_is_rebuilt_from_the_database_not_trusted_from_the_client(kb):
    api, _, pid = kb
    ords = passage_ords(api, pid)
    r = api.post("/kb/items", {"title": "DAR 的机制", "content": "两个模块", "kind": "method", "source_paper_id": pid,
                               "evidence": [{"paper_id": pid, "ord": ords[1], "quote": "客户端伪造的引文", "page": 99}]})
    assert r.status_code == 200
    ev = r.json()["evidence"][0]
    with api.ctx.db.session() as conn:
        real = conn.execute("SELECT text, page FROM passages WHERE paper_id=? AND ord=?", (pid, ords[1])).fetchone()
    assert ev["quote"] == real["text"][:400] and ev["page"] == real["page"] and "伪造" not in json.dumps(ev, ensure_ascii=False)
    assert ev["paper_title"] and ev["bbox"] and len(ev["bbox"]) == 4
    # 给了证据但全部找不到 → 拒绝，而不是悄悄存一条没有依据的“知识”
    bad = api.post("/kb/items", {"title": "x", "source_paper_id": pid, "evidence": [{"paper_id": pid, "ord": 99999}]})
    assert bad.status_code == 400 and "找不到" in bad.json()["error"]["message"]
    assert api.patch(f"/kb/items/{r.json()['id']}", {"title": "改名", "kind": "finding"}).json()["kind"] == "finding"
    assert api.delete(f"/kb/items/{r.json()['id']}").json()["ok"]


def test_relations_require_checkable_evidence(kb, tmp_path):
    api, _, pid = kb
    ords = passage_ords(api, pid)
    a = api.post("/kb/items", {"title": "A 方法", "source_paper_id": pid}).json()
    b = api.post("/kb/items", {"title": "B 方法", "source_paper_id": pid}).json()
    base = {"from_id": a["id"], "to_id": b["id"], "relation": "differs_from", "description": "区别"}
    assert api.post("/kb/relations", {**base, "evidence": []}).status_code == 400  # 无依据不得建立
    assert api.post("/kb/relations", {**base, "evidence": [{"paper_id": pid, "ord": 12345}]}).status_code == 400
    assert api.post("/kb/relations", {**base, "relation": "made_up", "evidence": [{"paper_id": pid, "ord": ords[0]}]}).status_code == 400
    rel = api.post("/kb/relations", {**base, "evidence": [{"paper_id": pid, "ord": ords[0]}]}).json()
    assert rel["status"] == "confirmed" and not rel["ai_inferred"] and rel["evidence"][0]["quote"]
    assert [r["id"] for r in api.get(f"/kb/items/{a['id']}").json()["relations"]] == [rel["id"]]
    assert api.delete(f"/kb/relations/{rel['id']}").json()["ok"]


# ---------------- 候选知识：提取 → 展示 → 确认 ----------------
def extract_handler(world: FakeWorld):
    def h(system: str, user: str, body: dict):
        if "科研知识整理助手" not in system:
            return None
        return json.dumps({"candidates": [
            {"title": "DAR 的双模块结构", "content": "DAR 由拓扑注意力与时间注意力两个模块组成。", "kind": "method", "from": ["S1"]},
            {"title": "时间模块的消融结论", "content": "移除时间模块使恢复时间增加 15%。", "kind": "finding", "from": ["S2", "S1"]},
            {"title": "没有来源的臆测", "content": "这条没有依据。", "kind": "other", "from": ["S99"]},
            {"title": "DAR 的双模块结构", "content": "重复标题", "kind": "method", "from": ["S1"]},
            {"title": "我的笔记要点", "content": "时间模块是关键", "kind": "concept", "from": [f"S{user.count('[S')}"]},
        ]}, ensure_ascii=False)
    world.llm_handlers.append(h)


def test_candidate_extraction_confirmation_flow(kb):
    api, world, pid = kb
    extract_handler(world)
    # 没有任何材料 → 明确提示，而不是凭空生成
    first = api.wait_job(api.post("/kb/extract", {"paper_id": pid}).json()["job_id"])
    assert first["status"] == "failed" and "还没有可整理的材料" in first["error"]

    ords = passage_ords(api, pid)
    put_analysis(api, pid, ords)
    api.post(f"/papers/{pid}/notes", {"text": "时间模块是关键", "page": 1, "quote": "temporal module", "passage_ord": ords[2]})
    job = api.wait_job(api.post("/kb/extract", {"paper_id": pid}).json()["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["added"] == 3 and job["result"]["skipped_without_evidence"] == 1
    cands = api.get("/kb/candidates", params={"paper_id": pid}).json()["items"]
    assert [c["title"] for c in cands] == ["DAR 的双模块结构", "时间模块的消融结论", "我的笔记要点"]  # 重复标题与无依据的被丢弃
    for c in cands:
        assert c["evidence"] and all(e["quote"] for e in c["evidence"])
    note_cand = cands[2]
    assert note_cand["evidence"][0]["kind"] == "note" and note_cand["origin_kind"] == "note"
    # 候选在用户确认前不在知识库里
    assert api.get("/kb/items").json()["items"] == []
    topic = api.post("/kb/topics", {"name": "级联恢复"}).json()
    item = api.post(f"/kb/candidates/{cands[0]['id']}/accept", {"topic_id": topic["id"], "title": "DAR 双模块（我改过标题）"}).json()
    assert item["title"] == "DAR 双模块（我改过标题）" and item["origin"] == "candidate" and item["topic_ids"] == [topic["id"]]
    assert item["source_paper"]["id"] == pid and item["evidence"]
    assert api.post(f"/kb/candidates/{cands[0]['id']}/accept", {}).status_code == 409  # 不能重复确认
    assert api.post(f"/kb/candidates/{cands[1]['id']}/reject").json()["ok"]
    new_topic = api.post(f"/kb/candidates/{cands[2]['id']}/accept", {"new_topic": "我的想法"}).json()
    assert len(new_topic["topic_ids"]) == 1
    assert api.get("/kb/candidates", params={"paper_id": pid}).json()["items"] == []
    # 再次整理：已确认/已拒绝的不会再冒出来
    again = api.wait_job(api.post("/kb/extract", {"paper_id": pid}).json()["job_id"])
    assert again["result"]["added"] == 0
    assert {t["name"]: t["item_count"] for t in api.get("/kb/topics").json()["items"]} == {"级联恢复": 1, "我的想法": 1}


def test_relation_suggestions_are_marked_ai_and_need_confirmation(tmp_path, sample_pdf):
    world = FakeWorld()
    world.llm_handlers.append(lambda s, u, b: json.dumps({"relations": [
        {"from": 1, "to": 2, "relation": "differs_from", "description": "采用的信号不同"},
        {"from": 1, "to": 3, "relation": "improves", "description": "同一篇论文内的条目，不应建立"},
    ]}, ensure_ascii=False) if "科研文献分析助手" in s else None)
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(sample_pdf)["paper_id"]
        other = tmp_path / "b.pdf"
        old = pdfgen.TITLE
        pdfgen.TITLE = "Another Routing Paper"
        try:
            pdfgen.build_paper(other, doi="10.7/b")
        finally:
            pdfgen.TITLE = old
        pb = api.import_pdf(other)["paper_id"]
        t = api.post("/kb/topics", {"name": "路由"}).json()
        oa, ob = passage_ords(api, pa), passage_ords(api, pb)
        mk = lambda title, pid, o: api.post("/kb/items", {"title": title, "content": title, "source_paper_id": pid, "topic_ids": [t["id"]],
                                                         "evidence": [{"paper_id": pid, "ord": o}]}).json()
        i1, i2, i3 = mk("A1", pa, oa[0]), mk("B1", pb, ob[0]), mk("A2", pa, oa[1])
        assert api.post("/kb/relations/suggest", {}).status_code == 400
        res = api.post("/kb/relations/suggest", {"topic_id": t["id"]}).json()
        assert len(res["suggested"]) == 1
        rel = res["suggested"][0]
        assert rel["status"] == "suggested" and rel["ai_inferred"] and len(rel["evidence"]) == 2
        assert {e["paper_id"] for e in rel["evidence"]} == {pa, pb}  # 证据来自两端条目的真实段落
        assert api.get("/kb/relations", params={"topic_id": t["id"], "status": "confirmed"}).json()["items"] == []
        assert api.post(f"/kb/relations/{rel['id']}/confirm").json()["status"] == "confirmed"
        assert api.post("/kb/relations/suggest", {"topic_id": t["id"]}).json()["suggested"] == []  # 已存在，不重复建议


def test_topic_suggestions_flow(tmp_path, sample_pdf):
    world = FakeWorld()
    world.llm_handlers.append(lambda s, u, b: json.dumps({"topics": [
        {"name": "级联失效与网络恢复", "description": "网络级联失效、恢复策略", "papers": [1, 2]},
        {"name": "空主题", "description": "x", "papers": [99]},
    ]}, ensure_ascii=False) if "科研文献整理助手" in s else None)
    api = make_world_api(tmp_path, world)
    with api.c:
        assert api.post("/kb/topics/suggest").status_code == 400  # 不足 2 篇
        api.import_pdf(sample_pdf)
        other = pdfgen.build_paper(tmp_path / "o.pdf", doi="10.7/o")
        import pymupdf

        doc = pymupdf.open(str(other))
        doc[0].insert_text((20, 20), "x", fontsize=5)
        doc.save(str(tmp_path / "o2.pdf"))
        api.import_pdf(tmp_path / "o2.pdf")
        sug = api.post("/kb/topics/suggest").json()["items"]
        assert [s["name"] for s in sug] == ["级联失效与网络恢复"] and len(sug[0]["papers"]) == 2  # 序号越界的主题被忽略
        assert api.get("/kb/topics").json()["items"] == []  # 建议未确认前不出现在主题列表
        tid = sug[0]["id"]
        assert api.post(f"/kb/topic-suggestions/{tid}/accept").json()["paper_count"] == 2
        assert [t["name"] for t in api.get("/kb/topics").json()["items"]] == ["级联失效与网络恢复"]
        assert api.get("/kb/topic-suggestions").json()["items"] == []


# ---------------- 个人知识画像 ----------------
def make_term_cards(api, pid, term: str, n: int):
    ords = passage_ords(api, pid)
    for _ in range(n):
        r = api.sse("/ai/ask", {"paper_id": pid, "intent": "term", "question": "", "selection": {"text": term, "page": 1, "passage_ords": [ords[0]]}})
        assert r[-1][0] == "done", r[-1]


def test_profile_never_marks_concepts_as_mastered_automatically(kb):
    api, _, pid = kb
    make_term_cards(api, pid, "cascading failures", 3)
    make_term_cards(api, pid, "routing", 1)
    prof = api.post("/profile/suggest").json()["items"]
    by = {s["name"]: s for s in prof}
    assert by["cascading failures"]["state"] == "learning" and "3 次" in by["cascading failures"]["basis"]
    assert by["routing"]["state"] == "unsure"
    assert {s["state"] for s in prof} <= {"learning", "unsure"}  # 绝不提议“已掌握”
    assert api.get("/profile").json()["concepts"] == []  # 建议未确认前不进入画像
    assert api.post("/profile/suggest").json()["items"].__len__() == 2  # 重复生成不会产生重复建议
    c = api.post(f"/profile/suggestions/{by['routing']['id']}/accept", {"state": "mastered"}).json()  # 用户自己决定
    assert c["state"] == "mastered" and c["origin"] == "suggestion"
    assert api.post(f"/profile/suggestions/{by['cascading failures']['id']}/dismiss").json()["ok"]
    assert api.post("/profile/suggest").json()["items"] == []  # 已接受/已忽略的都不再重复建议（忽略 = 别再问我）
    # 用户直接管理概念
    manual = api.post("/profile/concepts", {"name": "Attention", "state": "learning", "note": "在读"}).json()
    assert api.patch(f"/profile/concepts/{manual['id']}", {"state": "mastered"}).json()["state_label"] == "已确认掌握"
    assert api.post("/profile/concepts", {"name": "x", "state": "genius"}).status_code == 400
    assert api.delete(f"/profile/concepts/{manual['id']}").json()["ok"]
    meta = api.put("/profile/meta", {"interests": ["网络科学", " ", "强化学习"], "explanation_depth": "deep"}).json()
    assert meta == {"interests": ["网络科学", "强化学习"], "explanation_depth": "deep", "use_in_prompts": False}
    assert api.put("/profile/meta", {"explanation_depth": "extreme"}).status_code == 400


def test_profile_hint_is_off_by_default_and_never_overrides_citation_rules(kb):
    api, world, pid = kb
    ords = passage_ords(api, pid)
    ask = lambda: api.sse("/ai/ask", {"paper_id": pid, "question": "这个模块是做什么的？", "intent": "free"})
    ask()
    off = [c for c in world.llm_calls if "论文阅读与研究助手" in c["system"]][-1]["system"]
    assert "附加说明" not in off
    api.post("/profile/concepts", {"name": "softmax", "state": "mastered"})
    api.post("/profile/concepts", {"name": "attention", "state": "learning"})
    api.put("/profile/meta", {"explanation_depth": "brief"})
    ask()
    still_off = [c for c in world.llm_calls if "论文阅读与研究助手" in c["system"]][-1]["system"]
    assert "附加说明" not in still_off  # 只有“使用画像”开关打开才生效
    api.put("/profile/meta", {"use_in_prompts": True})
    ask()
    on = [c for c in world.llm_calls if "论文阅读与研究助手" in c["system"]][-1]["system"]
    assert "附加说明" in on and "简明扼要" in on and "softmax" in on and "attention" in on
    assert "不改变“事实必须引用摘录”的要求" in on and "每个事实性陈述后用 [P序号] 标注来源" in on  # 引用规则原样保留
    assert api.get("/profile").json()["prompt_hint"].startswith("用户偏好的解释深度")


# ---------------- 全局笔记与导出 ----------------
def test_global_notes_and_markdown_exports(kb):
    api, world, pid = kb
    ords = passage_ords(api, pid)
    n1 = api.post(f"/papers/{pid}/notes", {"text": "这里很关键", "page": 1, "quote": "Cascading failures occur", "passage_ord": ords[0]}).json()
    api.post(f"/papers/{pid}/notes", {"text": "公式的含义要再看", "page": 2})
    allnotes = api.get("/notes/all").json()
    assert allnotes["total"] == 2 and allnotes["items"][0]["paper_title"].startswith("Dual Attention")
    assert api.get("/notes/all", params={"q": "公式"}).json()["total"] == 1
    assert api.get("/notes/all", params={"paper_id": "nope"}).json()["total"] == 0

    t = api.post("/kb/topics", {"name": "级联失效"}).json()
    api.post("/kb/items", {"title": "DAR 的双模块结构", "content": "拓扑注意力与时间注意力。", "kind": "method", "source_paper_id": pid,
                           "topic_ids": [t["id"]], "evidence": [{"paper_id": pid, "ord": ords[1]}]})
    api.post("/kb/items", {"title": "没有来源的想法", "content": "纯手写", "topic_ids": [t["id"]]})
    md = api.post("/export/markdown", {"kind": "topic", "topic_id": t["id"]}).json()
    assert md["filename"] == "主题_级联失效.md" and md["count"] == 2
    c = md["content"]
    assert c.startswith("# 级联失效") and "## 方法" in c and "### DAR 的双模块结构" in c and "来源：《Dual Attention Routing" in c
    assert "证据：" in c and "第 1 页" in c and "“" in c  # 含页码与原文摘录
    assert "没有证据" not in c
    slim = api.post("/export/markdown", {"kind": "topic", "topic_id": t["id"], "include_evidence": False}).json()["content"]
    assert "证据：" not in slim
    vp = api.post("/export/markdown", {"kind": "viewpoints", "topic_id": t["id"]}).json()
    assert vp["filename"].startswith("文献观点汇总") and "- **DAR 的双模块结构**：拓扑注意力与时间注意力。（《Dual" in vp["content"]
    notes_md = api.post("/export/markdown", {"kind": "notes"}).json()["content"]
    assert "## 《Dual Attention" in notes_md and "- **第 1 页**：这里很关键" in notes_md and "> Cascading failures occur" in notes_md
    # 研究卡片：先保存一张
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "为什么这样设计？", "intent": "motivation"})
    assert ev[-1][0] == "done"
    cards_md = api.post("/export/markdown", {"kind": "cards"}).json()["content"]
    assert "# 研究卡片" in cards_md and "为什么这样设计？" in cards_md and "证据：" in cards_md and "核实状态" in cards_md
    assert api.post("/export/markdown", {"kind": "topic"}).status_code == 400
    assert api.post("/export/markdown", {"kind": "topic", "topic_id": "nope"}).status_code == 404
    assert api.delete(f"/notes/{n1['id']}").json()["ok"]
    assert api.post("/export/markdown", {"kind": "notes", "paper_ids": ["none"]}).status_code == 400
