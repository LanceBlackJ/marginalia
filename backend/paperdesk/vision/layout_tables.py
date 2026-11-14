"""用 PDF 文字层的词坐标重建表格（确定性，不调用模型）。

输入是区域内的词 [(x0, y0, x1, y1, text)]（PDF point，左上角原点），输出和 `/tables-analysis` 兼容的 TableData：
{title, columns, rows, notes, unverified_cells, misplaced_cells, verified_ratio, warnings, layout:{confidence, reasons, panels}}。

步骤：词按纵向位置聚成行 → 行内相邻的词按间距合并成单元格 → 单元格按横向位置聚成列 → 整张图里并排的几块（子图里的数据表）
按列间的大空隙拆开，各自成表。每张表给一个重建可信度：行列是否整齐、空单元格是否太多、有没有行标签/表头。
可信度低的不当作表格（交给视觉模型，或保持为图内文字），不硬凑。
"""
from __future__ import annotations

import re
from statistics import median

Word = tuple[float, float, float, float, str]

# 数值单元格：数字、带 ± 的“均值 ± 标准差”、以及表里常见的占位记号（OOM = 显存不足）
NUM_RE = re.compile(
    r"^[-+−–]?\d+(?:[.,]\d+)*(?:[%×x]\d*)?(?:\s*(?:±|\+/-)\s*\d+(?:[.,]\d+)*%?)?$|^(?:NA|N/A|OOM|OOT|OOR|OOS|TLE|DNF)$",
    re.IGNORECASE,
)


def _median_height(words: list[Word]) -> float:
    return median(max(0.1, w[3] - w[1]) for w in words) if words else 8.0


def group_rows(words: list[Word], tol: float = 0.55) -> list[list[Word]]:
    """按纵向中心把词聚成行（同一行里中心相差不超过 tol × 行高）。"""
    if not words:
        return []
    h = _median_height(words)
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        yc = (w[1] + w[3]) / 2
        if rows and abs(yc - sum((x[1] + x[3]) / 2 for x in rows[-1]) / len(rows[-1])) <= tol * h:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w[0]) for r in rows]


def merge_cells(row: list[Word], h: float, gap: float = 0.6) -> list[tuple[float, float, str]]:
    """行内相邻的词，间距不超过 gap × 行高就是同一个单元格（如 “Avg score”）。返回 [(x0, x1, text)]。"""
    cells: list[list] = []
    for w in row:
        # “59.3 ± 0.1”被切成几个词：和前面的数合成一个单元格
        if cells and (w[4] in ("±", "+/-") or cells[-1][2].rstrip().endswith(("±", "+/-"))) and w[0] - cells[-1][1] <= 1.5 * h:
            cells[-1][1] = max(cells[-1][1], w[2])
            cells[-1][2] += " " + w[4]
            continue
        # 数字和文字永远分成不同的单元格（“Avg score 46.08” 里的数字是数据，不是行标签的一部分）
        if cells and w[0] - cells[-1][1] <= gap * h and _is_num(w[4]) == _is_num(cells[-1][2].split(" ")[-1]) and not _is_num(w[4]):
            cells[-1][1] = max(cells[-1][1], w[2])
            cells[-1][2] += " " + w[4]
        else:
            cells.append([w[0], w[2], w[4]])
    return [(c[0], c[1], c[2]) for c in cells]


def cluster_1d(values: list[float], tol: float) -> list[list[int]]:
    """一维聚类：排序后相邻差不超过 tol 的归为一簇；返回每簇里的下标。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = []
    for i in order:
        if groups and values[i] - values[groups[-1][-1]] <= tol:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def split_panels(cells_by_row: list[list[tuple[float, float, str]]], h: float, min_gap: float = 2.2) -> list[tuple[float, float]]:
    """找出整块区域里并排的几块：把所有单元格的横向范围投影到 x 轴，空隙宽度 ≥ min_gap × 行高的地方切开。返回 [(x_lo, x_hi)]。"""
    spans = sorted((c[0], c[1]) for row in cells_by_row for c in row)
    if not spans:
        return []
    panels, lo, hi = [], spans[0][0], spans[0][1]
    for a, b in spans[1:]:
        if a - hi >= min_gap * h:
            panels.append((lo, hi))
            lo, hi = a, b
        else:
            hi = max(hi, b)
    panels.append((lo, hi))
    return panels


def _is_num(text: str) -> bool:
    return bool(NUM_RE.match(text.strip()))


def build_grid(cells_by_row: list[list[tuple[float, float, str]]], h: float) -> dict | None:
    """把一块区域里的单元格按列对齐成网格；返回 {rows, n_cols, confidence, reasons}，不成表返回 None。"""
    cells = [(ri, c) for ri, row in enumerate(cells_by_row) for c in row]
    if len(cells_by_row) < 2 or len(cells) < 4:
        return None
    centers = [(c[0] + c[1]) / 2 for _, c in cells]
    groups = cluster_1d(centers, tol=max(1.2 * h, 5.0))
    col_of = {}
    for gi, g in enumerate(groups):
        for i in g:
            col_of[i] = gi
    n_cols = len(groups)
    if n_cols < 2:
        return None
    grid = [["" for _ in range(n_cols)] for _ in cells_by_row]
    clash = 0
    for i, (ri, c) in enumerate(cells):
        gi = col_of[i]
        if grid[ri][gi]:
            clash += 1
            grid[ri][gi] += " " + c[2]
        else:
            grid[ri][gi] = c[2]
    total = len(grid) * n_cols
    filled = sum(1 for r in grid for v in r if v)
    fill = filled / total
    # 数值列的一致性：每一列里数字单元格要么占多数，要么几乎没有（行标签列、表头行除外）
    numeric_cols = 0
    for gi in range(n_cols):
        vals = [r[gi] for r in grid if r[gi]]
        if vals and sum(_is_num(v) for v in vals) / len(vals) >= 0.6:
            numeric_cols += 1
    reasons: list[str] = []
    conf = 1.0
    if fill < 0.7:
        conf -= (0.7 - fill) * 1.2
        reasons.append(f"空单元格较多（填充率 {fill:.0%}）")
    if clash:
        conf -= min(0.4, clash / max(1, len(cells)) * 2)
        reasons.append(f"{clash} 个单元格里挤了多段文字")
    if numeric_cols == 0:
        conf -= 0.3
        reasons.append("没有数值列")
    if n_cols > 24:
        conf -= 0.2
        reasons.append("列数很多，可能把图里的刻度当成了列")
    return {"rows": grid, "n_cols": n_cols, "confidence": round(max(0.0, min(1.0, conf)), 2), "reasons": reasons, "fill": round(fill, 2)}



def _segments(cells: list[tuple[float, float, str]]) -> list[tuple[str, list[tuple[float, float, str]]]]:
    """一行里的单元格按“行标签”切段：非数字单元格开启新一段，其后的数字属于这一段；开头的数字（刻度）丢掉。"""
    segs: list[tuple[str, list]] = []
    for c in cells:
        if _is_num(c[2]):
            if segs:
                segs[-1][1].append(c)
        elif segs and not segs[-1][1]:
            segs[-1] = (segs[-1][0] + " " + c[2], segs[-1][1])  # 连续的文字单元格并成一个行标签
        else:
            segs.append((c[2], []))
    return segs


def _span_names(ws: list[Word], keep: list[float], spacing: float, h: float) -> list[str] | None:
    """列名比列少：每个列名横跨相邻的几列（如 “SENATE” 管着它下面的两列）。
    列名的横向范围（两边各放宽半个列距）盖住哪几列，这几列就都用这个名字；盖不住 90% 的列、或有列名没对到列，返回 None。"""
    labels = merge_cells(sorted(ws, key=lambda w: w[0]), h)
    if len(labels) < 2:
        return None
    got = [""] * len(keep)
    used: set[int] = set()
    for j, kc in enumerate(keep):
        best, dist = None, None
        for i, (x0, x1, _t) in enumerate(labels):
            if x0 - 0.5 * spacing <= kc <= x1 + 0.5 * spacing:
                d = abs(kc - (x0 + x1) / 2)
                if dist is None or d < dist:
                    best, dist = i, d
        if best is not None:
            got[j] = labels[best][2]
            used.add(best)
    if len(used) < len(labels) or sum(1 for v in got if v) < 0.9 * len(keep):
        return None
    return got


def reconstruct_panels(words: list[Word], *, min_rows: int = 3, min_conf: float = 0.55) -> list[dict]:
    """图里并排的几块“行标签 + 数值”的数据网格（如每个子图下方的 Avg score 表）重建成表。

    数据行 = 有行标签、且标签后至少 3 个数字的行；同一行里行标签重复出现几次，就是并排的几块。
    每块的列由数字单元格的中心位置聚类得到，稀疏的列（色标刻度等噪声）丢掉；列名取数据行上下紧邻的文字行，
    把其中的词按横向位置分到最近的列；没有就留空。返回 [{panel, title, columns, rows, confidence, reasons, n_rows, n_cols}]。
    """
    if len(words) < 12:
        return []
    # 竖排的分组名（左侧旋转的 “Supervised / Unsupervised”）不是行标签：先拿出来，不让它粘进第一格。
    # 它只写在组的正中间、文字本身盖不住组里的所有行，没法可靠地逐行标注，所以只在重建提示里说一声
    h = _median_height(words)
    vert = [w for w in words if len(w[4]) >= 3 and (w[3] - w[1]) > 2.5 * (w[2] - w[0]) and (w[3] - w[1]) > 2.5 * h]
    if vert:
        words = [w for w in words if w not in vert]
        h = _median_height(words)
    row_words = group_rows(words)
    rows_cells = [merge_cells(r, h) for r in row_words]
    data: list[tuple[int, list]] = []  # (行下标, 切段结果)
    for ri, cells in enumerate(rows_cells):
        segs = [sg for sg in _segments(cells) if len(sg[1]) >= 3]
        if segs:
            data.append((ri, segs))
    if len(data) < min_rows:
        return []
    n_panels = max(len(sg) for _, sg in data)
    # 多数行的段数就是并排块数；段数明显不同的行（噪声）丢掉
    counts = [len(sg) for _, sg in data]
    n_panels = max(set(counts), key=counts.count)
    data = [(ri, sg) for ri, sg in data if len(sg) == n_panels]
    if len(data) < min_rows:
        return []
    out: list[dict] = []
    for k in range(n_panels):
        nums = [(ri, c) for ri, sg in data for c in sg[k][1]]
        centers = [(c[0] + c[1]) / 2 for _, c in nums]
        groups = cluster_1d(centers, tol=max(1.4 * h, 6.0))
        # 稀疏列（刻度）丢掉：出现的数据行数不足 40%
        keep = []
        for g in groups:
            rows_in = {nums[i][0] for i in g}
            if len(rows_in) >= max(2, 0.5 * len(data)):
                keep.append(sum(centers[i] for i in g) / len(g))
        if len(keep) < 2:
            continue
        keep.sort()
        spacing = median([b - a for a, b in zip(keep, keep[1:])]) if len(keep) > 1 else 3 * h
        grid: list[list[str]] = []
        dropped = 0
        for ri, sg in data:
            label, cells = sg[k]
            row = [label] + [""] * len(keep)
            for c in cells:
                cc = (c[0] + c[1]) / 2
                j = min(range(len(keep)), key=lambda t: abs(keep[t] - cc))
                if abs(keep[j] - cc) <= 0.6 * spacing and not row[1 + j]:
                    row[1 + j] = c[2]
                else:
                    dropped += 1
            grid.append(row)
        # 列名：数据行紧邻的上方或下方文字行
        first_r, last_r = data[0][0], data[-1][0]
        lo_x, hi_x = keep[0] - 0.7 * spacing, keep[-1] + 0.7 * spacing
        names = [""] * len(keep)
        data_idx = {ri for ri, _ in data}
        inner = [c for c in range(first_r, last_r + 1) if c not in data_idx]  # 列名常夹在数据行中间（色标图：行在上、列名在下、均值行在最后）
        outer = list(range(first_r - 1, max(-1, first_r - 4), -1)) + list(range(last_r + 1, min(len(rows_cells), last_r + 4)))
        for cand in inner + outer:
            if cand in data_idx:
                continue
            if cand in outer and re.search(r"[–—]|^\s*[a-z]\s", " ".join(w[4] for w in row_words[cand])):
                continue  # 带破折号或以子图字母开头的是子图标题，不是列名
            ws = [w for w in row_words[cand] if lo_x <= (w[0] + w[2]) / 2 <= hi_x and not _is_num(w[4])]
            if len(ws) < 2:
                continue
            got = [""] * len(keep)
            for w in ws:
                wc = (w[0] + w[2]) / 2
                j = min(range(len(keep)), key=lambda t: abs(keep[t] - wc))
                if abs(keep[j] - wc) <= 0.8 * spacing:
                    got[j] = (got[j] + " " + w[4]).strip()
            if sum(1 for v in got if v) < 0.6 * len(keep):
                got = _span_names(ws, keep, spacing, h) or got  # 一个列名跨好几列（合并单元格表头）
            if sum(1 for v in got if v) >= 0.6 * len(keep):
                names = got
                break
        fill = sum(1 for r in grid for v in r[1:] if v) / (len(grid) * len(keep))
        reasons: list[str] = []
        conf = 1.0
        if vert and k == 0:
            reasons.append("左侧有竖排的分组名（" + "、".join(v[4] for v in sorted(vert, key=lambda v: v[1])) + "），没有逐行标出，请对照原表")
        if fill < 0.85:
            conf -= (0.85 - fill) * 1.5
            reasons.append(f"空单元格较多（填充率 {fill:.0%}）")
        if not any(names):
            conf -= 0.15
            reasons.append("没找到列名")
        if dropped > 0.25 * len(nums):
            conf -= 0.2
            reasons.append("有较多数字没能对到列上")
        conf = round(max(0.0, min(1.0, conf)), 2)
        if conf < min_conf:
            continue
        # 子表标题：数据行上方一行里、水平位置落在这一块范围内的文字
        title = ""
        for cand in range(first_r - 1, max(-1, first_r - 5), -1):
            ts = [c for c in rows_cells[cand] if not _is_num(c[2]) and lo_x - 40 <= (c[0] + c[1]) / 2 <= hi_x + 40]
            if ts and not any(_is_num(c[2]) for c in rows_cells[cand] if lo_x <= (c[0] + c[1]) / 2 <= hi_x):
                title = re.sub(r"^(?:[a-z]\s+)|(?:\s+[a-z])$", "", " ".join(t[2] for t in ts)).strip()
                break
        out.append({"panel": k, "title": title, "columns": [""] + names, "rows": grid, "confidence": conf, "reasons": reasons,
                    "n_rows": len(grid), "n_cols": len(keep) + 1})
    return out


def _split_cells(row: list[Word], h: float, gap: float = 1.3) -> list[list]:
    """行内的词按横向间距切成单元格：间距超过 gap × 行高才算列与列之间的空隙。返回 [[x0, x1, text]]。"""
    cells: list[list] = []
    for w in row:
        if cells and w[0] - cells[-1][1] <= gap * h:
            cells[-1][1] = max(cells[-1][1], w[2])
            cells[-1][2] += " " + w[4]
        else:
            cells.append([w[0], w[2], w[4]])
    return cells


def reconstruct_text_table(words: list[Word], *, min_rows: int = 3, anchors: list[float] | None = None) -> dict | None:
    """文字型表格（没有竖线，靠左对齐的列；单元格文字会折成几行，列与列之间可能挨得很近）重建成表。

    列：表头那一行里每个词的左边界都是候选，只留下“正文里有不少行也在这个位置起头”的（真正的列左边界会反复出现，
    普通词不会）。每个词归到“左边界不超过它的最近一列”，所以挨得很近的两列（Train / Feedback）也能分开。
    行：行距明显比行内折行的行距大，就是新的一行；行距相同时，一行里所有有字的列在上一行的同一列也有字，才算折行
    （“Software Develop-”下面的“ment”），否则是新行（首列为空的合并单元格那种表）。
    首列为空的行是合并单元格，向下填充并在提示里说明。词的左边界和列对不齐、空格子太多就不当作表（返回 None）。
    """
    if len(words) < (6 if anchors else 12):
        return None
    h = _median_height(words)
    lines = [ln for ln in group_rows(words) if not re.match(r"(?i)^(table|tab\.)\s*[A-Z]?\d", " ".join(w[4] for w in ln))]
    if anchors is None:
        # 表头之前的行（图注的第一行、折行的图注）只有一个单元格：跳过，从第一行有多个单元格的位置当表头
        while len(lines) > min_rows + 1 and len(_split_cells(lines[0], h)) < 2:
            lines = lines[1:]
        if len(lines) < min_rows + 1:
            return None
        # 表头可能在同一个单元格里折成多行（如 Food- / Bay- / Dry (128)）。
        # 找到第一条含有多个数值的行作为表体起点，前面的整段都保留为多行表头。
        data_start = next((i for i, ln in enumerate(lines[1:], 1) if sum(_is_num(w[4]) for w in ln) >= 2), 1)
        header_lines = lines[:data_start]
        body_lines = lines[data_start:]
        cand = sorted({round(w[0], 1) for w in header_lines[0]})
        anchors = []
        for x in cand:
            hits = sum(1 for ln in body_lines if any(abs(w[0] - x) <= 1.5 for w in ln))
            # 第一列可能是合并单元格（只在少数行里出现）：表头最左边的词始终当作第一列
            if (not anchors and x == cand[0]) or (anchors and hits >= max(2, 0.25 * len(body_lines)) and x - anchors[-1] > 2 * h):
                anchors.append(x)
        if len(anchors) < 2:
            # 表头居中、和表体左边界对不上：改从表体里“反复出现的行内起点”找列
            starts: list[float] = []
            for ln in body_lines:
                prev_x1 = None
                for w in sorted(ln, key=lambda v: v[0]):
                    if prev_x1 is None or w[0] - prev_x1 > 3 * h:
                        starts.append(round(w[0], 1))
                    prev_x1 = w[2]
            anchors = []
            for x in sorted(set(starts)):
                hits = sum(1 for s in starts if abs(s - x) <= 1.5)
                if (not anchors and hits >= 1) or (anchors and hits >= max(3, 0.25 * len(body_lines)) and x - anchors[-1] > 2 * h):
                    anchors.append(x)
            if len(anchors) < 2:
                return None
        header_line: list | None = header_lines
    else:
        # 续页：没有表头，列沿用第一页的；所有行都是表体
        body_lines = lines
        if len(body_lines) < 1:
            return None
        header_line = None
    n = len(anchors)
    ys = [min(w[1] for w in ln) for ln in body_lines]
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0.5 * h]
    pitch = min(gaps) if gaps else 1.2 * h
    uniform = all(g <= 1.15 * pitch for g in gaps)

    def col_of(x0: float) -> int:
        return max((i for i in range(n) if anchors[i] <= x0 + 0.6 * h), default=0)

    rows: list[list[str]] = []
    last_line: list[int] = []  # 当前行每一列的文字最后出现在第几行
    aligned = total = 0
    for li, ln in enumerate(body_lines):
        by_col: dict[int, list[str]] = {}
        for w in ln:
            c = col_of(w[0])
            by_col.setdefault(c, []).append(w[4])
        # 对齐检查按“列内第一个词”算：折行出来的词不一定从列的左边界开始
        for c in by_col:
            first = min((w for w in ln if col_of(w[0]) == c), key=lambda w: w[0])
            total += 1
            aligned += abs(first[0] - anchors[c]) <= 1.6 * h
        if uniform:  # 行与行之间没有额外间距，分不出行距：只有最后一列有字的行才是折行
            cont = bool(rows) and min(by_col) == n - 1 and len(by_col) == 1
        else:
            cont = bool(rows) and li > 0 and ys[li] - ys[li - 1] <= 1.15 * pitch and all(last_line[c] == li - 1 for c in by_col)
        if cont:
            for c, ws in by_col.items():
                # 保留单元格内部的视觉换行，前端会按原来的多行排版；复制 TSV/Markdown 时再统一压成空格。
                rows[-1][c] = (rows[-1][c] + "\n" + " ".join(ws)).strip()
                last_line[c] = li
        else:
            rows.append([" ".join(by_col.get(i, [])) for i in range(n)])
            last_line = [li if i in by_col else -9 for i in range(n)]
    # 行末连字符的折行（“Develop-” + “ment”）接回去：只接小写字母开头的下一段，“Actor- Reporter”这类专名不动
    rows = [[re.sub(r"(\w)- ([a-z])", r"\1\2", c) for c in r] for r in rows]
    if len(rows) < (1 if header_line is None else min_rows) or total == 0 or aligned / total < 0.8:
        return None
    reasons: list[str] = []
    if rows[0][0] and any(r[0] == "" for r in rows):
        last = ""
        for r in rows:
            if r[0]:
                last = r[0]
            else:
                r[0] = last
        reasons.append("第一列是合并单元格，已向下填充")
    if header_line is not None and sum(1 for r in rows for c in r if not c) / max(1, len(rows) * n) > 0.35:
        return None
    header = ["\n".join(" ".join(w[4] for w in ln if col_of(w[0]) == i).strip() for ln in header_line if any(col_of(w[0]) == i for w in ln)).strip() for i in range(n)] if header_line is not None else []
    return {"columns": header, "rows": rows, "n_rows": len(rows), "n_cols": n, "confidence": 0.8, "reasons": reasons, "anchors": list(anchors)}
