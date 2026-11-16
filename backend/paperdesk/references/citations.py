"""正文里的引用标记识别：把 “[12]”“[12–15]”“systems1,2”“refs 3, 4”“(Smith et al., 2020)”映射到参考文献编号。

只处理明确的引用写法；映射不到参考文献列表的一律不当作引用（“Fig. 3”“Table 1”“S1”“log2”这类都不会被误加链接）。
"""
from __future__ import annotations

import re
from collections import Counter

from .parse import first_author_surname

_NUMS = r"\d{1,3}(?:\s*[,–‐\-]\s*\d{1,3}){0,6}"
_BRACKET = re.compile(rf"(?<![A-Za-z0-9])\[\s*({_NUMS})\s*\]")
_REF_WORD = re.compile(rf"\b[Rr]efs?\.?\s+({_NUMS})")
_SUP = re.compile(r"(?<![^\W\d_])([^\W\d_]{3,})(\d{1,3}(?:[,–‐-]\d{1,3}){0,4})(?=[\s.,;:)\]—–―\-/”’]|$)")
# 一组引用可能包含很多作者-年份项，不能按固定字符数截断；只匹配不含嵌套括号
# 的一对括号，仍然不会跨越整段正文。译文可能把括号改成全角。
_PAREN = re.compile(r"[（(]([^()（）]*)[）)]")
_NAME = r"(?:(?:van|von|de|der|den|di|da|del|della|la|le|ten|ter|bin|al|el|du|dos|das)\s+){0,2}[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]+"
_AY_PART = re.compile(
    rf"({_NAME})(?:\s+et\s+al\.?|\s+(?:and|&|＆)\s+{_NAME})?\s*[,，]?\s*((?:19|20)\d{{2}})([a-z]?)"
)
_AY_NARRATIVE = re.compile(
    rf"\b({_NAME})(?:\s+et\s+al\.?|\s+(?:and|&|＆)\s+{_NAME})?\s+[（(]\s*((?:19|20)\d{{2}})([a-z]?)\s*[）)]"
)

# 数学/记号里常见的“词+数字”，不是引用（与前端 textClean.ts 保持一致）
NOT_CITATION = {
    "resnet", "vgg", "bert", "gpt", "yolo", "densenet", "efficientnet", "mobilenet", "inception", "alexnet", "roberta", "llama",
    "albert", "xlnet", "electra", "convnext", "unet", "python", "cuda", "log", "exp", "top", "sin", "cos", "tan", "max", "min",
    "arg", "dim", "det", "tr", "sup", "inf", "lim", "sinh", "tanh", "mod",
}


def expand_numbers(group: str, valid: set[int]) -> list[int]:
    """“1,2”“12–15”“3, 5-7” → [1, 2]、[12..15]、[3, 5, 6, 7]；只保留参考文献列表里确实存在的编号；区间最长 30。"""
    out: list[int] = []
    for part in re.split(r"\s*,\s*", group.strip()):
        m = re.fullmatch(r"(\d{1,3})\s*[–‐\-]\s*(\d{1,3})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and b - a <= 30:
                out.extend(range(a, b + 1))
            continue
        if part.isdigit():
            out.append(int(part))
    seen: set[int] = set()
    return [n for n in out if n in valid and not (n in seen or seen.add(n))]


def build_vocab(passages: list[dict]) -> Counter:
    """论文里出现过的“独立单词”次数：判断“词+数字”里的词本身是不是一个真实的词。"""
    vocab: Counter = Counter()
    for p in passages:
        if p["kind"] in ("paragraph", "caption"):
            for m in re.finditer(r"(?<![^\W\d_])[^\W\d_]{3,}(?![^\W_])", p["text"]):
                vocab[m.group(0)] += 1
    return vocab


def author_year_index(entries: list[dict]) -> dict[tuple[str, int, str], list[int]]:
    out: dict[tuple[str, int, str], list[int]] = {}
    for e in entries:
        sn = first_author_surname(e)
        if sn and e.get("year"):
            suffixes = re.findall(r"\b((?:19|20)\d{2})([a-z])?\b", e.get("raw", ""), re.IGNORECASE)
            # 参考文献条目里可能还出现“revised in 2024”等其它年份；后缀必须取
            # 与解析出的出版年份相邻的那一个，而不是简单取最后一个年份。
            same_year = [s for y, s in suffixes if int(y) == int(e["year"])]
            suffix = next((s for s in same_year if s), (same_year[0] if same_year else "")).lower()
            out.setdefault((sn, e["year"], suffix), []).append(e["index"])
            first = next((a for a in e.get("authors") or [] if a and a != "et al."), "")
            words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", first) if w[0].isupper()]
            if "," not in first and len(words) >= 2:  # 机构作者：正文常用缩写（World Health Organization → WHO）
                out.setdefault(("".join(w[0] for w in words).lower(), e["year"], suffix), []).append(e["index"])
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^\w\-]", "", s).lower()


def detect_markers(text: str, valid: set[int], ay: dict[tuple[str, int, str], list[int]], vocab: Counter, lenient: bool = False) -> list[dict]:
    """返回段落里的引用标记 [{kind, text, start, end, refs}]，按位置排序、互不重叠。"""
    found: list[dict] = []

    def add(kind: str, start: int, end: int, refs: list[int]) -> None:
        if refs:
            found.append({"kind": kind, "text": text[start:end], "start": start, "end": end, "refs": refs})

    for m in _BRACKET.finditer(text):
        add("bracket", m.start(), m.end(), expand_numbers(m.group(1), valid))
    for m in _REF_WORD.finditer(text):
        add("ref", m.start(1), m.end(1), expand_numbers(m.group(1), valid))
    for m in _SUP.finditer(text):
        word, digits = m.group(1), m.group(2)
        lower, upper = word == word.lower(), word == word.upper() and len(word) >= 2
        if not (lower or upper) or word.lower() in NOT_CITATION or re.match(r"0\d", digits):
            continue
        known = vocab.get(word, 0)
        if lower and not known:  # 单复数形式出现过也算真实单词（suspects8 ← suspect）
            known = vocab.get(word[:-1], 0) if word.endswith("s") else vocab.get(word + "s", 0)
        if known < (2 if upper else 1) and not (lenient and lower and len(word) >= 5):
            continue
        add("sup", m.start(2), m.end(2), expand_numbers(digits, valid))
    if ay:
        for pm in _PAREN.finditer(text):
            inner_start = pm.start(1)
            for part in re.finditer(r"[^;；]+", pm.group(1)):
                am = _AY_PART.search(part.group(0))
                if not am:
                    continue
                refs = ay.get((_norm(am.group(1)), int(am.group(2)), am.group(3).lower()), [])
                if len(refs) == 1:  # 姓氏+年份对应多条时不猜
                    s = inner_start + part.start() + am.start()
                    add("authoryear", s, inner_start + part.start() + am.end(), refs)
        for nm in _AY_NARRATIVE.finditer(text):
            refs = ay.get((_norm(nm.group(1)), int(nm.group(2)), nm.group(3).lower()), [])
            if len(refs) == 1:
                add("authoryear", nm.start(), nm.end(), refs)
    # 一个括号里常常是多篇作者-年份引用：
    # “(Hong et al., 2023; Li et al., 2023a; Wu et al., 2023)”。
    # 不能只把每一项分别标记，否则 PDF 链接和译文定位时很容易只剩下其中一项。
    # 只在括号内已有两条以上“已映射到参考文献”的作者-年份引用时归并，
    # 不会把普通括号、公式括号或无法映射的文本误判为引用。
    for pm in _PAREN.finditer(text):
        members = [
            m for m in found
            if m["kind"] == "authoryear"
            and pm.start() <= m["start"]
            and m["end"] <= pm.end()
        ]
        refs = sorted({r for m in members for r in m["refs"]})
        if len(members) >= 2 and len(refs) >= 2:
            for m in members:
                found.remove(m)
            found.append({
                "kind": "authoryear",
                "text": text[pm.start():pm.end()],
                "start": pm.start(),
                "end": pm.end(),
                "refs": refs,
            })

    found.sort(key=lambda d: (d["start"], -d["end"]))
    out: list[dict] = []
    last_end = -1
    for d in found:  # 去掉重叠（保留靠前、较长的）
        if d["start"] >= last_end:
            out.append(d)
            last_end = d["end"]
    return out


def detect_all(passages: list[dict], entries: list[dict], style: str) -> list[dict]:
    """passages：论文全部段落 [{ord, kind, text}]；entries：解析出的参考文献。返回 [{ord, markers}]。"""
    valid = {e["index"] for e in entries}
    if not entries:
        return []
    vocab = build_vocab(passages)
    ay = author_year_index(entries) if style != "numeric" or any(e.get("year") for e in entries) else {}
    def run(lenient: bool) -> list[dict]:
        out = []
        for p in passages:
            if p["kind"] not in ("paragraph", "caption"):
                continue
            marks = detect_markers(p["text"], valid if style in ("numeric", "unknown") else set(), ay, vocab, lenient)
            if marks:
                out.append({"ord": p["ord"], "markers": marks})
        return out

    out = run(False)
    # 整篇都用上标数字引用（Nature 系，已识别出不少）时，只出现一次的词（suspects8）也认：不然这些引用悬停没有反应
    if sum(1 for o in out for m in o["markers"] if m["kind"] == "sup") >= 8:
        out = run(True)
    return out
