from __future__ import annotations

import httpx

from paperdesk.references import citations as cit
from paperdesk.references import parse, resolve

# ---------------------------------------------------------------- 解析（纯函数，手写样本）


def _ps(texts, page=1, start=100):
    return [{"ord": start + i, "page": page, "text": t, "bbox": [50, 100 + 12 * i, 280, 110 + 12 * i]} for i, t in enumerate(texts)]


NUMERIC = [
    "1. Rivera, A. & Chen, L. Cascade failure in layered networks. Phys. Rev. E 80, 41 (2009).",
    "2. Okafor, N., Larsen, K. & Mori, S. Learning to dismantle graphs. In Proceedings of the 30th",
    "International Conference on Graph Learning 11–19 (ACM, 2018).",
    "3. Novak, P. et al. Sparse recovery on networks. Preprint at https://arxiv.org/abs/1903.01234 (2019).",
    "The authors declare no competing interests.",
    "4. Haddad, R. Resilient design. Nat. Commun. 12, 345 (2021). https://doi.org/10.1038/S41467-021-00001-X.",
    "5. Barabási, A.-L.Network Science (Cambridge Univ. Press, 2016).",
]


def test_numeric_references_are_split_and_fields_are_conservative():
    style, es = parse.split_entries(_ps(NUMERIC))
    assert style == "numeric" and [e.index for e in es] == [1, 2, 3, 4, 5]
    e1, e2, e3, e4, e5 = es
    assert e1.authors == ["Rivera, A.", "Chen, L."] and e1.title == "Cascade failure in layered networks" and e1.year == 2009
    assert e1.venue == "Phys. Rev. E 80, 41"
    assert e2.title == "Learning to dismantle graphs" and e2.year == 2018 and len(e2.ords) == 2  # 被拆成两段的条目拼回一条
    assert e3.arxiv == "1903.01234" and e3.authors == ["Novak, P.", "et al."] and e3.url.endswith("1903.01234")
    assert e4.doi == "10.1038/s41467-021-00001-x" and e4.url == "https://doi.org/10.1038/s41467-021-00001-x"
    assert e5.title.startswith("Network Science (Cambridge Univ. Press, 2016)")  # 括号里的句点不当作标题结束
    assert all("competing interests" not in e.raw for e in es)  # 声明文字不属于任何参考文献


def test_unparseable_fields_stay_empty_instead_of_guessed():
    _, es = parse.split_entries(_ps(["1. See the supplementary methods for details about the procedure that was used.", "2. Foo."]))
    assert es[0].title == "" and es[0].authors == [] and es[0].doi == "" and es[0].year is None
    assert es[0].raw.startswith("See the supplementary")


def test_interleaved_columns_are_flagged_instead_of_trusted():
    """一行跨了两栏（段落特别宽）：条目标 merged，抽出的标题带页码区间时标 messy 且不报告标题。"""
    ps = _ps(["1. Rivera, A. & Chen, L. Cascade failure in layered networks. Phys. Rev. E 80, 41 (2009).",
              "2. Lund, E. & Park, J. Poker:1024–1034 (NIPS, 2017). Another title.Science 359, 418 (2018)."])
    ps[1]["bbox"] = [50, 120, 560, 130]
    _, es = parse.split_entries(ps, {1: 595.0})
    assert es[1].merged and es[1].messy and es[1].title == ""
    assert not es[0].merged and es[0].title


def test_author_year_style():
    texts = [
        "Rivera, A. & Chen, L. (2009). Cascade failure in layered networks. Physical Review E, 80, 41.",
        "Okafor, N., Larsen, K. & Mori, S. (2018). Learning to dismantle graphs. Graph Learning Conference, 11–19.",
        "Novak, P. (2019). Sparse recovery on networks. arXiv:1903.01234",
    ]
    style, es = parse.split_entries(_ps(texts))
    assert style == "author-year" and [e.index for e in es] == [1, 2, 3]
    assert es[0].year == 2009 and es[0].title == "Cascade failure in layered networks"
    assert es[1].authors[0] == "Okafor, N." and es[2].arxiv == "1903.01234"


# ---------------------------------------------------------------- 引用标记


def _body(texts):
    return [{"ord": i + 1, "kind": "paragraph", "text": t} for i, t in enumerate(texts)]


def test_numeric_markers_ranges_and_negative_cases():
    entries = [{"index": i, "year": 2000 + i, "authors": [f"Auth{i}, A."]} for i in range(1, 8)]
    body = _body([
        "Cascades are common1,2 in grids [3] and also in other systems (see refs 4, 6–7).",
        "A common pattern appears everywhere. Fig. 3 shows log2 scaling; Table S1 lists cascades9 and S2 and k=5.",
        "The common7 case is rare. The range [2–4] is fine but [9] does not exist and x[2] is code.",
    ])
    items = {i["ord"]: i["markers"] for i in cit.detect_all(body, entries, "numeric")}
    m1 = {(m["kind"], m["text"]): m["refs"] for m in items[1]}
    assert m1[("sup", "1,2")] == [1, 2] and m1[("bracket", "[3]")] == [3] and m1[("ref", "4, 6–7")] == [4, 6, 7]
    assert 2 not in items  # Fig. 3 / log2 / Table S1 / cascades9（编号不存在）/ k=5 都不是引用
    m3 = {(m["kind"], m["text"]): m["refs"] for m in items[3]}
    assert m3[("sup", "7")] == [7] and m3[("bracket", "[2–4]")] == [2, 3, 4]
    assert ("bracket", "[9]") not in m3 and not any(m["text"] == "x[2]" for m in items[3])
    assert all(m["start"] >= 0 and items[1] and True for m in items[1])


def test_marker_offsets_point_at_the_exact_text():
    entries = [{"index": 3, "year": 2003, "authors": ["Auth, A."]}]
    text = "As shown before [3], the effect is clear."
    (item,) = cit.detect_all(_body([text]), entries, "numeric")
    mk = item["markers"][0]
    assert text[mk["start"]:mk["end"]] == mk["text"] == "[3]"


def test_author_year_markers_need_a_unique_match():
    entries = [
        {"index": 1, "year": 2009, "authors": ["Rivera, A.", "Chen, L."]},
        {"index": 2, "year": 2018, "authors": ["Okafor, N.", "Larsen, K."]},
        {"index": 3, "year": 2018, "authors": ["Okafor, N."]},  # 同一第一作者、同一年份：不猜
        {"index": 4, "year": 2019, "authors": ["Novak, P."]},
    ]
    body = _body([
        "Failures spread quickly (Rivera and Chen, 2009; Novak, 2019) and Rivera et al. (2009) agree. Okafor et al. (2018) differ.",
        "Unknown work (Smith, 2001) is not in the list.",
    ])
    items = {i["ord"]: i["markers"] for i in cit.detect_all(body, entries, "author-year")}
    refs = sorted((m["text"], tuple(m["refs"])) for m in items[1])
    assert ("Rivera and Chen, 2009", (1,)) in refs and ("Novak, 2019", (4,)) in refs and ("Rivera et al. (2009)", (1,)) in refs
    assert not any(m["refs"] in ([2], [3]) for m in items[1])  # 姓氏+年份对应多条，不猜
    assert 2 not in items


# ---------------------------------------------------------------- 解析匹配（假的 OpenAlex / Crossref）


def test_title_similarity_and_abstract_reconstruction():
    assert resolve.title_similarity("Cascade failure in layered networks", "Cascade Failure in Layered Networks.") > 0.99
    assert resolve.title_similarity("Cascade failure", "Quantum dots for solar cells") < 0.4
    inv = {"Cascades": [0], "spread": [1, 4], "quickly": [2], "and": [3], "widely": [5]}
    assert resolve.abstract_from_inverted(inv) == "Cascades spread quickly and spread widely"


def _setup(api, pid):
    """给论文造一组正文段落和参考文献段落（先清掉样本里原有的参考文献）。"""
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM passages WHERE paper_id = ? AND kind = 'reference'", (pid,))
        top = conn.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM passages WHERE paper_id = ?", (pid,)).fetchone()[0]
        rows = [
            ("paragraph", "Cascading failures are common phenomena. Failures spread across layered networks1 and also [2] in other systems."),
            ("paragraph", "Common practice follows earlier work (Rivera and Chen, 2009). The method of Okafor et al. (2018) is related."),
        ] + [("reference", t) for t in (
            "1. Rivera, A. & Chen, L. Cascade failure in layered networks. Phys. Rev. E 80, 41 (2009).",
            "2. Okafor, N., Larsen, K. & Mori, S. Learning to dismantle graphs. In Proceedings of the 30th International Conference on Graph Learning 11–19 (ACM, 2018).",
            "3. Zhou, Q. A thing nobody indexed. Unknown J. 1, 1 (2005).",
        )]
        for i, (kind, text) in enumerate(rows):
            conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,50,50,300,60)",
                         (pid, top + i, 1, kind, text))
    return top


def _net(api, openalex=None, crossref=None, s2=None, log=None):
    fake = api.fake

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if log is not None:
            log.append(str(req.url))
        if host == "api.openalex.org":
            return httpx.Response(200, json=openalex(req) if openalex else {"results": []})
        if host == "api.crossref.org":
            return httpx.Response(200, json=crossref(req) if crossref else {"message": {"items": []}})
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=s2(req) if s2 else {})
        return fake.handler(req)

    api.ctx.async_transport = httpx.MockTransport(handler)


def _oa_work(title, year, doi, abstract_words=None, author="Alex Rivera"):
    inv = {}
    for i, w in enumerate(abstract_words or []):
        inv.setdefault(w, []).append(i)
    return {"id": "https://openalex.org/W1", "doi": f"https://doi.org/{doi}", "title": title, "publication_year": year,
            "authorships": [{"author": {"display_name": author}}], "primary_location": {"source": {"display_name": "Phys. Rev. E"}},
            "abstract_inverted_index": inv or None, "cited_by_count": 12, "best_oa_location": {"pdf_url": "https://example.org/a.pdf"}}


def test_references_and_citations_endpoints(imported):
    api, pid = imported
    _setup(api, pid)
    refs = api.get(f"/papers/{pid}/references").json()
    assert refs["style"] == "numeric" and refs["count"] == 3
    r1 = refs["items"][0]
    assert r1["index"] == 1 and r1["title"] == "Cascade failure in layered networks" and r1["year"] == 2009 and r1["ord"] > 0
    cits = api.get(f"/papers/{pid}/citations").json()
    kinds = {(m["kind"], m["text"]): m["refs"] for it in cits["items"] for m in it["markers"]}
    assert kinds[("sup", "1")] == [1] and kinds[("bracket", "[2]")] == [2]
    assert kinds[("authoryear", "Rivera and Chen, 2009")] == [1] and kinds[("authoryear", "Okafor et al. (2018)")] == [2]
    ctxs = api.get(f"/papers/{pid}/references/1/contexts").json()["items"]
    assert ctxs and "layered networks" in ctxs[0]["sentence"] and ctxs[0]["page"] == 1
    assert api.get(f"/papers/{pid}/references/99/contexts").status_code == 404


def test_resolve_by_title_matches_caches_and_reports_in_library(imported):
    api, pid = imported
    _setup(api, pid)
    log: list[str] = []
    _net(api, openalex=lambda r: {"results": [
        _oa_work("Cascade Failure in Layered Networks", 2009, "10.1103/physreve.80.041", ["Cascades", "spread", "quickly", "in", "layers"])]}, log=log)
    res = api.post(f"/papers/{pid}/references/1/resolve", {}).json()
    assert res["found"] and res["match"]["level"] == "high" and res["abstract"] == "Cascades spread quickly in layers"
    assert res["abstract_source"] == "openalex" and res["doi"] == "10.1103/physreve.80.041" and res["cached"] is False
    assert res["open_access_url"] == "https://example.org/a.pdf" and res["citations"] == 12
    # 只发出这条参考文献的题录（标题等），不发论文正文
    sent = " ".join(log)
    assert "Cascade" in sent and "Cascading failures are common" not in sent
    n = len(log)
    again = api.post(f"/papers/{pid}/references/1/resolve", {}).json()
    assert again["cached"] is True and len(log) == n  # 缓存：不再联网
    api.post(f"/papers/{pid}/references/1/resolve", {"force": True})
    assert len(log) > n
    # 已在库中的论文会被标出
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE papers SET doi = '10.1103/physreve.80.041' WHERE id = ?", (pid,))
    assert api.post(f"/papers/{pid}/references/1/resolve", {}).json()["in_library"]["paper_id"] == pid


def test_low_confidence_and_not_found_are_reported_honestly(imported):
    api, pid = imported
    _setup(api, pid)
    _net(api, openalex=lambda r: {"results": [_oa_work("Quantum dots for efficient solar cells", 2010, "10.1/qd", ["Quantum", "dots"], "Wei Zhang")]})
    low = api.post(f"/papers/{pid}/references/2/resolve", {}).json()
    assert low["found"] and low["match"]["level"] == "low"  # 原文是另一篇：不能当作同一篇
    _net(api)  # 什么都查不到
    missing = api.post(f"/papers/{pid}/references/3/resolve", {}).json()
    assert missing["found"] is False and "没有查到" in missing["reason"] and "abstract" not in missing


def test_doi_lookup_is_exact_and_arxiv_uses_datacite_doi(imported):
    api, pid = imported
    top = _setup(api, pid)
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM passages WHERE paper_id = ? AND kind = 'reference'", (pid,))
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,50,50,300,60)",
                     (pid, top + 10, 1, "reference", "1. Novak, P. et al. Sparse recovery on networks. Preprint at https://arxiv.org/abs/1903.01234 (2019)."))
    log: list[str] = []
    _net(api, openalex=lambda r: _oa_work("Sparse recovery on networks", 2019, "10.48550/arxiv.1903.01234", ["Sparse", "recovery"], "Petra Novak"), log=log)
    res = api.post(f"/papers/{pid}/references/1/resolve", {}).json()
    assert res["found"] and res["match"]["level"] == "high" and any("10.48550/arxiv.1903.01234" in u for u in log)


# ---------------------------------------------------------------- AI 概括


def test_summarize_refuses_without_an_abstract_and_does_not_call_the_model(imported):
    api, pid = imported
    _setup(api, pid)
    _net(api, openalex=lambda r: {"results": [_oa_work("Cascade Failure in Layered Networks", 2009, "10.1103/x")]})
    api.fake.requests.clear()
    ev = api.sse("/ai/summarize-reference", {"paper_id": pid, "index": 1})
    assert ev[-1][0] == "done" and ev[-1][1]["summary"] is None and ev[-1][1]["reason"] == "no_abstract"
    assert "没有可用摘要" in ev[-1][1]["message"] and ev[-1][1]["link"]
    assert not any("messages" in r["body"] for r in api.fake.requests)  # 没有摘要：一次模型调用都没有


def test_summarize_uses_abstract_and_citing_sentences_and_logs_privacy(imported):
    api, pid = imported
    _setup(api, pid)
    _net(api, openalex=lambda r: {"results": [_oa_work("Cascade Failure in Layered Networks", 2009, "10.1103/x", "Cascades of failure spread quickly across layered networks".split())]})
    api.fake.requests.clear()
    ev = api.sse("/ai/summarize-reference", {"paper_id": pid, "index": 1})
    assert ev[0][0] == "meta" and ev[-1][0] == "done"
    done = ev[-1][1]
    assert done["summary"] and done["label"] == "AI 概括，依据：摘要 + 本文引用处" and done["basis"]["contexts"]
    prompt = str([r["body"]["messages"] for r in api.fake.requests if "messages" in r["body"]])
    assert "Cascades of failure spread quickly across layered networks" in prompt and "layered networks" in prompt  # 只发摘要和引用句
    priv = api.get("/privacy/requests").json()["items"][0]
    assert priv["task_type"] == "summarize" and priv["includes"]["abstract"] is True and priv["includes"]["citing_context"] is True


def test_summarize_warns_when_the_match_is_low_confidence(imported):
    api, pid = imported
    _setup(api, pid)
    _net(api, openalex=lambda r: {"results": [_oa_work("Quantum dots for efficient solar cells", 2010, "10.1/qd", ["Quantum", "dots", "absorb"], "Wei Zhang")]})
    ev = api.sse("/ai/summarize-reference", {"paper_id": pid, "index": 2})
    assert ev[-1][0] == "done" and any("不是同一篇" in n for n in ev[-1][1]["notes"])


def test_reference_tables_are_removed_with_the_paper(imported):
    api, pid = imported
    _setup(api, pid)
    _net(api, openalex=lambda r: {"results": [_oa_work("Cascade Failure in Layered Networks", 2009, "10.1103/x", ["a", "b"])]})
    api.post(f"/papers/{pid}/references/1/resolve", {})
    with api.ctx.db.session() as conn:
        for t in ("paper_references", "reference_meta", "reference_resolutions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE paper_id = ?", (pid,)).fetchone()[0] > 0
    assert api.c.delete(f"/api/papers/{pid}", headers={"Authorization": "Bearer test-token"}).status_code == 200
    with api.ctx.db.session() as conn:
        for t in ("paper_references", "reference_meta", "reference_resolutions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE paper_id = ?", (pid,)).fetchone()[0] == 0


# ---------------------------------------------------------------- 摘要与标题的一致性（OpenAlex 偶尔把别的论文的摘要挂在这条记录上）

GO_TITLE = "Mastering the game of go without human knowledge"
WRONG_ABSTRACT = (  # 一篇讲五子棋的文章的摘要（自己改写的示例）：和“围棋”标题同属棋类，但说的不是同一篇
    "Gomoku, also called Five in a row, is a classical board game for two players. We study search methods that evaluate "
    "positions on the checkerboard and compare several heuristic engines on this game."
)
RIGHT_ABSTRACT = (  # 与标题相符的示例摘要（自己写的）
    "A long-standing aim of artificial intelligence is a program that masters a challenging game from scratch. We describe a Go "
    "program trained by self-play reinforcement learning, without human data or domain knowledge beyond the rules."
)


def test_abstract_consistency_catches_a_neighbouring_topic_but_not_real_abstracts():
    bad = resolve.abstract_consistency(GO_TITLE, WRONG_ABSTRACT)
    assert bad["level"] == "mismatch" and bad["rare_found"] == 0  # “go” 必须整词出现，“gomoku” 不算
    assert resolve.abstract_consistency(GO_TITLE, RIGHT_ABSTRACT)["level"] == "ok"
    normal = [
        ("Statistical mechanics of complex networks", "Complex networks describe a wide range of systems in nature and society, and we review the statistical mechanics of their structure and growth."),
        ("Influence maximization in complex networks through optimal percolation", "We map the problem of maximizing influence on complex networks to optimal percolation and give a fast algorithm."),
        ("Structure-based strategies for drug design and discovery", "Structure-based methods now guide the design of drugs, and we describe strategies used in discovery programmes."),
        ("Network dismantling", "We study how to dismantle a network by removing a small set of nodes and compare several strategies."),
    ]
    for title, abstract in normal:
        assert resolve.abstract_consistency(title, abstract)["level"] == "ok", title


def test_abstract_consistency_chinese_and_unknown_cases():
    zh = "基于深度强化学习的网络关键节点识别"
    assert resolve.abstract_consistency(zh, "提出一种基于深度强化学习的方法，用于识别网络中的关键节点，并在多个真实网络上验证。")["level"] == "ok"
    assert resolve.abstract_consistency(zh, "本文研究水稻在干旱条件下的产量变化，并比较了不同品种的耐旱表现差异。")["level"] == "mismatch"
    assert resolve.abstract_consistency(zh, "We propose a method to find key nodes in networks.")["level"] == "unknown"  # 语言不同：无法比较，不冤枉
    assert resolve.abstract_consistency("Short", "anything at all")["level"] == "unknown"


def test_mismatched_abstract_is_replaced_by_another_source(imported):
    api, pid = imported
    top = _setup(api, pid)
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM passages WHERE paper_id = ? AND kind = 'reference'", (pid,))
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,50,50,300,60)",
                     (pid, top + 10, 1, "reference", f"1. Silver, D. et al. {GO_TITLE}. Nature 550, 354–359 (2017)."))
    _net(api,
         openalex=lambda r: {"results": [_oa_work(GO_TITLE.capitalize(), 2017, "10.1038/nature24270", WRONG_ABSTRACT.split(), "David Silver")]},
         s2=lambda r: {"title": GO_TITLE.capitalize(), "abstract": RIGHT_ABSTRACT, "year": 2017} if "DOI:" in str(r.url) or "search" in str(r.url) else {})
    res = api.post(f"/papers/{pid}/references/1/resolve", {"force": True}).json()
    assert res["found"] and res["match"]["level"] == "high"  # 标题匹配没问题，问题只在摘要
    assert res["abstract"] == RIGHT_ABSTRACT and res["abstract_source"] == "semantic_scholar"
    assert any("OpenAlex 的摘要与这篇的标题不相符，已忽略，改用 Semantic Scholar" in n for n in res["abstract_notes"])
    assert "Gomoku" not in str(res)  # 错的摘要不会留在结果里


def test_mismatched_abstract_with_no_alternative_is_dropped_and_ai_refuses_without_calling_the_model(imported):
    api, pid = imported
    top = _setup(api, pid)
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM passages WHERE paper_id = ? AND kind = 'reference'", (pid,))
        conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,50,50,300,60)",
                     (pid, top + 10, 1, "reference", f"1. Silver, D. et al. {GO_TITLE}. Nature 550, 354–359 (2017)."))
    _net(api, openalex=lambda r: {"results": [_oa_work(GO_TITLE.capitalize(), 2017, "10.1038/nature24270", WRONG_ABSTRACT.split(), "David Silver")]})
    res = api.post(f"/papers/{pid}/references/1/resolve", {"force": True}).json()
    assert res["found"] is True and res["abstract"] is None and res["abstract_mismatch"] is True
    assert "与这篇的标题不相符，已忽略；请去原文查看" in res["abstract_reason"]
    assert "Gomoku" not in str(res)
    api.fake.requests.clear()
    ev = api.sse("/ai/summarize-reference", {"paper_id": pid, "index": 1})
    done = ev[-1][1]
    assert ev[-1][0] == "done" and done["summary"] is None and done["reason"] == "no_abstract"
    assert "不相符" in done["message"] and done["link"]
    assert not any("messages" in r["body"] for r in api.fake.requests)  # 错的摘要不会进入 AI 概括的输入


def test_real_abstracts_are_not_flagged_and_old_cache_entries_are_rechecked(imported):
    api, pid = imported
    _setup(api, pid)
    words = "Cascades of failure spread quickly across layered networks and we model how they start".split()
    _net(api, openalex=lambda r: {"results": [_oa_work("Cascade Failure in Layered Networks", 2009, "10.1103/x", words)]})
    res = api.post(f"/papers/{pid}/references/1/resolve", {}).json()
    assert res["abstract"] and not res.get("abstract_mismatch") and res["abstract_notes"] == []
    # 旧版本缓存（没有 v 字段，没做过一致性检查）会被忽略，重新查询
    stale = {"index": 1, "found": True, "title": "Cascade Failure in Layered Networks", "abstract": "Gomoku, a board game.", "abstract_source": "openalex", "match": {"level": "high", "score": 1.0, "reason": "x"}}
    from paperdesk.references import store
    store.save_resolution(api.ctx, pid, 1, stale)
    again = api.post(f"/papers/{pid}/references/1/resolve", {}).json()
    assert again["cached"] is False and again["abstract"] != "Gomoku, a board game."


def test_initials_first_authors_give_a_title():
    from paperdesk.references.parse import RefEntry, parse_fields

    raw = "R. Li, C. Zhang, S. Mao, From english to pcsel: LLM helps design and optimize photonic crystal surface emitting lasers. arXiv:2304.14257 (2023)."
    e = RefEntry(1, 0, 1, [0], raw)
    parse_fields(e, "numeric")
    assert e.authors[:2] == ["R. Li", "C. Zhang"]
    assert e.title.startswith("From english to pcsel")
