from datetime import datetime, timedelta, timezone

from paperdesk.knowledge import interest as I

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def _s(**kw):
    base = dict(notes=0, highlights=0, favorite=False, asks=0, progress=0.0, opened=True, last=NOW, now=NOW)
    base.update(kw)
    return I.score_paper(**base)


def test_reading_alone_is_weak_and_never_counts_as_explicit():
    full = _s(progress=1.0)
    assert full["explicit"] == 0 and full["score"] <= 1.0  # 读完一篇最多一小点
    assert _s(progress=0.2)["score"] == 0  # 没读多少，不算数
    glance = _s(progress=0.05)
    assert glance["penalty"] > 0 and glance["score"] == 0  # 点开就走，不加分


def test_notes_and_highlights_dominate_and_saturate():
    one = _s(notes=1)["score"]
    many = _s(notes=50)["score"]
    assert one > _s(progress=1.0)["score"] * 0.5
    assert many < 2.1  # 笔记再多也有上限
    assert _s(notes=2, highlights=3, favorite=True)["score"] > many


def test_decay_halves_each_90_days_but_has_floor():
    fresh = _s(notes=2)["score"]
    old = _s(notes=2, last=NOW - timedelta(days=90))["score"]
    assert abs(old / fresh - 0.5) < 0.01
    assert _s(notes=2, last=NOW - timedelta(days=2000))["score"] >= fresh * I.DECAY_FLOOR - 1e-9


def test_levels_need_explicit_engagement():
    assert I.level_of(10.0, 0) == "read_only"  # 光读再多也不是兴趣
    assert I.level_of(2.0, 1) == "watching"
    assert I.level_of(5.0, 1) == "watching"
    assert I.level_of(5.0, 2) == "interest"
    assert I.level_of(1.0, 1) == "read_only"


def test_auto_tasks_do_not_count_as_questions():
    assert "translate" not in I.USER_ASK_TASKS and "paper_analysis" not in I.USER_ASK_TASKS and "embedding" not in I.USER_ASK_TASKS


def test_topic_interests_end_to_end(api):
    from paperdesk.util import utcnow

    now = utcnow()
    with api.ctx.db.session() as conn:
        for pid in ("a", "b", "c", "d"):
            conn.execute("INSERT INTO papers(id, title, status, imported_at, read_progress, last_opened_at) VALUES (?,?,?,?,?,?)",
                         (pid, f"paper {pid}", "ready", now, 1.0 if pid == "d" else 0.0, now))
        conn.execute("INSERT INTO kb_topics(id, name, created_at, updated_at) VALUES ('t1','图神经网络',?,?)", (now, now))
        conn.execute("INSERT INTO kb_topics(id, name, created_at, updated_at) VALUES ('t2','只是读过的主题',?,?)", (now, now))
        for pid in ("a", "b"):
            conn.execute("INSERT INTO kb_paper_topics(paper_id, topic_id, status, source) VALUES (?, 't1', 'confirmed', 'user')", (pid,))
            for _ in range(3):
                conn.execute("INSERT INTO notes(paper_id, text, created_at, updated_at) VALUES (?, '想法', ?, ?)", (pid, now, now))
            conn.execute("UPDATE papers SET favorite = 1 WHERE id = ?", (pid,))
        conn.execute("INSERT INTO kb_paper_topics(paper_id, topic_id, status, source) VALUES ('d', 't2', 'confirmed', 'user')")
    items = api.get("/profile/interests").json()["items"]
    assert [i["name"] for i in items] == ["图神经网络"]  # 只读过的主题默认不出现
    assert items[0]["level"] == "interest" and "写过笔记" in items[0]["basis"]
    all_items = api.get("/profile/interests", params={"include_read_only": True}).json()["items"]
    assert {i["name"]: i["level"] for i in all_items}["只是读过的主题"] == "read_only"
    assert api.post("/profile/interests/t1/mute").status_code == 200
    assert api.get("/profile/interests").json()["items"] == []
    assert api.delete("/profile/interests/t1/mute").status_code == 200
    assert len(api.get("/profile/interests").json()["items"]) == 1
