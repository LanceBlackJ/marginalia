"""兴趣推测：从你读论文、做笔记的行为里，估计你对哪些主题感兴趣。只做推测，不会改动你确认过的画像。

核心原则：读了一篇论文，不等于对它感兴趣。打开、翻页、读完都可能只是任务需要（导师布置、查一个背景）。
所以算法把行为分成两类：
  · 主动投入：写了笔记、划了重点、收藏、向 AI 提了问题。这是“我想留下点什么”的行为，是兴趣的主要证据。
  · 单纯阅读：读到的进度。只有读得比较深才算一点，而且最多只贡献一小部分，也不能单独把一个主题判成“兴趣”。
  · 点开就走：打开了、进度很低、什么也没留下，记为轻微的负面信号。

计算分三步：
  1. 每篇论文的投入分 e(p)：主动投入用饱和函数（笔记再多也有上限，避免一篇论文刷出高分），阅读深度只算进度超过 30% 的部分。
  2. 时间衰减：最近一次互动距今每过 90 天减半，但不会低于 25%（长期兴趣不会凭空消失）。
  3. 主题兴趣：把同一主题（标签）下各论文的 e(p) 相加，用饱和函数压到 0 到 1。
     判定为“兴趣”需要至少两篇论文有主动投入；只有一篇有主动投入时是“关注中”；只有阅读时是“只是读过”，默认不展示。
你可以对某个主题点“不是我的兴趣”，之后它不会再出现，也不会参与推荐。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..context import AppContext
from ..util import dumps, loads

# 只算你自己发起的提问；翻译、全文分析、向量化这类自动任务不代表兴趣
USER_ASK_TASKS = ("free_qa", "explain", "term")
HALF_LIFE_DAYS = 90.0
DECAY_FLOOR = 0.25
DEPTH_START = 0.3  # 阅读进度低于这个比例不算数
EXPLICIT_PAPER_MIN = 0.5  # 一篇论文的主动投入分达到它，才算“有主动投入”
MUTED_KEY = "interest_muted"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sat(x: float, scale: float) -> float:
    """饱和函数：从 0 起步，越多越慢，趋近 1。"""
    return 1.0 - math.exp(-max(x, 0.0) / scale)


def decay(last: datetime | None, now: datetime) -> float:
    if last is None:
        return DECAY_FLOOR
    days = max((now - last).total_seconds() / 86400.0, 0.0)
    return max(DECAY_FLOOR, 0.5 ** (days / HALF_LIFE_DAYS))


def score_paper(*, notes: int, highlights: int, favorite: bool, asks: int, progress: float, opened: bool,
                last: datetime | None, now: datetime) -> dict:
    """一篇论文的投入分。返回 {explicit, reading, penalty, score}，score 已含时间衰减。"""
    explicit = 2.0 * _sat(notes, 2.0) + 1.2 * _sat(highlights, 4.0) + (1.0 if favorite else 0.0) + 0.6 * _sat(asks, 3.0)
    reading = 1.0 * min(max((progress - DEPTH_START) / (1.0 - DEPTH_START), 0.0), 1.0)
    penalty = 0.3 if (opened and progress < 0.1 and explicit == 0) else 0.0
    raw = max(explicit + reading - penalty, 0.0)
    return {"explicit": explicit, "reading": reading, "penalty": penalty, "score": raw * decay(last, now)}


def level_of(total: float, explicit_papers: int) -> str:
    if explicit_papers >= 2 and total >= 4.0:
        return "interest"
    if explicit_papers >= 1 and total >= 1.5:
        return "watching"
    return "read_only"


LEVEL_LABEL = {"interest": "兴趣", "watching": "关注中", "read_only": "只是读过"}


def _muted(app: AppContext) -> set[str]:
    with app.db.session() as conn:
        row = conn.execute("SELECT value_json FROM profile_meta WHERE key = ?", (MUTED_KEY,)).fetchone()
    return set(loads(row["value_json"], []) if row else [])


def set_muted(app: AppContext, topic_id: str, muted: bool) -> None:
    ids = _muted(app)
    (ids.add if muted else ids.discard)(topic_id)
    with app.db.session() as conn:
        conn.execute("INSERT INTO profile_meta(key, value_json) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                     (MUTED_KEY, dumps(sorted(ids))))


def paper_scores(app: AppContext, now: datetime | None = None) -> dict[str, dict]:
    """每篇论文的投入分与各项信号 {paper_id: {...}}。补充材料子论文不参与。"""
    now = now or datetime.now(timezone.utc)
    marks = ",".join("?" * len(USER_ASK_TASKS))
    with app.db.session() as conn:
        papers = conn.execute("SELECT id, favorite, read_progress, last_opened_at, imported_at FROM papers").fetchall()
        notes = {r["paper_id"]: (r["n"], r["t"]) for r in conn.execute(
            "SELECT paper_id, COUNT(*) n, MAX(updated_at) t FROM notes WHERE TRIM(text) <> '' OR TRIM(COALESCE(quote,'')) <> '' GROUP BY paper_id")}
        hls = {r["paper_id"]: (r["n"], r["t"]) for r in conn.execute("SELECT paper_id, COUNT(*) n, MAX(created_at) t FROM annotations GROUP BY paper_id")}
        asks = {r["paper_id"]: (r["n"], r["t"]) for r in conn.execute(
            f"SELECT paper_id, COUNT(*) n, MAX(ts) t FROM ai_requests WHERE paper_id IS NOT NULL AND task_type IN ({marks}) GROUP BY paper_id",
            USER_ASK_TASKS)}
    out: dict[str, dict] = {}
    for p in papers:
        pid = p["id"]
        n_note, t_note = notes.get(pid, (0, None))
        n_hl, t_hl = hls.get(pid, (0, None))
        n_ask, t_ask = asks.get(pid, (0, None))
        times = [d for d in (_parse(p["last_opened_at"]), _parse(t_note), _parse(t_hl), _parse(t_ask)) if d]
        s = score_paper(notes=n_note, highlights=n_hl, favorite=bool(p["favorite"]), asks=n_ask, progress=float(p["read_progress"] or 0.0),
                        opened=p["last_opened_at"] is not None, last=max(times) if times else _parse(p["imported_at"]), now=now)
        s.update({"notes": n_note, "highlights": n_hl, "asks": n_ask, "favorite": bool(p["favorite"]), "progress": float(p["read_progress"] or 0.0)})
        out[pid] = s
    return out


def topic_interests(app: AppContext, now: datetime | None = None, include_read_only: bool = False) -> list[dict]:
    """按主题（标签）汇总的兴趣推测，分数从高到低。每项带可读的依据，便于你判断对不对。"""
    scores = paper_scores(app, now)
    muted = _muted(app)
    with app.db.session() as conn:
        topics = conn.execute("SELECT id, name FROM kb_topics").fetchall()
        links = conn.execute("SELECT paper_id, topic_id FROM kb_paper_topics WHERE status = 'confirmed'").fetchall()
    by_topic: dict[str, list[str]] = {}
    for r in links:
        by_topic.setdefault(r["topic_id"], []).append(r["paper_id"])
    items = []
    for t in topics:
        if t["id"] in muted:
            continue
        pids = [p for p in by_topic.get(t["id"], []) if p in scores]
        if not pids:
            continue
        total = sum(scores[p]["score"] for p in pids)
        explicit_n = sum(1 for p in pids if scores[p]["explicit"] >= EXPLICIT_PAPER_MIN)
        level = level_of(total, explicit_n)
        if level == "read_only" and not include_read_only:
            continue
        notes_n = sum(1 for p in pids if scores[p]["notes"] > 0)
        hl_n = sum(1 for p in pids if scores[p]["highlights"] > 0)
        fav_n = sum(1 for p in pids if scores[p]["favorite"])
        deep_n = sum(1 for p in pids if scores[p]["progress"] >= 0.8)
        why = [f"{n} 篇{label}" for n, label in ((notes_n, "写过笔记"), (hl_n, "划过重点"), (fav_n, "收藏"), (deep_n, "读完")) if n]
        items.append({"topic_id": t["id"], "name": t["name"], "level": level, "level_label": LEVEL_LABEL[level],
                      "score": round(_sat(total, 3.0), 3), "papers": len(pids), "engaged_papers": explicit_n,
                      "basis": "，".join(why) if why else "只有阅读记录"})
    items.sort(key=lambda x: (-x["score"], x["name"]))
    return items
