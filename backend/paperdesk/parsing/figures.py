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
        same = [r for r in rules if top[0] <= r[0] < limit and min(r[2], top[2]) - max(r[1], top[1]) >= 0.6 * width]
        if len(same) < 2:
            continue
        out.append(Region(min(r[1] for r in same), top[0] - 1, max(r[2] for r in same), same[-1][0] + 1, "vector", table=True))
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
