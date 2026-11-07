from __future__ import annotations

import stat
import zipfile

import httpx
import pytest

from coderepo import FILES, make_zip, write_repo
from conftest import make_api

from paperdesk.code import fetch, links, safeio
from paperdesk.code.py_analyze import analyze_python, analyze_pylike
from paperdesk.code.ts_analyze import analyze_ts
from paperdesk.util import ApiError


# ---------------- 静态分析 ----------------
def test_python_symbols_calls_and_params():
    fa = analyze_python("train.py", FILES["train.py"])
    names = {s.qualname: s for s in fa.symbols}
    assert {"build_parser", "run_training", "main", "<main>"} <= set(names)
    assert names["run_training"].doc == "Offline training loop."
    assert names["run_training"].signature == "def run_training(args)"
    assert [c.name for c in names["run_training"].calls] == ["load_graphs", "Agent", "preprocess", "agent.learn", "save_model"]
    assert names["<main>"].kind == "main" and [c.name for c in names["<main>"].calls] == ["main"] and fa.has_main
    params = {(p.name, p.origin): p.value for p in fa.params}
    assert params[("lr", "argparse")] == "0.0001" and params[("nodes_min", "argparse")] == "30"
    assert params[("LEARNING_RATE", "constant")] == "0.0001" and params[("N_STEP", "constant")] == "5"
    assert fa.imports["Agent"].module == "model.agent" and fa.imports["Agent"].name == "Agent"


def test_methods_and_kwargs_and_nested():
    fa = analyze_python("model/agent.py", FILES["model/agent.py"])
    q = {s.qualname: s for s in fa.symbols}
    assert q["Agent"].kind == "class" and q["Agent.learn"].kind == "method" and q["Agent.learn"].parent == "Agent"
    assert "self.q_values" in [c.name for c in q["Agent.learn"].calls]
    assert any(p.name == "lr" and p.value == "0.0001" and p.origin == "kwarg" for p in fa.params)
    nested = analyze_python("n.py", "def outer():\n    def inner():\n        helper()\n    inner()\n")
    nq = {s.qualname: s for s in nested.symbols}
    assert "outer.inner" in nq and [c.name for c in nq["outer"].calls] == ["inner"] and [c.name for c in nq["outer.inner"].calls] == ["helper"]


def test_cython_and_old_syntax_fall_back_with_a_warning():
    pyx = "import numpy as np\ncimport numpy as cnp\n\ncdef int helper(int x):\n    return x\n\ncpdef run(n):\n    return helper(n)\n\nclass Env:\n    def step(self):\n        return run(1)\n"
    fa = analyze_python("a.pyx", pyx, "cython")
    q = {s.qualname: s for s in fa.symbols}
    assert {"helper", "run", "Env", "Env.step"} <= set(q) and any("容错" in e for e in fa.errors)
    assert [c.name for c in q["run"].calls] == ["helper"]
    py2 = analyze_python("old.py", "def f():\n    print 'hi'\n    g()\n")
    assert any("容错" in e for e in py2.errors) and "f" in {s.name for s in py2.symbols}


@pytest.mark.parametrize("ext,src,expected", [
    (".js", b"function a(){ b(); }\nconst c = () => { a(); };\nclass K { m(){ c(); } }", {"a": ["b"], "c": ["a"], "K.m": ["c"]}),
    (".ts", b"export function a(): void { b(); }", {"a": ["b"]}),
    (".java", b"class M { static void main(String[] x){ run(); } void run(){} }", {"M.main": ["run"]}),
    (".c", b"int h(){ return 1; }\nint main(){ return h(); }", {"main": ["h"]}),
    (".cpp", b"void G::add(int a){ edges.push_back(a); }", {"G.add": ["edges.push_back"]}),
    (".go", b"package m\ntype M struct{}\nfunc (m *M) Run(){ helper() }", {"M.Run": ["helper"]}),
    (".rs", b"fn run(){ helper(); }", {"run": ["helper"]}),
    (".sh", b"build(){ make all; }", {"build": ["make"]}),
])
def test_tree_sitter_languages(ext, src, expected):
    fa = analyze_ts("x" + ext, ext, src)
    got = {s.qualname: [c.name for c in s.calls] for s in fa.symbols if s.kind != "module"}
    for qual, calls in expected.items():
        assert got.get(qual) == calls, (ext, got)
    if ext in (".c", ".java"):
        assert fa.has_main


# ---------------- 仓库级分析与调用图 ----------------
@pytest.fixture
def repo(api, imported_paper):
    pid = imported_paper
    root = api.ctx.paths.paper_dir(pid) / "code" / "tmp"
    write_repo(root.parent / "src_repo")
    r = api.post(f"/papers/{pid}/code/import", {"path": str(root.parent / "src_repo")}).json()
    job = api.wait_job(r["job_id"])
    assert job["status"] == "completed", job
    return api, pid, r["repo_id"], job["result"]["overview"]


@pytest.fixture
def imported_paper(api, sample_pdf):
    return api.import_pdf(sample_pdf)["paper_id"]


def test_repo_overview_entries_roles_and_dependencies(repo):
    api, pid, rid, ov = repo
    assert ov["languages"]["python"]["files"] >= 7 and "c++" in ov["languages"] and "javascript" in ov["languages"]
    assert set(ov["frameworks"]) >= {"PyTorch", "NumPy", "NetworkX"}
    assert "requirements.txt" in ov["dependency_files"] and any(c.endswith("default.yaml") for c in ov["config_files"])
    eps = {(e["path"], e["kind"]) for e in ov["entry_points"]}
    assert ("train.py", "main_guard") in eps and ("evaluate.py", "main_guard") in eps and ("native/graph.cpp", "main_function") in eps
    train_ep = next(e for e in ov["entry_points"] if e["path"] == "train.py")
    assert train_ep["command"].startswith("python train.py") and train_ep["role"] == "train"  # README 里的运行命令
    assert ov["skipped"]["binary"] == 1  # weights/model.pt 只标注不落盘
    tree = api.get(f"/code/repos/{rid}/tree").json()["files"]
    roles = {f["path"]: f["role"] for f in tree}
    assert roles["train.py"] == "train" and roles["evaluate.py"] == "test" and roles["requirements.txt"] == "dependency"
    assert roles["config/default.yaml"] == "config" and roles["data/loader.py"] == "data" and roles["model/agent.py"] == "model"
    pt = next(f for f in tree if f["path"] == "weights/model.pt")
    assert pt["skipped"] == "binary"
    f = api.get(f"/code/repos/{rid}/file", params={"path": "weights/model.pt"}).json()
    assert f["content"] is None and "二进制" in f["note"]


def test_cross_file_call_graph_resolution(repo):
    api, pid, rid, _ = repo
    syms = {s["qualname"] + "@" + s["path"]: s for s in api.get(f"/code/repos/{rid}/symbols", params={"limit": 200}).json()["items"]}
    run = syms["run_training@train.py"]
    g = api.get(f"/code/repos/{rid}/symbols/{run['id']}/graph").json()
    callees = {c["callee_name"]: c for c in g["callees"]}
    # import 解析：从 data.loader / model.agent / utils 导入的函数/类被精确指向（resolved）
    assert callees["load_graphs"]["path"] == "data/loader.py" and callees["load_graphs"]["certainty"] == "resolved"
    assert callees["Agent"]["qualname"] == "Agent.__init__" and callees["Agent"]["path"] == "model/agent.py"
    assert callees["preprocess"]["certainty"] == "resolved" and callees["save_model"]["path"] == "utils.py"
    # agent = Agent(...) 之后的 agent.learn()：通过变量类型推断精确解析
    assert callees["agent.learn"]["certainty"] == "resolved" and callees["agent.learn"]["qualname"] == "Agent.learn"
    # 函数参数上的方法调用无法推断类型，只能按名称匹配（不确定）
    hooks = syms["run_hooks@utils.py"]
    hc = {c["callee_name"]: c for c in api.get(f"/code/repos/{rid}/symbols/{hooks['id']}/graph").json()["callees"]}
    assert hc["h.on_done"]["certainty"] == "name" and hc["h.on_done"]["qualname"] == "Hook.on_done"
    # 反向：谁调用了 load_graphs
    loader = syms["load_graphs@data/loader.py"]
    callers = api.get(f"/code/repos/{rid}/symbols/{loader['id']}/graph").json()["callers"]
    assert {c["path"] for c in callers} == {"train.py", "evaluate.py"}
    # self.method 精确解析；第三方库调用为 external，不产生边
    learn = syms["Agent.learn@model/agent.py"]
    lc = {c["callee_name"]: c for c in api.get(f"/code/repos/{rid}/symbols/{learn['id']}/graph").json()["callees"]}
    assert lc["self.q_values"]["certainty"] == "resolved" and lc["log"]["path"] == "utils.py"
    qv = syms["Agent.q_values@model/agent.py"]
    qc = {c["callee_name"]: c for c in api.get(f"/code/repos/{rid}/symbols/{qv['id']}/graph").json()["callees"]}
    assert qc["np.dot"]["certainty"] == "external" and qc["np.dot"]["callee_id"] is None


def test_flow_is_ordered_static_and_phase_labelled(repo):
    api, pid, rid, _ = repo
    flow = api.get(f"/code/repos/{rid}/flow").json()
    assert flow["roots"][0]["path"] in ("train.py", "evaluate.py", "native/graph.cpp")
    train_root = next(r for r in flow["roots"] if r["path"] == "train.py")
    flow = api.get(f"/code/repos/{rid}/flow", params={"symbol_id": train_root["symbol_id"]}).json()
    labels = [n["label"] for n in flow["nodes"]]
    assert labels[0] == "__main__" and labels[1] == "main"
    # 先 build_parser（参数解析）再 run_training，再按调用先后进入数据加载、模型初始化、训练、保存
    assert labels.index("build_parser") < labels.index("run_training") < labels.index("load_graphs") < labels.index("Agent.__init__") < labels.index("Agent.learn") < labels.index("save_model")
    phases = {n["label"]: n["phase"] for n in flow["nodes"]}
    assert phases["build_parser"] == "参数解析" and phases["load_graphs"] == "数据加载" and phases["Agent.learn"] == "训练" and phases["save_model"] == "结果保存"
    assert all(n["line_start"] <= n["line_end"] and n["path"] for n in flow["nodes"])
    assert all(e["line"] > 0 and e["certainty"] in ("resolved", "name") for e in flow["edges"]) and "静态" in flow["note"]
    # 外部库调用不在流程里
    assert not any("np.dot" in n["label"] for n in flow["nodes"])


def test_file_api_is_path_safe_and_supports_symbols(repo):
    api, pid, rid, _ = repo
    f = api.get(f"/code/repos/{rid}/file", params={"path": "train.py"}).json()
    assert f["content"].startswith('"""Training entry."""') and f["language"] == "python"
    assert {"build_parser", "run_training", "<main>"} <= {s["name"] for s in f["symbols"]}
    assert all(s["path"] == "train.py" and "parent" in s for s in f["symbols"]), "符号要带 path，前端据此跳转"
    for bad in ("../../../etc/passwd", "/etc/passwd", "..\\..\\secret", "train.py/../../x"):
        assert api.get(f"/code/repos/{rid}/file", params={"path": bad}).status_code in (400, 404)
    assert api.get(f"/code/repos/{rid}/file", params={"path": "nope.py"}).status_code == 404
    found = api.get(f"/code/repos/{rid}/symbols", params={"q": "agent"}).json()["items"]
    assert any(s["qualname"] == "Agent" for s in found)


def test_delete_repo_removes_files(repo):
    api, pid, rid, _ = repo
    root = api.ctx.paths.paper_dir(pid) / "code" / rid
    assert root.exists()
    assert api.delete(f"/code/repos/{rid}").json()["ok"] and not root.exists()
    assert api.get(f"/code/repos/{rid}").status_code == 404


# ---------------- 安全：zip-slip / 符号链接 / 超大文件 / 炸弹 ----------------
def test_zip_slip_symlink_and_large_files_are_blocked(tmp_path):
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("proj/ok.py", "x = 1\n")
        zf.writestr("proj/../../escape.py", "pwned")
        zf.writestr("/abs/evil.py", "pwned")
        zf.writestr("proj/C:/win.py", "pwned")
        info = zipfile.ZipInfo("proj/link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
        zf.writestr("proj/big.txt", "a" * (safeio.MAX_FILE_BYTES + 10))
        zf.writestr("proj/weights.pt", b"\x00\x01")
        zf.writestr("proj/data.bin2", b"\x00\x00binary")
        zf.writestr("proj/node_modules/x.js", "skip")
    dest = tmp_path / "out"
    rep = safeio.safe_extract_zip(zp, dest)
    assert not (tmp_path / "escape.py").exists() and not (dest.parent / "escape.py").exists()
    assert {f.path for f in rep.files if not f.skipped} == {"ok.py"}
    skipped = {f.path: f.skipped for f in rep.files if f.skipped}
    assert skipped["big.txt"] == "too_large" and skipped["weights.pt"] == "binary" and skipped["data.bin2"] == "binary"
    assert rep.skipped_symlinks == 1 and len(rep.skipped_unsafe) >= 2
    assert any("符号链接" in w for w in rep.warnings) and any("路径不安全" in w for w in rep.warnings)
    assert not any(p.name == "link.py" for p in dest.rglob("*")) and not (dest / "node_modules").exists()
    assert (dest / "ok.py").read_text() == "x = 1\n"  # 顶层目录 proj/ 被剥掉


def test_zip_bomb_and_non_zip_are_rejected(tmp_path, monkeypatch):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ApiError):
        safeio.safe_extract_zip(bad, tmp_path / "o1")
    monkeypatch.setattr(safeio, "MAX_TOTAL_BYTES", 1000)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "0" * 10_000)
    with pytest.raises(ApiError) as e:
        safeio.safe_extract_zip(bomb, tmp_path / "o2")
    assert "炸弹" in e.value.message


def test_copy_folder_skips_symlinks_and_never_touches_source(tmp_path):
    src = tmp_path / "src"
    write_repo(src, {"a.py": "x=1\n", ".git/config": "secret", "w.pt": "bin"})
    before = sorted(p.name for p in src.rglob("*"))
    try:
        (src / "link.py").symlink_to(src / "a.py")
    except OSError:
        pytest.skip("本机不允许创建符号链接")
    rep = safeio.copy_folder(src, tmp_path / "dest")
    assert {f.path for f in rep.files if not f.skipped} == {"a.py"} and rep.skipped_symlinks == 1
    assert not (tmp_path / "dest" / ".git").exists()
    assert sorted(p.name for p in src.rglob("*") if p.name != "link.py") == before


# ---------------- 论文里的代码链接 ----------------
def test_extracts_links_broken_across_lines_with_page_numbers():
    txt = ("All source codes and models are publicly available through our Code Ocean compute capsule (https://doi. "
           "org/10.24433/CO.3005605.v1) or on GitHub (https://github.com/ FFrankyy/FINDER). See also https://example.com/x.")
    found = dict(links.extract_links(txt))
    assert "https://github.com/FFrankyy/FINDER" in found and "https://doi.org/10.24433/CO.3005605.v1" in found
    assert links.classify("https://github.com/a/b")[0] == "github" and links.classify("https://github.com/a/b")[1] is True
    assert links.classify("https://zenodo.org/record/1") == ("zenodo", False)


def test_code_sources_endpoint_uses_passages_and_never_invents_links(api, sample_pdf, tmp_path):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    assert api.get(f"/papers/{pid}/code/sources").json() == {"items": []}  # 示例论文没有声明代码
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)",
                     (pid, 9001, 7, "paragraph",
                      "Code availability: source code is on GitHub (https://github.com/ FFrankyy/FINDER) and Code Ocean (https://doi. org/10.24433/CO.3005605.v1)."))
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)",
                     (pid, 9002, 9, "reference", "[12] Smith. Some paper. https://doi.org/10.1000/xyz123"))
    items = api.get(f"/papers/{pid}/code/sources").json()["items"]
    urls = [i["url"] for i in items]
    assert urls[0] == "https://github.com/FFrankyy/FINDER" and items[0]["page"] == 7 and items[0]["supported"] and items[0]["is_code_statement"]
    assert "https://doi.org/10.24433/CO.3005605.v1" in urls
    assert "https://doi.org/10.1000/xyz123" not in urls  # 参考文献里的 DOI 不是代码链接


# ---------------- GitHub 获取（模拟网络）----------------
def _github_handler(zip_bytes: bytes, *, size_kb=200, pushed="2024-06-01T00:00:00Z", fail_codeload=False):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://api.github.com/repos/acme/finder":
            return httpx.Response(200, json={"name": "finder", "owner": {"login": "acme"}, "html_url": "https://github.com/acme/finder",
                                              "default_branch": "master", "size": size_kb, "archived": False, "description": "demo"})
        if url == "https://api.github.com/repos/acme/finder/commits/master":
            return httpx.Response(200, json={"sha": "deadbeef" * 5, "commit": {"committer": {"date": pushed}}})
        if url.startswith("https://api.github.com/repos/acme/missing"):
            return httpx.Response(404)
        if url.startswith("https://codeload.github.com/acme/finder/zip/"):
            assert url.endswith("deadbeef" * 5), "必须下载解析出的那个 commit，而不是浮动的分支"
            return httpx.Response(404) if fail_codeload else httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)
    return handler


def test_github_resolve_fetch_records_commit_and_warns_when_code_is_newer(tmp_path, sample_pdf):
    zbytes = make_zip({k: v for k, v in FILES.items() if k != "weights/model.pt"})
    api, _ = make_api(tmp_path, handler=_github_handler(zbytes))
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]   # 论文年份 2023
        info = api.post(f"/papers/{pid}/code/resolve", {"url": "https://github.com/acme/finder"}).json()
        assert info["branch"] == "master" and info["commit_sha"] == "deadbeef" * 5 and info["requires_approval"] is False
        assert api.post(f"/papers/{pid}/code/resolve", {"url": "https://example.com/x"}).json()["error"]["code"] == "unsupported"
        assert api.post(f"/papers/{pid}/code/resolve", {"url": "https://github.com/acme/missing"}).status_code == 404

        r = api.post(f"/papers/{pid}/code/fetch", {"url": "https://github.com/acme/finder"}).json()
        job = api.wait_job(r["job_id"])
        assert job["status"] == "completed", job
        repo = api.get(f"/code/repos/{r['repo_id']}").json()
        assert repo["status"] == "ready" and repo["commit_sha"] == "deadbeef" * 5 and repo["branch"] == "master" and repo["downloaded_at"]
        assert repo["origin"] == "github" and repo["authored_by"] == "user" and repo["overview"]["entry_points"]
        assert repo["warnings"] == []   # 2024 vs 论文 2023：未超过 2 年
        assert [x["id"] for x in api.get(f"/papers/{pid}/code/repos").json()["items"]] == [repo["id"]]


def test_github_stale_code_warning_and_approval_for_large_repos(tmp_path, sample_pdf):
    zbytes = make_zip(FILES)
    api, _ = make_api(tmp_path, handler=_github_handler(zbytes, size_kb=40_000, pushed="2027-03-01T00:00:00Z"))
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        r = api.post(f"/papers/{pid}/code/fetch", {"url": "https://github.com/acme/finder"}).json()
        assert r["requires_approval"] is True and any("实现变化" in w for w in r["info"]["warnings"])
        j = api.get(f"/jobs/{r['job_id']}").json()
        assert j["status"] == "waiting_approval" and "40" in j["message"] or "39" in j["message"]
        import time
        time.sleep(0.3)
        assert api.get(f"/jobs/{r['job_id']}").json()["status"] == "waiting_approval"  # 批准前绝不下载
        assert api.post(f"/jobs/{r['job_id']}/approve").json()["status"] in ("pending", "running", "completed")
        assert api.wait_job(r["job_id"])["status"] == "completed"
        assert "实现变化" in " ".join(api.get(f"/code/repos/{r['repo_id']}").json()["warnings"])


def test_github_download_failure_marks_repo_failed(tmp_path, sample_pdf):
    api, _ = make_api(tmp_path, handler=_github_handler(b"", fail_codeload=True))
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        r = api.post(f"/papers/{pid}/code/fetch", {"url": "https://github.com/acme/finder"}).json()
        job = api.wait_job(r["job_id"])
        assert job["status"] == "failed" and "私有" in job["error"]
        repo = api.get(f"/code/repos/{r['repo_id']}").json()
        assert repo["status"] == "failed" and repo["error"]


def test_local_zip_import_and_authored_by(api, sample_pdf, tmp_path):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    zp = tmp_path / "mycode.zip"
    zp.write_bytes(make_zip(FILES))
    r = api.post(f"/papers/{pid}/code/import", {"path": str(zp), "authored_by": "self"}).json()
    assert api.wait_job(r["job_id"])["status"] == "completed"
    repo = api.get(f"/code/repos/{r['repo_id']}").json()
    assert repo["origin"] == "zip" and repo["authored_by"] == "self" and repo["overview"]["files"] >= 10  # “自行实现”被如实标注
    assert api.post(f"/papers/{pid}/code/import", {"path": str(tmp_path / "missing.zip")}).status_code == 400


def test_receiver_type_inference_for_locals_and_self_attributes_and_copies():
    src = (
        "class Agent:\n    def learn(self):\n        pass\n\n"
        "class Trainer:\n    def __init__(self):\n        self.agent = Agent()\n    def run(self):\n        self.agent.learn()\n\n"
        "def main():\n    t = Trainer()\n    t.run()\n"
    )
    fa = analyze_python("a.py", src)
    q = {s.qualname: s for s in fa.symbols}
    assert [(c.name, c.recv) for c in q["Trainer.run"].calls] == [("self.agent.learn", "Agent")]
    assert [(c.name, c.recv) for c in q["main"].calls] == [("Trainer", None), ("t.run", "Trainer")]
    # 回退解析（Cython 等）也推断
    pyx = "cdef class Env:\n    def step(self):\n        return 1\n\ndef main():\n    e = Env()\n    e.step()\n"
    fb = analyze_python("e.pyx", pyx, "cython")
    assert ("e.step", "Env") in [(c.name, c.recv) for s in fb.symbols if s.name == "main" for c in s.calls]


def test_ambiguous_names_prefer_the_nearest_copy(api, imported_paper, tmp_path):
    """研究仓库常有多份近似拷贝（A/Model.py 与 B/Model.py）：同目录子树里最近且唯一的候选胜出。"""
    files = {
        "A/train.py": "from Model import Net\nif __name__ == '__main__':\n    n = Net()\n    n.fit()\n",
        "A/Model.py": "class Net:\n    def fit(self):\n        pass\n",
        "B/train.py": "from Model import Net\nif __name__ == '__main__':\n    n = Net()\n    n.fit()\n",
        "B/Model.py": "class Net:\n    def fit(self):\n        pass\n",
    }
    write_repo(tmp_path / "copies", files)
    r = api.post(f"/papers/{imported_paper}/code/import", {"path": str(tmp_path / "copies")}).json()
    assert api.wait_job(r["job_id"])["status"] == "completed"
    items = api.get(f"/code/repos/{r['repo_id']}/symbols", params={"limit": 50}).json()["items"]
    for side in ("A", "B"):
        main = next(s for s in items if s["kind"] == "main" and s["path"] == f"{side}/train.py")
        callees = api.get(f"/code/repos/{r['repo_id']}/symbols/{main['id']}/graph").json()["callees"]
        fit = next(c for c in callees if c["callee_name"] == "n.fit")
        assert fit["path"] == f"{side}/Model.py", "必须指向同目录下的那一份，而不是另一份拷贝"


def test_renamed_repository_is_reported():
    def handler(request):
        url = str(request.url)
        if url.endswith("/repos/old/finder"):
            return httpx.Response(200, json={"name": "FINDER", "owner": {"login": "newowner"}, "html_url": "https://github.com/newowner/FINDER", "default_branch": "master", "size": 10})
        if "/commits/" in url:
            return httpx.Response(200, json={"sha": "a" * 40, "commit": {"committer": {"date": "2020-07-07T00:00:00Z"}}})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        info = fetch.resolve_github(client, fetch.parse_github_url("https://github.com/old/finder"), paper_year=2020)
    assert info.owner == "newowner" and any("已改名或转移" in w and "old/finder" in w for w in info.warnings)
    assert fetch.parse_github_url("https://github.com/o/r/tree/dev/sub").branch == "dev" and fetch.parse_github_url("https://github.com/o/r.git").name == "r"
    assert fetch.parse_github_url("https://gitlab.com/o/r") is None


def test_hyperparameters_from_cython_constants_and_self_attributes():
    pyx = "cdef int NUM_MIN = 30\ncdef float LEARNING_RATE = 0.0001\nBATCH_SIZE = 64\n\nclass FINDER:\n    def __init__(self):\n        self.embedding_size = 64\n        self.name = 'x'\n"
    fa = analyze_python("a.pyx", pyx, "cython")
    got = {p.name: p.value for p in fa.params}
    assert got["NUM_MIN"] == "30" and got["LEARNING_RATE"] == "0.0001" and got["BATCH_SIZE"] == "64" and got["embedding_size"] == "64"
    assert "name" not in got
    py = analyze_python("b.py", "class A:\n    def __init__(self):\n        self.learning_rate = 0.001\n        self.NUM_MAX = 50\n        self.model = Net()\n")
    assert {p.name: p.value for p in py.params} == {"learning_rate": "0.001", "NUM_MAX": "50"}


def test_canonical_parameter_names_cover_ai_spellings():
    from paperdesk.code.consistency import canon

    assert canon("min_training_nodes") == "nodes_min" and canon("NUM_MAX") == "nodes_max"
    assert canon("experience_replay_buffer_size") == "replay_size" and canon("MEMORY_SIZE") == "replay_size"
    assert canon("batchSize") == "batch_size" and canon("learningRate") == "learning_rate" and canon("embedding_dimension") == "embedding_dim"
    assert canon("totally_unrelated_name") is None


def test_reanalyze_rebuilds_the_index_without_redownloading_and_keeps_links(repo):
    api, pid, rid, ov = repo
    sym = next(s for s in api.get(f"/code/repos/{rid}/symbols", params={"limit": 200}).json()["items"] if s["qualname"] == "run_training")
    link = api.post(f"/code/repos/{rid}/links", {"module_name": "Training", "symbol_id": sym["id"], "passage_ords": []}).json()
    r = api.post(f"/code/repos/{rid}/reanalyze", {}).json()
    job = api.wait_job(r["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["overview"]["symbols"] == ov["symbols"] and api.get(f"/code/repos/{rid}").json()["status"] == "ready"
    new_sym = next(s for s in api.get(f"/code/repos/{rid}/symbols", params={"limit": 200}).json()["items"] if s["qualname"] == "run_training")
    links = api.get(f"/code/repos/{rid}/links").json()["items"]
    mine = next(l for l in links if l["id"] == link["id"])
    assert mine["symbol_id"] == new_sym["id"], "关联应按路径+符号名迁移到新的符号 id"


def test_old_analysis_is_flagged_outdated_until_reanalyzed(repo):
    api, pid, rid, ov = repo
    assert api.get(f"/code/repos/{rid}").json()["analysis_outdated"] is False
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE code_repos SET analysis_version = 1 WHERE id = ?", (rid,))
    assert api.get(f"/code/repos/{rid}").json()["analysis_outdated"] is True
    job = api.wait_job(api.post(f"/code/repos/{rid}/reanalyze", {}).json()["job_id"])
    assert job["status"] == "completed", job
    assert api.get(f"/code/repos/{rid}").json()["analysis_outdated"] is False


def test_images_are_kept_and_served_but_other_binaries_are_only_listed(repo):
    api, pid, rid, _ = repo
    src = api.ctx.paths.paper_dir(pid) / "code" / "src_repo"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (src / "assets").mkdir(exist_ok=True)
    (src / "assets" / "fig.png").write_bytes(png)
    (src / "assets" / "deck.pptx").write_bytes(b"PK\x03\x04" + b"\x00" * 16)
    r = api.post(f"/papers/{pid}/code/import", {"path": str(src)}).json()
    assert api.wait_job(r["job_id"])["status"] == "completed"
    rid2 = r["repo_id"]
    img = api.get(f"/code/repos/{rid2}/file", params={"path": "assets/fig.png"}).json()
    assert img["content"] is None and img["preview"] == "image"
    raw = api.get(f"/code/repos/{rid2}/raw", params={"path": "assets/fig.png"})
    assert raw.status_code == 200 and raw.content == png and raw.headers["content-type"] == "image/png"
    other = api.get(f"/code/repos/{rid2}/file", params={"path": "assets/deck.pptx"}).json()
    assert other["content"] is None and other["preview"] is None
    assert api.get(f"/code/repos/{rid2}/raw", params={"path": "assets/deck.pptx"}).status_code == 400
    assert api.get(f"/code/repos/{rid2}/raw", params={"path": "../../x.png"}).status_code == 400
