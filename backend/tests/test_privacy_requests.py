from __future__ import annotations

from paperdesk.ai import usage


def _fill(api, n, task="ask"):
    for i in range(n):
        usage.record_request(
            api.ctx, task_type=task, model="m", paper_id=None, passage_ords=[1, 2], chars_sent=100 + i,
            includes={"question": True}, summary=f"第 {i} 条",
        )


def test_old_call_still_returns_items_plus_new_fields(api):
    _fill(api, 3)
    d = api.get("/privacy/requests", params={"limit": 50}).json()
    assert len(d["items"]) == 3 and d["total"] == 3 and d["has_more"] is False
    assert d["items"][0]["summary"] == "第 2 条"  # 新的在前


def test_cursor_pagination_walks_every_record_once(api):
    _fill(api, 30)
    first = api.get("/privacy/requests", params={"limit": 5}).json()
    assert len(first["items"]) == 5 and first["has_more"] is True and first["total"] == 30
    seen = [i["id"] for i in first["items"]]
    cursor = seen[-1]
    while True:
        page = api.get("/privacy/requests", params={"limit": 20, "before_id": cursor}).json()
        seen += [i["id"] for i in page["items"]]
        if not page["has_more"]:
            break
        cursor = page["items"][-1]["id"]
    assert len(seen) == 30 and len(set(seen)) == 30 and seen == sorted(seen, reverse=True)


def test_last_page_has_no_more_and_empty_beyond(api):
    _fill(api, 6)
    last = api.get("/privacy/requests", params={"limit": 5}).json()
    rest = api.get("/privacy/requests", params={"limit": 20, "before_id": last["items"][-1]["id"]}).json()
    assert len(rest["items"]) == 1 and rest["has_more"] is False
    none = api.get("/privacy/requests", params={"limit": 20, "before_id": rest["items"][-1]["id"]}).json()
    assert none["items"] == [] and none["has_more"] is False


def test_filter_by_task_type_with_totals_and_facets(api):
    _fill(api, 4, "ask")
    _fill(api, 3, "translate")
    d = api.get("/privacy/requests", params={"limit": 2, "task_type": "translate"}).json()
    assert d["total"] == 3 and len(d["items"]) == 2 and d["has_more"] is True
    assert all(i["task_type"] == "translate" for i in d["items"])
    nxt = api.get("/privacy/requests", params={"limit": 2, "task_type": "translate", "before_id": d["items"][-1]["id"]}).json()
    assert len(nxt["items"]) == 1 and nxt["has_more"] is False
    # 筛选下拉的数据不受当前筛选影响
    assert {t["task_type"]: t["count"] for t in d["task_types"]} == {"ask": 4, "translate": 3}


def test_limit_bounds_are_validated(api):
    assert api.get("/privacy/requests", params={"limit": 0}).status_code == 400
    assert api.get("/privacy/requests", params={"limit": 500}).status_code == 400
    assert api.get("/privacy/requests", params={"before_id": 0}).status_code == 400
