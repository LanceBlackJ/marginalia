"""视觉理解模块：公式转写与解释、框架图结构化理解、实验图表与表格理解。

截图由 parsing.render 渲染（与解析共用 PyMuPDF 锁），交给视觉模型识别；识别结果缓存在数据库里，
所有向云端发送截图的调用都会进入隐私日志（includes.images=true）并计入用量。
"""
from ..db import register_schema

register_schema("vision", """
CREATE TABLE IF NOT EXISTS equation_latex (
  equation_id INTEGER PRIMARY KEY REFERENCES equations(id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL,
  latex TEXT,
  confidence TEXT,
  status TEXT NOT NULL,
  warnings_json TEXT,
  error TEXT,
  model TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS figure_analysis (
  figure_id INTEGER PRIMARY KEY REFERENCES figures(id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  data_json TEXT,
  mapping_json TEXT,
  analysis_json TEXT,
  evidence_json TEXT,
  verification_json TEXT,
  model TEXT,
  error TEXT,
  derived_ord INTEGER,
  job_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_figure_analysis_paper ON figure_analysis(paper_id);
""")
