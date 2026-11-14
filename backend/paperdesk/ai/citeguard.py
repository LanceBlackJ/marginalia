"""翻译时保护正文里的文献引用和行内公式：(Talmor et al., 2019)、Geva et al. (2021)、[12]、h = sf/(N−1) 这类标记不翻译、不改写。

只靠提示词让模型“保持原样”不可靠（模型常把 “et al.” 译成“等人”）。做法：发给模型之前把引用换成占位符 ⟦1⟧⟦2⟧…，
模型只翻译占位符以外的文字，拿到译文后（流式输出时边收边还原）再把原文的引用放回去。

行内公式同理：解析时还原出的公式（passages.math_json 里的 src，前端靠它把正文里的文字渲染成 KaTeX）也换成占位符，译完放回原来的 src 文字，
这样译文里的公式和原文一样能被前端识别、渲染；不放占位符的话，模型常把 “h = sf/(N−1)” 改写成 “h = Ns−f 1”，前端就认不出来了。
"""
from __future__ import annotations

import re

_NAME = r"[A-Z][\w'’\-]+"
_YEAR = r"(?:19|20)\d{2}[a-z]?"
_SEG = rf"{_NAME}(?:\s+et\s+al\.?|(?:,|\s+and|\s+&)\s+{_NAME}(?:\s+et\s+al\.?)?)*,?\s*{_YEAR}(?:\s*,\s*{_YEAR})*"
_PAREN = rf"\(\s*(?:(?:e\.g\.|see|cf\.|i\.e\.)[,\s]+)?{_SEG}(?:\s*;\s*(?:(?:see|cf\.)\s+)?{_SEG})*\s*\)"
_NARRATIVE = rf"{_NAME}(?:\s+et\s+al\.?|\s+and\s+{_NAME}|\s+&\s+{_NAME})?\s+\(\s*{_YEAR}(?:\s*[,;]\s*{_YEAR})*\s*\)"
_NUMERIC = r"\[\d+(?:\s*[,–\-]\s*\d+)*\]"
CITE_RE = re.compile("|".join([_PAREN, _NARRATIVE, _NUMERIC]))
_SLOT_RE = re.compile(r"⟦\s*(\d+)\s*⟧")

NOTE = "\n- 文本里形如 ⟦1⟧ 的标记是文献引用或行内数学公式：原样保留在译文里（可按中文语序放到合适的位置），不要翻译、改写、拆开、合并或删除它，也不要在它里面加空格。"


def math_spans(text: str, runs: list[dict] | None) -> list[tuple[int, int]]:
    """把解析时还原的行内公式 [{src, tex}] 按顺序定位到文字里（和前端 textModel.mathRanges 同一套规则）：
    只认有 tex 的；很短的片段（单个变量）要求前后不是字母数字，避免命中词中间；找不到的跳过。"""
    out: list[tuple[int, int]] = []
    cur = 0
    for r in runs or []:
        src, tex = (r.get("src") or ""), (r.get("tex") or "")
        if not src or not tex:
            continue
        start = cur
        while True:
            i = text.find(src, start)
            if i < 0:
                break
            before = text[i - 1] if i > 0 else ""
            after = text[i + len(src)] if i + len(src) < len(text) else ""
            if (src[0].isalnum() and before.isalnum()) or (src[-1].isalnum() and after.isalnum()):
                start = i + 1
                continue
            out.append((i, i + len(src)))
            cur = i + len(src)
            break
    return out


def mask(text: str, math: list[dict] | None = None, found: list[str] | None = None, formula_slots: set[int] | None = None) -> tuple[str, list[str]]:
    """把文本里的引用、行内公式换成 ⟦n⟧，返回 (换好的文本, 按序号排列的原文)。
    found：给了就接着往里加（全文翻译一批里有很多段，占位符编号要全批唯一）；formula_slots：收集哪些序号是公式（译文里丢了要补回）。"""
    found = [] if found is None else found
    spans: list[tuple[int, int, bool]] = [(m.start(), m.end(), False) for m in CITE_RE.finditer(text)]
    for a, b in math_spans(text, math):
        if not any(a < e and b > s for s, e, _f in spans):  # 与引用重叠的公式不单独保护
            spans.append((a, b, True))
    spans.sort()
    out: list[str] = []
    pos = 0
    for a, b, is_formula in spans:
        out.append(text[pos:a])
        found.append(text[a:b])
        if is_formula and formula_slots is not None:
            formula_slots.add(len(found))
        out.append(f"⟦{len(found)}⟧")
        pos = b
    out.append(text[pos:])
    return "".join(out), found


def restore(text: str, found: list[str], used: set[int] | None = None) -> str:
    """把译文里的 ⟦n⟧ 换回原来的引用；模型丢掉或写错的占位符保持不动（不会乱填）。used：收集译文里出现过的序号。
    原文里每个占位符只出现一次：同一个序号在译文里再出现就是模型复读，丢掉重复的，不放回两遍。"""
    if not found:
        return text
    seen: set[int] = set()

    def sub(m: re.Match) -> str:
        i = int(m.group(1))
        if 1 <= i <= len(found):
            if (used is not None and i in used) or i in seen:
                return ""
            seen.add(i)
            if used is not None:
                used.add(i)
            return found[i - 1]
        return m.group(0)

    return _SLOT_RE.sub(sub, text)


_CJK = "[　-〿一-鿿＀-￯]"


def tidy(text: str) -> str:
    """译文里的多余空格：汉字、全角标点之间不该有空格（模型常在占位符两边或断句处多加），连续空格并成一个。"""
    t = re.sub(rf"(?<={_CJK})[ 	]+(?={_CJK})", "", text)
    t = re.sub(rf"(?<={_CJK})[ 	]+(?=[，。；：！？、）】》」”])", "", t)
    t = re.sub(rf"(?<=[（【《「“])[ 	]+(?={_CJK})", "", t)
    return drop_repeated_tail(re.sub(r"[ 	]{2,}", " ", t))


def _core(s: str) -> str:
    """只留汉字和字母：比较两句话是不是同一句时，抹平数字角标、标点、空格的差异。"""
    return re.sub(r"[^一-鿿A-Za-z]+", "", s)


def drop_repeated_tail(text: str) -> str:
    """模型有时把最后一句话原样再说一遍（“……韧性至关重要6。 网络结构和底层动力学如何影响其整体系统韧性6。”）：
    最后一句去掉数字和标点后，整句已经出现在前面的文字里，就丢掉这个重复的尾巴。"""
    parts = re.split(r"(?<=[。！？!?])\s*", text.strip())
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return text
    last = _core(parts[-1])
    before = _core("".join(parts[:-1]))
    if len(last) >= 8 and last in before:
        return "".join(parts[:-1]).strip() if len(parts) > 2 else parts[0]
    return text


class StreamRestore:
    """流式输出时还原占位符：占位符可能被拆在两个数据块里，没写完的那截先扣着，等下一块再一起处理。"""

    def __init__(self, found: list[str]) -> None:
        self.found = found
        self.buf = ""
        self.used: set[int] = set()

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        i = self.buf.rfind("⟦")
        if i >= 0 and "⟧" not in self.buf[i:] and len(self.buf) - i <= 12:
            safe, self.buf = self.buf[:i], self.buf[i:]
        else:
            safe, self.buf = self.buf, ""
        return restore(safe, self.found, self.used)

    def flush(self) -> str:
        out, self.buf = restore(self.buf, self.found, self.used), ""
        return out


def append_missing(text: str, found: list[str], used: set[int], formula_slots: set[int] | None, only: set[int] | None = None) -> str:
    """模型把某些公式的占位符丢了：公式不能悄悄消失，把丢掉的公式原文补在译文末尾（括起来）。引用丢了照旧不管。
    only：只检查这些序号（全文翻译一批里每段只看自己的公式）。"""
    squashed = re.sub(r"\s+", "", text)
    # 模型有时没用占位符、直接把公式原样写进了译文：已经在译文里的不再补，免得重复
    missing = [i for i in sorted(formula_slots or ()) if i not in used and (only is None or i in only) and 1 <= i <= len(found)
               and re.sub(r"\s+", "", found[i - 1]) not in squashed]
    if not missing:
        return text
    return text.rstrip() + " （" + "，".join(found[i - 1] for i in missing) + "）"
