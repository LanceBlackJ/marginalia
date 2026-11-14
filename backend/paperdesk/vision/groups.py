"""把解析器切碎的图区域归并成“逻辑图”。

真实论文（如 Nature 系列）的复合图（Fig. 4 有 2×3 个子图）在解析器里是多个独立的图区域，只有靠近图注的那一排带标签，
上面几排没有标签。直接按区域做理解会得到残缺的结果，所以先按“同一图注”与“紧邻图注上方的无标签区域”归并，
理解、截图、热点都用合并后的外框。归并只在阅读/理解层面进行，不修改解析器的数据。
"""
from __future__ import annotations

from ..context import AppContext
from ..parsing.layout import clean_caption_text

MAX_GAP_BELOW = 420.0  # 无标签区域与其下方图注组的最大垂直间距（pt）
MAX_GAP_ABOVE = 60.0   # 图注在上方（常见于表格）时允许的间距


def _union(rows) -> tuple[float, float, float, float]:
    return (min(r["x0"] for r in rows), min(r["y0"] for r in rows), max(r["x1"] for r in rows), max(r["y1"] for r in rows))


def figure_groups(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT f.id, f.page, f.x0, f.y0, f.x1, f.y1, f.kind, f.label, f.caption_ord, c.text AS caption "
            "FROM figures f LEFT JOIN passages c ON c.paper_id = f.paper_id AND c.ord = f.caption_ord "
            "WHERE f.paper_id = ? ORDER BY f.page, f.y0, f.x0", (paper_id,),
        ).fetchall()
    by_page: dict[int, list] = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)

    groups: list[dict] = []
    for page, items in by_page.items():
        capt: dict[tuple, list] = {}
        loose: list = []
        for r in items:
            if r["caption_ord"] is not None:
                capt.setdefault((r["kind"], r["caption_ord"]), []).append(r)
            else:
                loose.append(r)
        members: dict[tuple, list] = {k: list(v) for k, v in capt.items()}
        alone: list = []
        for r in loose:
            best, best_gap = None, None
            for key, ms in members.items():
                if key[0] != r["kind"]:
                    continue
                anchor = capt[key]  # 只用带图注的区域做锚点，避免合并进来的区域改变锚点位置
                top, bottom = min(m["y0"] for m in anchor), max(m["y1"] for m in anchor)
                gap = top - r["y1"]  # 图注组在下方
                if -4.0 <= gap <= MAX_GAP_BELOW:
                    d = max(0.0, gap)
                elif -MAX_GAP_ABOVE <= r["y0"] - bottom <= MAX_GAP_ABOVE:  # 图注组在上方
                    d = abs(r["y0"] - bottom) + 1000.0
                else:
                    continue
                if best_gap is None or d < best_gap:
                    best, best_gap = key, d
            if best is None:
                alone.append(r)
            else:
                members[best].append(r)
        for key, ms in members.items():
            cap_rows = [m for m in ms if m["caption_ord"] is not None]
            primary = min(cap_rows, key=lambda m: m["id"])
            groups.append(_make(page, ms, primary, cap_rows[0]["label"], cap_rows[0]["caption_ord"], cap_rows[0]["caption"]))
        for r in alone:
            groups.append(_make(page, [r], r, r["label"], None, None))
    groups.sort(key=lambda g: (g["page"], g["bbox"][1], g["bbox"][0]))
    _expand_with_text(app, paper_id, groups)
    return groups


TEXT_NEAR_X = 40.0  # 图外框向左右多远的“图内文字”算图的一部分（纵轴刻度、图例）
TEXT_NEAR_ABOVE = 30.0  # 向上多远（子图标题、子图字母 a b c）
TEXT_NEAR_BELOW = 6.0


def _expand_with_text(app: AppContext, paper_id: str, groups: list[dict]) -> None:
    """图的外框只包着矢量图/位图本身，旁边的坐标刻度、子图标题、子图字母在框外，截图会被裁掉。
    把紧挨着外框的“图内文字”并进来（扩两轮：刻度数字是一列一列的）；不会扩进图注，也不会越过图注去拿下面那张图的文字。"""
    with app.db.session() as conn:
        texts = conn.execute(
            "SELECT page, x0, y0, x1, y1 FROM passages WHERE paper_id = ? AND kind = 'figure_text'", (paper_id,)).fetchall()
        caps = {r["ord"]: (r["y0"], r["y1"]) for r in conn.execute(
            "SELECT ord, y0, y1 FROM passages WHERE paper_id = ? AND kind = 'caption'", (paper_id,)).fetchall()}
    by_page: dict[int, list] = {}
    for t in texts:
        by_page.setdefault(t["page"], []).append(t)
    for g in groups:
        if g["kind"] != "figure":
            continue
        x0, y0, x1, y1 = g["bbox"]
        limit = caps[g["caption_ord"]][0] if g["caption_ord"] in caps else None
        for _ in range(2):
            for t in by_page.get(g["page"], []):
                if limit is not None and t["y1"] > limit + 1:
                    continue  # 在图注的位置或下面：不是这张图的
                if t["x1"] >= x0 - TEXT_NEAR_X and t["x0"] <= x1 + TEXT_NEAR_X and t["y1"] >= y0 - TEXT_NEAR_ABOVE and t["y0"] <= y1 + TEXT_NEAR_BELOW:
                    x0, y0, x1, y1 = min(x0, t["x0"]), min(y0, t["y0"]), max(x1, t["x1"]), max(y1, t["y1"])
        g["bbox"] = [x0, y0, x1, y1]


def _make(page: int, ms: list, primary, label: str | None, caption_ord: int | None, caption: str | None) -> dict:
    x0, y0, x1, y1 = _union(ms)
    return {
        "id": primary["id"], "member_ids": sorted(m["id"] for m in ms), "page": page, "bbox": [x0, y0, x1, y1],
        "kind": primary["kind"], "label": label, "caption_ord": caption_ord,
        "caption": clean_caption_text(caption or ""),
    }


def group_for(app: AppContext, paper_id: str, figure_id: int) -> dict | None:
    for g in figure_groups(app, paper_id):
        if figure_id in g["member_ids"]:
            return g
    return None
