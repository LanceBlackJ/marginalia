"""主动研究提示：后台扫描论文里“值得读者核查”的地方（设计动机不足、实验支撑不足、参数未说明、
结论可能超出实验、延伸阅读方向），每一条都必须带有后端核实过的原文依据。

设计原则（见 docs/MINDMAP.md）：
- 这不是“挑错工具”。提示词明确禁止为了凑数而挑毛病，论证充分的论文应当返回空结果（verdict=no_issues）。
- 没有任何有效依据的提示一律丢弃；低置信度的丢弃；总数 ≤ 8，去重。
- 核查状态：待核查（模型指出了原文里具体可见的情况，需要你对照原文确认）/ AI 推断（基于推理，没有原文直接陈述）/
  已核查（只能由用户在核对原文后手动标记，AI 不会自称“已核查”）。
- 论文↔代码差异、正文↔补充材料不一致：保留为扩展类型（EXTENSION_TYPES），本版本不产出，也不在界面上出现。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..ai import retrieval
from ..ai.providers import LLMError
from ..ai.router import TaskType
from ..ai.service import AIService
from ..context import AppContext
from ..db import register_schema
from ..jobs import JobCancelled, JobContext, JobManager
from ..llmjson import pick_model
from ..mindmap import store
from ..mindmap.generate import ASPECTS_ZH, _merge
from ..mindmap.llmcall import call_json
from ..mindmap.tree import clean_text, clip, humanize, title_similarity
from ..util import ApiError, dumps, loads, new_id, utcnow

JOB_TYPE = "proactive_scan"
BUDGET = 28000
MAX_FINDINGS = 8

FINDING_TYPES = {
    "weak_motivation": "设计动机不足",
    "weak_support": "实验支撑不足",
    "unexplained_param": "参数未说明",
    "overreach": "结论可能超出实验",
    "further_reading": "延伸阅读方向",
}
# 扩展点：需要论文以外的材料（代码仓库 / 补充材料）才能核实，本版本不产出
EXTENSION_TYPES = {"code_diff": "论文与代码不一致", "supp_inconsistency": "正文与补充材料不一致"}
CHECK_LABELS = {"unchecked": "待核查", "inferred": "AI 推断", "verified": "已核查"}
USER_STATES = ("new", "read", "ignored", "useful")
_CONF_RANK = {"high": 0, "medium": 1}
_TYPE_RANK = {t: i for i, t in enumerate(FINDING_TYPES)}

register_schema("insights", """
CREATE TABLE IF NOT EXISTS paper_findings (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  anchor TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',        -- [段落序号]
  ai_check_status TEXT NOT NULL DEFAULT 'unchecked',   -- 后端给出的核查状态：unchecked | inferred
  check_status TEXT NOT NULL DEFAULT 'unchecked',      -- 当前状态；用户标记后为 verified
  confidence TEXT NOT NULL DEFAULT 'medium',
  user_state TEXT NOT NULL DEFAULT 'new',          -- new | read | ignored | useful
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_paper ON paper_findings(paper_id, user_state);

CREATE TABLE IF NOT EXISTS findings_runs (
  paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  status TEXT NOT NULL,                            -- pending | running | ready | failed
  job_id TEXT,
  model TEXT,
  error TEXT,
  verdict TEXT,                                    -- issues | no_issues
  summary TEXT,
  verification_json TEXT,
  scanned_at TEXT,
  updated_at TEXT NOT NULL
);
""")

SYSTEM = """你是审慎、克制的论文阅读助手。任务：指出这篇论文里“值得读者回到原文核查”的地方。

只依据【论文摘录】（每段以 [P序号] 开头），不要使用摘录以外的知识。

最重要的原则：只在有真实依据时才提出；论证充分就什么都不提。
- 如果论文的设计有说明、主张有实验支撑、参数有来源，请返回空的 findings，并把 verdict 设为 "no_issues"。这是完全正常、也是被鼓励的结果。
- 严禁为了凑数而挑毛病：不要把写作风格、常见的篇幅取舍、摘录没覆盖到的部分（摘录只是论文的一部分）当成问题。
  某个信息“摘录里没看到”不等于“论文没写”，除非摘录里明确显示了相关主张却没有配套说明，否则不要提。
- 宁缺毋滥：最多 6 条；拿不准就不提；只输出 confidence 为 high 或 medium 的条目。

可以提出的类型（type）：
- weak_motivation（设计动机不足）：论文引入了某个设计/模块，但摘录里既没有解释为什么需要它，也没有消融或对比说明它有用。
- weak_support（实验支撑不足）：论文提出了某个明确的主张（例如“显著优于”“适用于大规模”“更鲁棒”），但摘录里的实验只覆盖了有限的数据/设置，或缺少相应的对比、消融，不足以支撑该主张。
- unexplained_param（参数未说明）：摘录里出现了关键超参数、阈值或常数的具体取值，却没有说明取值依据、来源或敏感性。
- overreach（结论可能超出实验）：摘要/结论里的断言范围明显大于实验实际覆盖的范围（例如只在合成数据上做了实验，却宣称适用于真实场景）。
- further_reading（延伸阅读方向）：论文自己指出的局限、未来工作，或它明确依赖的关键前人工作，值得继续阅读。只写摘录里能看到的线索，不要列出摘录里没有出现的论文题目、作者或年份。

每一条必须包含：
- type：上面五种之一；
- title：不超过 30 个字，点明具体对象（例如“消融实验没有单独验证拓扑注意力”）；
- detail：不超过 200 个字，写清楚：论文里哪里提出了什么 → 你为什么觉得值得核查 → 读者可以去核对什么。语气保持克制（“论文里没有看到…”“值得核对…”），不要下断言说作者错了；不要在 title/detail 里写 [P序号] 或“P46”这样的编号，也不要出现“摘录”二字，需要指位置时写“第 N 页”或直接复述原文内容；
- evidence_ords：1~3 个支撑这条提示的段落编号（整数，只能用摘录里出现的 [P序号]）；
- anchor：从所引段落里原样复制的一小段文字（不超过 20 个字或 12 个英文单词），指出你说的是哪一句；
- basis："stated" 表示原文里能直接看到你指出的具体内容（例如某个数值、某个主张）；"inferred" 表示这是你的推理判断；
- confidence："high" 或 "medium"。

输出：只输出一个 JSON 对象，不要任何其它文字：
{"verdict": "issues" 或 "no_issues", "summary": "一句话总体判断（不超过 60 字；没有发现问题就如实说明）", "findings": [{"type": "…", "title": "…", "detail": "…", "evidence_ords": [12], "anchor": "…", "basis": "stated", "confidence": "high"}]}"""

SCAN_ASPECTS: dict[str, tuple[list[str], TaskType]] = {
    "claims": (["propose", "outperform", "achieve", "demonstrate", "show that", "significantly", "state-of-the-art", "novel"], TaskType.ANALYSIS),
    "design": (["module", "component", "design", "because", "motivat", "intuition", "ablation", "propose"], TaskType.MOTIVATION),
    "experiments": (["experiment", "dataset", "baseline", "results", "evaluate", "table", "compare", "ablation"], TaskType.EXPERIMENT),
    "parameters": (["hyperparameter", "parameter", "learning rate", "threshold", "batch size", "coefficient", "set to", "weight", "epochs"], TaskType.EXPERIMENT),
    "conclusion": (["conclusion", "future work", "limitation", "limitations", "generalize", "real-world", "discussion"], TaskType.ANALYSIS),
}


# ---------------------------------------------------------------- 校验（纯逻辑）
def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").casefold())


def _as_ords(value: Any) -> list[int]:
    out: list[int] = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            n = v
        elif isinstance(v, str) and (m := re.fullmatch(r"\s*\[?P?(\d+)\]?\s*", v, re.I)):
            n = int(m.group(1))
        else:
            continue
        if n not in out:
            out.append(n)
    return out


def parse_findings(data: Any, passages: dict[int, str], pages: dict[int, int] | None = None) -> tuple[list[dict], str, str, dict]:
    """校验模型输出。passages：发给模型的摘录 {序号: 原文}。返回 (提示列表, verdict, 总体判断, 核实报告)。
    结构不对时抛 ValueError（让模型重写）；“没有问题”是合法结果，不会抛错。"""
    if isinstance(data, list):
        data = {"findings": data}
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError('JSON 里没有 findings 数组。没有问题时请输出 {"verdict": "no_issues", "summary": "…", "findings": []}。')
    report = {"proposed": len(data["findings"]), "dropped_no_evidence": 0, "dropped_low_confidence": 0,
              "dropped_bad_type": 0, "dropped_duplicates": 0, "dropped_refs": 0, "anchor_not_found": 0, "capped": 0}
    out: list[dict] = []
    for raw in data["findings"]:
        if not isinstance(raw, dict):
            continue
        ftype = clean_text(raw.get("type")).lower()
        if ftype not in FINDING_TYPES:
            report["dropped_bad_type"] += 1
            continue
        conf = clean_text(raw.get("confidence")).lower() or "medium"
        if conf not in _CONF_RANK:
            report["dropped_low_confidence"] += 1
            continue
        proposed = _as_ords(raw.get("evidence_ords"))
        ords = [o for o in proposed if o in passages]
        report["dropped_refs"] += len(proposed) - len(ords)
        title = clip(humanize(clean_text(raw.get("title")), pages), 30)
        detail = clip(humanize(clean_text(raw.get("detail")), pages), 240)
        if not ords:
            report["dropped_no_evidence"] += 1
            continue
        if not title or not detail:
            continue
        anchor = clean_text(raw.get("anchor"))[:80] or None
        anchor_ok = bool(anchor) and any(_norm(anchor) in _norm(passages[o]) for o in ords)
        note = None
        if anchor and not anchor_ok:
            report["anchor_not_found"] += 1
            note = "模型引用的短语没有在所引原文里原样找到，请直接对照原文确认。"
        stated = clean_text(raw.get("basis")).lower() == "stated"
        status = "unchecked" if (stated and anchor_ok and ftype != "further_reading") else "inferred"
        out.append({"type": ftype, "title": title, "detail": detail, "anchor": anchor if anchor_ok else None,
                    "ords": ords[:3], "confidence": conf, "ai_check_status": status, "note": note})
    out.sort(key=lambda f: (_CONF_RANK[f["confidence"]], _TYPE_RANK[f["type"]]))
    deduped: list[dict] = []
    for f in out:
        if any(g["type"] == f["type"] and (title_similarity(g["title"], f["title"]) >= 0.7
                                           or (set(g["ords"]) == set(f["ords"]) and title_similarity(g["title"], f["title"]) >= 0.5))
               for g in deduped):
            report["dropped_duplicates"] += 1
            continue
        deduped.append(f)
    if len(deduped) > MAX_FINDINGS:
        report["capped"] = len(deduped) - MAX_FINDINGS
        deduped = deduped[:MAX_FINDINGS]
    summary = clip(humanize(clean_text(data.get("summary")), pages), 120)
    verdict = "issues" if deduped else "no_issues"
    if not deduped and report["proposed"]:
        summary = (summary + " " if summary else "") + "模型提出的条目都没有通过依据核对，已全部丢弃。"
    return deduped, verdict, summary or ("没有发现需要特别提示的问题。" if not deduped else ""), report


# ---------------------------------------------------------------- 持久化
def _get_run(conn: sqlite3.Connection, paper_id: str) -> dict:
    r = conn.execute("SELECT * FROM findings_runs WHERE paper_id = ?", (paper_id,)).fetchone()
    if r is None:
        return {"status": "none", "job_id": None, "model": None, "error": None, "verdict": None, "summary": None,
                "verification": None, "scanned_at": None}
    return {"status": r["status"], "job_id": r["job_id"], "model": r["model"], "error": r["error"], "verdict": r["verdict"],
            "summary": r["summary"], "verification": loads(r["verification_json"]), "scanned_at": r["scanned_at"]}


def _upsert_run(app: AppContext, paper_id: str, status: str, **fields: Any) -> None:
    cols = ["paper_id", "status", "updated_at", *fields]
    vals = [paper_id, status, utcnow(), *fields.values()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    with app.db.session() as conn:
        conn.execute(
            f"INSERT INTO findings_runs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates}", vals,
        )


def _item(r: sqlite3.Row, passages: dict[int, dict]) -> dict:
    ords = [o for o in loads(r["evidence_json"], []) if isinstance(o, int)]
    pages = {o: p["page"] for o, p in passages.items()}  # 旧记录里残留的内部编号/“摘录”措辞在读出时也一并改写
    return {
        "id": r["id"], "paper_id": r["paper_id"], "type": r["type"], "type_label": FINDING_TYPES.get(r["type"], r["type"]),
        "title": humanize(r["title"], pages), "detail": humanize(r["detail"], pages), "anchor": r["anchor"], "evidence_ords": ords,
        "evidence": store.ref_items(passages, ords, f"{r['title']} {r['anchor'] or ''}", 260),
        "check_status": r["check_status"], "check_label": CHECK_LABELS.get(r["check_status"], r["check_status"]),
        "ai_check_status": r["ai_check_status"], "confidence": r["confidence"], "user_state": r["user_state"],
        "note": humanize(r["note"], pages) if r["note"] else None, "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_findings(app: AppContext, paper_id: str, include_ignored: bool = False) -> dict:
    with app.db.session() as conn:
        store._ensure_paper(conn, paper_id)
        run = _get_run(conn, paper_id)
        rows = conn.execute("SELECT * FROM paper_findings WHERE paper_id = ?", (paper_id,)).fetchall()
        rows = sorted(rows, key=lambda r: (_CONF_RANK.get(r["confidence"], 2), _TYPE_RANK.get(r["type"], 9), r["created_at"]))
        shown = [r for r in rows if include_ignored or r["user_state"] != "ignored"]
        ords = {o for r in shown for o in loads(r["evidence_json"], []) if isinstance(o, int)}
        passages = store.resolve_refs(conn, paper_id, ords)
    return {
        "status": run["status"], "job_id": run["job_id"], "model": run["model"], "error": run["error"],
        "verdict": run["verdict"], "summary": run["summary"], "scanned_at": run["scanned_at"], "verification": run["verification"],
        "items": [_item(r, passages) for r in shown], "ignored_count": sum(1 for r in rows if r["user_state"] == "ignored"),
        "types": FINDING_TYPES, "check_labels": CHECK_LABELS,
    }


def patch_finding(app: AppContext, paper_id: str, finding_id: str, *, user_state: str | None, verified: bool | None) -> dict:
    with app.db.session() as conn:
        r = conn.execute("SELECT * FROM paper_findings WHERE id = ? AND paper_id = ?", (finding_id, paper_id)).fetchone()
        if r is None:
            raise ApiError("not_found", "这条研究提示不存在", 404)
        sets, args = [], []
        if user_state is not None:
            if user_state not in USER_STATES:
                raise ApiError("bad_request", f"user_state 必须是 {', '.join(USER_STATES)} 之一")
            sets.append("user_state = ?")
            args.append(user_state)
        if verified is not None:
            sets.append("check_status = ?")
            args.append("verified" if verified else r["ai_check_status"])
        if not sets:
            raise ApiError("bad_request", "没有要修改的内容")
        conn.execute(f"UPDATE paper_findings SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, utcnow(), finding_id))
        row = conn.execute("SELECT * FROM paper_findings WHERE id = ?", (finding_id,)).fetchone()
        passages = store.resolve_refs(conn, paper_id, set(loads(row["evidence_json"], [])))
    return _item(row, passages)


def delete_finding(app: AppContext, paper_id: str, finding_id: str) -> None:
    with app.db.session() as conn:
        if conn.execute("DELETE FROM paper_findings WHERE id = ? AND paper_id = ?", (finding_id, paper_id)).rowcount == 0:
            raise ApiError("not_found", "这条研究提示不存在", 404)


def clear_findings(app: AppContext, paper_id: str) -> int:
    with app.db.session() as conn:
        store._ensure_paper(conn, paper_id)
        n = conn.execute("DELETE FROM paper_findings WHERE paper_id = ?", (paper_id,)).rowcount
        conn.execute("DELETE FROM findings_runs WHERE paper_id = ? AND status IN ('ready','failed')", (paper_id,))
    return n


def save_results(app: AppContext, paper_id: str, found: list[dict]) -> dict:
    """合并扫描结果：同类型且标题相近的沿用旧记录（保留用户的“有用/已读/已核查”标记）；
    没有再出现、且用户没有标记为“有用/忽略”的旧提示被移除；被忽略的提示继续保留，避免重新扫描后又冒出来。"""
    now = utcnow()
    stats = {"added": 0, "updated": 0, "removed": 0}
    with app.db.session() as conn:
        old = conn.execute("SELECT * FROM paper_findings WHERE paper_id = ?", (paper_id,)).fetchall()
        matched: set[str] = set()
        for f in found:
            best, score = None, 0.0
            for o in old:
                if o["id"] in matched or o["type"] != f["type"]:
                    continue
                s = title_similarity(o["title"], f["title"])
                if s > score:
                    best, score = o, s
            if best is not None and score >= 0.6:
                matched.add(best["id"])
                status = "verified" if best["check_status"] == "verified" else f["ai_check_status"]
                conn.execute(
                    "UPDATE paper_findings SET title=?, detail=?, anchor=?, evidence_json=?, ai_check_status=?, check_status=?, "
                    "confidence=?, note=?, updated_at=? WHERE id=?",
                    (f["title"], f["detail"], f["anchor"], dumps(f["ords"]), f["ai_check_status"], status, f["confidence"],
                     f["note"], now, best["id"]),
                )
                stats["updated"] += 1
            else:
                conn.execute(
                    "INSERT INTO paper_findings(id, paper_id, type, title, detail, anchor, evidence_json, ai_check_status, check_status, "
                    "confidence, user_state, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'new',?,?,?)",
                    (new_id(), paper_id, f["type"], f["title"], f["detail"], f["anchor"], dumps(f["ords"]), f["ai_check_status"],
                     f["ai_check_status"], f["confidence"], f["note"], now, now),
                )
                stats["added"] += 1
        for o in old:
            if o["id"] not in matched and o["user_state"] in ("new", "read"):
                conn.execute("DELETE FROM paper_findings WHERE id = ?", (o["id"],))
                stats["removed"] += 1
    return stats


# ---------------------------------------------------------------- 扫描任务
def enqueue_scan(app: AppContext, paper_id: str) -> str:
    from ..ai.settings import is_configured

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
        run = _get_run(conn, paper_id)
    if run["status"] in ("pending", "running") and run["job_id"]:
        job = app.jobs.get(run["job_id"])
        if job and job["status"] in ("pending", "running"):
            return run["job_id"]
    _upsert_run(app, paper_id, "pending", job_id=None, error=None)
    job_id = app.jobs.submit(JOB_TYPE, f"研究提示扫描：{paper['title']}", {"paper_id": paper_id}, paper_id)
    with app.db.session() as conn:  # 只补 job_id：任务线程可能已经把状态改成 running，不能再覆盖状态
        conn.execute("UPDATE findings_runs SET job_id = ? WHERE paper_id = ? AND job_id IS NULL", (job_id, paper_id))
    return job_id


def reconcile(app: AppContext, paper_id: str) -> None:
    with app.db.session() as conn:
        run = _get_run(conn, paper_id)
    if run["status"] not in ("pending", "running") or not run["job_id"]:
        return
    job = app.jobs.get(run["job_id"])
    if job is not None and job["status"] in ("pending", "running", "waiting_approval", "paused"):
        return
    if job is None or job["status"] == "failed":
        _upsert_run(app, paper_id, "failed", error=(job or {}).get("error") or "扫描任务丢失，请重试。")
    else:
        with app.db.session() as conn:
            had = conn.execute("SELECT 1 FROM paper_findings WHERE paper_id = ? LIMIT 1", (paper_id,)).fetchone()
        _upsert_run(app, paper_id, "ready" if had else "none", error=None)


def build_messages(pack: retrieval.ContextPack) -> list[dict]:
    head = f"【论文】{pack.paper_title}" + (f"（{pack.paper_year}）" if pack.paper_year else "")
    body = (f"{head}\n\n【章节大纲】\n{pack.render_outline()}\n\n"
            "（注意：下面只是论文的一部分摘录，没出现在摘录里的内容不能当作“论文没写”。）\n\n"
            f"【论文摘录】\n{pack.render_excerpts()}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]


async def _run(app: AppContext, ctx: JobContext, paper_id: str) -> None:
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    with app.db.session() as conn:
        paper = conn.execute("SELECT language FROM papers WHERE id = ?", (paper_id,)).fetchone()
    ctx.progress(0.1, "检索主张、设计、实验、参数与结论相关的段落")
    packs = []
    for terms, task in SCAN_ASPECTS.values():
        ctx.check_cancelled()
        packs.append(retrieval.build_context(
            app, paper_id, question=ASPECTS_ZH if paper["language"] == "zh" else "", selection=None, task=task,
            budget_chars=max(3000, BUDGET // len(SCAN_ASPECTS)), force_terms=terms,
        ))
    pack = _merge(packs, [], BUDGET)
    ctx.log(f"装配上下文 {len(pack.passages)} 段，约 {pack.chars_sent:,} 字（不是全文）")
    if not pack.passages:
        raise ApiError("bad_request", "没有检索到可用于扫描的正文内容。")
    texts = {p.ord: p.text for p in pack.passages if p.doc == 0}
    pages = {p.ord: p.page for p in pack.passages if p.doc == 0}
    messages = build_messages(pack)
    reports: list[dict] = []
    cfg_tokens = int(cfg.get("max_output_tokens") or 4096)

    def validate(data):
        found, verdict, summary, report = parse_findings(data, texts, pages)
        reports.append(report)
        return found, verdict, summary

    ctx.progress(0.25, f"调用模型 {pick_model(cfg, 'strong')} 分析")
    (found, verdict, summary), model, attempts = await call_json(
        app, ctx, provider, cfg, messages, task_type="proactive_scan", paper_id=paper_id, pack_ords=pack.ords,
        summary=f"发送了 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）及章节大纲，用于扫描研究提示",
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
        validate=validate, max_tokens=min(12000, max(cfg_tokens, 6000)),
    )
    ctx.check_cancelled()
    ctx.progress(0.9, "核实依据并保存")
    stats = save_results(app, paper_id, found)
    _upsert_run(app, paper_id, "ready", model=model, error=None, verdict=verdict, summary=summary, scanned_at=utcnow(),
                verification_json=dumps({"report": reports[-1], "attempts": attempts, "save": stats,
                                         "context": {"passages": len(pack.passages), "chars": pack.chars_sent}}))
    ctx.log(f"完成：{len(found)} 条研究提示（verdict={verdict}）；丢弃：{ {k: v for k, v in reports[-1].items() if k.startswith('dropped') and v} }")


def handle_scan(ctx: JobContext) -> dict:
    app = ctx.app
    paper_id = ctx.payload["paper_id"]
    _upsert_run(app, paper_id, "running", job_id=ctx.job_id, error=None)
    try:
        ctx.run_async(_run(app, ctx, paper_id))
    except BaseException as exc:
        if isinstance(exc, JobCancelled):
            with app.db.session() as conn:
                had = conn.execute("SELECT 1 FROM paper_findings WHERE paper_id = ? LIMIT 1", (paper_id,)).fetchone()
            _upsert_run(app, paper_id, "ready" if had else "none", error=None)
        else:
            msg = exc.message if isinstance(exc, (ApiError, LLMError)) else f"{type(exc).__name__}: {exc}"
            _upsert_run(app, paper_id, "failed", error=msg[:400])
            if isinstance(exc, LLMError):
                raise ApiError(exc.code, exc.message) from exc
        raise
    return {"paper_id": paper_id}


def register(manager: JobManager) -> None:
    manager.register(JOB_TYPE, handle_scan)
