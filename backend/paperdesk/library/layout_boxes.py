"""本地版面模型给每篇论文框出的表格 / 图 / 显示公式：检测一次、缓存在库里，解析和重新解析都用它补规则漏掉的对象。

流程：导入时先检测（单页约 0.6 秒，只在导入/重新解析时跑）再解析，所以新论文一次解析就用上；旧论文点“重新解析”时检测并缓存。
模型文件第一次用到时下载（约 75 MB，见 parsing/layout_model.py）；下载失败、缺运行库、检测出错都不影响解析——退回纯规则，
并在日志里留一条，不会让导入失败。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..context import AppContext
from ..db import register_schema
from ..parsing import layout_model
from ..util import dumps, loads, utcnow

log = logging.getLogger(__name__)

ENABLED = True  # 总开关（测试里关掉）

register_schema("layout_boxes", """
CREATE TABLE IF NOT EXISTS page_boxes (
  paper_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  boxes_json TEXT NOT NULL,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, page)
);
""")


def load(app: AppContext, paper_id: str) -> dict[int, list[dict]]:
    """缓存里这篇论文当前模型版本的检测结果；不完整（页数对不上）的不要。没有就返回 {}。"""
    try:
        with app.db.session() as conn:
            rows = conn.execute("SELECT page, boxes_json FROM page_boxes WHERE paper_id = ? AND model = ?",
                                (paper_id, layout_model.MODEL_VERSION)).fetchall()
            pages = conn.execute("SELECT COUNT(*) FROM page_info WHERE paper_id = ?", (paper_id,)).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("读不到版面检测缓存：%s", exc)
        return {}
    out = {r["page"]: loads(r["boxes_json"], []) for r in rows}
    if not out or (pages and len(out) < pages):
        return {}
    return out


def save(app: AppContext, paper_id: str, boxes: dict[int, list[dict]]) -> None:
    if not boxes:
        return
    now = utcnow()
    try:
        with app.db.session() as conn:
            conn.execute("DELETE FROM page_boxes WHERE paper_id = ?", (paper_id,))
            conn.executemany(
                "INSERT INTO page_boxes(paper_id, page, boxes_json, model, created_at) VALUES (?,?,?,?,?)",
                [(paper_id, int(p), dumps(b), layout_model.MODEL_VERSION, now) for p, b in boxes.items()],
            )
    except Exception as exc:  # noqa: BLE001  缓存只是为了下次不用再跑模型，写不进去不能让导入/重新解析失败
        log.warning("版面检测结果没能缓存：%s", exc)


def detect(app: AppContext, pdf: Path, say: Callable[[str], None] | None = None,
           check: Callable[[], None] | None = None) -> dict[int, list[dict]]:
    """检测一个 PDF 的所有页；必要时先下载模型。任何失败都返回 {}（退回规则解析）。"""
    if not ENABLED:
        return {}
    say = say or (lambda _m: None)
    data_dir = app.paths.data_dir
    try:
        if not layout_model.available(data_dir):
            if not layout_model.runtime_ok():
                return {}
            say("首次使用：下载版面模型（约 75 MB）…")
            if not layout_model.download(data_dir):
                log.warning("版面模型下载失败，这次按规则解析")
                say("版面模型下载失败，这次按规则解析（以后重新解析时会再试）")
                return {}
        say("检测表格、图和公式的位置 …")
        return layout_model.detect_pdf(pdf, data_dir, check=check)
    except Exception as exc:  # noqa: BLE001  检测只是增强，不能让导入失败
        if exc.__class__.__name__ == "JobCancelled":
            raise
        log.warning("版面模型检测失败，按规则解析：%s", exc)
        return {}


def ensure(app: AppContext, paper_id: str, pdf: Path, say: Callable[[str], None] | None = None,
           check: Callable[[], None] | None = None) -> dict[int, list[dict]]:
    """缓存里有就直接用；没有就检测并存起来。"""
    cached = load(app, paper_id)
    if cached:
        return cached
    boxes = detect(app, pdf, say, check)
    if boxes:
        save(app, paper_id, boxes)
    return boxes
