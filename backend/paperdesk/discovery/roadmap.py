"""学习路线：顺着一个方向，从入门到前沿，给一条循序渐进的“必读论文”路线。

AI 负责“选哪些论文、为什么必读、先后顺序”（这靠它对领域的了解）；每一篇都要回到 OpenAlex 核对：
标题对得上、真实存在才收，引用数、来源、年份都取真实数据，核对不上的（AI 记错或编造的）直接丢掉并如实告知数量。
"""
from __future__ import annotations

import asyncio

from ..ai.providers import LLMError
from ..context import AppContext
from ..llmjson import ask_json
from . import sources as S
from .dedupe import merge_works
from .models import Work, title_similarity
from .ranking import score_works
from .search import http_client, mark_in_library

ROADMAP_SYSTEM = """你是资深的研究导师。用户想系统地学习一个研究方向。请给出一条“循序渐进的必读论文路线”。
只输出 JSON：{"stages": [{"name": "阶段名", "goal": "这一阶段要建立什么认识（中文一句话）", "papers": [{"title": "论文的英文原标题", "year": 2017, "why": "为什么必读（中文一句话：它解决了什么问题、后来影响了什么）"}]}]}
要求：
- 4~6 个阶段，由浅入深：入门与综述 → 奠基/里程碑 → 关键改进与扩展 → 近年重要进展（按方向自己取名，不必照抄）；每阶段 2~4 篇，总共不超过 16 篇。
- 只选真正有分量的论文：奠基性的、里程碑式的、公认的高引用工作、被广泛认可的综述。宁缺毋滥，不要为了凑数列平庸的论文。
- title 必须是你确定存在的论文的准确英文原标题；不确定标题的就不要写，绝对不要编造。"""

MAX_PAPERS = 16


async def _resolve(client, title: str, year: int | None) -> Work | None:
    """按标题在 OpenAlex 里找这篇论文：标题足够像才算（年份相差太多的扣分），多个版本取引用最多的。"""
    try:
        hits = await S.fetch_openalex(client, title, limit=6)
    except Exception:  # noqa: BLE001  一篇查不到不影响其它篇
        return None
    best: Work | None = None
    best_key = (0.0, 0)
    for w in hits:
        sim = title_similarity(w.title, title)
        if year and w.year and abs(w.year - year) > 1:
            sim -= 0.15
        k = (round(sim, 3), w.citations or 0)
        if k > best_key:
            best, best_key = w, k
    return best if best is not None and best_key[0] >= 0.75 else None


async def build_roadmap(app: AppContext, topic: str, notes: list[str]) -> dict:
    """返回 {"stages": [{id, title, hint, items:[work dict]}], "proposed": n, "verified": m}。AI 不可用或失败时返回空路线并写明原因。"""
    try:
        data = await ask_json(
            app, system=ROADMAP_SYSTEM, user=f"我想系统学习的方向：{topic}", task_type="roadmap", tier="strong", max_tokens=2600,
            includes={"question": True, "selection": False, "notes": False, "supplements": False, "code": False},
            summary=f"把研究方向“{topic[:40]}”发给云端模型，请它列一条必读论文路线（只发了方向名称，没有发送论文内容）", notes=notes,
        )
    except LLMError as err:
        notes.append(f"没能生成学习路线：{err.message}")
        return {"stages": [], "proposed": 0, "verified": 0}
    raw = data.get("stages") if isinstance(data, dict) else None
    stages_in: list[dict] = []
    total = 0
    for st in raw or []:
        if not isinstance(st, dict):
            continue
        papers = []
        for p in st.get("papers") or []:
            if isinstance(p, dict) and isinstance(p.get("title"), str) and p["title"].strip() and total < MAX_PAPERS:
                yr = p.get("year") if isinstance(p.get("year"), int) else None
                papers.append({"title": p["title"].strip(), "year": yr, "why": str(p.get("why") or "").strip()})
                total += 1
        if papers:
            stages_in.append({"name": str(st.get("name") or "").strip() or "阶段", "goal": str(st.get("goal") or "").strip(), "papers": papers})

    sem = asyncio.Semaphore(6)
    async with http_client(app) as client:

        async def one(p: dict) -> Work | None:
            async with sem:
                return await _resolve(client, p["title"], p["year"])

        found = await asyncio.gather(*[one(p) for st in stages_in for p in st["papers"]])
    works = [w for w in found if w is not None]
    merged = merge_works(works)
    mark_in_library(app, merged)
    score_works(merged, query=topic, mode="impact")
    by_key = {w.key: w for w in merged}

    stages: list[dict] = []
    i = 0
    seen: set[str] = set()
    verified = 0
    for si, st in enumerate(stages_in):
        items: list[dict] = []
        for p in st["papers"]:
            w = found[i]
            i += 1
            if w is None:
                continue
            m = by_key.get(w.key) or max(merged, key=lambda x: title_similarity(x.title, w.title), default=w)  # 版本合并后取代表那一条
            if m.key in seen:
                continue
            seen.add(m.key)
            m.reason, m.reason_source = p["why"] or "该方向的重要论文", "ai"
            items.append(m.to_dict())
            verified += 1
        if items:
            stages.append({"id": f"road{si + 1}", "title": f"{si + 1}. {st['name']}", "hint": st["goal"], "items": items})
    proposed = sum(len(st["papers"]) for st in stages_in)
    if proposed > verified:
        notes.append(f"AI 列了 {proposed} 篇，其中 {proposed - verified} 篇在学术数据库里核对不上标题（可能记错或不存在），已去掉。")
    return {"stages": stages, "proposed": proposed, "verified": verified}
