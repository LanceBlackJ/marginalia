"""按目录（模块）解释：片段来自该目录里真实的文件，证据同样要回查真实文件与行号。"""
from __future__ import annotations

from test_code_ai import cenv, explain  # noqa: F401


def test_explain_module_uses_real_files_of_that_directory(cenv):
    api = cenv
    api.configure_ai()
    ev = explain(api, kind="module", path="model")
    assert [n for n, _ in ev][0] == "meta" and ev[-1][0] == "done"
    meta, card = ev[0][1], ev[-1][1]["card"]
    snippets = meta["context"]["snippets"]
    assert snippets and all(s["path"].startswith("model/") for s in snippets)  # 只发这个目录里的文件
    sent = api.fake.requests[-1]["body"]["messages"][1]["content"]
    assert "目录 model" in sent and "不可作为依据引用" in sent
    assert "def run_training" not in sent  # 目录外的代码没有发出去
    assert card["target"]["kind"] == "module" and card["target"]["path"] == "model"
    assert all(e["verified"] for e in card["evidence"]) and card["verification"]["invalid_citations"] == []
    for e in card["evidence"]:
        f = api.get(f"/code/repos/{api.rid}/file", params={"path": e["path"]}).json()
        assert e["path"].startswith("model/") and f["lines"] >= e["line_end"] >= e["line_start"] >= 1


def test_explain_module_of_empty_directory_is_404(cenv):
    api = cenv
    api.configure_ai()
    r = api.c.post("/api/code/explain", headers={"Authorization": "Bearer test-token"}, json={"repo_id": api.rid, "kind": "module", "path": "nope"})
    assert r.status_code == 404 or "没有可解释" in r.text


def test_explain_whole_repo_covers_each_top_level_group_with_real_snippets(cenv):
    api = cenv
    api.configure_ai()
    ev = explain(api, kind="repo")
    assert ev[-1][0] == "done"
    meta, card = ev[0][1], ev[-1][1]["card"]
    paths = {s["path"] for s in meta["context"]["snippets"]}
    assert "train.py" in paths and any(p.startswith("model/") for p in paths) and any(p.startswith("data/") for p in paths)
    sent = api.fake.requests[-1]["body"]["messages"][1]["content"]
    assert "整个仓库" in sent and "不可作为依据引用" in sent and len(sent) < 20000
    assert card["target"]["kind"] == "repo" and card["verification"]["invalid_citations"] == []
    assert all(e["verified"] for e in card["evidence"])
