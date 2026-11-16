"""全文翻译中的加粗/斜体边界保护。

PDF 解析保存的是原文里的样式片段，译文改变语言后不能再用原文字符串
定位样式。把样式边界作为不可改写的标记交给翻译模型，前端再把标记
还原成真正的 CSS 样式，避免依赖任何具体论文或具体英文词。
"""
from __future__ import annotations

import re


_TAG = re.compile(r"(?:⟪|《|<)/?PD(?:B|I)\d+(?:⟫|》|>)")
_CANON_TAG = re.compile(r"(?:⟪|《|<)(/?)PD([BI])(\d+)(?:⟫|》|>)")
LINE_TAG = "⟪PDNL⟫"


def canonicalize(text: str) -> str:
    """把旧译文中可见的格式标记变体统一成当前格式。"""
    return _CANON_TAG.sub(lambda m: f"⟪/PD{m.group(2)}{m.group(3)}⟫" if m.group(1) else f"⟪PD{m.group(2)}{m.group(3)}⟫", text or "")


def protect_lines(text: str) -> str:
    """把原文中的硬换行变成不可翻译标记；普通空格不受影响。"""
    return text.replace("\r\n", "\n").replace("\n", LINE_TAG)


def restore_lines(text: str) -> str:
    return text.replace(LINE_TAG, "\n")


def display(text: str) -> str:
    """供划词翻译/其它非正文视图使用：隐藏格式标记，但保留硬换行。"""
    return restore_lines(_TAG.sub("", canonicalize(text)))


def lines_valid(source: str, translated: str) -> bool:
    expected = source.replace("\r\n", "\n").count("\n")
    if expected == 0:
        return True
    actual = translated.count(LINE_TAG) if LINE_TAG in translated else translated.replace("\r\n", "\n").count("\n")
    return actual == expected


def _spans(text: str, values: list[str] | None, prefix: str, start: int = 0) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    cursor = start
    for value in values or []:
        needle = str(value or "")
        if not needle:
            continue
        at = text.find(needle, cursor)
        if at < 0:
            # 旧解析结果有时只在空格数量上不同；只放宽空格，不猜近似文字。
            pat = re.escape(needle).replace(r"\ ", r"\s+")
            m = re.search(pat, text[cursor:])
            if not m:
                continue
            at += cursor + m.start()
            end = cursor + m.end()
        else:
            end = at + len(needle)
        out.append((at, end, prefix))
        cursor = end
    return out


def mask(text: str, bold: list[str] | None = None, italic: list[str] | None = None) -> str:
    """在原文样式片段外加边界标记；找不到的旧样式片段不强行猜位置。"""
    spans = _spans(text, bold, "B") + _spans(text, italic, "I")
    if not spans:
        return text
    # 同一位置的同类/重复样式合并，避免产生交叉标签；不同样式按范围长短嵌套。
    merged: dict[tuple[int, int], set[str]] = {}
    for a, b, kind in spans:
        merged.setdefault((a, b), set()).add(kind)
    events: dict[int, list[tuple[int, int, str, int]]] = {}
    numbers = {key: i + 1 for i, key in enumerate(sorted(merged))}
    for (a, b), kinds in merged.items():
        for kind in sorted(kinds):
            number = numbers[(a, b)]
            events.setdefault(a, []).append((0, -b, kind, number))
            events.setdefault(b, []).append((1, a, kind, number))
    out: list[str] = []
    pos = 0
    for at in sorted(events):
        out.append(text[pos:at])
        closes = [e for e in events[at] if e[0] == 1]
        opens = [e for e in events[at] if e[0] == 0]
        for _typ, _other, kind, number in sorted(closes, key=lambda e: (e[1], e[2]), reverse=True):
            out.append(f"⟪/PD{kind}{number}⟫")
        for _typ, _other, kind, number in sorted(opens, key=lambda e: (e[1], e[2])):
            out.append(f"⟪PD{kind}{number}⟫")
        pos = at
    out.append(text[pos:])
    return "".join(out)


def required(text: str, bold: list[str] | None = None, italic: list[str] | None = None) -> set[str]:
    """返回这段译文必须保留的样式标记。"""
    masked = mask(text, bold, italic)
    return set(_TAG.findall(masked))


def valid(source: str, translated: str, bold: list[str] | None = None, italic: list[str] | None = None) -> bool:
    """检查样式标记是否完整、成对、顺序合法；没有样式要求时直接通过。"""
    translated = canonicalize(translated)
    expected = required(source, bold, italic)
    if not expected:
        # 模型不应凭空留下内部标记；否则这些标记会污染复制、划词翻译和导出。
        return not _TAG.search(translated or "")
    actual = _TAG.findall(translated or "")
    if set(actual) != expected or len(actual) != len(expected):
        return False
    stack: list[str] = []
    for tag in actual:
        close = tag.startswith("⟪/")
        name = tag[2:-1] if close else tag[1:-1]
        if close:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack
