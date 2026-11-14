from __future__ import annotations

import shutil


def test_manual_reorder_assigns_slots_and_lists_in_that_order(api, sample_pdf):
    ids = []
    for i in range(3):
        p = sample_pdf.parent / f"r{i}.pdf"
        shutil.copy(sample_pdf, p)
        out = api.import_pdf(p)
        ids.append(out["paper_id"]) if out.get("paper_id") else None
    ids = list(dict.fromkeys(ids))
    # 复制的是同一份文件：只会导入成功一篇，其余是重复。直接用库里已有的论文测排序逻辑
    items = api.get("/papers").json()["items"]
    assert items
    from paperdesk.library import repo

    with api.ctx.db.session() as conn:
        for n in range(3):
            conn.execute(
                "INSERT INTO papers(id, title, imported_at, role) VALUES (?, ?, ?, 'paper')",
                (f"x{n}", f"T{n}", f"2026-01-0{n + 1}T00:00:00.000Z"),
            )
    repo.reorder_papers(api.ctx, ["x2", "x0"])  # 只给了一部分
    got = [p["id"] for p in api.get("/papers", params={"sort": "manual"}).json()["items"]]
    assert got.index("x2") < got.index("x0")
    r = api.post("/papers/reorder", {"ids": ["x1", "x0", "x2"]})
    assert r.status_code == 200
    got = [p["id"] for p in api.get("/papers", params={"sort": "manual"}).json()["items"] if p["id"].startswith("x")]
    assert got == ["x1", "x0", "x2"]


def test_mark_finished_and_filter(imported):
    api, pid = imported
    assert api.get(f"/papers/{pid}").json().get("finished_at") is None
    r = api.patch(f"/papers/{pid}", {"finished": True}).json()
    assert r["finished_at"]
    assert [p["id"] for p in api.get("/papers", params={"finished": "true"}).json()["items"]] == [pid]
    assert api.get("/papers", params={"finished": "false"}).json()["items"] == []
    assert api.patch(f"/papers/{pid}", {"finished": False}).json()["finished_at"] is None


def test_tidy_drops_a_repeated_tail_sentence_but_keeps_real_ones():
    from paperdesk.ai import citeguard

    dup = "韧性是复杂网络的基本属性。理解网络结构和底层动力学如何影响其整体系统韧性至关重要6。 网络结构和底层动力学如何影响其整体系统韧性6。"
    assert citeguard.tidy(dup) == "韧性是复杂网络的基本属性。理解网络结构和底层动力学如何影响其整体系统韧性至关重要6。"
    ok = "我们提出了一种新的方法。实验表明该方法在三个数据集上都优于基线。"
    assert citeguard.tidy(ok) == ok
    short = "结果很好。很好。"
    assert citeguard.tidy(short) == short  # 太短的不动
