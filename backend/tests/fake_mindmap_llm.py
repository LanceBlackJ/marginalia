"""思维导图 / 主动研究提示测试用的假 LLM：在 FakeLLM 之上按系统提示分发，并可切换各种“坏输出”。

- 思维导图：从用户消息的摘录里取真实的 [P序号] 组树；mode 控制好/坏输出
  good | fabricated（夹带不存在的编号）| thin（只有根）| notjson（第一次不是 JSON）| always_bad | all_fabricated
- 研究提示：scan_mode 控制 issues | none | fabricated | many | dup | lowconf | badanchor | refs
- gate：不为 None 时，思维导图/扫描请求会阻塞到 gate.set()（用来测试取消）
"""
from __future__ import annotations

import json
import re
import threading

from fakellm import FakeLLM


class FakeMindmapLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.mode = "good"
        self.scan_mode = "issues"
        self.suffix = ""  # 叶子标题后缀：用来制造“新一轮生成结果与旧结果不同”
        self.gate: threading.Event | None = None
        self.mindmap_calls = 0
        self.scan_calls = 0
        self.last_system = ""
        self.last_user = ""

    # ---- 思维导图 ----
    @staticmethod
    def _section_ords(user: str) -> list[int]:
        body = user.split("【论文摘录】", 1)[-1]
        out: list[int] = []
        for m in re.finditer(r"^\[P(\d+)\]", body, re.M):
            n = int(m.group(1))
            if n not in out:
                out.append(n)
        return out

    @staticmethod
    def _ids(user: str, header: str) -> list[int]:
        if header not in user:
            return []
        block = user.split(header, 1)[1].split("\n\n", 1)[0]
        return [int(x) for x in re.findall(r"^(\d+):", block, re.M)]

    def _tree(self, user: str) -> str:
        ords = self._section_ords(user)
        figs = self._ids(user, "【可关联的图表（figure_ids）】")
        eqs = self._ids(user, "【可关联的公式（equation_ids）】")
        if self.mode == "thin":
            return json.dumps({"root": {"title": "只有根", "summary": "", "kind": "root", "evidence_ords": ords[:1], "children": []}}, ensure_ascii=False)
        if self.mode == "all_fabricated":
            ords = [90001, 90002, 90003, 90004, 90005, 90006]

        def pick(i: int) -> list[int]:
            if not ords:
                return []
            return [ords[i % len(ords)]]

        groups = ["研究问题", "核心方法", "实验验证"]
        kinds = ["problem", "method", "result"]
        children = []
        k = 0
        for gi, g in enumerate(groups):
            leaves = []
            for li in range(2):
                leaf: dict = {"title": f"{g}要点{'一二'[li]}{self.suffix}", "summary": f"说明{g}的第{li + 1}个要点 [P{pick(k)[0] if ords else 0}]",
                              "kind": ["topic", "concept", "metric"][li], "evidence_ords": pick(k), "children": []}
                if self.mode == "fabricated" and li == 1:
                    leaf["evidence_ords"] = [*leaf["evidence_ords"], 99991]
                if gi == 1 and figs:
                    leaf["figure_ids"] = [figs[0], 99999]
                if gi == 1 and eqs and li == 0:
                    leaf["equation_ids"] = [eqs[0]]
                leaves.append(leaf)
                k += 1
            node = {"title": g + self.suffix * 0, "summary": f"{g}部分的概述", "kind": kinds[gi], "evidence_ords": [], "children": leaves}
            children.append(node)
        # 一个完全没有依据的叶子：必须被丢弃
        children.append({"title": "编造的结论", "summary": "没有依据", "kind": "conclusion", "evidence_ords": [88888], "children": []})
        if "【任务：只重新生成其中一个分支】" in user:
            # 分支重新生成：只给两个新的下级
            kids = [{"title": f"新分支要点{n}{self.suffix}", "summary": "分支重新生成的内容", "kind": "topic",
                     "evidence_ords": pick(n + 3), "children": []} for n in range(2)]
            return json.dumps({"root": {"title": "忽略", "summary": "", "kind": "root", "evidence_ords": ords[:1], "children": kids}}, ensure_ascii=False)
        root = {"title": "测试论文主题", "summary": "全文主题", "kind": "root", "evidence_ords": ords[:2], "children": children}
        if self.mode == "toodeep":
            deep = {"title": "第五层", "summary": "", "kind": "topic", "evidence_ords": pick(0), "children": []}
            for t in ("第四层", "第三层"):
                deep = {"title": t, "summary": "", "kind": "topic", "evidence_ords": pick(0), "children": [deep]}
            children[0]["children"][0]["children"] = [deep]
        return json.dumps({"root": root}, ensure_ascii=False)

    # ---- 研究提示 ----
    def _scan(self, user: str) -> str:
        ords = self._section_ords(user) or [0]
        text_by_ord = {int(m.group(1)): m.group(2) for m in re.finditer(r"^\[P(\d+)\] \([^)]*\)\n(.+)$", user, re.M)}

        def anchor(o: int) -> str:
            return " ".join(text_by_ord.get(o, "").split()[:5])

        def f(i: int, t: str, **kw) -> dict:
            o = ords[i % len(ords)]
            d = {"type": t, "title": f"提示{i}：{t}", "detail": f"摘录里没有看到与第{i}点相关的说明，值得核对原文。", "evidence_ords": [o],
                 "anchor": anchor(o), "basis": "stated", "confidence": "high"}
            d.update(kw)
            if d["detail"] == "REFS":  # 模型把内部段落编号写进了正文
                d["detail"] = f"论文在P{o}明确指出了该设置，参见[P{o}]；摘录中没有看到更多说明（P{o}）。"
            return d

        m = self.scan_mode
        if m == "none":
            return json.dumps({"verdict": "no_issues", "summary": "论证充分，没有需要特别提示的问题。", "findings": []}, ensure_ascii=False)
        types = ["weak_motivation", "weak_support", "unexplained_param", "overreach", "further_reading"]
        if m == "many":
            names = ["消融实验缺失", "学习率没有来源", "只在合成数据上验证", "值得顺着局限继续读", "对比基线偏弱", "阈值缺少依据", "泛化主张证据不足",
                     "模块动机未解释", "评价指标过于单一", "批大小未交代", "大规模适用性无实验", "相关工作缺少比较"]
            items = [f(i, types[i % 5], title=names[i]) for i in range(12)]
        elif m == "dup":
            items = [f(0, "weak_support", title="消融实验没有单独验证模块"), f(1, "weak_support", title="消融实验没有单独验证模块 "), f(2, "overreach")]
        elif m == "fabricated":
            items = [f(0, "weak_support", evidence_ords=[777777]), f(1, "overreach")]
        elif m == "lowconf":
            items = [f(0, "weak_support", confidence="low"), f(1, "overreach", confidence="medium"), f(2, "bogus_type")]
        elif m == "refs":
            items = [f(0, "weak_support", detail="REFS", title="只在小图上验证 [P1]")]
        elif m == "badanchor":
            items = [f(0, "weak_support", anchor="这句话根本不在原文里 zzz qqq")]
        else:  # issues
            items = [f(0, "weak_support"), f(1, "unexplained_param", basis="inferred", confidence="medium"), f(2, "further_reading")]
        return json.dumps({"verdict": "issues", "summary": "有几处值得核查。", "findings": items}, ensure_ascii=False)

    def _answer(self, model: str, system: str, user: str) -> str:
        if "思维导图" in system and "论文结构分析助手" in system:
            self.mindmap_calls += 1
            self.last_system, self.last_user = system, user
            self._wait_gate()
            if self.mode == "notjson" and self.mindmap_calls == 1:
                return "好的，下面是思维导图：（没有 JSON）"
            if self.mode == "thin_once" and self.mindmap_calls == 1:
                self.mode_saved = self.mode
                prev, self.mode = self.mode, "thin"
                try:
                    return self._tree(user)
                finally:
                    self.mode = prev
            if self.mode == "always_bad":
                return "我无法完成。"
            return self._tree(user)
        if "审慎、克制的论文阅读助手" in system:
            self.scan_calls += 1
            self.last_system, self.last_user = system, user
            self._wait_gate()
            if self.scan_mode == "notjson_once" and self.scan_calls == 1:
                return "没有 JSON"
            return self._scan(user)
        return super()._answer(model, system, user)

    def _wait_gate(self) -> None:
        if self.gate is not None:
            self.gate.wait(timeout=20)
