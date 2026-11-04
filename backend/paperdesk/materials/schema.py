"""补充材料模块的表结构（通过 db.register_schema / register_column 自注册，不改核心迁移）。

数据模型：
- PDF 补充材料 = papers 表里的“子论文行”（role='supplement', parent_id=主论文），复用整套解析/阅读器/全文检索；
- 所有补充文件（含 PDF）在 supplementary_assets 有一行，PDF 通过 child_paper_id 指向子论文行。
"""
from __future__ import annotations

from ..db import register_column, register_schema

register_column("materials", "papers", "role", "TEXT NOT NULL DEFAULT 'paper'")
register_column("materials", "papers", "parent_id", "TEXT REFERENCES papers(id) ON DELETE CASCADE")

register_schema(
    "materials",
    """
CREATE INDEX IF NOT EXISTS idx_papers_parent ON papers(parent_id);

CREATE TABLE IF NOT EXISTS supplementary_assets (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  mime TEXT,
  size INTEGER,
  rel_path TEXT,
  source TEXT NOT NULL DEFAULT 'local',
  source_url TEXT,
  category TEXT NOT NULL DEFAULT '其他',
  category_source TEXT NOT NULL DEFAULT 'rule',
  summary TEXT,
  match_status TEXT NOT NULL DEFAULT '待确认',
  match_basis TEXT,
  download_status TEXT NOT NULL DEFAULT 'done',
  download_error TEXT,
  analysis_status TEXT NOT NULL DEFAULT 'none',
  analysis_json TEXT,
  child_paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
  job_id TEXT,
  sha256 TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supp_paper ON supplementary_assets(paper_id, created_at);
""",
)
