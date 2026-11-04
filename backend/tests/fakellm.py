"""模拟 OpenAI 兼容接口的假 LLM（httpx.MockTransport），让 AI 链路可以确定性地测试。

行为由请求内容决定：
- 系统提示含“检索关键词生成器” → 返回 JSON 数组
- 系统提示含“论文翻译助手”   → 返回 “【译文】” + 原文
- 系统提示含“论文分析助手”   → 返回分析 JSON（引用上下文里存在的第一个/第二个 [P序号]）
- 其它（研究问答）            → 返回约定格式的 Markdown，引用上下文里的真实序号；
  问题含 INVALID_CITE 时额外编造 [P9999]；模型名含 "weak" 且问题含 NOCITE 时不带任何引用。
"""
from __future__ import annotations

import json
import re

import httpx


def _flat(content) -> str:
    """多模态消息的 content 是 [{type:text,text}, {type:image_url,…}] 列表：文字拼接，图片记为占位符。"""
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "[image]") if isinstance(p, dict) else str(p) for p in content)


class FakeLLM:
    def __init__(self):
        self.requests: list[dict] = []
        self.fail_next: list[tuple[int, str]] = []  # (status, body) 依次触发
        self.analysis_mode = "good"  # good | echo_once | echo_always（模拟“照抄字段说明”的模型）
        self.analysis_calls = 0
        self.finish_overrides: list[str] = []  # 依次覆盖流式回复的 finish_reason（如 "length" 模拟输出被截断）
        self.fail_models: dict[str, int] = {}  # 指定模型名 -> 一直返回这个 HTTP 状态（模拟某个模型暂时不可用）
        self.translate_mode = ""  # DROP_SEGMENT：批量翻译时漏掉第 2 段
        self.transport = httpx.MockTransport(self.handler)

    # ---- 辅助 ----
    @staticmethod
    def _ords(text: str) -> list[int]:
        seen = []
        for m in re.finditer(r"^\[P(\d+)\]", text, re.M):
            n = int(m.group(1))
            if n not in seen:
                seen.append(n)
        return seen

    def _answer(self, model: str, system: str, user: str) -> str:
        if "检索关键词生成器" in system:
            return '["attention", "routing", "cascade failure"]'
        if "论文翻译助手" in system:
            src = user.split("待翻译文本：", 1)[-1].strip()
            if re.search(r"^###\d+###$", src, re.M):  # 全文翻译的批量格式：逐段加前缀，保留标记行
                if "DROP_SEGMENT" in self.translate_mode:  # 模拟模型漏掉第 2 段
                    parts = re.split(r"^(###\d+###)$", src, flags=re.M)
                    kept = [(m, b) for m, b in zip(parts[1::2], parts[2::2]) if m != "###2###"]
                    return "\n".join(f"{m}\n【译文】{b.strip()}" for m, b in kept)
                return re.sub(r"^(###\d+###)\n", r"\1\n【译文】", src, flags=re.M)
            return "【译文】" + src
        if "论文阅读测验出题人" in system:  # 自测：从给出的论文内容里逐字摘一句当依据；第 2 题故意编造依据、第 3 题故意缺选项
            body = user.split("论文内容：", 1)[-1]
            sent = re.sub(r"^\[[^\]]*\]\s*", "", body.strip().split("\n\n")[0])[:80]
            good = {"question": "这篇论文主要研究什么问题？", "options": ["选项甲", "选项乙", "选项丙", "选项丁"], "answer": 2, "explanation": "因为……", "evidence": sent}
            fake_evidence = {**good, "question": "第二题的问题是什么呢？", "answer": 0, "evidence": "这句话论文里根本没有出现过的内容"}
            broken = {"question": "缺了一个选项的题", "options": ["甲", "乙", "丙"], "answer": 1, "explanation": "", "evidence": ""}
            judge = {"type": "judge", "question": "这篇论文认为这个方法不需要任何训练数据。", "options": ["随便写的", "会被忽略"], "answer": 1, "explanation": "论文用了数据。", "evidence": sent}
            bad_judge = {"type": "judge", "question": "答案越界的判断题", "options": ["正确", "错误"], "answer": 3, "explanation": "", "evidence": ""}
            return json.dumps({"questions": [good, fake_evidence, broken, judge, bad_judge]}, ensure_ascii=False)
        if "论文补充材料整理助手" in system:  # F1：补充材料 AI 分类
            return '{"category": "实验配置", "summary": "列出了训练超参数与实验设置", "topics": ["超参数", "实验设置"]}'
        if "论文补充材料阅读顾问" in system:  # F1：阅读顺序推荐（故意夹带一个不存在的编号 A99）
            return '{"order": [{"id": "A2", "reason": "与你的问题最相关"}, {"id": "A99", "reason": "编造的"}, {"id": "A1", "reason": "补充背景"}]}'
        if "先扫清概念障碍" in system:  # 术语表：一个词真在那一段里且原话属实；一个原话是编造的；一个论文外的前置知识；一个重复；一个 ord 不存在
            ords = re.findall(r"^\[P(\d+)\] (.*)$", user, re.M)
            o1, t1 = (int(ords[0][0]), ords[0][1]) if ords else (0, "")
            word = max(re.findall(r"[A-Za-z]{6,}", t1) or ["word"], key=len)
            return json.dumps({"terms": [
                {"term": word, "zh": "示例词", "kind": "方法名", "plain": "这是论文里反复使用的一个关键概念。", "why": "后面的方法都建立在它之上。", "in_paper": True, "ord": o1, "quote": t1[:50]},
                {"term": word + "-fake", "zh": "", "kind": "术语", "plain": "原话是编造的词条。", "why": "", "in_paper": True, "ord": o1, "quote": "这是编造的一句话，并不在原文里"},
                {"term": "Gradient descent", "zh": "梯度下降", "kind": "前置知识", "plain": "沿着损失下降最快的方向一步步调整参数。", "why": "训练的基础。", "in_paper": False},
                {"term": word.upper(), "zh": "", "kind": "缩写", "plain": "重复词条（忽略大小写后和第一条相同）。", "why": "", "in_paper": False},
                {"term": "Ghost", "zh": "", "kind": "符号", "plain": "引用了不存在的段落。", "why": "", "in_paper": True, "ord": 99999, "quote": "不存在"}]}, ensure_ascii=False)
        if "帮读者读懂学术论文的学习导师" in system:  # 段落学习分析：关键句取自这一段原文；对照段落一个真一个编造；角色故意写错
            block = user.split("【选中的这一段】", 1)[-1]
            para = block.split("\n", 1)[-1].strip()
            near = re.findall(r"\[P(\d+)\]", user)
            return json.dumps({
                "role": "不存在的角色", "does": "在论证里负责铺出这个方法要解决的具体问题", "takeaway": "核心意思是……",
                "key_sentence": para[:60], "from": "接上文提出的问题", "to": "为下文的方法做铺垫",
                "watch": ["术语 A 没解释", "隐含假设：数据独立同分布", "第三条", "第四条不该出现"],
                "see": [{"ord": int(near[0]) if near else 0, "why": "对照上文定义"}, {"ord": 99999, "why": "编造的"}],
            }, ensure_ascii=False)
        if "资深的研究导师" in system:  # 学习路线：两篇真实存在的 + 一篇编造的
            return json.dumps({"stages": [
                {"name": "入门", "goal": "先建立整体认识", "papers": [{"title": "A Survey of Test Methods", "year": 2020, "why": "公认的综述"}]},
                {"name": "奠基", "goal": "核心思想", "papers": [
                    {"title": "The Original Test Method", "year": 2015, "why": "提出了核心方法"},
                    {"title": "A Completely Invented Paper About Nothing", "year": 2021, "why": "编造的"}]},
            ]}, ensure_ascii=False)
        if "问的内容论文里没有写" in system:  # 论文里没有时的补充回答（后端补的一轮）
            return "这是补充回答：它的通用做法是…… 见资料 [W1]，[P3] 不该出现。"
        if "TRIGGER_NOOUTSIDE" in user and "论文阅读与研究助手" in system:  # 模型只说“论文没有”，没有另写论文之外
            return "## 直接回答\n论文里没有写这一点。\n"
        if "TRIGGER_OUTSIDE" in user and "论文阅读与研究助手" in system:  # 论文里没有的问题：用自己的知识和网络资料回答，其中一个网络编号是编造的
            return (
                "## 直接回答\n论文里没有写这一点。\n"
                "## 论文之外\n这是一个通用概念，见资料 [W1]；另外还有一种说法 [W9]，这部分是我自己的知识。\n"
            )
        ords = self._ords(user)
        a = ords[0] if ords else 0
        b = ords[1] if len(ords) > 1 else a
        supp_cites = re.findall(r"^\[(S\d+P\d+)\]", user, re.M)  # F1：上下文里带补充材料段落时，回答引用它们
        if "论文分析助手" in system:
            self.analysis_calls += 1
            echo = self.analysis_mode == "echo_always" or (self.analysis_mode == "echo_once" and self.analysis_calls == 1)

            def field(t, o):
                if echo:  # 照抄字段说明，没有真正分析
                    return {"text": f"{t}：作者研究什么问题", "evidence_ords": [o]}
                return {"text": f"{t}：这是针对该论文的具体分析，说明了相关机制与依据 [P{o}]。", "evidence_ords": [o]}
            return json.dumps({
                "background": field("研究背景", a), "motivation": field("研究动机", b),
                "challenges": field("核心挑战", a), "method": field("方法设计", b),
                "modules": field("模块关系", a), "experiment_design": field("实验设计", b),
                "results": field("实验结果", a), "conclusion": field("研究结论", b),
                "limitations": {"text": "（推断）该方法假设负载测量是准确的，未讨论噪声情形。", "evidence_ords": []},
            }, ensure_ascii=False)
        question = user.split("【用户问题】", 1)[-1]
        if "NOCITE" in question and "weak" in model:
            return "## 直接回答\n这是一个没有任何依据的回答。"
        bad = " [P9999]" if "INVALID_CITE" in question else ""
        if "INVALID_SCITE" in question:
            bad += " [S1P9999]"
        if supp_cites:
            bad += f" 补充材料给出了具体细节 [{supp_cites[0]}]"
        return (
            f"## 直接回答\n该模块通过双重注意力分配负载 [P{a}]{bad}。\n"
            f"## 详细解释\n拓扑注意力与时间注意力互补 [P{b}]。\n"
            f"## 依据\n- [P{a}] 说明了模块的基本机制\n- [P{b}] 说明了两种信号互补\n"
            f"## AI 推断\n- 作者可能是为了避免单一信号的偏差（依据：[P{a}][P{b}]）\n"
            f"## 后续问题\n- 消融实验是否验证了两种注意力的互补性？\n"
        )

    @staticmethod
    def embed(text: str, dim: int = 64) -> list[float]:
        """确定性的“词袋哈希”嵌入：共享词越多越相似，足以测试检索链路（不模拟跨语言语义）。"""
        import hashlib
        import math

        v = [0.0] * dim
        for w in re.findall(r"[a-z0-9]{3,}|[一-鿿]", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    # ---- HTTP 处理 ----
    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"url": str(request.url), "body": body, "headers": dict(request.headers)})
        if body.get("model") in self.fail_models:
            return httpx.Response(self.fail_models[body["model"]], text="model unavailable")
        if self.fail_next:
            status, text = self.fail_next.pop(0)
            return httpx.Response(status, text=text)
        if str(request.url).endswith("/embeddings"):
            inputs = body.get("input", [])
            inputs = [inputs] if isinstance(inputs, str) else inputs
            return httpx.Response(200, json={
                "model": body.get("model"),
                "data": [{"index": i, "embedding": self.embed(t)} for i, t in enumerate(inputs)],
                "usage": {"prompt_tokens": sum(len(t) // 4 for t in inputs)},
            })
        messages = body.get("messages", [])
        system = "\n".join(_flat(m["content"]) for m in messages if m["role"] == "system")
        user = "\n".join(_flat(m["content"]) for m in messages if m["role"] == "user")
        model = body.get("model", "")
        text = self._answer(model, system, user)
        pt, ct = max(1, len(system + user) // 4), max(1, len(text) // 3)
        if not body.get("stream"):
            payload = {"model": model, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": pt, "completion_tokens": ct}}
            return httpx.Response(200, json=payload)
        lines = []
        for i in range(0, len(text), 14):
            lines.append("data: " + json.dumps({"choices": [{"delta": {"content": text[i : i + 14]}}]}, ensure_ascii=False))
        fin = self.finish_overrides.pop(0) if self.finish_overrides else "stop"
        lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": fin}]}))
        lines.append("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}))
        lines.append("data: [DONE]")
        return httpx.Response(200, content=("\n\n".join(lines) + "\n\n").encode("utf-8"),
                              headers={"content-type": "text/event-stream"})
