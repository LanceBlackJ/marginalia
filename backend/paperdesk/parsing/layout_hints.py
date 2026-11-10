"""视觉模型给出的整页版面划分（区域框 + 角色 + 阅读顺序），如何落到解析上。

模型只负责“这一页分成哪几块、每块是什么、先读哪块”；文字、字体、坐标仍然来自 PDF 文字层——
区域框是模型估出来的，位置有误差，所以按文字片段的中心点归属、容差内就近归属，归不进任何区域的片段太多就整页不采用，
回到规则解析。模型的划分永远要和文字层对得上，对不上的页宁可不用。

区域坐标统一存成 0–1 的比例（相对整页，左上角原点），解析时再乘以页面的宽高。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import layout
from .datablocks import looks_like_sentence
from .figures import Region

ROLES = ("title", "authors", "affiliation", "abstract", "heading", "paragraph", "caption", "figure", "table", "formula",
         "reference", "footnote", "sidebar", "header", "footer", "other")
DROP_ROLES = ("header", "footer")
FIGURE_ROLES = ("figure", "table")
TOL = 0.012  # 区域框的容差：页宽/页高的 1.2%
NEAR = 0.035  # 片段不在任何框里时，离最近的框不超过页面尺寸的 3.5% 才就近归属
MAX_ORPHANS = 0.08  # 归不进任何区域的片段超过 8%：这一页的划分不可信
MAX_REGIONS = 80


@dataclass
class HintRegion:
    role: str
    x0: float
    y0: float
    x1: float
    y1: float
    order: int

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)


def normalize_regions(raw: object) -> list[dict]:
    """把模型返回的区域清洗成 [{"role", "box": [x0,y0,x1,y1]（0–1）}]，顺序即阅读顺序。坐标是 0–1000 的整数网格。"""
    items = raw.get("regions") if isinstance(raw, dict) else raw
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items[:MAX_REGIONS]:
        if not isinstance(it, dict):
            continue
        role = str(it.get("role") or "").strip().lower()
        box = it.get("box") or it.get("bbox")
        if role not in ROLES or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = (min(1000.0, max(0.0, float(v))) / 1000.0 for v in box)
        except (TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if (x1 - x0) < 0.01 or (y1 - y0) < 0.004:
            continue
        out.append({"role": role, "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]})
    return out


def _dist(cx: float, cy: float, r: HintRegion) -> float:
    dx = max(r.x0 - cx, 0.0, cx - r.x1)
    dy = max(r.y0 - cy, 0.0, cy - r.y1)
    return (dx * dx + dy * dy) ** 0.5


def to_regions(hint: list[dict], w: float, h: float) -> list[HintRegion]:
    return [HintRegion(r["role"], r["box"][0] * w, r["box"][1] * h, r["box"][2] * w, r["box"][3] * h, i) for i, r in enumerate(hint)]


def assign(spans: list, regions: list[HintRegion], w: float, h: float) -> tuple[dict[int, list], list]:
    """每个文字片段归到包含它中心的最小区域；不在任何框里就近归属；仍然不行的是孤儿。
    返回 ({区域序号: [片段]}, [孤儿片段])。header/footer 区域也参与归属（它们的片段稍后整体丢掉）。"""
    tol_x, tol_y, near = TOL * w, TOL * h, NEAR * max(w, h)
    groups: dict[int, list] = {}
    orphans: list = []
    for sp in spans:
        if not sp.text.strip():
            continue
        cx, cy = (sp.x0 + sp.x1) / 2, (sp.y0 + sp.y1) / 2
        hits = [r for r in regions if r.x0 - tol_x <= cx <= r.x1 + tol_x and r.y0 - tol_y <= cy <= r.y1 + tol_y]
        if hits:
            best = min(hits, key=lambda r: (r.area, r.order))
        else:
            best = min(regions, key=lambda r: _dist(cx, cy, r), default=None)
            if best is None or _dist(cx, cy, best) > near:
                orphans.append(sp)
                continue
        groups.setdefault(best.order, []).append(sp)
    return groups, orphans


def _is_prose_box(spans: list) -> bool:
    """模型说是图/表的区域里其实是成段的句子（把正文误标成图）：宽行里至少 4 行是完整的句子。"""
    rows: dict[int, list] = {}
    for sp in spans:
        rows.setdefault(round(sp.baseline / 3.0), []).append(sp)
    sentences = 0
    for r in rows.values():
        text = " ".join(s.text for s in sorted(r, key=lambda s: s.x0))
        if (len(text) >= 70 and len(text.split()) >= 10 and text.rstrip().endswith((".", ",", ";"))) or looks_like_sentence(text):
            sentences += 1
    return sentences >= 4


def apply_to_page(pg, hint: list[dict], body: float, gutter_fn) -> bool:
    """用视觉划分重建这一页的行：返回 True 表示采用（pg.lines / pg.regions / pg.spans 已更新），False 表示这一页不可信、走规则。
    gutter_fn(spans, page_h) 是栏沟检测，用来处理模型把左右两栏框成一块的情况。"""
    if not hint:
        return False
    w, h = pg.native_w, pg.native_h
    regions = to_regions(hint, w, h)
    groups, orphans = assign(pg.spans, regions, w, h)
    total = sum(len(v) for v in groups.values()) + len(orphans)
    if not total or len(orphans) > MAX_ORPHANS * total:
        return False
    by_order = {r.order: r for r in regions}
    # 页眉页脚：整块丢掉
    drop = {o for o, r in by_order.items() if r.role in DROP_ROLES}
    dropped = [sp for o in drop for sp in groups.get(o, [])]
    # 图/表：补进图区域（里面的行之后会被标成图内文字）；框里其实是成段句子的不当图
    new_regions: list[Region] = []
    prose_as_para: set[int] = set()
    for o, r in by_order.items():
        if r.role in FIGURE_ROLES:
            if _is_prose_box(groups.get(o, [])):
                prose_as_para.add(o)
                continue
            if any(_overlap(r, e) >= 0.5 * min(r.area, max(1.0, (e.x1 - e.x0) * (e.y1 - e.y0))) for e in pg.regions):
                continue
            new_regions.append(Region(r.x0, r.y0, r.x1, r.y1, "vector", table=(r.role == "table")))
    pg.regions.extend(new_regions)
    lines: list = []
    seq = 0
    for o in sorted(by_order):
        r = by_order[o]
        sps = groups.get(o, [])
        if o in drop or not sps:
            continue
        role = "paragraph" if o in prose_as_para else r.role
        g = None
        if len(sps) >= 30 and (r.x1 - r.x0) > 0.6 * w and role not in ("formula", "figure", "table"):
            g = gutter_fn(sps, h)  # 模型把左右两栏框成了一块：区域内部仍按栏沟分栏
        ls = layout.build_lines(list(sps), g, body, h)
        layout.normalize_classes(ls)
        for ln in layout.reading_order(ls):
            ln.region, ln.role = seq, role
            lines.append(ln)
        seq += 1
    # 孤儿（模型漏框的少量文字）：按规则成行，放在最后；它们不属于任何区域
    if orphans:
        for ln in layout.build_lines(list(orphans), None, body, h):
            ln.region, ln.role = seq, ""
            lines.append(ln)
    drop_ids = {id(sp) for sp in dropped}
    pg.spans = [sp for sp in pg.spans if id(sp) not in drop_ids]
    pg.lines = lines
    pg.hinted = True
    return True


def _overlap(r: HintRegion, e: Region) -> float:
    ix0, iy0, ix1, iy1 = max(r.x0, e.x0), max(r.y0, e.y0), min(r.x1, e.x1), min(r.y1, e.y1)
    return max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
