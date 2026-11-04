"""“解释这个节点”：复用研究问答的完整链路（检索 → 流式生成 → 证据核实 → 研究卡片），只是把问题和锚点换成节点。

不绕过任何一道核实：节点的依据段落作为“选中内容”交给 AIService.ask_events，模型的 [P序号] 引用仍由
evidence.parse_answer 对照实际发送的摘录核实。生成的卡片 object_type=mindmap_node，并写入 mindmap_node_cards，
所以卡片能反查到节点，节点详情也能列出它的解释卡片。
"""
from __future__ import annotations

from typing import AsyncIterator

from ..ai.service import AIService, AskRequest, Event
from ..context import AppContext
from ..util import ApiError
from . import store

# 节点类型 → 研究问答的意图（决定检索侧重与使用快/强模型）
KIND_INTENT = {
    "problem": "motivation", "motivation": "motivation", "challenge": "motivation", "idea": "motivation",
    "contribution": "motivation", "formula": "formula",
    "setup": "experiment", "dataset": "experiment", "baseline": "experiment", "metric": "experiment",
    "result": "experiment", "ablation": "experiment", "finding": "experiment",
}


def default_question(node: dict) -> str:
    title, summary = node["title"], node["summary"]
    desc = f"「{title}」" + (f"（{summary}）" if summary else "")
    kind = node["kind"]
    if kind in ("method", "module", "step", "concept"):
        return f"请结合原文，讲清楚思维导图节点{desc}的方法机制：它做什么、输入输出是什么、与其他模块的关系，并给出原文依据。"
    if kind in KIND_INTENT:
        return f"请结合原文，解释思维导图节点{desc}：它想说明什么、依据是什么、作者为什么这样安排。"
    return f"请结合原文，解释思维导图节点{desc}的含义，以及它在整篇论文中起的作用。"


async def explain_events(app: AppContext, node_id: str, question: str | None = None) -> AsyncIterator[Event]:
    try:
        node = store.get_node_detail(app, node_id)
    except ApiError as err:
        yield "error", {"code": err.code, "message": err.message}
        return
    q = (question or "").strip() or default_question(node)
    refs = node["refs"]
    selection = {"text": f"{node['title']}：{node['summary']}".strip("：")[:600],
                 "page": refs[0]["page"] if refs else None, "passage_ords": node["evidence"]["ords"][:4] or None}
    req = AskRequest(
        paper_id=node["paper_id"], question=q, intent=KIND_INTENT.get(node["kind"], "free"), selection=selection,
        save=True, object_type="mindmap_node", object_key=f"mindmap:{node_id}", object_label=node["title"],
    )
    async for name, data in AIService(app).ask_events(req):
        if name == "meta":
            if data.get("card_id"):
                store.link_card(app, node_id, data["card_id"])
            data = {**data, "node_id": node_id}
        elif name == "done":
            data = {**data, "node_id": node_id}
        yield name, data
