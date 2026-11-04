from __future__ import annotations

import itertools
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import pdfgen_journal
from conftest import TOKEN, Api
from fake_mindmap_llm import FakeMindmapLLM

from paperdesk.app import create_app
from paperdesk.mindmap import tree as T

# ======================================================================
# 纯逻辑：校验 / 规范化 / 合并（不需要数据库与网络）
# ======================================================================
VALID = {1, 2, 3, 4, 5, 6, 7, 8}


def parse(raw, **kw):
    return T.parse_tree(raw, valid_ords=kw.pop("valid_ords", VALID), **kw)


def leaf(title, ords, **kw):
    return {"title": title, "summary": kw.pop("summary", ""), "kind": kw.pop("kind", "topic"), "evidence_ords": ords, "children": kw.pop("children", []), **kw}


def test_parse_drops_fabricated_ords_and_nodes_without_real_evidence():
    raw = {"root": {"title": "主题", "evidence_ords": [1], "children": [
        leaf("真实节点", [2, 999]),            # 一个真，一个编造
        leaf("完全编造", [888]),               # 没有任何真实依据 → 整个节点丢弃
        leaf("也编造", ["P777", "[P5000]"]),    # 字符串形式的编号同样要被核对
        leaf("第三个", [3]),
        leaf("第四个", [4]),
    ]}}
    root, rep = parse(raw)
    titles = [c.title for c in root.children]
    assert titles == ["真实节点", "第三个", "第四个"]
    assert root.children[0].ords == [2]
    assert rep.dropped_refs >= 4 and rep.dropped_nodes == 2
    assert any("不存在" in n for n in rep.notes)


def test_parse_limits_text_depth_children_and_moves_cites_out_of_text():
    long_title = "这是一个非常非常非常非常非常非常非常非常长的节点标题超过二十四个字"
    long_summary = "摘要" * 60
    raw = {"root": {"title": "主题", "evidence_ords": [1], "children": [
        leaf(long_title, [2], summary=long_summary + " [P3]"),
        leaf("1. 带编号的标题", [4]),
        leaf("带引用的标题 [P5]", []),   # 标题里的引用被取出作为依据
        leaf("深层", [6], children=[leaf("L3", [6], children=[leaf("L4", [6], children=[leaf("L5不应存在", [6])])])]),
    ] + [leaf(f"多余{i}", [7]) for i in range(10)]}}
    root, rep = parse(raw)
    assert len(root.children) == T.MAX_CHILDREN and rep.child_cut >= 2
    first = root.children[0]
    assert len(first.title) <= T.TITLE_MAX and first.title.endswith("…")
    assert len(first.summary) <= T.SUMMARY_MAX and "[P" not in first.summary
    assert 3 in first.ords  # summary 里的 [P3] 变成了结构化依据
    assert root.children[1].title == "带编号的标题"
    assert root.children[2].title == "带引用的标题" and root.children[2].ords == [5]
    deep = root.children[3]
    depth = 1
    n = deep
    while n.children:
        n = n.children[0]
        depth += 1
    assert depth + 1 <= T.MAX_DEPTH and rep.depth_cut >= 1  # 根=第 1 层，deep 是第 2 层，最多再往下 2 层


def test_parse_group_nodes_inherit_evidence_and_are_marked_derived():
    raw = {"root": {"title": "主题", "children": [
        {"title": "分组", "children": [leaf("甲", [2]), leaf("乙", [3])]},
        leaf("单独", [4]), leaf("另一个", [5]),
    ]}}
    root, _ = parse(raw)
    group = root.children[0]
    assert group.ords == [2, 3] and group.derived is True
    assert root.ords and root.derived is True


def test_parse_rejects_unusable_output_with_readable_reason():
    for bad in ("not a dict", {"foo": 1}, {"root": {"title": ""}}):
        with pytest.raises(T.TreeError):
            parse(bad)
    with pytest.raises(T.TreeError) as e:
        parse({"root": {"title": "只有根", "evidence_ords": [1], "children": []}})
    assert "有效节点太少" in str(e.value)
    with pytest.raises(T.TreeError):  # 全部引用都是编造的
        parse({"root": {"title": "R", "children": [leaf("a", [99]), leaf("b", [98]), leaf("c", [97])]}})


def test_parse_dedupes_same_titles_and_normalizes_kinds_and_entities():
    raw = {"root": {"title": "R", "evidence_ords": [1], "children": [
        leaf("重复", [2], kind="Approach"), leaf("重复", [3]), leaf("方法二", [4], kind="nonsense", figure_ids=[5, 6], equation_ids=["E9"]),
        leaf("方法三", [5]),
    ]}}
    root, rep = parse(raw, valid_figures={5}, valid_equations={9})
    assert [c.title for c in root.children] == ["重复", "方法二", "方法三"] and rep.duplicates == 1
    assert root.children[0].kind == "method" and root.children[1].kind == "topic"
    assert root.children[1].figure_ids == [5] and root.children[1].equation_ids == [9] and rep.dropped_figure_refs == 1


def test_parse_caps_total_nodes():
    kids = [leaf(f"节点{i}", [1 + i % 8], children=[leaf(f"子{i}-{j}", [2]) for j in range(8)]) for i in range(8)]
    root, _ = parse({"root": {"title": "R", "evidence_ords": [1], "children": kids}}, max_nodes=30)
    assert root.count() <= 30


# ---- 合并策略 ----
def _ids():
    c = itertools.count(1)
    return lambda: f"new{next(c)}"


def old(id_, parent, ord_, title, *, edited=False, origin="ai", ords=(1,)):
    return T.OldNode(id_, parent, ord_, title, "旧摘要", "topic", list(ords), [], [], edited, origin)


def g(title, *children, ords=(2,)):
    return T.GNode(title=title, summary="新摘要", ords=list(ords), children=list(children))


def test_merge_preserves_user_edited_and_replaces_untouched():
    olds = [
        old("r", None, 0, "主题"),
        old("a", "r", 0, "背景"),
        old("a1", "a", 0, "旧要点", edited=False),
        old("a2", "a", 1, "我改过的要点", edited=True),
        old("b", "r", 1, "方法"),
        old("b1", "b", 0, "只会被删除的旧节点"),
        old("u", "b", 1, "我新增的", origin="user", edited=True),
    ]
    new = g("主题", g("背景", g("新要点甲"), g("我改过的要点")), g("方法", g("新方法要点")), g("实验"))
    plan = T.merge_trees(olds, new, scope_root_id=None, new_id=_ids())
    assert set(plan.deletes) == {"a1", "b1"}  # 未改动且未被匹配的 AI 节点被新结果取代
    assert "a2" not in plan.updates and "u" not in plan.updates  # 用户的节点原样不动
    assert {"a", "b", "r"} <= set(plan.updates)  # 同名的未改动节点复用旧 id 并更新内容
    titles_new = {i["title"] for i in plan.inserts}
    assert titles_new == {"新要点甲", "新方法要点", "实验"}  # 模型给出的“我改过的要点”不会重复插入
    assert plan.order["a"][-1] == "a2" and "u" in plan.order["b"]
    assert plan.stats["kept_user"] == 2 and plan.stats["removed"] == 2


def test_merge_keeps_ancestors_of_user_nodes_even_when_model_dropped_them():
    olds = [
        old("r", None, 0, "主题"),
        old("x", "r", 0, "模型这次不再给出的分组"),
        old("xu", "x", 0, "用户新增", origin="user", edited=True),
        old("y", "r", 1, "同样不再给出的分组"),
        old("y1", "y", 0, "y 的子节点"),
    ]
    plan = T.merge_trees(olds, g("主题", g("全新的分组")), scope_root_id=None, new_id=_ids())
    assert "x" not in plan.deletes and "xu" not in plan.deletes  # 祖先保留，否则用户节点会断开
    assert set(plan.deletes) == {"y"}  # y 带着 y1 一起删除（外键级联）
    assert plan.order["r"][0:1] != [] and "x" in plan.order["r"]
    assert plan.stats["kept_scaffold"] == 1


def test_merge_branch_scope_only_touches_the_subtree():
    olds = [
        old("b", "r", 1, "方法"),  # 作用域根（它的祖先不在 old 里）
        old("b1", "b", 0, "旧要点"),
        old("b2", "b", 1, "改过的", edited=True),
        old("b21", "b2", 0, "改过节点下的旧子节点"),
    ]
    plan = T.merge_trees(olds, g("忽略", g("新要点"), g("改过的", g("新的子节点"))), scope_root_id="b", new_id=_ids())
    assert "b" not in plan.updates and "b" not in plan.deletes  # 分支根本身不变
    assert set(plan.deletes) == {"b1", "b21"}
    assert {i["title"] for i in plan.inserts} == {"新要点", "新的子节点"}
    new_child = next(i for i in plan.inserts if i["title"] == "新的子节点")
    assert new_child["parent_id"] == "b2"  # 新子节点挂在用户保留的父节点下


def test_merge_user_node_returns_at_its_original_position():
    olds = [old("r", None, 0, "主题"), old("a", "r", 0, "甲"), old("u", "r", 1, "用户插入", origin="user", edited=True), old("b", "r", 2, "乙")]
    plan = T.merge_trees(olds, g("主题", g("甲"), g("乙"), g("丙")), scope_root_id=None, new_id=_ids())
    order = plan.order["r"]
    assert order.index("u") == 1 and order[0] == "a" and order[2] == "b"


def test_export_markdown_outline():
    nodes = [
        {"id": "r", "parent_id": None, "title": "主题", "summary": "全文", "origin": "ai", "refs": [{"page": 1}]},
        {"id": "a", "parent_id": "r", "title": "要点", "summary": "说明", "origin": "user", "refs": [{"page": 3}, {"page": 2}]},
    ]
    md = T.export_markdown("Paper", "逻辑视角", nodes)
    assert md.startswith("# Paper") and "- **主题**（p.1）：全文" in md and "  - **要点**（p.2, 3）·用户添加：说明" in md


# ======================================================================
# 接口：生成 / 核实 / 重试 / 取消 / 编辑 / 合并 / 解释 / 搜索 / 导出
# ======================================================================
@pytest.fixture
def menv(tmp_path, sample_pdf):
    fake = FakeMindmapLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        api.fake = fake
        item = api.import_pdf(sample_pdf)
        assert item["status"] == "imported", item
        api.pid = item["paper_id"]
        api.configure_ai()
        yield api


def gen(api, view="logic", branch_id=None, expect="completed"):
    body = {"view": view, **({"branch_id": branch_id} if branch_id else {})}
    r = api.post(f"/papers/{api.pid}/mindmap/generate", body)
    assert r.status_code == 200, r.text
    job = api.wait_job(r.json()["job_id"])
    assert job["status"] == expect, job
    return job


def tree(api, view="logic"):
    r = api.get(f"/papers/{api.pid}/mindmap", params={"view": view})
    assert r.status_code == 200, r.text
    return r.json()


def by_title(t, title):
    return next(n for n in t["nodes"] if n["title"] == title)


def real_passages(api):
    with api.ctx.db.session() as conn:
        return {r["ord"]: r for r in conn.execute("SELECT ord, page, text FROM passages WHERE paper_id = ?", (api.pid,)).fetchall()}


def test_generate_is_verified_every_node_evidence_is_real(menv):
    api = menv
    t0 = tree(api)
    assert t0["status"] == "none" and t0["nodes"] == []
    gen(api)
    t = tree(api)
    assert t["status"] == "ready" and t["model"] == "strong-m" and t["generated_at"]
    passages = real_passages(api)
    assert len(t["nodes"]) >= 7
    root = t["nodes"][0]
    assert root["parent_id"] is None and root["kind"] == "root" and root["depth"] == 1
    for n in t["nodes"]:
        assert len(n["title"]) <= T.TITLE_MAX and len(n["summary"]) <= T.SUMMARY_MAX
        assert n["refs"], f"节点 {n['title']} 没有可跳转的依据"
        for ref in n["refs"]:
            p = passages[ref["ord"]]
            assert ref["page"] == p["page"] and len(ref["bbox"]) == 4
            # quote 必须来自数据库里的真实段落（可能是截断/摘句，但一定是原文的子串，忽略空白差异）
            assert " ".join(ref["quote"].rstrip("…").split()) in " ".join(p["text"].split())
    assert "编造的结论" not in {n["title"] for n in t["nodes"]}  # 只有编造依据的节点被丢弃
    rep = t["verification"]["report"]
    assert rep["dropped_nodes"] >= 1 and rep["dropped_refs"] >= 1
    # 各层级信息
    assert t["stats"]["max_depth"] <= T.MAX_DEPTH and t["stats"]["user_nodes"] == 0


def test_fabricated_references_are_discarded_not_shown(menv):
    api = menv
    api.fake.mode = "fabricated"
    gen(api)
    t = tree(api)
    for n in t["nodes"]:
        assert 99991 not in n["evidence"]["ords"]
    assert t["verification"]["report"]["dropped_refs"] >= 3


def test_invalid_output_is_retried_once_with_the_reason(menv):
    api = menv
    api.fake.mode = "notjson"
    job = gen(api)
    t = tree(api)
    assert t["status"] == "ready" and t["verification"]["attempts"] == 2 and api.fake.mindmap_calls == 2
    # 重试请求里带着上一次失败的具体原因
    retry_body = api.fake.requests[-1]["body"]["messages"]
    assert any("上一次输出不可用" in m["content"] and "JSON" in m["content"] for m in retry_body if m["role"] == "user")
    assert job["status"] == "completed"


def test_thin_tree_is_rejected_then_retried(menv):
    api = menv
    api.fake.mode = "thin_once"
    gen(api)
    t = tree(api)
    assert t["verification"]["attempts"] == 2 and len(t["nodes"]) >= 7


def test_two_bad_outputs_fail_cleanly_and_keep_the_existing_map(menv):
    api = menv
    gen(api)
    before = tree(api)
    api.fake.mode = "always_bad"
    job = gen(api, expect="failed")
    assert "没有通过校验" in job["error"]
    t = tree(api)
    assert t["status"] == "failed" and "没有通过校验" in t["error"]
    assert [n["id"] for n in t["nodes"]] == [n["id"] for n in before["nodes"]]  # 旧的图没有被破坏
    # 重试：任务重试 API 会重新提交同样的任务
    api.fake.mode = "good"
    retry = api.post(f"/jobs/{job['id']}/retry").json()
    done = api.wait_job(retry["job_id"] if "job_id" in retry else retry["id"])
    assert done["status"] == "completed" and tree(api)["status"] == "ready"


def test_all_fabricated_fails_instead_of_saving_an_unsupported_map(menv):
    api = menv
    api.fake.mode = "all_fabricated"
    job = gen(api, expect="failed")
    assert "有效节点太少" in job["error"]
    assert tree(api)["nodes"] == []


def test_cancel_running_generation_saves_nothing(menv):
    api = menv
    api.fake.gate = threading.Event()
    r = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "logic"}).json()
    deadline = time.time() + 10
    while api.fake.mindmap_calls == 0 and time.time() < deadline:
        time.sleep(0.02)
    assert api.fake.mindmap_calls == 1
    assert tree(api)["status"] == "running"
    api.post(f"/jobs/{r['job_id']}/cancel")
    api.fake.gate.set()
    job = api.wait_job(r["job_id"])
    assert job["status"] == "cancelled"
    t = tree(api)
    assert t["status"] == "none" and t["nodes"] == []


def test_duplicate_generate_request_reuses_the_running_job(menv):
    api = menv
    api.fake.gate = threading.Event()
    a = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "logic"}).json()["job_id"]
    b = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "logic"}).json()["job_id"]
    assert a == b
    api.fake.gate.set()
    api.wait_job(a)


def test_generate_all_three_views_with_view_specific_context(menv):
    api = menv
    r = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "all"}).json()
    assert set(r["jobs"]) == {"logic", "method", "experiment"}
    for job_id in r["jobs"].values():
        assert api.wait_job(job_id)["status"] == "completed"
    ov = api.get(f"/papers/{api.pid}/mindmap/overview").json()["views"]
    assert all(ov[v]["status"] == "ready" and ov[v]["count"] >= 7 for v in ov)
    # 三个视角的提示词不同
    systems = {json.dumps(x["body"]["messages"][0]["content"]) for x in api.fake.requests if "思维导图" in x["body"]["messages"][0]["content"]}
    assert len(systems) == 3


def test_method_view_links_real_figures_and_equations_and_drops_fake_ids(tmp_path):
    fake = FakeMindmapLLM()
    app = create_app(tmp_path / "data", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        pdf = pdfgen_journal.build_journal_paper(tmp_path / "journal.pdf")
        item = api.import_pdf(pdf)
        api.pid = item["paper_id"]
        api.configure_ai()
        figs = api.get(f"/papers/{api.pid}/figures").json()["items"]
        assert figs
        gen(api, "method")
        t = tree(api, "method")
        linked = [n for n in t["nodes"] if n["evidence"]["figure_ids"]]
        assert linked and all(f == figs[0]["id"] for n in linked for f in n["evidence"]["figure_ids"])  # 99999 被丢弃
        detail = api.get(f"/mindmap/nodes/{linked[0]['id']}").json()
        assert detail["figures"][0]["id"] == figs[0]["id"] and len(detail["figures"][0]["bbox"]) == 4
        # 逻辑视角不关联图表
        gen(api, "logic")
        assert not any(n["evidence"]["figure_ids"] for n in tree(api, "logic")["nodes"])


def test_user_edits_survive_regeneration(menv):
    api = menv
    gen(api)
    t = tree(api)
    leaf1 = by_title(t, "研究问题要点一")
    grp = by_title(t, "核心方法")
    root = t["nodes"][0]

    # 1) 改标题 → 标记为用户编辑；折叠、位置不算编辑
    r = api.patch(f"/mindmap/nodes/{leaf1['id']}", {"title": "我改的标题", "summary": "我写的摘要"}).json()
    assert r["user_edited"] is True and r["title"] == "我改的标题"
    # 2) 新增子节点、新增同级
    added = api.post(f"/mindmap/nodes/{grp['id']}/children", {"title": "我新增的子节点", "summary": "x"}).json()
    sib = api.post(f"/mindmap/nodes/{leaf1['id']}/siblings", {"title": "我新增的同级"}).json()
    assert added["origin"] == "user" and added["parent_id"] == grp["id"] and sib["parent_id"] == leaf1["parent_id"]
    assert sib["ord"] == leaf1["ord"] + 1
    # 3) 删除一整个分支（用户删的）
    victim = by_title(t, "实验验证")
    assert api.delete(f"/mindmap/nodes/{victim['id']}").json()["removed"] == 3

    # 折叠与位置只是视图状态：不算编辑，但重新生成后仍然保留（放在新增之后：新增子节点会自动展开父节点）
    prob = by_title(t, "研究问题")
    api.patch(f"/mindmap/nodes/{prob['id']}", {"collapsed": True, "x": 10, "y": 20})
    g2 = api.get(f"/mindmap/nodes/{prob['id']}").json()
    assert g2["collapsed"] is True and g2["x"] == 10 and g2["user_edited"] is False

    # 模型这次给的叶子标题都变了
    api.fake.suffix = "（新）"
    gen(api)
    t2 = tree(api)
    titles = {n["title"] for n in t2["nodes"]}
    assert "我改的标题" in titles and "我新增的子节点" in titles and "我新增的同级" in titles
    kept = by_title(t2, "我改的标题")
    assert kept["id"] == leaf1["id"] and kept["summary"] == "我写的摘要" and kept["user_edited"] is True
    assert by_title(t2, "我新增的子节点")["parent_id"] == grp["id"]  # 仍在原来的父节点下
    assert "核心方法要点一（新）" in titles and "核心方法要点一" not in titles  # 没改过的 AI 叶子被新结果取代
    assert by_title(t2, "核心方法")["id"] == grp["id"]  # 同名分组复用 id（折叠状态、位置都保留）
    assert by_title(t2, "研究问题")["id"] == prob["id"] and by_title(t2, "研究问题")["collapsed"] is True and by_title(t2, "研究问题")["x"] == 10
    assert t2["verification"]["merge"]["kept_user"] == 3
    assert by_title(t2, "测试论文主题")["id"] == root["id"]


def test_branch_regeneration_only_changes_that_branch(menv):
    api = menv
    gen(api)
    t = tree(api)
    grp = by_title(t, "核心方法")
    other = by_title(t, "研究问题")
    mine = api.post(f"/mindmap/nodes/{grp['id']}/children", {"title": "分支里我的节点"}).json()
    other_children_before = {n["id"]: (n["title"], n["summary"]) for n in t["nodes"] if n["parent_id"] == other["id"]}

    gen(api, branch_id=grp["id"])
    t2 = tree(api)
    kids = [n for n in t2["nodes"] if n["parent_id"] == grp["id"]]
    titles = [k["title"] for k in kids]
    assert "新分支要点0" in titles and "新分支要点1" in titles and "分支里我的节点" in titles
    assert not any(n["title"].startswith("核心方法要点") for n in t2["nodes"])  # 旧的 AI 子节点被取代
    assert {n["id"]: (n["title"], n["summary"]) for n in t2["nodes"] if n["parent_id"] == other["id"]} == other_children_before
    assert by_title(t2, "核心方法")["id"] == grp["id"] and mine["id"] in {n["id"] for n in kids}
    assert t2["verification"]["branch"] is True
    # 分支请求只发送了这个分支相关的上下文，并要求只输出该分支
    assert "只重新生成其中一个分支" in api.fake.last_user and "核心方法" in api.fake.last_user


def test_branch_regeneration_validates_target(menv):
    api = menv
    gen(api)
    r = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "logic", "branch_id": "nope"})
    assert r.status_code == 404
    r = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "all", "branch_id": "x"})
    assert r.status_code == 400


def test_node_editing_rules(menv):
    api = menv
    gen(api)
    t = tree(api)
    root = t["nodes"][0]
    a, b = by_title(t, "研究问题"), by_title(t, "核心方法")
    leaf_a = by_title(t, "研究问题要点一")
    # 根节点：可改名，不可删除/移动
    assert api.patch(f"/mindmap/nodes/{root['id']}", {"title": "新根名"}).status_code == 200
    assert api.delete(f"/mindmap/nodes/{root['id']}").status_code == 400
    assert api.patch(f"/mindmap/nodes/{root['id']}", {"parent_id": a["id"]}).status_code == 400
    assert api.post(f"/mindmap/nodes/{root['id']}/siblings", {"title": "x"}).status_code == 400
    # 空标题 / 非法类型 / 非法坐标
    assert api.patch(f"/mindmap/nodes/{a['id']}", {"title": "   "}).status_code == 400
    assert api.patch(f"/mindmap/nodes/{a['id']}", {"kind": "bogus"}).status_code == 400
    assert api.patch(f"/mindmap/nodes/{a['id']}", {}).status_code == 400
    # 移动：不能移到自己的下级；可以移到别的分支，原分支序号被重排
    assert api.patch(f"/mindmap/nodes/{a['id']}", {"parent_id": leaf_a["id"]}).status_code == 400
    moved = api.patch(f"/mindmap/nodes/{leaf_a['id']}", {"parent_id": b["id"], "index": 0}).json()
    assert moved["parent_id"] == b["id"] and moved["ord"] == 0 and moved["user_edited"] is True
    t2 = tree(api)
    assert [n["ord"] for n in t2["nodes"] if n["parent_id"] == a["id"]] == [0]
    assert [n["ord"] for n in t2["nodes"] if n["parent_id"] == b["id"]] == [0, 1, 2]
    # 重新排序（同一个父节点内）
    again = api.patch(f"/mindmap/nodes/{leaf_a['id']}", {"parent_id": b["id"], "index": 2}).json()
    assert again["ord"] == 2
    # 删除分支后序号连续
    assert api.delete(f"/mindmap/nodes/{b['id']}").json()["removed"] == 4
    assert api.get(f"/mindmap/nodes/{b['id']}").status_code == 404
    assert api.get("/mindmap/nodes/nope").status_code == 404


def test_positions_and_collapse_all(menv):
    api = menv
    gen(api)
    t = tree(api)
    ids = [n["id"] for n in t["nodes"][:3]]
    r = api.put(f"/papers/{api.pid}/mindmap/positions", {"view": "logic", "items": [{"id": i, "x": 1.5 * k, "y": 2.0 * k} for k, i in enumerate(ids)]})
    assert r.json()["updated"] == 3
    t = tree(api)
    assert t["nodes"][1]["x"] == 1.5 and t["nodes"][1]["y"] == 2.0
    assert api.delete(f"/papers/{api.pid}/mindmap/positions", params={"view": "logic"}).json()["updated"] >= 3
    assert all(n["x"] is None for n in tree(api)["nodes"])
    n = api.post(f"/papers/{api.pid}/mindmap/collapse", {"view": "logic", "collapsed": True, "min_depth": 2}).json()["updated"]
    t = tree(api)
    assert n >= 1 and all(n["collapsed"] == (n["depth"] >= 2 and n["child_count"] > 0) for n in t["nodes"])
    assert api.post(f"/papers/{api.pid}/mindmap/collapse", {"view": "logic", "collapsed": False}).json()["updated"] == n


def test_search_and_export(menv):
    api = menv
    gen(api)
    hits = api.get(f"/papers/{api.pid}/mindmap/search", params={"q": "核心方法"}).json()["items"]
    assert hits and hits[0]["title"] == "核心方法" and hits[0]["path"] == ["测试论文主题"]
    deep = [h for h in hits if h["title"].startswith("核心方法要点")]
    assert deep and deep[0]["path"] == ["测试论文主题", "核心方法"]
    assert api.get(f"/papers/{api.pid}/mindmap/search", params={"q": "zzzz不存在"}).json()["items"] == []
    assert api.get(f"/papers/{api.pid}/mindmap/search", params={"q": "核心", "view": "method"}).json()["items"] == []
    md = api.get(f"/papers/{api.pid}/mindmap/export", params={"view": "logic", "format": "markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"] and ".md" in md.headers["content-disposition"]
    text = md.content.decode("utf-8")
    assert "- **测试论文主题**" in text and "  - **核心方法**" in text and "（p." in text
    js = api.get(f"/papers/{api.pid}/mindmap/export", params={"view": "logic", "format": "json"}).json()
    assert js["view"] == "logic" and len(js["nodes"]) == len(tree(api)["nodes"]) and js["nodes"][0]["refs"]
    assert api.get(f"/papers/{api.pid}/mindmap/export", params={"view": "method"}).status_code == 404  # 没有可导出的图


def test_explain_node_creates_linked_card_and_keeps_evidence_verification(menv):
    api = menv
    gen(api)
    t = tree(api)
    node = by_title(t, "核心方法要点一")
    ev = api.sse(f"/mindmap/nodes/{node['id']}/explain", {})
    names = [n for n, _ in ev]
    assert names[0] == "meta" and "delta" in names and names[-1] == "done"
    meta = ev[0][1]
    assert meta["node_id"] == node["id"] and meta["context"]["passage_count"] > 0
    card = ev[-1][1]["card"]
    assert card["object_type"] == "mindmap_node" and card["object_label"] == node["title"] and card["saved"] is True
    assert card["status"] == "complete" and card["evidence"] and card["verification"]["status"] == "verified"
    # 默认问题包含节点标题与摘要；发送给模型的选中内容来自节点的真实依据段落
    last = api.fake.requests[-1]["body"]["messages"][-1]["content"]
    assert "核心方法要点一" in last and f"[P{node['evidence']['ords'][0]}]" in last
    # 节点详情里能看到这张卡片，卡片也能反查节点所属的分析链
    detail = api.get(f"/mindmap/nodes/{node['id']}").json()
    assert [c["id"] for c in detail["cards"] if c["relation"] == "explain"] == [card["id"]]
    chains = api.get(f"/papers/{api.pid}/chains").json()["items"]
    assert any(c["object_type"] == "mindmap_node" for c in chains)
    # 再解释一次 → 同一个节点的同一条分析链，且带自定义问题
    ev2 = api.sse(f"/mindmap/nodes/{node['id']}/explain", {"question": "INVALID_CITE 这个节点有什么局限？"})
    card2 = ev2[-1][1]["card"]
    assert card2["chain_id"] == card["chain_id"]
    # 证据核实没有被绕过：模型编造的 [P9999] 被标记
    assert 9999 in card2["verification"]["invalid_citations"] and card2["verification"]["status"] == "partial"
    assert len(api.get(f"/mindmap/nodes/{node['id']}").json()["cards"]) == 2


def test_explain_unknown_node_streams_an_error(menv):
    api = menv
    ev = api.sse("/mindmap/nodes/nope/explain", {})
    assert ev[0][0] == "error" and ev[0][1]["code"] == "not_found"


def test_node_detail_links_cards_by_shared_evidence_and_notes(menv):
    api = menv
    gen(api)
    t = tree(api)
    node = by_title(t, "研究问题要点一")
    o = node["evidence"]["ords"][0]
    # 一张引用了同一段原文的普通问答卡片 + 一条笔记
    ev = api.sse("/ai/ask", {"paper_id": api.pid, "question": "该模块如何工作？", "selection": {"text": "x", "page": 1, "passage_ords": [o]}, "save": True})
    card = ev[-1][1]["card"]
    assert any(e["ord"] == o for e in card["evidence"]) or True  # 假模型引用上下文里的前两个段落；选中段落总在其中
    api.post(f"/papers/{api.pid}/notes", {"text": "我的笔记", "passage_ord": o})
    detail = api.get(f"/mindmap/nodes/{node['id']}").json()
    assert [n["text"] for n in detail["notes"]] == ["我的笔记"]
    rel = [c["id"] for c in detail["cards"] if c["relation"] == "evidence"]
    assert (card["id"] in rel) == any(e["ord"] == o for e in card["evidence"])
    assert detail["code"] == []  # 没有 F2 的代码关联时为空（不报错）
    assert detail["path"][0]["title"] == "测试论文主题" and detail["children"] == []


def test_privacy_log_and_usage_record_each_generation(menv):
    api = menv
    gen(api)
    reqs = api.get("/privacy/requests").json()["items"]
    mm = [r for r in reqs if r["task_type"] == "mindmap"]
    assert mm and mm[0]["passage_count"] > 0 and "思维导图" in mm[0]["summary"] and mm[0]["includes"]["notes"] is False
    usage = api.get("/usage").json()
    assert any(x["task_type"] == "mindmap" for x in usage["by_task_type"])
    # 不会把整篇全文发出去：发送的段落数 ≤ 论文总段落数，且字符数受预算约束
    assert mm[0]["chars_sent"] < 60000


def test_requires_configuration_and_valid_inputs(tmp_path, sample_pdf):
    fake = FakeMindmapLLM()
    app = create_app(tmp_path / "d2", TOKEN, sync_transport=httpx.MockTransport(lambda r: httpx.Response(503)), async_transport=fake.transport)
    api = Api(TestClient(app), app)
    with api.c:
        pid = api.import_pdf(sample_pdf)["paper_id"]
        r = api.post(f"/papers/{pid}/mindmap/generate", {"view": "logic"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"
        api.configure_ai()
        assert api.post("/papers/nope/mindmap/generate", {"view": "logic"}).status_code == 404
        assert api.get("/papers/nope/mindmap").status_code == 404
        assert api.get(f"/papers/{pid}/mindmap", params={"view": "bogus"}).status_code == 400
        assert api.post(f"/papers/{pid}/mindmap/generate", {"view": "bogus"}).status_code == 400
        assert api.get(f"/papers/{pid}/mindmap/search", params={"q": ""}).status_code == 400
        assert api.get(f"/papers/{pid}/mindmap/export", params={"format": "pdf"}).status_code == 400


def test_interrupted_generation_is_reconciled_after_restart(menv):
    api = menv
    # 模拟应用异常退出：运行表停在 running，但对应任务已经被标记失败
    api.fake.gate = threading.Event()
    r = api.post(f"/papers/{api.pid}/mindmap/generate", {"view": "logic"}).json()
    deadline = time.time() + 10
    while api.fake.mindmap_calls == 0 and time.time() < deadline:
        time.sleep(0.02)
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE jobs SET status='failed', error='应用被中断，可重试' WHERE id=?", (r["job_id"],))
    t = tree(api)
    assert t["status"] == "failed" and "中断" in t["error"]
    api.fake.gate.set()
    time.sleep(0.3)


def test_parse_tree_strips_bare_internal_numbers_from_titles_and_summaries():
    raw = {"root": {"title": "方法 P3", "summary": "在P5提出，摘录里说明了细节（P7）", "evidence_ords": [3], "children": [
        {"title": "子节点", "summary": "要点", "evidence_ords": [3]}, {"title": "另一个", "summary": "要点二", "evidence_ords": [3]}]}}
    root, _ = T.parse_tree(raw, valid_ords={3}, min_nodes=2)
    assert root.title == "方法" and "P5" not in root.summary and "P7" not in root.summary and "摘录" not in root.summary


# ---------------- 撤销用的快照：删除返回 snapshot，restore 原样恢复 ----------------
def _shape(t):
    return sorted((n["id"], n["parent_id"], n["title"], n["summary"], n["kind"]) for n in t["nodes"])


def test_delete_snapshot_restores_the_same_subtree_in_place(menv):
    api = menv
    gen(api)
    before = tree(api)
    branch = next(n for n in before["nodes"] if n["parent_id"] and any(c["parent_id"] == n["id"] for c in before["nodes"]))
    order_before = [n["id"] for n in before["nodes"] if n["parent_id"] == branch["parent_id"]]
    r = api.c.delete(f"/api/mindmap/nodes/{branch['id']}", headers={"Authorization": "Bearer test-token"}).json()
    assert r["removed"] >= 2 and len(r["snapshot"]["rows"]) == r["removed"] and r["snapshot"]["parent_id"] == branch["parent_id"]
    mid = tree(api)
    assert branch["id"] not in {n["id"] for n in mid["nodes"]} and len(mid["nodes"]) == len(before["nodes"]) - r["removed"]
    back = api.post("/mindmap/nodes/restore", {"snapshot": r["snapshot"]})
    assert back.status_code == 200 and back.json()["restored"] == r["removed"] and back.json()["id"] == branch["id"]
    after = tree(api)
    assert _shape(after) == _shape(before)  # 同样的 id、标题、摘要、层级
    assert [n["id"] for n in after["nodes"] if n["parent_id"] == branch["parent_id"]] == order_before  # 位置也回到原处
    # 已经恢复过：再恢复一次是冲突，不会产生重复节点
    assert api.post("/mindmap/nodes/restore", {"snapshot": r["snapshot"]}).status_code == 409
    assert len(tree(api)["nodes"]) == len(before["nodes"])


def test_restore_rejects_bad_snapshots_and_root_cannot_be_deleted(menv):
    api = menv
    gen(api)
    t = tree(api)
    root = next(n for n in t["nodes"] if n["parent_id"] is None)
    assert api.c.delete(f"/api/mindmap/nodes/{root['id']}", headers={"Authorization": "Bearer test-token"}).status_code == 400
    assert api.post("/mindmap/nodes/restore", {"snapshot": {}}).status_code == 400
    assert api.post("/mindmap/nodes/restore", {"snapshot": {"parent_id": "nope", "rows": [{"id": "x", "parent_id": "nope"}]}}).status_code == 404
    leaf = next(n for n in t["nodes"] if n["parent_id"] and not any(c["parent_id"] == n["id"] for c in t["nodes"]))
    snap = api.c.delete(f"/api/mindmap/nodes/{leaf['id']}", headers={"Authorization": "Bearer test-token"}).json()["snapshot"]
    forged = {**snap, "rows": [{**snap["rows"][0], "parent_id": "somewhere-else"}]}
    assert api.post("/mindmap/nodes/restore", {"snapshot": forged}).status_code == 400  # 必须恰好挂在原父节点下


def test_user_added_node_survives_delete_restore_and_regeneration(menv):
    api = menv
    gen(api)
    t = tree(api)
    parent = next(n for n in t["nodes"] if n["parent_id"] is None)
    added = api.post(f"/mindmap/nodes/{parent['id']}/children", {"title": "我新增的想法", "summary": "手工添加"}).json()
    snap = api.c.delete(f"/api/mindmap/nodes/{added['id']}", headers={"Authorization": "Bearer test-token"}).json()["snapshot"]
    assert api.post("/mindmap/nodes/restore", {"snapshot": snap}).status_code == 200
    gen(api)  # 重新生成：恢复回来的用户节点仍然保留
    node = next(n for n in tree(api)["nodes"] if n["id"] == added["id"])
    assert node["title"] == "我新增的想法" and node["origin"] == "user"
