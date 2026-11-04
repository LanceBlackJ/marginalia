from __future__ import annotations

import json

import pytest

PAPER_COLS = ("paper_id", "source_paper_id", "child_paper_id", "parent_id")


def _tables_referencing_papers(conn) -> list[tuple[str, str]]:
    """自省：所有带“指向论文”列的表。新模块新增的表也会被自动纳入检查。"""
    out = []
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in names:
        for col in (r[1] for r in conn.execute(f"PRAGMA table_info({t})")):
            if col in PAPER_COLS:
                out.append((t, col))
    return out


def _leftovers(api, pids: list[str]) -> list[tuple[str, str, int]]:
    left = []
    with api.ctx.db.session() as conn:
        for t, col in _tables_referencing_papers(conn):
            marks = ",".join("?" * len(pids))
            n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IN ({marks})", pids).fetchone()[0]
            if n:
                left.append((t, col, n))
    return left


def _populate(api, pid: str) -> None:
    """尽可能多地给论文造关联数据：能走接口的走接口，没有接口的表直接插入。"""
    api.post(f"/papers/{pid}/bookmarks", {"page": 1, "label": "书签"})
    api.post(f"/papers/{pid}/notes", {"text": "一条笔记", "page": 1, "quote": "x", "passage_ord": 3})
    api.post(f"/papers/{pid}/annotations", {"page": 1, "color": "blue", "text": "abc", "rects": [[10, 10, 100, 24]]})
    ev = api.sse("/ai/ask", {"paper_id": pid, "question": "时间注意力模块的作用？"})  # 卡片、证据链、隐私日志、用量
    assert ev[-1][0] == "done"
    job = api.post(f"/papers/{pid}/index").json()["job_id"]  # 语义索引
    if job:
        api.wait_job(job)
    job = api.post(f"/papers/{pid}/translation").json()["job_id"]  # 全文翻译缓存
    if job:
        api.wait_job(job)
    tag = api.post(f"/papers/{pid}/tags", {"name": "待精读"})
    assert tag.status_code == 200
    with api.ctx.db.session() as conn:
        now = "2026-01-01T00:00:00Z"
        conn.execute(  # 知识候选与知识条目
            "INSERT INTO kb_candidates(id, paper_id, kind, title, content, evidence_json, origin_kind, status, created_at) "
            "VALUES ('c1', ?, 'other', 't', 'c', '[]', 'analysis', 'pending', ?)", (pid, now))
        conn.execute(
            "INSERT INTO kb_items(id, title, content, kind, source_paper_id, evidence_json, origin, created_at, updated_at) "
            "VALUES ('i1', 't', 'c', 'other', ?, '[]', 'manual', ?, ?)", (pid, now, now))
        conn.execute(  # 一条与该论文关联的任务及日志
            "INSERT INTO jobs(id, type, title, paper_id, status, progress, created_at) "
            "VALUES ('j1', 'noop', 'x', ?, 'completed', 1, ?)", (pid, now))
        conn.execute("INSERT INTO job_logs(job_id, ts, line) VALUES ('j1', ?, 'log')", (now,))


def test_delete_removes_every_table_row_and_files(imported):
    api, pid = imported
    _populate(api, pid)
    assert _leftovers(api, [pid]), "测试前提：造出的数据应当真实存在"
    pdir = api.ctx.paths.paper_dir(pid)
    assert pdir.exists()
    r = api.delete(f"/papers/{pid}")
    assert r.status_code == 200, r.text
    assert api.get(f"/papers/{pid}").status_code == 404
    assert _leftovers(api, [pid]) == []
    assert not pdir.exists()
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_logs WHERE job_id='j1'").fetchone()[0] == 0  # 任务日志随任务清除
        assert conn.execute("SELECT COUNT(*) FROM kb_topics WHERE name='待精读'").fetchone()[0] == 1  # 标签本身属于知识库，保留
        # 用户保存的知识条目保留内容，只清掉对已删论文的来源引用
        assert conn.execute("SELECT source_paper_id FROM kb_items WHERE id='i1'").fetchone()[0] is None
        # 隐私日志与用量统计保留记录，只清掉论文引用
        assert conn.execute("SELECT COUNT(*) FROM ai_requests").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0] > 0


def test_delete_main_paper_also_deletes_its_supplements(imported, tmp_path):
    api, pid = imported
    from test_materials import build_supplement

    supp_pdf = build_supplement(tmp_path / "SI.pdf")
    r = api.post(f"/papers/{pid}/supplements/files", {"paths": [str(supp_pdf)]})
    assert r.status_code == 200, r.text
    api.wait_job(r.json()["job_id"])
    with api.ctx.db.session() as conn:
        children = [x[0] for x in conn.execute("SELECT id FROM papers WHERE parent_id = ?", (pid,))]
    assert children, "应当有补充材料子论文"
    _populate_child = children[0]
    api.post(f"/papers/{_populate_child}/notes", {"text": "补充材料上的笔记", "page": 1})
    child_dirs = [api.ctx.paths.paper_dir(c) for c in children]
    r = api.delete(f"/papers/{pid}")
    assert r.status_code == 200, r.text
    assert _leftovers(api, [pid, *children]) == []
    assert all(not d.exists() for d in child_dirs)


def test_database_failure_is_reported_readably_and_changes_nothing(imported, monkeypatch):
    api, pid = imported
    import sqlite3

    from paperdesk.library import repo

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "purge_paper_rows", locked)
    r = api.delete(f"/papers/{pid}")
    assert r.status_code == 409
    assert "删除失败" in r.json()["error"]["message"] and "正忙" in r.json()["error"]["message"]
    assert api.get(f"/papers/{pid}").status_code == 200  # 事务整体回滚：论文完整保留
    assert api.ctx.paths.paper_dir(pid).exists()


def test_locked_files_do_not_fail_the_delete_but_are_reported(imported, monkeypatch):
    api, pid = imported
    from paperdesk.library import repo

    monkeypatch.setattr(repo.shutil, "rmtree", lambda *a, **k: None)  # 模拟 Windows 上文件被占用，删不掉
    monkeypatch.setattr("time.sleep", lambda _s: None)
    r = api.delete(f"/papers/{pid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["warnings"] and "占用" in body["warnings"][0]
    assert api.get(f"/papers/{pid}").status_code == 404
    assert _leftovers(api, [pid]) == []  # 数据库记录已经清干净


def test_delete_while_a_job_for_the_paper_is_running(imported):
    api, pid = imported
    with api.ctx.db.session() as conn:
        conn.execute(
            "INSERT INTO jobs(id, type, title, paper_id, status, progress, created_at) "
            "VALUES ('jr', 'noop', 'x', ?, 'running', 0.5, '2026-01-01T00:00:00Z')", (pid,))
    assert api.delete(f"/papers/{pid}").status_code == 200
    assert _leftovers(api, [pid]) == []
