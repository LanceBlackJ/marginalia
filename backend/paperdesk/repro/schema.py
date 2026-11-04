"""复现模块的表结构（通过 register_schema 自注册，幂等；所有表随论文删除级联清理）。"""
from __future__ import annotations

from ..db import register_schema

register_schema(
    "repro",
    """
CREATE TABLE IF NOT EXISTS repro_assessments (
  repo_id TEXT PRIMARY KEY REFERENCES code_repos(id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  status TEXT NOT NULL,                       -- ready | failed
  report_json TEXT,
  error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repro_plans (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  repo_id TEXT NOT NULL UNIQUE REFERENCES code_repos(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repro_steps (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES repro_plans(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  phase TEXT NOT NULL,                        -- env | data | build | smoke | run | compare | other
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  command TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL DEFAULT '',
  side_effects_json TEXT NOT NULL DEFAULT '[]',   -- 下载 | 安装 | 执行 | 编译 | 写文件 | 占用GPU
  risk TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  needs_approval INTEGER NOT NULL DEFAULT 0,
  approval TEXT NOT NULL DEFAULT 'none',      -- none | pending | approved | rejected
  done INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'generated',   -- generated | user
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repro_steps_plan ON repro_steps(plan_id, ord);

-- 追加式批准记录：谁（用户）在何时对哪一步、当时的命令内容做了什么决定
CREATE TABLE IF NOT EXISTS repro_approvals (
  id INTEGER PRIMARY KEY,
  step_id TEXT NOT NULL REFERENCES repro_steps(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL REFERENCES repro_plans(id) ON DELETE CASCADE,
  decision TEXT NOT NULL,                     -- approved | rejected | reset
  actor TEXT NOT NULL DEFAULT 'user',
  command_snapshot TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repro_targets (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  value REAL NOT NULL,
  std REAL,
  unit TEXT NOT NULL DEFAULT '',
  tol_rel REAL,                               -- 用户设定的相对容差（0.05 = 5%）
  tol_abs REAL,
  comparable INTEGER NOT NULL DEFAULT 1,      -- 用户可标记“指标定义不同，无法比较”
  page INTEGER,
  ord INTEGER,
  quote TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'user',        -- table | text | user
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repro_targets_paper ON repro_targets(paper_id);

CREATE TABLE IF NOT EXISTS repro_runs (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  repo_id TEXT REFERENCES code_repos(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  source_format TEXT NOT NULL,                -- csv | json | text
  metrics_json TEXT NOT NULL,                 -- [{name, value, std, n, raw}]
  env_json TEXT NOT NULL DEFAULT '{}',        -- 用户填写：python/框架版本/GPU/随机种子/commit/命令…
  notes TEXT NOT NULL DEFAULT '',
  conclusion TEXT NOT NULL DEFAULT '',        -- 用户自己写的结论；系统不替用户下结论
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repro_runs_paper ON repro_runs(paper_id);

CREATE TABLE IF NOT EXISTS repro_mappings (
  run_id TEXT NOT NULL REFERENCES repro_runs(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES repro_targets(id) ON DELETE CASCADE,
  metric TEXT NOT NULL,
  PRIMARY KEY (run_id, target_id)
);
-- 隔离执行记录：每次在 Docker 容器里运行一步都落一行（命令、镜像、是否联网、资源上限当时的值）
CREATE TABLE IF NOT EXISTS repro_exec_runs (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  repo_id TEXT NOT NULL REFERENCES code_repos(id) ON DELETE CASCADE,
  step_id TEXT,
  job_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  command TEXT NOT NULL,
  cwd TEXT NOT NULL DEFAULT '',
  image TEXT NOT NULL,
  network INTEGER NOT NULL DEFAULT 0,
  limits_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,                       -- queued | running | succeeded | failed | timeout | cancelled
  reason TEXT NOT NULL DEFAULT '',
  exit_code INTEGER,
  files_json TEXT NOT NULL DEFAULT '[]',
  imported_run_id TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_repro_exec_paper ON repro_exec_runs(paper_id, created_at);
""",
)
