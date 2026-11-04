from __future__ import annotations

from paperdesk import db as dbmod


def test_modules_can_register_tables_and_columns_idempotently(tmp_path):
    dbmod.register_schema("test_mod", "CREATE TABLE IF NOT EXISTS test_mod_items (id INTEGER PRIMARY KEY, v TEXT);")
    dbmod.register_column("test_mod", "papers", "test_mod_flag", "INTEGER NOT NULL DEFAULT 0")
    d = dbmod.Database(tmp_path / "x.db")
    d.migrate()
    d.migrate()  # 再次启动不能报错（幂等）
    with d.session() as conn:
        conn.execute("INSERT INTO test_mod_items(v) VALUES ('a')")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
        assert "test_mod_flag" in cols
        assert conn.execute("SELECT COUNT(*) FROM test_mod_items").fetchone()[0] == 1
    # 清理注册表，避免影响其它测试
    dbmod._MODULE_SCHEMAS.pop("test_mod", None)
    dbmod._MODULE_COLUMNS[:] = [c for c in dbmod._MODULE_COLUMNS if c[0] != "test_mod"]
