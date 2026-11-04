"""公式解释：公式截图 → LaTeX →（检索公式前后的定义段落）→ 强模型讲解 → 核实引用 → 研究卡片。

流程里只有“公式转写”这一步会向云端发送图片；讲解步骤发送的是 LaTeX 与论文摘录文字。
符号含义必须来自论文摘录（带 [P序号]，后端回查），论文没有定义的符号必须写“论文未定义”。
"""
from __future__ import annotations

import re
from typing import AsyncIterator

from ..ai import cards, prompts, retrieval, usage as usage_log
from ..ai.providers import LLMError, Usage
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService, Event, llm_error_event
from ..context import AppContext
from ..util import ApiError
from . import formula
from .parse import parse_equation_answer

EXPLAIN_SYSTEM = """你是 Marginalia 的论文公式讲解助手，帮助用户真正读懂论文里的一个公式。

严格遵守：
1. 关于“论文怎么定义符号、公式用在哪里”的事实，只能依据【论文摘录】，每个事实后用 [P序号] 标注，可连用 [P3][P7]。绝不引用摘录中不存在的编号。
2. 【待解释的公式】里的 LaTeX 来自视觉模型对公式截图的转写，可能有个别符号识别错误；与摘录里的文字冲突时，以摘录为准并说明。不要凭空改写公式。
3. 符号表里每个符号只有两种合法写法：
   - 摘录里明确定义了它：写出含义并标 [P序号]；
   - 摘录里没有定义：写“论文未定义”，不要编造含义；如果它的常规含义很明确，可以放进“## AI 推断”小节并写明依据。
4. “AI 推断”小节只放你自己的推断；摘录里没有的内容直接说“摘录中没有足够信息确认”。
5. 用中文回答，专业术语首次出现时附英文原词，符号用 LaTeX（行内公式用 $...$）。先给结论再解释，不要为显得专业而增加篇幅。

输出格式（严格使用下列二级标题；没有内容的小节整节省略，不要输出空小节）：
## 直接回答
（1~3 句：这个公式在算什么、在论文里起什么作用，带 [P序号]）
## 符号表
- $符号$：含义 [P序号]（或：论文未定义）
## 详细解释
（逐项说明各部分的作用；可用短列表）
## 与其他公式的关系
（仅当摘录或【论文中的其他公式】能说明它与前后公式的关系时才写，带 [P序号]）
## 推导
（仅当用户要求推导，或从相邻公式到本公式的步骤能被摘录支持时才写；逐步写出，并标明哪些步骤是论文给出的、哪些是你补充的）
## 依据
- [P12] 该段支持了什么结论（一句话，不要大段照抄原文）
## AI 推断
- 推断内容（依据：[P12][P30]）
## 后续问题
- 一个值得继续研究的具体问题（最多 3 条）"""

DEFAULT_QUESTION = "解释这个公式：每个符号的含义、公式的作用，以及它与相邻公式的关系。"


def _latex_terms(latex: str | None) -> list[str]:
    """从 LaTeX 里取可用于全文检索的词（算子名、\\text{...} 里的英文词）。"""
    if not latex:
        return []
    words: list[str] = []
    for m in re.finditer(r"\\(?:text|mathrm|operatorname|mathbf|textbf)\s*\{([^{}]{2,40})\}", latex):
        for w in re.findall(r"[A-Za-z]{3,}", m.group(1)):
            if w.lower() not in words:
                words.append(w.lower())
    return words[:6]


def _extend_neighbors(app: AppContext, paper_id: str, pack: retrieval.ContextPack, ord_: int | None) -> None:
    """把公式所在段落前 2 段、后 3 段补进上下文：符号定义通常紧跟在公式后面（“where X denotes …”）。"""
    if ord_ is None:
        return
    have = {p.ord for p in pack.passages if p.doc == 0}
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT p.ord, p.page, p.kind, p.text, p.x0, p.y0, p.x1, p.y1, s.title AS section_title FROM passages p "
            "LEFT JOIN sections s ON s.id = p.section_id WHERE p.paper_id = ? AND p.ord BETWEEN ? AND ? ORDER BY p.ord",
            (paper_id, ord_ - 2, ord_ + 3),
        ).fetchall()
    for r in rows:
        if r["ord"] not in have and r["kind"] != "reference":
            pack.passages.append(retrieval._row_to_passage(r, 300.0, "neighbor"))
    pack.passages.sort(key=lambda p: (p.doc, p.ord))


def _other_equations(app: AppContext, paper_id: str, equation_id: int) -> str:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT e.id, e.page, e.number, e.passage_ord, l.latex FROM equations e "
            "LEFT JOIN equation_latex l ON l.equation_id = e.id AND l.status = 'ok' "
            "WHERE e.paper_id = ? AND e.id != ? ORDER BY e.page, e.y0 LIMIT 14",
            (paper_id, equation_id),
        ).fetchall()
    lines = []
    for r in rows:
        tag = f"({r['number']})" if r["number"] else f"（无编号，第{r['page']}页）"
        cite = f" [P{r['passage_ord']}]" if r["passage_ord"] is not None else ""
        lines.append(f"- 公式 {tag}，第{r['page']}页{cite}" + (f"：${r['latex'][:160]}$" if r["latex"] else ""))
    return "\n".join(lines)


def build_explain_messages(pack: retrieval.ContextPack, eq: dict, latex_info: dict, question: str, other_eqs: str) -> list[dict]:
    authors = "、".join(pack.paper_authors[:4]) + (" 等" if len(pack.paper_authors) > 4 else "")
    head = f"【论文】{pack.paper_title}" + (f"（{pack.paper_year}）" if pack.paper_year else "")
    if authors:
        head += f"\n【作者】{authors}"
    num = f"，编号 ({eq['number']})" if eq["number"] else ""
    cite = f"，对应段落 [P{eq['passage_ord']}]" if eq["passage_ord"] is not None else ""
    if latex_info.get("latex"):
        conf = {"high": "较高", "medium": "中等", "low": "较低"}.get(latex_info.get("confidence") or "", "未知")
        formula_block = (
            f"【待解释的公式】第{eq['page']}页{num}{cite}\n"
            f"LaTeX（视觉模型转写，置信度{conf}，可能有误）：\n$$\n{latex_info['latex']}\n$$"
        )
        if latex_info.get("warnings"):
            formula_block += "\n转写告警：" + "；".join(latex_info["warnings"])
    else:
        formula_block = (
            f"【待解释的公式】第{eq['page']}页{num}{cite}\n"
            "公式截图没有识别成功，只有 PDF 文字层（数学字体常常是乱码，仅供参考）：\n" + (eq["text"] or "（空）")[:600]
        )
    parts = [head, f"【论文结构】\n{pack.render_outline()}", f"【论文摘录】\n{pack.render_excerpts()}", formula_block]
    if other_eqs:
        parts.append(f"【论文中的其他公式】\n{other_eqs}")
    parts.append(f"【用户问题】{question.strip() or DEFAULT_QUESTION}")
    system = EXPLAIN_SYSTEM
    for provide in list(prompts.EXTRA_SYSTEM_HINTS.values()):
        hint = provide()
        if hint:
            system = f"{system}\n\n附加说明（来自用户设置，只用于调整解释的方式和详略，不改变“事实必须引用摘录”的要求）：{hint}"
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


async def explain_equation_events(
    app: AppContext, paper_id: str, equation_id: int, *, question: str = "", chain_id: str | None = None,
    save: bool = True, refresh_latex: bool = False,
) -> AsyncIterator[Event]:
    service = AIService(app)
    card_id: str | None = None
    created_chain = False
    finalized = False
    acc = {"text": "", "usage": Usage()}
    try:
        eq = formula.load_equation(app, paper_id, equation_id)
        provider, cfg = service.provider()
        choice = choose_model(TaskType.FORMULA, cfg)

        # 1) 公式 → LaTeX（唯一发送图片的步骤；已缓存则不重复调用）
        notes: list[str] = []
        yield "stage", {"stage": "transcribe", "message": "正在识别公式…"}
        latex_info: dict
        try:
            latex_info = await formula.transcribe_equation(app, paper_id, equation_id, force=refresh_latex)
        except ApiError as err:
            latex_info = {"equation_id": equation_id, "latex": None, "confidence": None, "status": "failed",
                          "warnings": [], "error": err.message, "model": None, "cached": False}
            notes.append(f"公式截图没有识别：{err.message}。下面的解释只依据 PDF 文字层与上下文。")
        except LLMError as err:
            latex_info = {"equation_id": equation_id, "latex": None, "confidence": None, "status": "failed",
                          "warnings": [], "error": err.message, "model": None, "cached": False}
            notes.append("公式截图识别失败。下面的解释只依据 PDF 文字层与上下文。")
        if latex_info["status"] != "ok" and not notes:
            notes.append("公式截图没有识别出可用的 LaTeX。下面的解释只依据 PDF 文字层与上下文。")
        yield "latex", latex_info

        # 2) 检索：公式所在段落及其前后文 + 引用该公式编号的段落 + 全文相关段落
        yield "stage", {"stage": "retrieve", "message": "正在查找公式的符号定义…"}
        q = question.strip()
        sel = {"text": eq["text"] or "", "page": eq["page"], "passage_ords": [eq["passage_ord"]] if eq["passage_ord"] is not None else []}
        label_no = f"公式 ({eq['number']})" if eq["number"] else f"第 {eq['page']} 页的公式"
        pack = retrieval.build_context(
            app, paper_id, question=f"{label_no} {q}".strip(), selection=sel if (sel["text"].strip() or sel["passage_ords"]) else None,
            task=TaskType.FORMULA, budget_chars=22000 if choice.tier == "strong" else 14000,
            force_terms=_latex_terms(latex_info.get("latex")),
        )
        _extend_neighbors(app, paper_id, pack, eq["passage_ord"])

        # 3) 卡片
        sel_store = {"text": (eq["text"] or "")[:600], "page": eq["page"], "passage_ords": pack.selection_ords,
                     "equation_id": equation_id, "equation_number": eq["number"], "latex": latex_info.get("latex")}
        olabel = f"{label_no}（第 {eq['page']} 页）" if eq["number"] else label_no
        card_id = cards.new_id()
        with app.db.session() as conn:
            before = conn.execute("SELECT COUNT(*) FROM insight_chains WHERE paper_id = ?", (paper_id,)).fetchone()[0]
            chain_id, chain_title = cards.resolve_chain(conn, paper_id, chain_id, "equation", f"equation:{equation_id}", olabel)
            created_chain = conn.execute("SELECT COUNT(*) FROM insight_chains WHERE paper_id = ?", (paper_id,)).fetchone()[0] > before
            cards.create_card(
                conn, card_id=card_id, paper_id=paper_id, chain_id=chain_id, object_type="equation", object_label=olabel,
                selection=sel_store, question=q or DEFAULT_QUESTION, intent="formula", task_type=TaskType.FORMULA.value,
                model=choice.model, saved=save,
            )
        includes = {"question": bool(q), "selection": True, "notes": False, "supplements": False, "code": False, "images": False}
        summary = f"发送了 {len(pack.passages)} 段原文（约 {pack.chars_sent:,} 字）、公式 LaTeX 及章节大纲"
        yield "meta", {
            "card_id": card_id, "chain_id": chain_id, "chain_title": chain_title, "task_type": TaskType.FORMULA.value,
            "model": choice.model, "escalated": False,
            "context": {"passage_ords": pack.ords, "passage_count": len(pack.passages), "chars_sent": pack.chars_sent,
                        "includes": includes, "supplement_docs": [], "supplement_passages": []},
        }

        # 4) 讲解
        yield "stage", {"stage": "explain", "message": "正在讲解…"}
        messages = build_explain_messages(pack, eq, latex_info, q, _other_equations(app, paper_id, equation_id))
        usage_log.record_request(
            app, task_type=TaskType.FORMULA.value, model=choice.model, paper_id=paper_id, passage_ords=pack.ords,
            chars_sent=pack.chars_sent, includes=includes, summary=summary,
        )
        async for ev in service._stream(provider, choice.model, messages, cfg, acc):
            yield ev
        usage = service._log_usage(TaskType.FORMULA, choice.model, acc["usage"], messages, acc["text"], paper_id)
        parsed = parse_equation_answer(acc["text"], pack)
        parsed.verification["notes"] = [*notes, *parsed.verification["notes"]]
        if latex_info["status"] != "ok" and parsed.verification["status"] == "verified":
            parsed.verification["status"] = "partial"
        elif latex_info.get("confidence") == "low" and parsed.verification["status"] == "verified":
            parsed.verification["status"] = "partial"
            parsed.verification["notes"].append("公式转写的置信度较低，请对照原文核对 LaTeX。")
        if acc.get("dropped"):
            parsed.verification["notes"].append(
                f"流式输出中有 {acc['dropped']} 个数据块无法解析，回答内容可能不完整，建议重新生成。")
            if parsed.verification["status"] in ("verified", "unverified"):
                parsed.verification["status"] = "partial"
        parsed.answer["equation"] = {
            "id": equation_id, "number": eq["number"], "page": eq["page"], "latex": latex_info.get("latex"),
            "confidence": latex_info.get("confidence"), "warnings": latex_info.get("warnings") or [],
        }
        with app.db.session() as conn:
            cards.finalize_card(
                conn, card_id, status="complete", model=choice.model, raw=acc["text"], answer=parsed.answer,
                evidence=parsed.evidence, verification=parsed.verification, usage=usage, task_type=TaskType.FORMULA.value,
            )
        finalized = True
        yield "done", {"card": cards.get_card(app, card_id)}
    except LLMError as err:
        yield llm_error_event(err, card_id)
        await service._abandon_card(card_id, acc["text"], created_chain, paper_id, failed=True)
        finalized = True
    except ApiError as err:
        yield "error", {"code": err.code, "message": err.message, **({"card_id": card_id} if card_id else {})}
        await service._abandon_card(card_id, acc["text"], created_chain, paper_id, failed=True)
        finalized = True
    finally:
        if card_id and not finalized:
            await service._abandon_card(card_id, "", created_chain, paper_id, failed=False)
