"""思维导图生成：按视角检索相关段落（不是全文）→ 强模型输出受约束的 JSON 树 → 后端核实 → 合并保存。

流程与保证：
1. 上下文 = 章节大纲 + 已有的整体分析（仅帮助理解结构，不能当依据）+ 按视角检索的段落摘录（每段带 [P序号]）
   + 图表/公式清单（用 F/E 编号关联）。模型只看到这些，不会收到整篇全文。
2. 模型输出 {root:{title,summary,kind,evidence_ords,figure_ids,equation_ids,children}}。
3. tree.parse_tree 校验：evidence_ords 只能是发给模型的摘录编号（编造的丢弃），标题/摘要超长截断，
   层级 ≤ 4、同级 ≤ 8，没有任何真实依据的叶子节点整个丢弃。不通过则带着具体原因让模型重写一次。
4. 校验通过后用 tree.merge_trees 合并进数据库：用户改过/新建的节点保留，未改动的 AI 节点被新结果覆盖。
"""
from __future__ import annotations

import re

from ..ai import retrieval
from ..ai.providers import LLMError
from ..ai.router import TaskType
from ..ai.service import AIService
from ..context import AppContext
from ..jobs import JobCancelled, JobContext, JobManager
from ..llmjson import pick_model
from ..util import ApiError, dumps, loads, new_id, utcnow
from . import store
from . import tree as T
from .llmcall import call_json

JOB_TYPE = "mindmap"
TOTAL_BUDGET = 26000
BRANCH_BUDGET = 16000
ASPECTS_ZH = "背景 问题 局限 不足 挑战 提出 方法 模块 框架 实验 数据集 结果 提升 结论 展望"

# 每个视角要检索的“方面”：名称 → (关键词, 任务类型)。关键词用英文（论文正文多为英文），中文论文再叠加中文词。
VIEW_ASPECTS: dict[str, dict[str, tuple[list[str], TaskType]]] = {
    "logic": {
        "background": (["background", "introduction", "problem", "important", "recent", "widely"], TaskType.MOTIVATION),
        "motivation": (["limitation", "drawback", "existing", "however", "fail", "motivation", "lack"], TaskType.MOTIVATION),
        "challenges": (["challenge", "difficult", "problem", "issue", "hard"], TaskType.ANALYSIS),
        "method": (["propose", "method", "approach", "framework", "novel", "algorithm"], TaskType.METHOD),
        "results": (["results", "outperform", "improve", "achieve", "accuracy", "performance"], TaskType.EXPERIMENT),
        "conclusion": (["conclusion", "future", "limitation", "limitations", "summarize", "presented"], TaskType.ANALYSIS),
    },
    "method": {
        "framework": (["framework", "overview", "architecture", "pipeline", "propose", "novel", "approach"], TaskType.METHOD),
        "modules": (["module", "layer", "encoder", "decoder", "network", "component", "attention", "embedding", "policy"], TaskType.METHOD),
        "formulas": (["equation", "defined", "denote", "where", "objective", "loss", "minimize", "maximize", "reward"], TaskType.FORMULA),
        "procedure": (["algorithm", "step", "training", "inference", "procedure", "iteration", "update", "sample"], TaskType.METHOD),
    },
    "experiment": {
        "setup": (["experiment", "setup", "implementation", "dataset", "benchmark", "settings", "hyperparameter", "parameters"], TaskType.EXPERIMENT),
        "baselines": (["baseline", "compare", "compared", "state-of-the-art", "competing"], TaskType.EXPERIMENT),
        "metrics": (["metric", "accuracy", "score", "evaluate", "evaluation", "measure", "criterion"], TaskType.EXPERIMENT),
        "results": (["results", "outperform", "improve", "achieve", "table", "performance", "faster"], TaskType.EXPERIMENT),
        "analysis": (["ablation", "analysis", "sensitivity", "robust", "generalization", "scalability", "discussion", "limitation"], TaskType.EXPERIMENT),
    },
}

VIEW_BRIEF = {
    "logic": (
        "按论文的论证逻辑组织：研究问题/背景 → 现有方法的不足（动机）→ 核心思想与贡献 → 方法要点 → 实验验证 → 结论与局限。"
        "第二层通常就是这些环节，其下写要点。kind 常用 problem / motivation / challenge / idea / contribution / method / result / conclusion / limitation。"
    ),
    "method": (
        "按方法的结构组织：整体框架 → 各模块或阶段（它的作用、输入输出）→ 关键公式与算法步骤 → 训练/推理流程。"
        "第二层是模块或阶段，其下写作用、输入输出、关键公式。kind 常用 module / step / formula / concept。"
        "摘录里有对应的图表或公式时，用 figure_ids / equation_ids 关联（只能取下面清单里的编号）。"
    ),
    "experiment": (
        "按实验结构组织：实验目的 → 数据集与设置 → 对比基线与评价指标 → 主要结果 → 消融/分析 → 实验结论。"
        "第二层是这些环节，其下写具体的数据集、基线、指标、结果。kind 常用 setup / dataset / baseline / metric / result / ablation / finding / conclusion。"
        "摘要里出现的具体数字必须出自摘录。"
    ),
}

SYSTEM = """你是论文结构分析助手，要把一篇论文整理成“{view_label}”思维导图。

只依据下面给出的【论文摘录】（每段以 [P序号] 开头）与【章节大纲】，不要使用摘录以外的知识，也不要编造摘录里没有的内容。
只输出一个 JSON 对象，不要任何其它文字：
{{"root": {{"title": "…", "summary": "…", "kind": "root", "evidence_ords": [3, 7], "children": [{{"title": "…", "summary": "…", "kind": "…", "evidence_ords": [12], "figure_ids": [], "equation_ids": [], "children": []}}]}}}}

字段规则：
- title：不超过 {title_max} 个字，名词短语或短句，不要照抄整句原文；技术名词保留英文原词。
- summary：不超过 {summary_max} 个字，概括该节点在论文里说了什么。
- kind：只能取 {kinds}。
- evidence_ords：支撑该节点的段落编号整数列表（只能用摘录里出现过的 [P序号]；每个节点 1~3 个，选最直接的）。
- figure_ids / equation_ids：可选，关联的图表 / 公式编号（只能取清单里出现的整数，不要加 F/E 前缀）。
- children：子节点数组，叶子节点用 []。

结构要求：
- 根节点 = 全文主题；层级最多 {max_depth} 层（根为第 1 层）；每个节点最多 {max_children} 个子节点；除根以外共 {lo}~{hi} 个节点。
- {brief}
- 每个叶子节点都必须带 evidence_ords；摘录里找不到依据的内容就不要写，不要为了凑结构而编造。
- 不要在 title / summary 里写 [P…]，引用只放在 evidence_ords 里。
- 用中文写标题和摘要。"""


def _passages_by_ords(app: AppContext, paper_id: str, ords: list[int], neighbors: int = 0, priority: float = 500.0
                      ) -> list[retrieval.CtxPassage]:
    """直接按序号取段落（分支重新生成时，用原分支引用过的原文做锚点）。"""
    want: set[int] = set()
    for o in ords:
        for d in range(-neighbors, neighbors + 1):
            if o + d >= 0:
                want.add(o + d)
    if not want:
        return []
    out: list[retrieval.CtxPassage] = []
    items = sorted(want)
    with app.db.session() as conn:
        for i in range(0, len(items), 400):
            chunk = items[i : i + 400]
            rows = conn.execute(
                "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title FROM passages p "
                f"LEFT JOIN sections s ON s.id = p.section_id WHERE p.paper_id = ? AND p.ord IN ({','.join('?' * len(chunk))})",
                (paper_id, *chunk),
            ).fetchall()
            for r in rows:
                if r["kind"] == "reference":
                    continue
                out.append(retrieval.CtxPassage(
                    r["ord"], r["page"], r["kind"], r["section_title"], r["text"][: retrieval.MAX_PASSAGE_CHARS],
                    [r["x0"], r["y0"], r["x1"], r["y1"]], priority if r["ord"] in ords else priority / 3, {"anchor"},
                ))
    return out


def _caption_passages(app: AppContext, paper_id: str, limit: int = 16) -> list[retrieval.CtxPassage]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title FROM passages p "
            "LEFT JOIN sections s ON s.id = p.section_id WHERE p.paper_id = ? AND p.kind = 'caption' ORDER BY p.ord LIMIT ?",
            (paper_id, limit),
        ).fetchall()
    return [retrieval.CtxPassage(r["ord"], r["page"], r["kind"], r["section_title"], r["text"][:700],
                                 [r["x0"], r["y0"], r["x1"], r["y1"]], 90.0, {"caption"}) for r in rows]


def _merge(packs: list[retrieval.ContextPack], extra: list[retrieval.CtxPassage], budget: int) -> retrieval.ContextPack:
    merged: dict[int, retrieval.CtxPassage] = {}
    for p in [*(q for pk in packs for q in pk.passages), *extra]:
        if p.ord in merged:
            merged[p.ord].priority = max(merged[p.ord].priority, p.priority)
        else:
            merged[p.ord] = p
    chosen, used = [], 0
    for p in sorted(merged.values(), key=lambda x: -x.priority):
        cost = len(p.text) + 40
        if used + cost > budget and chosen:
            continue
        chosen.append(p)
        used += cost
    chosen.sort(key=lambda p: p.ord)
    base = packs[0]
    return retrieval.ContextPack(base.paper_title, base.paper_year, base.paper_authors, base.outline, chosen, None, [], [])


def build_pack(app: AppContext, paper_id: str, view: str, *, anchor_ords: list[int] | None = None, budget: int = TOTAL_BUDGET
               ) -> retrieval.ContextPack:
    with app.db.session() as conn:
        paper = conn.execute("SELECT language FROM papers WHERE id = ?", (paper_id,)).fetchone()
    aspects = VIEW_ASPECTS[view]
    per_aspect = max(3000, budget // len(aspects))
    packs = []
    for terms, task in aspects.values():
        packs.append(retrieval.build_context(
            app, paper_id, question=ASPECTS_ZH if paper["language"] == "zh" else "", selection=None, task=task,
            budget_chars=per_aspect, force_terms=terms,
        ))
    extra = _caption_passages(app, paper_id) if view in ("method", "experiment") else []
    if anchor_ords:
        extra += _passages_by_ords(app, paper_id, anchor_ords, neighbors=1)
    return _merge(packs, extra, budget)


def _entities(app: AppContext, paper_id: str) -> tuple[list[dict], list[dict]]:
    with app.db.session() as conn:
        figs = conn.execute(
            "SELECT f.id, f.page, f.kind, f.label, c.text AS caption FROM figures f "
            "LEFT JOIN passages c ON c.paper_id = f.paper_id AND c.ord = f.caption_ord WHERE f.paper_id = ? ORDER BY f.page, f.y0",
            (paper_id,),
        ).fetchall()
        eqs = conn.execute("SELECT id, page, number FROM equations WHERE paper_id = ? ORDER BY page, y0", (paper_id,)).fetchall()
    return [dict(r) for r in figs], [dict(r) for r in eqs]


def _analysis_hint(app: AppContext, paper_id: str) -> str:
    """已有的整体分析（如果有）：让模型的结构与概览一致。去掉引用标记，明确告诉模型它不是依据。"""
    with app.db.session() as conn:
        row = conn.execute("SELECT content_json FROM paper_analysis WHERE paper_id = ? AND status = 'ready'", (paper_id,)).fetchone()
    content = loads(row["content_json"], {}) if row else {}
    labels = {"background": "背景", "motivation": "动机", "challenges": "挑战", "method": "方法", "modules": "模块",
              "experiment_design": "实验设计", "results": "结果", "conclusion": "结论", "limitations": "局限"}
    lines = []
    for k, label in labels.items():
        text = re.sub(r"\[P\d+\]", "", str((content.get(k) or {}).get("text", ""))).strip()
        if text and "没有为该项给出有效内容" not in text:
            lines.append(f"- {label}：{text[:280]}")
    return "\n".join(lines)


def build_messages(
    view: str, pack: retrieval.ContextPack, figures: list[dict], equations: list[dict], analysis_hint: str,
    *, branch: dict | None = None, max_depth: int = T.MAX_DEPTH,
) -> list[dict]:
    system = SYSTEM.format(
        view_label=T.VIEW_LABELS[view], title_max=T.TITLE_MAX, summary_max=T.SUMMARY_MAX,
        kinds=" / ".join(k for k in T.KIND_LABELS if k != "root"), max_depth=max_depth, max_children=T.MAX_CHILDREN,
        lo=3 if branch is None else 2, hi=60 if branch is None else 25, brief=VIEW_BRIEF[view],
    )
    parts = [f"【论文】{pack.paper_title}" + (f"（{pack.paper_year}）" if pack.paper_year else ""),
             "【章节大纲】\n" + pack.render_outline()]
    if analysis_hint:
        parts.append("【已有的整体分析（仅帮助你理解结构，不能作为依据，也不能从中引用）】\n" + analysis_hint)
    if view in ("method", "experiment") and figures:
        parts.append("【可关联的图表（figure_ids）】\n" + "\n".join(
            f"{f['id']}: {f['label'] or ('表' if f['kind'] == 'table' else '图')}（第{f['page']}页）{(f['caption'] or '')[:70]}" for f in figures[:24]))
    if view == "method" and equations:
        parts.append("【可关联的公式（equation_ids）】\n" + "\n".join(
            f"{e['id']}: 公式{('(' + e['number'] + ')') if e['number'] else ''}（第{e['page']}页）" for e in equations[:30]))
    if branch:
        parts.append(
            "【任务：只重新生成其中一个分支】\n"
            f"分支所在路径：{' > '.join(branch['path'])}\n"
            f"分支根节点：{branch['title']}" + (f"（{branch['summary']}）" if branch["summary"] else "") + "\n"
            "请只输出这个分支的子树：JSON 的 root 就是该分支的根节点（title 保持不变，不会被采用），children 是它新的下级节点。"
            + (f"\n用户已经手工编辑/新增、会被保留的下级节点（不要重复它们）：{'；'.join(branch['protected'])}" if branch["protected"] else "")
        )
    parts.append("【论文摘录】\n" + pack.render_excerpts())
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


# ---------------------------------------------------------------- 任务
def enqueue(app: AppContext, paper_id: str, view: str, branch_id: str | None = None) -> str:
    from ..ai.settings import is_configured

    store.check_view(view)
    if not is_configured(app):
        raise ApiError("not_configured", "尚未配置 AI 模型与 API Key，请先在设置 → AI 模型中完成配置。")
    with app.db.session() as conn:
        paper = conn.execute(
            "SELECT id, title, (SELECT COUNT(*) FROM passages WHERE paper_id = papers.id) AS n FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if paper is None:
            raise ApiError("not_found", "论文不存在", 404)
        if not paper["n"]:
            raise ApiError("bad_request", "这篇论文没有可分析的文字内容。")
        if branch_id:
            node = conn.execute("SELECT * FROM mindmap_nodes WHERE id = ? AND paper_id = ? AND view = ?", (branch_id, paper_id, view)).fetchone()
            if node is None:
                raise ApiError("not_found", "要重新生成的分支不存在", 404)
            if node["parent_id"] is None:
                branch_id = None  # 根节点 = 整张图
        running = store.get_run(conn, paper_id, view)
    if running["status"] in ("pending", "running") and running["job_id"]:
        job = app.jobs.get(running["job_id"])
        if job and job["status"] in ("pending", "running"):
            return running["job_id"]
    # 先写 pending 再提交任务：避免任务线程先写 running 又被覆盖回 pending
    store.upsert_run(app, paper_id, view, "pending", job_id=None, scope=branch_id, error=None)
    label = T.VIEW_LABELS[view] + ("（分支）" if branch_id else "")
    job_id = app.jobs.submit(JOB_TYPE, f"思维导图 · {label}：{paper['title'][:30]}",
                             {"paper_id": paper_id, "view": view, "branch_id": branch_id}, paper_id)
    with app.db.session() as conn:  # 只补 job_id：任务线程可能已经把状态改成 running，不能再覆盖状态
        conn.execute("UPDATE mindmap_runs SET job_id = ? WHERE paper_id = ? AND view = ? AND job_id IS NULL", (job_id, paper_id, view))
    return job_id


def reconcile(app: AppContext, paper_id: str, view: str) -> None:
    """生成状态表与任务表对不上时自愈（应用重启、任务被取消/丢失）：状态仍是 pending/running 但任务已经结束。"""
    with app.db.session() as conn:
        run = store.get_run(conn, paper_id, view)
    if run["status"] not in ("pending", "running") or not run["job_id"]:
        return
    job = app.jobs.get(run["job_id"])
    if job is not None and job["status"] in ("pending", "running", "waiting_approval", "paused"):
        return
    if job is None:
        _fail_status(app, paper_id, view, "生成任务丢失，请重试。")
    elif job["status"] == "cancelled":
        _restore_status(app, paper_id, view)
    elif job["status"] == "failed":
        _fail_status(app, paper_id, view, job.get("error") or "生成失败，请重试。")
    else:  # completed 但状态没写 ready：以图是否存在为准
        _restore_status(app, paper_id, view)


def _fail_status(app: AppContext, paper_id: str, view: str, message: str) -> None:
    store.upsert_run(app, paper_id, view, "failed", error=message[:400], scope=None)


def _restore_status(app: AppContext, paper_id: str, view: str) -> None:
    """取消：图没有被改动，状态回到取消之前（有图 → ready，没有 → none）。"""
    with app.db.session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM mindmap_nodes WHERE paper_id = ? AND view = ?", (paper_id, view)).fetchone()[0]
    store.upsert_run(app, paper_id, view, "ready" if n else "none", error=None, scope=None)


async def _run(app: AppContext, ctx: JobContext, paper_id: str, view: str, branch_id: str | None) -> None:
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    branch: dict | None = None
    anchor_ords: list[int] = []
    depth_limit = T.MAX_DEPTH
    scope_nodes: list[T.OldNode] = []
    if branch_id:
        with app.db.session() as conn:
            row = conn.execute("SELECT * FROM mindmap_nodes WHERE id = ? AND paper_id = ? AND view = ?", (branch_id, paper_id, view)).fetchone()
            if row is None:
                raise ApiError("not_found", "要重新生成的分支已经不存在了")
            scope_nodes = store.load_old(conn, paper_id, view, branch_id)
            depth = store._depth(conn, branch_id)
            path, cur = [], row
            while cur is not None:
                path.append(cur["title"])
                cur = conn.execute("SELECT * FROM mindmap_nodes WHERE id = ?", (cur["parent_id"],)).fetchone() if cur["parent_id"] else None
        path.reverse()
        depth_limit = max(2, T.MAX_DEPTH - depth + 1)
        for n in scope_nodes:
            anchor_ords.extend(o for o in n.ords if o not in anchor_ords)
        branch = {"path": path, "title": row["title"], "summary": row["summary"],
                  "protected": [n.title for n in scope_nodes if n.protected and n.id != branch_id][:12]}
        if depth_limit < 2:
            raise ApiError("bad_request", "这个节点已经在最深一层，没有可重新生成的下级。")

    ctx.progress(0.08, "按视角检索全文相关段落")
    pack = build_pack(app, paper_id, view, anchor_ords=anchor_ords[:24], budget=BRANCH_BUDGET if branch else TOTAL_BUDGET)
    ctx.log(f"{T.VIEW_LABELS[view]}：装配上下文 {len(pack.passages)} 段，约 {pack.chars_sent:,} 字（不是全文）")
    if not pack.passages:
        raise ApiError("bad_request", "没有检索到可用于生成思维导图的正文内容。")
    figures, equations = _entities(app, paper_id)
    hint = _analysis_hint(app, paper_id)
    messages = build_messages(view, pack, figures, equations, hint, branch=branch, max_depth=depth_limit)

    valid_ords = {p.ord for p in pack.passages if p.doc == 0}
    valid_figs = {f["id"] for f in figures} if view in ("method", "experiment") else set()
    valid_eqs = {e["id"] for e in equations} if view == "method" else set()
    reports: list[T.ParseReport] = []

    def validate(data):
        root, report = T.parse_tree(
            data, valid_ords=valid_ords, valid_figures=valid_figs, valid_equations=valid_eqs,
            min_nodes=2 if branch else T.MIN_NODES, max_depth=depth_limit,
        )
        reports.append(report)
        return root

    cfg_tokens = int(cfg.get("max_output_tokens") or 4096)
    ctx.progress(0.2, f"调用模型 {pick_model(cfg, 'strong')} 生成")
    root, model, attempts = await call_json(
        app, ctx, provider, cfg, messages, task_type="mindmap", paper_id=paper_id, pack_ords=pack.ords,
        summary=f"发送了 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）、章节大纲"
                + ("、图表/公式清单" if (figures or equations) and view != "logic" else "") + f"，用于生成{T.VIEW_LABELS[view]}思维导图",
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
        validate=validate, max_tokens=min(16000, max(cfg_tokens, 9000)),
    )
    report = reports[-1]
    ctx.check_cancelled()
    ctx.progress(0.9, "核实引用并合并到思维导图")
    with app.db.session() as conn:
        old = store.load_old(conn, paper_id, view, branch_id)
        if branch_id and not old:
            raise ApiError("not_found", "要重新生成的分支在生成期间被删除了，没有保存任何内容。")
        plan = T.merge_trees(old, root, scope_root_id=branch_id, new_id=new_id)
        store.apply_plan(conn, paper_id, view, plan)
    verification = {
        "report": report.to_dict(), "merge": plan.stats, "attempts": attempts,
        "context": {"passages": len(pack.passages), "chars": pack.chars_sent}, "branch": bool(branch_id),
    }
    store.upsert_run(app, paper_id, view, "ready", model=model, error=None, scope=None, generated_at=utcnow(),
                     verification_json=dumps(verification))
    s = plan.stats
    ctx.log(f"完成：新增 {s['added']}、更新 {s['updated']}、移除 {s['removed']}、保留用户节点 {s['kept_user']}；"
            f"丢弃无效引用 {report.dropped_refs} 处、无依据节点 {report.dropped_nodes} 个")


def handle_mindmap(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id, view = ctx.payload["paper_id"], ctx.payload["view"]
    branch_id = ctx.payload.get("branch_id")
    store.upsert_run(app, paper_id, view, "running", job_id=ctx.job_id, scope=branch_id, error=None)
    try:
        ctx.run_async(_run(app, ctx, paper_id, view, branch_id))
    except BaseException as exc:
        if isinstance(exc, JobCancelled):
            _restore_status(app, paper_id, view)
        else:
            msg = exc.message if isinstance(exc, (ApiError, LLMError)) else f"{type(exc).__name__}: {exc}"
            _fail_status(app, paper_id, view, msg)
            if isinstance(exc, LLMError):
                raise ApiError(exc.code, exc.message) from exc
        raise
    return {"paper_id": paper_id, "view": view}


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle_mindmap)
