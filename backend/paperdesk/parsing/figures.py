"""图表区域检测：位图 + 矢量图（把大量绘图指令按位置聚成区域）。

用途：1) 图内的坐标轴标签/图例等文字不能当作正文段落；2) 为阶段二的“框架图/实验图表理解”
提供真实的图区域坐标（用于截图后交给视觉模型）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

CELL = 4.0  # 栅格边长（pt）


@dataclass
class Region:
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str  # image | vector
    table: bool = False  # 由“图注 + 横线”认出的三线表：里面的文字不论长短都是表格内容，不是正文

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def contains_point(self, x: float, y: float, pad: float = 1.5) -> bool:
        return self.x0 - pad <= x <= self.x1 + pad and self.y0 - pad <= y <= self.y1 + pad


def text_panel_boxes(page: pymupdf.Page, native_w: float, native_h: float) -> list[tuple[float, float, float, float]]:
    """找出页面上用填充矩形承载的文本框，并返回其坐标。

    这类框常见于论文里的 prompt、算法说明、伪代码和警示/定义框。它们不是
    图，也不应该被当成图内文字；但框内的 PDF 文字层换行是内容结构的一部分，
    不能像普通正文行那样全部折叠成空格。只检测有明显面积的填充框，避免把
    普通字符的局部绘图、细线和整页背景当成文本框。
    """
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001  损坏页面没有绘图信息时不影响正文解析
        return []
    page_area = max(1.0, native_w * native_h)
    candidates: list[tuple[float, float, float, float]] = []
    for d in drawings:
        r = d.get("rect")
        if r is None or d.get("fill") is None:
            continue
        w, h = float(r.width), float(r.height)
        if w < 0.32 * native_w or h < 24.0 or w * h < 0.012 * page_area:
            continue
        # 整页底色不是一个文本框；大图的底色也不应负责正文换行。
        if w > 0.98 * native_w and h > 0.90 * native_h:
            continue
        candidates.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))

    # 同一个圆角文本框通常有外层描边/阴影和内层底色两个重叠矩形，合并后
    # 只保留一个区域；较大的区域优先，保证框内所有文字都能被覆盖。
    out: list[tuple[float, float, float, float]] = []
    for box in sorted(candidates, key=lambda b: -((b[2] - b[0]) * (b[3] - b[1]))):
        area = (box[2] - box[0]) * (box[3] - box[1])
        duplicate = False
        for old in out:
            ix = max(0.0, min(box[2], old[2]) - max(box[0], old[0]))
            iy = max(0.0, min(box[3], old[3]) - max(box[1], old[1]))
            if ix * iy >= 0.65 * min(area, (old[2] - old[0]) * (old[3] - old[1])):
                duplicate = True
                break
        if not duplicate:
            out.append(box)
    return out


def detect_regions(page: pymupdf.Page, native_w: float, native_h: float) -> list[Region]:
    page_area = native_w * native_h
    regions: list[Region] = []

    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        w, h = x1 - x0, y1 - y0
        if w * h >= 0.02 * page_area and w >= 40 and h >= 30:
            regions.append(Region(x0, y0, x1, y1, "image"))

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001  个别损坏页面的绘图流无法解析
        drawings = []
    rects = []
    for d in drawings:
        r = d.get("rect")
        if r is None or r.is_empty and r.width == 0 and r.height == 0:
            continue
        # 装饰不是图：只有描边没有填充的整栏宽边框（框起摘要、要点的文字框）、整栏宽的细横条（刊名横幅、分隔条）。
        # 框里的真正图形（曲线、位图）有自己的绘图指令，照常成区。
        wide = r.width >= 0.5 * native_w
        if wide and d.get("fill") is None and r.height >= 20:
            continue
        if wide and r.height <= 20:
            continue
        rects.append((r.x0, r.y0, r.x1, r.y1))
    if len(rects) >= 12:
        regions.extend(_cluster(rects, native_w, native_h))
    try:
        regions.extend(_rule_tables(page, drawings, native_w))
    except Exception:  # noqa: BLE001  取不到文字块或绘图异常时，只是少识别一张表
        pass
    # 有些出版社把表格完全作为文字排进 PDF：没有边框、横线，也没有位图/矢量图对象。
    # 这类表格不能靠“有没有画线”判断；以表题为锚点，用表体的行结构和数字密度
    # 保守地找出表体，避免把表前最后一段普通正文误移出正文流。
    try:
        regions.extend(_text_tables(page, native_w, native_h))
    except Exception:  # noqa: BLE001  文字块结构异常时不影响其它图表检测
        pass

    # 合并相互重叠的区域，并过滤页眉/页脚/分隔线之类的细长装饰
    merged: list[Region] = []
    for r in sorted(regions, key=lambda r: -r.area):
        for m in merged:
            ix0, iy0, ix1, iy1 = max(r.x0, m.x0), max(r.y0, m.y0), min(r.x1, m.x1), min(r.y1, m.y1)
            if ix1 > ix0 and iy1 > iy0 and (ix1 - ix0) * (iy1 - iy0) >= 0.5 * min(r.area, m.area):
                m.x0, m.y0, m.x1, m.y1 = min(m.x0, r.x0), min(m.y0, r.y0), max(m.x1, r.x1), max(m.y1, r.y1)
                m.table = m.table or r.table
                break
        else:
            merged.append(Region(r.x0, r.y0, r.x1, r.y1, r.kind, r.table))
    out = []
    for r in merged:
        w, h = r.x1 - r.x0, r.y1 - r.y0
        band = (r.y1 < 0.12 * native_h or r.y0 > 0.93 * native_h) and w > 0.6 * native_w
        thin = h < 0.04 * native_h or w < 0.04 * native_w
        if not band and not thin and r.area >= 0.008 * page_area and w >= 30 and h >= 30:
            out.append(r)
    return out


TABLE_CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE|表)\s*[A-Z]?\d+")
ANY_CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE|表|Fig\.|Figure|FIGURE|图)\s*[A-Z]?\d+")


def _rule_rows(drawings: list, native_w: float) -> list[tuple[float, float, float]]:
    """横线：高度不超过 1.6pt 的绘图（描边线或极扁的填充条）。同一高度上首尾相连的小段合成一条（三线表的线常被切成每列一段），
    只留宽度够大的，返回 [(y, x0, x1)]。"""
    by_y: dict[int, list[tuple[float, float]]] = {}
    for d in drawings:
        r = d.get("rect")
        if r is None or r.height > 1.6 or r.width < 12:
            continue
        by_y.setdefault(round((r.y0 + r.y1) / 2), []).append((r.x0, r.x1))
    rows: list[tuple[float, float, float]] = []
    for y, segs in by_y.items():
        segs.sort()
        runs: list[list[float]] = []
        for x0, x1 in segs:
            if runs and x0 <= runs[-1][1] + 3:
                runs[-1][1] = max(runs[-1][1], x1)
            else:
                runs.append([x0, x1])
        x0, x1 = max(runs, key=lambda r: r[1] - r[0])
        if x1 - x0 >= 0.3 * native_w:
            rows.append((float(y), x0, x1))
    return sorted(rows)


def _rule_tables(page: pymupdf.Page, drawings: list, native_w: float) -> list[Region]:
    """三线表（只有几条横线、没有竖线和边框）不会被“绘图聚类”认成图形，里面的格子文字就成了正文段落。
    以 Table 图注为锚点：图注正下方（30pt 内）的第一条通栏横线是表头线，到下一个图注之前、与它同宽的最后一条横线是表尾线，
    中间整块算表格区域。没有横线的表（纯空白对齐）不处理。"""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    caps = sorted((b[1], b[3], b[0], b[2], bool(TABLE_CAPTION_RE.match(b[4]))) for b in blocks if ANY_CAPTION_RE.match(b[4]))
    if not any(c[4] for c in caps):
        return []
    page_h = page.rect.height
    # 页眉、页脚的横线（Frontiers 每页顶上一条、底下一条整栏宽的细线）不是表线：否则“最后一条同宽的横线”一路找到页脚，整页正文都成了表格
    rules = [r for r in _rule_rows(drawings, native_w) if 0.07 * page_h < r[0] < 0.93 * page_h]
    out: list[Region] = []
    for i, (cy0, cy1, cx0, cx1, is_table) in enumerate(caps):
        if not is_table:
            continue
        top = next((r for r in rules if cy1 - 3 <= r[0] <= cy1 + 30 and min(r[2], cx1 + 400) - max(r[1], cx0) > 20), None)
        if top is None:
            continue
        limit = caps[i + 1][0] if i + 1 < len(caps) else float("inf")
        width = top[2] - top[1]
        same = [r for r in rules if top[0] <= r[0] < limit and min(r[2], top[2]) - max(r[1], top[1]) >= 0.6 * width
                and 0.7 * width <= (r[2] - r[1]) <= 1.35 * width]  # 表线和表头线宽度相近，整页宽的页脚线不是表尾线
        if len(same) < 2:
            continue
        out.append(Region(min(r[1] for r in same), top[0] - 1, max(r[2] for r in same), same[-1][0] + 1, "vector", table=True))
    return out


def _table_body_like(text: str) -> bool:
    """判断一个文字块是否像“没有绘图边界的表体”。"""
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    if len(lines) < 3:
        return False
    tokens = [t for line in lines for t in line.split()]
    if len(tokens) < 8:
        return False
    numeric = sum(bool(re.fullmatch(r"[()\[\]{}±+\-−≤≥<>]?\d+(?:[.,:]\d+)*%?", t)) for t in tokens)
    # 表头可能全是文字，但至少要有多行、数字列或常见的数值不确定性符号。
    numeric_share = numeric / max(1, len(tokens))
    has_numeric_row = sum(bool(re.search(r"\d|±", line)) for line in lines)
    short_rows = sum(len(line.split()) <= 12 for line in lines)
    table_like_rows = sum(len(line.split()) <= 20 and not line.endswith((".", "。", ";", "；", ":", "：")) for line in lines)
    # 纯文字、无数字的表格只能保守识别：表格行通常都从列值/列名开头，
    # 而普通段落折行经常出现小写续行或句末标点。这个门槛避免把“表题后
    # 的说明段落”误吞进表格区域。
    text_row_table = (
        table_like_rows >= 3 and table_like_rows == len(lines)
        and all(re.match(r"[A-Z\d(\[]", line) for line in lines)
    )
    return (
        (numeric >= 4 and numeric_share >= 0.18 and has_numeric_row >= 2)
        or (numeric >= 6 and short_rows >= 3)
        or text_row_table
    )


def _text_tables(page: pymupdf.Page, native_w: float, native_h: float) -> list[Region]:
    """找“文字层表格”：表题下方/上方紧邻的、由多行单元格文字组成的表体。"""
    blocks = [b for b in page.get_text("blocks") if len(b) >= 7 and b[6] == 0 and b[4].strip()]
    captions = sorted(
        (b for b in blocks if TABLE_CAPTION_RE.match(b[4].strip())),
        key=lambda b: (b[1], b[0]),
    )
    if not captions:
        return []
    out: list[Region] = []
    page_area = max(1.0, native_w * native_h)
    for cap in captions:
        above = [b for b in blocks if b is not cap and b[3] <= cap[1] + 1]
        below = [b for b in blocks if b is not cap and b[1] >= cap[3] - 1]
        if not above and not below:
            continue
        # 表题经常会在表格另一侧再折出一块续行（例如题目第一行在表下、
        # 第二行紧挨着表上）。不能只按“距离表题最近”选边，否则会把题目
        # 续行当成表体。优先选择首个候选块本身就呈现多行/数值列的一侧。
        def ordered(items: list[tuple], is_above: bool) -> list[tuple]:
            result = sorted(items, key=lambda b: b[1])
            return list(reversed(result)) if is_above else result

        above_ordered = ordered(above, True)
        below_ordered = ordered(below, False)
        def seed_info(items: list[tuple]) -> tuple[int, int] | None:
            candidates: list[tuple[int, int]] = []
            for i, b in enumerate(items[:3]):
                overlap = min(b[2], cap[2]) - max(b[0], cap[0])
                if overlap < 0.35 * min(max(1.0, b[2] - b[0]), max(1.0, cap[2] - cap[0])):
                    continue
                if _table_body_like(b[4]):
                    lines = len([x for x in b[4].splitlines() if x.strip()])
                    digits = sum(ch.isdigit() for ch in b[4])
                    candidates.append((3 * lines + digits, i))
            return max(candidates, default=None)

        above_info = seed_info(above_ordered)
        below_info = seed_info(below_ordered)
        if above_info is not None and below_info is None:
            side, is_above, seed = above_ordered, True, above_info[1]
        elif below_info is not None and above_info is None:
            side, is_above, seed = below_ordered, False, below_info[1]
        elif above_info is not None and below_info is not None:
            # 多行表题可能让表体两侧都出现“像表格”的短文字；优先行数/数值
            # 更充分的一侧，而不是盲选离表题最近的一块正文。
            if above_info[0] >= below_info[0]:
                side, is_above, seed = above_ordered, True, above_info[1]
            else:
                side, is_above, seed = below_ordered, False, below_info[1]
        else:
            above_gap = cap[1] - max(b[3] for b in above) if above else float("inf")
            below_gap = min(b[1] for b in below) - cap[3] if below else float("inf")
            is_above = bool(above) and above_gap <= below_gap
            side, seed = (above_ordered, 0) if is_above else (below_ordered, 0)
            if not side:
                continue
        # 丢掉表题续行/其它近邻文字，从真正的表体块开始向外扩展。
        side = side[seed:]
        first = side[0]
        overlap = min(first[2], cap[2]) - max(first[0], cap[0])
        if overlap < 0.35 * min(max(1.0, first[2] - first[0]), max(1.0, cap[2] - cap[0])):
            continue
        chosen = [first]
        for b in side[1:]:
            prev = chosen[-1]
            gap = prev[1] - b[3] if is_above else b[1] - prev[3]
            x_overlap = min(b[2], prev[2]) - max(b[0], prev[0])
            if gap < -1 or gap > 55 or x_overlap < 0.35 * min(max(1.0, b[2] - b[0]), max(1.0, prev[2] - prev[0])):
                break
            # 首块已经是完整的多行表体时，向上/向下的下一块很可能是页眉、
            # 表题续行或表后正文；不要为了扩大区域把它一并吞掉。
            if _table_body_like(first[4]) and not _table_body_like(b[4]):
                break
            chosen.append(b)
        if not _table_body_like("\n".join(b[4] for b in chosen)):
            continue
        x0 = min(float(b[0]) for b in chosen)
        y0 = min(float(b[1]) for b in chosen)
        x1 = max(float(b[2]) for b in chosen)
        y1 = max(float(b[3]) for b in chosen)
        if (x1 - x0) * (y1 - y0) < 0.006 * page_area or y1 - y0 < 30:
            continue
        out.append(Region(x0, y0, x1, y1, "vector", table=True))
    return out


def _cluster(rects: list[tuple[float, float, float, float]], w: float, h: float) -> list[Region]:
    """把绘图矩形栅格化后做连通域，得到矢量图区域。"""
    gx, gy = int(w / CELL) + 3, int(h / CELL) + 3
    grid = [[False] * gx for _ in range(gy)]
    for x0, y0, x1, y1 in rects:
        if x1 - x0 > 0.9 * w and y1 - y0 > 0.9 * h:
            continue  # 整页背景
        cx0, cx1 = max(0, int(x0 / CELL) - 1), min(gx - 1, int(x1 / CELL) + 1)
        cy0, cy1 = max(0, int(y0 / CELL) - 1), min(gy - 1, int(y1 / CELL) + 1)
        for cy in range(cy0, cy1 + 1):
            row = grid[cy]
            for cx in range(cx0, cx1 + 1):
                row[cx] = True
    seen = [[False] * gx for _ in range(gy)]
    out: list[Region] = []
    for sy in range(gy):
        for sx in range(gx):
            if not grid[sy][sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            minx = maxx = sx
            miny = maxy = sy
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                minx, maxx, miny, maxy = min(minx, cx), max(maxx, cx), min(miny, cy), max(maxy, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1), (cx + 1, cy + 1), (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1)):
                    if 0 <= nx < gx and 0 <= ny < gy and grid[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if count >= 30:
                out.append(Region(minx * CELL + CELL, miny * CELL + CELL, maxx * CELL, maxy * CELL, "vector"))
    return out


def regions_from_text_clusters(spans: list, native_w: float, native_h: float) -> list[Region]:
    """扫描页（OCR）没有图片对象可用：整页就是一张图。改用文字本身判断——图里的坐标轴刻度、图例、标注是大量
    密集的“短行”（不超过 4 个词）。把它们聚成连通的区域，当作图区域，里面的文字就按“图内文字”处理，
    不会混进正文、也不会被当成章节标题。偏保守：短行数量和面积都要够大才认。"""
    cell = 36.0
    short = [s for s in spans if len(s.text.split()) <= 4]
    cells: dict[tuple[int, int], list] = {}
    for s in short:
        cx, cy = int(((s.x0 + s.x1) / 2) // cell), int(((s.y0 + s.y1) / 2) // cell)
        cells.setdefault((cx, cy), []).append(s)
    dense = {k for k, v in cells.items() if len(v) >= 3}
    seen: set[tuple[int, int]] = set()
    regions: list[Region] = []
    for start in sorted(dense):
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx in (-2, -1, 0, 1, 2):  # 允许相隔一个空格子（刻度和图例之间常有空隙）
                for dy in (-2, -1, 0, 1, 2):
                    n = (cx + dx, cy + dy)
                    if n in dense and n not in seen:
                        seen.add(n)
                        stack.append(n)
        members = [s for k in comp for s in cells[k]]
        if len(members) < 10:
            continue
        x0, y0 = min(s.x0 for s in members) - 6, min(s.y0 for s in members) - 6
        x1, y1 = max(s.x1 for s in members) + 6, max(s.y1 for s in members) + 6
        reg = Region(max(0.0, x0), max(0.0, y0), min(native_w, x1), min(native_h, y1), "scan")
        if reg.area >= 0.015 * native_w * native_h:
            regions.append(reg)
    return regions
