"""SQLite 访问层与数据库结构（阶段一的核心数据实体）。

每次调用使用独立连接（WAL 模式），FastAPI 线程池、任务线程与异步流都可以安全使用。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_V1 = """
CREATE TABLE papers (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  authors_json TEXT NOT NULL DEFAULT '[]',
  year INTEGER,
  venue TEXT,
  doi TEXT,
  arxiv_id TEXT,
  abstract TEXT,
  source_url TEXT,
  original_path TEXT,
  source_folder TEXT,
  has_pdf INTEGER NOT NULL DEFAULT 0,
  file_hash TEXT,
  status TEXT NOT NULL DEFAULT 'parsing',
  parse_error TEXT,
  page_count INTEGER,
  language TEXT,
  favorite INTEGER NOT NULL DEFAULT 0,
  reading_page INTEGER NOT NULL DEFAULT 1,
  read_progress REAL NOT NULL DEFAULT 0,
  reading_state_json TEXT,
  last_opened_at TEXT,
  imported_at TEXT NOT NULL,
  metadata_source TEXT NOT NULL DEFAULT 'pdf',
  access_info_json TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX idx_papers_hash ON papers(file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX idx_papers_doi ON papers(doi);

CREATE TABLE file_assets (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  sha256 TEXT,
  size INTEGER,
  original_path TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE sections (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  level INTEGER NOT NULL DEFAULT 1,
  title TEXT NOT NULL,
  page INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'detected'
);
CREATE INDEX idx_sections_paper ON sections(paper_id, ord);

CREATE TABLE page_info (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  width REAL NOT NULL,
  height REAL NOT NULL,
  PRIMARY KEY (paper_id, page)
);

CREATE TABLE passages (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  page INTEGER NOT NULL,
  section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
  kind TEXT NOT NULL DEFAULT 'paragraph',
  text TEXT NOT NULL,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  UNIQUE (paper_id, ord)
);
CREATE INDEX idx_passages_page ON passages(paper_id, page);

CREATE VIRTUAL TABLE passages_fts USING fts5(
  text, content='passages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER passages_ai AFTER INSERT ON passages BEGIN
  INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER passages_ad AFTER DELETE ON passages BEGIN
  INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER passages_au AFTER UPDATE OF text ON passages BEGIN
  INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE bookmarks (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER,
  quote TEXT,
  passage_ord INTEGER,
  text TEXT NOT NULL DEFAULT '',
  card_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE annotations (
  id INTEGER PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'highlight',
  color TEXT NOT NULL DEFAULT 'yellow',
  text TEXT NOT NULL DEFAULT '',
  rects_json TEXT NOT NULL DEFAULT '[]',
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE insight_chains (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_key TEXT NOT NULL,
  object_label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_chains_paper ON insight_chains(paper_id, object_key);

CREATE TABLE insight_cards (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  chain_id TEXT NOT NULL REFERENCES insight_chains(id) ON DELETE CASCADE,
  object_type TEXT NOT NULL,
  object_label TEXT NOT NULL DEFAULT '',
  selection_json TEXT,
  question TEXT NOT NULL DEFAULT '',
  intent TEXT NOT NULL DEFAULT 'free',
  task_type TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'streaming',
  saved INTEGER NOT NULL DEFAULT 1,
  answer_json TEXT,
  raw_markdown TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  verification_json TEXT,
  usage_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_cards_paper ON insight_cards(paper_id, created_at);

CREATE TABLE paper_analysis (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  job_id TEXT,
  model TEXT,
  content_json TEXT,
  evidence_json TEXT,
  verification_json TEXT,
  error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE translations (
  paper_id TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  target TEXT NOT NULL,
  source_text TEXT NOT NULL,
  translation TEXT NOT NULL,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, text_hash, target)
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  paper_id TEXT,
  status TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX idx_jobs_created ON jobs(created_at);

CREATE TABLE job_logs (
  id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  line TEXT NOT NULL
);
CREATE INDEX idx_job_logs ON job_logs(job_id, id);

CREATE TABLE ai_usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  month TEXT NOT NULL,
  task_type TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  estimated INTEGER NOT NULL DEFAULT 0,
  paper_id TEXT,
  job_id TEXT
);
CREATE INDEX idx_usage_month ON ai_usage(month);

CREATE TABLE ai_requests (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  task_type TEXT NOT NULL,
  model TEXT NOT NULL,
  paper_id TEXT,
  passage_ords_json TEXT NOT NULL DEFAULT '[]',
  passage_count INTEGER NOT NULL DEFAULT 0,
  chars_sent INTEGER NOT NULL DEFAULT 0,
  includes_json TEXT NOT NULL DEFAULT '{}',
  summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
"""

MIGRATIONS = [SCHEMA_V1]

# ---- 功能模块自注册的表结构 ----
# 核心结构（上面的 SCHEMA_V1）用线性迁移；各功能模块（补充材料、代码、发现、知识库…）通过
# register_schema() 注册自己的 `CREATE TABLE IF NOT EXISTS …` 脚本与列补丁，
# 启动时幂等执行，因此多个模块可以并行开发，不会争抢同一个迁移序号。
_MODULE_SCHEMAS: dict[str, str] = {}
_MODULE_COLUMNS: list[tuple[str, str, str, str]] = []  # (模块名, 表, 列, DDL 片段)


def register_schema(module: str, sql: str) -> None:
    """注册模块的建表脚本（必须幂等：CREATE TABLE/INDEX IF NOT EXISTS）。同名模块重复注册会覆盖。"""
    _MODULE_SCHEMAS[module] = sql


def register_column(module: str, table: str, column: str, ddl: str) -> None:
    """给已有表追加列（如 papers.parent_id）。ddl 形如 'TEXT REFERENCES papers(id)'。已存在则跳过。"""
    entry = (module, table, column, ddl)
    if entry not in _MODULE_COLUMNS:
        _MODULE_COLUMNS.append(entry)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for i in range(version, len(MIGRATIONS)):
                conn.executescript(MIGRATIONS[i])
                conn.execute(f"PRAGMA user_version = {i + 1}")
                conn.commit()
            def add_columns() -> None:
                for _module, table, column, ddl in _MODULE_COLUMNS:
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if cols and column not in cols:  # 表还不存在（由模块自己建）时跳过，建好后再补一次
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

            add_columns()  # 先补列：模块的建表脚本里可能给这些列建索引
            for sql in _MODULE_SCHEMAS.values():
                conn.executescript(sql)
            add_columns()
            conn.commit()
        finally:
            conn.close()
