"""引文导出、阅读成果合集导出（Markdown/Word）、知识库关系图与批量操作。"""
from __future__ import annotations

import io
import json

from paperdesk.library import citations as C

FAN = {"id": "p1", "title": "Finding key players in complex networks through deep reinforcement learning",
       "authors": ["Changjun Fan", "Li Zeng", "Yizhou Sun", "Yang-Yu Liu"], "year": 2020, "venue": "Nature Machine Intelligence",
       "doi": "10.1038/s42256-020-0177-2", "arxiv_id": None, "url": None}


def paper(**kw):
    base = {"id": "x", "title": "T", "authors": [], "year": None, "venue": None, "doi": None, "arxiv_id": None, "url": None}
    return {**base, **kw}


def add_paper(api, pid, **kw):
    d = paper(id=pid, **kw)
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO papers(id, title, authors_json, year, venue, doi, arxiv_id, source_url, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (pid, d["title"], json.dumps(d["authors"], ensure_ascii=False), d["year"], d["venue"], d["doi"], d["arxiv_id"], d["url"],
                      "2026-01-01T00:00:00Z"))
    return pid


# ---------------- 作者与类型 ----------------
def test_person_normalisation():
    assert C.parse_person("Changjun Fan") == C.Person("Fan", "Changjun")
    assert C.parse_person("Fan, Changjun") == C.Person("Fan", "Changjun")
    assert C.parse_person("Yang-Yu Liu") == C.Person("Liu", "Yang-Yu")
    assert C.parse_person("Ludwig van Beethoven") == C.Person("van Beethoven", "Ludwig")
    assert C.parse_person("Martin Luther King Jr.") == C.Person("King Jr.", "Martin Luther")
    assert C.parse_person("张三") == C.Person("张三")
    assert C.parse_person("Plato") == C.Person("Plato")


def test_entry_type_is_conservative():
    assert C.kind_of(paper(venue="Nature Machine Intelligence")) == "article"
    assert C.kind_of(paper(venue="Advances in Neural Information Processing Systems")) == "inproceedings"
    assert C.kind_of(paper(venue="Proceedings of the 36th ICML")) == "inproceedings"
    assert C.kind_of(paper(arxiv_id="2001.00001")) == "preprint"
    assert C.kind_of(paper()) == "misc"


# ---------------- BibTeX ----------------
def test_bibtex_known_sample_field_by_field():
    out = C.to_bibtex([FAN])
    assert out == (
        "@article{Fan2020Finding,\n"
        "  title = {Finding key players in complex networks through deep reinforcement learning},\n"
        "  author = {Fan, Changjun and Zeng, Li and Sun, Yizhou and Liu, Yang-Yu},\n"
        "  year = {2020},\n"
        "  journal = {Nature Machine Intelligence},\n"
        "  doi = {10.1038/s42256-020-0177-2}\n"
        "}\n"
    )


def test_bibtex_escapes_and_never_invents_fields():
    p = paper(id="a", title="Graph & Net_100% {a} #1", authors=["San Zhang"], year=2021, arxiv_id="2101.00001")
    out = C.to_bibtex([p])
    assert r"title = {Graph \& Net\_100\% \{a\} \#1}" in out
    assert "@misc{Zhang2021Graph," in out and "eprint = {2101.00001}" in out and "archiveprefix = {arXiv}" in out
    bare = C.to_bibtex([paper(id="b", title="Only a title")])
    assert bare == "@misc{anonOnly,\n  title = {Only a title}\n}\n"  # 没有作者、年份、来源：一个字段都不编造
    assert "volume" not in out and "pages" not in out


def test_bibtex_keys_are_stable_and_unique():
    a = {**FAN, "id": "1"}
    b = {**FAN, "id": "2"}
    c = {**FAN, "id": "3"}
    keys = C.make_keys([a, b, c])
    assert keys == {"1": "Fan2020Finding", "2": "Fan2020Findinga", "3": "Fan2020Findingb"}
    assert C.make_keys([a, b, c]) == keys  # 重复导出结果一致
    zh = paper(id="z", title="深度学习综述", authors=["张三"], year=2019)
    assert C.make_keys([zh])["z"].startswith("张三2019") or C.make_keys([zh])["z"].startswith("anon2019")


def test_bibtex_conference_uses_booktitle_and_chinese_author_is_protected():
    p = paper(id="c", title="Attention", authors=["李四", "Ashish Vaswani"], year=2017, venue="Advances in Neural Information Processing Systems")
    out = C.to_bibtex([p])
    assert out.startswith("@inproceedings{")
    assert "booktitle = {Advances in Neural Information Processing Systems}" in out
    assert "author = {{李四} and Vaswani, Ashish}" in out


# ---------------- RIS / CSL / 文本 ----------------
def test_ris_known_sample():
    assert C.to_ris([FAN]) == (
        "TY  - JOUR\nTI  - Finding key players in complex networks through deep reinforcement learning\n"
        "AU  - Fan, Changjun\nAU  - Zeng, Li\nAU  - Sun, Yizhou\nAU  - Liu, Yang-Yu\nPY  - 2020\n"
        "JO  - Nature Machine Intelligence\nDO  - 10.1038/s42256-020-0177-2\nUR  - https://doi.org/10.1038/s42256-020-0177-2\nER  - \n"
    )
    conf = C.to_ris([paper(title="X", venue="Proceedings of ICML", year=2020)])
    assert "TY  - CONF" in conf and "T2  - Proceedings of ICML" in conf


def test_csl_json_is_valid_and_complete():
    items = json.loads(C.to_csl([FAN]))
    assert items == [{"id": "Fan2020Finding", "type": "article-journal", "title": FAN["title"],
                      "author": [{"family": "Fan", "given": "Changjun"}, {"family": "Zeng", "given": "Li"}, {"family": "Sun", "given": "Yizhou"},
                                 {"family": "Liu", "given": "Yang-Yu"}],
                      "issued": {"date-parts": [[2020]]}, "container-title": "Nature Machine Intelligence",
                      "DOI": "10.1038/s42256-020-0177-2", "URL": "https://doi.org/10.1038/s42256-020-0177-2"}]


def test_gbt7714_and_apa_known_samples():
    assert C.to_gbt7714([FAN]) == (
        "[1] FAN C, ZENG L, SUN Y, et al. Finding key players in complex networks through deep reinforcement learning[J]. "
        "Nature Machine Intelligence, 2020. DOI:10.1038/s42256-020-0177-2.\n"
    )
    assert C.to_apa([FAN]) == (
        "Fan, C., Zeng, L., Sun, Y., & Liu, Y.-Y. (2020). Finding key players in complex networks through deep reinforcement learning. "
        "Nature Machine Intelligence. https://doi.org/10.1038/s42256-020-0177-2\n"
    )
    nodate = C.to_apa([paper(title="Untitled work")])
    assert nodate == "(n.d.). Untitled work.\n"


# ---------------- 引文导出 API ----------------
def test_citation_api_by_selection_tag_and_all(api):
    add_paper(api, "p1", **{k: v for k, v in FAN.items() if k not in ("id",)})
    add_paper(api, "p2", title="Second", authors=["Ann Lee"], year=2019, venue="Science")
    r = api.post("/export/citations", {"format": "bibtex", "paper_ids": ["p2", "p1"]}).json()
    assert r["count"] == 2 and r["filename"].endswith(".bib") and r["content"].index("Second") < r["content"].index("Finding key")
    tag = api.post("/papers/p1/tags", {"name": "导出"}).json()
    by_tag = api.post("/export/citations", {"format": "ris", "tag_id": tag["id"]}).json()
    assert by_tag["count"] == 1 and "Fan, Changjun" in by_tag["content"] and "Second" not in by_tag["content"]
    assert api.post("/export/citations", {"format": "apa"}).json()["count"] == 2
    assert api.post("/export/citations", {"format": "bibtex", "paper_ids": ["nope"]}).status_code == 400
    assert api.post("/export/citations", {"format": "word"}).status_code == 400
    fmts = api.get("/export/formats").json()
    assert {f["id"] for f in fmts["citations"]} == {"bibtex", "ris", "csl", "gbt7714", "apa", "mla", "ieee"}


def test_supplement_children_are_not_exported(api):
    add_paper(api, "m", title="Main", authors=["A B"], year=2020)
    add_paper(api, "s", title="Main supplement", authors=["A B"], year=2020)
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO supplementary_assets(id, paper_id, filename, child_paper_id, created_at) VALUES ('sup1','m','supp.pdf','s','2026-01-01T00:00:00Z')")
        conn.execute("UPDATE papers SET parent_id = 'm' WHERE id = 's'")
    r = api.post("/export/citations", {"format": "gbt7714"}).json()
    assert r["count"] == 1 and "Main supplement" not in r["content"]


# ---------------- 阅读成果合集 ----------------
def _seed_reading(api, pid):
    api.post(f"/papers/{pid}/notes", {"text": "我的想法：双重注意力很关键", "page": 1, "quote": "DAR uses two attention modules"})
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO annotations(paper_id, page, kind, color, text, rects_json, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (pid, 1, "highlight", "yellow", "temporal attention", "[]", "和时间模块对应", "2026-01-01T00:00:00Z"))
    api.sse("/ai/ask", {"paper_id": pid, "question": "时间注意力模块的作用是什么？"})
    api.post("/kb/items", {"title": "拓扑注意力", "content": "按邻居分配权重", "kind": "method", "source_paper_id": pid})


def test_bundle_markdown_marks_ai_content_and_keeps_sources(imported):
    api, pid = imported
    _seed_reading(api, pid)
    r = api.post("/export/bundle", {"format": "md", "paper_ids": [pid]}).json()
    md = r["content"]
    assert r["counts"]["notes"] == 1 and r["counts"]["annotations"] == 1 and r["counts"]["cards"] >= 1 and r["counts"]["items"] == 1
    assert "### 我的笔记" in md and "第 1 页" in md and "原文：DAR uses two attention modules" in md
    assert "### 研究卡片（AI 生成）" in md and "**AI 生成**" in md and "**AI 推断**（非论文原文）" in md
    assert "“temporal attention”" in md and "我的想法：和时间模块对应" in md
    assert "### 知识条目（我确认保留的）" in md and "拓扑注意力" in md
    only_notes = api.post("/export/bundle", {"format": "md", "paper_ids": [pid], "cards": False, "items": False, "annotations": False}).json()
    assert "研究卡片" not in only_notes["content"].split("说明：", 1)[1]


def test_bundle_docx_is_a_real_word_file_with_the_same_content(imported):
    api, pid = imported
    _seed_reading(api, pid)
    r = api.post("/export/bundle", {"format": "docx", "paper_ids": [pid]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert r.content[:2] == b"PK"
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    for needle in ("我的笔记", "双重注意力很关键", "DAR uses two attention modules", "研究卡片（AI 生成）", "AI 推断（非论文原文）", "拓扑注意力"):
        assert needle in text, needle


def test_bundle_with_nothing_to_export_is_a_clear_error(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    r = api.post("/export/bundle", {"format": "md", "paper_ids": [pid]})
    assert r.status_code == 400 and "没有可导出" in r.json()["error"]["message"]


# ---------------- 关系图 ----------------
def _items(api, pid, n=3):
    return [api.post("/kb/items", {"title": f"条目{i}", "content": f"内容{i}", "kind": "method" if i % 2 == 0 else "finding",
                                   "source_paper_id": pid}).json() for i in range(n)]


def test_graph_has_items_topics_papers_and_marks_suggested_relations(imported):
    api, pid = imported
    a, b, c = _items(api, pid)
    topic = api.post("/kb/topics", {"name": "注意力"}).json()
    api.post("/kb/items/bulk", {"item_ids": [a["id"], b["id"]], "action": "add_topic", "topic_id": topic["id"]})
    with api.ctx.db.session() as conn:
        for rid, f, t, st in (("r1", a["id"], b["id"], "confirmed"), ("r2", b["id"], c["id"], "suggested")):
            conn.execute("INSERT INTO kb_relations(id, from_kind, from_id, to_kind, to_id, relation, description, evidence_json, ai_inferred, status, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (rid, "item", f, "item", t, "supports", "互相印证", "[]", int(st == "suggested"), st, "2026-01-01T00:00:00Z"))
    g = api.get("/kb/graph").json()
    types = {n["type"] for n in g["nodes"]}
    assert types == {"item", "topic", "paper"} and g["counts"] == {"items": 3, "relations": 2, "suggested": 1}
    rel = {e["ref"]: e for e in g["edges"] if e["type"] == "relation"}
    assert rel["r1"]["status"] == "confirmed" and rel["r2"]["status"] == "suggested" and rel["r2"]["ai_inferred"] is True
    assert {e["type"] for e in g["edges"]} == {"relation", "member", "source"}
    # 按主题过滤：只剩该主题下的 2 个条目和它们之间的关系
    gt = api.get("/kb/graph", params={"topic_id": topic["id"]}).json()
    assert gt["counts"]["items"] == 2 and gt["counts"]["relations"] == 1
    # 按类型过滤
    gk = api.get("/kb/graph", params={"kinds": "finding"}).json()
    assert {n["kind"] for n in gk["nodes"] if n["type"] == "item"} == {"finding"}


# ---------------- 批量操作 ----------------
def test_bulk_add_remove_delete_items(imported):
    api, pid = imported
    a, b, c = _items(api, pid)
    t = api.post("/kb/topics", {"name": "T"}).json()
    ids = [a["id"], b["id"], c["id"]]
    assert api.post("/kb/items/bulk", {"item_ids": ids, "action": "add_topic", "topic_id": t["id"]}).json() == {"affected": 3, "skipped": 0}
    assert all(api.get(f"/kb/items/{i}").json()["topic_ids"] == [t["id"]] for i in ids)
    assert api.post("/kb/items/bulk", {"item_ids": ids[:2] + ["ghost"], "action": "remove_topic", "topic_id": t["id"]}).json() == {"affected": 2, "skipped": 1}
    assert api.get(f"/kb/items/{a['id']}").json()["topic_ids"] == [] and api.get(f"/kb/items/{c['id']}").json()["topic_ids"] == [t["id"]]
    assert api.post("/kb/items/bulk", {"item_ids": ids[:2], "action": "delete"}).json()["affected"] == 2
    assert api.get(f"/kb/items/{a['id']}").status_code == 404 and api.get(f"/kb/items/{c['id']}").status_code == 200
    assert api.post("/kb/items/bulk", {"item_ids": ids, "action": "add_topic", "topic_id": "nope"}).status_code == 404
    assert api.post("/kb/items/bulk", {"item_ids": [], "action": "delete"}).status_code == 400


def test_merge_topics_moves_items_and_papers_and_drops_sources(imported):
    api, pid = imported
    a, b, _ = _items(api, pid)
    t1, t2, t3 = (api.post("/kb/topics", {"name": n}).json() for n in ("一", "二", "三"))
    api.post("/kb/items/bulk", {"item_ids": [a["id"]], "action": "add_topic", "topic_id": t2["id"]})
    api.post("/kb/items/bulk", {"item_ids": [a["id"], b["id"]], "action": "add_topic", "topic_id": t3["id"]})
    api.post(f"/kb/topics/{t2['id']}/papers", {"paper_id": pid})
    r = api.post("/kb/topics/merge", {"source_ids": [t2["id"], t3["id"], t1["id"]], "target_id": t1["id"]}).json()
    assert r["merged"] == 2 and r["topic"]["id"] == t1["id"] and r["topic"]["item_count"] == 2 and r["topic"]["paper_count"] == 1
    assert {t["id"] for t in api.get("/kb/topics").json()["items"]} == {t1["id"]}
    assert api.get(f"/kb/items/{a['id']}").json()["topic_ids"] == [t1["id"]]
    assert api.post("/kb/topics/merge", {"source_ids": [t1["id"]], "target_id": t1["id"]}).status_code == 400
    assert api.post("/kb/topics/merge", {"source_ids": ["ghost"], "target_id": t1["id"]}).status_code == 404
