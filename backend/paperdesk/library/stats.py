"""文献库的数量统计：全库有多少篇、读到哪了、哪年的多、来自哪些期刊、放在哪些文件夹。数字都是从已有记录数出来的。"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from ..context import AppContext

FINISHED = 0.9  # 读到 90% 以上算读完
TOP = 10


def library_stats(app: AppContext) -> dict:
    base = "FROM papers p WHERE COALESCE(p.role, 'paper') = 'paper'"
    with app.db.session() as conn:
        def one(sql: str, *a) -> int:
            return int(conn.execute(sql, a).fetchone()[0] or 0)

        total = one(f"SELECT COUNT(*) {base}")
        by_status = {r["status"]: r["n"] for r in conn.execute(f"SELECT p.status AS status, COUNT(*) AS n {base} GROUP BY p.status")}
        reading = {
            "unread": one(f"SELECT COUNT(*) {base} AND COALESCE(p.read_progress, 0) <= 0"),
            "finished": one(f"SELECT COUNT(*) {base} AND COALESCE(p.read_progress, 0) >= ?", FINISHED),
        }
        reading["reading"] = max(0, total - reading["unread"] - reading["finished"])
        favorites = one(f"SELECT COUNT(*) {base} AND p.favorite = 1")

        years = [{"year": r["y"], "count": r["n"]} for r in conn.execute(
            f"SELECT p.year AS y, COUNT(*) AS n {base} AND p.year IS NOT NULL GROUP BY p.year ORDER BY p.year DESC")]
        no_year = one(f"SELECT COUNT(*) {base} AND p.year IS NULL")
        venues = [{"name": r["v"], "count": r["n"]} for r in conn.execute(
            f"SELECT p.venue AS v, COUNT(*) AS n {base} AND TRIM(COALESCE(p.venue, '')) != '' GROUP BY p.venue ORDER BY n DESC, p.venue LIMIT ?", (TOP,))]
        no_venue = one(f"SELECT COUNT(*) {base} AND TRIM(COALESCE(p.venue, '')) = ''")

        flist = [dict(r) for r in conn.execute("SELECT id, name, parent_id, sort FROM folders")]
        counts = {r["fid"]: r["n"] for r in conn.execute(f"SELECT p.folder_id AS fid, COUNT(*) AS n {base} AND p.folder_id IS NOT NULL GROUP BY p.folder_id")}
        unfiled = one(f"SELECT COUNT(*) {base} AND p.folder_id IS NULL")

        tags = [{"name": r["name"], "count": r["n"]} for r in conn.execute(
            "SELECT t.name AS name, COUNT(*) AS n FROM kb_paper_topics k JOIN kb_topics t ON t.id = k.topic_id "
            "JOIN papers p ON p.id = k.paper_id WHERE k.status = 'confirmed' AND COALESCE(p.role, 'paper') = 'paper' "
            "GROUP BY t.id ORDER BY n DESC, t.name LIMIT ?", (TOP,))]

        today = datetime.now(timezone.utc).date()  # 导入时间存的是 UTC，月份也按 UTC 数
        months = []
        y, m = today.year, today.month
        for _ in range(12):
            months.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        per_month = {r["ym"]: r["n"] for r in conn.execute(f"SELECT substr(p.imported_at, 1, 7) AS ym, COUNT(*) AS n {base} GROUP BY ym")}

        mine = {
            "notes": one("SELECT COUNT(*) FROM notes"),
            "annotations": one("SELECT COUNT(*) FROM annotations"),
            "bookmarks": one("SELECT COUNT(*) FROM bookmarks"),
            "ai_cards": one("SELECT COUNT(*) FROM insight_cards WHERE saved = 1 AND status = 'complete'"),
        }

    by_folder = []
    for f in folders_in_order(flist):
        n = counts.get(f["id"], 0)
        if n:
            by_folder.append({"name": f["path"], "count": n})
    return {
        "total": total,
        "with_pdf": by_status.get("ready", 0),
        "metadata_only": by_status.get("metadata_only", 0),
        "failed": by_status.get("failed", 0),
        "parsing": by_status.get("parsing", 0),
        "favorites": favorites,
        "reading": reading,
        "years": years,
        "no_year": no_year,
        "venues": venues,
        "no_venue": no_venue,
        "folders": by_folder,
        "unfiled": unfiled,
        "tags": tags,
        "imported_by_month": [{"month": mo, "count": per_month.get(mo, 0)} for mo in months],
        "mine": mine,
        "activity": activity_by_day(app),
    }


ACTIVITY_DAYS = 371  # 53 周：和日历方框图的列数对上


def _local_day(ts: str | None) -> date | None:
    """库里的时间戳是 UTC（ISO 格式）；方框图按本机时区的“哪一天”数。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().date()


def activity_by_day(app: AppContext, today: date | None = None) -> dict:
    """最近一年每天做了多少事：导入论文、打开读论文、记笔记、做标注、加书签、保存 AI 问答。
    “打开读论文”每篇只记得最近一次打开的那天，所以更早的阅读天数会偏少——界面上要说明。"""
    today = today or datetime.now().astimezone().date()
    start = today - timedelta(days=ACTIVITY_DAYS - 1)
    kinds = {
        "imports": "SELECT imported_at FROM papers WHERE COALESCE(role, 'paper') = 'paper'",
        "opened": "SELECT last_opened_at FROM papers WHERE COALESCE(role, 'paper') = 'paper' AND last_opened_at IS NOT NULL",
        "notes": "SELECT created_at FROM notes",
        "annotations": "SELECT created_at FROM annotations",
        "bookmarks": "SELECT created_at FROM bookmarks",
        "ai": "SELECT created_at FROM insight_cards WHERE saved = 1 AND status = 'complete'",
    }
    per: dict[str, Counter] = {k: Counter() for k in kinds}
    with app.db.session() as conn:
        for k, sql in kinds.items():
            for (ts,) in conn.execute(sql):
                d = _local_day(ts)
                if d and start <= d <= today:
                    per[k][d] += 1
    days = []
    for i in range(ACTIVITY_DAYS):
        d = start + timedelta(days=i)
        parts = {k: per[k][d] for k in kinds}
        days.append({"date": d.isoformat(), "count": sum(parts.values()), **parts})
    # 连续有记录的天数（今天还没做事不断：从昨天往前数）
    by_date = {x["date"]: x["count"] for x in days}
    streak, d = 0, today if by_date.get(today.isoformat()) else today - timedelta(days=1)
    while by_date.get(d.isoformat()):
        streak += 1
        d -= timedelta(days=1)
    longest = run = 0
    for x in days:
        run = run + 1 if x["count"] else 0
        longest = max(longest, run)
    return {"days": days, "active_days": sum(1 for x in days if x["count"]), "total": sum(x["count"] for x in days),
            "streak": streak, "longest": longest}


def folders_in_order(items: list[dict]) -> list[dict]:
    """文件夹按树的顺序排好，并带上完整路径（“图神经网络 / 注意力”）。"""
    by_parent: dict[str | None, list[dict]] = {}
    ids = {f["id"] for f in items}
    for f in items:
        k = f["parent_id"] if f["parent_id"] in ids else None
        by_parent.setdefault(k, []).append(f)
    out: list[dict] = []

    def walk(parent: str | None, prefix: str) -> None:
        for f in sorted(by_parent.get(parent, []), key=lambda x: (x["sort"], x["name"])):
            path = f"{prefix} / {f['name']}" if prefix else f["name"]
            out.append({"id": f["id"], "path": path})
            walk(f["id"], path)

    walk(None, "")
    return out
