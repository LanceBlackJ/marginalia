"""F5：公式/图表理解的假视觉 LLM（在 FakeLLM 之上拦截视觉相关提示词，其余沿用原行为）。

只证明工程链路（多模态消息、JSON 校验、证据核实、防编造），不证明视觉模型真的看得懂图。
可通过属性调整行为，用来测试各种“模型出错”的情形。
"""
from __future__ import annotations

import json
import re

from fakellm import FakeLLM


class VisionFakeLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.latex_reply = (
            "```latex\nR(v_1, v_2, \\ldots, v_N) = \\frac{1}{N} \\sum_{k=1}^{N} "
            "\\frac{\\sigma(\\mathcal{G} \\setminus \\{v_1, \\ldots, v_k\\})}{\\sigma(\\mathcal{G})} \\tag{1}\n```\nCONFIDENCE: high"
        )
        self.latex_reply_second: str | None = None  # 非空时，每对转写里的第二次返回它（模拟两次识别不一致）
        self.formula_calls = 0
        self.arbiter_reply = '{"better": "neither", "differences": []}'
        self.override_queue: list[tuple[str, str]] = []
        self.fail_formula_call: int | None = None
        self.formula_requests = 0
        self.classify_kind = "framework"
        self.framework_raw: dict | None = None
        self.chart_raw: dict | None = None
        self.table_raw: dict | None = None
        self.not_framework = False
        self.explain_extra_symbol = False   # 符号表里加一个既无引用也没标“论文未定义”的符号
        self.analysis_bad_number = False    # 解读里编造一个具体数字
        self.analysis_fake_ord = False      # 模块描述引用不存在的段落
        self.qa_reply: dict | None = None
        self.json_garbage_first = False     # 视觉模型第一次输出不是 JSON
        self._garbage_sent = False

    def handler(self, request):
        """fail_formula_call=N：第 N 次起的公式转写请求都返回 HTTP 400（模拟第二次交叉核对调用失败）。"""
        if self.fail_formula_call is not None:
            body = json.loads(request.content or b"{}")
            sys_text = "\n".join(m["content"] for m in body.get("messages", []) if m.get("role") == "system" and isinstance(m["content"], str))
            if "数学公式转写器" in sys_text:
                self.formula_requests += 1
                if self.formula_requests >= self.fail_formula_call:  # 含网关适配参数后的重试，保证这次调用确实失败
                    import httpx

                    self.requests.append({"url": str(request.url), "body": body, "headers": dict(request.headers)})
                    return httpx.Response(400, text="bad request")
        return super().handler(request)

    # ---- 视觉模型的 JSON 输出 ----
    def _framework(self) -> dict:
        return self.framework_raw or {
            "summary": "离线训练与在线应用两阶段框架", "modules": [
                {"id": "m1", "label": "Synthetic graphs", "role": "训练用的合成图", "kind": "data", "group": "Offline training"},
                {"id": "m2", "label": "Encoder", "role": "把图编码成向量", "kind": "model", "group": "Offline training"},
                {"id": "m3", "label": "Decoder", "role": "输出节点分数", "kind": "model", "group": "Offline training"},
                {"id": "m4", "label": "Experience replay buffer", "role": "存放训练样本", "kind": "data", "group": ""},
            ],
            "flows": [
                {"from": "m1", "to": "m2", "label": "f_E", "kind": "data"},
                {"from": "m2", "to": "m3", "label": "f_D", "kind": "data"},
                {"from": "m3", "to": "m4", "label": "Reward", "kind": "loss"},
                {"from": "m3", "to": "m99", "label": "", "kind": "data"},   # 不存在的模块：应被丢弃
            ],
            "groups": [{"id": "g1", "label": "Offline training", "members": ["m1", "m2", "m3"]}],
            "uncertain": ["右下角的机器人图标没有文字"],
        }

    def _chart(self) -> dict:
        return self.chart_raw or {"panels": [{
            "label": "a", "chart_type": "line", "title": "ANC on BA graphs",
            "x_axis": {"label": "Graph size", "unit": "", "range": "30-50"}, "y_axis": {"label": "ANC", "unit": "", "range": "0-0.5"},
            "series": ["FINDER", "HDA"], "observations": ["FINDER 的曲线整体低于 HDA", "两者差距随规模增大而变大"],
            "values": [
                {"text": "FINDER 在最大规模下的 ANC", "value": "0.2345", "precision": "exact"},   # 文字层里没有：应降级
                {"text": "HDA 在最大规模下的 ANC", "value": "0.3", "precision": "approx"},
            ],
        }], "unreadable": ["图例右侧被遮挡"]}

    def _table(self) -> dict:
        return self.table_raw or {"title": "Table 1 Datasets", "columns": ["Network", "Nodes", "Edges"],
                                  "rows": [["Karate", "34", "78"], ["Dolphins", "62", "159"], ["Fake", "999", "12345"]],
                                  "notes": "n/a"}

    # ---- 路由 ----
    def _answer(self, model: str, system: str, user: str) -> str:
        for i, (marker, text) in enumerate(self.override_queue):  # 按系统提示里的标记，一次性覆盖某类请求的回复
            if marker in system:
                self.override_queue.pop(i)
                return text
        if "行内的一小段公式" in system:  # 行内公式兜底：按“图 k（提取出的文字：…）”逐项作答；提示里含 BAD 的故意答错
            self.inline_calls = getattr(self, "inline_calls", 0) + 1
            items = []
            for m in re.finditer(r"图 (\d+)（提取出的文字：([^）]*)）", user):
                hint = m.group(2)
                items.append({"n": int(m.group(1)), "latex": "q_{9}" if "BAD" in hint else re.sub(r"\s+", "", hint)})
            return json.dumps({"items": items}, ensure_ascii=False)
        if "公式校对员" in system:
            return self.arbiter_reply
        if "数学公式转写器" in system:
            self.formula_calls += 1
            if self.formula_calls % 2 == 0 and self.latex_reply_second is not None:  # 第二次（高分辨率）转写
                return self.latex_reply_second
            return self.latex_reply
        if "判断图片类型" in system:
            return json.dumps({"kind": self.classify_kind, "reason": "测试"}, ensure_ascii=False)
        if "框架图/流程图转成结构化" in system:
            if self.json_garbage_first and not self._garbage_sent:
                self._garbage_sent = True
                return "好的，我来分析这张图：模块有 Encoder 和 Decoder。"
            if self.not_framework:
                return json.dumps({"not_a_framework": True, "actual_kind": "chart"})
            return json.dumps(self._framework(), ensure_ascii=False)
        if "实验结果图" in system:
            return json.dumps(self._chart(), ensure_ascii=False)
        if "表格转录器" in system:
            return json.dumps(self._table(), ensure_ascii=False)
        if "回答关于一张论文图的问题" in system:
            return json.dumps(self.qa_reply or {"answerable": False, "answer": "这个问题涉及运行时间，图里没有标出。", "seen": "只有曲线", "numbers": []}, ensure_ascii=False)
        ords = self._ords(user)
        a = ords[0] if ords else 0
        b = ords[1] if len(ords) > 1 else a
        if "公式讲解助手" in system:
            sym = "- $N$：网络的节点总数 [P%d]\n- $\\sigma(\\mathcal{G})$：最大连通分量的大小 [P%d]\n- $c(v)$：论文未定义\n" % (a, b)
            if self.explain_extra_symbol:
                sym += "- $Z$：归一化常数\n"
            return (
                f"## 直接回答\n该公式计算删除节点序列后的平均网络鲁棒性 [P{a}]。\n"
                f"## 符号表\n{sym}"
                f"## 详细解释\n分子是删除前 k 个节点后的最大连通分量，分母是原图的最大连通分量 [P{b}]。\n"
                f"## 与其他公式的关系\n公式 (2) 在此基础上加入了节点代价 [P{b}]。\n"
                f"## 依据\n- [P{a}] 给出了公式及符号定义\n"
                f"## AI 推断\n- 这个指标越小，说明攻击越有效（依据：[P{a}]）\n"
                f"## 后续问题\n- 为什么对 k 取平均？\n"
            )
        if "规则：" in system and "只输出一个 JSON 对象" in system:
            n = f"约 {'99.7%'} 的提升" if self.analysis_bad_number else "整体更低"
            if "解释这张框架图" in user:
                mods = re.findall(r"^- (m\d+)：", user, re.M)
                items = []
                for i, mid in enumerate(mods):
                    if i == 0 and self.analysis_fake_ord:
                        items.append({"id": mid, "description": "论文说它是合成数据 [P9999]", "ords": [9999]})
                    elif i == 1:
                        items.append({"id": mid, "description": f"编码器把图映射为节点向量 [P{a}]", "ords": [a]})
                    else:
                        items.append({"id": mid, "description": None, "ords": []})
                return json.dumps({
                    "overview": f"这张图展示 FINDER 的离线训练与在线应用 [P{a}]", "flow_explanation": f"数据依次经过编码器和解码器 [P{b}]",
                    "modules": items, "inferences": [{"text": "回放缓冲区可能用于打破样本相关性", "basis_ords": [a]}],
                    "limitations": ["右下角图标无文字"],
                }, ensure_ascii=False)
            return json.dumps({
                "direct": f"FINDER 的 ANC {n} [P{a}]", "points": [{"text": f"FINDER 曲线低于 HDA [P{a}]", "ords": [a]}],
                "paper_claims": [{"text": f"论文称 FINDER 优于基线 [P{b}]", "supported": "partly", "note": "图中趋势一致"}],
                "inferences": [], "limitations": ["数值只能读图估计"],
            }, ensure_ascii=False)
        return super()._answer(model, system, user)
