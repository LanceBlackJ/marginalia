"""论文详情页要用的汇总：页数、图表公式数量、笔记标注数量、目录、关键词、封面。数字都来自已有数据，没有的就是 None。"""
from __future__ import annotations

import re
from pathlib import Path

from ..context import AppContext
from ..parsing import render
from ..references import store as ref_store
from ..vision import table_batch
from ..util import ApiError
from . import repo

KEYWORDS_RE = re.compile(r"^\s*(?:keywords?|key\s*words?|index terms|关键词|关键字)\s*[:：\-—  ]*\s*(.+)$", re.I | re.S)
COVER_DPI = 110


def _keywords(app: AppContext, paper_id: str) -> list[str]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT text FROM passages WHERE paper_id = ? AND page <= 2 AND kind = 'paragraph' ORDER BY ord LIMIT 60",
                            (paper_id,)).fetchall()
    for r in rows:
        t = (r["text"] or "").strip()
        m = KEYWORDS_RE.match(t) if len(t) < 400 else None
        if m:
            parts = [p.strip(" .;；") for p in re.split(r"[,;，；·•]|\s{2,}", m.group(1)) if p.strip(" .;；")]
            return [p for p in parts if 1 < len(p) <= 60][:12]
    return []


def _logical_figures(rows) -> list[dict]:
    """一张图常被检测成几个子图区域：按图注合并成一张图（区域取并集）。没有图注的区域不算一张图。"""
    out: dict[str, dict] = {}
    for r in rows:
        if not r["label"]:
            continue
        key = f'{r["label"]}|{r["page"]}'
        box = [r["x0"], r["y0"], r["x1"], r["y1"]]
        if key not in out:
            out[key] = {"id": r["id"], "page": r["page"], "label": r["label"], "caption": (r["caption"] or "")[:160], "bbox": box}
        else:
            b = out[key]["bbox"]
            out[key]["bbox"] = [min(b[0], box[0]), min(b[1], box[1]), max(b[2], box[2]), max(b[3], box[3])]
    return list(out.values())


def _reference_count(app: AppContext, paper_id: str) -> int | None:
    try:
        n = int(ref_store.load_references(app, paper_id).get("count") or 0)
    except Exception:  # noqa: BLE001  参考文献解析失败不影响详情页
        return None
    return n or None


def _tables(app: AppContext, paper_id: str) -> list[dict]:
    """带 “Table N” 图注的表（和阅读页识别表格用的是同一套检测）；id 是图注的段落序号，用来取表格截图。"""
    try:
        found = table_batch.tables_for(app, paper_id)
    except Exception:  # noqa: BLE001
        return []
    return [{"id": t["caption_ord"], "page": t["page"], "label": t["label"], "caption": (t["caption"] or "")[:160]} for t in found
            if t.get("kind", "table") == "table"]


def quality_notes(detail: dict, out: dict) -> list[str]:
    """解析里哪些地方可能不准：只说有证据的（没取到摘要、没认出参考文献、表格没读出来……），让你知道哪里该对照原文。"""
    notes: list[str] = []
    pages = out.get("pages") or 0
    if detail.get("status") != "ready":
        return notes
    if not (detail.get("abstract") or "").strip():
        notes.append("没有识别到摘要")
    if not detail.get("authors"):
        notes.append("没有识别到作者")
    if pages >= 4 and not (out.get("references") or 0):
        notes.append("没有识别到参考文献，引用悬停和文献查找可能不可用")
    elif pages >= 6 and 0 < (out.get("references") or 0) < 5:
        notes.append(f"只识别到 {out['references']} 条参考文献，可能不完整")
    if len(out.get("outline") or []) == 0 and pages >= 4:
        notes.append("没有识别到章节目录")
    return notes


def paper_profile(app: AppContext, paper_id: str) -> dict:
    detail = repo.get_detail(app, paper_id)
    with app.db.session() as conn:
        def count(sql: str) -> int:
            return int(conn.execute(sql, (paper_id,)).fetchone()[0])

        figs = conn.execute(
            "SELECT f.id, f.page, f.kind, f.label, f.x0, f.y0, f.x1, f.y1, c.text AS caption FROM figures f "
            "LEFT JOIN passages c ON c.paper_id = f.paper_id AND c.ord = f.caption_ord WHERE f.paper_id = ? ORDER BY f.page, f.y0, f.x0",
            (paper_id,)).fetchall()
        outline = conn.execute("SELECT title, level, page FROM sections WHERE paper_id = ? AND level <= 2 ORDER BY ord", (paper_id,)).fetchall()
        out = {
            "id": paper_id,
            "pages": detail.get("page_count"),
            "equations": count("SELECT COUNT(*) FROM equations WHERE paper_id = ?"),
            "notes": count("SELECT COUNT(*) FROM notes WHERE paper_id = ?"),
            "annotations": count("SELECT COUNT(*) FROM annotations WHERE paper_id = ?"),
            "bookmarks": count("SELECT COUNT(*) FROM bookmarks WHERE paper_id = ?"),
            "cards": count("SELECT COUNT(*) FROM insight_cards WHERE paper_id = ?"),
            "references": _reference_count(app, paper_id),
            "figures": _logical_figures([r for r in figs if r["kind"] == "figure"]),
            "tables": _tables(app, paper_id),
            "outline": [{"title": r["title"], "level": r["level"], "page": r["page"]} for r in outline
                        if r["title"] not in ("首部信息", "正文")][:40],
        }
    out["keywords"] = _keywords(app, paper_id)
    out["quality_notes"] = quality_notes(detail, out)
    return out


def cover_png(app: AppContext, paper_id: str) -> bytes:
    """第一页的小缩略图（本地渲染并缓存在论文目录里，PDF 更新后重新生成）。"""
    detail = repo.get_detail(app, paper_id)
    pdf = app.paths.paper_dir(paper_id) / "original.pdf"
    if not detail["has_pdf"] or not pdf.is_file():
        raise ApiError("not_found", "该论文没有可用的 PDF 文件", 404)
    cache: Path = app.paths.paper_dir(paper_id) / "cover110.png"
    if cache.is_file() and cache.stat().st_mtime >= pdf.stat().st_mtime:
        return cache.read_bytes()
    try:
        data = render.render_clip(pdf, 1, None, dpi=COVER_DPI)
    except render.RenderError as exc:
        raise ApiError("not_found", str(exc), 404) from exc
    try:
        cache.write_bytes(data)
    except OSError:
        pass
    return data
