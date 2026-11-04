from __future__ import annotations

import json

import httpx
import pytest

from f3_helpers import FakeWorld, arxiv_feed, make_world_api, oa_work
from paperdesk.discovery import sources as S
from paperdesk.discovery.dedupe import merge_works
from paperdesk.discovery.models import Work, decide_status, norm_arxiv, norm_doi, norm_title, title_similarity
from paperdesk.discovery.planner import rule_plan
from paperdesk.discovery.ranking import (
    diversify, domain_group, impact_score, quality_score, recency_score, rule_reason, score_works, weights_for,
)


# ---------------- 解析与发表状态 ----------------
def test_openalex_published_with_preprint_location_and_normalized_impact():
    d = oa_work(1, "Finding key players", doi="10.1038/s42256-020-0177-2", year=2020, cites=431, fwci=19.1, pct=0.998,
                locations=[{"landing_page_url": "https://arxiv.org/abs/1906.04567", "version": "submittedVersion",
                            "source": {"display_name": "arXiv", "type": "repository"}}],
                abstract="We introduce a deep reinforcement learning framework FINDER")
    w = S.parse_openalex(d)
    assert w.status == "published" and w.venue_type == "journal" and w.peer_reviewed is True and w.core_venue is True
    assert w.arxiv_id == "1906.04567"
    assert w.versions and w.versions[0]["kind"] == "preprint" and w.versions[0]["arxiv_id"] == "1906.04567"
    assert w.fwci == 19.1 and w.percentile == 0.998 and w.field_name == "Computer Science"
    assert w.abstract.startswith("We introduce")  # 倒排索引已还原


def test_status_decisions_are_evidence_based():
    assert decide_status(doi="10.48550/arxiv.2301.00001", work_type="article", venue_type="journal", version=None)[0] == "preprint"
    assert decide_status(doi="10.1101/2020.01.01.123", work_type=None, venue_type=None, version=None)[0] == "preprint"
    assert decide_status(doi="10.1/x", work_type="preprint", venue_type=None, version=None)[0] == "preprint"
    assert decide_status(doi="10.1/x", work_type="article", venue_type="journal", version="publishedVersion")[0] == "published"
    assert decide_status(doi=None, work_type=None, venue_type=None, version=None)[0] == "other"  # 没有信号就不下结论
    assert decide_status(doi="10.1/x", work_type="article", venue_type="journal", version=None, accepted_signal=True)[0] == "accepted"


def test_crossref_preprint_relation_and_accepted_rule():
    pre = S.parse_crossref({"DOI": "10.1101/2021.1", "title": ["A preprint"], "type": "posted-content", "issued": {"date-parts": [[2021, 3]]},
                            "relation": {"is-preprint-of": [{"id-type": "doi", "id": "10.1234/final"}]}})
    assert pre.status == "preprint" and pre.versions == [{"kind": "published", "doi": "10.1234/final", "url": "https://doi.org/10.1234/final",
                                                           "venue": None, "arxiv_id": None}]
    acc = S.parse_crossref({"DOI": "10.1234/inpress", "title": ["In press"], "type": "journal-article", "container-title": ["J. Foo"],
                            "accepted": {"date-parts": [[2025, 1, 1]]}, "issued": {"date-parts": [[2025]]}})
    assert acc.status == "accepted"
    done = S.parse_crossref({"DOI": "10.1234/done", "title": ["Done"], "type": "journal-article", "container-title": ["J. Foo"],
                             "volume": "3", "page": "1-9", "accepted": {"date-parts": [[2025, 1, 1]]}, "issued": {"date-parts": [[2025]]}})
    assert done.status == "published"  # 已有卷期页码，不是“待出版”


def test_arxiv_feed_journal_ref_and_accepted_comment():
    xml = arxiv_feed([
        {"id": "2301.00001", "title": "Plain   preprint"},
        {"id": "2301.00002", "title": "Has journal ref", "jref": "Phys. Rev. E 108, 044301", "doi": "10.1103/PhysRevE.108.044301"},
        {"id": "2301.00003", "title": "Accepted one", "comment": "Accepted to NeurIPS 2024"},
    ])
    a, b, c = S.parse_arxiv_feed(xml)
    assert a.title == "Plain preprint" and a.status == "preprint" and a.arxiv_id == "2301.00001"
    assert b.status == "preprint" and b.versions[0]["kind"] == "published" and "可能已正式发表" in b.status_evidence
    assert c.status == "accepted" and "NeurIPS" in c.status_evidence


def test_other_sources_parse():
    e = S.parse_epmc({"id": "PPR1", "source": "PPR", "title": "A bio preprint", "authorString": "Smith J, Doe A.", "pubYear": "2023",
                      "doi": "10.1101/xyz", "abstractText": "<h4>Abstract</h4>Cells.", "isOpenAccess": "Y"})
    assert e.status == "preprint" and e.authors == ["Smith J", "Doe A"] and e.open_access and e.abstract == "Cells."
    s = S.parse_s2({"paperId": "p1", "title": "S2 paper", "year": 2021, "venue": "arXiv.org", "externalIds": {"ArXiv": "2101.00001"},
                    "authors": [{"name": "A B"}], "citationCount": 5, "publicationTypes": ["JournalArticle"]})
    assert s.status == "preprint" and s.arxiv_id == "2101.00001"


def test_identifier_normalizers():
    assert norm_doi("https://doi.org/10.1038/ABC.1.") == "10.1038/abc.1"
    assert norm_arxiv("https://arxiv.org/abs/2105.12345v3") == "2105.12345"
    assert norm_arxiv("https://x.org/biblio/1965232") is None and norm_arxiv("article/2826341") is None
    assert title_similarity("Dual Attention Routing", "dual attention routing!") == 1.0
    assert norm_title("Résumé: A Study") == "resumeastudy"


# ---------------- 去重与版本关联 ----------------
def _w(title, doi=None, arxiv=None, status="other", authors=("Alice Zhang",), year=2023, **kw):
    return Work(title=title, doi=doi, arxiv_id=arxiv, status=status, authors=list(authors), year=year, **kw)


def test_dedupe_links_preprint_and_published_by_doi_and_title():
    published = _w("Dual Attention Routing for Cascade Recovery", doi="10.1/pub", status="published", venue="J. Net",
                   sources=[{"name": "Crossref"}], citations=5)
    preprint = _w("Dual attention routing for cascade recovery", doi="10.48550/arxiv.2301.00001", arxiv="2301.00001", status="preprint",
                  sources=[{"name": "arXiv"}], abstract="An abstract.", url="https://arxiv.org/abs/2301.00001")
    other = _w("Totally different paper about proteins", doi="10.9/other", status="published", authors=("Zed",))
    merged = merge_works([preprint, other, published])
    assert len(merged) == 2
    m = next(w for w in merged if "Dual" in w.title)
    assert m.status == "published" and m.doi == "10.1/pub"  # 以正式发表版本为主
    assert m.abstract == "An abstract." and m.arxiv_id == "2301.00001"  # 缺失字段由预印本补全
    assert {s["name"] for s in m.sources} == {"Crossref", "arXiv"}
    assert any(v["kind"] == "preprint" and v["arxiv_id"] == "2301.00001" for v in m.versions)
    assert "另有预印本版本" in m.status_evidence


def test_dedupe_does_not_merge_series_parts_or_different_authors():
    a = _w("Cascading failures in networks Part I", authors=("Li",), year=2020)
    b = _w("Cascading failures in networks Part II", authors=("Wang",), year=2021)
    assert len(merge_works([a, b])) == 2
    c = _w("Graph neural networks for routing", authors=("Smith",), year=2020)
    d = _w("Graph neural networks for routing", authors=("Smith",), year=2021)
    assert len(merge_works([c, d])) == 1  # 同标题同第一作者、年份相近：同一篇


def test_dedupe_uses_source_declared_version_links():
    pre = _w("Early title wording differs", doi="10.1101/pre", status="preprint", authors=("A",),
             versions=[{"kind": "published", "doi": "10.5/final", "url": None, "venue": None, "arxiv_id": None}])
    fin = _w("A completely reworded final title", doi="10.5/final", status="published", authors=("A",))
    merged = merge_works([pre, fin])
    assert len(merged) == 1 and merged[0].status == "published"


# ---------------- 排序 ----------------
def test_ranking_is_not_citation_count_ordering():
    relevant = _w("Cascading failure recovery with reinforcement learning", status="published", venue_type="journal", core_venue=True,
                  peer_reviewed=True, abstract="recovery of cascading failures using reinforcement learning", citations=12, percentile=0.8,
                  field_name="Computer Science", source_rank={"OpenAlex": 0})
    famous_irrelevant = _w("A famous paper about something else entirely", status="published", venue_type="journal", core_venue=True,
                           peer_reviewed=True, abstract="unrelated topic", citations=50000, percentile=0.999, field_name="Computer Science",
                           source_rank={"OpenAlex": 25})
    score_works([famous_irrelevant, relevant], query="cascading failure recovery reinforcement learning", now_year=2025)
    assert relevant.score > famous_irrelevant.score
    assert "相关性" in relevant.rank_note and "影响力" in relevant.rank_note


def test_quality_impact_recency_components():
    pub = _w("x", status="published", venue_type="journal", core_venue=True, peer_reviewed=True)
    pre = _w("x", status="preprint")
    assert quality_score(pub) > quality_score(_w("x", status="accepted")) > quality_score(pre) > 0
    assert impact_score(_w("x", percentile=0.995), 2025)[1] == "同领域同年份前 1%"
    assert impact_score(_w("x", fwci=3.0), 2025)[0] > impact_score(_w("x", fwci=0.3), 2025)[0]
    raw = impact_score(_w("x", citations=500, year=2024), 2025)[0]
    assert raw < 0.7  # 只有原始引用数时打折，不当作归一化指标
    assert recency_score(_w("x", year=2025), 2025, "cs") > recency_score(_w("x", year=2015), 2025, "cs")
    # 数学/物理的半衰期更长：同样 10 年前的论文衰减更慢
    assert recency_score(_w("x", year=2015), 2025, "phys") > recency_score(_w("x", year=2015), 2025, "cs")


def test_domain_weights_and_mode_tilt():
    assert domain_group(_w("x", field_name="Medicine")) == "bio" and domain_group(_w("x", domain="Physical Sciences", field_name="Physics and Astronomy")) == "phys"
    base = weights_for("cs", "comprehensive")
    recent = weights_for("cs", "recent")
    impact = weights_for("cs", "impact")
    assert abs(sum(base) - 1) < 1e-9 and abs(sum(impact) - 1) < 1e-9
    assert recent[3] > base[3] and impact[2] > base[2]  # 最新研究加重时效，高影响力加重影响力
    assert weights_for("bio", "comprehensive")[1] > weights_for("cs", "comprehensive")[1]  # 医学更看重质量


def test_diversify_penalizes_repeated_fields():
    ws = [_w(f"p{i}", field_name="Computer Science", score=1.0 - i * 0.01) if i < 4 else _w("other", field_name="Biology", score=0.9) for i in range(5)]
    chosen = diversify(ws, 3)
    assert {w.field_name for w in chosen} == {"Computer Science", "Biology"}


def test_rule_reason_states_only_checkable_facts():
    w = _w("x", status="published", venue="Nature", core_venue=True, percentile=0.97, year=2020, relevance_note="cascading、recovery")
    r = rule_reason(w, "q")
    assert "Nature" in r and "前 10%" in r and "cascading" in r
    assert "兴趣" not in r


def test_rule_plan_flags_untranslated_cjk():
    p = rule_plan("级联失效恢复", "comprehensive")
    assert p.english_query == "级联失效恢复" and p.notes and not p.ai_used
    q = rule_plan("cascading failure recovery deep learning", "comprehensive")
    assert q.is_cs and q.keywords[:2] == ["cascading", "failure"]


# ---------------- 端到端检索（模拟网络） ----------------
def _world_with_sources(*, crossref_ok=True, s2_status=429, dblp_bot=True) -> FakeWorld:
    world = FakeWorld()
    published = oa_work(1, "Dual attention routing for cascading failure recovery", doi="10.1/dar", year=2024, cites=40, fwci=4.2, pct=0.96,
                        abstract="We recover networks after cascading failure with dual attention routing and reinforcement learning",
                        locations=[{"landing_page_url": "https://arxiv.org/abs/2301.00001", "version": "submittedVersion",
                                    "source": {"display_name": "arXiv", "type": "repository"}}])
    famous = oa_work(2, "A famous unrelated paper on protein folding", doi="10.1/fam", year=2018, cites=90000, fwci=80, pct=0.9999,
                     abstract="protein structure prediction")
    pre = oa_work(3, "Self-healing recovery from cascading failure via deep RL", doi="10.48550/arxiv.2402.00009", year=2025, cites=1, fwci=None, pct=None,
                  wtype="preprint", source="arXiv", stype="repository", core=False, abstract="cascading failure recovery deep reinforcement learning preprint",
                  version="submittedVersion")
    world.route("openalex", lambda r: httpx.Response(200, json={"results": [published, famous, pre]}))
    world.route("crossref", (lambda r: httpx.Response(200, json={"message": {"items": [
        {"DOI": "10.1/dar", "title": ["Dual attention routing for cascading failure recovery"], "type": "journal-article",
         "container-title": ["Journal of Networks"], "author": [{"given": "Alice", "family": "Zhang"}], "issued": {"date-parts": [[2024]]},
         "is-referenced-by-count": 41, "volume": "9", "page": "1-12"}]}})) if crossref_ok else (lambda r: httpx.Response(400, text="bad")))
    world.route("semanticscholar", lambda r: httpx.Response(s2_status, json={"data": []}))
    world.route("arxiv.org", lambda r: httpx.Response(200, text=arxiv_feed([
        {"id": "2301.00001", "title": "Dual attention routing for cascading failure recovery", "doi": "10.1/dar",
         "summary": "Preprint of the dual attention routing paper."}])))
    world.route("dblp", lambda r: httpx.Response(202, text="<html>Making sure you're not a bot</html>", headers={"content-type": "text/html"}) if dblp_bot
                else httpx.Response(200, json={"result": {"hits": {"hit": []}}}))
    return world


@pytest.fixture
def world_api(tmp_path):
    world = _world_with_sources()
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        yield api, world


def test_search_dedupes_links_versions_and_degrades_gracefully(world_api):
    api, world = world_api
    r = api.post("/discovery/search", {"query": "cascading failure recovery reinforcement learning", "ai": False, "limit": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    by = {w["doi"]: w for w in d["results"]}
    # 三个来源给出的“同一篇”被合并：OpenAlex 记录 + Crossref 记录 + arXiv 预印本
    dar = by["10.1/dar"]
    assert dar["status"] == "published" and dar["status_label"] == "正式发表"
    assert {s["name"] for s in dar["sources"]} >= {"OpenAlex", "Crossref", "arXiv"}
    assert any(v["kind"] == "preprint" and v["arxiv_id"] == "2301.00001" for v in dar["versions"])
    assert d["results"][0]["doi"] == "10.1/dar"  # 相关 + 高质量排在前
    # 引用 9 万但完全不相关的论文不能因为引用多而排第一
    assert [w["doi"] for w in d["results"]].index("10.1/fam") > 0
    pre = by["10.48550/arxiv.2402.00009"]
    assert pre["status"] == "preprint" and pre["breakdown"]["quality"] < dar["breakdown"]["quality"]
    # 来源状态与降级提示
    reps = {s["name"]: s for s in d["sources"]}
    assert reps["OpenAlex"]["ok"] and reps["Crossref"]["ok"] and reps["arXiv"]["ok"]
    assert not reps["Semantic Scholar"]["ok"] and "限流" in reps["Semantic Scholar"]["error"]
    assert "DBLP" not in reps  # DBLP 要求人机验证，已不再作为检索来源
    assert any("Semantic Scholar" in w for w in d["warnings"])
    assert d["total_candidates"] > d["after_dedupe"]


def test_search_all_sources_down_is_a_clear_error(tmp_path):
    world = FakeWorld()
    world.route("", lambda r: httpx.Response(500, text="down"))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        r = api.post("/discovery/search", {"query": "anything about graphs", "ai": False})
        assert r.status_code == 502 and r.json()["error"]["code"] == "network"
        assert api.post("/discovery/search", {"query": "x", "mode": "bogus"}).status_code == 400
        assert api.post("/discovery/search", {"query": ""}).status_code == 400


def test_search_modes_really_change_queries_and_ranking(world_api):
    api, world = world_api
    api.post("/discovery/search", {"query": "cascading failure recovery", "mode": "recent", "ai": False})
    oa = [r for r in world.requests if r.url.host == "api.openalex.org"]
    assert any(r.url.params.get("sort") == "publication_date:desc" for r in oa)
    assert any("sort" not in r.url.params for r in oa)  # 同时取一份按相关性的，避免只有“最新但无关”
    assert all("from_publication_date" in r.url.params["filter"] for r in oa)
    cr = [r for r in world.requests if r.url.host == "api.crossref.org"]
    assert any(r.url.params.get("sort") == "published" for r in cr) and all("from-pub-date" in r.url.params["filter"] for r in cr)
    api.post("/discovery/search", {"query": "cascading failure recovery", "mode": "impact", "ai": False})
    reqs = [r for r in world.requests if r.url.host == "api.openalex.org"][-2:]
    assert any(r.url.params.get("sort") == "cited_by_count:desc" for r in reqs)
    # 影响力模式下过滤掉“被引很多但与问题无关”的候选
    res = api.post("/discovery/search", {"query": "cascading failure recovery", "mode": "impact", "ai": False}).json()["results"]
    assert "10.1/fam" not in [w["doi"] for w in res]


def test_cross_discipline_mode_excludes_dominant_field(world_api):
    api, world = world_api
    d = api.post("/discovery/search", {"query": "cascading failure recovery", "mode": "cross", "ai": False}).json()
    flt = [r.url.params.get("filter", "") for r in world.requests if r.url.host == "api.openalex.org"]
    assert any("primary_topic.field.id:!fields/17" in f for f in flt)
    # 没有 AI 提供类比概念时如实说明，而不是假装做了跨学科类比
    assert any("需要 AI 提出其它学科的类比概念" in w for w in d["warnings"])


def test_recent_mode_does_not_surface_new_but_unrelated_papers(tmp_path):
    world = FakeWorld()
    on_topic = oa_work(1, "Cascading failure recovery with deep reinforcement learning", doi="10.1/on", year=2025, cites=1,
                       abstract="cascading failure recovery in networks with deep reinforcement learning")
    off_topic = oa_work(2, "Closed-loop cognitive state control for neuroergonomics", doi="10.1/off", year=2026, cites=0,
                        abstract="a hypothesis framework for real-time estimation of cognitive state with learning")
    world.route("openalex", lambda r: httpx.Response(200, json={"results": [on_topic, off_topic]}))
    world.route("crossref", lambda r: httpx.Response(200, json={"message": {"items": []}}))
    world.route("semanticscholar", lambda r: httpx.Response(429, json={}))
    world.route("arxiv.org", lambda r: httpx.Response(200, text=arxiv_feed([])))
    world.route("dblp", lambda r: httpx.Response(200, json={"result": {"hits": {"hit": []}}}))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        res = api.post("/discovery/search", {"query": "cascading failure recovery deep reinforcement learning", "mode": "recent", "ai": False}).json()["results"]
        assert [w["doi"] for w in res] == ["10.1/on"]  # 更新但无关的论文被相关性闸门过滤


def test_related_mode_without_query_has_no_translation_warning(tmp_path):
    world = FakeWorld()
    seed = oa_work(10, "Seed paper on key players", doi="10.1/seed", abstract="key players networks")
    world.route("openalex", lambda r: httpx.Response(200, json=seed) if "/works/https" in str(r.url) else httpx.Response(200, json={"results": []}))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/search", {"query": "", "mode": "related", "seed": {"doi": "10.1/seed"}, "ai": False}).json()
        assert not any("检索词不含英文" in w for w in d["warnings"])


def test_related_mode_uses_references_citations_and_marks_relation(tmp_path):
    world = FakeWorld()
    seed = oa_work(10, "Seed paper on key players", doi="10.1/seed", refs=["https://openalex.org/W11"], abstract="key players networks")
    seed["related_works"] = ["https://openalex.org/W12"]
    ref = oa_work(11, "A referenced foundation", doi="10.1/ref", abstract="foundation of key players")
    rel = oa_work(12, "A related work", doi="10.1/rel", abstract="related key players study")
    citing = oa_work(13, "A citing follow-up", doi="10.1/cit", abstract="follow-up on key players")

    def oa(request):
        p = request.url.params
        path = request.url.path
        if "/works/https://doi.org/" in str(request.url) or path.startswith("/works/https"):
            return httpx.Response(200, json=seed)
        flt = p.get("filter", "")
        if flt.startswith("openalex:"):
            return httpx.Response(200, json={"results": [ref, rel]})
        if flt.startswith("cites:"):
            return httpx.Response(200, json={"results": [citing]})
        return httpx.Response(200, json={"results": [seed]})

    world.route("openalex", oa)
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/search", {"query": "", "mode": "related", "seed": {"doi": "10.1/seed"}, "ai": False}).json()
        notes = {w["doi"]: w["relation"] for w in d["results"]}
        assert notes == {"10.1/ref": "被该论文引用", "10.1/rel": "OpenAlex 判定的相关工作", "10.1/cit": "引用了该论文"}
        assert d["seed"]["doi"] == "10.1/seed"
        assert all(w["reason"].startswith(("被该论文引用", "OpenAlex 判定的相关工作", "引用了该论文")) for w in d["results"])


def test_results_are_marked_when_already_in_library(world_api, sample_pdf):
    api, world = world_api
    api.import_pdf(sample_pdf)  # DOI 10.1234/dar.2023.001
    world.routes["openalex"] = lambda r: httpx.Response(200, json={"results": [
        oa_work(1, "Dual Attention Routing for Robust Cascade Recovery", doi="10.1234/dar.2023.001", abstract="dual attention")]})
    d = api.post("/discovery/search", {"query": "dual attention routing", "ai": False}).json()
    hit = next(w for w in d["results"] if w["doi"] == "10.1234/dar.2023.001")
    assert hit["in_library"]


# ---------------- AI：规划 / 嵌入 / 重排 / 理由 ----------------
def _ai_handlers(world: FakeWorld):
    def handler(system: str, user: str, body: dict):
        if "检索规划器" in system:
            return json.dumps({"english_query": "cascading failure recovery reinforcement learning", "alt_queries": ["network restoration deep rl"],
                               "keywords": ["cascading failure", "recovery"], "domains": ["computer science"], "is_cs": True,
                               "is_biomedical": False, "analogies": ["epidemic containment"]})
        if "推荐理由" in system:
            n = user.count("\n[")
            items = [{"i": i, "rel": 9 if i == 0 else 3, "reason": f"摘要说明该文用双重注意力恢复级联失效（候选 {i}）"} for i in range(n + 1)]
            items[1:2] = [{"i": 1, "rel": 2, "reason": "与你的兴趣相关"}] if n >= 1 else []  # 空话理由必须被丢弃
            return json.dumps({"results": items})
        return None
    world.llm_handlers.append(handler)


def test_ai_search_plans_reranks_and_gives_grounded_reasons(tmp_path):
    world = _world_with_sources()
    _ai_handlers(world)
    api = make_world_api(tmp_path, world)
    with api.c:
        d = api.post("/discovery/search", {"query": "我想找研究级联失效恢复的强化学习论文", "limit": 10}).json()
        assert d["plan"]["ai_used"] and d["plan"]["english_query"].startswith("cascading failure")
        assert d["ai"] == {"planner": True, "embeddings": True, "rerank": True}
        assert not any("没有可用的模型" in w for w in d["warnings"])  # AI 规划成功后，规则回退的提示不再出现
        top = d["results"][0]
        assert top["reason_source"] == "ai" and "双重注意力" in top["reason"]
        # “与你的兴趣相关”这类空话被丢弃，改用可核对事实的规则理由
        assert all("兴趣" not in w["reason"] for w in d["results"])
        # 发给云端的内容被记录（隐私）
        priv = api.get("/privacy/requests").json()["items"]
        assert any(p["task_type"] == "discovery_rerank" and "公开元数据" in p["summary"] for p in priv)
        assert any(p["task_type"] == "discovery_plan" for p in priv)
        usage = {t["task_type"] for t in api.get("/usage").json()["by_task_type"]}
        assert {"discovery_plan", "discovery_rerank"} <= usage


def test_ai_search_falls_back_when_embeddings_and_llm_fail(tmp_path):
    world = _world_with_sources()
    world.embed_enabled = False
    world.llm_handlers.append(lambda s, u, b: "this is not json at all")
    api = make_world_api(tmp_path, world)
    with api.c:
        d = api.post("/discovery/search", {"query": "cascading failure recovery"}).json()
        assert d["results"] and d["ai"]["embeddings"] is False and d["ai"]["rerank"] is False
        assert any("向量嵌入不可用" in w for w in d["warnings"])
        assert all(w["reason_source"] == "rule" for w in d["results"])


def test_embeddings_are_cached_in_the_database(tmp_path):
    world = _world_with_sources()
    api = make_world_api(tmp_path, world)
    with api.c:
        api.post("/discovery/search", {"query": "cascading failure recovery", "ai": True})
        n1 = sum(1 for r in world.requests if r.url.path.endswith("/embeddings"))
        api.post("/discovery/search", {"query": "cascading failure recovery", "ai": True})
        n2 = sum(1 for r in world.requests if r.url.path.endswith("/embeddings"))
        assert n1 >= 1 and n2 == n1  # 相同文本第二次不再请求


def test_series_titles_differing_by_a_digit_are_not_merged():
    a = _w("Sampled study number 1", authors=("Alice Zhang",), year=2022)
    b = _w("Sampled study number 9", authors=("Alice Zhang",), year=2022)
    assert len(merge_works([a, b])) == 2
    c = _w("Cascading failures in networks Part I", authors=("Li",), year=2020)
    d = _w("Cascading failures in networks Part II", authors=("Li",), year=2020)
    assert len(merge_works([c, d])) == 2


def test_relevance_floor_never_returns_empty_when_there_are_candidates(tmp_path):
    """闸门把候选全部过滤掉时，退回按综合评分的前几条并写明原因，而不是返回空。"""
    world = FakeWorld()
    off_topic = oa_work(2, "Closed-loop cognitive state control for neuroergonomics", doi="10.1/off", year=2026, cites=0,
                        abstract="a hypothesis framework for real-time estimation of cognitive state with learning")
    world.route("openalex", lambda r: httpx.Response(200, json={"results": [off_topic]}))
    world.route("crossref", lambda r: httpx.Response(200, json={"message": {"items": []}}))
    world.route("semanticscholar", lambda r: httpx.Response(429, json={}))
    world.route("arxiv.org", lambda r: httpx.Response(200, text=arxiv_feed([])))
    world.route("dblp", lambda r: httpx.Response(200, json={"result": {"hits": {"hit": []}}}))
    api = make_world_api(tmp_path, world, configure=False)
    with api.c:
        d = api.post("/discovery/search", {"query": "cascading failure recovery deep reinforcement learning", "mode": "recent", "ai": False}).json()
        assert [w["doi"] for w in d["results"]] == ["10.1/off"]
        assert any("没有候选通过相关性筛选" in w for w in d["warnings"])


def test_search_with_several_sources_returns_a_sensible_number_of_results(world_api):
    api, _world = world_api
    d = api.post("/discovery/search", {"query": "deep reinforcement learning key players complex networks", "mode": "comprehensive",
                                       "limit": 10, "ai": False}).json()
    # 响应里的结果字段叫 results（不是 items）：多个来源都有结果时不能为空
    assert "items" not in d and len(d["results"]) >= 2 and d["after_dedupe"] >= len(d["results"])
    assert not any("没有候选通过相关性筛选" in w for w in d["warnings"])


def test_merge_prefers_the_untruncated_abstract():
    """Semantic Scholar 常给被截断的摘要（以省略号结尾）；合并时要用别的来源的完整版。"""
    from paperdesk.discovery import dedupe
    from paperdesk.discovery.models import Work

    cut = "The emergence of agents is a milestone. This paper, drawing on multi-..."
    full = "The emergence of agents is a milestone. This paper, drawing on multi-expert analysis, reviews the field in depth."
    a = Work(title="AI Agents and Agentic Systems", doi="10.1/x", abstract=cut, citations=500, sources=[{"name": "semanticscholar"}])
    b = Work(title="AI Agents and Agentic Systems", doi="10.1/x", abstract=full, citations=100, sources=[{"name": "openalex"}])
    merged = dedupe._merge_cluster([a, b])
    assert merged.abstract == full
    assert dedupe.is_truncated(cut) and not dedupe.is_truncated(full)
    assert dedupe.best_abstract([a]) == cut  # 只有被截断的版本时，仍然用它


def test_direction_recommendation_adds_a_verified_learning_roadmap(api, monkeypatch):
    from paperdesk.discovery import roadmap
    from paperdesk.discovery.models import Work

    real = {
        "a survey of test methods": Work(title="A Survey of Test Methods", year=2020, citations=900, openalex_id="https://openalex.org/W1", doi="10.1/survey"),
        "the original test method": Work(title="The Original Test Method", year=2015, citations=12000, openalex_id="https://openalex.org/W2", doi="10.1/orig"),
    }

    async def fake_openalex(client, query, **kw):
        w = real.get((query or "").strip().lower())
        return [w] if w else []

    monkeypatch.setattr(roadmap.S, "fetch_openalex", fake_openalex)
    api.put("/settings/ai/secret", {"api_key": "k"})
    d = api.post("/discovery/recommend/direction", {"topic": "test methods", "ai": True}).json()
    road = [g for g in d["groups"] if g["id"].startswith("road")]
    assert [g["title"] for g in road] == ["1. 入门", "2. 奠基"]  # 按 AI 给的先后顺序、由浅入深
    titles = [w["title"] for g in road for w in g["items"]]
    assert titles == ["A Survey of Test Methods", "The Original Test Method"]  # 编造的那篇核对不上，被丢掉
    assert road[1]["items"][0]["citations"] == 12000 and road[1]["items"][0]["reason"] == "提出了核心方法"
    assert any("核对不上" in w for w in d["warnings"])
    assert d["groups"][0]["id"].startswith("road")  # 路线放在最前面
