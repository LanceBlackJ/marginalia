from __future__ import annotations


def test_text_endpoint_returns_whole_paper_in_reading_order(imported):
    api, pid = imported
    d = api.get(f"/papers/{pid}/text").json()
    assert d["count"] == len(d["items"]) > 0
    assert d["chars"] == sum(len(i["text"]) for i in d["items"])
    ords = [i["ord"] for i in d["items"]]
    assert ords == sorted(ords)
    first = d["items"][0]
    assert {"ord", "page", "kind", "section_title", "text", "bbox"} <= set(first)
    assert len(first["bbox"]) == 4 and first["bbox"][2] > first["bbox"][0]
    # 与按页取段落的接口一致：同一个 ord 的文字和位置相同
    one = api.get(f"/papers/{pid}/passages/{first['ord']}").json()
    assert one["text"] == first["text"] and one["bbox"] == first["bbox"]


def test_text_endpoint_unknown_paper_is_404(api):
    assert api.get("/papers/nope/text").status_code == 404
