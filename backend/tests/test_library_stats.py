def test_library_stats_counts(api, sample_pdf):
    empty = api.get("/papers/stats").json()
    assert empty["total"] == 0 and empty["years"] == [] and empty["folders"] == [] and empty["unfiled"] == 0

    pid = api.import_pdf(sample_pdf)["paper_id"]
    s = api.get("/papers/stats").json()
    assert s["total"] == 1 and s["with_pdf"] == 1
    assert s["reading"] == {"unread": 1, "finished": 0, "reading": 0}
    assert s["unfiled"] == 1 and s["favorites"] == 0
    assert len(s["imported_by_month"]) == 12 and s["imported_by_month"][-1]["count"] == 1  # 这个月导入的

    api.patch(f"/papers/{pid}", {"read_progress": 0.4, "favorite": True})
    s = api.get("/papers/stats").json()
    assert s["reading"] == {"unread": 0, "finished": 0, "reading": 1} and s["favorites"] == 1
    api.patch(f"/papers/{pid}", {"read_progress": 0.95})
    assert api.get("/papers/stats").json()["reading"]["finished"] == 1

    fid = api.post("/folders", {"name": "图神经网络"}).json()["id"]
    child = api.post("/folders", {"name": "注意力", "parent_id": fid}).json()["id"]
    api.post("/papers/move", {"paper_ids": [pid], "folder_id": child})
    s = api.get("/papers/stats").json()
    assert s["folders"] == [{"name": "图神经网络 / 注意力", "count": 1}] and s["unfiled"] == 0

    api.post(f"/papers/{pid}/notes", {"text": "一条笔记"})
    assert api.get("/papers/stats").json()["mine"]["notes"] == 1


def test_activity_by_day_counts_real_records(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    api.post(f"/papers/{pid}/notes", {"text": "笔记 A"})
    api.post(f"/papers/{pid}/notes", {"text": "笔记 B"})
    a = api.get("/papers/stats").json()["activity"]
    assert len(a["days"]) == 371
    today = a["days"][-1]
    assert today["imports"] == 1 and today["notes"] == 2 and today["count"] == 3
    assert a["active_days"] == 1 and a["total"] == 3 and a["streak"] == 1 and a["longest"] == 1
    assert sum(d["count"] for d in a["days"][:-1]) == 0  # 其他天没有记录就是 0，不编数据
