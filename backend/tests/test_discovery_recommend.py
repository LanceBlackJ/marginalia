from __future__ import annotations

import base64
import json

import httpx

from f3_helpers import FakeWorld, make_world_api, oa_work
from paperdesk.discovery.codefinder import match_evidence

LONG_ABS = ("This study investigates a long standing problem in the field and proposes a novel method that is evaluated extensively "
            "on several benchmarks showing consistent and significant improvements over strong baselines in many settings. " * 2)


def test_direction_recommendation_groups_surveys_classics_and_recent(tmp_path):
    world = FakeWorld()
    works = [
        oa_work(1, "A survey of cascading failure recovery", doi="10.1/sv", year=2021, wtype="review", pct=0.95,
                abstract="survey of cascading failure recovery methods"),
        oa_work(2, "Classic model of cascading failure in networks", doi="10.1/cl", year=2012, cites=2500, fwci=30, pct=0.999,
                abstract="classic model cascading failure networks recovery"),
        oa_work(3, "New learning approach to cascading failure recovery", doi="10.1/nw", year=2025, cites=2, fwci=1, pct=0.7,
                abstract="learning approach cascading failure recovery"),
    ]
    world.route("openalex", lambda r: httpx.Response(200, json={"results": works}))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/recommend/direction", {"topic": "cascading failure recovery", "ai": False}).json()
        groups = {g["id"]: [w["doi"] for w in g["items"]] for g in d["groups"]}
        assert groups["surveys"] == ["10.1/sv"] and groups["classics"] == ["10.1/cl"] and groups["recent"] == ["10.1/nw"]
        assert api.post("/discovery/recommend/direction", {"topic": " "}).status_code == 400


def test_personal_recommendation_uses_library_and_excludes_it(tmp_path, sample_pdf):
    world = FakeWorld()
    mine = oa_work(1, "Dual Attention Routing for Robust Cascade Recovery", doi="10.1234/dar.2023.001", abstract="dual attention routing cascade")
    cand = oa_work(2, "Attention routing for cascade recovery in power grids", doi="10.1/new", year=2025,
                   abstract="attention routing cascade recovery power grid " * 6)
    world.route("openalex", lambda r: httpx.Response(200, json={"results": [mine, cand]}))
    world.llm_handlers.append(lambda s, u, b: json.dumps(
        {"interests": ["级联失效恢复", "注意力机制"], "queries": [{"query": "attention routing cascade recovery", "why": "你库中《Dual Attention Routing…》研究注意力路由"}]})
        if "科研阅读助手" in s else None)
    api = make_world_api(tmp_path, world)
    with api.c:
        assert api.post("/discovery/recommend/personal", {"ai": False}).status_code == 400  # 空库
        api.import_pdf(sample_pdf)
        d = api.post("/discovery/recommend/personal", {"ai": True}).json()
        dois = [w["doi"] for w in d["results"]]
        assert "10.1/new" in dois and "10.1234/dar.2023.001" not in dois  # 已在库里的不再推荐
        item = next(w for w in d["results"] if w["doi"] == "10.1/new")
        assert item["related_library"]["title"].startswith("Dual Attention") and "共享术语" in item["reason"]
        assert d["interests"] == ["级联失效恢复", "注意力机制"] and d["queries"][0]["why"]


# ---------------- 相似论文 ----------------
def test_similar_works_marks_relation_respects_limit_and_reports_unknown_seed(tmp_path, sample_pdf):
    world = FakeWorld()
    seed = oa_work(10, "Seed paper on key players", doi="10.1/seed", refs=["https://openalex.org/W11"], abstract="key players networks")
    seed["related_works"] = ["https://openalex.org/W12"]
    ref = oa_work(11, "A referenced foundation on key players", doi="10.1/ref", abstract="foundation of key players networks")
    rel = oa_work(12, "A related work on key players", doi="10.1/rel", abstract="related key players study networks")
    mine = oa_work(13, "Dual Attention Routing for Robust Cascade Recovery", doi="10.1234/dar.2023.001", abstract="key players networks routing")
    citing = oa_work(14, "A citing follow-up on key players", doi="10.1/cit", abstract="follow-up on key players networks")
    missing = {"on": False}

    def oa(request: httpx.Request) -> httpx.Response:
        flt = request.url.params.get("filter", "")
        if request.url.path.startswith("/works/https"):
            return httpx.Response(200, json=seed)
        if flt.startswith("openalex:"):
            return httpx.Response(200, json={"results": [ref, rel, mine]})
        if flt.startswith("cites:"):
            return httpx.Response(200, json={"results": [citing]})
        return httpx.Response(200, json={"results": [] if missing["on"] else [seed]})

    world.route("openalex", oa)
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        api.import_pdf(sample_pdf)  # DOI 10.1234/dar.2023.001：应被标为“已在库”
        d = api.post("/discovery/similar", {"doi": "10.1/seed"}).json()
        assert d["seed"]["doi"] == "10.1/seed"
        by_doi = {w["doi"]: w for w in d["results"]}
        assert set(by_doi) == {"10.1/ref", "10.1/rel", "10.1234/dar.2023.001", "10.1/cit"}
        assert by_doi["10.1/ref"]["relation"] == "被该论文引用" and by_doi["10.1/cit"]["relation"] == "引用了该论文"
        assert by_doi["10.1/ref"]["reason"].startswith("被该论文引用")  # 理由只陈述可核对的事实
        assert by_doi["10.1234/dar.2023.001"]["in_library"]
        assert [w["score"] for w in d["results"]] == sorted((w["score"] for w in d["results"]), reverse=True)
        assert len(api.post("/discovery/similar", {"doi": "10.1/seed", "limit": 2}).json()["results"]) == 2
        assert api.post("/discovery/similar", {"doi": "10.1/seed", "limit": 99}).status_code == 400  # 参数校验失败统一为 400
        missing["on"] = True  # 查不到基准论文：明确报 404，不返回空列表冒充“没有相似论文”
        world.route("openalex", lambda r: httpx.Response(200, json={"results": []}))
        assert api.post("/discovery/similar", {"title": "A paper nobody has indexed"}).status_code == 404


# ---------------- 查找代码 ----------------
def test_match_evidence_requires_checkable_clues():
    readme = "# FINDER\nCode for 'Finding key players in complex networks through deep reinforcement learning'. arXiv:1906.04567. doi 10.1038/s42256-020-0177-2"
    ev = match_evidence(readme, title="Finding key players in complex networks through deep reinforcement learning", doi="10.1038/s42256-020-0177-2",
                        arxiv_id="1906.04567")
    assert len(ev) == 3
    assert match_evidence("# some tool for graphs", title="Finding key players in complex networks", doi=None, arxiv_id=None) == []


def test_find_code_marks_high_only_with_readme_evidence(tmp_path):
    world = FakeWorld()

    def gh(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/repositories":
            return httpx.Response(200, json={"items": [
                {"full_name": "a/finder", "html_url": "https://github.com/a/finder", "description": "FINDER", "stargazers_count": 50, "language": "Python", "pushed_at": "2021-01-01"},
                {"full_name": "b/other", "html_url": "https://github.com/b/other", "description": "key players", "stargazers_count": 500, "language": "C++", "pushed_at": "2022-01-01"},
                {"full_name": "c/awesome-key-players", "name": "awesome-key-players", "html_url": "https://github.com/c/awesome-key-players", "description": "A curated list of papers",
                 "stargazers_count": 900, "language": None, "pushed_at": "2023-01-01", "topics": ["awesome"]}]})
        if path == "/repos/a/finder/readme":
            return httpx.Response(200, json={"content": base64.b64encode(b"Official code of 'Finding key players in complex networks through deep reinforcement learning'").decode()})
        if path == "/repos/c/awesome-key-players/readme":
            return httpx.Response(200, json={"content": base64.b64encode(b"- Finding key players in complex networks through deep reinforcement learning (Nature MI 2020)").decode()})
        if path == "/repos/b/other/readme":
            return httpx.Response(200, json={"content": base64.b64encode(b"A different tool").decode()})
        return httpx.Response(404)

    world.route("github", gh)
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/find-code", {"title": "Finding key players in complex networks through deep reinforcement learning"}).json()
        by = {c["full_name"]: c for c in d["candidates"]}
        assert by["a/finder"]["match"] == "high" and "论文标题" in by["a/finder"]["evidence"][0]
        assert by["b/other"]["match"] == "candidate" and "不能确认" in by["b/other"]["evidence"][0]
        assert d["candidates"][0]["full_name"] == "a/finder"  # 高匹配在前，尽管星数更少
        # 论文清单类仓库虽然 README 里有标题，但只算“仅提及”，排在最后，且说明它不是代码实现
        assert by["c/awesome-key-players"]["match"] == "mention" and d["candidates"][-1]["full_name"] == "c/awesome-key-players"
        assert any("不是它的代码实现" in e for e in by["c/awesome-key-players"]["evidence"])


def test_find_code_reports_rate_limit(tmp_path):
    world = FakeWorld()
    world.route("github", lambda r: httpx.Response(403, json={"message": "rate limit"}))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/find-code", {"title": "Some long enough paper title here"}).json()
        assert d["candidates"] == [] and any("每分钟" in w for w in d["warnings"])
        assert api.post("/discovery/find-code", {}).status_code == 400


# ---------------- 收藏 ----------------
def test_saved_works_roundtrip(tmp_path):
    api = make_world_api(tmp_path, FakeWorld(), configure=False)
    with api.c:
        w = {"key": "doi:10.1/x", "title": "Saved paper", "authors": ["A"], "year": 2024, "doi": "10.1/x", "reason": "r", "unknown_field": 1}
        assert api.post("/discovery/saved", w).json()["title"] == "Saved paper"
        items = api.get("/discovery/saved").json()["items"]
        assert len(items) == 1 and "unknown_field" not in items[0] and items[0]["saved_at"]
        assert api.delete("/discovery/saved/doi:10.1/x").json()["ok"]  # key 里带斜杠也能删
        assert api.get("/discovery/saved").json()["items"] == []
        assert api.post("/discovery/saved", {"title": "no key"}).status_code == 400


def test_library_match_settings_and_sources_endpoints(tmp_path, sample_pdf):
    api = make_world_api(tmp_path, FakeWorld(), configure=False)
    with api.c:
        api.import_pdf(sample_pdf)
        m = api.post("/discovery/library-match", {"items": [
            {"key": "k1", "doi": "10.1234/dar.2023.001"}, {"key": "k2", "doi": "10.9/none", "title": "unrelated"}]}).json()["matches"]
        assert list(m) == ["k1"]
        assert api.get("/discovery/settings").json() == {"embedding_model": "bge-m3", "use_embeddings": True}
        assert api.put("/discovery/settings", {"use_embeddings": False, "embedding_model": "  "}).json() == {"embedding_model": "bge-m3", "use_embeddings": False}
        names = [s["name"] for s in api.get("/discovery/sources").json()["items"]]
        assert names[:2] == ["OpenAlex", "Crossref"] and "DBLP" not in names
