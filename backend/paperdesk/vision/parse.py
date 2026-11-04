"""公式解释输出的解析与核实：在通用的 ai.evidence 之上，增加“符号表 / 与其他公式的关系 / 推导”三个小节。

沿用核心保证：返回的 evidence.quote 取自数据库里的真实段落；模型编造的 [P序号] 被标为无法核实；
符号表里每个符号必须有可核实的论文定义，或者明确写了“论文未定义”，否则会被标注为“缺少依据”。
"""
from __future__ import annotations

import re

from ..ai import evidence
from ..ai.retrieval import ContextPack

EXTRA_SECTIONS = {"符号表": "symbols", "符号说明": "symbols", "与其他公式的关系": "relations", "公式关系": "relations", "推导": "derivation"}
STD_KEYS = {"direct": "直接回答", "detail": "详细解释", "evidence": "依据", "inference": "AI 推断", "follow_ups": "后续问题"}
UNDEFINED_RE = re.compile(r"论文(?:中)?未(?:明确)?(?:给出|定义|说明)|未(?:明确)?定义|没有(?:明确)?定义")


def strip_cites(meaning: str) -> str:
    """去掉含义里的 [P序号] 标记（出处单独显示在“出处”列）。句末/标点前的标记直接删；
    句中的标记常常是主语（“但 [P105] 指出……”），直接删会留下残缺的句子，所以换成“原文”。"""
    text = re.sub(r"\s*(?:\[P\d+\])+(?=\s*(?:[。；;，,.)）]|$))", "", meaning)
    text = re.sub(r"\s*(?:\[P\d+\])+\s*", "原文", text)
    return text.strip(" 。；;")


def split_all(markdown: str) -> dict[str, str]:
    mapping = {**evidence.SECTION_MAP, **EXTRA_SECTIONS}
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    pre: list[str] = []
    for line in markdown.splitlines():
        if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            continue
        m = re.match(r"^\s{0,3}#{1,3}\s*(.+?)\s*$", line)
        key = mapping.get(m.group(1).strip().lower().replace("：", "").replace(":", "")) if m else None
        if key:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = key, []
        elif current is None:
            pre.append(line)
        else:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    head = "\n".join(pre).strip()
    if head and "direct" not in out:
        out["direct"] = head
    if not out and markdown.strip():
        out["direct"] = markdown.strip()
    return out


def _symbol_bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.、)])\s+(.*\S)\s*$", line)
        if m:
            items.append(m.group(1))
        elif items and line.strip():
            items[-1] += " " + line.strip()
    return items


_TABLE_HEADS = {"符号", "变量", "记号", "symbol", "notation", "term", "项"}


def _symbol_table_rows(text: str) -> list[tuple[str, str]]:
    """Markdown 表格形式的符号表（| 符号 | 含义 | 来源 |）：返回 (符号, 含义+来源) 行；表头和分隔行被跳过。"""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.count("|") >= 3):
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s.strip("|"))]
        if len(cells) < 2 or all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
            continue  # 分隔行
        if cells[0].strip("*` ").lower() in _TABLE_HEADS:
            continue  # 表头
        if not cells[0] or not cells[1]:
            continue
        meaning = cells[1]
        for extra in cells[2:]:  # “来源”列：引用要并进去；已经在含义里说过“论文未定义”的就不重复
            if extra and not (UNDEFINED_RE.search(extra) and UNDEFINED_RE.search(meaning)):
                meaning += " " + extra
        rows.append((cells[0], meaning))
    return rows


def parse_symbols(text: str, valid_ords: set[int]) -> list[dict]:
    symbols: list[dict] = []
    text = evidence.normalize_cites(text)
    pairs: list[tuple[str, str]] = _symbol_table_rows(text)
    for b in _symbol_bullets(text):
        m = re.match(r"^(.{1,80}?)\s*(?:[：:]|\s[—–]\s)\s*(.+)$", b)
        if m:
            pairs.append((m.group(1), m.group(2)))
    for raw_sym, raw_meaning in pairs:
        sym = re.sub(r"^[`$*\s]+|[`$*\s]+$", "", raw_sym)
        meaning = raw_meaning.strip()
        ords = evidence.cited_ords(meaning)
        good = [o for o in ords if o in valid_ords]
        if UNDEFINED_RE.search(meaning):
            status = "undefined"
        elif good:
            status = "defined"
        else:
            status = "unsupported"
        symbols.append({
            "symbol": sym, "meaning": strip_cites(meaning) or meaning,
            "ords": good, "status": status,
        })
    return symbols


def parse_equation_answer(markdown: str, pack: ContextPack) -> evidence.ParsedAnswer:
    md = evidence.normalize_cites(markdown)
    sec = split_all(md)
    std_md = "".join(f"## {name}\n{sec[key]}\n" for key, name in STD_KEYS.items() if sec.get(key))
    parsed = evidence.parse_answer(std_md or md, pack)
    valid = {p.ord for p in pack.passages if p.doc == 0}

    symbols = parse_symbols(sec.get("symbols", ""), valid)
    relations = sec.get("relations", "").strip() or None
    derivation = sec.get("derivation", "").strip() or None

    # 额外小节里的引用：并入证据、核实有效性
    extra_text = "\n".join(filter(None, [sec.get("symbols"), relations, derivation]))
    extra_cited = evidence.cited_ords(extra_text)
    have = {e["ord"] for e in parsed.evidence}
    for o in extra_cited:
        p = pack.passage(o)
        if p is not None and o not in have:
            parsed.evidence.append({"ord": p.ord, "page": p.page, "section_title": p.section_title,
                                    "quote": evidence.best_excerpt(p.text), "bbox": p.bbox, "kind": "paper_text"})
            have.add(o)
    extra_invalid = [o for o in extra_cited if o not in valid and o not in parsed.verification["invalid_citations"]]
    if extra_invalid:
        parsed.verification["invalid_citations"].extend(extra_invalid)
        parsed.verification["notes"].append("符号表/关系/推导里引用了不存在于所给摘录的段落编号，已标记为无法核实。")
        if parsed.verification["status"] in ("verified", "unverified"):
            parsed.verification["status"] = "partial"
    unsupported = [s["symbol"] for s in symbols if s["status"] == "unsupported"]
    if unsupported:
        parsed.verification["notes"].append(
            "这些符号的含义既没有论文依据、也没有标注“论文未定义”，请谨慎对待：" + "、".join(unsupported[:8]))
        if parsed.verification["status"] == "verified":
            parsed.verification["status"] = "partial"
    if not symbols and "symbols" not in sec:
        parsed.verification["notes"].append("模型没有给出符号表。")
    elif not symbols and sec.get("symbols", "").strip():
        # 有内容却一个符号也没解析出来：说明格式认不出，如实告知而不是显示一个空表
        parsed.verification["notes"].append("符号表的格式无法解析，已忽略，请直接看回答正文。")
        if parsed.verification["status"] == "verified":
            parsed.verification["status"] = "partial"
    parsed.answer["symbols"] = symbols
    parsed.answer["relations"] = relations
    parsed.answer["derivation"] = derivation
    return parsed
