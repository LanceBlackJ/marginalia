"""每页用视觉模型划分版面：导入后先让视觉模型看每一页的整页截图，把页面划成区域（标题、作者、摘要、小节标题、正文栏、图注、公式、
参考文献、侧栏、页眉页脚……），连同阅读顺序存下来；再用这个划分重新解析（见 parsing.layout_hints）。

为什么让模型做这件事：栏、侧栏、标题、参考文献的边界是“看一眼就知道”的版面问题，规则只能靠字号、坐标去猜，换个期刊的版式就错。
文字本身（字符、公式上下标、坐标）仍然来自 PDF 文字层：模型的区域框有误差，解析时按文字片段的中心点归属，
归不进任何框的片段太多的页整页不采用、回到规则解析。所以模型错了最多是这一页退回规则，不会把文字弄丢。

流程（后台任务 layout_ai）：逐页渲染低分辨率整页图 → 模型返回区域 JSON（0–1000 的网格坐标）→ 清洗后按页缓存在 page_layout 表 →
全部完成后自动重新解析这篇论文（用现成的“重新解析”：笔记、标注等按位置重新映射，可一键撤销）。
没有配置视觉模型时完全不启用，和以前一样用规则解析。发送的截图进隐私日志（includes.images=true）并计入用量。
"""
from __future__ import annotations

import asyncio
import logging
import re

AUTO_ON_IMPORT = False  # 导入新论文时是否自动做视觉划分；换成更会定位的视觉模型并验证过再打开

from ..ai import usage as usage_log
from ..ai.providers import LLMError, image_part
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..db import register_column, register_schema
from ..jobs import JobContext, JobManager
from ..llmjson import extract_json
from ..parsing import layout_hints
from ..util import ApiError, dumps, loads, utcnow
from .images import paper_pdf_path
from ..parsing.render import RenderError, render_clip

JOB_TYPE = "layout_ai"
PROMPT_VERSION = 1
LONG_SIDE_PX = 1500
MAX_PAGES = 150
CONCURRENCY = 3

register_schema("layout_ai", """
CREATE TABLE IF NOT EXISTS page_layout (
  paper_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  status TEXT NOT NULL,
  regions_json TEXT,
  model TEXT,
  prompt_version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, page)
);
""")
# 这篇论文当前的解析结果是不是已经用视觉划分重建过（0 = 还是规则解析的）
register_column("layout_ai", "papers", "layout_ai", "INTEGER NOT NULL DEFAULT 0")

SYSTEM = (
    "你是学术论文 PDF 的版面分析器。给你一页论文的整页截图，请把这一页划分成若干区域，按阅读顺序列出，只输出 JSON。\n"
    "输出格式：{\"regions\":[{\"role\":\"角色\",\"box\":[x0,y0,x1,y1]}, ...]}，坐标是相对整张图的 0–1000 整数网格（x 向右、y 向下，左上角是 0,0）。\n"
    "角色只能是下面这些：\n"
    "- title 论文标题（只有首页有）；authors 作者名行；affiliation 单位、通讯作者、邮箱；abstract 摘要正文（含“Abstract”字样那一小段可以放进同一个框）；\n"
    "- heading 小节标题（“1 Introduction”“3.2 Training”“A.1 Details”这种单独成行的）；paragraph 正文段落；\n"
    "- caption 图注或表注（“Figure 1: …”“Table 2. …”）；figure 图（含图里的文字、坐标轴）；table 表格（含所有单元格）；formula 独立成行的公式（含右边的编号）；\n"
    "- reference 参考文献条目；footnote 脚注；sidebar 页面侧边的窄栏（期刊的 Edited by / Citation / 版权等）；header 页眉；footer 页脚和页码；other 其他。\n"
    "划分规则：\n"
    "1. 两栏的页面，左栏和右栏必须是各自独立的框，绝不能跨栏；一栏里连续的正文可以是一个框，但标题、图注、公式、图表要各自单独成框。\n"
    "2. 阅读顺序：先通栏的标题、作者、摘要，再左栏从上到下，再右栏从上到下；侧栏单独成框，放在它所在位置的阅读顺序里；图和它的图注相邻。\n"
    "3. 框要紧贴文字，不要框到别的区域的文字；页面空白不要框。所有可见的文字都必须落在某个框里。\n"
    "4. 参考文献列表整页按栏各框成一个 reference 框即可（不用一条一个框）。\n"
    "5. 不确定就选 paragraph 或 other，不要编造图里没有的区域。"
)


# ---------------------------------------------------------------- 缓存与读取
def load_hints(app: AppContext, paper_id: str) -> dict[int, list[dict]]:
    """{页码: 区域列表}：只取当前提示词版本、状态正常的页。没有缓存返回 {}。"""
    with app.db.session() as conn:
        rows = conn.execute("SELECT page, regions_json FROM page_layout WHERE paper_id = ? AND status = 'ok' AND prompt_version = ?",
                            (paper_id, PROMPT_VERSION)).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        regions = loads(r["regions_json"], [])
        if regions:
            out[r["page"]] = regions
    return out


def _done_pages(app: AppContext, paper_id: str) -> set[int]:
    with app.db.session() as conn:
        return {r["page"] for r in conn.execute("SELECT page FROM page_layout WHERE paper_id = ? AND prompt_version = ?", (paper_id, PROMPT_VERSION))}


def attempted(app: AppContext, paper_id: str) -> bool:
    """这篇论文以前跑过视觉划分（成功或失败）：失败过的不要每次打开都重试，免得把别的自动步骤一直挡住。"""
    if _done_pages(app, paper_id):
        return True
    with app.db.session() as conn:
        return conn.execute("SELECT 1 FROM jobs WHERE type = ? AND paper_id = ? AND status IN ('failed','cancelled') LIMIT 1", (JOB_TYPE, paper_id)).fetchone() is not None


def active(app: AppContext, paper_id: str) -> str | None:
    with app.db.session() as conn:
        r = conn.execute("SELECT id FROM jobs WHERE type = ? AND paper_id = ? AND status IN ('pending','running') LIMIT 1", (JOB_TYPE, paper_id)).fetchone()
    return r["id"] if r else None


def status(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        pages = conn.execute("SELECT COUNT(*) FROM page_layout WHERE paper_id = ? AND status = 'ok' AND prompt_version = ?", (paper_id, PROMPT_VERSION)).fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM page_layout WHERE paper_id = ? AND status != 'ok' AND prompt_version = ?", (paper_id, PROMPT_VERSION)).fetchone()[0]
        row = conn.execute("SELECT page_count, layout_ai FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "论文不存在", 404)
    return {"pages_total": row["page_count"] or 0, "pages_ok": pages, "pages_failed": failed, "applied": bool(row["layout_ai"]), "active_job_id": active(app, paper_id)}


def enqueue(app: AppContext, paper_id: str, force: bool = False) -> str | None:
    """创建视觉划分任务。已经有结果的页不重复问（force 才全部重来）；没有要做的返回 None。"""
    with app.db.session() as conn:
        row = conn.execute("SELECT title, has_pdf, page_count, role FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None or not row["has_pdf"]:
        return None
    if (row["page_count"] or 0) > MAX_PAGES:
        return None
    if (j := active(app, paper_id)):
        return j
    if not force and len(_done_pages(app, paper_id)) >= (row["page_count"] or 0) > 0:
        return None
    return app.jobs.submit(JOB_TYPE, f"视觉划分版面：{(row['title'] or '')[:40]}", {"paper_id": paper_id, "force": force}, paper_id)


# ---------------------------------------------------------------- 模型调用
def parse_reply(text: str) -> list[dict]:
    """模型的回答 → 清洗后的区域列表；格式不对或一个有效区域都没有返回 []。"""
    try:
        data = extract_json(text)
    except ValueError:
        data = _salvage(text)
        if not data:
            return []
    return layout_hints.normalize_regions(data)


_REGION_OBJ = re.compile(r'\{\s*"role"\s*:\s*"([a-z_]+)"\s*,\s*"box"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]\s*\}')


def _salvage(text: str) -> list[dict]:
    """回答被截断（页面很满、区域很多）时，把已经写完整的区域捞出来；最后一个写到一半的丢掉。"""
    out = []
    for m in _REGION_OBJ.finditer(text or ""):
        out.append({"role": m.group(1), "box": [float(m.group(i)) for i in range(2, 6)]})
    return out


async def _ask_page(service: AIService, provider, model: str, png: bytes, page: int, paper_id: str, app: AppContext) -> list[dict] | None:
    """一页的区域划分；模型临时出错返回 None（这一页退回规则）。密钥类错误上抛。"""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": f"这是论文的第 {page} 页。请划分版面，只输出 JSON。"}, image_part(png)]},
    ]
    usage_log.record_request(
        app, task_type="vision_layout", model=model, paper_id=paper_id, passage_ords=[], chars_sent=0,
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False, "images": True},
        summary=f"发送了第 {page} 页的整页截图（长边约 {LONG_SIDE_PX} 像素），用于划分版面（标题、栏、图注、参考文献等区域）；"
                "模型只给出区域框，文字仍取自 PDF 文字层，对不上的页不采用",
    )
    try:
        res = await provider.complete(model, messages, temperature=0, max_tokens=6000)
    except LLMError as err:
        if err.code in ("auth_failed", "no_api_key", "not_configured"):
            raise ApiError(err.code, err.message) from err
        logging.getLogger(__name__).warning("第 %s 页版面划分请求失败：%s %s", page, err.code, err.message)
        return None
    service._log_usage(TaskType.VISION, model, res.usage, messages, res.text, paper_id)
    regions = parse_reply(res.text)
    if not regions:
        logging.getLogger(__name__).warning("第 %s 页版面划分回答无法解析（%d 字）：%s", page, len(res.text or ""), (res.text or "")[:300].replace("\n", " "))
    return regions or None


def _render(app: AppContext, paper_id: str, page: int, w: float, h: float) -> bytes:
    dpi = int(max(50, min(200, LONG_SIDE_PX / (max(w, h, 1.0) / 72.0))))
    try:
        return render_clip(paper_pdf_path(app, paper_id), page, None, dpi)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc


async def _run(app: AppContext, ctx: JobContext, paper_id: str, force: bool) -> dict:
    service = AIService(app)
    provider, cfg = service.provider()
    model = choose_model(TaskType.VISION, cfg).model
    with app.db.session() as conn:
        sizes = {r["page"]: (r["width"], r["height"]) for r in conn.execute("SELECT page, width, height FROM page_info WHERE paper_id = ?", (paper_id,))}
    done = set() if force else _done_pages(app, paper_id)
    todo = [p for p in sorted(sizes) if p not in done]
    sem = asyncio.Semaphore(CONCURRENCY)
    finished = 0
    ok = failed = 0

    async def one(page: int) -> tuple[int, list[dict] | None]:
        async with sem:
            ctx.check_cancelled()
            w, h = sizes[page]
            png = await asyncio.to_thread(_render, app, paper_id, page, w, h)
            return page, await _ask_page(service, provider, model, png, page, paper_id, app)

    tasks = [asyncio.ensure_future(one(p)) for p in todo]
    try:
        for fut in asyncio.as_completed(tasks):
            page, regions = await fut
            with app.db.session() as conn:
                conn.execute(
                    "INSERT INTO page_layout(paper_id, page, status, regions_json, model, prompt_version, created_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(paper_id, page) DO UPDATE SET status=excluded.status, regions_json=excluded.regions_json, model=excluded.model, "
                    "prompt_version=excluded.prompt_version, created_at=excluded.created_at",
                    (paper_id, page, "ok" if regions else "failed", dumps(regions) if regions else None, model, PROMPT_VERSION, utcnow()),
                )
            ok += bool(regions)
            failed += not regions
            finished += 1
            ctx.progress(finished / max(1, len(todo)), f"已划分 {finished}/{len(todo)} 页")
    finally:
        for t in tasks:
            t.cancel()
    return {"pages": len(todo), "ok": ok, "failed": failed, "model": model}


def handle(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id = ctx.payload["paper_id"]
    result = ctx.run_async(_run(app, ctx, paper_id, bool(ctx.payload.get("force"))))
    # 划分好了就用它重新解析（任务的最后一步：重新解析任务启动时会取消这篇论文的其他任务）
    if load_hints(app, paper_id):
        from ..library import reparse

        try:
            result["reparse_job_id"] = reparse.enqueue(app, paper_id)
        except ApiError as exc:
            result["reparse_error"] = exc.message
    if not result.get("reparse_job_id"):
        # 没有可用的划分（模型没按格式回答、全部页失败），不会重新解析：接着做被推迟的公式、表格、索引等步骤
        from ..library import processing

        try:
            processing.start_auto(app, paper_id, layout=False)  # 这个任务自己还在“运行中”，不能再被当成“划分没做完”
        except ApiError:
            pass
    return result


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle)
