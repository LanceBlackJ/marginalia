"""全文翻译：把论文正文（段落、图表标题）批量翻译成中文，存库后在“文本”视图里与原文对照。

要点：
- 译文按段落存进 `translations` 表，和划词翻译共用同一个缓存键（段落归一化文本的哈希），已经翻过的段落不会重复发送；
- 后台任务按批发送（每批约 2400 字、最多 8 段），用 `###N###` 标记对齐；模型漏段、串段时，缺的段落单独重译，仍失败的如实计为“未译”；
- 参考文献、公式、图内文字不翻译；已经是中文的段落跳过；
- 发往云端的原文同样记入隐私日志，用量记入用量统计。
"""
from __future__ import annotations

import hashlib
import re

from .. import reader
from ..context import AppContext
from ..jobs import JobContext, JobManager
from ..util import ApiError, utcnow
from . import prompts
from . import usage as usage_log
from .providers import LLMError
from .router import TaskType, choose_model

TRANSLATE_KINDS = ("paragraph", "caption")
MIN_CHARS = 20
BATCH_CHARS = 2400
BATCH_ITEMS = 8
_MARK = re.compile(r"^[ \t]*###(\d+)###[ \t]*$", re.M)

BATCH_NOTE = (
    "\n\n本次输入包含多个段落，每段以单独一行的“###N###”标记开头（N 为序号）。"
    "输出必须逐段对应：先写同样的“###N###”标记行，下一行起写该段译文；不要合并、遗漏或调换段落，不要输出标记以外的任何说明。"
)


def _digest(text: str) -> str:
    return hashlib.sha256(reader.normalize(text).encode()).hexdigest()[:32]


def _is_chinese(text: str) -> bool:
    letters = len(re.findall(r"[A-Za-z一-鿿]", text))
    return bool(letters) and len(re.findall(r"[一-鿿]", text)) / letters > 0.5


def skip_reason(text: str, kind: str, center: tuple[float, float] | None, regions: list[tuple[float, float, float, float]]) -> str | None:
    """这段文字不该翻译时返回原因（保守、可解释）；该翻译返回 None。

    只翻译“句子类”的段落：太短的标签、数字符号为主的碎片、落在图区域里的坐标轴/图例文字都不翻译。
    图注（caption）即使在图区域旁边也照常翻译。
    """
    t = text.strip()
    if len(t) < MIN_CHARS:
        return "太短"
    if _is_chinese(t):
        return "已是中文"
    compact = re.sub(r"\s+", "", t)
    letters = len(re.findall(r"[A-Za-z]", compact))
    if letters / max(1, len(compact)) < 0.5:
        return "数字或符号为主"
    if len(t.split()) < 4:
        return "不足 4 个词（标签类文字）"
    if kind == "paragraph" and center is not None and any(x0 <= center[0] <= x1 and y0 <= center[1] <= y1 for x0, y0, x1, y1 in regions):
        return "位于图区域内（坐标轴或图例文字）"
    return None


def _eligible(app: AppContext, paper_id: str, with_skipped: bool = False):
    with app.db.session() as conn:
        rows = conn.execute(
            f"SELECT ord, page, kind, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND kind IN ({','.join('?' * len(TRANSLATE_KINDS))}) ORDER BY ord",
            (paper_id, *TRANSLATE_KINDS),
        ).fetchall()
        figs = conn.execute("SELECT page, x0, y0, x1, y1 FROM figures WHERE paper_id = ? AND kind = 'figure'", (paper_id,)).fetchall()
    by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for f in figs:
        by_page.setdefault(f["page"], []).append((f["x0"], f["y0"], f["x1"], f["y1"]))
    out, skipped = [], 0
    for r in rows:
        center = ((r["x0"] + r["x1"]) / 2, (r["y0"] + r["y1"]) / 2)
        if skip_reason(r["text"], r["kind"], center, by_page.get(r["page"], [])):
            skipped += 1
            continue
        out.append({"ord": r["ord"], "page": r["page"], "text": r["text"], "digest": _digest(r["text"])})
    return (out, skipped) if with_skipped else out


CELL_BATCH_CHARS = 2000
CELL_BATCH_ITEMS = 40
CELL_NOTE = (
    "\n\n这些是表格里的单元格、表头，或论文和章节的标题，每个都很短。标题要译成简洁的中文标题。译文要简洁、像论文表格里的写法；数字、单位、模型名、数据集名、缩写（如 BERT、F1、GPU）保持原样，"
    "只翻译其中的英文说明词。"
)


def _cell_translatable(text: str) -> bool:
    t = " ".join(str(text).split())
    if len(t) < 2 or len(t) > 200 or _is_chinese(t):
        return False
    letters = len(re.findall(r"[A-Za-z]", t))
    return letters >= 3 and letters / len(re.sub(r"\s+", "", t)) >= 0.4 and not re.fullmatch(r"[\w.+\-/]*\d[\w.+\-/]*", t)


def _table_grids(app: AppContext, paper_id: str) -> dict[int, dict]:
    """识别好的表格：{表题序号: {columns, rows}}。"""
    import json

    with app.db.session() as conn:
        rows = conn.execute("SELECT caption_ord, data_json FROM table_analysis WHERE paper_id = ? AND status = 'ready' AND data_json IS NOT NULL", (paper_id,)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        try:
            d = json.loads(r["data_json"])
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("rows"), list):
            out[r["caption_ord"]] = {"columns": [str(c) for c in d.get("columns") or []], "rows": [[str(c) for c in row] for row in d["rows"] if isinstance(row, list)]}
    return out


_NO_TITLE = {"abstract", "正文", "首部信息", "references", "keywords"}


def _heading_texts(app: AppContext, paper_id: str) -> list[str]:
    """要翻译的标题：论文标题和各级章节标题（去重；“Abstract”这类固定的、已是中文的、太短的不翻）。"""
    with app.db.session() as conn:
        rows = conn.execute("SELECT title FROM sections WHERE paper_id = ? ORDER BY ord", (paper_id,)).fetchall()
        paper = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    out: list[str] = []
    for t in [paper["title"] if paper else "", *(r["title"] for r in rows)]:
        t = " ".join((t or "").split())
        if t and t not in out and t.lower() not in _NO_TITLE and len(re.findall(r"[A-Za-z]", t)) >= 3 and not _is_chinese(t):
            out.append(t)
    return out


def heading_translations(app: AppContext, paper_id: str, cache: dict[str, tuple[str, str]] | None = None) -> dict[str, str]:
    """已翻译的标题：{原文标题: 译文}。"""
    cache = cache if cache is not None else _cached(app, paper_id)
    return {t: cache[_digest(t)][0] for t in _heading_texts(app, paper_id) if _digest(t) in cache}


def _cells_todo(app: AppContext, paper_id: str) -> list[dict]:
    """表格里要翻译的单元格和各级标题（去重）：{ord: -1, text, digest}。"""
    seen: dict[str, dict] = {}
    for t in _heading_texts(app, paper_id):
        seen[t] = {"ord": -1, "page": 0, "text": t, "digest": _digest(t)}
    for g in _table_grids(app, paper_id).values():
        for c in [*g["columns"], *(c for row in g["rows"] for c in row)]:
            t = " ".join(c.split())
            if t and t not in seen and _cell_translatable(t):
                seen[t] = {"ord": -1, "page": 0, "text": t, "digest": _digest(t)}
    return list(seen.values())


def table_translations(app: AppContext, paper_id: str, cache: dict[str, tuple[str, str]] | None = None) -> dict[str, dict]:
    """已翻译的表格：{表题序号: {columns: [译文或 null], rows: [[译文或 null]]}}；一个单元格都没译的表不出现。"""
    cache = cache if cache is not None else _cached(app, paper_id)

    def tr(c: str) -> str | None:
        t = " ".join(c.split())
        if not t or not _cell_translatable(t):
            return None
        hit = cache.get(_digest(t))
        return hit[0] if hit else None

    out: dict[str, dict] = {}
    for ord_, g in _table_grids(app, paper_id).items():
        cols = [tr(c) for c in g["columns"]]
        rows = [[tr(c) for c in row] for row in g["rows"]]
        if any(cols) or any(any(r) for r in rows):
            out[str(ord_)] = {"columns": cols, "rows": rows}
    return out


def _cached(app: AppContext, paper_id: str) -> dict[str, tuple[str, str]]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT text_hash, translation, model FROM translations WHERE paper_id = ? AND target = 'zh'", (paper_id,)
        ).fetchall()
    return {r["text_hash"]: (r["translation"], r["model"]) for r in rows}


def status(app: AppContext, paper_id: str, with_items: bool = True) -> dict:
    """翻译进度与已有译文：{total, done, complete, model, items:[{ord, translation}]}。"""
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
    todo, skipped = _eligible(app, paper_id, with_skipped=True)
    cache = _cached(app, paper_id)
    items, models = [], set()
    for p in todo:
        hit = cache.get(p["digest"])
        if hit:
            models.add(hit[1])
            if with_items:
                items.append({"ord": p["ord"], "translation": hit[0]})
    done = sum(1 for p in todo if p["digest"] in cache)
    cells = _cells_todo(app, paper_id)
    cells_done = sum(1 for c in cells if c["digest"] in cache)
    return {
        "total": len(todo), "done": done, "complete": bool(todo) and done >= len(todo),
        "models": sorted(models), "skipped": skipped, "items": items, "revision": reader.revision(app, paper_id),
        "cells_total": len(cells), "cells_done": cells_done,
        "tables": table_translations(app, paper_id, cache) if with_items else {},
        "headings": heading_translations(app, paper_id, cache) if with_items else {},
    }


def enqueue(app: AppContext, paper_id: str) -> str | None:
    """还有没翻译的段落时创建后台任务；已经全部翻完则返回 None。"""
    st = status(app, paper_id, with_items=False)
    if (not st["total"] or st["complete"]) and st["cells_done"] >= st["cells_total"]:
        return None
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    return app.jobs.submit("translate_paper", f"全文翻译：{title[:40]}", {"paper_id": paper_id}, paper_id)


def _batches(todo: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for p in todo:
        if cur and (size + len(p["text"]) > BATCH_CHARS or len(cur) >= BATCH_ITEMS):
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p["text"])
    if cur:
        out.append(cur)
    return out


def _cell_batches(cells: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for c in cells:
        if cur and (size + len(c["text"]) > CELL_BATCH_CHARS or len(cur) >= CELL_BATCH_ITEMS):
            out.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += len(c["text"])
    if cur:
        out.append(cur)
    return out


def _parse(text: str, n: int) -> dict[int, str]:
    """把带“###N###”标记的输出拆成 {序号: 译文}；序号越界或译文为空的丢弃。"""
    parts = _MARK.split(text)
    got: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        idx, body = int(parts[i]), parts[i + 1].strip()
        if 1 <= idx <= n and body and idx not in got:
            got[idx] = body
    return got


async def _run(app: AppContext, ctx: JobContext, paper_id: str) -> dict:
    from .service import AIService

    svc = AIService(app)
    try:
        provider, cfg = svc.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    choice = choose_model(TaskType.TRANSLATE, cfg)
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    cache = _cached(app, paper_id)
    todo = [p for p in _eligible(app, paper_id) if p["digest"] not in cache]
    cells = [c for c in _cells_todo(app, paper_id) if c["digest"] not in cache]
    if not todo and not cells:
        return {"translated": 0, "failed": 0}
    chars = sum(len(p["text"]) for p in todo) + sum(len(c["text"]) for c in cells)
    usage_log.record_request(
        app, task_type=TaskType.TRANSLATE.value, model=choice.model, paper_id=paper_id, passage_ords=[p["ord"] for p in todo[:2000]],
        chars_sent=chars, includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
        summary=f"发送了 {len(todo)} 段原文和 {len(cells)} 个表格单元格（约 {chars:,} 字）用于全文翻译",
    )

    def save(p: dict, out: str) -> None:
        with app.db.session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO translations(paper_id, text_hash, target, source_text, translation, model, created_at) "
                "VALUES (?,?, 'zh', ?,?,?,?)", (paper_id, p["digest"], p["text"], out, choice.model, utcnow()),
            )

    async def call(messages: list[dict]) -> str:
        res = await provider.complete(choice.model, messages, temperature=0, max_tokens=cfg["max_output_tokens"])
        svc._log_usage(TaskType.TRANSLATE, choice.model, res.usage, messages, res.text, paper_id, ctx.job_id)
        return res.text

    done = failed = 0
    for batch in _batches(todo):
        ctx.check_cancelled()
        body = "\n\n".join(f"###{i}###\n{p['text']}" for i, p in enumerate(batch, 1))
        messages = prompts.build_translate_messages(body, None, title)
        messages[0] = {"role": "system", "content": messages[0]["content"] + BATCH_NOTE}
        got: dict[int, str] = {}
        try:
            got = _parse(await call(messages), len(batch))
        except LLMError as err:
            if not err.retriable and err.code in ("auth_failed", "no_api_key"):
                raise ApiError(err.code, err.message) from err
        for i, p in enumerate(batch, 1):  # 漏段/串段：缺的单独重译
            out = got.get(i)
            if not out:
                try:
                    out = (await call(prompts.build_translate_messages(p["text"], None, title))).strip()
                except LLMError:
                    out = ""
            if out:
                save(p, out)
                done += 1
            else:
                failed += 1
        ctx.progress((done + failed) / len(todo), f"已翻译 {done}/{len(todo)} 段" + (f"，{failed} 段失败" if failed else ""))
    # 表格单元格：很短，一批多放一些
    cell_done = 0
    for batch in _cell_batches(cells):
        ctx.check_cancelled()
        body = "\n\n".join(f"###{i}###\n{c['text']}" for i, c in enumerate(batch, 1))
        messages = prompts.build_translate_messages(body, "表格", title)
        messages[0] = {"role": "system", "content": messages[0]["content"] + BATCH_NOTE + CELL_NOTE}
        try:
            got = _parse(await call(messages), len(batch))
        except LLMError as err:
            if not err.retriable and err.code in ("auth_failed", "no_api_key"):
                raise ApiError(err.code, err.message) from err
            got = {}
        for i, c in enumerate(batch, 1):
            if got.get(i):
                save(c, got[i])
                cell_done += 1
        ctx.progress(min(1.0, (done + failed + cell_done) / max(1, len(todo) + len(cells))), f"已翻译 {done} 段正文和 {cell_done} 个表格单元格")
    if failed and not done and not cell_done:
        raise ApiError("provider_error", "全文翻译没有成功翻译任何段落，请检查模型设置后重试。")
    return {"translated": done, "failed": failed, "table_cells": cell_done}


def handle_translate_paper(ctx: JobContext) -> dict:
    return ctx.run_async(_run(ctx.app, ctx, ctx.payload["paper_id"]))


def register(manager: JobManager) -> None:
    manager.register("translate_paper", handle_translate_paper)
