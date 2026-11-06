"""阅读流重排：图和表不切断正文，并紧跟在首次提到它的段落后面。

PDF 里图表按版面位置夹在正文中间：常常把一个句子从中间切成两半（“…methods that use | 图 1 | pyramids of images…”），
或者离提到它的地方隔了好几页。这里把每张图/表（图注 + 图里的文字 + 表里的数据）作为一组拿出正文流，
放回“第一次提到它的段落”后面（没有段落提到它就放回原来前一段的后面），并保证不会插进被切成两半的句子里。
纯函数、不依赖 PDF：输入段落、图实体、公式实体，原地改顺序并重新编号（ord 即阅读顺序）。
"""
from __future__ import annotations

import re
from typing import Any

SENTENCE_END = re.compile(r"[.!?。！？:：;；][\"”’)\]）]*\s*$")
_ID = r"[A-Z]?\d+(?:\.\d+)?"
_SEP = r"(?:\s*(?:,|，|、|and|&|和|与|及|–|—|-|to)\s*)"
# 正文里的“提到”：Figure 1 / Figs. 2 and 3 / Fig. 4(a) / 图 5 / Table 2 / 表 3
MENTION_RE = re.compile(
    rf"(?<![A-Za-z])(Figures?|Figs?\.?|图|Tables?|表)\s*({_ID}[a-z]?(?:{_SEP}{_ID}[a-z]?)*)", re.IGNORECASE
)
CAPTION_RE = re.compile(rf"^\s*(Figure|Fig\.?|FIGURE|图|Table|TABLE|表)\s*({_ID})", re.IGNORECASE)
ID_RE = re.compile(_ID)


def _kind_of(word: str) -> str:
    return "table" if word.lower().startswith(("table", "表")) else "fig"


def caption_key(text: str) -> tuple[str, str] | None:
    m = CAPTION_RE.match(text or "")
    return (_kind_of(m.group(1)), m.group(2).upper()) if m else None


def mentioned(text: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for m in MENTION_RE.finditer(text or ""):
        kind = _kind_of(m.group(1))
        for i in ID_RE.findall(m.group(2)):
            out.add((kind, i.upper()))
    return out


def _continues(prev: Any, nxt: Any) -> bool:
    """nxt 是 prev 被切断的后半段：prev 末尾没有句末标点，nxt 以小写字母开头；或（专有名词被栏尾切开，“…the Hugging | Face community”）
    nxt 以大写单词开头，但确实换了页/栏、prev 够长。和前端接段落的规则一致（textModel.continuesAcrossPage）。"""
    if prev.kind != "paragraph" or nxt.kind != "paragraph":
        return False
    if SENTENCE_END.search(prev.text.strip()):
        return False
    if re.match(r"\s*[a-zà-ÿ]", nxt.text):
        return True
    if len(prev.text.strip()) < 80 or not re.match(r"\s*[A-ZÀ-Þ][a-zà-ÿ]+[\s,]", nxt.text):
        return False
    return nxt.page > prev.page or (nxt.bbox[1] < prev.bbox[1] - 50 and nxt.bbox[0] > prev.bbox[0] + 80)


def _inside(p: Any, box: tuple[float, float, float, float], pad: float = 4.0) -> bool:
    cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


def reflow(passages: list[Any], figures: list[Any], equations: list[Any]) -> bool:
    """返回是否改动了顺序。passages 原地重排并重新编号；figures 的 caption_ord/inner_ord、equations 的 passage_ord 同步更新。"""
    n = len(passages)
    if n == 0:
        return False
    by_ord = {p.ord: i for i, p in enumerate(passages)}

    # 1. 每张图/表一组：图注 + 落在它版面区里的“图内文字”
    group_of: dict[int, int] = {}  # 段落下标 → 图注下标
    for f in figures:
        if f.caption_ord is None or f.caption_ord not in by_ord:
            continue
        ci = by_ord[f.caption_ord]
        if passages[ci].kind != "caption":
            continue
        group_of[ci] = ci
        for i, p in enumerate(passages):
            if p.kind == "figure_text" and p.page == f.page and i not in group_of and _inside(p, f.bbox):
                group_of[i] = ci
    for i, p in enumerate(passages):  # 没有对应图实体的图注也单独成组
        if p.kind == "caption" and i not in group_of:
            group_of[i] = i

    groups: dict[int, list[int]] = {}
    for i in sorted(group_of):
        groups.setdefault(group_of[i], []).append(i)
    # 图注放在组的最前面，图内文字跟在后面（原有相对顺序不变）
    for ci, members in groups.items():
        groups[ci] = [ci] + [m for m in members if m != ci]

    main = [i for i in range(n) if i not in group_of]
    if not groups:
        return False
    pos_in_main = {idx: k for k, idx in enumerate(main)}

    # 2. 每组的落点：第一次提到它的段落（找不到就是原来紧挨着的前一段）
    first_mention: dict[tuple[str, str], int] = {}  # 提到某图/表的第一个正文段落（在 main 里的位置）
    for k, idx in enumerate(main):
        p = passages[idx]
        if p.kind != "paragraph":
            continue
        for key in mentioned(p.text):
            first_mention.setdefault(key, k)

    anchor_of: dict[int, int] = {}  # 组（图注下标）→ main 里的落点位置
    for ci in groups:
        key = caption_key(passages[ci].text)
        k = first_mention.get(key) if key else None
        if k is None:  # 没有段落提到：留在原来的位置（前一个正文段落之后）
            before = [pos_in_main[i] for i in main if i < ci]
            k = (before[-1]) if before else -1
        # 落点段落如果被切成两半（后面接着它的后半段），放到后半段之后，不能插进句子中间
        while 0 <= k < len(main) - 1 and _continues(passages[main[k]], passages[main[k + 1]]):
            k += 1
        anchor_of[ci] = k

    # 3. 重新排列
    at: dict[int, list[int]] = {}
    for ci in sorted(groups):  # 按图注原顺序：同一落点的多张图保持原来的先后
        at.setdefault(anchor_of[ci], []).append(ci)
    new_order: list[int] = []
    sec_for: dict[int, Any] = {}
    for ci in at.get(-1, []):
        new_order.extend(groups[ci])
    for k, idx in enumerate(main):
        new_order.append(idx)
        for ci in at.get(k, []):
            new_order.extend(groups[ci])
            for m in groups[ci]:
                sec_for[m] = passages[idx].section_ord

    if new_order == list(range(n)):
        return False

    # 4. 重新编号，并同步引用 ord 的实体
    old_to_new = {passages[i].ord: j for j, i in enumerate(new_order)}
    reordered = [passages[i] for i in new_order]
    for m, sec in sec_for.items():  # 被挪走的图/表归到新位置所在的章节
        if sec is not None:
            passages[m].section_ord = sec
    for j, p in enumerate(reordered):
        p.ord = j
    passages[:] = reordered
    for f in figures:
        if f.caption_ord is not None:
            f.caption_ord = old_to_new.get(f.caption_ord, f.caption_ord)
        if getattr(f, "inner_ord", None) is not None:
            f.inner_ord = old_to_new.get(f.inner_ord, f.inner_ord)
    for e in equations:
        e.passage_ord = old_to_new.get(e.passage_ord, e.passage_ord)
    return True
