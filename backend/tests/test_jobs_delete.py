def test_delete_and_clear_finished(api):
    from paperdesk.util import utcnow

    with api.ctx.db.session() as conn:
        for i, st in enumerate(["completed", "failed", "cancelled", "running", "pending"]):
            conn.execute(
                "INSERT INTO jobs(id, type, title, status, created_at) VALUES (?, 'x', ?, ?, ?)",
                (f"j{i}", f"t{i}", st, utcnow()),
            )
            conn.execute("INSERT INTO job_logs(job_id, ts, line) VALUES (?, ?, 'log')", (f"j{i}", utcnow()))
    assert api.delete("/jobs/j0").status_code == 200
    assert api.get("/jobs/j0").status_code == 404
    assert api.delete("/jobs/j3").status_code == 409  # 进行中的不能直接删
    assert api.delete("/jobs/nope").status_code == 404
    r = api.delete("/jobs").json()
    assert r["deleted"] == 2  # failed + cancelled
    left = {j["id"] for j in api.get("/jobs").json()["items"]}
    assert left == {"j3", "j4"}
    with api.ctx.db.session() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM job_logs WHERE job_id IN ('j0','j1','j2')").fetchone()["c"] == 0
