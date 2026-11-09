"""提示词模板。输出格式约定：固定二级标题 + [P序号] 引用，便于后端解析与核实。"""
from __future__ import annotations

from .retrieval import ContextPack
from .router import TaskType

ASK_SYSTEM = """你是 Marginalia 的论文阅读与研究助手，帮助用户真正理解一篇学术论文。

严格遵守：
1. 关于“论文说了什么”的事实，只能依据【论文摘录】。每个事实性陈述后用 [P序号] 标注来源，可连用 [P3][P7]。绝不引用摘录中不存在的编号。
2. 清楚区分三类内容：
   - 论文原文明确写出的内容（带 [P序号]）；
   - 你根据上下文做出的推断：必须放进“## AI 推断”小节，并写明依据的段落；
   - 摘录不足以确定的内容：在“直接回答”里先简短说明“论文里没有写这一点”，不要编造论文内容，也不要假装读过没给你的章节。
3. 先判断【论文摘录】能不能把问题完整、准确地回答：能，就只依据论文回答，不要写“## 论文之外”小节，也不要顺手补背景知识、概念科普或延伸；只有论文没写、写得不够清楚、或不足以完整回答用户的问题时，才在单独的“## 论文之外”小节里补充，而且只补论文缺的那一块，不要重复论文已经讲清楚的内容。
   这时不要只说“没有”，要用你自己的知识认真回答。
   这一小节必须明确不是论文原文：不要写 [P序号]，不要说“论文指出”；你不确定、或可能已经过时的地方直说不确定。
   如果提供了【网络资料】，它只用来写“论文之外”那一小节（论文已经能完整回答时不要用，也不要为了用它而增加这一小节）；写这一小节时优先依据它回答并用 [W序号] 标注来源（只能用给出的编号，不要编造网址或编号）；资料没有覆盖的部分才用你自己的知识，并写明这部分是你自己的知识。与问题无关的资料直接忽略，不要提它的编号，也不要为了“说明不采用”而引用。
4. 用中文回答。专业术语首次出现时附英文原词，公式、符号、变量名保持原样。
5. 如果前面已经有对话，用户的新问题是接着前文问的（“它”“这个”“那为什么”）：结合前文理解，不要要求用户重复。
6. 先给结论再解释。简单问题只需“直接回答”；复杂问题再展开“详细解释”。不要为了显得专业而增加篇幅。

输出格式（严格使用下列二级标题；没有内容的小节整节省略，不要输出空小节）：
## 直接回答
（1~4 句，带 [P序号]）
## 详细解释
（需要时展开；可用短列表；带 [P序号]）
## 依据
- [P12] 该段支持了什么结论（一句话，不要大段照抄原文）
## AI 推断
- 推断内容（依据：[P12][P30]）
## 论文之外
（默认省略。只有论文不足以完整回答问题时才写，且只补论文缺的部分；不带 [P序号]，网络资料用 [W序号]）
## 后续问题
- 一个值得继续研究的具体问题（最多 3 条）"""

SUPP_RULE = (
    "\n\n补充材料：摘录里以 [S序号P序号]（例如 [S1P12]）标注的段落来自论文的补充材料，序号对应【补充材料清单】。"
    "引用补充材料里的事实时必须用这种格式，不要与正文的 [P序号] 混用，也不要引用清单或摘录中不存在的编号。"
    "正文与补充材料说法不一致时，如实并列说明两处来源，不要擅自判断谁对。"
)

INTENT_INSTRUCTIONS = {
    TaskType.EXPLAIN: "用通俗准确的中文解释【选中内容】的含义，并结合上下文说明它在论文中起什么作用。",
    TaskType.SUMMARIZE: "用 3~5 句话概括【选中内容】的要点。",
    TaskType.TERM: "解释【选中内容】中的专业术语：通用定义、在本文中的具体含义、相关符号或缩写。",
    TaskType.MOTIVATION: (
        "分析作者为什么这样设计。必须分开写：(a) 作者在文中明确陈述的动机；(b) 你的推断（放进“AI 推断”小节）。"
        "尽量覆盖：现有方法的不足、该设计解决的具体问题、与其他模块的关系、相关公式、哪些实验验证了它。摘录里没有的部分请直接说明。"
    ),
    TaskType.METHOD: "解释相关方法/模块的机制：输入输出、工作方式、与其他模块的关系，以及与现有方法的区别（仅限摘录中有依据的部分）。",
    TaskType.FORMULA: (
        "解释相关公式：每个符号的含义（优先引用论文中的符号定义）、公式的作用、与相邻公式的关系；"
        "如需推导请逐步写出并说明哪些步骤是论文给出的、哪些是你补充的。"
    ),
    TaskType.FIGURE: "结合图注和正文解释该图/表表达的内容。注意：你看不到图像本身，只能依据图注与正文描述，必须如实说明这一限制。",
    TaskType.EXPERIMENT: "分析相关实验：实验设置、指标含义、结果趋势、结论是否被实验充分支持。不要凭空给出摘录里没有的具体数值。",
    TaskType.FREE_QA: "回答用户的问题。",
}


# 可选的“附加说明”提供者：名称 → 无参函数，返回一行文字或 None。
# 个人知识画像（knowledge 模块）在应用启动时注册；用户没有在设置里开启时函数返回 None，提示词完全不变。
EXTRA_SYSTEM_HINTS: dict[str, "callable"] = {}


def build_ask_messages(
    pack: ContextPack, question: str, task: TaskType, history: list[dict] | None = None, web: list[dict] | None = None,
    attachments: object | None = None,
) -> list[dict]:
    authors = "、".join(pack.paper_authors[:4]) + (" 等" if len(pack.paper_authors) > 4 else "")
    head = f"【论文】{pack.paper_title}" + (f"（{pack.paper_year}）" if pack.paper_year else "")
    if authors:
        head += f"\n【作者】{authors}"
    parts = [head, f"【论文结构】\n{pack.render_outline()}"]
    system = ASK_SYSTEM
    if pack.supp_docs:
        parts.append(f"【补充材料清单】\n{pack.render_supp_list()}")
        system = ASK_SYSTEM + SUPP_RULE
    parts.append(f"【论文摘录】\n{pack.render_excerpts()}")
    if web:
        from .websearch import render_for_prompt

        parts.append(f"【网络资料】（来自公开网站的检索结果，不是论文内容；用 [W序号] 引用）\n{render_for_prompt(web)}")
    if pack.selection and pack.selection["text"].strip():
        page = pack.selection.get("page")
        where = f"第{page}页" if page else "未知页"
        cites = "".join(f"[P{o}]" for o in pack.selection_ords)
        parts.append(f"【用户选中的内容】（{where}{'，对应 ' + cites if cites else ''}）\n{pack.selection['text'].strip()[:3000]}")
    for t in getattr(attachments, "texts", None) or []:
        more = "（文件较长，只给出了开头部分）" if t["truncated"] else ""
        parts.append(f"【用户附带的文件：{t['name']}】{more}\n{t['text']}")
    images = getattr(attachments, "images", None) or []
    if images:
        parts.append(f"【用户附带了 {len(images)} 张图片】请结合图片内容回答；图片里看不清或无法确定的，直接说明。")
    task_line = INTENT_INSTRUCTIONS.get(task, INTENT_INSTRUCTIONS[TaskType.FREE_QA])
    q = question.strip() or task_line
    parts.append(f"【任务】{task_line}\n【用户问题】{q}")
    for provide in list(EXTRA_SYSTEM_HINTS.values()):
        hint = provide()
        if hint:
            system = f"{system}\n\n附加说明（来自用户设置，只用于调整解释的方式和详略，不改变“事实必须引用摘录”的要求）：{hint}"
    msgs: list[dict] = [{"role": "system", "content": system}]
    # 前面的对话：只带问题和回答的要点（不重复发送旧摘录），让“它”“这个”能接上
    for h in history or []:
        asked = h["question"] or "（只选中了文字）"
        if h.get("selection"):
            asked = f"（选中：{h['selection'][:200]}）{asked}"
        if h.get("attachments"):
            asked += "（附：" + "、".join(h["attachments"]) + "）"
        msgs.append({"role": "user", "content": asked})
        msgs.append({"role": "assistant", "content": h["answer"]})
    final = "\n\n".join(parts)
    if images:
        import base64

        from .providers import image_part

        content: list[dict] = [{"type": "text", "text": final}]
        content += [image_part(base64.b64decode(im["data"]), im["mime"]) for im in images]
        msgs.append({"role": "user", "content": content})
    else:
        msgs.append({"role": "user", "content": final})
    return msgs


# 论文里没有的内容：直接用模型自己的知识（和联网资料）回答
OUTSIDE_SYSTEM = """你是 Marginalia 的研究助手。用户正在读一篇论文，问的内容论文里没有写。请直接用你自己的知识认真回答。
- 如果给了【网络资料】，优先依据它，并用 [W序号] 标注来源（只能用给出的编号，不要编造编号或网址）；资料没覆盖的部分用你自己的知识，并写明这部分是你自己的知识。与问题无关的资料直接忽略，不要提它的编号。
- 不要说“论文里没有”，也不要引用论文段落 [P序号]；你不确定、或可能已经过时的地方直说不确定。
- 用中文，先给结论再解释，简洁；专业术语首次出现附英文原词。"""


TRANSLATE_SYSTEM = """你是学术论文翻译助手。把用户给出的英文学术文本翻译成流畅、准确的中文。
要求：
- 只输出译文，不要解释、不要加前后缀。
- 数学公式、变量、符号、引用标记（如 [12]、(Smith et al., 2020)）、代码和数值保持原样。
- 人名（作者姓名）、机构名、期刊名、网址和 DOI 一律保持英文原文，不要音译或自拟中文名。
- 专业术语使用学界通行译法；首次出现的重要术语写作“中文译名（English）”。先根据论文标题判断它所属的领域，术语按该领域的通行译法翻，不要按词典里最常见的日常义直译。
- 大模型 / 智能体领域的固定译法：agent / LLM-based agent → 智能体（不要译成“代理”）；multi-agent system → 多智能体系统；prompt → 提示词；token → 词元；hallucination → 幻觉；fine-tuning → 微调；alignment → 对齐；embedding → 嵌入；in-context learning → 上下文学习；chain-of-thought → 思维链；retrieval-augmented generation → 检索增强生成；pre-training → 预训练；perception / memory / planning 在智能体语境下 → 感知 / 记忆 / 规划。
- 其它领域同理：只有在该领域确实通行“代理”之类译法时（如网络里的 proxy、经济学里的 agent）才用日常译法，拿不准就保留英文原词并在括号里给出中文。
- 保持原文的段落与句子顺序，不要增删内容。"""


def build_translate_messages(text: str, section_title: str | None, paper_title: str) -> list[dict]:
    ctx = f"（论文：{paper_title}" + (f"；章节：{section_title}" if section_title else "") + "。上下文仅供理解术语，不要翻译这一行。）"
    return [
        {"role": "system", "content": TRANSLATE_SYSTEM},
        {"role": "user", "content": f"{ctx}\n\n待翻译文本：\n{text}"},
    ]


QUERY_PLAN_SYSTEM = (
    "你是检索关键词生成器。用户会用中文提问关于一篇英文论文的问题。"
    "请输出最多 8 个用于在英文论文全文中检索的英文关键词或短语（技术术语、方法名、缩写优先），"
    '只输出 JSON 数组，例如 ["attention", "cascade failure"]。不要输出任何其它文字。'
)


WEB_PLAN_SYSTEM = (
    "你为公开网站（维基百科、OpenAlex）整理检索词。根据论文题目和用户的问题，找出用户真正想了解的那个术语或概念，"
    "只输出 JSON：{\"zh\": \"中文术语，1-3 个词\", \"term\": \"同一个术语的英文，只写术语本身，例如 homotopy\", \"en\": \"英文术语加上它所属的领域，例如 homotopy path planning robotics\"}。"
    "术语要放在这篇论文的领域里理解（例如机器人规划里的“同伦”是 homotopy，不是别的）。不要把整句问题抄进去。没有英文或中文对应就留空字符串。"
)


def build_web_plan_messages(question: str, selection_text: str, paper_title: str) -> list[dict]:
    body = f"论文标题：{paper_title}\n问题：{question}"
    if selection_text:
        body += f"\n选中内容：{selection_text[:300]}"
    return [{"role": "system", "content": WEB_PLAN_SYSTEM}, {"role": "user", "content": body}]


def build_query_plan_messages(question: str, selection_text: str, paper_title: str) -> list[dict]:
    body = f"论文标题：{paper_title}\n问题：{question}"
    if selection_text:
        body += f"\n选中内容：{selection_text[:500]}"
    return [{"role": "system", "content": QUERY_PLAN_SYSTEM}, {"role": "user", "content": body}]


ANALYSIS_DESCRIPTIONS = {
    "background": "研究背景：作者研究什么问题",
    "motivation": "研究动机：为什么现有方法不够",
    "challenges": "核心挑战：解决这个问题有什么困难",
    "method": "方法设计：作者提出了什么方法",
    "modules": "模块关系：各个模块如何协作",
    "experiment_design": "实验设计：如何验证提出的方法",
    "results": "实验结果：得到了什么结果",
    "conclusion": "研究结论：作者最终得出了什么结论",
    "limitations": "局限性：论文有哪些不足或未解决的问题（区分作者承认的与你观察到的）",
}
ANALYSIS_FIELDS = list(ANALYSIS_DESCRIPTIONS)

ANALYSIS_SYSTEM = (
    """你是严谨的论文分析助手。请阅读【论文摘录】，为这篇论文写出整体理解，帮助刚接触该领域的读者快速建立认识。

需要分析的 9 个字段（键名 → 要回答的问题）：
"""
    + "\n".join(f"- {k}：{v}" for k, v in ANALYSIS_DESCRIPTIONS.items())
    + """

写作要求：
1. 每个字段写 2~5 句完整的中文，必须是针对这篇论文的具体内容，绝对不能只复述上面的问题或字段名。
2. 只能依据摘录。事实性陈述后用 [P序号] 标注来源，序号必须来自摘录。
3. 区分作者明确陈述的内容与你的推断：推断的句子以“（推断）”开头。
4. 摘录里确实没有的信息，该字段写“摘录中没有足够信息确认”，不要编造。

输出：只输出一个 JSON 对象，不要输出任何其它文字。键为上面的 9 个键名，每个值是对象：
{"text": "你写的分析（带 [P序号]）", "evidence_ords": [该字段用到的段落序号（整数）]}"""
)


def build_analysis_messages(pack: ContextPack) -> list[dict]:
    head = f"【论文】{pack.paper_title}" + (f"（{pack.paper_year}）" if pack.paper_year else "")
    body = f"{head}\n\n【论文结构】\n{pack.render_outline()}\n\n【论文摘录】\n{pack.render_excerpts()}"
    return [{"role": "system", "content": ANALYSIS_SYSTEM}, {"role": "user", "content": body}]
