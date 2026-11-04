"""代码理解模块专用的假 LLM（httpx.MockTransport）。按系统提示分发，并按问题里的魔法词产出各种“坏输出”。"""
from __future__ import annotations

import json
import re

import httpx


class FakeCodeLLM:
    def __init__(self):
        self.requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handler)

    # ---- 生成内容 ----
    def explain(self, model: str, user: str) -> str:
        snippets = re.findall(r"\[(C\d+)\] (\S+):(\d+)-(\d+)（", user)
        question = user.split("【用户问题】", 1)[-1]
        if "REFUSE" in question:
            return "## 直接回答\n所给代码片段里看不出这个信息。"
        if "NOCITE" in question and "weak" in model:
            return "## 直接回答\n这个函数负责训练流程。"
        label, path, a, b = snippets[0] if snippets else ("C1", "x.py", "1", "2")
        a, b = int(a), int(b)
        extra = ""
        if "BADCITE" in question:
            extra = f" 另外它依赖一个不存在的片段 [C9]、不存在的文件 [nofile.py:1-3] 和越界行号 [{label}:99999-100000]。"
        return (
            f"## 直接回答\n该代码段按顺序完成数据加载与训练 [{label}:{a}-{min(b, a + 2)}]。{extra}\n"
            f"## 详细解释\n函数先读取数据，再逐批更新模型 [{path}:{a}-{b}]。\n"
            f"## 依据\n- [{label}:{a}-{min(b, a + 1)}] 说明了函数的入口部分\n"
            f"## AI 推断\n- 作者可能把训练循环放在这里以便复用（依据：[{label}]）\n"
            f"## 后续问题\n- 学习率是在哪里设置的？\n"
        )

    def annotate(self, user: str) -> str:
        ids = re.findall(r"id=(\d+) ", user)
        data = {i: {"title": f"步骤{n + 1}" + "很长的名称" * 5, "summary": f"第 {n + 1} 步做什么"} for n, i in enumerate(ids)}
        data["99999"] = {"title": "编造的节点", "summary": "不存在"}
        return json.dumps(data, ensure_ascii=False)

    def modules(self, user: str) -> str:
        ords = [int(x) for x in re.findall(r"\[P(\d+)\]", user)]
        mods = [
            {"name": "Dual attention", "description": "拓扑注意力与时间注意力", "passage_ords": ords[:2] + [99999], "keywords": ["attention", "topology", "temporal"]},
            {"name": "Training loop", "description": "训练 agent", "passage_ords": ords[2:3], "keywords": ["train", "learn", "agent"]},
        ]
        return json.dumps({"modules": mods}, ensure_ascii=False)

    def rerank(self, user: str) -> str:
        cands = re.findall(r"symbol_id=(\d+) (\S+) \((\S+?):(\d+)-(\d+)\)", user)
        links = []
        for i, (sid, qual, path, a, b) in enumerate(cands):
            a, b = int(a), int(b)
            if i == 0:
                links.append({"symbol_id": int(sid), "confidence": "likely", "basis": f"{qual} 的循环体直接实现了该步骤", "lines": [a, min(b, a + 1)]})
            elif i == 1:
                links.append({"symbol_id": int(sid), "confidence": "likely", "basis": "名字相似但给的行号不在范围内", "lines": [b + 100, b + 101]})
            elif i == 2:
                links.append({"symbol_id": int(sid), "confidence": "no", "basis": "不相关"})
        links.append({"symbol_id": 99999, "confidence": "likely", "basis": "编造的符号", "lines": [1, 2]})
        return json.dumps({"links": links}, ensure_ascii=False)

    def claims(self, user: str) -> str:
        ords = re.findall(r"\[P(\d+)\] ([^\n]+)", user)
        out = []
        for o, text in ords:
            m = re.search(r"embedding dimension (?:of )?(\d+)", text)
            if m:
                out.append({"name": "embedding_dim", "value": m.group(1), "ord": int(o), "quote": m.group(0)})
            m = re.search(r"graphs of (\d+)[–-](\d+) nodes", text)
            if m:  # 真实网关上出现过的归类错误：节点数区间被当成“图的数量”
                out.append({"name": "num_training_graphs", "value": f"{m.group(1)}-{m.group(2)}", "ord": int(o), "quote": m.group(0)})
            m = re.search(r"we (?:chose|set) M = ([\d,]+)", text)
            if m:  # 引文本身没有“replay/memory”字样：是否真的是回放池容量要看上下文
                out.append({"name": "replay_size", "value": m.group(1), "ord": int(o), "quote": m.group(0)})
        if ords:
            out.append({"name": "momentum", "value": "0.9", "ord": int(ords[0][0]), "quote": "momentum was fixed to 0.9 in all runs"})  # 伪造的引文，必须被丢弃
        return json.dumps({"claims": out}, ensure_ascii=False)

    def datasets(self, user: str) -> str:
        """复现评估：抽取论文里的数据集名称。故意夹带一条引文不在原文里的（必须被丢弃）。"""
        out = []
        for o, text in re.findall(r"\[P(\d+)\] ([^\n]+)", user):
            if "three real networks" in text:
                out.append({"text": "power grid", "ord": int(o), "quote": "a power grid, a road network and an internet topology"})
                out.append({"text": "ImageNet", "ord": int(o), "quote": "trained on ImageNet-1k"})  # 编造
        return json.dumps({"datasets": out}, ensure_ascii=False)

    # ---- HTTP ----
    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"url": str(request.url), "body": body})
        messages = body.get("messages", [])
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        model = body.get("model", "")
        if "论文代码阅读助手" in system:
            text = self.explain(model, user)
        elif "运行流程" in system and "节点" in system:
            text = self.annotate(user)
        elif "论文方法分析助手" in system:
            text = self.modules(user)
        elif "审阅者" in system:
            text = self.rerank(user)
        elif "数据集或数据来源名称" in system:
            text = self.datasets(user)
        elif "论文信息抽取器" in system:
            text = self.claims(user)
        elif "检索关键词生成器" in system:
            text = '["attention"]'
        else:
            text = "OK"
        pt, ct = max(1, len(system + user) // 4), max(1, len(text) // 3)
        frames = [json.dumps({"choices": [{"delta": {"content": text[i:i + 24]}}]}, ensure_ascii=False) for i in range(0, len(text), 24)]
        frames.append(json.dumps({"choices": [], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}))
        frames.append("[DONE]")
        return httpx.Response(200, content="".join(f"data: {f}\n\n" for f in frames).encode("utf-8"), headers={"content-type": "text/event-stream"})
