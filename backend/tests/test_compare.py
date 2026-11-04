from __future__ import annotations

import json
import re

import httpx
import pytest

import pdfgen
from f3_helpers import FakeWorld, make_world_api, oa_work
from paperdesk.compare.evidence import PaperPack, cited_pairs, normalize_cites, verify_text


# ---------------- 多论文引用的规范化与核实 ----------------
def test_multi_paper_citation_variants_are_normalized():
    cases = {
        "[A:P12]": "[A:P12]", "[A-P12]": "[A:P12]", "【A：P12】": "[A:P12]", "(B:12)": "[B:P12]", "[论文A:P12]": "[A:P12]",
        "[A:P12, P13]": "[A:P12][A:P13]", "[A:P12; B:P7]": "[A:P12][B:P7]", "[A:P12、B:P7]": "[A:P12][B:P7]",
        "[A:ABS]": "[A:ABS]", "[B:摘要]": "[B:ABS]", "[A:P12][B:P7]": "[A:P12][B:P7]",
    }
    for raw, expected in cases.items():
        assert normalize_cites(f"x {raw} y") == f"x {expected} y", raw
    # 普通括号、单篇引用写法不受影响
    for keep in ("（见图 3）", "[12]", "[P5]", "(a:b)", "[Figure 1]"):
        assert normalize_cites(f"x {keep} y") == f"x {keep} y", keep
    assert cited_pairs("甲 [A:P3] 乙 [B:ABS] 丙 [A:P3, P4]") == [("A", 3), ("B", None), ("A", 4)]


def _packs():
    a = PaperPack("A", "pa", "Paper A", 2020, passages={3: {"page": 1, "section_title": "Intro", "text": "Alpha text about routing. " * 20, "bbox": [0, 0, 1, 1]}})
    b = PaperPack("B", "pb", "Paper B", 2021, abstract="Only an abstract here about attention.", basis="abstract")
    return {"A": a, "B": b}


def test_verify_text_uses_database_text_and_flags_invented_citations():
    norm, ev, inv = verify_text("A 用了路由 [A:P3]；B 的摘要说明了注意力 [B:ABS]；但还有编造的 [A:P99] 和 [C:P1]。", _packs())
    assert [(e["label"], e["ord"], e["kind"]) for e in ev] == [("A", 3, "paper_text"), ("B", None, "abstract")]
    assert "Alpha text" in ev[0]["quote"] and ev[0]["page"] == 1 and ev[1]["quote"].startswith("Only an abstract")
    assert inv == ["A:P99", "C:P1"]
    assert verify_text("没有摘要的论文 [A:ABS]", _packs())[2] == ["A:ABS"]  # A 没有摘要，不能引用


# ---------------- 端到端 ----------------
def make_paper_a(tmp_path):
    """第二篇论文：参考文献里列出了第一篇（DAR）的标题 → 应产生有文本依据的“引用”边。"""
    old = (pdfgen.TITLE, pdfgen.REFERENCES)
    pdfgen.TITLE = "Learned Priority Routing for Network Recovery"
    pdfgen.REFERENCES = ["[1] Alice Zhang, Bob Miller and Carol Davies. Dual Attention Routing for Robust Cascade Recovery. Journal of Cascade Recovery, 2023.",
                         "[2] D. Foundational. Cascade models. Nature, 2010."]
    try:
        return pdfgen.build_paper(tmp_path / "paper_a.pdf", doi="10.5555/paper.a")
    finally:
        pdfgen.TITLE, pdfgen.REFERENCES = old


def compare_llm(world: FakeWorld, *, bad_cite: bool = True):
    def handler(system: str, user: str, body: dict):
        if "文献比较助手" not in system:
            return None
        ords = {}
        for label, ord_ in re.findall(r"^\[([A-Z]):P(\d+)\]", user, re.M):
            ords.setdefault(label, []).append(int(ord_))
        dims = re.search(r"需要覆盖的维度（rows）：(.+)", user).group(1).split("、")
        a, b = ords["A"][0], ords["B"][0]
        rows = [{"dimension": d, "cells": {"A": f"A 在{d}方面采用学习式优先级 [A:P{a}]", "B": f"B 在{d}方面采用双重注意力 [B:P{b}]"}} for d in dims]
        if bad_cite:
            rows[0]["cells"]["A"] += " [A:P9999]"
        return json.dumps({
            "overall": f"两篇论文都处理级联恢复，但机制不同 [A:P{a}][B:P{b}]", "rows": rows,
            "similarities": [f"都面向级联失效恢复 [A:P{a}][B:P{b}]"], "differences": [f"A 用学习式优先级，B 用双重注意力 [A:P{a}] [B:P{b}]"],
            "detail": f"## 机制差异\nA：学习式优先级 [A:P{a}]。B：双重注意力 [B:P{b}]。",
            "relations": [
                {"from": "A", "to": "B", "relation": "improves", "note": "A 在 B 的基础上改进", "cites": [f"A:P{a}", f"B:P{b}"]},
                {"from": "B", "to": "A", "relation": "alternative", "note": "没有依据的关系", "cites": []},
                {"from": "A", "to": "Z", "relation": "improves", "note": "不存在的论文", "cites": [f"A:P{a}"]},
                {"from": "A", "to": "B", "relation": "made_up", "note": "不存在的关系类型", "cites": [f"A:P{a}"]},
            ]})
    world.llm_handlers.append(handler)


def lineage_world(world: FakeWorld):
    shared = oa_work(9, "Foundational cascade models", doi="10.1/found", year=2010, cites=3000)
    b = oa_work(2, "Dual Attention Routing for Robust Cascade Recovery", doi="10.1234/dar.2023.001", refs=["https://openalex.org/W9"])
    a = oa_work(1, "Learned Priority Routing for Network Recovery", doi="10.5555/paper.a", refs=["https://openalex.org/W2", "https://openalex.org/W9"])

    def oa(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        flt = request.url.params.get("filter", "")
        if "10.5555" in url:
            return httpx.Response(200, json=a)
        if "10.1234" in url:
            return httpx.Response(200, json=b)
        if flt.startswith("openalex:"):
            return httpx.Response(200, json={"results": [shared]})
        return httpx.Response(200, json={"results": []})

    world.route("openalex", oa)


@pytest.fixture
def pair(tmp_path, sample_pdf):
    world = FakeWorld()
    lineage_world(world)
    compare_llm(world)
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(make_paper_a(tmp_path))["paper_id"]
        pb = api.import_pdf(sample_pdf)["paper_id"]
        yield api, world, pa, pb


def test_preflight_blocks_deep_comparison_without_fulltext(pair, scanned_pdf):
    api, world, pa, pb = pair
    ok = api.get("/compare/preflight", params={"paper_ids": f"{pa},{pb}", "mode": "method"}).json()
    assert ok["ok"] and [p["label"] for p in ok["papers"]] == ["A", "B"] and all(p["basis"] == "fulltext" for p in ok["papers"])
    scan = api.import_pdf(scanned_pdf)["paper_id"]
    bad = api.get("/compare/preflight", params={"paper_ids": f"{pa},{scan}", "mode": "method"}).json()
    assert not bad["ok"] and "既没有全文也没有摘要" in bad["blockers"][0]  # 扫描版没有摘要：任何模式都不行
    quick = api.get("/compare/preflight", params={"paper_ids": f"{pa},{scan}", "mode": "quick"}).json()
    assert not quick["ok"] and "无法比较" in quick["blockers"][0]
    assert api.post("/compare", {"paper_ids": [pa, scan], "mode": "method"}).status_code == 400
    assert api.post("/compare", {"paper_ids": [pa], "mode": "quick"}).status_code == 400  # 至少 2 篇


def test_method_comparison_end_to_end_with_verified_evidence_and_grounded_graph(pair):
    api, world, pa, pb = pair
    made = api.post("/compare", {"paper_ids": [pa, pb], "mode": "method"}).json()
    job = api.wait_job(made["job_id"], timeout=60)
    assert job["status"] == "completed", job
    r = api.get(f"/compare/{made['id']}").json()
    assert r["status"] == "ready" and r["mode"] == "method"
    res = r["result"]
    assert [p["label"] for p in res["papers"]] == ["A", "B"] and res["verification"]["status"] == "partial"
    assert res["verification"]["invalid_citations"] == ["A:P9999"]  # 编造的引用被识别
    assert res["dimensions"][:2] == ["研究问题", "核心挑战"] and len(res["rows"]) == 11
    cell = res["rows"][1]["cells"]["B"]
    assert cell["cites"] and cell["cites"][0]["label"] == "B" and "双重注意力" in cell["text"]
    # 证据 quote 来自数据库里的真实段落
    for e in res["evidence"]:
        with api.ctx.db.session() as conn:
            row = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND ord = ?", (e["paper_id"], e["ord"])).fetchone()
        assert row and e["quote"][:30] in row["text"]
    assert res["differences"] and res["detail"].startswith("## 机制差异")

    edges = {(e["from"], e["to"], e["relation"], e["basis"]): e for e in res["graph"]["edges"]}
    # 有依据的“引用”边：OpenAlex 参考文献数据 + A 的本地参考文献文本（合并为一条，依据以 OpenAlex 为准）
    cite = next(e for e in res["graph"]["edges"] if e["relation"] == "cites")
    assert (cite["from"], cite["to"], cite["basis"], cite["ai_inferred"]) == ("A", "B", "openalex", False)
    assert any(ev["section_title"] == "参考文献" and "Dual Attention Routing" in ev["quote"] for ev in cite["evidence"])
    # AI 提出的关系：只保留有可核实依据、两端存在、类型合法的那一条，并标注 AI 推断
    ai = [e for e in res["graph"]["edges"] if e["basis"] == "ai"]
    assert len(ai) == 1 and ai[0]["relation"] == "improves" and ai[0]["ai_inferred"] and len(ai[0]["evidence"]) == 2
    # 共同参考文献（OpenAlex）
    assert res["shared_references"][0]["title"] == "Foundational cascade models" and res["shared_references"][0]["cited_by"] == ["A", "B"]
    # 发给云端的内容被记录
    assert any(p["task_type"] == "compare_method" for p in api.get("/privacy/requests").json()["items"])
    assert api.get("/compare").json()["items"][0]["id"] == made["id"]


def test_quick_comparison_uses_only_four_dimensions_and_no_network(tmp_path, sample_pdf):
    world = FakeWorld()
    compare_llm(world, bad_cite=False)
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(make_paper_a(tmp_path))["paper_id"]
        pb = api.import_pdf(sample_pdf)["paper_id"]
        made = api.post("/compare", {"paper_ids": [pa, pb], "mode": "quick", "title": "我的比较"}).json()
        assert api.wait_job(made["job_id"], 60)["status"] == "completed"
        res = api.get(f"/compare/{made['id']}").json()["result"]
        assert res["dimensions"] == ["研究问题", "核心方法", "实验条件", "主要结论"] and res["verification"]["status"] == "verified"
        assert not any(r.url.host == "api.openalex.org" for r in world.requests)  # 快速比较不查引用数据
        assert api.get(f"/compare/{made['id']}").json()["title"] == "我的比较"


def test_lineage_mode_degrades_when_openalex_is_down_but_keeps_text_evidence(tmp_path, sample_pdf):
    world = FakeWorld()
    world.route("openalex", lambda r: httpx.Response(500, text="down"))
    compare_llm(world, bad_cite=False)
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(make_paper_a(tmp_path))["paper_id"]
        pb = api.import_pdf(sample_pdf)["paper_id"]
        made = api.post("/compare", {"paper_ids": [pa, pb], "mode": "lineage"}).json()
        assert api.wait_job(made["job_id"], 60)["status"] == "completed"
        res = api.get(f"/compare/{made['id']}").json()["result"]
        assert any("OpenAlex" in w for w in res["warnings"])
        cite = next(e for e in res["graph"]["edges"] if e["relation"] == "cites")
        assert cite["basis"] == "text_reference" and cite["evidence"][0]["kind"] == "paper_text"


def test_abstract_only_paper_is_flagged_in_quick_mode(tmp_path, sample_pdf):
    world = FakeWorld()
    compare_llm(world, bad_cite=False)
    # 这个测试里“论文 B”只有摘要：模型要用 [B:ABS]
    world.llm_handlers.insert(0, lambda s, u, b: json.dumps({
        "overall": "A 有全文；B 仅基于摘要 [B:ABS]", "rows": [], "similarities": [], "differences": [], "detail": "", "relations": []})
        if "文献比较助手" in s and "只有摘要" in u else None)
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(sample_pdf)["paper_id"]
        with api.ctx.db.session() as conn:
            conn.execute("INSERT INTO papers(id, title, authors_json, has_pdf, status, abstract, imported_at, metadata_source) "
                         "VALUES ('meta1', 'Metadata only paper', '[]', 0, 'metadata_only', 'We study attention in routing networks.', '2025-01-01T00:00:00Z', 'crossref')")
        pre = api.get("/compare/preflight", params={"paper_ids": f"{pa},meta1", "mode": "quick"}).json()
        assert pre["ok"] and "只能基于摘要" in pre["warnings"][0]
        assert not api.get("/compare/preflight", params={"paper_ids": f"{pa},meta1", "mode": "method"}).json()["ok"]
        made = api.post("/compare", {"paper_ids": [pa, "meta1"], "mode": "quick"}).json()
        assert api.wait_job(made["job_id"], 60)["status"] == "completed"
        res = api.get(f"/compare/{made['id']}").json()["result"]
        assert res["papers"][1]["basis"] == "abstract" and any("没有全文" in w for w in res["warnings"])
        assert any(e["kind"] == "abstract" and e["label"] == "B" for e in res["evidence"])


def test_compare_job_failure_is_recorded_and_rerun_works(tmp_path, sample_pdf):
    world = FakeWorld()  # 没有任何 LLM 回答 → 模型返回 {} ，格式不对
    api = make_world_api(tmp_path, world)
    with api.c:
        pa = api.import_pdf(make_paper_a(tmp_path))["paper_id"]
        pb = api.import_pdf(sample_pdf)["paper_id"]
        made = api.post("/compare", {"paper_ids": [pa, pb], "mode": "quick"}).json()
        job = api.wait_job(made["job_id"], 60)
        row = api.get(f"/compare/{made['id']}").json()
        assert job["status"] == "completed" and row["status"] == "ready"  # {} 是合法 JSON：行被标注“模型没有给出”，且无依据
        assert row["result"]["verification"]["status"] == "no_evidence"
        assert all("模型没有给出这一项" in c["text"] for r in row["result"]["rows"] for c in r["cells"].values())
        compare_llm(world, bad_cite=False)
        again = api.post(f"/compare/{made['id']}/rerun").json()
        assert api.wait_job(again["job_id"], 60)["status"] == "completed"
        assert api.get(f"/compare/{made['id']}").json()["result"]["verification"]["status"] == "verified"
        assert api.delete(f"/compare/{made['id']}").json()["ok"] and api.get(f"/compare/{made['id']}").status_code == 404


def test_compare_without_ai_configured_fails_clearly(tmp_path, sample_pdf):
    world = FakeWorld()
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        pa = api.import_pdf(make_paper_a(tmp_path))["paper_id"]
        pb = api.import_pdf(sample_pdf)["paper_id"]
        made = api.post("/compare", {"paper_ids": [pa, pb], "mode": "quick"}).json()
        job = api.wait_job(made["job_id"], 60)
        assert job["status"] == "failed" and "API Key" in job["error"]
        assert api.get(f"/compare/{made['id']}").json()["status"] == "failed"
