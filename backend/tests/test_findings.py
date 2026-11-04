from __future__ import annotations

import threading
import time

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN, Api
from fake_mindmap_llm import FakeMindmapLLM

from paperdesk.app import create_app
from paperdesk.insights import findings as F
from paperdesk.mindmap import llmcall
from paperdesk.mindmap.tree import humanize


@pytest.fixture(autouse=True)
def _no_long_waits(monkeypatch):
    """可重试错误的“再等 8/20 秒”在测试里不真等，轮数不变。"""
    monkeypatch.setattr(llmcall, "OUTER_RETRY_DELAYS", (0.0, 0.0))

# ---------------- 纯逻辑：校验 ----------------
TEXTS = {
    1: "We propose a routing module and train it with learning rate 0.003 on three datasets.",
    2: "The ablation removes the temporal module and recovery time increases by 15 percent.",
    3: "A limitation of our work is that we assume accurate load measurements.",
}


def item(**kw):
    d = {"type": "weak_support", "title": "实验只覆盖了三个数据集", "detail": "摘录里没有看到更多数据集上的结果，值得核对。",
         "evidence_ords": [1], "anchor": "learning rate 0.003", "basis": "stated", "confidence": "high"}
    d.update(kw)
    return d


def test_parse_accepts_empty_result_as_a_normal_outcome():
    found, verdict, summary, rep = F.parse_findings({"verdict": "no_issues", "summary": "论证充分。", "findings": []}, TEXTS)
    assert found == [] and verdict == "no_issues" and summary == "论证充分。" and rep["proposed"] == 0


def test_parse_requires_a_findings_array_but_not_findings():
    with pytest.raises(ValueError):
        F.parse_findings({"verdict": "no_issues"}, TEXTS)
    with pytest.raises(ValueError):
        F.parse_findings("nope", TEXTS)


def test_parse_drops_findings_without_verified_evidence_and_low_confidence():
    data = {"findings": [
        item(evidence_ords=[999]),                                   # 编造的编号 → 没有证据 → 丢弃
        item(title="低置信度应被丢弃", confidence="low", evidence_ords=[2]),
        item(title="未知类型", type="whatever", evidence_ords=[2]),
        item(title="保留的一条", evidence_ords=[1, 888]),
    ]}
    found, verdict, _, rep = F.parse_findings(data, TEXTS)
    assert [f["title"] for f in found] == ["保留的一条"] and found[0]["ords"] == [1]
    assert rep["dropped_no_evidence"] == 1 and rep["dropped_low_confidence"] == 1 and rep["dropped_bad_type"] == 1 and rep["dropped_refs"] >= 2
    assert verdict == "issues"


def test_parse_all_dropped_means_no_issues_and_says_so():
    found, verdict, summary, _ = F.parse_findings({"verdict": "issues", "summary": "有问题", "findings": [item(evidence_ords=[999])]}, TEXTS)
    assert found == [] and verdict == "no_issues" and "全部丢弃" in summary


def test_parse_check_status_rules_and_anchor_verification():
    data = {"findings": [
        item(title="A 原文可见且短语真实存在", evidence_ords=[1]),                                   # → 待核查
        item(type="overreach", title="B 模型自己推理", evidence_ords=[2], basis="inferred", anchor="percent"),  # → AI 推断
        item(type="unexplained_param", title="C 短语不在所引段落", evidence_ords=[3], anchor="这句话原文里没有"),  # → AI 推断 + 备注
        item(type="further_reading", title="D 延伸阅读恒为推断", evidence_ords=[3], anchor="accurate load measurements"),
    ]}
    found, _, _, rep = F.parse_findings(data, TEXTS)
    by = {f["title"][0]: f for f in found}
    assert by["A"]["ai_check_status"] == "unchecked" and by["A"]["anchor"] == "learning rate 0.003"
    assert by["B"]["ai_check_status"] == "inferred"
    assert by["C"]["ai_check_status"] == "inferred" and by["C"]["anchor"] is None and "没有在所引原文里原样找到" in by["C"]["note"] and "摘录" not in by["C"]["note"]
    assert by["D"]["ai_check_status"] == "inferred"
    assert rep["anchor_not_found"] == 1
    # 后端永远不会自己给出“已核查”
    assert all(f["ai_check_status"] in ("unchecked", "inferred") for f in found)


def test_parse_dedupes_and_caps_at_eight():
    dup = {"findings": [item(title="消融实验没有单独验证模块"), item(title="消融实验没有单独验证模块 "), item(type="overreach", title="另一类")]}
    found, _, _, rep = F.parse_findings(dup, TEXTS)
    assert len(found) == 2 and rep["dropped_duplicates"] == 1
    names = ["消融实验缺失", "学习率没有来源", "只在合成数据上验证", "值得顺着局限继续读", "对比基线偏弱", "阈值缺少依据", "泛化主张证据不足",
             "模块动机未解释", "评价指标过于单一", "批大小未交代"]
    many = {"findings": [item(type=t, title=names[i], evidence_ords=[1 + i % 3]) for i, t in enumerate(list(F.FINDING_TYPES) * 2)]}
    found, _, _, rep = F.parse_findings(many, TEXTS)
    assert len(found) == F.MAX_FINDINGS and rep["capped"] == 2 and rep["dropped_duplicates"] == 0


# ---------------- 接口 ----------------
@pytest.fixture
def fenv(tmp_path, sample_pdf):
    fake = FakeMindmapLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        api.fake = fake
        item_ = api.import_pdf(sample_pdf)
        assert item_["status"] == "imported", item_
        api.pid = item_["paper_id"]
        api.configure_ai()
        yield api


def scan(api, expect="completed"):
    r = api.post(f"/papers/{api.pid}/findings")
    assert r.status_code == 200, r.text
    job = api.wait_job(r.json()["job_id"])
    assert job["status"] == expect, job
    return job


def listing(api, **params):
    r = api.get(f"/papers/{api.pid}/findings", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_stores_findings_with_verified_evidence(fenv):
    api = fenv
    assert listing(api)["status"] == "none"
    scan(api)
    d = listing(api)
    assert d["status"] == "ready" and d["verdict"] == "issues" and d["model"] == "strong-m" and d["scanned_at"]
    assert 1 <= len(d["items"]) <= 8
    with api.ctx.db.session() as conn:
        real = {r["ord"]: r["text"] for r in conn.execute("SELECT ord, text FROM passages WHERE paper_id = ?", (api.pid,)).fetchall()}
    for it in d["items"]:
        assert it["type"] in F.FINDING_TYPES and it["type_label"] == F.FINDING_TYPES[it["type"]]
        assert it["check_status"] in ("unchecked", "inferred") and it["user_state"] == "new"
        assert it["evidence"], "每条研究提示都必须带有可跳转的依据"
        for e in it["evidence"]:
            assert " ".join(e["quote"].rstrip("…").split()) in " ".join(real[e["ord"]].split()) and len(e["bbox"]) == 4
    kinds = {i["type"]: i["check_status"] for i in d["items"]}
    assert kinds["further_reading"] == "inferred" and kinds["unexplained_param"] == "inferred"


def test_prompt_forbids_forced_fault_finding_and_sends_only_excerpts(fenv):
    api = fenv
    api.fake.scan_mode = "none"
    scan(api)
    system, user = api.fake.last_system, api.fake.last_user
    assert "严禁为了凑数而挑毛病" in system and "no_issues" in system and "宁缺毋滥" in system
    assert "论文摘录" in user and "不能当作“论文没写”" in user
    # 不是全文：段落数少于等于预算，且只发送摘录
    reqs = api.get("/privacy/requests").json()["items"]
    sc = [r for r in reqs if r["task_type"] == "proactive_scan"]
    assert sc and sc[0]["chars_sent"] > 0 and sc[0]["includes"]["notes"] is False


def test_no_problem_paper_yields_no_findings_and_is_not_retried(fenv):
    api = fenv
    api.fake.scan_mode = "none"
    scan(api)
    d = listing(api)
    assert d["items"] == [] and d["verdict"] == "no_issues" and "充分" in d["summary"] and d["status"] == "ready"
    assert api.fake.scan_calls == 1  # “没有问题”是合法结果，不会为了凑数而重试


def test_findings_are_capped_deduped_and_filtered(fenv):
    api = fenv
    api.fake.scan_mode = "many"
    scan(api)
    assert len(listing(api)["items"]) == 8
    api.fake.scan_mode = "dup"
    scan(api)
    titles = [i["title"].strip() for i in listing(api)["items"]]
    assert titles.count("消融实验没有单独验证模块") == 1
    api.fake.scan_mode = "fabricated"
    scan(api)
    d = listing(api)
    assert len(d["items"]) == 1 and d["items"][0]["type"] == "overreach"  # 编造依据的那条被丢弃
    assert d["verification"]["report"]["dropped_no_evidence"] == 1
    api.fake.scan_mode = "lowconf"
    scan(api)
    d = listing(api)
    assert [i["type"] for i in d["items"]] == ["overreach"]
    api.fake.scan_mode = "badanchor"
    scan(api)
    it = listing(api)["items"][0]
    assert it["check_status"] == "inferred" and it["anchor"] is None and it["note"] == "模型引用的短语没有在所引原文里原样找到，请直接对照原文确认。" and "摘录" not in it["note"]


def test_user_marks_survive_rescan_and_ignored_stay_hidden(fenv):
    api = fenv
    scan(api)
    items = listing(api)["items"]
    a, b, c = items[0], items[1], items[2]
    assert api.patch(f"/papers/{api.pid}/findings/{a['id']}", {"user_state": "useful"}).json()["user_state"] == "useful"
    assert api.patch(f"/papers/{api.pid}/findings/{b['id']}", {"user_state": "ignored"}).json()["user_state"] == "ignored"
    marked = api.patch(f"/papers/{api.pid}/findings/{c['id']}", {"verified": True}).json()
    assert marked["check_status"] == "verified" and marked["ai_check_status"] in ("unchecked", "inferred")
    d = listing(api)
    assert d["ignored_count"] == 1 and b["id"] not in {i["id"] for i in d["items"]}
    assert b["id"] in {i["id"] for i in listing(api, include_ignored=True)["items"]}
    # 取消“已核查”回到后端给出的状态
    assert api.patch(f"/papers/{api.pid}/findings/{c['id']}", {"verified": False}).json()["check_status"] == c["ai_check_status"]
    api.patch(f"/papers/{api.pid}/findings/{c['id']}", {"verified": True})
    # 重新扫描（同样的结果）：沿用旧记录，用户的标记不丢，被忽略的不会冒出来
    scan(api)
    d2 = listing(api)
    ids = {i["id"] for i in d2["items"]}
    assert a["id"] in ids and c["id"] in ids and b["id"] not in ids and d2["ignored_count"] == 1
    assert next(i for i in d2["items"] if i["id"] == a["id"])["user_state"] == "useful"
    assert next(i for i in d2["items"] if i["id"] == c["id"])["check_status"] == "verified"


def test_rescan_removes_untouched_findings_that_no_longer_appear(fenv):
    api = fenv
    scan(api)
    first = listing(api)["items"]
    api.patch(f"/papers/{api.pid}/findings/{first[0]['id']}", {"user_state": "read"})
    api.fake.scan_mode = "none"
    scan(api)
    d = listing(api)
    assert d["items"] == [] and d["verdict"] == "no_issues"  # 新旧都没有的：未标记（含“已读”）的旧提示被移除


def test_patch_and_delete_validation(fenv):
    api = fenv
    scan(api)
    fid = listing(api)["items"][0]["id"]
    assert api.patch(f"/papers/{api.pid}/findings/{fid}", {"user_state": "bogus"}).status_code == 400
    assert api.patch(f"/papers/{api.pid}/findings/{fid}", {}).status_code == 400
    assert api.patch(f"/papers/{api.pid}/findings/nope", {"user_state": "read"}).status_code == 404
    assert api.delete(f"/papers/{api.pid}/findings/{fid}").json()["ok"] is True
    assert api.delete(f"/papers/{api.pid}/findings/{fid}").status_code == 404
    n = len(listing(api)["items"])
    assert api.delete(f"/papers/{api.pid}/findings").json()["removed"] == n
    assert listing(api)["status"] == "none"
    assert api.get("/papers/nope/findings").status_code == 404


def test_scan_retries_bad_json_once_then_fails_cleanly_and_can_be_retried(fenv):
    api = fenv
    api.fake.scan_mode = "notjson_once"
    scan(api)
    assert api.fake.scan_calls == 2 and listing(api)["verification"]["attempts"] == 2
    # 配置缺失
    fake2 = FakeMindmapLLM()
    fake2.scan_mode = "x"
    r = api.fake.fail_next.append((500, "boom"))
    api.fake.scan_mode = "issues"
    # 网关持续报错（含 provider 自身的重试、强模型失败后改用快速模型的那几次调用，以及后续几轮重试）
    api.fake.fail_next.extend([(500, "boom")] * 40)
    job = scan(api, expect="failed")
    assert job["error"]
    d = listing(api)
    assert d["status"] == "failed" and d["error"]
    api.fake.fail_next.clear()
    retry = api.post(f"/jobs/{job['id']}/retry").json()
    done = api.wait_job(retry.get("job_id") or retry["id"])
    assert done["status"] == "completed" and listing(api)["status"] == "ready"


def test_cancel_scan_leaves_no_partial_results(fenv):
    api = fenv
    api.fake.gate = threading.Event()
    job_id = api.post(f"/papers/{api.pid}/findings").json()["job_id"]
    deadline = time.time() + 10
    while api.fake.scan_calls == 0 and time.time() < deadline:
        time.sleep(0.02)
    assert listing(api)["status"] == "running"
    assert api.post(f"/papers/{api.pid}/findings").json()["job_id"] == job_id  # 重复点击复用同一个任务
    api.post(f"/jobs/{job_id}/cancel")
    api.fake.gate.set()
    assert api.wait_job(job_id)["status"] == "cancelled"
    d = listing(api)
    assert d["status"] == "none" and d["items"] == []


def test_scan_requires_configured_ai(tmp_path, sample_pdf):
    fake = FakeMindmapLLM()
    app = create_app(tmp_path / "d3", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        r = api.post(f"/papers/{pid}/findings")
        assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"
        assert api.post("/papers/nope/findings").status_code in (400, 404)


def test_extension_types_are_reserved_but_never_produced(fenv):
    assert set(F.EXTENSION_TYPES) == {"code_diff", "supp_inconsistency"}
    assert not (set(F.EXTENSION_TYPES) & set(F.FINDING_TYPES))
    found, _, _, rep = F.parse_findings({"findings": [item(type="code_diff", evidence_ords=[1])]}, TEXTS)
    assert found == [] and rep["dropped_bad_type"] == 1


def _well_supported_pdf(path):
    """合成一篇“主张都有支撑”的短文：每个主张都有对应实验与说明，参数有来源，结论没有越界。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 70
    blocks = [
        ("Sorting Small Arrays With Insertion Sort", 18, "hebo"),
        ("Abstract. We show that insertion sort is faster than quicksort for arrays of at most 16 elements. "
         "We support this claim with timing experiments on arrays of size 4, 8, 16 and 32, and we state the "
         "claim only for the tested range.", 10, "helv"),
        ("1 Method", 12, "hebo"),
        ("Insertion sort avoids recursion overhead. The cutoff of 16 elements was chosen because the timing "
         "experiment in Section 2 shows the break-even point between 16 and 32 elements.", 10, "helv"),
        ("2 Experiments", 12, "hebo"),
        ("We time both algorithms on 1000 random arrays for each size on one machine with a 3.0 GHz CPU. "
         "Table 1 reports mean times. Insertion sort wins for sizes 4, 8 and 16 and loses for size 32. "
         "An ablation that removes the early-exit check makes insertion sort 8 percent slower, confirming "
         "that the check matters.", 10, "helv"),
        ("3 Conclusion", 12, "hebo"),
        ("Insertion sort is faster for arrays of at most 16 elements on the tested machine. We do not claim "
         "this holds for other hardware or for larger arrays.", 10, "helv"),
    ]
    for text, size, font in blocks:
        rc = page.insert_textbox(pymupdf.Rect(50, y, 545, y + 160), text, fontsize=size, fontname=font)
        assert rc >= 0
        y += (160 - rc) + 8
    doc.save(path)
    return path


def test_well_supported_synthetic_paper_goes_through_the_whole_pipeline(tmp_path):
    """合成“主张都有支撑”的论文：流程能跑通，且提示词里没有任何诱导挑错的内容；
    真实模型对它不挑毛病的验证见 docs/MINDMAP.md（需要真实网关，不在单元测试里）。"""
    fake = FakeMindmapLLM()
    fake.scan_mode = "none"
    app = create_app(tmp_path / "d4", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        pdf = _well_supported_pdf(tmp_path / "ok.pdf")
        item_ = api.import_pdf(pdf)
        assert item_["status"] == "imported", item_
        api.pid = item_["paper_id"]
        api.configure_ai()
        scan(api)
        d = listing(api)
        assert d["verdict"] == "no_issues" and d["items"] == []
        assert "insertion sort" in fake.last_user.lower() and "quicksort" in fake.last_user.lower()


# ---------------- 正文里不能出现内部编号 / 检索措辞 ----------------
def test_humanize_turns_internal_refs_into_page_numbers():
    pages = {46: 12, 47: 12, 31: 7}
    assert humanize("论文在P46明确指出方法有效", pages) == "论文在第 12 页明确指出方法有效"
    assert humanize("见 [P46]、[P31]。", pages) == "见 第 12 页、第 7 页。"
    assert humanize("见【P46、P47】与（P31）", pages) == "见第 12 页与第 7 页"
    assert humanize("没有的编号 [P999] 直接去掉", pages) == "没有的编号 直接去掉"
    assert humanize("P3 蛋白和 P-value 不是内部编号", pages) == "P3 蛋白和 P-value 不是内部编号"
    assert humanize("摘录里没有看到，摘录中也没有", pages) == "所引原文里没有看到，所引原文中也没有"


def test_parse_findings_rewrites_refs_and_retrieval_wording():
    data = {"findings": [item(title="只在小图上验证 [P1]", detail="论文在P1明确指出了该设置（P2、P3）；摘录里没有看到更多说明。")]}
    found, _, _, _ = F.parse_findings(data, TEXTS, {1: 4, 2: 5, 3: 5})
    assert found[0]["title"] == "只在小图上验证 第 4 页"
    assert found[0]["detail"] == "论文在第 4 页明确指出了该设置第 5 页；所引原文里没有看到更多说明。"


def test_scan_never_shows_internal_numbers_to_the_user(fenv):
    import re

    api = fenv
    api.fake.scan_mode = "refs"
    scan(api)
    it = listing(api)["items"][0]
    assert not re.search(r"P\d+|摘录", it["title"] + it["detail"]), it
    assert re.search(r"第 \d+ 页", it["detail"]) and "所引原文" in it["detail"]
    assert it["evidence"], "页码之外，依据仍以可点击的引用显示"


# ---------------- 网关暂时不可用：继续等一等再试，而不是立刻放弃 ----------------
def test_gateway_502_is_retried_in_later_rounds(fenv):
    api = fenv
    # 超过 provider 一轮内的重试次数（强模型 3 次 + 强模型不可用后改用快速模型 3 次），靠后面的轮次恢复
    api.fake.fail_next.extend([(502, "bad gateway")] * 7)
    job = scan(api)
    assert any("稍等" in line for line in api.get(f"/jobs/{job['id']}").json()["log"]), "应当在任务日志里说明正在等待重试"
    assert listing(api)["status"] == "ready"


def test_gateway_502_that_never_recovers_fails_with_a_clear_message(fenv):
    api = fenv
    api.fake.fail_next.extend([(502, "bad gateway")] * 40)
    job = scan(api, expect="failed")
    assert "模型服务暂时不可用" in job["error"]
    d = listing(api)
    assert d["status"] == "failed" and "暂时不可用" in d["error"]


def test_legacy_rows_with_old_wording_are_rewritten_when_listed(fenv):
    api = fenv
    scan(api)
    first = listing(api)["items"][0]
    page = first["evidence"][0]["page"]
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE paper_findings SET detail = ?, note = ? WHERE id = ?",
                     (f"论文在P{first['evidence_ords'][0]}明确指出；摘录中没有更多说明。", "模型摘录的短语没有找到", first["id"]))
    it = next(i for i in listing(api)["items"] if i["id"] == first["id"])
    assert it["detail"] == f"论文在第 {page} 页明确指出；所引原文中没有更多说明。" and "摘录" not in it["note"]
