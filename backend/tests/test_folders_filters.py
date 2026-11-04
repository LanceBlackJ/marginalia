"""文库：文件夹（树、移动、删除回收）、多特征筛选（笔记内容/批注/正文/年份/阅读状态…）、facets、建议归类、性能。"""
from __future__ import annotations

import json
import statistics
import time

from paperdesk.util import dumps, new_id, utcnow


def mk(api, title, *, authors=("A. Author",), year=2020, venue=None, status="ready", language="en", abstract="", role="paper",
       parent_id=None, favorite=0, reading_page=1, read_progress=0.0, page_count=10, last_opened_at=None, imported_at=None):
    """直接往库里插一篇论文（不走 PDF 解析），返回 id。"""
    pid = new_id()
    with api.ctx.db.session() as conn:
        conn.execute(
            "INSERT INTO papers(id, title, authors_json, year, venue, abstract, status, language, role, parent_id, favorite, reading_page, "
            "read_progress, page_count, last_opened_at, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, title, dumps(list(authors)), year, venue, abstract, status, language, role, parent_id, favorite, reading_page,
             read_progress, page_count, last_opened_at, imported_at or utcnow()),
        )
    return pid


def note(api, pid, text, quote=None, page=1):
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO notes(paper_id, page, quote, text, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (pid, page, quote, text, utcnow(), utcnow()))


def annotation(api, pid, text, note_text=None, page=1):
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO annotations(paper_id, page, kind, color, text, rects_json, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (pid, page, "highlight", "yellow", text, "[]", note_text, utcnow()))


def ids(api, **params):
    r = api.get("/papers", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


# ---------------------------------------------------------------- 文件夹
def test_folder_tree_crud_nesting_and_validation(api):
    a = api.post("/folders", {"name": "图神经网络"}).json()
    b = api.post("/folders", {"name": "注意力", "parent_id": a["id"]}).json()
    assert b["parent_id"] == a["id"]
    assert api.post("/folders", {"name": "  "}).status_code == 400
    assert api.post("/folders", {"name": "图神经网络"}).status_code == 409  # 同级重名（不分大小写）
    assert api.post("/folders", {"name": "x" * 61}).status_code == 400
    assert api.post("/folders", {"name": "好", "parent_id": "nope"}).status_code == 404
    # 同名但在不同层级是允许的
    assert api.post("/folders", {"name": "图神经网络", "parent_id": a["id"]}).status_code == 200
    # 改名
    assert api.patch(f"/folders/{b['id']}", {"name": "自注意力"}).json()["name"] == "自注意力"
    # 不能移进自己的子孙里
    assert api.patch(f"/folders/{a['id']}", {"parent_id": b["id"]}).status_code == 400
    assert api.patch(f"/folders/{a['id']}", {"parent_id": a["id"]}).status_code == 400
    # 移到最上层（显式 null）
    moved = api.patch(f"/folders/{b['id']}", {"parent_id": None}).json()
    assert moved["parent_id"] is None
    # 只改名不带 parent_id：不会被误移动
    c = api.post("/folders", {"name": "子", "parent_id": a["id"]}).json()
    assert api.patch(f"/folders/{c['id']}", {"name": "子2"}).json()["parent_id"] == a["id"]
    # 嵌套层数上限
    cur = None
    for i in range(6):
        r = api.post("/folders", {"name": f"L{i}", "parent_id": cur})
        if i < 6 and r.status_code == 200:
            cur = r.json()["id"]
    assert api.post("/folders", {"name": "太深", "parent_id": cur}).status_code == 400
    assert api.patch("/folders/missing", {"name": "x"}).status_code == 404


def test_reorder_folders(api):
    f1 = api.post("/folders", {"name": "一"}).json()["id"]
    f2 = api.post("/folders", {"name": "二"}).json()["id"]
    f3 = api.post("/folders", {"name": "三"}).json()["id"]
    assert api.post("/folders/reorder", {"parent_id": None, "ids": [f3, f1, f2]}).status_code == 200
    names = [f["name"] for f in sorted(api.get("/folders").json()["items"], key=lambda f: f["sort"])]
    assert names == ["三", "一", "二"]
    assert api.post("/folders/reorder", {"parent_id": None, "ids": [f3, f1]}).status_code == 400  # 必须是这一级的全部


def test_move_papers_filter_counts_and_supplements_follow_parent(api):
    root = api.post("/folders", {"name": "根"}).json()["id"]
    sub = api.post("/folders", {"name": "子", "parent_id": root}).json()["id"]
    p1, p2, p3 = mk(api, "论文一"), mk(api, "论文二"), mk(api, "论文三")
    supp = mk(api, "补充材料", role="supplement", parent_id=p1)
    r = api.post("/papers/move", {"paper_ids": [p1, p2, supp], "folder_id": root}).json()
    assert r == {"ok": True, "moved": 2, "skipped_supplements": 1}
    assert api.post("/papers/move", {"paper_ids": [p3], "folder_id": sub}).json()["moved"] == 1
    assert api.post("/papers/move", {"paper_ids": ["nope"], "folder_id": root}).status_code == 404
    assert api.post("/papers/move", {"paper_ids": [p1], "folder_id": "nope"}).status_code == 404
    assert api.post("/papers/move", {"paper_ids": []}).status_code == 400
    assert ids(api, folder=root) == {p1, p2, p3}  # 默认含子文件夹
    assert ids(api, folder=root, subfolders="false") == {p1, p2}
    assert ids(api, folder=sub) == {p3}
    assert ids(api, folder="none") == set()
    items = api.get("/papers", params={"folder": sub}).json()["items"]
    assert items[0]["folder_id"] == sub and items[0]["folder_path"] == "根 / 子"
    fl = api.get("/folders").json()
    by = {f["id"]: f for f in fl["items"]}
    assert (by[root]["paper_count"], by[root]["total_count"], by[sub]["paper_count"]) == (2, 3, 1)
    assert fl["counts"]["all"] == 3 and fl["counts"]["unfiled"] == 0
    # 移回未分类
    api.post("/papers/move", {"paper_ids": [p2], "folder_id": None})
    assert ids(api, folder="none") == {p2}


def test_delete_folder_keeps_papers_and_returns_them_to_unfiled(api):
    root = api.post("/folders", {"name": "根"}).json()["id"]
    sub = api.post("/folders", {"name": "子", "parent_id": root}).json()["id"]
    p1, p2 = mk(api, "甲"), mk(api, "乙")
    api.post("/papers/move", {"paper_ids": [p1], "folder_id": root})
    api.post("/papers/move", {"paper_ids": [p2], "folder_id": sub})
    r = api.c.delete(f"/api/folders/{root}", headers={"Authorization": "Bearer test-token"}).json()
    assert r == {"ok": True, "deleted_folders": 2, "moved_papers": 2}
    assert api.get("/folders").json()["items"] == []
    assert ids(api, folder="none") == {p1, p2}  # 论文还在，只是回到未分类
    assert api.get(f"/papers/{p1}").json()["folder_id"] is None


def test_deleting_a_paper_cleans_folder_related_rows_and_updates_counts(api):
    f = api.post("/folders", {"name": "夹"}).json()["id"]
    p1, p2 = mk(api, "一"), mk(api, "二")
    api.post("/papers/move", {"paper_ids": [p1, p2], "folder_id": f})
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO folder_suggestion_ignored(paper_id, folder_id) VALUES (?,?)", (p1, f))
        conn.execute("INSERT INTO paper_vectors(paper_id, model, dim, n, vec) VALUES (?,?,?,?,?)", (p1, "m", 1, 1, b"\x00\x00\x00\x00"))
    assert api.c.delete(f"/api/papers/{p1}", headers={"Authorization": "Bearer test-token"}).status_code == 200
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM folder_suggestion_ignored WHERE paper_id = ?", (p1,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_vectors WHERE paper_id = ?", (p1,)).fetchone()[0] == 0
    assert api.get("/folders").json()["items"][0]["paper_count"] == 1  # 文件夹还在，数量更新


# ---------------------------------------------------------------- 筛选
def test_note_content_filter_returns_highlightable_snippets(api):
    a = mk(api, "Paper A")
    b = mk(api, "Paper B")
    c = mk(api, "Paper C")
    note(api, a, "这里的 Attention 机制是关键，需要和 Transformer 对比", quote="self-attention computes weights", page=3)
    note(api, b, "实验设置有问题", quote="We train for 100 epochs with attention dropout", page=5)
    note(api, c, "无关的想法")
    r = api.get("/papers", params={"note_q": "attention"}).json()
    assert {i["id"] for i in r["items"]} == {a, b} and r["total"] == 2
    ma = next(i for i in r["items"] if i["id"] == a)["matches"]["notes"][0]
    assert ma["page"] == 3 and ma["from"] == "笔记" and "Attention" in ma["text"]
    s, e = ma["hit"]
    assert ma["text"][s:e].lower() == "attention"  # 下标正好圈住命中词
    mb = next(i for i in r["items"] if i["id"] == b)["matches"]["notes"][0]
    assert mb["from"] == "笔记引用的原文"
    # 中文子串、多个词（AND）、大小写不敏感
    assert ids(api, note_q="机制") == {a}
    assert ids(api, note_q="attention transformer") == {a}
    assert ids(api, note_q="attention 实验") == {b}  # 词可以分别出现在笔记正文和它引用的原文里
    assert ids(api, note_q="attention 不存在的词") == set()  # 但每个词都必须出现（AND）
    assert ids(api, note_q="ATTENTION") == {a, b}
    assert ids(api, note_q="根本不存在的词") == set()


def test_note_filter_follows_edits_and_deletes(api):
    p = mk(api, "P")
    note(api, p, "旧的内容 alpha")
    assert ids(api, note_q="alpha") == {p}
    with api.ctx.db.session() as conn:
        conn.execute("UPDATE notes SET text = 'beta' WHERE paper_id = ?", (p,))
    assert ids(api, note_q="alpha") == set() and ids(api, note_q="beta") == {p}
    with api.ctx.db.session() as conn:
        conn.execute("DELETE FROM notes WHERE paper_id = ?", (p,))
    assert ids(api, note_q="beta") == set()


def test_annotation_and_card_filters(api):
    a, b = mk(api, "A"), mk(api, "B")
    annotation(api, a, "the key finding is robust", note_text="需要复核")
    annotation(api, b, "irrelevant sentence")
    r = api.get("/papers", params={"annotation_q": "复核"}).json()
    assert [i["id"] for i in r["items"]] == [a] and r["items"][0]["matches"]["annotations"][0]["from"] == "批注"
    assert ids(api, annotation_q="finding") == {a}
    chain = new_id()
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO insight_chains(id, paper_id, title, object_type, object_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (chain, b, "t", "selection", "k", utcnow(), utcnow()))
        conn.execute(
            "INSERT INTO insight_cards(id, paper_id, chain_id, object_type, status, saved, raw_markdown, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_id(), b, chain, "selection", "complete", 1, "## 直接回答\n该方法使用了图注意力机制。", utcnow()),
        )
    r = api.get("/papers", params={"card_q": "图注意力"}).json()
    assert [i["id"] for i in r["items"]] == [b] and "AI 卡片" in r["items"][0]["matches"]["cards"][0]["from"]


def test_fulltext_filter_uses_passage_index_and_falls_back_for_chinese(imported, chinese_pdf):
    api, pid = imported
    r = api.get("/papers", params={"text_q": "attention"}).json()
    assert pid in {i["id"] for i in r["items"]}
    hit = next(i for i in r["items"] if i["id"] == pid)["matches"]["fulltext"][0]
    assert hit["page"] >= 1 and "attention" in hit["text"].lower() and hit["from"] == "正文"
    assert pid not in ids(api, text_q="zzzzqqqq")
    # 前缀匹配
    assert pid in ids(api, text_q="attent")
    zh = api.import_pdf(chinese_pdf)["paper_id"]
    with api.ctx.db.session() as conn:
        word = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND length(text) > 6 LIMIT 1", (zh,)).fetchone()["text"][:3]
    assert zh in ids(api, text_q=word)  # 中文走子串匹配


def test_metadata_filters_and_combinations(api):
    p1 = mk(api, "Graph Learning", authors=("Alice Zhang", "Bob Li"), year=2019, venue="NeurIPS", language="en")
    p2 = mk(api, "Vision Transformers", authors=("Carol Wu",), year=2021, venue="ICLR", language="en", favorite=1)
    p3 = mk(api, "图神经网络综述", authors=("王芳",), year=2023, venue="计算机学报", language="zh", status="failed")
    assert ids(api, year_from=2020) == {p2, p3} and ids(api, year_to=2020) == {p1} and ids(api, year_from=2020, year_to=2021) == {p2}
    assert ids(api, author="alice") == {p1} and ids(api, author="王芳") == {p3}
    assert ids(api, venue="iclr") == {p2} and ids(api, language="zh") == {p3}
    assert ids(api, favorite="true") == {p2} and ids(api, view="favorite") == {p2}
    assert ids(api, parse="failed") == {p3} and ids(api, parse="ready") == {p1, p2}
    assert api.get("/papers", params={"parse": "bogus"}).status_code == 400
    assert api.get("/papers", params={"read": "bogus"}).status_code == 400
    # 组合：AND
    assert ids(api, year_from=2019, language="en", q="transformers") == {p2}
    assert ids(api, year_from=2019, language="en", author="carol", venue="neurips") == set()
    note(api, p1, "笔记")
    annotation(api, p2, "高亮")
    assert ids(api, has_notes="true") == {p1} and ids(api, has_notes="false") == {p2, p3}
    assert ids(api, has_annotations="true") == {p2}
    assert ids(api, has_notes="true", year_to=2019, language="en") == {p1}


def test_read_status_filters(api):
    unread = mk(api, "未读")
    reading = mk(api, "在读", reading_page=3, read_progress=0.3, last_opened_at=utcnow())
    done_by_progress = mk(api, "读完A", read_progress=0.97, reading_page=9, last_opened_at=utcnow())
    done_by_page = mk(api, "读完B", reading_page=10, page_count=10, last_opened_at=utcnow())
    opened_only = mk(api, "刚打开", last_opened_at=utcnow())
    assert ids(api, read="unread") == {unread}
    assert ids(api, read="reading") == {reading, opened_only}
    assert ids(api, read="done") == {done_by_progress, done_by_page}


def test_tag_modes_and_imported_range_and_code_supplement_flags(api):
    p1, p2, p3 = mk(api, "一", imported_at="2024-01-05T00:00:00Z"), mk(api, "二", imported_at="2024-02-10T00:00:00Z"), mk(api, "三", imported_at="2024-03-15T00:00:00Z")
    t1 = api.post(f"/papers/{p1}/tags", {"name": "图"}).json()["id"]
    api.post(f"/papers/{p2}/tags", {"name": "图"})
    t2 = api.post(f"/papers/{p1}/tags", {"name": "注意力"}).json()["id"]
    assert ids(api, tags=f"{t1},{t2}", tag_mode="any") == {p1, p2}
    assert ids(api, tags=f"{t1},{t2}", tag_mode="all") == {p1}
    assert ids(api, tag=t2) == {p1}  # 旧的单标签参数仍然可用
    assert ids(api, imported_from="2024-02-01", imported_to="2024-03-01") == {p2}
    supp = mk(api, "补", role="supplement", parent_id=p3)
    assert supp not in ids(api)
    assert ids(api, has_supplement="true") == {p3} and ids(api, has_supplement="false") == {p1, p2}
    with api.ctx.db.session() as conn:
        conn.execute("INSERT INTO code_repos(id, paper_id, origin, created_at) VALUES (?,?,?,?)", (new_id(), p2, "github", utcnow()))
    assert ids(api, has_code="true") == {p2}


def test_relevance_sort_and_backward_compatible_sorts(api):
    a = mk(api, "A study of deep learning for graphs", abstract="x")
    b = mk(api, "Something else", abstract="mentions learning once")
    c = mk(api, "Learning to learn", authors=("Learning Lab",))
    order = [i["id"] for i in api.get("/papers", params={"q": "learning", "sort": "relevance"}).json()["items"]]
    assert order[0] in (a, c) and order[-1] == b  # 标题命中排在只有摘要命中之前
    for s in ("recent", "title", "author", "year", "imported"):
        assert api.get("/papers", params={"sort": s}).status_code == 200


def test_facets(api):
    p1 = mk(api, "一", authors=("Alice", "Bob"), year=2020, venue="NeurIPS")
    mk(api, "二", authors=("Alice",), year=2020, venue="NeurIPS", language="zh")
    mk(api, "三", authors=("Carol",), year=2022, venue="ICLR", status="failed")
    note(api, p1, "n")
    api.post(f"/papers/{p1}/tags", {"name": "图"})
    f = api.get("/papers/facets").json()
    assert f["total"] == 3
    assert {y["year"]: y["count"] for y in f["years"]} == {2022: 1, 2020: 2}
    assert f["authors"][0] == {"author": "Alice", "count": 2}
    assert f["venues"][0] == {"venue": "NeurIPS", "count": 2}
    assert f["tags"] == [{"id": f["tags"][0]["id"], "name": "图", "count": 1}]
    assert {x["language"]: x["count"] for x in f["languages"]} == {"en": 2, "zh": 1}
    assert f["parse"]["failed"] == 1 and f["flags"]["has_notes"] == 1
    assert sum(f["read"].values()) == 3


def test_smart_views_save_and_delete(api):
    assert api.post("/smart-views", {"name": "空", "query": {}}).status_code == 400
    v = api.post("/smart-views", {"name": "有笔记的 2020 年后论文", "query": {"has_notes": True, "year_from": 2020, "bogus": None}}).json()
    assert v["query"] == {"has_notes": True, "year_from": 2020}
    assert api.post("/smart-views", {"name": "有笔记的 2020 年后论文", "query": {"has_notes": True}}).status_code == 409
    assert api.get("/smart-views").json()["items"][0]["id"] == v["id"]
    assert api.post("/smart-views", {"name": "坏", "query": {"read": "nope"}}).status_code == 400
    assert api.c.delete(f"/api/smart-views/{v['id']}", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert api.get("/smart-views").json()["items"] == []


# ---------------------------------------------------------------- 建议归类
def test_suggestions_by_keywords_when_no_semantic_index(api):
    f = api.post("/folders", {"name": "图神经网络"}).json()["id"]
    g = api.post("/folders", {"name": "医学影像"}).json()["id"]
    a = mk(api, "Graph neural networks for node classification", abstract="graph convolution message passing node embeddings")
    b = mk(api, "Graph attention networks", abstract="graph neural network attention over neighbours node features")
    m1 = mk(api, "Medical image segmentation", abstract="convolutional segmentation of MRI scans tumour boundaries")
    api.post("/papers/move", {"paper_ids": [a, b], "folder_id": f})
    api.post("/papers/move", {"paper_ids": [m1], "folder_id": g})
    new_graph = mk(api, "Message passing on graph neural networks", abstract="we study graph neural network node embeddings and convolution")
    unrelated = mk(api, "Quantum error correction codes", abstract="stabiliser codes and logical qubits")
    s = api.get("/folders-suggestions").json()
    assert s["method"] == "keywords"
    by = {i["paper_id"]: i for i in s["items"]}
    assert by[new_graph]["folder_id"] == f and by[new_graph]["confidence"] in ("high", "medium", "low")
    assert "共享关键词" in by[new_graph]["basis"] and "按关键词重合判断" in by[new_graph]["basis"]
    assert unrelated not in by
    # 忽略后不再建议这一对；接受 = 普通的移动
    api.post("/folders-suggestions/ignore", {"paper_id": new_graph, "folder_id": f})
    assert new_graph not in {i["paper_id"] for i in api.get("/folders-suggestions").json()["items"]}
    assert ids(api, folder="none") >= {new_graph}  # 建议从不自动移动论文


def test_keyword_suggestions_work_between_chinese_papers(api):
    f = api.post("/folders", {"name": "复杂网络"}).json()["id"]
    a = mk(api, "复杂网络中关键节点的识别方法", abstract="研究复杂网络里关键节点识别与网络瓦解", language="zh")
    api.post("/papers/move", {"paper_ids": [a], "folder_id": f})
    new = mk(api, "基于强化学习的复杂网络关键节点识别", abstract="用强化学习识别复杂网络的关键节点", language="zh")
    other = mk(api, "蛋白质结构预测的深度学习方法", abstract="预测蛋白质折叠结构", language="zh")
    by = {i["paper_id"]: i for i in api.get("/folders-suggestions").json()["items"]}
    assert by[new]["folder_id"] == f and other not in by


def test_suggestions_need_folders_and_unfiled_papers(api):
    mk(api, "孤零零的论文")
    s = api.get("/folders-suggestions").json()
    assert s["items"] == [] and s["method"] == "none" and s["reason"]


def test_suggestions_by_embeddings_when_index_exists(api):
    from fakellm import FakeLLM
    from paperdesk.ai.embeddings import pack

    api.configure_ai()
    f = api.post("/folders", {"name": "注意力机制"}).json()["id"]
    a = mk(api, "Self attention", abstract="x")
    b = mk(api, "Cooking recipes", abstract="x")
    c = mk(api, "Cross attention", abstract="x")
    api.post("/papers/move", {"paper_ids": [a], "folder_id": f})
    texts = {a: "attention weights queries keys values softmax attention", c: "attention queries keys values softmax weights attention", b: "pasta tomato oven recipe kitchen cooking"}
    with api.ctx.db.session() as conn:
        for pid, text in texts.items():
            for ord_ in range(3):
                conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)", (pid, ord_, 1, "paragraph", text))
                conn.execute("INSERT INTO passage_embeddings(paper_id, ord, model, dim, vec, created_at) VALUES (?,?,?,?,?,?)",
                             (pid, ord_, "bge-m3", 64, pack(FakeLLM.embed(text)), utcnow()))
    s = api.get("/folders-suggestions").json()
    assert s["method"] == "embedding"
    by = {i["paper_id"]: i for i in s["items"]}
    assert by[c]["folder_id"] == f and "语义相似度" in by[c]["basis"]
    assert b not in by
    # 向量缓存在 paper_vectors，删除论文时一并清掉
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_vectors").fetchone()[0] >= 2


# ---------------------------------------------------------------- 性能
def test_list_filters_stay_fast_with_thousands_of_papers_and_notes(api):
    import random

    random.seed(7)
    vocab = "graph network attention learning transformer vision language model training optimisation kernel protein quantum robot".split()
    n_papers, n_notes, n_pass = 2000, 5000, 12
    pids = []
    now = utcnow()
    with api.ctx.db.session() as conn:
        for i in range(n_papers):
            pid = new_id()
            pids.append(pid)
            conn.execute(
                "INSERT INTO papers(id, title, authors_json, year, venue, abstract, status, language, role, imported_at, page_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"Paper {i} {' '.join(random.sample(vocab, 3))}", dumps([f"Author {i % 300}"]), 2010 + i % 15, f"Venue {i % 20}",
                 " ".join(random.choices(vocab, k=40)), "ready", "en", "paper", now, 12),
            )
            for k in range(n_pass):
                conn.execute("INSERT INTO passages(paper_id, ord, page, kind, text, x0,y0,x1,y1) VALUES (?,?,?,?,?,0,0,1,1)",
                             (pid, k, 1 + k // 3, "paragraph", " ".join(random.choices(vocab, k=60))))
        for i in range(n_notes):
            conn.execute("INSERT INTO notes(paper_id, page, quote, text, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                         (random.choice(pids), 1, " ".join(random.choices(vocab, k=15)), " ".join(random.choices(vocab, k=25)), now, now))
    f = api.post("/folders", {"name": "大夹"}).json()["id"]
    api.post("/papers/move", {"paper_ids": pids[:800], "folder_id": f})

    def timed(**params):
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = api.get("/papers", params={"limit": 50, **params})
            ts.append(time.perf_counter() - t0)
            assert r.status_code == 200
        return statistics.median(ts), r.json()["total"]

    results = {
        "plain": timed(),
        "note_q": timed(note_q="quantum robot"),
        "text_q": timed(text_q="protein kernel"),
        "annotation+note+year": timed(note_q="graph", year_from=2015, has_notes="true"),
        "folder+q+relevance": timed(folder=f, q="attention", sort="relevance"),
        "text_q+note_q": timed(text_q="vision", note_q="language"),
    }
    t0 = time.perf_counter()
    assert api.get("/papers/facets").status_code == 200
    results["facets"] = (time.perf_counter() - t0, 0)
    print("\n性能实测（2000 篇论文、24000 段正文、5000 条笔记；列表每次取 50 条，三次取中位数）：")
    for k, (t, total) in results.items():
        print(f"  {k:24s} {t * 1000:7.1f} ms  命中 {total}")
    for k, (t, _total) in results.items():
        assert t < 0.6, f"{k} 太慢：{t:.3f}s"
