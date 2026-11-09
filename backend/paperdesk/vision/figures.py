"""图表理解（后台任务）：框架图结构化 / 实验图读图 / 表格转录。

流程：图区域截图 →（需要时先判断类型）→ 视觉模型抽取结构 → 后端校验（连线、文字层比对、数字降级）→
文本模型结合论文摘录做“有出处的解读”→ 核实引用与数字 → 入库；表格与框架图还会作为可检索段落写回论文。
截图发送记入隐私日志（includes.images=true）与用量。
"""
from __future__ import annotations

import re

from ..ai import evidence, retrieval, usage as usage_log
from ..ai.providers import LLMError, image_part
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..jobs import JobCancelled, JobContext, JobManager
from ..llmjson import extract_json, repair_truncated_json
from ..util import ApiError, dumps, loads, utcnow
from . import groups, table_check
from . import validate as V
from .images import crop_png_fit

FRAMEWORK_HINT = re.compile(r"framework|architecture|overview|pipeline|workflow|schematic|flow\s?chart|diagram|scheme|"
                            r"框架|架构|流程|示意|总览", re.I)
CHART_HINT = re.compile(r"performance|comparison|compar|result|accuracy|curve|ablation|versus|vs\.|\bscore|"
                        r"distribution|scaling|sensitivity|trend|实验|对比|结果|曲线|消融|性能", re.I)

CLASSIFY_SYSTEM = (
    "判断图片类型，只输出 JSON：{\"kind\":\"framework|chart|table|other\",\"reason\":\"一句话\"}。"
    "framework=模块/流程/架构示意图；chart=曲线、柱状、热力、散点等数据图（含多子图混合时取占主要面积的一类）；"
    "table=表格；other=照片、插图、公式等。"
)

FRAMEWORK_SYSTEM = """你是严谨的科研图表识别器，任务是把论文里的框架图/流程图转成结构化的模块与连线。
只描述图里实际可见的内容：
1. modules：图中每一个有文字标注的方框/模块/步骤/数据对象。label 必须逐字照抄图中文字（保持原语言与大小写），不要意译，不要自己起名。role 用一句话概括它在图里的作用，只写图或图注能直接支持的内容；不确定就留空。
2. flows：图中的箭头/连线。from、to 填 modules 里的 id；label 抄写箭头上的文字（没有则留空）；kind 为 data（数据流）、control（控制/反馈）、loss（损失/监督）或 other。方向看不清时不要猜，写进 uncertain。
3. groups：图里用大框/底色圈出来的分区（如 Encoder、Decoder），members 是其中的模块 id；没有就返回空数组。
4. 如果图里还有曲线图、柱状图等数据子图，只识别框架/流程部分，并在 uncertain 里说明忽略了数据子图。
5. 看不清、被遮挡、含糊的地方一律写进 uncertain，不要补全，不要编造。
6. 如果这张图根本不是框架/流程图，只返回 {"not_a_framework": true, "actual_kind": "chart|table|other"}。
只输出一个 JSON 对象，格式：
{"summary":"一句话：图的整体含义","modules":[{"id":"m1","label":"...","role":"...","kind":"input|process|model|output|data|other","group":""}],"flows":[{"from":"m1","to":"m2","label":"","kind":"data"}],"groups":[{"id":"g1","label":"...","members":["m1"]}],"uncertain":["..."]}"""

CHART_SYSTEM = """你是严谨的科研图表识别器，任务是读懂实验结果图（曲线、柱状、热力、散点、箱线等）。
1. 先分子图：图上有 (a)(b)(c) 等标记就逐个子图识别；没有就当作一个子图。
2. 每个子图给出：chart_type、title（图上有才写）、x_axis/y_axis（label、unit、图上标出的刻度范围）、series（图例里的每个系列名，逐字照抄）。
3. observations：只写肉眼可见的定性结论（趋势、谁高谁低、交叉、饱和、离群点），每个子图至多 4 条、每条一句话；不要在里面写读不准的具体数字。values 每个子图至多 8 个。整体输出要精简，子图很多时每个子图只写最重要的内容。
4. values：只有图上“直接印出来”的数字（数据标签、表格格子、图中文字）才能写 precision 为 exact，并原样抄写；靠坐标轴估读的数值必须写 precision 为 approx；读不出来的不要写。绝不推算、绝不补全。
5. 看不清、被遮挡、图例对不上的地方写进 unreadable。
只输出一个 JSON 对象：
{"panels":[{"label":"a","chart_type":"line|bar|heatmap|scatter|box|other","title":"","x_axis":{"label":"","unit":"","range":""},"y_axis":{"label":"","unit":"","range":""},"series":["..."],"observations":["..."],"values":[{"text":"这个数字说明什么","value":"0.52","precision":"exact|approx"}]}],"unreadable":["..."]}"""

TABLE_SYSTEM = """你是严谨的表格转录器。把图片里的表格逐格转成 JSON。
1. columns 是表头（多级表头用“上级 / 下级”合并成一行）；rows 是数据行，每行的单元格数必须等于 columns 数，合并单元格在每个位置都重复其内容。
2. 单元格里的文字、数字、符号（±、%、上标星号）逐字照抄；看不清的格子写 "?"，不要猜。
3. 表格外的脚注、说明写进 notes；表题写进 title（图里有才写）。
4. 不要增删行列，不要计算、汇总或重排。
只输出一个 JSON 对象：{"title":"","columns":["..."],"rows":[["..."]],"notes":""}"""

ANALYSIS_RULES = """规则：
1. 关于“论文怎么说”的事实只能依据【论文摘录】，每个事实后写 [P序号]，绝不引用摘录中不存在的编号。
2. 关于“图/表里画了什么”的事实只能依据【图的识别结果】（视觉模型从图像识别，后端已校验），不要补充识别结果里没有的内容。
3. 数字：只能使用【图的识别结果】里标为 exact 的数字或摘录里出现的数字；标为 approx 的值必须写成“约”并说明是读图估计；绝不自己推算或编造图中读不到的数值。
4. 论文没写、图里看不出的内容，直接写“摘录/图中没有足够信息确认”。
5. 你自己的推断放进 inferences，并写明依据；不要把推断混进其他字段。
6. 用中文，专业术语首次出现时附英文。只输出一个 JSON 对象，不要任何其它文字。"""

FRAMEWORK_ANALYSIS_USER = """【任务】结合论文正文，解释这张框架图。
JSON 格式：
{"overview":"这张图整体展示什么（1~3 句，带 [P序号]）","flow_explanation":"按图中连线说明数据/信息怎么流动（3~6 句，带 [P序号]）","modules":[{"id":"识别结果里的模块 id","description":"论文正文如何描述这个模块（至多 2 句，带 [P序号]）；正文里找不到就填 null","ords":[12]}],"inferences":[{"text":"你的推断","basis_ords":[12]}],"limitations":["这张图/这次解读的局限，如图中看不清的部分"]}"""

CHART_ANALYSIS_USER = """【任务】结合论文正文，解读这张实验图/表。
JSON 格式：
{"direct":"这张图/表在比较什么、最主要的结论（1~3 句，带 [P序号]）","points":[{"text":"一条具体观察（图中可见的现象，或论文对它的说法）","ords":[12]}],"paper_claims":[{"text":"论文正文对这张图/表做出的结论性说法 [P序号]","supported":"yes|partly|no|unclear","note":"图/表中能否支持它，不一致或无法判断的地方"}],"inferences":[{"text":"你的推断","basis_ords":[12]}],"limitations":["局限，如数值只能估读、子图看不清"]}"""


# ---------- 存取 ----------
def _upsert(app: AppContext, figure_id: int, paper_id: str, **fields) -> None:
    """原子 upsert：入队线程与任务线程可能同时写同一行。更新时只改显式给出的字段。"""
    now = utcnow()
    row = {"figure_id": figure_id, "paper_id": paper_id, "kind": "unknown", "status": "pending",
           "created_at": now, "updated_at": now, **fields}
    cols = list(row)
    updates = ", ".join(f"{c} = excluded.{c}" for c in [*fields, "updated_at"])
    with app.db.session() as conn:
        conn.execute(
            f"INSERT INTO figure_analysis({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(figure_id) DO UPDATE SET {updates}", list(row.values()),
        )


def load_figure(app: AppContext, paper_id: str, figure_id: int) -> dict:
    """返回“逻辑图”（解析器切碎的子图已归并）：id 为主区域 id，bbox 为合并外框，source_text 为图的文字层。"""
    g = groups.group_for(app, paper_id, figure_id)
    if g is None:
        raise ApiError("not_found", "图表不存在", 404)
    x0, y0, x1, y1 = g["bbox"]
    with app.db.session() as conn:
        sec = None
        if g["caption_ord"] is not None:
            r = conn.execute("SELECT section_id FROM passages WHERE paper_id = ? AND ord = ?", (paper_id, g["caption_ord"])).fetchone()
            sec = r["section_id"] if r else None
        parts: list[str] = []
        for r in conn.execute(
            "SELECT i.text FROM figures f JOIN passages i ON i.paper_id = f.paper_id AND i.ord = f.inner_ord "
            f"WHERE f.paper_id = ? AND f.id IN ({','.join('?' * len(g['member_ids']))})", (paper_id, *g["member_ids"]),
        ).fetchall():
            parts.append(r["text"])
        # 图的文字层：落在合并外框内的 figure_text 段落
        for p in conn.execute(
            "SELECT text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND page = ? AND kind = 'figure_text'",
            (paper_id, g["page"]),
        ).fetchall():
            cx, cy = (p["x0"] + p["x1"]) / 2, (p["y0"] + p["y1"]) / 2
            if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2 and p["text"] not in parts:
                parts.append(p["text"])
    return {
        "id": g["id"], "member_ids": g["member_ids"], "page": g["page"], "bbox": (x0, y0, x1, y1), "kind": g["kind"],
        "label": g["label"], "caption_ord": g["caption_ord"], "caption": g["caption"], "section_id": sec,
        "source_text": "\n".join(x for x in parts if x),
    }


def get_analysis(app: AppContext, paper_id: str, figure_id: int) -> dict:
    figure_id = load_figure(app, paper_id, figure_id)["id"]  # 子区域 id 归一到逻辑图的主区域 id
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM figure_analysis WHERE figure_id = ?", (figure_id,)).fetchone()
    if r is None:
        return {"figure_id": figure_id, "status": "none", "kind": None, "job_id": None, "model": None, "error": None,
                "updated_at": None, "data": None, "mapping": None, "analysis": None, "evidence": [], "verification": None,
                "warnings": [], "derived_ord": None}
    ready = r["status"] == "ready"
    data = loads(r["data_json"]) if ready else None
    return {
        "figure_id": figure_id, "status": r["status"], "kind": r["kind"] if r["kind"] != "unknown" else None,
        "job_id": r["job_id"], "model": r["model"], "error": r["error"], "updated_at": r["updated_at"],
        "data": data, "mapping": loads(r["mapping_json"]) if ready else None,
        "analysis": loads(r["analysis_json"]) if ready else None,
        "evidence": loads(r["evidence_json"], []) if ready else [],
        "verification": loads(r["verification_json"]) if ready else None,
        "warnings": (data or {}).get("warnings", []) if ready else [], "derived_ord": r["derived_ord"] if ready else None,
    }


def list_figures(app: AppContext, paper_id: str) -> list[dict]:
    """逻辑图列表（已归并子区域）+ 理解状态。"""
    with app.db.session() as conn:
        st = {r["figure_id"]: r for r in conn.execute(
            "SELECT figure_id, status, kind, job_id FROM figure_analysis WHERE paper_id = ?", (paper_id,)).fetchall()}
    out = []
    for g in groups.figure_groups(app, paper_id):
        r = st.get(g["id"])
        out.append({**g, "analysis_status": r["status"] if r else "none",
                    "analysis_kind": (r["kind"] if r and r["kind"] != "unknown" else None), "job_id": r["job_id"] if r else None})
    return out


def list_status(app: AppContext, paper_id: str) -> dict:
    with app.db.session() as conn:
        figs = conn.execute("SELECT figure_id, status, kind, job_id FROM figure_analysis WHERE paper_id = ?", (paper_id,)).fetchall()
        eqs = conn.execute("SELECT equation_id, status, confidence FROM equation_latex WHERE paper_id = ?", (paper_id,)).fetchall()
    return {
        "figures": [{"figure_id": r["figure_id"], "status": r["status"], "kind": r["kind"], "job_id": r["job_id"]} for r in figs],
        "equations": [{"equation_id": r["equation_id"], "status": r["status"], "confidence": r["confidence"]} for r in eqs],
    }


def delete_analysis(app: AppContext, paper_id: str, figure_id: int) -> None:
    figure_id = load_figure(app, paper_id, figure_id)["id"]
    with app.db.session() as conn:
        r = conn.execute("SELECT derived_ord FROM figure_analysis WHERE figure_id = ?", (figure_id,)).fetchone()
        if r and r["derived_ord"] is not None:
            conn.execute("DELETE FROM passages WHERE paper_id = ? AND ord = ? AND kind IN ('table_data','figure_data')",
                         (paper_id, r["derived_ord"]))
        conn.execute("DELETE FROM figure_analysis WHERE figure_id = ?", (figure_id,))


def enqueue(app: AppContext, paper_id: str, figure_id: int, *, force: bool = False, kind: str | None = None) -> dict:
    fig = load_figure(app, paper_id, figure_id)
    figure_id = fig["id"]
    service = AIService(app)
    try:
        _provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    choose_model(TaskType.VISION, cfg)  # 未配置视觉模型时抛 vision_not_configured
    with app.db.session() as conn:
        row = conn.execute("SELECT status, job_id FROM figure_analysis WHERE figure_id = ?", (figure_id,)).fetchone()
    if row and row["status"] in ("pending", "running") and row["job_id"]:
        return {"job_id": row["job_id"], "status": row["status"]}
    if row and row["status"] == "ready" and not force:
        return {"job_id": None, "status": "ready"}
    _upsert(app, figure_id, paper_id, status="pending", kind=kind or "unknown", error=None, job_id=None)
    job_id = app.jobs.submit("vision_figure", f"图表理解：{fig['label'] or '第 ' + str(fig['page']) + ' 页的图'}",
                             {"paper_id": paper_id, "figure_id": figure_id, "kind": kind}, paper_id)
    with app.db.session() as conn:
        conn.execute("UPDATE figure_analysis SET job_id = ? WHERE figure_id = ?", (job_id, figure_id))
    return {"job_id": job_id, "status": "pending"}


# ---------- 视觉模型调用 ----------
def guess_kind(fig: dict) -> str | None:
    if fig["kind"] == "table":
        return "table"
    cap = fig["caption"][:300]
    f, c = bool(FRAMEWORK_HINT.search(cap)), bool(CHART_HINT.search(cap))
    if f and not c:
        return "framework"
    if c and not f:
        return "chart"
    return None


TRUNCATED_WARNING = "视觉模型的输出被长度限制截断，已丢弃不完整的最后部分：后面的子图/模块/行可能缺失，请对照原图。"
BRIEF_HINT = ("上一次输出太长被截断了。请重新输出，并且更精简：每个子图/部分只保留最重要的内容（observations 至多 3 条、values 至多 6 个、"
              "说明文字尽量短），其余省略；仍然只输出一个完整的 JSON 对象。")


TEXT_BRIEF_HINT = ("上一次输出太长被截断了。请重新输出一个更精简的 JSON：points 至多 4 条、其余每项说明至多 1 句，不要输出思考过程；"
                   "仍然只输出一个完整的 JSON 对象。")


def _parse_or_repair(res) -> tuple[dict | None, bool, str]:
    """解析模型输出。返回 (数据, 是否被截断后修复, 失败原因)。被长度限制截断的 JSON 会丢弃不完整的末尾后补全。"""
    err = ""
    try:
        data = extract_json(res.text)
        if isinstance(data, dict):
            return data, False, ""
        err = "输出不是 JSON 对象"
    except ValueError as exc:
        err = str(exc)
    truncated = (res.finish_reason or "") in ("length", "max_tokens")
    if truncated:
        try:
            data = repair_truncated_json(res.text)
            if isinstance(data, dict) and data:
                return data, True, ""
        except ValueError:
            pass
    return None, truncated, err


def _log_bad_output(job: JobContext, who: str, res, err: str, truncated: bool, tokens: int) -> None:
    head = " ".join((res.text or "").split())[:300]
    job.log(f"{who}输出无法解析（{err}；结束原因={res.finish_reason or '未知'}；输出约 {res.usage.completion_tokens} token，上限 {tokens}"
            f"{'；输出被长度限制截断' if truncated else ''}）。原始输出前 300 字：{head or '（空）'}")


def _fail_hint(err: str, truncated: bool) -> str:
    return "输出被长度限制截断，内容太多" if truncated else "输出不是合法的 JSON"


async def _vlm_json(app: AppContext, service: AIService, provider, model: str, *, paper_id: str, job: JobContext,
                    system: str, text: str, png: bytes, task_type: str, summary: str, max_tokens: int) -> dict:
    base = [{"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": text}, image_part(png)]}]
    messages = base
    tokens = max_tokens
    last_err, truncated = "", False
    fallback: dict | None = None  # 第一次被截断但还能用的部分结果：重试也失败时才使用
    for attempt in range(2):
        job.check_cancelled()
        usage_log.record_request(
            app, task_type=task_type, model=model, paper_id=paper_id, passage_ords=[], chars_sent=len(text) + len(system),
            includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False, "images": True},
            summary=summary + ("（输出被截断或格式有误，重试）" if attempt else ""),
        )
        try:
            res = await provider.complete(model, messages, temperature=0, max_tokens=tokens, json_mode=True)
        except LLMError:
            if fallback is not None:  # 重试时网关出错：仍然使用第一次可用的部分结果
                break
            raise
        service._log_usage(task_type, model, res.usage, messages, res.text, paper_id, job.job_id)
        data, truncated, last_err = _parse_or_repair(res)
        if data is not None and not truncated:
            return data
        if data is not None:  # 被截断，但丢掉不完整的末尾后还能用
            _log_bad_output(job, "视觉模型", res, "输出被截断，已修复", True, tokens)
            data["_truncated"] = True
            if attempt == 1:
                job.log("重试后输出仍被截断，使用丢弃不完整末尾后的结果，可能缺内容")
                return data
            fallback = data
        else:
            _log_bad_output(job, "视觉模型", res, last_err, truncated, tokens)
        if truncated:
            # 输出被截断：不要把半截输出再喂回去，换成“更精简”的要求并放宽长度上限后重来
            tokens = min(int(tokens * 1.6), 8000)
            messages = [base[0], {**base[1], "content": [*base[1]["content"], {"type": "text", "text": BRIEF_HINT}]}]
        else:
            messages = [*base, {"role": "assistant", "content": res.text[:4000]},
                        {"role": "user", "content": "上一次输出不是合法的 JSON 对象。请只输出一个 JSON 对象，不要任何其它文字。"}]
    if fallback is not None:
        job.log("重试失败，使用第一次丢弃不完整末尾后的结果，可能缺内容")
        return fallback
    raise ApiError("provider_error", "视觉模型多次返回了无法解析的结果：" + _fail_hint(last_err, truncated) + "。请重试或更换视觉模型。")


def _context_text(fig: dict) -> str:
    cap = fig["caption"][:900] or "（没有识别到图注）"
    inner = fig["source_text"][:1200]
    return (f"图编号：{fig['label'] or '未知'}（第 {fig['page']} 页）\n图注：{cap}\n"
            + (f"图内文字（PDF 文字层，可能不全；供对照拼写）：{inner}" if inner else "图内文字：PDF 文字层里没有（可能是位图）"))


async def _text_json(app: AppContext, service: AIService, provider, cfg: dict, *, paper_id: str, job: JobContext,
                     system: str, user: str, pack: retrieval.ContextPack, summary: str, task: TaskType) -> dict:
    choice = choose_model(task, cfg)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    usage_log.record_request(
        app, task_type=task.value, model=choice.model, paper_id=paper_id, passage_ords=pack.ords, chars_sent=len(system) + len(user),
        includes={"question": False, "selection": True, "notes": False, "supplements": False, "code": False, "images": False},
        summary=summary,
    )
    base = messages
    # 结构化解读的输出比普通问答长，带“思考”的强模型还会把预算花在推理上：至少给 6000 token
    model, tokens = choice.model, max(cfg["max_output_tokens"], 6000)
    err, truncated = "", False
    fallback: dict | None = None
    for attempt in range(2):
        job.check_cancelled()
        try:
            res = await provider.complete(model, messages, temperature=0.1, max_tokens=tokens, json_mode=True)
        except LLMError:
            if fallback is not None:  # 重试时网关出错：仍然使用第一次可用的部分结果
                break
            raise
        service._log_usage(task, model, res.usage, messages, res.text, paper_id, job.job_id)
        data, truncated, err = _parse_or_repair(res)
        if data is not None and not truncated:
            data["_model"] = model
            return data
        if data is not None:  # 被截断，但丢掉不完整的末尾后还能用
            _log_bad_output(job, "文本模型", res, "输出被截断，已修复", True, tokens)
            data["_truncated"] = True
            data["_model"] = model
            if attempt == 1:
                job.log("重试后输出仍被截断，使用丢弃不完整末尾后的结果，解读可能缺内容")
                return data
            fallback = data
        else:
            _log_bad_output(job, "文本模型", res, err, truncated, tokens)
        if truncated:
            # 带“思考”的强模型常把输出预算花在推理上：放宽上限，并改用不做长推理的快速模型重试
            tokens = min(max(tokens * 2, 6000), 8000 if tokens < 8000 else max(32000, tokens))  # 重试的额度只增不减（默认上限已是 16000）
            fast = cfg.get("fast_model") or ""
            if fast and fast != model:
                job.log(f"改用快速模型 {fast} 重试")
                model = fast
            messages = [*base, {"role": "user", "content": TEXT_BRIEF_HINT}]
        else:
            messages = [*base, {"role": "assistant", "content": res.text[:4000]},
                        {"role": "user", "content": "上一次输出不是合法的 JSON 对象。请只输出一个 JSON 对象，不要任何其它文字。"}]
    if fallback is not None:
        job.log("重试失败，使用第一次丢弃不完整末尾后的结果，解读可能缺内容")
        return fallback
    raise ApiError("provider_error", "模型多次返回了无法解析的解读结果：" + _fail_hint(err, truncated) + "。请重试或更换模型。")


def _head(pack: retrieval.ContextPack) -> str:
    return f"【论文】{pack.paper_title}\n【论文结构】\n{pack.render_outline()}\n\n【论文摘录】\n{pack.render_excerpts()}"


def _clean_list(items, key="text") -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            it = {key: it}
        if isinstance(it, dict) and V.clean(it.get(key)):
            out.append(it)
    return out


_BARE_REF = re.compile(r"(?<![\[【A-Za-z0-9_])P(\d+)(?![\d\]】])")


def bare_refs_to_pages(text: str, pages: dict[int, int]) -> str:
    """模型有时把段落编号写在方括号外（“论文P36指出…”）：只对真实存在的段落编号，换成读者看得懂的“第 N 页”；
    带方括号的 [P36] 是引用标记，保持原样（界面把它渲染成可点击的引用）。"""
    return _BARE_REF.sub(lambda m: f"第 {pages[int(m.group(1))]} 页" if int(m.group(1)) in pages else m.group(0), text or "")


def _ords(v, valid: set[int]) -> list[int]:
    return [o for o in dict.fromkeys(x for x in (v or []) if isinstance(x, int)) if o in valid]


def _finalize_texts(analysis: dict, pack: retrieval.ContextPack, allowed_extra: str) -> tuple[list[dict], dict]:
    """核实所有字段里的引用与数字；返回 (证据, 核实结论)。"""
    texts: list[str] = []
    listed: list[int] = []

    def collect(s):
        if s:
            texts.append(evidence.normalize_cites(str(s)))

    for k in ("direct", "overview", "flow_explanation"):
        collect(analysis.get(k))
    for p in analysis.get("points", []):
        collect(p.get("text"))
        listed += p.get("ords", [])
    for p in analysis.get("paper_claims", []):
        collect(p.get("text"))
        collect(p.get("note"))
    for p in analysis.get("inferences", []):
        collect(p.get("text"))
        listed += p.get("basis_ords", [])
    for m in analysis.get("module_descriptions", {}).values():
        collect(m.get("description"))
        listed += m.get("ords", [])
    ev, ver = evidence.verify_ords(texts, pack, listed)
    cited_text = " ".join(p.text for p in pack.passages if p.ord in {e["ord"] for e in ev})
    bad_nums = V.ungrounded_numbers(texts, f"{allowed_extra} {cited_text}")
    ver["ungrounded_numbers"] = bad_nums
    if bad_nums:
        ver["notes"].append("解读里出现了图的识别结果和所引原文里都找不到的数字：" + "、".join(bad_nums[:8])
                            + "。请不要当作事实使用，可能是模型编造或读错。")
        if ver["status"] == "verified":
            ver["status"] = "partial"
    return ev, ver


# ---------- 各类型流程 ----------
async def _do_framework(app, service, provider, cfg, vmodel, fig, png, job, paper_id):
    job.progress(0.3, "识别框架图的模块与连线")
    raw = await _vlm_json(app, service, provider, vmodel, paper_id=paper_id, job=job, system=FRAMEWORK_SYSTEM,
                          text=_context_text(fig) + "\n\n请识别这张框架图。", png=png, task_type="vision_framework",
                          summary=f"发送了 1 张框架图截图（{fig['label'] or '第 ' + str(fig['page']) + ' 页'}）用于结构化识别", max_tokens=4500)
    if raw.get("not_a_framework"):
        return "reroute", clean_kind(raw.get("actual_kind")), None, None
    truncated = bool(raw.pop("_truncated", False))
    data, warnings = V.validate_framework(raw, fig["source_text"])
    if truncated:
        warnings = [*warnings, TRUNCATED_WARNING]
    data["warnings"] = warnings
    job.log(f"识别出 {len(data['modules'])} 个模块、{len(data['flows'])} 条连线；告警 {len(warnings)} 条")
    return "ok", "framework", data, warnings


def clean_kind(k) -> str:
    k = V.clean(k).lower()
    return k if k in ("chart", "table", "framework") else "chart"


async def _do_chart(app, service, provider, cfg, vmodel, fig, png, job, paper_id):
    job.progress(0.3, "读取实验图")
    raw = await _vlm_json(app, service, provider, vmodel, paper_id=paper_id, job=job, system=CHART_SYSTEM,
                          text=_context_text(fig) + "\n\n请识别这张实验图。", png=png, task_type="vision_chart",
                          summary=f"发送了 1 张实验图截图（{fig['label'] or '第 ' + str(fig['page']) + ' 页'}）用于读图", max_tokens=5500)
    truncated = bool(raw.pop("_truncated", False))
    data, warnings = V.validate_chart(raw, fig["source_text"], fig["caption"])
    if truncated:
        warnings = [*warnings, TRUNCATED_WARNING]
    data["warnings"] = warnings
    job.log(f"识别出 {len(data['panels'])} 个子图；告警 {len(warnings)} 条")
    return "ok", "chart", data, warnings


async def _do_table(app, service, provider, cfg, vmodel, fig, png, job, paper_id):
    job.progress(0.3, "转录表格")
    raw = await _vlm_json(app, service, provider, vmodel, paper_id=paper_id, job=job, system=TABLE_SYSTEM,
                          text=_context_text(fig) + "\n\n请转录这张表格。", png=png, task_type="vision_table",
                          summary=f"发送了 1 张表格截图（{fig['label'] or '第 ' + str(fig['page']) + ' 页'}）用于转录", max_tokens=6000)
    truncated = bool(raw.pop("_truncated", False))
    data, warnings = V.validate_table(raw, fig["source_text"])
    if truncated:
        warnings = [*warnings, TRUNCATED_WARNING]
    data["warnings"] = warnings
    # 位置感知核对 + 类型一致性：值在文字层里出现过但不在对应行列（模型把相邻行的数填错了格子）也要被标出
    table_check.refine(app, paper_id, fig, data)
    job.log(f"转录出 {len(data['rows'])} 行 × {len(data['columns'])} 列；告警 {len(data['warnings'])} 条"
            + (f"，位置存疑 {len(data['misplaced_cells'])} 格" if data.get("misplaced_cells") else ""))
    return "ok", "table", data, warnings


def _render_result(kind: str, data: dict) -> str:
    """把识别结果渲染成给文本模型看的文字（含精确/估读标记）。"""
    if kind == "framework":
        names = {m["id"]: m["label"] for m in data["modules"]}
        lines = ["图的整体含义：" + (data.get("summary") or "（未给出）"), "模块（id：名称 — 作用）："]
        lines += [f"- {m['id']}：{m['label']}" + (f" — {m['role']}" if m.get("role") else "") for m in data["modules"]]
        lines.append("连线：")
        lines += [f"- {names[f['from']]}（{f['from']}）→ {names[f['to']]}（{f['to']}）" + (f"：{f['label']}" if f.get("label") else "")
                  for f in data["flows"]] or ["- （未识别出连线）"]
        if data.get("uncertain"):
            lines.append("识别时不确定：" + "；".join(data["uncertain"]))
        return "\n".join(lines)
    if kind == "table":
        txt = V.table_markdown(data)
        un = data.get("unverified_cells") or []
        if un:
            txt += f"\n（{len(un)} 个单元格未能与 PDF 文字层核对：" + "、".join(
                f"第{r + 1}行第{c + 1}列“{data['rows'][r][c]}”" for r, c in un[:10]) + "）"
        return (data.get("title") + "\n" if data.get("title") else "") + txt + (("\n注：" + data["notes"]) if data.get("notes") else "")
    lines: list[str] = []
    for p in data["panels"]:
        lines.append(f"子图 {p['label'] or '-'}（{p['chart_type']}）{p['title']}")
        lines.append(f"  横轴：{p['x_axis']['label']} {p['x_axis']['unit']} {p['x_axis']['range']}".rstrip())
        lines.append(f"  纵轴：{p['y_axis']['label']} {p['y_axis']['unit']} {p['y_axis']['range']}".rstrip())
        if p["series"]:
            lines.append("  系列：" + "、".join(p["series"]))
        lines += [f"  观察：{o['text']}" for o in p["observations"]]
        lines += [f"  数值[{'exact，已在图的文字层核对' if v['precision'] == 'exact' else 'approx，读图估计'}]：{v['text']} = {v['value']}"
                  for v in p["values"]]
    if data.get("unreadable"):
        lines.append("看不清：" + "；".join(data["unreadable"]))
    return "\n".join(lines)


async def _analyze(app, service, provider, cfg, fig, kind, data, job, paper_id):
    """文本模型结合论文摘录做有出处的解读，并核实。返回 (analysis, mapping, evidence, verification, model)。"""
    job.progress(0.6, "检索论文正文中的相关描述")
    caption = fig["caption"]
    label = fig["label"] or ""
    task = {"framework": TaskType.METHOD, "chart": TaskType.EXPERIMENT, "table": TaskType.EXPERIMENT}[kind]
    sel = {"text": caption, "page": fig["page"], "passage_ords": [fig["caption_ord"]] if fig["caption_ord"] is not None else []}
    names = " ".join(m["label"] for m in data["modules"]) if kind == "framework" else ""
    pack = retrieval.build_context(
        app, paper_id, question=f"{label} {caption[:240]} {names[:300]}".strip(),
        selection=sel if (caption.strip() or sel["passage_ords"]) else None, task=task, budget_chars=22000,
    )
    job.log(f"检索到 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）")
    user_tpl = FRAMEWORK_ANALYSIS_USER if kind == "framework" else CHART_ANALYSIS_USER
    user = f"{_head(pack)}\n\n【图的识别结果】（{label or '图'}，第 {fig['page']} 页）\n{_render_result(kind, data)}\n\n{user_tpl}"
    job.progress(0.7, "撰写有出处的解读")
    raw = await _text_json(app, service, provider, cfg, paper_id=paper_id, job=job, system=ANALYSIS_RULES, user=user, pack=pack,
                           summary=f"发送了 {len(pack.passages)} 段原文和{label or '图'}的识别结果（文字）用于解读", task=task)
    model = raw.pop("_model", "")
    truncated = bool(raw.pop("_truncated", False))
    valid = {p.ord for p in pack.passages if p.doc == 0}
    pages = {p.ord: p.page for p in pack.passages if p.doc == 0}

    def cite(text: str) -> str:
        return bare_refs_to_pages(evidence.normalize_cites(text), pages)

    analysis: dict = {"inferences": [], "limitations": [V.clean(x) for x in (raw.get("limitations") or []) if V.clean(x)][:6]}
    mapping: dict | None = None
    for inf in _clean_list(raw.get("inferences")):
        analysis["inferences"].append({"text": cite(V.clean(inf.get("text"))),
                                       "basis_ords": _ords(inf.get("basis_ords"), valid)})
    if kind == "framework":
        analysis["overview"] = cite(V.clean(raw.get("overview")))
        analysis["flow_explanation"] = cite(V.clean(raw.get("flow_explanation")))
        mods = {m["id"] for m in data["modules"]}
        mapping = {"modules": {}}
        dropped = 0
        for it in raw.get("modules") or []:
            if not isinstance(it, dict) or V.clean(it.get("id")) not in mods:
                continue
            desc = V.clean(it.get("description")) if it.get("description") else ""
            text = cite(desc)
            cited = [o for o in evidence.cited_ords(text) if o in valid]
            ords = list(dict.fromkeys([*cited, *_ords(it.get("ords"), valid)]))
            if desc and not ords:  # 没有可核实引用的“论文描述”不展示，避免把编造的内容当成原文说法
                dropped += 1
                desc, text = "", ""
            mapping["modules"][V.clean(it["id"])] = {"description": text or None, "ords": ords, "found": bool(text)}
        for m in data["modules"]:
            mapping["modules"].setdefault(m["id"], {"description": None, "ords": [], "found": False})
        mapping["dropped_unsupported"] = dropped
        analysis["module_descriptions"] = mapping["modules"]
    else:
        analysis["direct"] = cite(V.clean(raw.get("direct")))
        analysis["points"] = [{"text": cite(V.clean(p["text"])), "ords": _ords(p.get("ords"), valid)}
                              for p in _clean_list(raw.get("points"))][:8]
        claims = []
        for c in _clean_list(raw.get("paper_claims")):
            sup = V.clean(c.get("supported")).lower()
            claims.append({"text": cite(V.clean(c["text"])), "supported": sup if sup in ("yes", "partly", "no", "unclear") else "unclear",
                           "note": cite(V.clean(c.get("note")))})
        analysis["paper_claims"] = claims[:6]
    job.progress(0.85, "核实引用与数字")
    allowed = _render_result(kind, data) + " " + caption + " " + fig["source_text"]
    ev, ver = _finalize_texts(analysis, pack, allowed)
    if kind != "framework" and not analysis["direct"]:
        ver["notes"].append("模型没有给出直接结论。")
        ver["status"] = "partial" if ver["status"] == "verified" else ver["status"]
    if kind == "framework" and mapping and mapping.get("dropped_unsupported"):
        ver["notes"].append(f"有 {mapping['dropped_unsupported']} 个模块的“论文描述”没有可核实的引用，已丢弃。")
    if analysis.get("direct") is not None or analysis.get("overview") is not None:
        pass
    if truncated:
        ver["notes"].append("模型的解读输出被长度限制截断，已丢弃不完整的最后部分，解读可能缺内容。")
        analysis["limitations"] = [*analysis["limitations"], "解读输出被截断，可能不完整"][:7]
        if ver["status"] == "verified":
            ver["status"] = "partial"
    # 把“图的识别结果里有告警”也反映到核实状态
    if data.get("warnings") and ver["status"] == "verified":
        ver["status"] = "partial"
        ver["notes"].append("图的识别有校验告警，解读可能受影响。")
    return analysis, mapping, ev, ver, model


def _write_back(app: AppContext, paper_id: str, fig: dict, kind: str, data: dict, existing: int | None) -> int | None:
    """表格、框架图的文字化结果作为可检索段落写回（kind=table_data/figure_data），带明确的“非原文”说明。"""
    if kind == "table":
        ratio = data.get("verified_ratio")
        if ratio is not None and ratio < 0.5:
            return None  # 与文字层吻合度太低，不当作可检索内容
        text, pkind = V.table_text(data, fig["label"]), "table_data"
    elif kind == "framework":
        text, pkind = V.framework_text(data, fig["label"]), "figure_data"
    else:
        return None
    x0, y0, x1, y1 = fig["bbox"]
    with app.db.session() as conn:
        if existing is not None and conn.execute(
                "SELECT 1 FROM passages WHERE paper_id = ? AND ord = ? AND kind IN ('table_data','figure_data')",
                (paper_id, existing)).fetchone():
            conn.execute("UPDATE passages SET text = ?, kind = ?, page = ?, x0 = ?, y0 = ?, x1 = ?, y1 = ? WHERE paper_id = ? AND ord = ?",
                         (text[:6000], pkind, fig["page"], x0, y0, x1, y1, paper_id, existing))
            return existing
        nxt = conn.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM passages WHERE paper_id = ?", (paper_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO passages(paper_id, ord, page, section_id, kind, text, x0, y0, x1, y1) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (paper_id, nxt, fig["page"], fig["section_id"], pkind, text[:6000], x0, y0, x1, y1),
        )
        return nxt


async def _run(app: AppContext, job: JobContext, paper_id: str, figure_id: int, kind_hint: str | None) -> None:
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    vmodel = choose_model(TaskType.VISION, cfg).model
    fig = load_figure(app, paper_id, figure_id)
    with app.db.session() as conn:
        prev = conn.execute("SELECT derived_ord FROM figure_analysis WHERE figure_id = ?", (figure_id,)).fetchone()
    job.progress(0.05, "渲染图区域")
    png = crop_png_fit(app, paper_id, fig["page"], fig["bbox"])
    job.log(f"图区域截图 {len(png) // 1024} KB（第 {fig['page']} 页）")

    kind = kind_hint if kind_hint in ("framework", "chart", "table") else guess_kind(fig)
    if kind is None:
        job.progress(0.15, "判断图的类型")
        raw = await _vlm_json(app, service, provider, vmodel, paper_id=paper_id, job=job, system=CLASSIFY_SYSTEM,
                              text=_context_text(fig), png=png, task_type="vision_classify",
                              summary=f"发送了 1 张图截图（{fig['label'] or '第 ' + str(fig['page']) + ' 页'}）用于判断图的类型", max_tokens=300)
        k = V.clean(raw.get("kind")).lower()
        if k == "other":
            raise ApiError("bad_request", "这张图看起来不是框架图、实验图或表格，暂不做结构化理解。")
        kind = k if k in ("framework", "chart", "table") else "chart"
        job.log(f"视觉模型判断类型：{kind}")
    _upsert(app, figure_id, paper_id, status="running", kind=kind, job_id=job.job_id, error=None)

    step = {"framework": _do_framework, "chart": _do_chart, "table": _do_table}
    status, kind, data, _w = await step[kind](app, service, provider, cfg, vmodel, fig, png, job, paper_id)
    if status == "reroute":  # 视觉模型认为不是框架图：按它给的类型换一条路径再来一次
        job.log(f"视觉模型认为这不是框架图，改按 {kind} 处理")
        status, kind, data, _w = await step[kind](app, service, provider, cfg, vmodel, fig, png, job, paper_id)
        if status == "reroute":
            raise ApiError("bad_request", "无法确定这张图的类型。")

    analysis, mapping, ev, ver, model = await _analyze(app, service, provider, cfg, fig, kind, data, job, paper_id)
    job.progress(0.93, "写回可检索内容")
    derived = _write_back(app, paper_id, fig, kind, data, prev["derived_ord"] if prev else None)
    _upsert(
        app, figure_id, paper_id, status="ready", kind=kind, data_json=dumps(data), mapping_json=dumps(mapping) if mapping else None,
        analysis_json=dumps(analysis), evidence_json=dumps(ev), verification_json=dumps(ver),
        model=f"{vmodel} + {model}" if model else vmodel, error=None, derived_ord=derived, job_id=job.job_id,
    )
    job.log("完成" + (f"，已写回为可检索段落 P{derived}" if derived is not None else ""))


def handle_vision_figure(job: JobContext) -> dict:
    app = job.app
    paper_id, figure_id = job.payload["paper_id"], job.payload["figure_id"]
    _upsert(app, figure_id, paper_id, status="running", job_id=job.job_id, error=None)
    try:
        job.run_async(_run(app, job, paper_id, figure_id, job.payload.get("kind")))
    except BaseException as exc:
        if isinstance(exc, JobCancelled):
            with app.db.session() as conn:
                conn.execute("DELETE FROM figure_analysis WHERE figure_id = ? AND status != 'ready'", (figure_id,))
        else:
            msg = exc.message if isinstance(exc, (ApiError, LLMError)) else f"{type(exc).__name__}: {exc}"
            _upsert(app, figure_id, paper_id, status="failed", error=msg[:400])
            if isinstance(exc, LLMError):
                raise ApiError(exc.code, exc.message) from exc
        raise
    return {"paper_id": paper_id, "figure_id": figure_id}


def register(manager: JobManager) -> None:
    manager.register("vision_figure", handle_vision_figure)
