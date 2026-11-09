from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from coderepo import FILES, write_repo
from conftest import TOKEN, Api
from fake_code_llm import FakeCodeLLM

from paperdesk.app import create_app

EXTRA = {
    "model/attention.py": '''import numpy as np


class TopologyAttention:
    """Attention weights over neighbouring nodes (topology module)."""

    def forward(self, x, adj):
        return np.dot(adj, x)


def temporal_attention(history):
    """Attend to the recent load history of each node."""
    return history[-1]
''',
}


@pytest.fixture
def cenv(tmp_path, sample_pdf):
    fake = FakeCodeLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        api.fake = fake
        api.pid = api.import_pdf(sample_pdf)["paper_id"]
        write_repo(tmp_path / "src_repo", {**FILES, **EXTRA})
        r = api.post(f"/papers/{api.pid}/code/import", {"path": str(tmp_path / "src_repo")}).json()
        assert api.wait_job(r["job_id"])["status"] == "completed"
        api.rid = r["repo_id"]
        yield api


def sym(api, qualname, path):
    items = api.get(f"/code/repos/{api.rid}/symbols", params={"limit": 300}).json()["items"]
    return next(s for s in items if s["qualname"] == qualname and s["path"] == path)


def explain(api, **body):
    return api.sse("/code/explain", {"repo_id": api.rid, **body})


# ---------------- 解释 ----------------
def test_explain_function_sends_only_necessary_code_and_verifies_evidence(cenv):
    api = cenv
    api.configure_ai()
    s = sym(api, "run_training", "train.py")
    ev = explain(api, kind="function", symbol_id=s["id"], question="这个函数做什么？")
    names = [n for n, _ in ev]
    assert names[0] == "meta" and names[-1] == "done" and names.count("delta") > 3
    meta, card = ev[0][1], ev[-1][1]["card"]
    assert meta["model"] == "fast-m" and meta["context"]["includes"]["code"] is True
    snippets = meta["context"]["snippets"]
    assert snippets[0]["path"] == "train.py" and snippets[0]["role"] == "target" and snippets[0]["label"] == "C1"
    assert {sn["path"] for sn in snippets} >= {"train.py", "data/loader.py", "model/agent.py", "utils.py"}  # 目标 + 被调用者
    assert card["target"]["symbol"] == "run_training"  # 卡片记下符号名，界面才能写出“解释了哪个函数”

    # 实际发给模型的内容：目标函数与被调用者，不含无关文件，也不是整个仓库
    sent = api.fake.requests[-1]["body"]["messages"][1]["content"]
    assert "def run_training(args)" in sent and "def load_graphs(n_min, n_max)" in sent
    assert "draw(x)" not in sent and "int helper(int v)" not in sent and "weights/model.pt" not in sent
    assert len(sent) < 16000

    # 证据：[C1:a-b] 与 [path:a-b] 统一成 [L#]，并逐条核实
    assert card["verification"]["status"] == "verified" and card["verification"]["invalid_citations"] == []
    assert card["evidence"] and all(e["verified"] for e in card["evidence"])
    e1 = card["evidence"][0]
    assert e1["path"] == "train.py" and e1["symbol"] == "run_training" and e1["kind"] == "code"
    real = FILES["train.py"].split("\n")
    assert e1["snippet"] == "\n".join(real[e1["line_start"] - 1:e1["line_end"]][:12])  # 摘录取自真实文件
    assert "[L1]" in card["answer"]["direct"] and "[C1" not in card["raw_markdown"]
    assert card["answer"]["inferences"][0]["basis_refs"] and "依据" not in card["answer"]["inferences"][0]["text"]

    # 隐私与用量
    priv = api.get("/privacy/requests").json()["items"][0]
    assert priv["includes"]["code"] is True and "代码片段" in priv["summary"] and "不含整个仓库" in priv["summary"] and priv["passage_count"] == 0
    assert any(t["task_type"] == "code_explain" for t in api.get("/usage").json()["by_task_type"])
    # 持久化
    cards = api.get(f"/code/repos/{api.rid}/cards", params={"symbol_id": s["id"]}).json()["items"]
    assert [c["id"] for c in cards] == [card["id"]]
    assert api.delete(f"/code/cards/{card['id']}").json()["ok"]


def test_fabricated_or_out_of_range_citations_are_flagged(cenv):
    api = cenv
    api.configure_ai()
    s = sym(api, "run_training", "train.py")
    card = explain(api, kind="function", symbol_id=s["id"], question="BADCITE 这个函数做什么？")[-1][1]["card"]
    ver = card["verification"]
    assert ver["status"] == "partial" and len(ver["invalid_citations"]) == 3
    bad = [e for e in card["evidence"] if not e["verified"]]
    reasons = " ".join(e["reason"] for e in bad)
    assert "不存在的代码片段" in reasons and "没有这个文件" in reasons and "不在所给片段" in reasons
    assert all(e["snippet"] == "" for e in bad)  # 无法核实的不会展示任何“代码摘录”
    assert any(e["verified"] for e in card["evidence"])  # 同一回答里真实的引用仍然保留


def test_answer_without_code_evidence_escalates_to_strong_model(cenv):
    api = cenv
    api.configure_ai(fast="weak-fast")
    s = sym(api, "run_training", "train.py")
    ev = explain(api, kind="function", symbol_id=s["id"], question="NOCITE 这个函数做什么？")
    names = [n for n, _ in ev]
    assert "escalate" in names and names[-1] == "done"
    card = ev[-1][1]["card"]
    assert card["model"] == "strong-m" and card["verification"]["status"] == "verified"


def test_honest_refusal_is_neutral_not_a_warning(cenv):
    api = cenv
    api.configure_ai()
    s = sym(api, "run_training", "train.py")
    card = explain(api, kind="function", symbol_id=s["id"], question="REFUSE 它在 GPU 上跑多快？")[-1][1]["card"]
    assert card["verification"]["status"] == "unverified" and card["evidence"] == []


def test_explain_by_file_and_by_name_and_errors(cenv):
    api = cenv
    ev = explain(api, kind="file", path="train.py")
    assert ev[0][0] == "error" and ev[0][1]["code"] == "no_api_key"
    api.configure_ai()
    card = explain(api, kind="file", path="train.py")[-1][1]["card"]
    assert card["target"]["kind"] == "file" and card["evidence"][0]["path"] == "train.py"
    card = explain(api, kind="function", path="model/agent.py", symbol="Agent.learn")[-1][1]["card"]
    assert card["evidence"][0]["symbol"] in ("Agent.learn", "Agent")
    assert explain(api, kind="function", path="weights/model.pt")[0][1]["code"] == "not_found"  # 二进制文件无法解释
    assert explain(api, kind="function", path="nope.py")[0][1]["code"] == "not_found"
    assert api.post("/code/explain", {"repo_id": "x", "kind": "bogus"}).status_code == 400


def test_algorithm_kind_routes_to_strong_model(cenv):
    api = cenv
    api.configure_ai()
    s = sym(api, "Agent.learn", "model/agent.py")
    ev = explain(api, kind="algorithm", symbol_id=s["id"], question="这里实现了什么算法？")
    assert ev[0][1]["model"] == "strong-m"


# ---------------- 流程节点命名 ----------------
def test_flow_annotation_is_ai_generated_validated_and_labelled(cenv):
    api = cenv
    api.configure_ai()
    roots = api.get(f"/code/repos/{api.rid}/flow").json()["roots"]
    train = next(r for r in roots if r["path"] == "train.py")
    flow = api.post(f"/code/repos/{api.rid}/flow/annotate", {"symbol_id": train["symbol_id"]}).json()
    ann = flow["annotations"]
    assert ann and all(a["ai"] is True and a["model"] == "fast-m" and len(a["title"]) <= 16 for a in ann.values())
    assert "99999" not in ann  # 模型编造的节点 id 被丢弃
    assert set(ann) <= {str(n["id"]) for n in flow["nodes"]}
    again = api.get(f"/code/repos/{api.rid}/flow", params={"symbol_id": train["symbol_id"]}).json()
    assert again["annotations"] == ann  # 已持久化，结构仍是静态分析的结果
    assert [n["id"] for n in again["nodes"]] == [n["id"] for n in flow["nodes"]]


# ---------------- 论文 ↔ 代码 ----------------
def test_paper_modules_are_deterministic(cenv):
    mods = cenv.get(f"/papers/{cenv.pid}/code/modules").json()["items"]
    assert [m["name"] for m in mods] == ["Method"] and mods[0]["passage_ords"] and mods[0]["pages"] and "attention" in mods[0]["keywords"]


def test_lexical_association_without_ai_is_possible_only_and_reversible(cenv):
    api = cenv  # 未配置 AI
    job = api.wait_job(api.post(f"/code/repos/{api.rid}/associate").json()["job_id"])
    assert job["status"] == "completed" and job["result"]["ai_used"] is False and job["result"]["links"] >= 1
    links = api.get(f"/code/repos/{api.rid}/links").json()["items"]
    assert links and all(l["confidence"] == "possible" and l["method"] == "lexical" and l["status"] == "suggested" for l in links)
    assert any("attention" in l["symbol"]["qualname"].lower() for l in links)
    assert all(l["basis"]["matched_terms"] and "仅词法匹配" in l["basis"]["text"] for l in links)
    top = links[0]
    # 确认 / 拒绝；重新关联时保留用户确认的，替换待确认的
    assert api.patch(f"/code/links/{top['id']}", {"status": "confirmed"}).json()["status"] == "confirmed"
    api.wait_job(api.post(f"/code/repos/{api.rid}/associate").json()["job_id"])
    after = api.get(f"/code/repos/{api.rid}/links").json()["items"]
    assert [l["id"] for l in after if l["status"] == "confirmed"] == [top["id"]]
    assert sum(1 for l in after if l["symbol_id"] == top["symbol_id"]) == 1
    assert api.patch("/code/links/nope", {"status": "rejected"}).status_code == 404
    # 手动关联
    s = sym(api, "Agent.learn", "model/agent.py")
    m = api.post(f"/code/repos/{api.rid}/links", {"module_name": "Training loop", "symbol_id": s["id"], "passage_ords": []}).json()
    assert m["status"] == "confirmed" and m["method"] == "manual"
    # 反向：函数 → 论文
    back = api.get(f"/code/symbols/{top['symbol_id']}/paper").json()
    assert back["links"] and back["links"][0]["id"] == top["id"] and "可能相关" in back["note"]
    assert back["related_passages"] and all(p["confidence"] == "possible" and p["page"] >= 1 for p in back["related_passages"])
    assert any("attention" in " ".join(p["matched_terms"]) for p in back["related_passages"])


def test_ai_association_never_asserts_without_concrete_lines(cenv):
    api = cenv
    api.configure_ai()
    job = api.wait_job(api.post(f"/code/repos/{api.rid}/associate").json()["job_id"])
    assert job["status"] == "completed" and job["result"]["ai_used"] is True
    links = api.get(f"/code/repos/{api.rid}/links").json()["items"]
    assert links and all(l["method"] == "llm" for l in links)
    assert all(l["symbol_id"] is not None and l["symbol_id"] != 99999 for l in links)  # 编造的符号被丢弃
    likely = [l for l in links if l["confidence"] == "likely"]
    possible = [l for l in links if l["confidence"] == "possible"]
    assert likely and possible, "第一个候选有范围内的具体行 → likely；第二个给的行号在范围外 → 必须降级为 possible"
    for l in likely:
        sy = next(s for s in api.get(f"/code/repos/{api.rid}/symbols", params={"limit": 300}).json()["items"] if s["id"] == l["symbol_id"])
        assert sy["line_start"] <= l["line_start"] <= l["line_end"] <= sy["line_end"]
    for l in possible:
        sy = next(s for s in api.get(f"/code/repos/{api.rid}/symbols", params={"limit": 300}).json()["items"] if s["id"] == l["symbol_id"])
        assert (l["line_start"], l["line_end"]) == (sy["line_start"], sy["line_end"])
    names = {l["module"]["name"] for l in links}
    assert "Dual attention" in names
    mod = next(l["module"] for l in links if l["module"]["name"] == "Dual attention")
    assert 99999 not in mod["passage_ords"]  # 论文侧引用的段落编号也要核实


# ---------------- 一致性 ----------------
def _add_paper_params(api):
    texts = [
        (9101, 3, "We trained FINDER on 200,000 randomly generated small synthetic graphs of 30–50 nodes using a learning rate of 0.0001 and a batch size of 64."),
        (9102, 3, "A 3-step Q-learning loss was used. The embedding dimension of 128 was chosen by grid search."),
    ]
    with api.ctx.db.session() as conn:
        for ord_, page, text in texts:
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)", (api.pid, ord_, page, "paragraph", text))


def test_flow_reports_why_it_was_truncated(cenv):
    api = cenv
    flow = api.get(f"/code/repos/{api.rid}/flow", params={"max_depth": 1}).json()
    assert flow["truncated"] is True and flow["truncated_by"] == ["depth"] and flow["max_depth"] == 1
    full = api.get(f"/code/repos/{api.rid}/flow", params={"max_depth": 8}).json()
    assert full["truncated"] is False and full["truncated_by"] == []


def test_code_ai_endpoints_require_ready_repo(cenv):
    api = cenv
    from paperdesk.code import service

    rid2 = service.create_repo(api.ctx, api.pid, origin="folder")
    assert api.post(f"/code/repos/{rid2}/associate").status_code == 409
    assert api.get(f"/code/repos/{rid2}/flow").status_code == 404


def test_association_with_ai_false_never_calls_the_model_even_when_configured(cenv):
    """首次打开映射页自动生成关联时用 ai=false：已经配置了模型也只做本地词法匹配，不发送论文或代码内容。"""
    api = cenv
    api.configure_ai()
    before = api.get("/usage").json()
    job = api.wait_job(api.post(f"/code/repos/{api.rid}/associate", {"ai": False}).json()["job_id"])
    assert job["status"] == "completed" and job["result"]["ai_used"] is False and job["result"]["links"] >= 1
    links = api.get(f"/code/repos/{api.rid}/links").json()["items"]
    assert links and all(l["method"] == "lexical" and l["confidence"] == "possible" for l in links)
    assert api.get("/usage").json()["by_model"] == before["by_model"]  # 没有产生任何模型用量
