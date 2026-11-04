"""第 4 阶段“复现”：静态评估、批准闸门、结果导入与对比。

硬性约束的测试：评估/计划/批准/对比的全过程不得启动任何子进程（本模块不执行用户代码）。
"""
from __future__ import annotations

import subprocess

import pytest

from test_code_ai import cenv  # noqa: F401  复用“已导入并分析的合成仓库”夹具

from paperdesk.repro import results, sandbox


@pytest.fixture
def no_exec(monkeypatch):
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("复现模块不得启动子进程")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)


def _assess(api):
    job = api.post(f"/papers/{api.pid}/repro/assessment", {}).json()["job_id"]
    done = api.wait_job(job)
    assert done["status"] == "completed", done
    return api.get(f"/papers/{api.pid}/repro/assessment").json()


def _items(report):
    return {it["key"]: it for sec in report["sections"] for it in sec["items"]}


# ---------------- 隔离环境检测 ----------------
def test_sandbox_is_unavailable_without_docker_or_wsl(cenv, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _n: None)
    monkeypatch.setattr(sandbox.os.environ, "get", lambda _k, _d=None: None)  # 也不要去固定的安装位置找
    d = cenv.get("/system/sandbox").json()
    assert d["available"] is False and d["can_run_here"] is False
    assert "不会在本机直接运行" in d["reason"] and d["docker"]["found"] is False


def test_sandbox_docker_running_means_can_run_here(cenv, monkeypatch):
    monkeypatch.setattr(sandbox, "find_docker", lambda: "C:/fake/docker.exe")
    monkeypatch.setattr(sandbox, "_run", lambda _a: (0, "26.1.0"))
    d = cenv.get("/system/sandbox").json()
    assert d["docker"]["running"] is True and d["available"] is True
    assert d["can_run_here"] is True and "每次运行都要你确认" in d["reason"]


def test_sandbox_docker_installed_but_stopped_cannot_run(cenv, monkeypatch):
    monkeypatch.setattr(sandbox, "find_docker", lambda: "C:/fake/docker.exe")
    monkeypatch.setattr(sandbox, "_run", lambda _a: (1, ""))
    d = cenv.get("/system/sandbox").json()
    assert d["can_run_here"] is False and "没有运行" in d["reason"]


# ---------------- A：评估 ----------------
def test_assessment_is_static_and_all_code_evidence_is_real(cenv, no_exec):
    api = cenv
    rep = _assess(api)["report"]
    assert [s["key"] for s in rep["sections"]] == ["environment", "entry", "data", "resources", "params", "risks"]
    assert rep["effort"]["level"] in ("低", "中", "高") and rep["effort"]["basis"] and "不是时间估计" in rep["effort"]["note"]
    it = _items(rep)
    assert it["entry:train"]["status"] == "found" and it["entry:train"]["evidence"][0]["path"].endswith("train.py")
    assert it["commands"]["status"] == "found"
    assert it["compile"]["status"] == "risk"  # 仓库带 C++ 源码
    assert it["gpu"]["status"] == "uncertain" and "不确定" in it["gpu"]["text"]  # 没有 GPU 线索 ≠ 可以只用 CPU
    assert it["data_missing"]["status"] == "missing"  # 没找到的明确写“没找到”，不乐观
    assert it["risk:unpinned"]["status"] == "risk" and "framework_versions" in it
    # 每条代码证据都指向真实存在的文件与行号范围
    checked = 0
    for item in it.values():
        for e in item["evidence"]:
            if e["type"] != "code":
                continue
            f = api.get(f"/code/repos/{api.rid}/file", params={"path": e["path"]}).json()
            if e["line_start"] == 0:  # 文件级证据（二进制文件没有行）：文件必须存在且确实没有可显示的行
                assert f["skipped"] and f["lines"] == 0, (item["key"], e)
            else:
                assert f["lines"] >= e["line_end"] >= e["line_start"] >= 1, (item["key"], e)
            checked += 1
    assert checked >= 5


def test_ai_dataset_names_must_quote_the_paper(cenv, no_exec):
    api = cenv
    api.configure_ai()
    rep = _assess(api)["report"]
    titles = [i["title"] for sec in rep["sections"] for i in sec["items"]]
    assert any("power grid" in t for t in titles)
    assert not any("ImageNet" in t for t in titles)  # 引文不在原文里的抽取被丢弃
    ds = next(i for sec in rep["sections"] for i in sec["items"] if i["key"].startswith("dataset:"))
    assert ds["evidence"][0]["type"] == "paper" and ds["evidence"][0]["page"] and "power grid" in ds["evidence"][0]["quote"]


def test_assessment_reports_missing_repo(cenv):
    r = cenv.get(f"/papers/{cenv.pid}/repro/assessment", params={"repo_id": "nope"})
    assert r.status_code == 404


# ---------------- B：计划与批准闸门 ----------------
def _plan(api):
    return api.post(f"/papers/{api.pid}/repro/plan", {}).json()["plan"]


def test_plan_needs_assessment_and_command_steps_need_approval(cenv, no_exec):
    api = cenv
    r = api.post(f"/papers/{api.pid}/repro/plan", {})
    assert r.status_code == 409 and "可行性评估" in r.json()["error"]["message"]
    _assess(api)
    plan = _plan(api)
    assert len(plan["steps"]) >= 5 and "不会在本机运行" in plan["note"]
    for s in plan["steps"]:
        if s["command"].strip():
            assert s["needs_approval"] is True and s["approval"] == "pending", s["title"]
        else:
            assert s["approval"] == "none"
    train = next(s for s in plan["steps"] if s["title"] == "运行训练")
    assert "train.py" in train["command"] and "占用GPU" not in train["side_effects"] and train["evidence"]
    assert any(s["phase"] == "build" for s in plan["steps"])  # 有 C++ 源码 → 有编译步骤
    again = api.post(f"/papers/{api.pid}/repro/plan", {})
    assert again.status_code == 409  # 默认不覆盖已有计划
    assert api.post(f"/papers/{api.pid}/repro/plan", {"force": True}).status_code == 200


def test_approval_is_recorded_and_editing_the_command_voids_it(cenv, no_exec):
    api = cenv
    _assess(api)
    plan = _plan(api)
    step = next(s for s in plan["steps"] if s["command"])
    r = api.post(f"/repro/steps/{step['id']}/approve", {"decision": "approve", "note": "看过了"}).json()
    assert r["executed"] is False  # 批准只是记录
    s = next(x for x in r["plan"]["steps"] if x["id"] == step["id"])
    assert s["approval"] == "approved" and s["approval_log"][-1]["decision"] == "approved"
    assert s["approval_log"][-1]["command"] == step["command"] and s["approval_log"][-1]["actor"] == "user"
    # 批准后改命令：批准作废，记录里留下“撤销”
    r2 = api.patch(f"/repro/steps/{step['id']}", {"command": step["command"] + " --extra"}).json()
    s2 = next(x for x in r2["plan"]["steps"] if x["id"] == step["id"])
    assert s2["approval"] == "pending" and s2["approval_log"][-1]["decision"] == "reset"
    assert api.post(f"/repro/steps/{step['id']}/approve", {"decision": "reject"}).json()["plan"]["summary"]["rejected"] == 1
    # 没有命令的步骤不需要批准
    nocmd = next(s for s in plan["steps"] if not s["command"])
    assert api.post(f"/repro/steps/{nocmd['id']}/approve", {"decision": "approve"}).status_code == 409
    # 往没有命令的步骤里加命令：立刻变成待批准
    r3 = api.patch(f"/repro/steps/{nocmd['id']}", {"command": "echo hi"}).json()
    s3 = next(x for x in r3["plan"]["steps"] if x["id"] == nocmd["id"])
    assert s3["needs_approval"] is True and s3["approval"] == "pending"


def test_user_steps_and_exports_keep_unapproved_commands_disabled(cenv, no_exec):
    api = cenv
    _assess(api)
    plan = _plan(api)
    added = api.post(f"/repro/plans/{plan['id']}/steps", {"title": "我自己的步骤", "command": "echo mine", "phase": "run"}).json()["plan"]
    mine = added["steps"][-1]
    assert mine["origin"] == "user" and mine["approval"] == "pending" and mine["ord"] == len(added["steps"])
    ok = next(s for s in added["steps"] if s["id"] != mine["id"] and s["command"])
    api.post(f"/repro/steps/{ok['id']}/approve", {"decision": "approve"})
    md = api.get(f"/repro/plans/{plan['id']}/export", params={"format": "md"}).json()
    assert md["format"] == "md" and "不会在本机运行" in md["content"] and "待批准" in md["content"] and "已批准" in md["content"]
    sh = api.get(f"/repro/plans/{plan['id']}/export", params={"format": "sh"}).json()["content"]
    assert "echo mine" in sh and "# [未批准，已跳过]" in sh  # 未批准的命令只以注释保留
    assert any(ln.strip() == ok["command"].splitlines()[0].strip() or ok["command"].splitlines()[0] in ln for ln in sh.splitlines() if not ln.startswith("#"))
    gone = api.c.delete(f"/api/repro/steps/{mine['id']}", headers={"Authorization": "Bearer test-token"}).json()
    assert all(s["id"] != mine["id"] for s in gone["plan"]["steps"])


# ---------------- C：结果解析 ----------------
def test_parse_results_formats():
    fmt, m, _w = results.parse_results("model,ANC,F1\nFINDER,0.52,91.3%\nHDA,0.61,88.0%")
    by = {x["name"]: x["value"] for x in m}
    assert fmt == "csv" and by["FINDER / ANC"] == 0.52 and by["HDA / F1"] == 88.0
    fmt, m, _w = results.parse_results("metric,value,std\nacc,0.9,0.01\nloss,0.3,")
    assert {x["name"]: (x["value"], x["std"]) for x in m} == {"acc": (0.9, 0.01), "loss": (0.3, None)}
    fmt, m, w = results.parse_results("acc,loss\n0.9,0.3\n0.8,0.4\n0.7,0.5")  # 无标签列：按列取均值与标准差
    acc = next(x for x in m if x["name"] == "acc")
    assert fmt == "csv" and acc["n"] == 3 and abs(acc["value"] - 0.8) < 1e-9 and acc["std"] > 0 and w
    fmt, m, _w = results.parse_results('{"test": {"acc": 0.93, "f1": "88.1 ± 0.4"}, "runs": [1, 2, 3]}')
    by = {x["name"]: x for x in m}
    assert fmt == "json" and by["test.acc"]["value"] == 0.93 and by["test.f1"]["std"] == 0.4 and by["runs"]["n"] == 3
    fmt, m, _w = results.parse_results("| method | acc |\n|---|---|\n| A | 0.5 |\n| B | 0.6 |")  # 粘贴的 Markdown 表格
    assert {x["name"] for x in m} == {"A / acc", "B / acc"}
    for bad in ("", "hello world", '{"a": "x"}', "a,b\nx,y"):
        with pytest.raises(Exception):
            results.parse_results(bad)


def test_parse_number_variants():
    assert results.parse_number("12.5%")[0] == 12.5
    assert results.parse_number("1,234.5")[0] == 1234.5
    assert results.parse_number("3 ± 0.2")[:2] == (3.0, 0.2)
    assert results.parse_number("abc") is None and results.parse_number(True) is None and results.parse_number("nan") is None


# ---------------- C：目标、导入、对比 ----------------
def _insert_passage(api, text, ord_=9001):
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)", (api.pid, ord_, 3, "paragraph", text))
    return ord_


def test_target_candidates_and_quote_verification(cenv):
    api = cenv
    text = "Our model reaches accuracy 92.5% and the ANC is 0.88 ± 0.02 on the test networks."
    o = _insert_passage(api, text)
    cands = api.get(f"/papers/{api.pid}/repro/target-candidates").json()["items"]
    mine = [c for c in cands if c["ord"] == o]
    assert any(c["value"] == 92.5 and c["unit"] == "%" for c in mine) and any(c["value"] == 0.88 and c["std"] == 0.02 for c in mine)
    ok = api.post(f"/papers/{api.pid}/repro/targets", {"name": "ANC", "value": 0.88, "std": 0.02, "ord": o, "page": 3, "quote": "the ANC is 0.88 ± 0.02", "source": "text"}).json()
    bad = api.post(f"/papers/{api.pid}/repro/targets", {"name": "X", "value": 1, "ord": o, "quote": "这句话论文里没有"}).json()
    assert ok["quote_verified"] is True and bad["quote_verified"] is False
    assert api.post(f"/papers/{api.pid}/repro/targets", {"name": "", "value": 1}).status_code == 400
    assert api.post(f"/papers/{api.pid}/repro/targets", {"name": "n", "value": None}).status_code == 400


def test_compare_states_facts_and_never_a_verdict(cenv, tmp_path, no_exec):
    api = cenv
    mk = lambda **b: api.post(f"/papers/{api.pid}/repro/targets", b).json()  # noqa: E731
    anc = mk(name="ANC", value=0.50, tol_rel=0.05, page=4)
    f1 = mk(name="F1", value=90.0, std=1.0, unit="%", page=5)
    acc = mk(name="accuracy", value=0.9)
    gone = mk(name="BLEU", value=30.0)
    nc = mk(name="speed", value=5.0, comparable=False)
    csv_file = tmp_path / "result.csv"
    csv_file.write_text("metric,value\nANC,0.52\nF1,85.0\naccuracy,0.95\n", encoding="utf-8")
    run = api.post(f"/papers/{api.pid}/repro/runs", {"name": "我的第一次", "path": str(csv_file), "repo_id": api.rid, "env": {"python": "3.8"}}).json()
    assert run["source_format"] == "csv" and len(run["metrics"]) == 3
    cmp = api.get(f"/repro/runs/{run['id']}/compare").json()
    st = {i["target"]["name"]: i for i in cmp["items"]}
    assert st["ANC"]["status"] == "within_tolerance" and abs(st["ANC"]["rel_error"] - 0.04) < 1e-9 and st["ANC"]["mapping"] == "auto"
    assert st["F1"]["status"] == "outside_reported_range" and abs(st["F1"]["diff"] + 5.0) < 1e-9
    assert st["accuracy"]["status"] == "no_tolerance" and st["BLEU"]["status"] == "unmapped" and st["speed"]["status"] == "not_comparable"
    assert cmp["verdict"] is None and "不会判断" in cmp["note"]  # 不替用户断言“复现成功/失败”
    kinds = {(r["kind"], r["text"][:6]) for r in cmp["reasons"]}
    assert any(k == "conjecture" for k, _ in kinds)  # 超出范围时的原因线索标明“推测”
    # 手动对应：把 BLEU 指到 accuracy 指标
    api.c.put(f"/api/repro/runs/{run['id']}/mapping", headers={"Authorization": "Bearer test-token"}, json={"target_id": gone["id"], "metric": "accuracy"})
    assert {i["target"]["name"]: i for i in api.get(f"/repro/runs/{run['id']}/compare").json()["items"]}["BLEU"]["mapping"] == "user"
    bad = api.c.put(f"/api/repro/runs/{run['id']}/mapping", headers={"Authorization": "Bearer test-token"}, json={"target_id": gone["id"], "metric": "不存在"})
    assert bad.status_code == 400
    # 用户写结论、导出报告
    api.patch(f"/repro/runs/{run['id']}", {"conclusion": "F1 差了 5 个点，我怀疑数据划分不同", "env": {"python": "3.8", "seed": "1"}})
    rep = api.get(f"/repro/runs/{run['id']}/report").json()["content"]
    assert "不判断复现成功与否" in rep and "F1 差了 5 个点" in rep and "| F1 |" in rep and "超出论文报告的波动范围" in rep
    assert "待你填写" not in rep
    assert "待你填写" in api.get(f"/repro/runs/{api.post(f'/papers/{api.pid}/repro/runs', {'content': 'a,b\n1,2'}).json()['id']}/report").json()["content"]


def test_run_import_validation(cenv, tmp_path):
    api = cenv
    exe = tmp_path / "x.exe"
    exe.write_text("nope")
    assert api.post(f"/papers/{api.pid}/repro/runs", {"path": str(exe)}).status_code == 400
    assert api.post(f"/papers/{api.pid}/repro/runs", {"path": str(tmp_path / "missing.csv")}).status_code == 404
    assert api.post(f"/papers/{api.pid}/repro/runs", {"content": "无法解析的内容"}).status_code == 400
    assert api.post(f"/papers/{api.pid}/repro/runs", {"content": "a,b\n1,2", "repo_id": "不是这篇的仓库"}).status_code == 400
    r = api.post(f"/papers/{api.pid}/repro/runs", {"content": "a,b\n1,2"}).json()
    assert api.get(f"/papers/{api.pid}/repro/runs").json()["items"][0]["metric_count"] == 2
    api.c.delete(f"/api/repro/runs/{r['id']}", headers={"Authorization": "Bearer test-token"})
    assert api.get(f"/papers/{api.pid}/repro/runs").json()["items"] == []


def test_assessment_reads_env_prefixed_readme_commands_dockerfile_and_doc_versions(cenv, tmp_path, no_exec):
    """真实仓库（FINDER）上发现的三处不准：README 里带环境变量的命令被漏掉、文档里的版本没写进版本线索、Dockerfile 被当成依赖清单。"""
    from coderepo import write_repo

    api = cenv
    write_repo(tmp_path / "r2", {
        "README.md": "# X\n\nInstall tensorflow-gpu==1.14.0 and python>=3.6.\n\n```\nCUDA_VISIBLE_DEVICES=0 python train.py --epochs 3\n```\n",
        "train.py": 'if __name__ == "__main__":\n    print(1)\n',
        "environment/Dockerfile": "FROM python:3.6\nRUN pip install numpy\n",
        "requirements.txt": "numpy\n",
    })
    r = api.post(f"/papers/{api.pid}/code/import", {"path": str(tmp_path / "r2")}).json()
    assert api.wait_job(r["job_id"])["status"] == "completed"
    rid2 = r["repo_id"]
    assert api.wait_job(api.post(f"/papers/{api.pid}/repro/assessment", {"repo_id": rid2}).json()["job_id"])["status"] == "completed"
    it = _items(api.get(f"/papers/{api.pid}/repro/assessment", params={"repo_id": rid2}).json()["report"])
    assert it["commands"]["status"] == "found" and "CUDA_VISIBLE_DEVICES=0 python train.py --epochs 3" in it["commands"]["text"]
    assert it["commands"]["evidence"][0]["path"] == "README.md" and it["commands"]["evidence"][0]["line_start"] == 6
    assert "tensorflow-gpu==1.14.0" in it["doc_versions"]["text"]
    docker = it["deps:environment/Dockerfile"]
    assert docker["status"] == "found" and any("FROM python:3.6" in e["quote"] for e in docker["evidence"])
    # 计划里的训练命令优先使用 README 里的写法（含环境变量），不是凭路径猜的 “python train.py”
    plan = api.post(f"/papers/{api.pid}/repro/plan", {"repo_id": rid2}).json()["plan"]
    train = next(s for s in plan["steps"] if s["title"] == "运行训练")
    assert train["command"] == "CUDA_VISIBLE_DEVICES=0 python train.py --epochs 3"


def test_candidates_skip_citation_numbers_instance_counts_and_axis_ticks(cenv):
    """真实论文上发现的噪声：“(ANC)29”（引用编号）、“100 instances”、“Gnutella31”、坐标轴刻度行都不该当成论文报告的数值。"""
    api = cenv
    noisy = _insert_passage(api, "We use the normalized connectivity (ANC)29 and averaged ANC over 100 instances on Gnutella31 network.", 9100)
    ticks = _insert_passage(api, "RatioCut 0.05 0.05 0.04 0.04 0.03", 9101)
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE passages SET kind = 'figure_text' WHERE ord = ?", (ticks,))
    real = _insert_passage(api, "ANC of FINDER reaches 0.35 on the test set and the accuracy is 92%.", 9102)
    row = _insert_passage(api, "Avg score 6.36 6.62 6.68 6.04", 9103)
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE passages SET kind = 'figure_text' WHERE ord = ?", (row,))
    cands = api.get(f"/papers/{api.pid}/repro/target-candidates").json()["items"]
    assert not [c for c in cands if c["ord"] == noisy]  # 整数、紧贴字母/括号的数字一概不要
    assert not [c for c in cands if c["ord"] == ticks]  # 图内文字里的刻度行
    assert {c["value"] for c in cands if c["ord"] == real} == {0.35, 92.0}
    assert {c["label"] for c in cands if c["ord"] == row} == {"Avg score #1", "Avg score #2", "Avg score #3", "Avg score #4"}  # 像指标行的图内文字保留


# ---------------- 硬性规则：“已找到/风险”必须带证据 ----------------
def test_item_without_evidence_can_never_be_found_or_risk():
    from paperdesk.repro.assess import _item

    assert _item("k", "found", "t", "x")["status"] == "uncertain"
    assert _item("k", "risk", "t", "x")["status"] == "uncertain"
    assert "没有可核对的证据" in _item("k", "found", "t", "x")["note"]
    ok = _item("k", "found", "t", "x", [{"type": "code", "path": "a.py", "line_start": 1, "line_end": 1, "quote": "q"}])
    assert ok["status"] == "found" and ok["evidence"]
    assert _item("k", "missing", "t", "x")["status"] == "missing"  # 没找到/不确定本身不需要证据


def test_assessment_has_no_found_or_risk_item_without_evidence(cenv, no_exec):
    report = _assess(cenv)["report"]
    bad = [it["key"] for sec in report["sections"] for it in sec["items"] if it["status"] in ("found", "risk") and not it["evidence"]]
    assert not bad, f"这些项状态是已找到/风险却没有证据：{bad}"
    items = _items(report)
    # 之前没有证据的三项：语言与框架有源码文件证据；一致性要么带证据、要么如实标“不确定”
    assert items["languages"]["status"] in ("found", "missing") and (items["languages"]["status"] != "found" or items["languages"]["evidence"])
    if "framework_versions" in items:
        assert items["framework_versions"]["evidence"]
    assert items["consistency"]["status"] != "found" or items["consistency"]["evidence"]
