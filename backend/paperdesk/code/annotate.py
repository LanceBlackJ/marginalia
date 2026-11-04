"""AI 给流程节点起名并写一句话说明。流程结构本身来自静态分析，这里只做“命名与解释”，结果标注为 AI 生成。"""
from __future__ import annotations

import json
import re

from ..ai import usage as usage_log
from ..ai.providers import LLMError
from ..ai.router import TaskType, choose_model
from ..ai.service import AIService
from ..context import AppContext
from ..util import ApiError
from . import flow as flowmod
from . import service

SYSTEM = (
    "你是代码阅读助手。下面是某个程序“运行流程”里的节点（函数）。请为每个节点起一个不超过 10 个字的中文名称，"
    "并用一句不超过 40 字的话说明它在流程中做什么。只能依据给出的签名、文档串和代码行；看不出来就如实写“从代码里看不出”，不要猜。"
    '只输出一个 JSON 对象，键是节点 id（字符串），值是 {"title": "...", "summary": "..."}。不要输出其它文字。'
)


async def annotate_flow(app: AppContext, repo_id: str, symbol_id: int | None) -> dict:
    repo = service.get_repo(app, repo_id)
    fl = flowmod.build_flow(app, repo_id, symbol_id)
    nodes = fl["nodes"][:40]
    if not nodes:
        raise ApiError("bad_request", "流程为空，无法命名。")
    ai = AIService(app)
    try:
        provider, cfg = ai.provider()
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    choice = choose_model(TaskType.SUMMARIZE, cfg)
    parts, chars = [], 0
    for n in nodes:
        lines = service.file_lines(app, repo_id, n["path"]) or []
        head = "\n".join(l for l in lines[n["line_start"] - 1: n["line_start"] + 8] if l.strip())[:600]
        block = f"id={n['id']} {n['label']}（{n['path']}:{n['line_start']}-{n['line_end']}，阶段推断：{n['phase']}）\n签名：{n['signature']}\n文档：{n['doc'] or '无'}\n代码：\n{head}"
        parts.append(block)
        chars += len(block)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n---\n".join(parts)}]
    usage_log.record_request(
        app, task_type="code_annotate", model=choice.model, paper_id=repo["paper_id"], passage_ords=[], chars_sent=chars,
        includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": True},
        summary=f"发送了 {len(nodes)} 个流程节点的签名与开头几行代码（约 {chars:,} 字符）用于命名",
    )
    try:
        res = await provider.complete(choice.model, messages, temperature=0.1, max_tokens=2000, json_mode=True)
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    ai._log_usage("code_annotate", choice.model, res.usage, messages, res.text, repo["paper_id"])
    m = re.search(r"\{.*\}", res.text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError as exc:
        raise ApiError("provider_error", "模型返回的命名结果无法解析，请重试。") from exc
    valid = {str(n["id"]) for n in nodes}
    ann = {}
    for k, v in data.items():
        if str(k) in valid and isinstance(v, dict) and str(v.get("title", "")).strip():
            ann[str(k)] = {"title": str(v["title"]).strip()[:16], "summary": str(v.get("summary", "")).strip()[:80], "ai": True, "model": choice.model}
    flowmod.save_annotations(app, repo_id, ann)
    return flowmod.build_flow(app, repo_id, symbol_id)
