def test_profile_and_cover(api, sample_pdf):
    pid = api.import_pdf(sample_pdf)["paper_id"]
    r = api.get(f"/papers/{pid}/profile")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == pid and d["pages"] and isinstance(d["figures"], list) and isinstance(d["outline"], list)
    assert d["notes"] == 0 and d["annotations"] == 0
    c = api.get(f"/papers/{pid}/cover")
    assert c.status_code == 200 and c.content[:4] == b"\x89PNG"
    assert api.get("/papers/nope/profile").status_code == 404
    assert api.get("/papers/nope/cover").status_code == 404


def test_quality_notes_only_say_what_is_missing():
    from paperdesk.library.profile import quality_notes

    ok = {"status": "ready", "abstract": "x", "authors": ["A"]}
    assert quality_notes(ok, {"pages": 10, "references": 40, "outline": [{"t": 1}]}) == []
    bad = quality_notes({"status": "ready", "abstract": "", "authors": []}, {"pages": 10, "references": 0, "outline": []})
    assert "没有识别到摘要" in bad and "没有识别到作者" in bad and any("参考文献" in n for n in bad) and "没有识别到章节目录" in bad
    assert quality_notes({"status": "parsing", "abstract": ""}, {"pages": 10}) == []  # 还在解析：不下结论
    assert any("只识别到 2 条" in n for n in quality_notes(ok, {"pages": 12, "references": 2, "outline": [1]}))
