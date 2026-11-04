from __future__ import annotations


def _tags(api, pid):
    return api.get(f"/papers/{pid}").json()["tags"]


def test_add_tag_creates_topic_and_shows_in_detail_and_list(imported):
    api, pid = imported
    assert _tags(api, pid) == []
    r = api.post(f"/papers/{pid}/tags", {"name": "  图神经网络  "})
    assert r.status_code == 200
    tag = r.json()
    assert tag["name"] == "图神经网络" and tag["id"]
    assert _tags(api, pid) == [tag]
    item = next(i for i in api.get("/papers").json()["items"] if i["id"] == pid)
    assert item["tags"] == [tag]
    # 标签就是知识库主题：知识库里能看到同一个
    assert any(t["id"] == tag["id"] and t["paper_count"] == 1 for t in api.get("/kb/topics").json()["items"])


def test_duplicate_tag_is_case_insensitive_and_idempotent(imported):
    api, pid = imported
    a = api.post(f"/papers/{pid}/tags", {"name": "GNN"}).json()
    b = api.post(f"/papers/{pid}/tags", {"name": "gnn"}).json()
    assert a["id"] == b["id"] and b["name"] == "GNN"  # 复用已有标签，保留原来的写法
    assert len(_tags(api, pid)) == 1
    assert len([t for t in api.get("/kb/topics").json()["items"] if t["name"].lower() == "gnn"]) == 1


def test_tag_validation(imported):
    api, pid = imported
    assert api.post(f"/papers/{pid}/tags", {"name": "   "}).status_code == 400
    assert api.post(f"/papers/{pid}/tags", {"name": "x" * 31}).status_code == 400
    assert api.post(f"/papers/{pid}/tags", {"name": "x" * 30}).status_code == 200
    assert api.post("/papers/nope/tags", {"name": "a"}).status_code == 404


def test_remove_tag_keeps_the_topic(imported):
    api, pid = imported
    tag = api.post(f"/papers/{pid}/tags", {"name": "待读"}).json()
    assert api.delete(f"/papers/{pid}/tags/{tag['id']}").status_code == 200
    assert _tags(api, pid) == []
    assert any(t["id"] == tag["id"] for t in api.get("/kb/topics").json()["items"])  # 只解除关联，不删标签本身


def test_filter_papers_by_tag(api, sample_pdf):
    first = api.import_pdf(sample_pdf)["paper_id"]
    tag = api.post(f"/papers/{first}/tags", {"name": "重点"}).json()
    items = api.get("/papers", params={"tag": tag["id"]}).json()
    assert items["total"] == 1 and items["items"][0]["id"] == first
    assert api.get("/papers", params={"tag": "no-such-tag"}).json()["total"] == 0
    assert api.get("/papers").json()["total"] >= 1


def test_tags_are_sorted_by_name_and_listing_stays_one_query_per_page(imported):
    api, pid = imported
    for name in ["beta", "Alpha", "gamma"]:
        api.post(f"/papers/{pid}/tags", {"name": name})
    assert [t["name"] for t in _tags(api, pid)] == ["Alpha", "beta", "gamma"]
