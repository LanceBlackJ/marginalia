def test_alias_set_search_clear(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    assert api.get(f"/papers/{pid}").json()["alias"] is None
    d = api.patch(f"/papers/{pid}", {"alias": "  我的  综述  "}).json()
    assert d["alias"] == "我的 综述" and d["title"]  # 原标题不动，空白被整理
    # 列表里也带备注名，并且能按备注名搜到
    items = api.get("/papers", params={"q": "综述"}).json()["items"]
    assert [p["id"] for p in items] == [pid] and items[0]["alias"] == "我的 综述"
    assert api.get("/papers", params={"q": "综述", "sort": "relevance"}).json()["total"] == 1
    # 空字符串清除
    assert api.patch(f"/papers/{pid}", {"alias": ""}).json()["alias"] is None
    assert api.get("/papers", params={"q": "综述"}).json()["total"] == 0
