"""补充材料的 AI 分析：分类与摘要、阅读顺序推荐、数据集通俗解释。

全部以“用户显式触发 + 已授权”为前提；发送给云端的只有完成任务所需的最小内容（文件名、分类、摘要、章节标题、
开头片段、数据集统计），并写入隐私日志（includes.supplements=true）。模型给出的编号都回查真实数据。
"""
from __future__ import annotations

import json
import re

from ..ai import evidence, retrieval, usage as usage_log
from ..ai.prompts import ASK_SYSTEM
from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..util import ApiError, utcnow
from . import classify, datasets, grants, store
from .process import text_sample_for

NOT_AUTH = ("not_authorized", "需要先在“材料”面板里允许 AI 使用这篇论文的补充材料。")


def require_grant(app: AppContext, paper_id: str) -> None:
    if not grants.is_granted(app, paper_id):
        raise ApiError(NOT_AUTH[0], NOT_AUTH[1], 403)


def _privacy(app: AppContext, *, task: str, model: str, paper_id: str, chars: int, summary: str, passages: list[int] | None = None) -> None:
    usage_log.record_request(
        app, task_type=task, model=model, paper_id=paper_id, passage_ords=passages or [], chars_sent=chars,
        includes={"question": False, "selection": False, "notes": False, "supplements": True, "code": False}, summary=summary,
    )


def _json_obj(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("not object")
    return data


CLASSIFY_SYSTEM = (
    "你是论文补充材料整理助手。根据给出的文件名和内容片段，判断这个文件属于哪一类，并用一句中文概括它讲什么。\n"
    "类别只能是：" + "、".join(store.CATEGORIES) + "。\n"
    "只依据给出的内容，不要编造；内容不足时 category 选“其他”，summary 写“内容不足，无法概括”。\n"
    '只输出一个 JSON 对象：{"category": "类别", "summary": "不超过 80 字的中文概述", "topics": ["关键词", "…"]}'
)


async def ai_classify(app: AppContext, asset_id: str, log=None) -> dict:
    asset = store.get_asset(app, asset_id)
    require_grant(app, asset["paper_id"])
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    sample = text_sample_for(app, asset_id)[:3500]
    user = f"文件名：{asset['filename']}\n文件类型：{asset['kind']}\n内容片段：\n{sample or '（没有可用的文本内容）'}"
    choice = choose_model(TaskType.SUMMARIZE, cfg)
    messages = [{"role": "system", "content": CLASSIFY_SYSTEM}, {"role": "user", "content": user}]
    _privacy(app, task="supplement_classify", model=choice.model, paper_id=asset["paper_id"], chars=len(user),
             summary=f"发送了补充材料《{asset['filename'][:40]}》的文件名与约 {len(sample)} 字内容片段用于分类")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=500, json_mode=True)
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    service._log_usage("supplement_classify", choice.model, res.usage, messages, res.text, asset["paper_id"])
    try:
        data = _json_obj(res.text)
    except ValueError as exc:
        raise ApiError("provider_error", "模型返回的分类结果无法解析，请重试") from exc
    cat = str(data.get("category", "")).strip()
    summary = str(data.get("summary", "")).strip()[:300]
    fields: dict = {}
    if cat in store.CATEGORIES:
        fields.update(category=cat, category_source="ai")
    if summary:
        fields["summary"] = summary
    if fields:
        store.update_asset(app, asset_id, **fields)
    topics = [str(t)[:40] for t in (data.get("topics") or []) if isinstance(t, str)][:8]
    store.set_analysis_part(app, asset_id, ai={"topics": topics, "model": choice.model, "at": utcnow()})
    return store.get_asset(app, asset_id)


RECOMMEND_SYSTEM = (
    "你是论文补充材料阅读顾问。给出一篇论文的补充材料清单（编号 A1、A2…，含类别与概述）和读者当前关心的问题，"
    "请推荐阅读顺序：先读最能帮助理解正文/回答问题且体量小的材料。每项给一句具体理由（说明它为什么对当前问题有用）。\n"
    '只输出 JSON：{"order": [{"id": "A2", "reason": "…"}, …]}，id 必须来自清单，不要编造。'
)


async def ai_recommend(app: AppContext, paper_id: str, question: str) -> dict:
    assets = [a for a in store.list_assets(app, paper_id) if a["download_status"] == "done"]
    rules = classify.rule_recommend(assets, question)
    if len(assets) < 2:
        return {"items": rules, "source": "rules"}
    if not grants.is_granted(app, paper_id):
        return {"items": rules, "source": "rules", "note": "未授权 AI 使用补充材料，已按规则排序"}
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError:
        return {"items": rules, "source": "rules", "note": "尚未配置 AI 模型，已按规则排序"}
    with app.db.session() as conn:
        title = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()["title"]
    ids = {f"A{i + 1}": a for i, a in enumerate(assets)}
    listing = "\n".join(f"{k}: {a['filename']}｜类别：{a['category']}｜概述：{a.get('summary') or '（无）'}" for k, a in ids.items())
    user = f"论文：{title}\n读者当前的问题：{question.strip() or '（没有具体问题，请给出通用的阅读顺序）'}\n补充材料清单：\n{listing}"
    choice = choose_model(TaskType.SUMMARIZE, cfg)
    messages = [{"role": "system", "content": RECOMMEND_SYSTEM}, {"role": "user", "content": user}]
    _privacy(app, task="supplement_recommend", model=choice.model, paper_id=paper_id, chars=len(user),
             summary=f"发送了 {len(assets)} 个补充材料的文件名/类别/概述用于推荐阅读顺序")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=800, json_mode=True)
        service._log_usage("supplement_recommend", choice.model, res.usage, messages, res.text, paper_id)
        data = _json_obj(res.text)
    except (LLMError, ValueError):
        return {"items": rules, "source": "rules", "note": "AI 推荐失败，已按规则排序"}
    items, seen = [], set()
    for entry in data.get("order") or []:
        a = ids.get(str(entry.get("id")))
        if a and a["id"] not in seen:  # 只接受清单里真实存在的编号
            seen.add(a["id"])
            items.append({"asset_id": a["id"], "order": len(items) + 1, "reason": str(entry.get("reason", "")).strip()[:200] or "AI 推荐"})
    for r in rules:  # 模型漏掉的补在后面
        if r["asset_id"] not in seen:
            items.append({"asset_id": r["asset_id"], "order": len(items) + 1, "reason": r["reason"]})
    return {"items": items, "source": "ai", "model": choice.model}


EXPLAIN_RULES = (
    "\n\n补充规则（数据集解释）：\n"
    "- 【数据集统计】来自对该文件的程序化检查，可以直接陈述，不必引用 [P序号]。\n"
    "- 说明字段含义、与论文实验的关系时，凡依据论文摘录的事实必须带 [P序号]；论文没有说明的字段含义，放进“AI 推断”并写明依据。\n"
    "- 如实指出数据质量问题（缺失率高、类型混杂、分布偏斜等，来自统计），不要夸大。"
)


async def ai_explain_dataset(app: AppContext, asset_id: str, log=None) -> dict:
    asset = store.get_asset(app, asset_id)
    require_grant(app, asset["paper_id"])
    profile = store.get_analysis(app, asset_id)
    if not profile or profile.get("kind") != "table":
        raise ApiError("bad_request", "这个文件还没有数据集分析结果，无法解释")
    service = AIService(app)
    try:
        provider, cfg = service.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    paper_id = asset["paper_id"]
    cols = [c["name"] for s in profile["sheets"] for c in s["columns"]][:30]
    question = "dataset experiment data used in the experiments " + " ".join(cols)
    pack = retrieval.build_context(app, paper_id, question=question, selection=None, task=TaskType.EXPERIMENT, budget_chars=9000)
    brief = datasets.profile_brief(profile)
    head = f"【论文】{pack.paper_title}"
    user = (f"{head}\n\n【论文结构】\n{pack.render_outline()}\n\n【论文摘录】\n{pack.render_excerpts()}\n\n"
            f"【数据集文件】{asset['filename']}（类别：{asset['category']}）\n【数据集统计】\n{brief}\n\n"
            "【任务】用通俗中文解释这个数据集：它是什么、每个关键字段大概表示什么、与论文实验的关系、数据质量需要注意什么、可以怎么用。")
    choice = choose_model(TaskType.EXPERIMENT, cfg)
    messages = [{"role": "system", "content": ASK_SYSTEM + EXPLAIN_RULES}, {"role": "user", "content": user}]
    _privacy(app, task="supplement_dataset_explain", model=choice.model, paper_id=paper_id, chars=len(user),
             passages=pack.ords, summary=f"发送了数据集《{asset['filename'][:40]}》的字段统计（含少量样本行摘要）与 {len(pack.passages)} 段论文原文")
    try:
        res = await provider.complete(choice.model, messages, temperature=0.2, max_tokens=cfg["max_output_tokens"])
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    service._log_usage("supplement_dataset_explain", choice.model, res.usage, messages, res.text, paper_id)
    parsed = evidence.parse_answer(res.text, pack, TaskType.EXPERIMENT)
    out = {"markdown": res.text, "answer": parsed.answer, "evidence": parsed.evidence,
           "verification": parsed.verification, "model": choice.model, "at": utcnow()}
    store.set_analysis_part(app, asset_id, explanation=out)
    return out
