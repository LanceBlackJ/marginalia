"""代码理解模块的表结构（通过 register_schema 自注册，幂等）。"""
from __future__ import annotations

from ..db import register_schema

register_schema(
    "code",
    """
CREATE TABLE IF NOT EXISTS code_repos (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  origin TEXT NOT NULL,                       -- github | zip | folder | self_implemented
  authored_by TEXT NOT NULL DEFAULT 'author', -- author（论文作者的代码）| user（用户提供，来源未确认）| self（根据论文自行实现，非作者代码）
  url TEXT, owner TEXT, name TEXT, branch TEXT,
  commit_sha TEXT, commit_date TEXT, downloaded_at TEXT,
  size_bytes INTEGER, local_rel TEXT,
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | downloading | scanning | ready | failed
  error TEXT,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  overview_json TEXT,
  flow_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_repos_paper ON code_repos(paper_id);

CREATE TABLE IF NOT EXISTS code_files (
  id INTEGER PRIMARY KEY,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  language TEXT,
  size INTEGER NOT NULL DEFAULT 0,
  lines INTEGER NOT NULL DEFAULT 0,
  role TEXT NOT NULL DEFAULT 'other',         -- entry | config | dependency | data | model | train | test | util | doc | other
  skipped TEXT,                               -- 非空表示未提取内容：binary | too_large | unsafe
  UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS code_symbols (
  id INTEGER PRIMARY KEY,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  qualname TEXT NOT NULL,
  kind TEXT NOT NULL,                         -- function | method | class | module | main
  parent TEXT,
  line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL,
  signature TEXT,
  doc TEXT,
  language TEXT
);
CREATE INDEX IF NOT EXISTS idx_code_symbols_repo ON code_symbols(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_code_symbols_name ON code_symbols(repo_id, name);

CREATE TABLE IF NOT EXISTS code_calls (
  id INTEGER PRIMARY KEY,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  caller_id INTEGER NOT NULL REFERENCES code_symbols(id) ON DELETE CASCADE,
  callee_id INTEGER REFERENCES code_symbols(id) ON DELETE SET NULL,
  callee_name TEXT NOT NULL,
  line INTEGER NOT NULL,
  certainty TEXT NOT NULL                     -- resolved | name | ambiguous | external
);
CREATE INDEX IF NOT EXISTS idx_code_calls_caller ON code_calls(repo_id, caller_id, line);
CREATE INDEX IF NOT EXISTS idx_code_calls_callee ON code_calls(repo_id, callee_id);

CREATE TABLE IF NOT EXISTS code_params (
  id INTEGER PRIMARY KEY,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  line INTEGER NOT NULL,
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  origin TEXT NOT NULL,                       -- argparse | constant | kwarg | config
  context TEXT
);
CREATE INDEX IF NOT EXISTS idx_code_params_repo ON code_params(repo_id);

CREATE TABLE IF NOT EXISTS code_cards (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  target_json TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'streaming',
  model TEXT NOT NULL DEFAULT '',
  raw_markdown TEXT NOT NULL DEFAULT '',
  answer_json TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  verification_json TEXT,
  usage_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_cards_repo ON code_cards(repo_id, created_at);

CREATE TABLE IF NOT EXISTS paper_code_links (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  module_json TEXT NOT NULL,                  -- {name, description, passage_ords, pages, section}
  symbol_id INTEGER REFERENCES code_symbols(id) ON DELETE SET NULL,
  path TEXT NOT NULL,
  line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL,
  confidence TEXT NOT NULL,                   -- likely | possible
  method TEXT NOT NULL,                       -- lexical | llm
  basis_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'suggested',   -- suggested | confirmed | rejected
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pcl_repo ON paper_code_links(repo_id, status);

CREATE TABLE IF NOT EXISTS code_consistency (
  repo_id TEXT PRIMARY KEY REFERENCES code_repos(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  report_json TEXT,
  error TEXT,
  updated_at TEXT NOT NULL
);
""",
)
