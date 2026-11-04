"""证据核实：解析模型输出，把 [P序号] 引用映射回真实段落，识别编造的引用。

关键保证：返回给前端的 `evidence.quote` 永远取自数据库里的真实段落文本，而不是模型生成的“引文”。
模型的自检不能替代这一步的独立核对。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import websearch
from .retrieval import ContextPack
from .router import TaskType

CITE_RE = re.compile(r"\[P(\d+)\]")
# 真实模型的引用写法五花八门：[P3]、[P3, P7]、[P3、P7]、【P3】、（依据：P3）、[来源：P3、P7]、[依据：P5] …
SCITE_RE = re.compile(r"\[S(\d+)P(\d+)\]")  # 补充材料引用：[S{补充文档序号}P{段落序号}]
_ANY_CITES = r"(?:\[(?:P\d+|S\d+P\d+)\]\s*)+"
_CITE_TOKEN = r"(?:S\d+\s*[-:.]?\s*P\d+|P\d+)"
_CITE_SEP = r"(?:[,，、;；/]|和|与|及)"
CITE_GROUP_RE = re.compile(
    r"[\[【(（]\s*(?:依据|来源|见|参见|出处|引用|参考)?\s*[:：]?\s*"
    r"(" + _CITE_TOKEN + r"(?:\s*" + _CITE_SEP + r"\s*(?:" + _CITE_TOKEN + r"|\d+))*)\s*[\]】)）]"
)
# 诚实地说“摘录里没有这个信息”：这类回答没有事实断言，不应被提示“缺少依据”
INSUFFICIENT_RE = re.compile(
    r"没有足够(?:的)?信息|无法确认|无法判断|无法从.{0,8}(?:得知|确定)|(?:摘录|论文|文中|原文|文章)(?:里|中)?(?:并|也|还)?(?:没有|没|未)(?:详细|明确|具体|直接|进一步)?(?:提及|提到|给出|包含|说明|涉及|描述|解释|讲|写|阐述|介绍|披露)|"
    r"not enough information|cannot be determined|not (?:mentioned|stated)",
    re.I,
)
SECTION_MAP = {
    "直接回答": "direct", "详细解释": "detail", "依据": "evidence", "ai 推断": "inference", "ai推断": "inference",
    "后续问题": "follow_ups", "后续研究": "follow_ups",
    "论文之外": "outside", "论文以外": "outside", "论文之外的补充": "outside", "补充知识": "outside",
}
QUOTE_MAX = 320


def normalize_cites(text: str) -> str:
    """[P3, P7] / [P3、P7] → [P3][P7]；[S1P12] / [S1-P12] / [S1P12, 14] → [S1P12][S1P14]，方便统一解析与前端渲染。"""

    def repl(m: re.Match) -> str:
        out: list[str] = []
        doc: int | None = None
        for part in re.split(r"\s*" + _CITE_SEP + r"\s*", m.group(1)):
            sm = re.fullmatch(r"S(\d+)\s*[-:.]?\s*P(\d+)", part)
            pm = re.fullmatch(r"P(\d+)", part)
            if sm:
                doc = int(sm.group(1))
                out.append(f"[S{doc}P{sm.group(2)}]")
            elif pm:
                doc = None
                out.append(f"[P{pm.group(1)}]")
            elif part.isdigit():  # “[S1P12, 14]”里的裸数字沿用前一个引用的文档
                out.append(f"[S{doc}P{part}]" if doc else f"[P{part}]")
        return "".join(out)

    return CITE_GROUP_RE.sub(repl, text)


def cited_ords(text: str) -> list[int]:
    """正文引用 [P12] 的序号（保序去重）。补充材料引用见 cited_refs。"""
    seen: list[int] = []
    for m in CITE_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def cited_refs(text: str) -> list[tuple[int, int]]:
    """所有引用，保持出现顺序并去重：(0, ord) 表示正文，(n, ord) 表示第 n 个补充文档。"""
    found: list[tuple[int, int, int]] = []
    for m in CITE_RE.finditer(text):
        found.append((m.start(), 0, int(m.group(1))))
    for m in SCITE_RE.finditer(text):
        found.append((m.start(), int(m.group(1)), int(m.group(2))))
    seen: list[tuple[int, int]] = []
    for _pos, doc, ord_ in sorted(found):
        if (doc, ord_) not in seen:
            seen.append((doc, ord_))
    return seen


def ref_tag(doc: int, ord_: int) -> str:
    return f"P{ord_}" if doc == 0 else f"S{doc}P{ord_}"


def split_sections(markdown: str) -> dict[str, str]:
    """按 `## 标题` 切分；没有任何已知标题时整段视为直接回答。"""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    preamble: list[str] = []
    for line in markdown.splitlines():
        if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            continue  # 模型有时在小节末尾加分隔线，不属于内容
        m = re.match(r"^\s{0,3}#{1,3}\s*(.+?)\s*$", line)
        key = SECTION_MAP.get(m.group(1).strip().lower().replace("：", "").replace(":", "")) if m else None
        if key:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = key, []
        elif current is None:
            preamble.append(line)
        else:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    pre = "\n".join(preamble).strip()
    if pre and "direct" not in out:
        out["direct"] = pre
    if not out and markdown.strip():
        out["direct"] = markdown.strip()
    return out


def _bullets(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.、)])\s+(.*\S)\s*$", line)
        if m:
            items.append(m.group(1))
        elif items and line.strip():
            items[-1] += " " + line.strip()
    return items


def best_excerpt(text: str, hint: str = "", limit: int = QUOTE_MAX) -> str:
    """从真实段落里挑出与说明最相关的句子（无说明则取开头）。"""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    hint_words = set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", hint.lower()))
    best, best_score = sentences[0], -1
    for i, s in enumerate(sentences):
        score = len(hint_words & set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", s.lower())))
        if score > best_score:
            best, best_score = s, score
            if i + 1 < len(sentences) and len(best) < limit // 2:
                best = f"{best} {sentences[i + 1]}"
    return best[:limit] + ("…" if len(best) > limit else "")


def integrity_issues(text: str) -> list[str]:
    """检测模型输出里疑似“缺词”的迹象。实测某些服务端在公式密集内容上会漏掉一段 token
    （例如 `$ a_i $ $ W_t a_i $` 中间缺了“和时间注意力输出 $ h_i $ 相加”），解析层无法发现，只能事后检查。"""
    issues: list[str] = []
    plain = text.replace("$$", "")
    if plain.count("$") % 2 == 1:
        issues.append("公式定界符 $ 不成对")
    if re.search(r"\$\s+\$", plain):
        issues.append("两个公式之间紧挨着、中间可能缺了文字")
    if text.count("**") % 2 == 1:
        issues.append("加粗标记不成对")
    return issues


@dataclass
class ParsedAnswer:
    answer: dict
    evidence: list[dict]
    verification: dict


def parse_answer(markdown: str, pack: ContextPack, task: TaskType | None = None, web: list[dict] | None = None) -> ParsedAnswer:
    markdown = normalize_cites(markdown)
    sections = split_sections(markdown)
    direct = sections.get("direct", "")
    detail = sections.get("detail") or None
    outside = sections.get("outside") or None
    web_used = websearch.cited_web(markdown, web or []) if outside or markdown else []

    notes: list[tuple[int, dict]] = []  # (文档序号, 条目)
    for b in _bullets(sections.get("evidence", "")):
        brefs = cited_refs(b)
        if brefs:
            d0, o0 = brefs[0]
            note_text = SCITE_RE.sub("", CITE_RE.sub("", b)).strip(" ：:-—")
            notes.append((d0, {"ord": o0, "note": note_text, "ref": ref_tag(d0, o0)}))
    inferences = []
    for b in _bullets(sections.get("inference", "")):
        basis = cited_ords(b)
        basis_refs = [ref_tag(d, o) for d, o in cited_refs(b)]
        # 只清理“括号里仅有 依据：[P..]”或句末“依据：[P..]”的标准形式；括号里还有解释文字时保持原样，避免吃字
        text = re.sub(r"\s*[（(]\s*依据[:：]?\s*" + _ANY_CITES + r"[）)]", "", b)
        text = re.sub(r"[；;，,]?\s*依据[:：]\s*" + _ANY_CITES + r"(?=[。.]?\s*$)", "", text)
        text = re.sub(r"^\s*(?:\*\*)?(?:推断内容|推断)(?:\*\*)?[:：]\s*", "", text)
        # 引用已单独记录在 basis_ords/basis_refs，句末的引用标记不再重复留在文本里
        text = re.sub(r"\s*(?:\[(?:P\d+|S\d+P\d+)\])+(?=\s*[。.；;]?\s*$)", "", text).strip(" ；;")
        inferences.append({"text": text, "basis_ords": basis, "basis_refs": basis_refs})
    follow_ups = [f for f in _bullets(sections.get("follow_ups", ""))][:3]

    refs = cited_refs(markdown)
    valid_refs = {(p.doc, p.ord) for p in pack.passages}
    invalid = [o for d, o in refs if d == 0 and (0, o) not in valid_refs]  # 正文里不存在的编号（整数，兼容旧前端）
    invalid_refs = [ref_tag(d, o) for d, o in refs if (d, o) not in valid_refs]  # 全部无效引用（含补充材料，字符串）
    note_by_ref = {n["ref"]: n["note"] for _d, n in notes}

    evidence = []
    for d, o in refs:
        p = pack.passage(o, d)
        if p is None:
            continue
        tag = ref_tag(d, o)
        entry = {
            "ord": p.ord, "page": p.page, "section_title": p.section_title,
            "quote": best_excerpt(p.text, note_by_ref.get(tag, "")), "bbox": p.bbox,
            "kind": "paper_text" if d == 0 else "supplement_text", "ref": tag,
        }
        if d:
            info = pack.doc_info(d) or {}
            entry["source"] = {"paper_id": info.get("paper_id"), "role": "supplement", "doc": d,
                               "label": info.get("label") or f"补充材料 {d}", "asset_id": info.get("asset_id")}
        evidence.append(entry)

    notes_out = [n for d, n in notes if (d, n["ord"]) in valid_refs]
    claims_facts = bool(pack.passages) and bool(direct.strip())
    v_notes: list[str] = []
    if invalid_refs:  # 含补充材料里不存在的编号（如 S1P9999）
        status = "partial"
        v_notes.append("回答中引用了不存在于所给摘录的段落编号，已标记为无法核实。")
    else:
        status = "verified"
    if claims_facts and not evidence:
        if INSUFFICIENT_RE.search(direct):
            status = "unverified"
            v_notes.append("模型说明论文摘录中没有该信息，没有作事实断言。")
        else:
            status = "no_evidence"
            v_notes.append("回答没有附带可核实的原文依据，请谨慎对待。")
    elif status == "verified" and not any(r in valid_refs for r in refs):
        status = "unverified"
    if outside:
        v_notes.append("“论文之外”的内容来自模型自身知识" + ("和联网查到的资料" if web_used else "") + "，不是论文原文，请自行判断。")
        if status == "no_evidence":
            status = "unverified"
    if "direct" not in sections:
        v_notes.append("模型输出未按约定格式组织。")
    broken = integrity_issues(f"{direct}\n{detail or ''}")
    if broken:
        v_notes.append("回答文字可能有缺失：" + "；".join(broken) + "，建议重新生成。")
        if status in ("verified", "unverified"):
            status = "partial"
    return ParsedAnswer(
        answer={
            "direct": direct, "detail": detail, "evidence_notes": notes_out,
            "inferences": inferences, "follow_ups": follow_ups, "outside": outside, "web": web_used,
        },
        evidence=evidence,
        verification={"status": status, "invalid_citations": invalid, "invalid_refs": invalid_refs, "notes": v_notes},
    )


def verify_ords(texts: list[str], pack: ContextPack, listed: list[int] | None = None) -> tuple[list[dict], dict]:
    """用于结构化分析（JSON 字段内文本）：汇总文本里的引用与字段显式列出的序号并核实。"""
    joined = normalize_cites("\n".join(texts))
    all_cited = list(dict.fromkeys([*cited_ords(joined), *(listed or [])]))
    valid = {p.ord for p in pack.passages}
    invalid = [o for o in all_cited if o not in valid]
    evidence = []
    for o in all_cited:
        p = pack.passage(o)
        if p:
            evidence.append({"ord": p.ord, "page": p.page, "section_title": p.section_title,
                             "quote": best_excerpt(p.text), "bbox": p.bbox, "kind": "paper_text"})
    status = "no_evidence" if not evidence else ("partial" if invalid else "verified")
    notes = ["部分引用编号不存在于所给摘录，已忽略。"] if invalid else []
    return evidence, {"status": status, "invalid_citations": invalid, "notes": notes}
