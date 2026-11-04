"""隔离执行器：用假的 docker 可执行文件（tests/fake_docker.py）测完执行器的逻辑。

没有在真实 Docker 里跑过：这里验证的是我们传给 docker 的参数、任务/取消/超时/清理流程和结果解析，
不是容器本身的隔离效果。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_code_ai import cenv  # noqa: F401
from test_repro import _assess, _plan

from paperdesk.repro import executor, sandbox

FAKE = str(Path(__file__).parent / "fake_docker.py")


@pytest.fixture
def denv(cenv, monkeypatch, tmp_path):
    state = tmp_path / "docker_state"
    state.mkdir()
    (state / "images.txt").write_text("python:3.11-slim\t130MB\n", "utf-8")
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setenv("PAPERDESK_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("SOME_TOKEN", "tok-must-not-leak")
    monkeypatch.setattr(executor, "docker_argv", lambda: [sys.executable, FAKE])
    monkeypatch.setattr(sandbox, "find_docker", lambda: "C:/fake/docker.exe")
    monkeypatch.setattr(sandbox, "_run", lambda _a: (0, "29.9.2"))
    spawned = []
    real = subprocess.Popen

    def spy(args, *a, **k):
        spawned.append(list(args))
        return real(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", spy)
    cenv.state, cenv.spawned = state, spawned
    _assess(cenv)
    cenv.plan = _plan(cenv)
    return cenv


def calls(api):
    f = api.state / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def make_step(api, command, cwd=""):
    r = api.post(f"/repro/plans/{api.plan['id']}/steps", {"title": "测试步骤", "command": command, "cwd": cwd}).json()
    return r["plan"]["steps"][-1]


def body(step, **kw):
    return {"confirm": True, "command": step["command"], "cwd": step["cwd"], **kw}


def run(api, step, **kw):
    r = api.post(f"/repro/steps/{step['id']}/execute", body(step, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def finish(api, ex, timeout=20):
    api.wait_job(ex["job_id"], timeout)
    return api.get(f"/repro/exec/{ex['id']}").json()


def run_args(api):
    return next(c["argv"] for c in calls(api) if c["argv"][0] == "run")


def wait_started(api, ex):
    name = executor.container_name(ex["id"])
    deadline = time.time() + 10
    while not (api.state / f"{name}.started").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert (api.state / f"{name}.started").exists()
    return name


# ---------------- 选项与确认 ----------------
def test_options_report_missing_image_and_pull_hint(denv):
    o = denv.get("/repro/exec/options").json()
    assert o["available"] is True and o["image_present"] is True and o["pull"] is None
    assert o["defaults"]["timeout_s"] == 600 and o["maxima"]["memory_mb"] >= o["defaults"]["memory_mb"]
    o2 = denv.get("/repro/exec/options", params={"image": "pytorch/pytorch:2.4.0"}).json()
    assert o2["image_present"] is False and o2["pull"]["image"] == "pytorch/pytorch:2.4.0" and o2["pull"]["size_hint"]
    assert denv.get("/repro/exec/options", params={"image": "-v /:/host"}).status_code == 400


def test_run_requires_confirm_and_matching_command(denv):
    api = denv
    step = make_step(api, "echo hi")
    assert api.post(f"/repro/steps/{step['id']}/execute", {**body(step), "confirm": False}).status_code == 400
    r = api.post(f"/repro/steps/{step['id']}/execute", {**body(step), "command": "rm -rf /"})
    assert r.status_code == 409 and "修改过" in r.json()["error"]["message"]
    assert api.post(f"/repro/steps/{step['id']}/approve", {"decision": "reject"}).status_code == 200
    assert api.post(f"/repro/steps/{step['id']}/execute", body(step)).status_code == 409  # 已拒绝
    assert not [c for c in calls(api) if c["argv"][0] == "run"]


def test_missing_image_is_never_pulled_automatically(denv):
    api = denv
    step = make_step(api, "echo hi")
    r = api.post(f"/repro/steps/{step['id']}/execute", body(step, image="pytorch/pytorch:2.4.0"))
    assert r.status_code == 409 and "同意拉取" in r.json()["error"]["message"]
    assert not [c for c in calls(api) if c["argv"][0] in ("pull", "run")]


def test_pull_only_after_explicit_consent(denv):
    api = denv
    assert api.post("/repro/images/pull", {"image": "python:3.12-slim"}).status_code == 400
    assert not [c for c in calls(api) if c["argv"][0] == "pull"]
    j = api.post("/repro/images/pull", {"image": "python:3.12-slim", "confirm": True}).json()
    assert api.wait_job(j["job_id"])["status"] == "completed"
    assert [c for c in calls(api) if c["argv"][0] == "pull"][0]["argv"][1] == "python:3.12-slim"
    assert api.get("/repro/exec/options", params={"image": "python:3.12-slim"}).json()["image_present"] is True


# ---------------- 安全参数 ----------------
def test_docker_args_are_locked_down(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "echo hello")))
    assert ex["status"] == "succeeded" and ex["exit_code"] == 0
    a = run_args(api)
    j = " ".join(a)
    assert a[a.index("--network") + 1] == "none"
    assert "--privileged" not in a and "docker.sock" not in j and "--pid" not in a and "--ipc" not in a
    assert a[a.index("--user") + 1] not in ("0", "0:0", "root") and "--cap-drop" in a and "no-new-privileges" in j and "--read-only" in a
    assert "--rm" in a
    mounts = [a[i + 1] for i, x in enumerate(a) if x == "--mount"]
    assert len(mounts) == 2
    code = next(m for m in mounts if "target=/code" in m)
    work = next(m for m in mounts if "target=/work" in m)
    assert code.endswith(",readonly") and "readonly" not in work
    assert ex["id"] in work  # 可写目录是这次运行自己的目录
    assert "-v" not in a and "--volume" not in a and "--env-file" not in a
    env_args = [a[i + 1] for i, x in enumerate(a) if x == "-e"]
    assert env_args and all("=" in e and not any(w in e.lower() for w in ("key", "token", "secret")) for e in env_args)
    assert a[a.index("--memory") + 1] == "2048m" and a[a.index("--pids-limit") + 1] == "256" and a[a.index("--cpus") + 1] == "2.0"
    assert a[-3:-1] == ["sh", "-c"] and a[-1].endswith("echo hello")
    # 宿主机上只启动了 docker 本身，没有直接跑用户命令
    assert api.spawned and all(s[:2] == [sys.executable, FAKE] for s in api.spawned)
    names = calls(api)[-1]["env_names"]
    assert "PAPERDESK_API_KEY" not in names and "SOME_TOKEN" not in names


def test_network_only_when_checked_and_logged(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "echo hello"), network=True))
    a = run_args(api)
    assert a[a.index("--network") + 1] == "bridge" and ex["network"] is True
    assert any("已按你的勾选开放" in ln for ln in ex["log"])
    assert any("开放" in x for x in api.get(f"/jobs/{ex['job_id']}").json()["log"])


def test_limits_can_be_lowered_not_raised(denv):
    api = denv
    step = make_step(api, "echo hello")
    assert api.post(f"/repro/steps/{step['id']}/execute", body(step, limits={"memory_mb": 999999})).status_code == 400
    ex = finish(api, run(api, step, limits={"memory_mb": 512, "timeout_s": 30, "cpus": 1, "pids": 64}))
    a = run_args(api)
    assert a[a.index("--memory") + 1] == "512m" and a[a.index("--pids-limit") + 1] == "64" and ex["limits"]["timeout_s"] == 30


def test_cwd_cannot_escape_repo(denv):
    api = denv
    step = make_step(api, "echo hi", cwd="../../etc")
    assert api.post(f"/repro/steps/{step['id']}/execute", body(step)).status_code == 400
    assert not [c for c in calls(api) if c["argv"][0] == "run"]


# ---------------- 运行、失败、超时、取消 ----------------
def test_failed_run_is_reported_as_failed(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "python fail.py")))
    assert ex["status"] == "failed" and ex["exit_code"] == 3 and "退出码 3" in ex["reason"]
    assert api.get(f"/jobs/{ex['job_id']}").json()["status"] == "failed"
    assert any("Traceback" in ln for ln in ex["log"])
    assert api.post(f"/repro/exec/{ex['id']}/import", {}).status_code == 409  # 失败的运行不能当作结果导入


def test_timeout_kills_and_cleans_container(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "sleep 100"), limits={"timeout_s": 5}), timeout=30)
    assert ex["status"] == "timeout" and "运行时长上限" in ex["reason"]
    name = executor.container_name(ex["id"])
    assert (api.state / f"{name}.kill").exists() and (api.state / f"{name}.rm").exists()
    assert api.get(f"/jobs/{ex['job_id']}").json()["status"] == "failed"


def test_cancel_kills_and_cleans_container(denv):
    api = denv
    ex = run(api, make_step(api, "sleep 100"))
    name = wait_started(api, ex)
    assert api.post(f"/repro/exec/{ex['id']}/cancel").status_code == 200
    done = finish(api, ex)
    assert done["status"] == "cancelled"
    assert (api.state / f"{name}.kill").exists() and (api.state / f"{name}.rm").exists()
    assert api.get(f"/jobs/{ex['job_id']}").json()["status"] == "cancelled"
    assert api.post(f"/repro/exec/{ex['id']}/cancel").status_code == 409


def test_one_running_task_per_repo(denv):
    api = denv
    ex = run(api, make_step(api, "sleep 100"))
    wait_started(api, ex)
    step2 = make_step(api, "echo hi")
    assert api.post(f"/repro/steps/{step2['id']}/execute", body(step2)).status_code == 409
    api.post(f"/repro/exec/{ex['id']}/cancel")
    finish(api, ex)


def test_recover_cleans_stale_runs(denv):
    api = denv
    step = make_step(api, "echo hi")
    ex = run(api, step)
    finish(api, ex)
    executor._update(api.ctx, ex["id"], status="running")  # 模拟应用被强杀后重启：记录停在 running
    executor.recover(api.ctx)
    got = api.get(f"/repro/exec/{ex['id']}").json()
    assert got["status"] == "failed" and "中断" in got["reason"]
    assert (api.state / f"{executor.container_name(ex['id'])}.rm").exists()


# ---------------- 结果：来源可追溯 ----------------
def test_metrics_come_from_log_and_files_with_sources(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "python metrics.py")))
    assert ex["status"] == "succeeded" and any(f["path"] == "results.csv" for f in ex["files"])
    m = api.get(f"/repro/exec/{ex['id']}/metrics").json()
    by = {x["name"]: x for x in m["metrics"]}
    assert by["f1"]["value"] == 0.88 and by["f1"]["source"]["kind"] == "file" and by["f1"]["source"]["path"] == "results.csv"
    assert by["accuracy"]["value"] == 0.91 and by["accuracy"]["source"]["kind"] == "log"
    log_text = (executor.run_dir(api.ctx, api.pid, ex["id"]) / "run.log").read_text("utf-8").splitlines()
    assert log_text[by["accuracy"]["source"]["line"] - 1].strip() == "accuracy: 0.91"  # 行号确实指向那一行
    assert by["loss"]["value"] == 0.25 and "not a metric line 1 2 3" not in by
    assert all(x["source"]["run_id"] == ex["id"] for x in m["metrics"])
    assert any("epoch" in w and "2 次" in w for w in m["warnings"])
    t = api.post(f"/papers/{api.pid}/repro/targets", {"name": "accuracy", "value": 0.9}).json()
    imp = api.post(f"/repro/exec/{ex['id']}/import", {"name": "容器里的结果"}).json()
    assert imp["env"]["联网"] == "否" and imp["repo_id"] == api.rid
    cmp = api.get(f"/repro/runs/{imp['id']}/compare").json()
    item = next(i for i in cmp["items"] if i["target"]["id"] == t["id"])
    assert item["metric"]["value"] == 0.91 and item["metric"]["source"]["kind"] == "log"
    rep = api.get(f"/repro/runs/{imp['id']}/report").json()["content"]
    assert ex["id"] in rep and "run.log" in rep
    assert api.get(f"/repro/exec/{ex['id']}").json()["imported_run_id"] == imp["id"]


def test_no_metrics_means_no_metrics(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "echo hello")))
    m = api.get(f"/repro/exec/{ex['id']}/metrics").json()
    assert m["metrics"] == [] and m["warnings"]
    assert api.post(f"/repro/exec/{ex['id']}/import", {}).status_code == 400


def test_delete_run_removes_files(denv):
    api = denv
    ex = finish(api, run(api, make_step(api, "echo hello")))
    d = executor.run_dir(api.ctx, api.pid, ex["id"])
    assert (d / "run.log").is_file()
    assert api.delete(f"/repro/exec/{ex['id']}").status_code == 200 and not d.exists()
    assert api.get(f"/repro/exec/{ex['id']}").status_code == 404


def test_unavailable_docker_refuses(denv, monkeypatch):
    api = denv
    monkeypatch.setattr(sandbox, "_run", lambda _a: (1, ""))
    step = make_step(api, "echo hi")
    assert api.post(f"/repro/steps/{step['id']}/execute", body(step)).status_code == 409
