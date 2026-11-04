from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pymupdf
import pytest

from conftest import make_api
from paperdesk.ai import evidence, retrieval
from paperdesk.ai.router import TaskType
from paperdesk.materials import classify, datasets, discover
from paperdesk.materials import files as F
from paperdesk.materials import retrieval as supp_retrieval
from paperdesk.materials.labels import label_patterns, supp_label_fts

DOI = "10.1234/dar.2023.001"


def build_supplement(path: Path, doi: str | None = DOI, marker: str = "") -> Path:
    """合成补充材料 PDF：与正文同 DOI，含 Supplementary Section / Table 标签与可核对的事实。"""
    doc = pymupdf.open()

    def page(blocks):
        pg = doc.new_page(width=595, height=842)
        y = 60
        for text, size, font in blocks:
            rect = pymupdf.Rect(50, y, 545, y + 14 + size * 1.4 * (1 + len(text) // 70))
            pg.insert_textbox(rect, text, fontsize=size, fontname=font)
            y = rect.y1 + 10
        pg.insert_text((290, 815), "S", fontsize=8)
        return pg

    first = page([
        ("Supplementary Information for Dual Attention Routing for Robust Cascade Recovery", 16, "hebo"),
        (f"https://doi.org/{doi}" if doi else "", 9, "helv"),
        ("This document provides supplementary methods, derivations and additional experiments for the main paper. "
         "It contains the implementation details and the complete configuration used in all experiments. " + marker, 10, "helv"),
    ])
    _ = first
    page([
        ("Supplementary Section II.D.3 Time complexity", 14, "hebo"),
        ("The time complexity of DAR is O(|E| + |V| log |V|) per recovery step because attention is computed over "
         "neighbours only and the temporal module keeps a fixed-size window. The memory footprint scales linearly "
         "with the number of edges in the network.", 10, "helv"),
    ])
    page([
        ("Supplementary Table 5 | Hyperparameters used in all experiments.", 9, "helv"),
        ("We use a learning rate of 0.001, a batch size of 64 and train for 200 epochs on every dataset. "
         "The hidden dimension of both attention modules is 128 and we apply dropout with probability 0.1.", 10, "helv"),
    ])
    page([
        ("Supplementary Section III Additional experiments", 14, "hebo"),
        ("We further evaluate DAR on two additional synthetic networks and find consistent improvements in recovery "
         "time over all baselines, which supports the robustness of the proposed approach.", 10, "helv"),
    ])
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="session")
def supp_pdf(tmp_path_factory) -> Path:
    return build_supplement(tmp_path_factory.mktemp("supp") / "SI.pdf")


def wait_ok(api, job_id: str) -> dict:
    job = api.wait_job(job_id, timeout=60)
    assert job["status"] == "completed", job
    return job


@pytest.fixture
def with_supp(api, sample_pdf, supp_pdf):
    """已导入正文、已配置假模型，并把 supp_pdf 添加为补充材料。返回 (api, 主论文 id, 补充材料记录)。"""
    pid = api.import_pdf(sample_pdf)["paper_id"]
    api.configure_ai()
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp_pdf)]})
    assert r.status_code == 200, r.text
    wait_ok(api, r.json()["job_id"])
    asset = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    assert asset["download_status"] == "done", asset
    return api, pid, asset


# ---------------- 补充材料成为独立概念（真实问题：同 DOI 被当成重复）----------------
def test_same_doi_pdf_is_offered_as_supplement_instead_of_plain_duplicate(api, sample_pdf, supp_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    item = api.import_pdf(supp_pdf)
    assert item["status"] == "duplicate" and item["paper_id"] == pid
    assert item["can_attach_as_supplement"] is True and item["target_paper_id"] == pid
    assert item["supplement_hint"] is True  # 文件首页含 Supplementary 字样
    # 完全相同的文件仍是普通重复，不提供“作为补充材料”
    same = api.import_pdf(sample_pdf)
    assert same["status"] == "duplicate" and not same.get("can_attach_as_supplement")
    # 用户确认后一键作为补充材料添加
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp_pdf)]})
    wait_ok(api, r.json()["job_id"])
    assert len(api.get(f"/papers/{pid}/supplements").json()["items"]) == 1


def test_supplement_pdf_becomes_child_paper_hidden_from_library(with_supp):
    api, pid, asset = with_supp
    assert asset["kind"] == "pdf" and asset["child_paper_id"] and asset["child"]["page_count"] == 4
    assert asset["category"] in ("方法补充", "公式推导", "实验配置", "额外实验")  # 规则分类
    assert asset["match_status"] == "用户确认" and asset["source"] == "local"
    # 文献库只显示主论文；主论文带补充材料数量
    lib = api.get("/papers").json()
    assert lib["total"] == 1 and lib["items"][0]["id"] == pid and lib["items"][0]["supplement_count"] == 1
    assert api.get("/system/info").json()["paper_count"] == 1
    # 子论文可以被阅读器完整使用：详情、大纲、检索、文件
    cid = asset["child_paper_id"]
    d = api.get(f"/papers/{cid}").json()
    assert d["role"] == "supplement" and d["parent_id"] == pid and d["parent_title"].startswith("Dual Attention")
    assert api.get(f"/papers/{cid}/file").content[:5] == b"%PDF-"
    hits = api.get(f"/papers/{cid}/search", params={"q": "time complexity"}).json()["items"]
    assert hits and "complexity" in hits[0]["snippet"].lower()
    assert api.get(f"/papers/{cid}/outline").json()["items"]
    # 同一文件不能重复添加；相同哈希也不会变成第二个独立论文
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(Path(d['original_path']))]})
    job = api.wait_job(r.json()["job_id"])
    assert job["result"]["items"][0]["status"] == "failed" and "补充材料" in job["result"]["items"][0]["message"]


def test_delete_asset_and_main_paper_clean_up_children_but_never_user_files(api, sample_pdf, supp_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp_pdf)]})
    wait_ok(api, r.json()["job_id"])
    asset = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    child_dir = api.ctx.paths.paper_dir(asset["child_paper_id"])
    assert child_dir.exists()
    assert api.delete(f"/supplements/{asset['id']}").json()["ok"]
    assert not child_dir.exists() and supp_pdf.exists()
    assert api.get(f"/papers/{asset['child_paper_id']}").status_code == 404
    # 再加一次，然后删除主论文：子论文与目录一并清理
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp_pdf)]}).json()["job_id"])
    asset = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    child_dir = api.ctx.paths.paper_dir(asset["child_paper_id"])
    assert api.delete(f"/papers/{pid}").json()["ok"]
    assert not child_dir.exists() and supp_pdf.exists() and sample_pdf.exists()
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM supplementary_assets").fetchone()[0] == 0


def test_convert_existing_library_paper_into_supplement(api, sample_pdf, tmp_path):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    other = build_supplement(tmp_path / "other_si.pdf", doi=None, marker="standalone")
    other_id = api.import_pdf(other)["paper_id"]  # 先被当作独立论文导入
    assert api.get("/papers").json()["total"] == 2
    a = api.post(f"/papers/{pid}/supplements/attach-paper", {"paper_id": other_id})
    assert a.status_code == 200, a.text
    assert a.json()["child_paper_id"] == other_id and a.json()["download_status"] == "done"
    assert api.get("/papers").json()["total"] == 1
    assert api.post(f"/papers/{pid}/supplements/attach-paper", {"paper_id": other_id}).status_code == 409
    assert api.post(f"/papers/{pid}/supplements/attach-paper", {"paper_id": pid}).status_code == 400
    # 补充材料本身不能再挂补充材料
    assert api.get(f"/papers/{other_id}/supplements").status_code == 400


# ---------------- 授权 ----------------
def test_authorization_is_per_session_and_resettable(with_supp):
    api, pid, _ = with_supp
    assert api.get(f"/papers/{pid}/supplements/authorization").json() == {
        "granted": False, "decision": None, "decided_at": None, "ttl_seconds": 8 * 3600}
    assert api.post(f"/papers/{pid}/supplements/authorization", {"allow": True}).json()["granted"] is True
    assert api.get(f"/papers/{pid}/supplements").json()["authorization"]["decision"] == "allow"
    assert api.post(f"/papers/{pid}/supplements/authorization", {"allow": False}).json()["decision"] == "deny"
    assert api.post(f"/papers/{pid}/supplements/authorization", {"allow": None}).json()["decision"] is None  # 重新打开 → 重新询问


def test_ttl_expiry_revokes_grant(with_supp):
    from paperdesk.materials import grants

    api, pid, _ = with_supp
    grants.set_decision(api.ctx, pid, True)
    assert grants.is_granted(api.ctx, pid)
    api.ctx.__dict__["_supplement_grants"][pid]["expires"] = 0
    assert not grants.is_granted(api.ctx, pid) and grants.state(api.ctx, pid)["decision"] is None


# ---------------- 问答集成：未授权不读取、授权后带 [S1P..] 引用 ----------------
def _ask(api, pid, question, **kw):
    return api.sse("/ai/ask", {"paper_id": pid, "question": question, "intent": kw.pop("intent", "free"), **kw})


def _last_user_prompt(api) -> str:
    msgs = api.fake.requests[-1]["body"]["messages"]
    return "\n".join(m["content"] for m in msgs if m["role"] == "user")


def test_unauthorized_ask_emits_permission_event_and_sends_no_supplement_text(with_supp):
    api, pid, asset = with_supp
    ev = _ask(api, pid, "Please derive the time complexity as in Supplementary Section II.D.3")
    names = [n for n, _ in ev]
    assert names[0] == "meta" and "needs_supplement_permission" in names and names[-1] == "done"
    need = dict(ev)["needs_supplement_permission"]
    assert "Supplementary Section II.D.3" in need["labels"] and need["card_id"] == ev[0][1]["card_id"]
    assert need["supplements"][0]["asset_id"] == asset["id"] and need["reason"]
    # 没有读取/发送任何补充材料内容
    prompt = _last_user_prompt(api)
    assert "O(|E|" not in prompt and "[S1P" not in prompt and "补充材料清单" not in prompt
    meta = ev[0][1]["context"]
    assert meta["includes"]["supplements"] is False and meta["supplement_docs"] == []
    card = ev[-1][1]["card"]
    assert all(e["kind"] == "paper_text" for e in card["evidence"])


def test_detail_question_triggers_permission_only_when_main_text_cites_supplement(with_supp):
    api, pid, _ = with_supp
    # 通用问题、正文高相关段落里也没有 Supplementary 引用 → 不打扰用户
    ev = _ask(api, pid, "Explain the temporal attention module in detail")
    assert "needs_supplement_permission" not in [n for n, _ in ev]


def test_authorized_ask_includes_supplement_passages_with_s_citations(with_supp):
    api, pid, asset = with_supp
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": True})
    ev = _ask(api, pid, "What is the time complexity of DAR? See Supplementary Section II.D.3 for the derivation")
    names = [n for n, _ in ev]
    assert "needs_supplement_permission" not in names and names[-1] == "done"
    prompt = _last_user_prompt(api)
    assert "【补充材料清单】" in prompt and "O(|E| + |V| log |V|)" in prompt and "[S1P" in prompt
    system = api.fake.requests[-1]["body"]["messages"][0]["content"]
    assert "[S序号P序号]" in system
    meta = ev[0][1]["context"]
    assert meta["includes"]["supplements"] is True
    assert meta["supplement_docs"][0]["n"] == 1 and meta["supplement_docs"][0]["asset_id"] == asset["id"]
    assert meta["supplement_passages"], "应列出被发送的补充材料段落"
    card = ev[-1][1]["card"]
    supp_ev = [e for e in card["evidence"] if e["kind"] == "supplement_text"]
    assert supp_ev, card["evidence"]
    e = supp_ev[0]
    assert e["ref"].startswith("S1P") and e["source"]["role"] == "supplement" and e["source"]["paper_id"] == asset["child_paper_id"]
    assert e["page"] >= 1 and len(e["bbox"]) == 4 and e["quote"]
    assert card["verification"]["status"] == "verified" and card["verification"]["invalid_refs"] == []
    # 标签命中的 caption/章节优先：问题点名的 Section II.D.3 内容一定在上下文里
    assert any(p["doc"] == 1 for p in meta["supplement_passages"])
    # 隐私日志如实记录发送了补充材料
    priv = api.get("/privacy/requests").json()["items"][0]
    assert priv["includes"]["supplements"] is True and "补充材料" in priv["summary"]


def test_invented_supplement_citation_is_flagged_not_trusted(with_supp):
    api, pid, _ = with_supp
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": True})
    ev = _ask(api, pid, "INVALID_SCITE what is the time complexity in Supplementary Section II.D.3")
    card = ev[-1][1]["card"]
    assert "S1P9999" in card["verification"]["invalid_refs"] and card["verification"]["status"] == "partial"
    assert all(e["ref"] != "S1P9999" for e in card["evidence"])


def test_revoking_authorization_stops_using_supplements(with_supp):
    api, pid, _ = with_supp
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": True})
    _ask(api, pid, "time complexity Supplementary Section II.D.3")
    assert "[S1P" in _last_user_prompt(api)
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": False})
    ev = _ask(api, pid, "time complexity Supplementary Section II.D.3")
    assert "[S1P" not in _last_user_prompt(api) and "needs_supplement_permission" in [n for n, _ in ev]


# ---------------- 引用规范化与核实（单元）----------------
def test_supplement_citation_normalization_variants():
    n = evidence.normalize_cites
    assert n("x [S1P12] y") == "x [S1P12] y"
    assert n("x [S1-P12] y") == "x [S1P12] y"
    assert n("x [S1:P12] y") == "x [S1P12] y"
    assert n("x （S2 P3） y") == "x [S2P3] y"
    assert n("x [S1P12, 14] y") == "x [S1P12][S1P14] y"
    assert n("x [P3, S1P12] y") == "x [P3][S1P12] y"
    assert n("x [依据：S1P5、S1P6] y") == "x [S1P5][S1P6] y"
    assert n("x [P3, P7] y") == "x [P3][P7] y"  # 原有格式不变
    assert evidence.cited_refs("[S1P2] a [P2] b [S1P2] c [S2P2]") == [(1, 2), (0, 2), (2, 2)]
    assert evidence.cited_ords("[S1P2] a [P2]") == [2]  # 补充材料引用不会混进正文序号


def _mixed_pack():
    main = retrieval.CtxPassage(3, 1, "paragraph", "Method", "Main text about attention.", [0, 0, 1, 1])
    supp = retrieval.CtxPassage(3, 2, "paragraph", "Supplementary Section II", "Supplement text: complexity is O(n).",
                                [0, 0, 1, 1], doc=1)
    docs = [{"n": 1, "paper_id": "child1", "asset_id": "a1", "filename": "SI.pdf", "category": "公式推导", "label": "补充材料 1 · SI.pdf"}]
    return retrieval.ContextPack("T", 2023, [], [], [main, supp], None, [], [], supp_docs=docs)


def test_same_ord_in_main_and_supplement_are_not_confused():
    pack = _mixed_pack()
    assert pack.ords == [3] and pack.supp_refs == [(1, 3)]
    assert pack.passage(3).text.startswith("Main") and pack.passage(3, 1).text.startswith("Supplement")
    text = pack.render_excerpts()
    assert "[P3] (第1页" in text and "[S1P3] (补充材料1 第2页" in text
    md = "## 直接回答\n注意力见正文 [P3]，复杂度见补充材料 [S1P3]，另一条 [S1P9] 和 [P9]。\n## 依据\n- [S1P3] 复杂度为 O(n)\n"
    parsed = evidence.parse_answer(md, pack)
    refs = [e["ref"] for e in parsed.evidence]
    assert refs == ["P3", "S1P3"]
    assert parsed.evidence[1]["kind"] == "supplement_text" and "complexity" in parsed.evidence[1]["quote"]
    assert parsed.evidence[1]["source"] == {"paper_id": "child1", "role": "supplement", "doc": 1,
                                            "label": "补充材料 1 · SI.pdf", "asset_id": "a1"}
    assert parsed.verification["invalid_refs"] == ["S1P9", "P9"] and parsed.verification["invalid_citations"] == [9]
    assert parsed.verification["status"] == "partial"
    assert parsed.answer["evidence_notes"][0]["ref"] == "S1P3"


# ---------------- 标签抽取与补充材料检索 ----------------
def test_supplementary_label_extraction():
    ex = supp_retrieval.extract_labels
    assert ex("see Supplementary Fig. 14 and Supplementary Table 5") == ["Supplementary Fig. 14", "Supplementary Table 5"]
    assert ex("(Supplementary Section IV.B) and Supplementary equation (27)") == ["Supplementary Section IV.B", "Supplementary Equation 27"]
    assert ex("Supplementary Section II.D.3") == ["Supplementary Section II.D.3"]
    assert ex("参见补充图 3 和补充表 2") == ["补充图3", "补充表2"]
    assert ex("nothing here, only a Supplementary approach") == []


def test_retrieval_prioritizes_label_matches_and_respects_authorization(with_supp):
    api, pid, asset = with_supp
    docs = supp_retrieval.list_docs(api.ctx, pid)
    assert docs[0]["n"] == 1 and docs[0]["paper_id"] == asset["child_paper_id"]
    pack = retrieval.build_context(api.ctx, pid, question="Supplementary Table 5 hyperparameters learning rate", selection=None,
                                   task=TaskType.EXPERIMENT, supplements=docs)
    sp = [p for p in pack.passages if p.doc == 1]
    assert sp and any("learning rate of 0.001" in p.text for p in sp)
    assert "label" in {r for p in sp for r in p.reasons}
    assert sp[0].reasons & {"supplement"}
    # 不传 supplements 就绝不包含补充文档
    plain = retrieval.build_context(api.ctx, pid, question="Supplementary Table 5 hyperparameters", selection=None,
                                    task=TaskType.EXPERIMENT)
    assert all(p.doc == 0 for p in plain.passages) and plain.supp_docs == []


def build_si_style_supplement(path: Path) -> Path:
    """真实期刊的写法：正文说 “Supplementary Table 5”，附录里的图注写成 “Table S5: …”、“Fig. S3 | …”。"""
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_textbox(pymupdf.Rect(50, 60, 545, 120), "Supplementary Information for Dual Attention Routing", fontsize=14, fontname="hebo")
    pg.insert_textbox(pymupdf.Rect(50, 140, 545, 240), "Table S5: Time complexity analysis. FINDER runs in O(|V| log |V|) per step on sparse graphs. "
                      "Running times were measured on a single GPU.", fontsize=10, fontname="helv")
    pg.insert_textbox(pymupdf.Rect(50, 300, 545, 400), "Fig. S3 | Training curves for synthetic networks of size 1000 to 5000 nodes.", fontsize=10, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def test_label_patterns_and_fts_cover_table_s5_convention():
    pats = label_patterns("Supplementary Table 5")
    assert "Table 5%" in pats and "Table S5%" in pats
    assert "Fig. S3%" in label_patterns("Supplementary Fig. 3")
    assert supp_label_fts("Supplementary Table 5") == '("table" AND "s5")'
    assert supp_label_fts("Supplementary Fig. 12") == '("fig"* AND "s12")'
    assert supp_label_fts("how does it work") is None
    # 用户直接写 S5 也规范成同一个标签
    assert supp_retrieval.extract_labels("see Supplementary Table S5") == ["Supplementary Table 5"]


def test_search_and_retrieval_find_table_s5_when_asked_for_supplementary_table_5(api, sample_pdf, tmp_path):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    si = build_si_style_supplement(tmp_path / "si_style.pdf")
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(si)]}).json()["job_id"])
    asset = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    child = asset["child_paper_id"]
    hits = api.get(f"/papers/{child}/search", params={"q": "Supplementary Table 5"}).json()["items"]
    assert hits and any("Table S5" in h["snippet"] for h in hits), hits
    assert api.get(f"/papers/{child}/search", params={"q": "Supplementary Fig. 3"}).json()["items"]
    docs = supp_retrieval.list_docs(api.ctx, pid)
    pack = retrieval.build_context(api.ctx, pid, question="What does Supplementary Table 5 report?", selection=None,
                                   task=TaskType.EXPERIMENT, supplements=docs)
    sp = [p for p in pack.passages if p.doc == 1]
    assert any("Table S5" in p.text and "label" in p.reasons for p in sp), [(p.ord, p.reasons) for p in sp]


# ---------------- 分类 / 阅读建议 / AI ----------------
def test_rule_classification_by_name_and_content():
    assert classify.rule_category("code.zip")[0] == "代码"
    assert classify.rule_category("results.csv")[0] == "数据集"
    assert classify.rule_category("S1_proofs.pdf", "Proof of Lemma 2 and Theorem 1")[0] == "公式推导"
    assert classify.rule_category("S2.pdf", "Hyperparameter configuration and implementation details")[0] == "实验配置"
    assert classify.rule_category("S3.pdf", "Additional experiments and ablation results")[0] == "额外实验"
    assert classify.rule_category("x.pdf", "nothing relevant at all")[0] == "其他"


def test_ai_actions_require_authorization_then_classify_and_recommend(with_supp, tmp_path):
    api, pid, asset = with_supp
    r = api.post(f"/supplements/{asset['id']}/analyze")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_authorized"
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": True})
    wait_ok(api, api.post(f"/supplements/{asset['id']}/analyze").json()["job_id"])
    a = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    assert a["category"] == "实验配置" and a["category_source"] == "ai" and "超参数" in a["summary"]
    priv = api.get("/privacy/requests").json()["items"][0]
    assert priv["task_type"] == "supplement_classify" and priv["includes"]["supplements"] is True
    # 用户手动指定的类别优先，不再被规则/AI 覆盖
    api.patch(f"/supplements/{a['id']}", {"category": "公式推导"})
    assert api.get(f"/papers/{pid}/supplements").json()["items"][0]["category_source"] == "user"

    # 阅读顺序：没有授权 → 规则；授权且有 ≥2 个文件 → AI，编造的编号被丢弃
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(csv)]}).json()["job_id"])
    rec = api.post(f"/papers/{pid}/supplements/recommend", {"question": "超参数"}).json()
    assert rec["source"] == "ai" and [i["order"] for i in rec["items"]] == [1, 2]
    ids = {x["id"] for x in api.get(f"/papers/{pid}/supplements").json()["items"]}
    assert {i["asset_id"] for i in rec["items"]} == ids and rec["items"][0]["reason"] == "与你的问题最相关"
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": False})
    rec2 = api.post(f"/papers/{pid}/supplements/recommend", {"question": "x"}).json()
    assert rec2["source"] == "rules" and "未授权" in rec2["note"]


# ---------------- 下载策略与审批 ----------------
def _pdf_bytes(tmp_path) -> bytes:
    return build_supplement(tmp_path / "dl.pdf", doi=None, marker="downloaded").read_bytes()


def test_small_remote_file_downloads_after_user_confirms_candidate(tmp_path, sample_pdf):
    data = _pdf_bytes(tmp_path)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.method} {req.url}")
        if str(req.url) == "https://pub.example.org/esm/si.pdf":
            return httpx.Response(200, content=data, headers={"content-type": "application/pdf", "content-length": str(len(data))})
        return httpx.Response(404)

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        r = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [{
            "url": "https://pub.example.org/esm/si.pdf", "filename": "si.pdf", "size": len(data), "source": "publisher",
            "basis": "出版商页面上的补充材料链接"}]})
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        assert item["match_status"] == "用户确认" and item["match_basis"] == "出版商页面上的补充材料链接"
        assert item["download_status"] in ("pending", "downloading", "done")
        wait_ok(api, item["job_id"])
        done = api.get(f"/papers/{pid}/supplements").json()["items"][0]
        assert done["download_status"] == "done" and done["child_paper_id"] and done["source_url"] == "https://pub.example.org/esm/si.pdf"
        # 同一链接不会重复关联
        again = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [{"url": "https://pub.example.org/esm/si.pdf"}]}).json()
        assert again["items"] == [] and again["skipped_existing"] == 1


def test_large_download_waits_for_approval_and_reject_cannot_be_bypassed_by_retry(tmp_path, sample_pdf):
    data = _pdf_bytes(tmp_path)
    calls = {"get": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and "x.example.org" in str(req.url):  # 导入样例 PDF 时的元数据查询不算
            calls["get"] += 1
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf", "content-length": str(len(data))})

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        big = {"url": "https://x.example.org/big.pdf", "filename": "big.pdf", "size": 50 * 1024 * 1024}
        item = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [big]}).json()["items"][0]
        assert item["download_status"] == "waiting_approval"
        job = api.get(f"/jobs/{item['job_id']}").json()
        assert job["status"] == "waiting_approval" and job["can_approve"] and calls["get"] == 0  # 批准前没有任何下载
        # 拒绝：任务取消、材料标记 rejected；重试不能绕过批准
        rej = api.post(f"/supplements/{item['id']}/reject").json()
        assert rej["download_status"] == "rejected" and api.get(f"/jobs/{item['job_id']}").json()["status"] == "cancelled"
        new_id = api.post(f"/jobs/{item['job_id']}/retry").json()["job_id"]
        assert api.wait_job(new_id)["status"] == "failed" and calls["get"] == 0
        # 另一个：批准后才开始下载
        c2 = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [{**big, "url": "https://x.example.org/big2.pdf", "filename": "big2.pdf"}]}).json()["items"][0]
        assert api.post(f"/supplements/{c2['id']}/approve").json()["download_status"] in ("pending", "downloading", "processing", "done")
        wait_ok(api, c2["job_id"])
        assert api.get(f"/papers/{pid}/supplements").json()["items"][-1]["download_status"] == "done" and calls["get"] == 1
        assert api.post(f"/supplements/{c2['id']}/approve").status_code == 409  # 重复批准无效


def test_retry_endpoint_requeues_failed_and_rerequests_rejected(tmp_path, sample_pdf):
    data = _pdf_bytes(tmp_path)
    state = {"up": False, "get": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "x.example.org" not in str(req.url):
            return httpx.Response(404)
        if req.method == "GET":
            state["get"] += 1
        if not state["up"]:
            return httpx.Response(500)
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf", "content-length": str(len(data))})

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        small = {"url": "https://x.example.org/a.pdf", "filename": "a.pdf", "size": len(data)}
        item = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [small]}).json()["items"][0]
        assert api.wait_job(item["job_id"], timeout=60)["status"] == "failed"
        failed = api.get(f"/papers/{pid}/supplements").json()["items"][0]
        assert failed["download_status"] == "failed" and "500" in failed["download_error"]
        assert api.post(f"/supplements/{failed['id']}/approve").status_code == 409  # 只有等待批准的才能批准
        state["up"] = True
        re = api.post(f"/supplements/{failed['id']}/retry")
        assert re.status_code == 200 and re.json()["download_status"] in ("pending", "downloading", "processing", "done")
        assert re.json()["job_id"] != item["job_id"]
        wait_ok(api, re.json()["job_id"])
        assert api.get(f"/papers/{pid}/supplements").json()["items"][0]["download_status"] == "done"
        assert api.post(f"/supplements/{failed['id']}/retry").status_code == 409  # 已完成的不能重试

        # 被拒绝的大文件：重试只会重新进入“等待批准”，不会直接下载
        before = state["get"]
        big = {"url": "https://x.example.org/big.pdf", "filename": "big.pdf", "size": 50 * 1024 * 1024}
        b = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [big]}).json()["items"][0]
        api.post(f"/supplements/{b['id']}/reject")
        again = api.post(f"/supplements/{b['id']}/retry").json()
        assert again["download_status"] == "waiting_approval" and again["job_id"] != b["job_id"]
        assert api.get(f"/jobs/{again['job_id']}").json()["status"] == "waiting_approval"
        assert state["get"] == before  # 批准前没有任何下载
        assert api.post(f"/supplements/{b['id']}/approve").json()["download_status"] in ("pending", "downloading", "processing", "done")


def test_unknown_size_over_auto_limit_is_deferred_to_approval(tmp_path, sample_pdf, monkeypatch):
    monkeypatch.setattr(F, "AUTO_DOWNLOAD_LIMIT", 1024)  # 让“很小的文件”也超过自动上限
    data = _pdf_bytes(tmp_path)

    def handler(req):
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})  # 不带 content-length

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        item = api.post(f"/papers/{pid}/supplements/url", {"url": "https://x.example.org/unknown.pdf"}).json()["items"][0]
        job = wait_ok(api, item["job_id"])
        assert job["result"]["deferred"] is True
        cur = api.get(f"/papers/{pid}/supplements").json()["items"][0]
        assert cur["download_status"] == "waiting_approval" and cur["job_id"] != item["job_id"]
        assert api.get(f"/jobs/{cur['job_id']}").json()["status"] == "waiting_approval"
        api.post(f"/supplements/{cur['id']}/approve")
        wait_ok(api, cur["job_id"])
        assert api.get(f"/papers/{pid}/supplements").json()["items"][0]["download_status"] == "done"


def test_download_of_login_page_fails_with_clear_reason(tmp_path, sample_pdf):
    def handler(req):
        return httpx.Response(200, text="<html><body>Please sign in to download</body></html>", headers={"content-type": "text/html"})

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        item = api.post(f"/papers/{pid}/supplements/url", {"url": "https://paywall.example.org/s.pdf"}).json()["items"][0]
        job = api.wait_job(item["job_id"])
        assert job["status"] == "failed"
        cur = api.get(f"/papers/{pid}/supplements").json()["items"][0]
        assert cur["download_status"] == "failed" and "网页" in cur["download_error"]


def test_forbidden_http_gives_manual_download_hint_and_never_bypasses(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path / "d", handler=lambda r: httpx.Response(403))
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        item = api.post(f"/papers/{pid}/supplements/url", {"url": "https://inst.example.org/s.pdf"}).json()["items"][0]
        assert api.wait_job(item["job_id"])["status"] == "failed"
        err = api.get(f"/papers/{pid}/supplements").json()["items"][0]["download_error"]
        assert "机构权限" in err and "添加本地文件" in err


# ---------------- 自动寻找 ----------------
LANDING = """<html><body>
<a href="/articles/123">Article</a>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs42256-020-0177-2/MediaObjects/42256_2020_177_MOESM1_ESM.pdf">Supplementary information</a>
<a href="https://static-content.springer.com/esm/art%3A10.1038%2Fs42256-020-0177-2/MediaObjects/42256_2020_177_MOESM2_ESM.xlsx">Source data</a>
<a href="https://example.org/cookies.pdf">Cookie policy</a>
<a href="https://example.org/random.pdf">Some other paper.pdf</a>
</body></html>"""


def test_discover_candidates_come_from_the_papers_own_page_and_need_confirmation(tmp_path, sample_pdf):
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://doi.org/"):
            return httpx.Response(200, text=LANDING, headers={"content-type": "text/html"})
        if "api.crossref.org" in url:
            return httpx.Response(200, json={"message": {"relation": {"is-supplemented-by": [{"id-type": "doi", "id": "10.5281/zenodo.1"}]}}})
        if req.method == "HEAD" and "MOESM1" in url:
            return httpx.Response(200, headers={"content-length": "4000000"})
        if req.method == "HEAD" and "MOESM2" in url:
            return httpx.Response(200, headers={"content-length": "52000000"})
        return httpx.Response(404)

    api, _ = make_api(tmp_path / "d", handler=handler)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        job = wait_ok(api, api.post(f"/papers/{pid}/supplements/discover").json()["job_id"])
        cands = job["result"]["candidates"]
        names = {c["filename"] for c in cands}
        assert "42256_2020_177_MOESM1_ESM.pdf" in names and "42256_2020_177_MOESM2_ESM.xlsx" in names
        assert "cookies.pdf" not in names and "random.pdf" not in names  # 只按文件名/后缀不能认定属于本文
        c1 = next(c for c in cands if "MOESM1" in c["filename"])
        assert c1["size"] == 4000000 and c1["source"] == "publisher" and "出版商页面" in c1["basis"]
        assert any(c["source"] == "crossref" and "is-supplemented-by" in c["basis"] for c in cands)
        c2 = next(c for c in cands if "MOESM2" in c["filename"])
        assert c2["category_guess"] == "数据集" and c2["size"] == 52000000
        # 只发现、不关联：没有任何材料被建立
        assert api.get(f"/papers/{pid}/supplements").json()["items"] == []
        # 用户确认大文件候选 → 进入等待批准，而不是直接下载
        linked = api.post(f"/papers/{pid}/supplements/link-remote", {"items": [c2]}).json()["items"][0]
        assert linked["download_status"] == "waiting_approval" and linked["match_basis"] == c2["basis"]


def test_discover_reports_blocked_publisher_pages(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path / "d", handler=lambda r: httpx.Response(403))
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        job = wait_ok(api, api.post(f"/papers/{pid}/supplements/discover").json()["job_id"])
        assert job["result"]["candidates"] == []
        assert any("机构权限" in n for n in job["result"]["notes"])


def test_github_repo_mentions_become_code_candidates():
    assert discover.repos_in_text("Code is at https://github.com/foo/bar-baz. See also github.com/x") == [("foo", "bar-baz")]
    assert discover.repos_in_text("https://github.com/foo/bar.git, and https://github.com/Foo/Bar") == [("foo", "bar")]


# ---------------- 数据集检查 ----------------
def _csv(tmp_path: Path, n: int = 3000) -> Path:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    df = pd.DataFrame({"id": range(n), "score": rng.normal(5, 1, n), "group": rng.choice(["a", "b", "c"], n),
                       "note": [None if i % 10 == 0 else f"n{i}" for i in range(n)]})
    p = tmp_path / "results.csv"
    df.to_csv(p, index=False)
    return p


def test_csv_dataset_profile_preview_and_paging(with_supp, tmp_path):
    api, pid, _ = with_supp
    p = _csv(tmp_path)
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(p)]})
    wait_ok(api, r.json()["job_id"])
    asset = next(a for a in api.get(f"/papers/{pid}/supplements").json()["items"] if a["filename"] == "results.csv")
    assert asset["kind"] == "table" and asset["category"] == "数据集" and asset["analysis"]["rows"] == 3000
    prof = api.get(f"/supplements/{asset['id']}/analysis").json()["analysis"]
    sh = prof["sheets"][0]
    cols = {c["name"]: c for c in sh["columns"]}
    assert prof["format"] == "csv" and sh["rows"] == 3000 and not sh["partial"] and len(sh["columns"]) == 4
    assert cols["id"]["dtype"] == "integer" and cols["score"]["dtype"] == "float" and cols["group"]["dtype"] == "string"
    assert abs(cols["score"]["stats"]["mean"] - 5) < 0.2 and len(cols["score"]["histogram"]["bins"]) == 12
    assert sum(b["count"] for b in cols["score"]["histogram"]["bins"]) == 3000
    assert cols["note"]["missing"] == 300 and abs(cols["note"]["missing_rate"] - 0.1) < 1e-9
    assert {t["value"] for t in cols["group"]["top_values"]} == {"a", "b", "c"}
    assert len(sh["preview"]["rows"]) == datasets.PREVIEW_ROWS == 50 and sh["preview"]["columns"] == ["id", "score", "group", "note"]
    page = api.get(f"/supplements/{asset['id']}/table", params={"offset": 100, "limit": 5}).json()
    assert page["columns"][0] == "id" and page["rows"][0][0] == "100" and len(page["rows"]) == 5 and page["total_rows"] == 3000
    assert api.get(f"/supplements/{asset['id']}/table", params={"offset": 600000}).status_code == 400
    # 列表接口只带轻量摘要，不带数据行
    assert "preview" not in json.dumps(api.get(f"/papers/{pid}/supplements").json()["items"][-1])


def test_quantiles_stay_unbiased_on_sorted_data_when_sampling(tmp_path, monkeypatch):
    """按行号递增的数据（常见）：蓄水池抽样必须对各个分块一视同仁，中位数不能偏向后面的块。"""
    monkeypatch.setattr(datasets, "CHUNK_ROWS", 2000)
    monkeypatch.setattr(datasets, "SAMPLE_CAP", 1500)
    p = tmp_path / "sorted.csv"
    p.write_text("id,v\n" + "\n".join(f"{i},{i % 7}" for i in range(30000)), encoding="utf-8")
    col = {c["name"]: c for c in datasets.profile_csv(p, ".csv")["sheets"][0]["columns"]}["id"]
    assert col["stats"]["min"] == 0 and col["stats"]["max"] == 29999 and abs(col["stats"]["mean"] - 14999.5) < 1e-6
    assert abs(col["stats"]["median"] - 15000) < 1800, col["stats"]
    assert abs(col["stats"]["q25"] - 7500) < 1800 and abs(col["stats"]["q75"] - 22500) < 1800
    assert col["histogram"]["sampled"] is True
    counts = [b["count"] for b in col["histogram"]["bins"]]
    assert max(counts) < 2.2 * (sum(counts) / len(counts))  # 近似均匀，而不是挤在某一端


def test_iso_date_strings_are_typed_as_datetime(tmp_path):
    p = tmp_path / "dates.csv"
    p.write_text(
        "day,when,label\n2021-03-29,2021-03-29 10:00:00,a\n2020-01-01,2020-01-01T00:00:00,b\n2022-06-18,2022-06-18 23:59,c\n",
        encoding="utf-8",
    )
    cols = {c["name"]: c for c in datasets.profile_csv(p, ".csv")["sheets"][0]["columns"]}
    assert cols["day"]["dtype"] == "datetime" and cols["day"]["stats"] == {"min": "2020-01-01", "max": "2022-06-18"}
    assert cols["when"]["dtype"] == "datetime" and cols["label"]["dtype"] == "string"


def test_zip_with_one_bad_entry_is_rejected_whole_and_leaves_nothing_behind(tmp_path):
    z = tmp_path / "mixed.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("fine/readme.txt", "hello")
        zf.writestr("../escape.txt", "x")
    dest = tmp_path / "out"
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(z, dest)
    assert not dest.exists() and not (tmp_path / "escape.txt").exists()
    # 目标目录事先存在时，失败也不留下已解压的文件
    dest.mkdir()
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(z, dest)
    assert list(dest.rglob("*")) == []


def test_large_csv_is_scanned_partially_with_bounded_work(with_supp, tmp_path, monkeypatch):
    api, pid, _ = with_supp
    monkeypatch.setattr(datasets, "SCAN_ROW_LIMIT", 1000)
    monkeypatch.setattr(datasets, "CHUNK_ROWS", 400)
    p = _csv(tmp_path, 5000)
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(p)]}).json()["job_id"])
    asset = next(a for a in api.get(f"/papers/{pid}/supplements").json()["items"] if a["filename"] == "results.csv")
    sh = api.get(f"/supplements/{asset['id']}/analysis").json()["analysis"]["sheets"][0]
    assert sh["partial"] is True and sh["rows_scanned"] < 5000 and sh["rows"] == 5000 and sh["rows_approx"] is True
    assert asset["analysis"]["partial"] is True


def test_excel_with_multiple_sheets_and_odd_values(with_supp, tmp_path):
    import pandas as pd

    api, pid, _ = with_supp
    p = tmp_path / "book.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame({"x": [1, 2, 3, None], "flag": [True, False, True, True]}).to_excel(w, sheet_name="first", index=False)
        pd.DataFrame({"when": pd.to_datetime(["2020-01-01", "2020-06-01", "2021-01-01"]), "label": ["u", "v", ""]}).to_excel(
            w, sheet_name="second", index=False)
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(p)]}).json()["job_id"])
    asset = next(a for a in api.get(f"/papers/{pid}/supplements").json()["items"] if a["filename"] == "book.xlsx")
    prof = api.get(f"/supplements/{asset['id']}/analysis").json()["analysis"]
    assert prof["sheet_names"] == ["first", "second"]
    s1, s2 = prof["sheets"]
    c1 = {c["name"]: c for c in s1["columns"]}
    assert c1["x"]["missing"] == 1 and c1["x"]["dtype"] == "integer" and c1["flag"]["dtype"] == "boolean"  # 1,2,3 + 空 → 整数列（Excel 不区分）
    c2 = {c["name"]: c for c in s2["columns"]}
    assert c2["when"]["dtype"] == "datetime" and c2["when"]["stats"]["min"].startswith("2020-01-01")
    assert c2["label"]["missing"] == 1  # 空字符串算缺失
    pg = api.get(f"/supplements/{asset['id']}/table", params={"sheet": "second", "limit": 2}).json()
    assert pg["sheet"] == "second" and len(pg["rows"]) == 2


def test_unreadable_or_unsupported_tables_fail_gracefully(with_supp, tmp_path):
    api, pid, _ = with_supp
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"PK\x03\x04 not a real workbook")
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(bad)]}).json()["job_id"])
    a = next(x for x in api.get(f"/papers/{pid}/supplements").json()["items"] if x["filename"] == "broken.xlsx")
    assert a["analysis_status"] == "failed" and a["download_status"] == "done" and a["analysis"]["kind"] == "error"
    assert a["analysis"]["error"] and a["child_paper_id"] is None
    with pytest.raises(Exception):
        datasets.profile_file(tmp_path / "old.xls")


def test_ai_explains_dataset_with_evidence_and_privacy_note(with_supp, tmp_path):
    api, pid, _ = with_supp
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(_csv(tmp_path, 400))]}).json()["job_id"])
    asset = next(a for a in api.get(f"/papers/{pid}/supplements").json()["items"] if a["filename"] == "results.csv")
    r = api.post(f"/supplements/{asset['id']}/explain")
    assert r.status_code == 403
    api.post(f"/papers/{pid}/supplements/authorization", {"allow": True})
    wait_ok(api, api.post(f"/supplements/{asset['id']}/explain").json()["job_id"])
    exp = api.get(f"/supplements/{asset['id']}/analysis").json()["analysis"]["explanation"]
    assert exp["answer"]["direct"] and exp["evidence"] and exp["verification"]["status"] in ("verified", "partial", "unverified")
    assert all(e["kind"] == "paper_text" for e in exp["evidence"])
    sent = api.fake.requests[-1]["body"]["messages"]
    assert "【数据集统计】" in sent[1]["content"] and "results.csv" in sent[1]["content"] and "id：" in sent[1]["content"]
    priv = api.get("/privacy/requests").json()["items"][0]
    assert priv["task_type"] == "supplement_dataset_explain" and priv["includes"]["supplements"] is True
    assert api.get(f"/papers/{pid}/supplements").json()["items"][-1]["analysis"]["has_explanation"] is True
    # 不是数据集的文件不能让 AI 解释
    pdf_asset = api.get(f"/papers/{pid}/supplements").json()["items"][0]
    assert api.post(f"/supplements/{pdf_asset['id']}/explain").status_code == 400


# ---------------- docx / zip / 安全 ----------------
def test_docx_text_extraction(with_supp, tmp_path):
    import docx

    api, pid, _ = with_supp
    d = docx.Document()
    d.add_heading("Implementation details", level=1)
    d.add_paragraph("We trained the model with Adam for 200 epochs.")
    p = tmp_path / "details.docx"
    d.save(p)
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(p)]}).json()["job_id"])
    a = next(x for x in api.get(f"/papers/{pid}/supplements").json()["items"] if x["filename"] == "details.docx")
    assert a["kind"] == "document" and a["category"] == "实验配置" and a["analysis"]["headings"] == 1
    info = api.get(f"/supplements/{a['id']}/analysis").json()["analysis"]
    assert "Adam" in info["text_sample"]
    assert api.get(f"/supplements/{a['id']}/file").status_code == 200


def _zip(path: Path, members: dict[str, bytes], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (0o120777 << 16)
            zf.writestr(zi, "/etc/passwd")
    return path


def test_zip_listing_never_extracts_and_extraction_is_explicit_and_safe(with_supp, tmp_path):
    api, pid, _ = with_supp
    z = _zip(tmp_path / "code.zip", {"src/main.py": b"print(1)", "README.md": b"hi", "data/x.csv": b"a\n1"}, symlink="link")
    wait_ok(api, api.post(f"/papers/{pid}/supplements/files", {"paths": [str(z)]}).json()["job_id"])
    a = next(x for x in api.get(f"/papers/{pid}/supplements").json()["items"] if x["filename"] == "code.zip")
    assert a["category"] == "代码" and a["analysis"]["total_files"] == 4
    info = api.get(f"/supplements/{a['id']}/analysis").json()["analysis"]
    assert {e["name"] for e in info["entries"]} >= {"src/main.py", "README.md"} and any("符号链接" in s for s in info["suspicious"])
    asset_path = api.ctx.paths.data_dir / "papers" / pid / "supplements"
    assert not list(asset_path.glob("*_extracted"))  # 添加时只列目录，没有解压
    j = wait_ok(api, api.post(f"/supplements/{a['id']}/extract").json()["job_id"])
    out = api.ctx.paths.data_dir / j["result"]["dir"]
    assert (out / "src" / "main.py").read_text() == "print(1)" and not (out / "link").exists()  # 符号链接被跳过


def test_zip_slip_and_bombs_are_rejected(tmp_path):
    evil = _zip(tmp_path / "evil.zip", {"../../outside.txt": b"x", "ok.txt": b"y"})
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(evil, tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists() and not (tmp_path.parent / "outside.txt").exists()
    assert any("路径穿越" in s for s in F.list_zip(evil)["suspicious"])
    abs_zip = _zip(tmp_path / "abs.zip", {"/etc/cron.d/x": b"x"})
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(abs_zip, tmp_path / "dest2")
    bomb = _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (80 * 1024 * 1024)})
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(bomb, tmp_path / "dest3")
    assert any("体积异常" in s for s in F.list_zip(bomb)["suspicious"])
    many = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(30)})
    with pytest.raises(F.UnsafeArchive):
        F.safe_extract_zip(many, tmp_path / "dest4", max_files=10)


def test_filename_sanitization_and_blocked_files(with_supp, tmp_path):
    s = F.sanitize_filename
    assert s("..\\..\\evil.pdf") == "evil.pdf" and s("a/b/c.csv") == "c.csv" and s("") == "file"
    assert s("CON.pdf") == "_CON.pdf" and s("x" * 300 + ".pdf").endswith(".pdf") and len(s("x" * 300 + ".pdf")) <= 120
    assert "<" not in s("a<b>:c?.txt") and s("补充材料 表1.xlsx") == "补充材料 表1.xlsx"
    api, pid, _ = with_supp
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ")
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(exe)]})
    assert r.status_code == 400 and "不会运行" in r.json()["error"]["message"]
    assert api.post(f"/papers/{pid}/supplements/files", {"paths": [str(tmp_path / "missing.pdf")]}).status_code == 400
    assert api.post(f"/papers/{pid}/supplements/files", {"paths": []}).status_code == 400
    assert api.post(f"/papers/{pid}/supplements/files", {"paths": [str(exe)], "category": "乱写"}).status_code == 400


def test_magic_check_rejects_fake_files(tmp_path):
    fake_pdf = tmp_path / "a.pdf"
    fake_pdf.write_bytes(b"<html>login</html>")
    assert "网页" in (F.check_magic(fake_pdf) or "") or "PDF" in (F.check_magic(fake_pdf) or "")
    fake_zip = tmp_path / "b.zip"
    fake_zip.write_bytes(b"plain text")
    assert F.check_magic(fake_zip)
    ok = tmp_path / "c.csv"
    ok.write_text("a,b\n1,2")
    assert F.check_magic(ok) is None


def test_stored_path_cannot_escape_data_dir(with_supp):
    from paperdesk.materials import store

    api, pid, asset = with_supp
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE supplementary_assets SET rel_path = '../../etc/passwd' WHERE id = ?", (asset["id"],))
        row = store.get_row(conn, asset["id"])
    assert store.file_path(api.ctx, row) is None
    assert api.get(f"/supplements/{asset['id']}/file").status_code == 404


# ---------------- 任务系统新增的批准能力 ----------------
def test_generic_job_approval_flow(api):
    ran = []
    api.ctx.jobs.register("needs_ok", lambda ctx: ran.append(ctx.payload.get("approved")) or {"ok": True})
    jid = api.ctx.jobs.submit_waiting("needs_ok", "需要批准", {"x": 1})
    j = api.get(f"/jobs/{jid}").json()
    assert j["status"] == "waiting_approval" and j["can_approve"] and j["can_cancel"] and not j["can_retry"]
    api.ctx.jobs.recover()  # 重启不会把等待批准的任务变成失败或自动执行
    assert api.get(f"/jobs/{jid}").json()["status"] == "waiting_approval" and ran == []
    assert api.post(f"/jobs/{jid}/approve").json()["status"] in ("pending", "running", "completed")
    assert api.wait_job(jid)["status"] == "completed" and ran == [True]
    assert api.post(f"/jobs/{jid}/approve").status_code == 409
    j2 = api.ctx.jobs.submit_waiting("needs_ok", "要被拒绝", {})
    assert api.post(f"/jobs/{j2}/reject").json()["status"] == "cancelled"
    j3 = api.ctx.jobs.submit_waiting("needs_ok", "要被取消", {})
    assert api.post(f"/jobs/{j3}/cancel").json()["status"] == "cancelled"
    assert api.post("/jobs/nope/approve").status_code == 404


def test_authorization_accepts_both_spellings_ttl_and_rejects_unknown_fields(with_supp):
    api, pid = with_supp[0], with_supp[1]
    url = f"/papers/{pid}/supplements/authorization"
    # 文档写法 granted + ttl_seconds
    d = api.post(url, {"granted": True, "ttl_seconds": 600}).json()
    assert d["granted"] is True and d["decision"] == "allow" and d["ttl_seconds"] == 600
    assert api.post(url, {"granted": False}).json()["decision"] == "deny"
    # 早期写法 allow 仍然可用
    assert api.post(url, {"allow": True}).json()["granted"] is True
    # 两个字段同时给且冲突：报错，不猜
    assert api.post(url, {"allow": True, "granted": False}).status_code == 400
    # 未知参数不再被静默忽略；TTL 范围受限
    assert api.post(url, {"grant": True}).status_code == 400
    assert api.post(url, {"granted": True, "ttl_seconds": 5}).status_code == 400
    assert api.post(url, {"granted": True, "ttl_seconds": 10**9}).status_code == 400
    # 不给参数 / null = 重置
    assert api.post(url, {"granted": None}).json()["decision"] is None
    assert api.post(url, {}).json()["decision"] is None
