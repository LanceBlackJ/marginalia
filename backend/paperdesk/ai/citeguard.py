"""翻译时保护正文里的文献引用：(Talmor et al., 2019)、Geva et al. (2021)、[12] 这类标记不翻译、不改写。

只靠提示词让模型“保持原样”不可靠（模型常把 “et al.” 译成“等人”）。做法：发给模型之前把引用换成占位符 ⟦1⟧⟦2⟧…，
模型只翻译占位符以外的文字，拿到译文后（流式输出时边收边还原）再把原文的引用放回去。
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

NOTE = "\n- 文本里形如 ⟦1⟧ 的标记是文献引用：原样保留在译文里对应的位置，不要翻译、改写、拆开或删除它。"


def mask(text: str) -> tuple[str, list[str]]:
    """把文本里的引用换成 ⟦n⟧，返回 (换好的文本, 按序号排列的原引用)。"""
    found: list[str] = []

    def sub(m: re.Match) -> str:
        found.append(m.group(0))
        return f"⟦{len(found)}⟧"

    return CITE_RE.sub(sub, text), found


def restore(text: str, found: list[str]) -> str:
    """把译文里的 ⟦n⟧ 换回原来的引用；模型丢掉或写错的占位符保持不动（不会乱填）。"""
    if not found:
        return text

    def sub(m: re.Match) -> str:
        i = int(m.group(1))
        return found[i - 1] if 1 <= i <= len(found) else m.group(0)

    return _SLOT_RE.sub(sub, text)


class StreamRestore:
    """流式输出时还原占位符：占位符可能被拆在两个数据块里，没写完的那截先扣着，等下一块再一起处理。"""

    def __init__(self, found: list[str]) -> None:
        self.found = found
        self.buf = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        i = self.buf.rfind("⟦")
        if i >= 0 and "⟧" not in self.buf[i:] and len(self.buf) - i <= 12:
            safe, self.buf = self.buf[:i], self.buf[i:]
        else:
            safe, self.buf = self.buf, ""
        return restore(safe, self.found)

    def flush(self) -> str:
        out, self.buf = restore(self.buf, self.found), ""
        return out
