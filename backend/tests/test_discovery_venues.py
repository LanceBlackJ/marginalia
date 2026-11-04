from __future__ import annotations

import json

import httpx
import pytest

from f3_helpers import FakeWorld, make_world_api, oa_work
from paperdesk.discovery import venue_sources
from paperdesk.discovery.venues import TOP_VENUES, VENUES, match_venue, norm_venue, public_list, venues_for_field

LONG_ABS = ("This study investigates a long standing problem in the field and proposes a novel method that is evaluated extensively "
            "on several benchmarks showing consistent and significant improvements over strong baselines in many settings. " * 2)


# ---------------------------------------------------------------- 白名单匹配（纯函数）
@pytest.mark.parametrize("name,vtype,expected", [
    ("Nature", "journal", "nature"),
    ("nature", "journal", "nature"),
    ("Science", "journal", "science"),
    ("Proceedings of the National Academy of Sciences", "journal", "pnas"),
    ("Proceedings of the National Academy of Sciences of the United States of America", "journal", "pnas"),
    ("Physical Review Letters", "journal", "prl"),
    ("The Lancet", "journal", "lancet"),
    ("New England Journal of Medicine", "journal", "nejm"),  # 归一化会去掉开头的 “The”
    ("The New England Journal of Medicine", "journal", "nejm"),
    ("Nature Machine Intelligence", "journal", "nat-mach-intell"),
    ("IEEE Transactions on Pattern Analysis and Machine Intelligence", "journal", "tpami"),
    ("Proceedings of the 37th International Conference on Machine Learning", "conference", "icml"),
    ("International Conference on Machine Learning", "conference", "icml"),
    ("2022 IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)", "conference", "cvpr"),
    ("Advances in neural information processing systems", "conference", "neurips"),
    ("Advances in Neural Information Processing Systems", "book series", "neurips"),
    ("Proceedings of the AAAI Conference on Artificial Intelligence", "journal", "aaai"),
    ("Proceedings of the 61st Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)", "conference", "acl"),
    ("Proceedings of the 2021 Conference on Empirical Methods in Natural Language Processing", "conference", "emnlp"),
    ("Proceedings of the 28th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining", "conference", "kdd"),
    ("Proceedings of the VLDB Endowment", "journal", "pvldb"),
    ("ICLR", None, "iclr"),
    # 2025-10 在真实 OpenAlex 上看到的来源名称
    ("IEEE Computer Society Conference on Computer Vision and Pattern Recognition/Proceedings - "
     "IEEE Computer Society Conference on Computer Vision and Pattern Recognition/Proceedings", "conference", "cvpr"),
    ("Uncertainty in Artificial Intelligence", "conference", "uai"),
    ("Proceedings of the conference - Association for Computational Linguistics. Meeting", "conference", "acl"),
    ("ACM SIGKDD Conference on Knowledge Discovery and Data Mining (KDD)", "conference", "kdd"),
    ("Operating Systems Design and Implementation", "conference", "osdi"),
    ("Networked Systems Design and Implementation", "conference", "nsdi"),
    ("ACM SIGOPS Symposium on Operating Systems Principles (SOSP)", "conference", "sosp"),
    ("ACM SIGSAC Conference on Computer and Communications Security (CCS)", "conference", "ccs"),
    ("Proceedings - International Symposium on Computer Architecture/Proceedings", "conference", "isca"),
    ("CHI Conference on Human Factors in Computing Systems", "conference", "chi"),
])
def test_match_venue_positive_and_normalization(name, vtype, expected):
    v = match_venue(name, vtype)
    assert (v.id if v else None) == expected


@pytest.mark.parametrize("name,vtype", [
    ("arXiv (Cornell University)", "repository"),
    ("arXiv", "journal"),
    ("Nature", "repository"),  # 类型不对
    ("ICML Workshop on Machine Learning for Healthcare", "conference"),
    ("Workshop on Neural Information Processing Systems", "conference"),
    ("Companion Proceedings of the ACM Web Conference 2023", "conference"),
    ("Findings of the Association for Computational Linguistics: ACL 2023", "conference"),
    ("Scientific Data", "journal"),  # 不在名单里
    ("Journal of Unrelated Studies", "journal"),
    ("Nature Reviews Physics", "journal"),  # 同前缀但不是名单里的刊
    ("International Conference on Machine Learning and Applications", "conference"),  # 名称包含主会名但不是主会
    ("Journal of Science", "journal"),
    # 真实 OpenAlex 里和主会同名前缀但不是主会的来源
    ("IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW)", "conference"),
    ("2009 IEEE Computer Society Conference on Computer Vision and Pattern Recognition Workshops", "conference"),
    ("ACM/IEEE International Conference on Software Engineering: Companion Proceedings (ICSE-Companion)", "conference"),
    ("IEEE/ACM International Conference on Software Engineering: Software Engineering in Practice (ICSE-SEIP)", "conference"),
    ("International Conference on Scour and Erosion (ICSE)", "conference"),  # 缩写相同但是另一个会议
    ("", "journal"),
    (None, "journal"),
    ("   ", None),
])
def test_match_venue_rejects_lookalikes_workshops_preprints_and_missing(name, vtype):
    assert match_venue(name, vtype) is None


def test_norm_venue_strips_year_ordinal_prefix_and_parentheses():
    assert norm_venue("Proceedings of the 37th International Conference on Machine Learning, ICML 2020 (Virtual)") == \
        "international conference on machine learning icml"
    assert norm_venue("The  Web Conference 2024") == "web conference"
    assert norm_venue(None) == ""


def test_whitelist_is_consistent_and_reviewable():
    assert len({v.id for v in VENUES}) == len(VENUES)
    assert all(v.kind in ("journal", "conference") and v.category for v in VENUES)
    keys = {}
    for v in VENUES:  # 两个不同的来源不能共用同一个归一化别名，否则匹配会含糊
        for a in v.aliases:
            assert keys.setdefault(norm_venue(a), v.id) == v.id, a
    ids = {v.id for v in VENUES}
    assert {"nature", "science", "cell", "pnas", "nat-mach-intell", "prl", "lancet", "nejm", "neurips", "icml", "iclr", "cvpr", "iccv",
            "eccv", "acl", "emnlp", "naacl", "aaai", "ijcai", "kdd", "www", "sigir", "sigmod", "pvldb", "osdi", "sosp",
            "stoc", "focs", "jmlr", "tpami", "ijcv", "tkde"} <= ids
    assert len(public_list()) == len(VENUES)
    # 学科过滤：综合来源对所有学科有效；计算机顶会只出现在计算机科学
    assert {v.id for v in venues_for_field(17)} >= {"nature", "neurips", "tpami"}
    assert "neurips" not in {v.id for v in venues_for_field(12)} and "nature" in {v.id for v in venues_for_field(12)}


# ---------------------------------------------------------------- 随机探索（模拟的 OpenAlex）
def _world(log: list, *, good_at: dict | None = None, decoy_only: bool = False, only_bad: bool = False, no_sources: bool = False) -> FakeWorld:
    world = FakeWorld()
    by_search = {v.search: v for v in VENUES}
    good_at = good_at or {3.0: 10, 1.5: 10, None: 10}

    def oa(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/sources"):
            log.append(("sources", params.get("search")))
            if no_sources:
                return httpx.Response(200, json={"results": []})
            q = params.get("search") or ""
            res = [
                {"id": "https://openalex.org/S999", "display_name": f"Workshop on {q}", "type": "conference", "works_count": 99999},
                {"id": "https://openalex.org/S998", "display_name": "arXiv (Cornell University)", "type": "repository", "works_count": 9999999},
            ]
            v = by_search.get(q)
            if v and not decoy_only:
                res.append({"id": f"https://openalex.org/S{100 + VENUES.index(v)}", "display_name": v.name, "type": v.kind, "works_count": 5000})
            return httpx.Response(200, json={"results": res})
        log.append(("works", params))
        flt = params.get("filter", "")
        fwci_min = float(flt.split("fwci:>")[1].split(",")[0]) if "fwci:>" in flt else None
        n = good_at.get(fwci_min, 10)
        good = [] if only_bad else [
            oa_work(100 + i, f"Top venue study number {i}", doi=f"10.1/g{i}", year=2022, cites=30 + i, fwci=4.0 + i * 0.1, pct=0.9 + i * 0.005,
                    abstract=LONG_ABS, source="International Conference on Machine Learning" if i % 2 else "Nature",
                    stype="conference" if i % 2 else "journal", field=("17", "Computer Science"))
            for i in range(n)
        ]
        bad = [  # 这些必须被来源核对丢掉：即使检索条件没挡住，也不能出现在结果里
            oa_work(300, "Preprint copy of a top paper", doi="10.1/pre", abstract=LONG_ABS, source="arXiv (Cornell University)", stype="repository"),
            oa_work(301, "A workshop paper", doi="10.1/ws", abstract=LONG_ABS, source="ICML Workshop on Foo", stype="conference"),
            oa_work(302, "Paper without any source", doi="10.1/nosrc", abstract=LONG_ABS, source=None),
            oa_work(303, "A findings paper", doi="10.1/find", abstract=LONG_ABS,
                    source="Findings of the Association for Computational Linguistics: ACL 2023", stype="conference"),
            oa_work(304, "Ordinary journal paper", doi="10.1/ord", abstract=LONG_ABS, source="Scientific Data", stype="journal"),
        ]
        return httpx.Response(200, json={"results": good + bad, "meta": {"count": 4000 if "has_abstract" in flt else 90000}})

    world.route("openalex", oa)
    return world


@pytest.fixture(autouse=True)
def _reset_resolution_cache():
    venue_sources._MISSING.clear()
    yield
    venue_sources._MISSING.clear()


def test_random_explore_only_returns_whitelisted_top_venues(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log), configure=False)
    with api.c:
        d = api.post("/discovery/random", {"count": 3, "ai": False, "seed": 3}).json()
        assert len(d["items"]) == 3
        dumped = json.dumps(d, ensure_ascii=False)
        for bad in ("pre", "ws", "nosrc", "find", "ord"):  # 预印本 / workshop / 无来源 / findings / 非名单期刊
            assert f"10.1/{bad}" not in dumped, bad
        for it in d["items"]:
            tv = it["top_venue"]
            assert tv and tv["label"] in ("已确认顶刊", "已确认顶会") and tv["id"] in {v.id for v in TOP_VENUES}
            assert match_venue(it["venue"], it["venue_type"]) is not None
            assert it["abstract"] and it["quality_facts"]
        st = d["filter"]
        assert st["dropped_unconfirmed"] == 5  # 5 篇来源不可确认的被丢弃，并如实报告
        assert st["field"] is None and st["venue_pool"] == len(TOP_VENUES) and 1 <= len(st["venues"]) <= 6
        assert st["pool_total"] == 90000 and st["pool_passed"] == 4000
        assert "只显示来源可确认" in st["venue_policy"]
        assert d["items"][0]["summary"] is None  # 没有模型时不伪造导读


def test_random_explore_queries_whitelisted_source_ids_directly(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log), configure=False)
    with api.c:
        api.post("/discovery/random", {"ai": False, "seed": 1})
        works = [p for kind, p in log if kind == "works"]
        flt = works[0]["filter"]
        # 直接按白名单来源 id 检索，而不是先随机再丢弃；不再用“核心来源”这种宽泛条件
        assert "primary_location.source.id:S1" in flt and "|" in flt.split("primary_location.source.id:")[1].split(",")[0]
        for part in ("type:article", "has_abstract:true", "is_retracted:false", "fwci:>3.0"):
            assert part in flt, part
        assert "is_core" not in flt and "primary_topic.field.id" not in flt  # 全部学科：不加学科条件
        assert works[0]["sample"] == "40"
        searched = [q for kind, q in log if kind == "sources"]
        assert 1 <= len(searched) <= 6 and set(searched) <= {v.search for v in VENUES}
    # 选了学科：加学科条件，并且只会取适用于该学科的来源
    log2: list = []
    api2 = make_world_api(tmp_path / "b", _world(log2), configure=False)
    with api2.c:
        d = api2.post("/discovery/random", {"field_id": 17, "ai": False, "seed": 2}).json()
        flt = [p for kind, p in log2 if kind == "works"][0]["filter"]
        assert "primary_topic.field.id:fields/17" in flt
        assert d["filter"]["field_zh"] == "计算机科学"
        assert {v["id"] for v in d["filter"]["venues"]} <= {v.id for v in venues_for_field(17)}


def test_random_explore_relaxes_fwci_threshold_when_the_sample_is_small(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log, good_at={3.0: 3, 1.5: 10, None: 10}), configure=False)
    with api.c:
        d = api.post("/discovery/random", {"ai": False, "seed": 1}).json()
        assert d["filter"]["thresholds"]["fwci_min"] == 1.5
        assert len([1 for kind, _ in log if kind == "works" and _.get("per-page") != "1"]) >= 2


def test_random_explore_never_lets_unconfirmed_sources_through(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log, only_bad=True), configure=False)
    with api.c:
        r = api.post("/discovery/random", {"ai": False, "seed": 1})
        assert r.status_code == 404 and "来源可确认" in r.json()["error"]["message"]


def test_source_resolution_rejects_workshops_and_repositories_and_reports_failure(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log, decoy_only=True), configure=False)
    with api.c:
        r = api.post("/discovery/random", {"ai": False, "seed": 1})
        assert r.status_code == 502 and "确认" in r.json()["error"]["message"]
        assert not [1 for kind, _ in log if kind == "works"]  # 没确认到来源就不检索论文，更不会拿诱饵来源凑数


def test_resolved_sources_are_cached_so_next_click_does_not_search_again(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log), configure=False)
    with api.c:
        api.post("/discovery/random", {"ai": False, "seed": 7})
        first = [q for kind, q in log if kind == "sources"]
        assert first
        log.clear()
        api.post("/discovery/random", {"ai": False, "seed": 7, "exclude": []})  # 同一随机种子：同一批来源
        assert not [1 for kind, _ in log if kind == "sources"]
        with api.ctx.db.session() as conn:
            assert conn.execute("SELECT COUNT(*) FROM venue_sources").fetchone()[0] >= len(first)


def test_random_explore_excludes_seen_and_library_and_uses_ai_summary(tmp_path):
    world = _world([])
    world.llm_handlers.append(lambda s, u, b: json.dumps(
        {"research_question": "如何改进某方法", "contribution": "提出了一种新方法并在多个基准上验证", "why_read": "发表于顶会且引用领先"})
        if "科研导读助手" in s else None)
    api = make_world_api(tmp_path, world)
    with api.c:
        first = api.post("/discovery/random", {"ai": True, "seed": 5}).json()["items"][0]
        assert first["summary"]["research_question"] == "如何改进某方法" and first["summary"]["source"] == "AI 根据摘要概括"
        again = api.post("/discovery/random", {"ai": True, "seed": 5, "exclude": [first["key"]]}).json()["items"][0]
        assert again["key"] != first["key"]  # “换一篇”不会重复
        priv = api.get("/privacy/requests").json()["items"]
        assert any(p["task_type"] == "discovery_summary" and "不含用户内容" in p["summary"] for p in priv)


def test_venue_list_and_field_counts_are_exposed_for_review(tmp_path):
    api = make_world_api(tmp_path, _world([]), configure=False)
    with api.c:
        d = api.get("/discovery/venues").json()
        assert d["count"] == len(VENUES) and "只显示来源可确认" in d["policy"]
        assert {"id", "name", "short", "kind", "category"} <= set(d["items"][0])
        fields = {f["id"]: f for f in api.get("/discovery/fields").json()["items"]}
        assert fields[17]["venue_count"] > fields[12]["venue_count"] > 0  # 计算机科学的来源最多；人文也有综合顶刊
        assert api.post("/discovery/random", {"field_id": 9999, "ai": False}).status_code in (200, 404, 502)  # 未知学科按“全部”处理，不报 500


# ---------------------------------------------------------------- 按研究方向随机探索
def test_clean_topic_removes_filter_syntax_and_limits_length():
    from paperdesk.discovery.recommend import clean_topic

    assert clean_topic("complex networks, FINDER|x:y") == "complex networks FINDER x y"
    assert clean_topic('  "key"   players ') == "key players"
    assert clean_topic(" , | ") is None and clean_topic(None) is None
    assert len(clean_topic("a" * 200)) == 60


def test_random_explore_with_topic_searches_title_and_abstract_and_drops_field_limit(tmp_path):
    log: list = []
    api = make_world_api(tmp_path, _world(log), configure=False)
    with api.c:
        d = api.post("/discovery/random", {"ai": False, "seed": 2, "field_id": 17, "topic": "complex networks, key|players"}).json()
        works = [p for kind, p in log if kind == "works"]
        flt = works[0]["filter"]
        assert "title_and_abstract.search:complex networks key players" in flt
        assert "primary_topic.field.id" not in flt  # 给了研究方向就不再限定一级学科
        assert d["filter"]["topic"] == "complex networks key players" and d["filter"]["field"] is None
        # 来源核对不因为带了方向而放松
        assert all(it["top_venue"] for it in d["items"])
        # 不带方向时不出现这个条件
        api.post("/discovery/random", {"ai": False, "seed": 2})
        assert "title_and_abstract" not in [p for kind, p in log if kind == "works"][-1]["filter"]


def test_venue_intro_only_for_listed_and_ids_valid():
    from paperdesk.discovery.venue_info import INTRO, curated
    from paperdesk.discovery.venues import BY_ID

    assert all(k in BY_ID for k in INTRO)
    assert curated("Nature")["intro"]
    assert curated("Some Random Journal") is None
    assert curated("CVPR Workshops") is None  # workshop 不算


def test_random_year_range_param():
    from paperdesk.discovery.recommend import random_filters

    f = random_filters(None, 2015, 2018, None, ["S1"])
    assert "publication_year:2015-2018" in f


def test_recommend_filters():
    from paperdesk.discovery.recommend import passes_filters

    top = {"year": 2022, "venue": "Nature", "venue_type": "journal", "status": "published"}
    core = {"year": 2022, "venue": "Some Journal", "core_venue": True, "status": "published"}
    pre = {"year": 2022, "venue": "arXiv", "status": "preprint"}
    peer = {"year": 2022, "venue": "X", "peer_reviewed": True, "status": "published"}
    assert passes_filters(dict(top), None, None, "top") and not passes_filters(dict(core), None, None, "top")
    assert passes_filters(dict(core), None, None, "core") and not passes_filters(dict(peer), None, None, "core")
    assert passes_filters(dict(peer), None, None, "peer") and not passes_filters(dict(pre), None, None, "peer")
    assert passes_filters(dict(pre), None, None, "any")
    assert not passes_filters(dict(top), 2023, None, "any") and not passes_filters(dict(top), None, 2021, "any")
    assert not passes_filters({"year": None, "venue": "Nature"}, 2020, None, "any")
    w = dict(top)
    passes_filters(w, None, None, "top")
    assert w["top_venue"]["short"] == "Nature"


def test_venue_page_has_intro_stats_and_the_papers_published_there(tmp_path):
    import httpx

    from f3_helpers import FakeWorld, make_world_api, oa_work

    world = FakeWorld()
    cited = oa_work(1, "A highly cited paper in the journal", doi="10.1/hc", cites=900, abstract="graph learning study", source="Nature Communications")
    recent = oa_work(2, "A very recent paper in the journal", doi="10.1/new", year=2025, cites=1, abstract="new result", source="Nature Communications")

    def openalex(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sources"):
            return httpx.Response(200, json={"results": [{"id": "https://openalex.org/S1", "display_name": "Nature Communications", "type": "journal",
                                                          "works_count": 54000, "cited_by_count": 2500000, "summary_stats": {"2yr_mean_citedness": 17.2, "h_index": 480},
                                                          "host_organization_name": "Springer Nature", "is_in_doaj": True}]})
        sort = request.url.params.get("sort", "")
        return httpx.Response(200, json={"results": [cited] if "cited_by_count" in sort else [recent]})

    world.route("openalex", openalex)
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        page = api.get("/discovery/venue-page", params={"id": "nat-commun"}).json()
    assert page["name"] == "Nature Communications" and page["level"] == "core" and page["label"] == "已确认重要期刊"
    assert page["info"]["listed"]["intro"] and "Springer Nature" in page["info"]["summary"] and "h 指数 480" in page["info"]["summary"]
    assert [w["title"] for w in page["works"]["cited"]] == ["A highly cited paper in the journal"]
    assert [w["title"] for w in page["works"]["recent"]] == ["A very recent paper in the journal"]
    assert page["errors"] == []


def test_library_venues_groups_my_papers_by_venue(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE papers SET venue = ?, year = ? WHERE id = ?", ("Nature Communications", 2024, pid))
    got = api.get("/discovery/library-venues").json()
    top = got["items"][0]
    assert top["name"] == "Nature Communications" and top["count"] == 1 and top["id"] == "nat-commun" and top["label"] == "已确认重要期刊"
    assert got["papers"] == 1


def test_iso_abbreviations_and_tnse_match_the_listed_venue_but_never_for_top_confirmation():
    from paperdesk.discovery.venues import match_venue

    for name in ("IEEE Transactions on Network Science and Engineering", "IEEE Trans. Netw. Sci. Eng.", "TNSE"):
        assert match_venue(name).id == "tnse"
    assert match_venue("IEEE Trans. Neural Netw. Learn. Syst.").id == "tnnls"
    assert match_venue("Int. J. Comput. Vis.").id == "ijcv"
    assert match_venue("IEEE Transactions on Network Science") is None  # 少一个词就不是同一个来源
    assert match_venue("Int. J. Comput. Vis.", loose=False) is None  # 随机探索确认顶刊时仍只认完全相等的名称


def test_venue_search_and_page_by_openalex_id_and_abbreviation(tmp_path):
    import httpx

    from f3_helpers import FakeWorld, make_world_api

    world = FakeWorld()
    src = {"id": "https://openalex.org/S4210186438", "display_name": "IEEE Transactions on Network Science and Engineering", "abbreviated_title": "IEEE Trans. Netw. Sci. Eng.",
           "alternate_titles": ["TNSE"], "type": "journal", "works_count": 2800, "cited_by_count": 60000, "summary_stats": {"2yr_mean_citedness": 4.1, "h_index": 90},
           "host_organization_name": "Institute of Electrical and Electronics Engineers"}

    def openalex(request: httpx.Request) -> httpx.Response:
        if "/sources/S4210186438" in request.url.path:
            return httpx.Response(200, json=src)
        if request.url.path.endswith("/sources"):
            return httpx.Response(200, json={"results": [src]})
        return httpx.Response(200, json={"results": []})

    world.route("openalex", openalex)
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        found = api.get("/discovery/venue-search", params={"q": "network science eng"}).json()["items"]
        assert found[0]["name"].endswith("Network Science and Engineering") and found[0]["listed_id"] == "tnse"
        by_abbr = api.get("/discovery/venue-page", params={"name": "IEEE Trans. Netw. Sci. Eng.", "works": "false"}).json()
        assert by_abbr["name"] == "IEEE Transactions on Network Science and Engineering" and by_abbr["id"] == "tnse" and by_abbr["works"]["cited"] == []
        by_id = api.get("/discovery/venue-page", params={"oa": "S4210186438", "works": "false"}).json()
        assert by_id["info"]["stats"]["h_index"] == 90 and by_id["level"] == "core"
