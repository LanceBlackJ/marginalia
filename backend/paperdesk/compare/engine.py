"""多论文比较引擎：为每篇论文按主题检索全文片段 → 强模型生成结构化对比（JSON）→ 逐条核实引用 → 组装关系图。

关系图只包含有依据的边：
  · OpenAlex 参考文献数据（X 的参考文献包含 Y）        basis = openalex
  · X 的本地参考文献文本里出现了 Y 的标题/DOI            basis = text_reference
  · 模型提出的关系（必须带至少一条能核实的引用）          basis = ai，ai_inferred = true
不能仅凭摘要做深度方法比较：缺全文的论文在深入比较中会被拒绝并说明原因。
"""
from __future__ import annotations

import re
from typing import Any

from ..ai.providers import LLMError
from ..ai.retrieval import build_context
from ..ai.router import TaskType
from ..context import AppContext, redact_url_keys
from ..discovery import sources as S
from ..discovery.models import norm_doi, norm_title
from ..discovery.search import http_client
from ..llmjson import ask_json
from ..util import ApiError, loads
from .evidence import PaperPack, merge_evidence, verify_text

MODES = ("quick", "method", "lineage")
MODE_LABELS = {"quick": "快速比较", "method": "深入方法比较", "lineage": "研究脉络分析"}
DIMENSIONS = {
    "quick": ["研究问题", "核心方法", "实验条件", "主要结论"],
    "method": ["研究问题", "核心挑战", "设计动机", "方法结构", "关键公式", "实验数据", "评价指标", "主要结果", "局限性", "优缺点", "适用条件"],
    "lineage": ["研究问题", "核心方法", "与其它论文的关系"],
}
BUDGET = {"quick": 6500, "method": 11000, "lineage": 8500}
ASPECTS = {
    "problem": ["problem", "challenge", "introduction", "goal", "aim", "propose", "motivation"],
    "method": ["method", "approach", "framework", "architecture", "algorithm", "model", "module", "propose"],
    "formula": ["equation", "formula", "defined", "objective", "loss", "where", "denotes", "function"],
    "experiment": ["experiment", "dataset", "baseline", "setup", "implementation", "metric", "evaluate", "parameter"],
    "results": ["results", "outperform", "improve", "achieve", "accuracy", "performance", "table", "compared"],
    "limitation": ["limitation", "future work", "conclusion", "however", "assume", "fail", "drawback"],
    "related": ["related work", "prior", "previous", "existing", "compared with", "inspired", "extend"],
}
LETTERS = "ABCDEFGH"
RELATIONS = {"improves": "改进", "extends": "扩展", "alternative": "替代方案", "shares_mechanism": "共享机制", "applies": "应用/迁移", "contrasts": "对比"}

SYSTEM = """你是严谨的科研文献比较助手。用户给出若干篇论文（标记为 A、B、C…）及从全文中检索出的片段。
请基于这些片段做比较，输出一个 JSON 对象。

规则：
1. 只能依据给出的片段。每个事实性陈述后用 [论文字母:P序号] 标注来源，例如 [A:P12][B:P7]；序号必须来自该论文的片段。
   只有摘要的论文，引用写作 [A:ABS]，并在文字里说明“仅基于摘要”。
2. 摘录里没有的信息，直接写“片段中没有足够信息确认”，不要编造；不要把推断写成论文明确说过的话。
3. 用中文，术语附英文原词；公式用文字或 LaTeX 行内写法描述，保持符号原样。
4. 比较要有信息量：指出具体差异（对象、机制、假设、数据、指标、结论），而不是各写各的摘要。
5. relations 只写你能从片段中找到依据的关系，每条必须带 cites；不确定就不写。

输出 JSON 格式：
{
 "overall": "总体结论（3-6 句，带引用）",
 "rows": [{"dimension": "维度名", "cells": {"A": "该论文在此维度的内容（带引用）", "B": "…"}}],
 "similarities": ["共同点（带引用）"],
 "differences": ["关键差异（带引用）"],
 "detail": "详细分析（Markdown，可分小节，带引用）",
 "relations": [{"from": "B", "to": "A", "relation": "improves|extends|alternative|shares_mechanism|applies|contrasts", "note": "一句话说明", "cites": ["B:P3", "A:P9"]}]
}
rows 必须覆盖下面指定的全部维度，每个维度包含所有论文的单元格。"""


def _paper_rows(app: AppContext, ids: list[str]) -> list[dict]:
    with app.db.session() as conn:
        out = []
        for pid in ids:
            r = conn.execute(
                "SELECT id, title, year, doi, arxiv_id, abstract, has_pdf, status, "
                "(SELECT COUNT(*) FROM passages WHERE paper_id = papers.id) AS n_passages FROM papers WHERE id = ?", (pid,)
            ).fetchone()
            if r is None:
                raise ApiError("not_found", "有论文不存在或已被删除", 404)
            out.append(dict(r))
    return out


def preflight(app: AppContext, ids: list[str], mode: str) -> dict:
    """比较前检查：每篇论文是否有全文可供检索。"""
    rows = _paper_rows(app, ids)
    items = []
    for i, r in enumerate(rows):
        full = bool(r["n_passages"])
        items.append({"label": LETTERS[i] if i < len(LETTERS) else str(i), "paper_id": r["id"], "title": r["title"], "year": r["year"],
                      "has_fulltext": full, "has_abstract": bool(r["abstract"]), "basis": "fulltext" if full else ("abstract" if r["abstract"] else "none")})
    blockers: list[str] = []
    warnings: list[str] = []
    for it in items:
        if it["basis"] == "none":
            blockers.append(f"《{it['title'][:40]}》既没有全文也没有摘要，无法比较")
        elif not it["has_fulltext"]:
            if mode == "method":
                blockers.append(f"《{it['title'][:40]}》没有可检索的全文，深入方法比较不能仅凭摘要进行")
            else:
                warnings.append(f"《{it['title'][:40]}》没有全文，只能基于摘要比较，结论的可靠性有限")
    if len(items) < 2:
        blockers.append("请至少选择 2 篇论文")
    if len(items) > len(LETTERS):
        blockers.append(f"最多同时比较 {len(LETTERS)} 篇论文")
    return {"papers": items, "blockers": blockers, "warnings": warnings, "ok": not blockers}


def build_packs(app: AppContext, ids: list[str], mode: str) -> tuple[dict[str, PaperPack], str, list[str]]:
    """为每篇论文检索全文片段，返回 (标签→PaperPack, 给模型的文本, 警告)。"""
    rows = _paper_rows(app, ids)
    budget = BUDGET[mode]
    packs: dict[str, PaperPack] = {}
    blocks: list[str] = []
    warnings: list[str] = []
    aspects = ["problem", "method", "experiment", "results", "limitation"] + (["formula"] if mode == "method" else []) \
        + (["related"] if mode == "lineage" else [])
    for i, r in enumerate(rows):
        label = LETTERS[i]
        pack = PaperPack(label=label, paper_id=r["id"], title=r["title"], year=r["year"], abstract=r["abstract"])
        head = f"【论文 {label}】{r['title']}（{r['year'] or '年份未知'}）"
        if r["n_passages"]:
            merged: dict[int, Any] = {}
            per = max(1500, budget // len(aspects))
            for asp in aspects:
                ctx = build_context(app, r["id"], question="", selection=None, task=TaskType.ANALYSIS, budget_chars=per,
                                    force_terms=ASPECTS[asp])
                for p in ctx.passages:
                    merged.setdefault(p.ord, p)
            total = 0
            lines: list[str] = []
            for ord_ in sorted(merged):
                p = merged[ord_]
                if total + len(p.text) > budget * 1.3:
                    continue
                total += len(p.text)
                pack.passages[ord_] = {"page": p.page, "section_title": p.section_title, "text": p.text, "bbox": p.bbox}
                sec = f" · {p.section_title}" if p.section_title else ""
                lines.append(f"[{label}:P{ord_}] (第{p.page}页{sec})\n{p.text}")
            blocks.append(head + "\n" + "\n\n".join(lines))
        else:
            pack.basis = "abstract"
            if not r["abstract"]:
                raise ApiError("bad_request", f"《{r['title'][:40]}》既没有全文也没有摘要，无法比较")
            warnings.append(f"论文 {label}《{r['title'][:40]}》没有全文，只基于摘要比较")
            blocks.append(head + f"\n（该论文没有可检索的全文，只有摘要，引用写作 [{label}:ABS]）\n摘要：{r['abstract'][:2500]}")
        packs[label] = pack
    return packs, "\n\n=====\n\n".join(blocks), warnings


# ---------------- 关系图的依据 ----------------
def _reference_passages(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, page, text, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND kind = 'reference' ORDER BY ord",
                            (paper_id,)).fetchall()
    return [dict(r) for r in rows]


def text_reference_edges(app: AppContext, packs: dict[str, PaperPack], papers: list[dict]) -> list[dict]:
    """X 的参考文献文本里出现了 Y 的标题或 DOI → X 引用了 Y（证据来自 X 的真实参考文献段落）。"""
    by_label = {p.label: papers[i] for i, p in enumerate(packs.values())}
    edges: list[dict] = []
    for xl, xp in by_label.items():
        refs = _reference_passages(app, xp["id"])
        if not refs:
            continue
        for yl, yp in by_label.items():
            if xl == yl:
                continue
            nt = norm_title(yp["title"])
            doi = norm_doi(yp["doi"])
            for r in refs:
                nr = norm_title(r["text"])
                hit_title = len(nt) >= 20 and nt in nr
                hit_doi = bool(doi) and doi in r["text"].lower()
                if hit_title or hit_doi:
                    edges.append({
                        "from": xl, "to": yl, "relation": "cites", "relation_label": "引用", "basis": "text_reference",
                        "ai_inferred": False, "note": f"{xl} 的参考文献列表中出现了 {yl} 的{'标题' if hit_title else 'DOI'}",
                        "evidence": [{"label": xl, "paper_id": xp["id"], "paper_title": xp["title"], "ord": r["ord"], "page": r["page"],
                                      "section_title": "参考文献", "quote": r["text"][:300], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]],
                                      "kind": "paper_text"}],
                    })
                    break
    return edges


async def openalex_edges(app: AppContext, packs: dict[str, PaperPack], papers: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """OpenAlex 的参考文献数据：返回 (边, 共同参考文献, 警告)。"""
    warnings: list[str] = []
    works: dict[str, Any] = {}
    try:
        async with http_client(app, timeout=25) as client:
            for i, p in enumerate(papers):
                # 只有 arXiv 版本的论文没有 DOI：OpenAlex 用 arXiv 的 DataCite DOI（10.48550/arxiv.<编号>）收录
                doi = norm_doi(p["doi"]) or (f"10.48550/arxiv.{p['arxiv_id']}" if p.get("arxiv_id") else None)
                if not doi:
                    continue
                w = await S.fetch_openalex_by_doi(client, doi)
                if w and w.openalex_id:
                    works[LETTERS[i]] = w
            shared: list[dict] = []
            ref_count: dict[str, list[str]] = {}
            for label, w in works.items():
                for ref in w.referenced_works:
                    ref_count.setdefault(ref, []).append(label)
            common = [(ref, labels) for ref, labels in ref_count.items() if len(labels) >= 2]
            common.sort(key=lambda x: -len(x[1]))
            if common:
                ws = await S.fetch_openalex_by_ids(client, [r for r, _ in common[:12]])
                by_id = {w.openalex_id: w for w in ws}
                for ref, labels in common[:12]:
                    w = by_id.get(ref)
                    if w:
                        shared.append({"title": w.title, "year": w.year, "doi": w.doi, "venue": w.venue,
                                       "cited_by": labels, "citations": w.citations, "openalex_id": ref})
    except S.SourceError as exc:
        return [], [], [f"无法获取 OpenAlex 的引用数据：{redact_url_keys(exc)}。关系图只使用论文自带的参考文献文本。"]
    if len(works) < len(papers):
        missing = [LETTERS[i] for i, p in enumerate(papers) if LETTERS[i] not in works]
        warnings.append(f"论文 {'、'.join(missing)} 没有 DOI 或在 OpenAlex 中找不到，缺少这部分的引用数据")
    edges: list[dict] = []
    for xl, xw in works.items():
        for yl, yw in works.items():
            if xl != yl and yw.openalex_id in set(xw.referenced_works):
                edges.append({"from": xl, "to": yl, "relation": "cites", "relation_label": "引用", "basis": "openalex", "ai_inferred": False,
                              "note": f"OpenAlex 参考文献数据显示 {xl} 引用了 {yl}", "evidence": [
                                  {"label": xl, "paper_id": papers[LETTERS.index(xl)]["id"], "paper_title": papers[LETTERS.index(xl)]["title"],
                                   "ord": None, "page": None, "section_title": "OpenAlex 参考文献数据", "quote": f"{xw.openalex_id} → {yw.openalex_id}",
                                   "bbox": None, "kind": "metadata"}]})
    return edges, shared, warnings


def _merge_edges(edges: list[dict]) -> list[dict]:
    """同一对论文的“引用”边只保留一条：合并证据与说明，OpenAlex 的依据优先作为 basis。"""
    out: dict[tuple, dict] = {}
    for e in edges:
        k = (e["from"], e["to"], e["relation"])
        if e["relation"] != "cites" or k not in out:
            out[k] = e
            continue
        cur = out[k]
        cur["evidence"] = cur["evidence"] + [x for x in e["evidence"] if x not in cur["evidence"]]
        if e["basis"] == "openalex":
            cur["basis"] = "openalex"
        cur["note"] += "；" + e["note"]
    return list(out.values())


# ---------------- 主流程 ----------------
async def run_comparison(app: AppContext, ids: list[str], mode: str, *, job=None) -> dict:
    if mode not in MODES:
        raise ApiError("bad_request", f"不支持的比较模式：{mode}")
    pre = preflight(app, ids, mode)
    if pre["blockers"]:
        raise ApiError("bad_request", "；".join(pre["blockers"]))
    if job:
        job.progress(0.1, "检索各论文的相关片段")
    packs, material, warnings = build_packs(app, ids, mode)
    papers = _paper_rows(app, ids)
    dims = DIMENSIONS[mode]
    labels = list(packs)
    known_edges: list[dict] = []
    shared: list[dict] = []
    if mode in ("lineage", "method"):
        if job:
            job.check_cancelled()
            job.progress(0.25, "收集引用关系依据")
        known_edges = text_reference_edges(app, packs, papers)
        oa_edges, shared, w2 = await openalex_edges(app, packs, papers)
        known_edges += oa_edges
        warnings += w2
        known_edges = _merge_edges(known_edges)
    known_note = ""
    if known_edges:
        known_note = "\n已核实的引用关系（来自参考文献数据，不需要你再推断）：" + "；".join(
            f"{e['from']} 引用 {e['to']}" for e in known_edges)
    user = (f"比较模式：{MODE_LABELS[mode]}\n需要覆盖的维度（rows）：{'、'.join(dims)}\n论文标签：{'、'.join(labels)}"
            + known_note + "\n\n" + material)
    if job:
        job.check_cancelled()
        job.progress(0.4, "调用模型生成比较")
    total_chars = sum(len(p["text"]) for pk in packs.values() for p in pk.passages.values())
    model_notes: list[str] = []
    try:
        data = await ask_json(
            app, system=SYSTEM, user=user, task_type=f"compare_{mode}", tier="strong", max_tokens=4500, notes=model_notes,
            summary=f"发送了 {len(packs)} 篇论文的相关全文片段（约 {total_chars:,} 字）用于{MODE_LABELS[mode]}",
            includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
            job_id=getattr(job, "job_id", None),
        )
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    if not isinstance(data, dict):
        raise ApiError("provider_error", "模型返回的比较结果格式不正确，请重试。")
    if job:
        job.progress(0.8, "核实引用并组装结果")
    warnings = [*warnings, *model_notes]  # 强模型不可用而改用快速模型时，如实告知
    return assemble_result(app, mode, packs, papers, data, known_edges, shared, warnings, dims)


def assemble_result(app, mode, packs, papers, data: dict, known_edges, shared, warnings, dims) -> dict:
    all_evidence: list[list[dict]] = []
    invalid_all: list[str] = []
    texts_with_cites = 0

    def check(text: str) -> tuple[str, list[dict]]:
        nonlocal texts_with_cites
        norm, ev, inv = verify_text(str(text or ""), packs)
        invalid_all.extend(i for i in inv if i not in invalid_all)
        if ev:
            texts_with_cites += 1
            all_evidence.append(ev)
        return norm, ev

    overall, _ = check(data.get("overall", ""))
    rows_in = {str(r.get("dimension", "")).strip(): r for r in (data.get("rows") or []) if isinstance(r, dict)}
    rows = []
    for dim in dims:
        src = rows_in.get(dim) or {}
        cells = {}
        for label in packs:
            text, ev = check((src.get("cells") or {}).get(label, "") or "模型没有给出这一项")
            cells[label] = {"text": text, "cites": [{"label": e["label"], "ord": e["ord"], "page": e["page"]} for e in ev]}
        rows.append({"dimension": dim, "cells": cells})
    similarities = [check(s)[0] for s in (data.get("similarities") or []) if str(s).strip()][:8]
    differences = [check(s)[0] for s in (data.get("differences") or []) if str(s).strip()][:10]
    detail, _ = check(data.get("detail", ""))
    # AI 提出的关系：必须有可核实的依据
    ai_edges: list[dict] = []
    for rel in data.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        f, t, kind = str(rel.get("from", "")).upper(), str(rel.get("to", "")).upper(), str(rel.get("relation", "")).strip()
        if f not in packs or t not in packs or f == t or kind not in RELATIONS:
            continue
        cite_text = "".join(f"[{c}]" for c in (rel.get("cites") or []) if isinstance(c, str))
        _norm, ev, _inv = verify_text(cite_text, packs)
        if not ev:
            continue  # 没有可核实的依据，不建立这条关系
        all_evidence.append(ev)
        ai_edges.append({"from": f, "to": t, "relation": kind, "relation_label": RELATIONS[kind], "basis": "ai", "ai_inferred": True,
                         "note": str(rel.get("note") or "")[:200], "evidence": ev})
    edges = known_edges + ai_edges
    evidence = merge_evidence(*all_evidence)
    if texts_with_cites == 0:
        v = {"status": "no_evidence", "invalid_citations": invalid_all, "notes": ["比较结果没有附带可核实的原文依据，请谨慎对待。"]}
    elif invalid_all:
        v = {"status": "partial", "invalid_citations": invalid_all, "notes": ["部分引用编号不存在于所给片段，已标记为无法核实。"]}
    else:
        v = {"status": "verified", "invalid_citations": [], "notes": []}
    return {
        "mode": mode, "mode_label": MODE_LABELS[mode],
        "papers": [{"label": lb, "paper_id": pk.paper_id, "title": pk.title, "year": pk.year, "basis": pk.basis} for lb, pk in packs.items()],
        "overall": overall, "dimensions": dims, "rows": rows, "similarities": similarities, "differences": differences, "detail": detail,
        "graph": {"nodes": [{"id": lb, "title": pk.title, "year": pk.year} for lb, pk in packs.items()], "edges": edges},
        "shared_references": shared, "evidence": evidence, "verification": v, "warnings": list(dict.fromkeys(warnings)),
    }
