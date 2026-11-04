"""段落学习分析：用户选中论文里的某一段，告诉他这一段在整篇论证里起什么作用、该带走什么、怎么承上启下、读的时候留意什么。

目标是帮他读懂，不是给段落贴标签：
- 只分析被选中的这一段（按需，不对整篇批量调用），带上摘要、章节结构、前后文，让“作用”说得具体；
- 关键句必须是这一段里的原话（后端核对，对不上就不给）；“对照段落”只能是给过模型的真实段落编号；
- 全部是 AI 的判断，界面上标明不是论文原文；结果按段落缓存，重复点开不再调用模型。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from .. import reader
from ..context import AppContext
from ..db import register_schema
from ..llmjson import ask_json
from ..util import ApiError, utcnow
from .providers import LLMError

register_schema("passage_insight", """
CREATE TABLE IF NOT EXISTS passage_insights (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  result_json TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, ord)
);
""")

ROLES = (
    "研究背景", "提出问题", "现有方法的不足", "研究动机", "核心思想", "贡献陈述", "方法描述", "定义与符号", "公式推导",
    "实验设置", "结果呈现", "结果分析", "对比与消融", "讨论", "局限", "结论", "相关工作", "过渡衔接", "其他",
)

FIELDS = """{"role": "从下面的角色里选一个最贴切的：""" + "、".join(ROLES) + """",
 "does": "这一段在论证里起什么作用：它回答了什么问题、为整篇文章的哪个论点服务（具体到这一篇，不超过 70 字；不要写“这一段介绍了方法”这种放在哪篇都成立的空话）",
 "takeaway": "读完这一段应该带走什么：用通俗的话说清核心意思（不超过 90 字）",
 "key_sentence": "这一段里最关键的一句话，必须逐字照抄原文（不要改写、不要翻译）；这一段没有这样的句子就留空",
 "from": "和上文怎么接（不超过 45 字；这一段是开头或看不出来就留空）",
 "to": "为下文铺垫了什么（不超过 45 字；看不出来就留空）",
 "watch": ["读的时候要留意的点，最多 3 条，每条不超过 55 字：没解释的术语或符号、隐含的假设、论证里跳过去的一步、需要对照图表才能理解的地方、容易误解的地方。没有就给空数组，不要凑数"],
 "see": [{"ord": 12, "why": "想真正读懂这一段，应该对照哪一段、为什么（不超过 40 字）；ord 只能从下面给出的段落编号里选，最多 3 个"}]}
原则：
- 结合摘要和章节结构判断这一段在整篇里的位置；说具体的，不说空话，也不要复述原文。
- 这一段本身没有多少内容（纯过渡、纯引用说明）就如实写得很短，不要硬凑分析。
- 不要编造论文里没有的内容；不确定的地方直接说“从这一段看不出来”。
- 用中文，术语首次出现附英文原词。"""

SYSTEM = """你是帮读者读懂学术论文的学习导师。读者选中了论文里的某一段，想知道：这一段在整篇论证里到底起什么作用、读完该带走什么、读的时候要留意什么。
请只输出 JSON：
""" + FIELDS

NEIGHBOR_CHARS = 700
SELF_CHARS = 3200


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _verbatim(sentence: str, paragraph: str) -> str | None:
    """关键句必须真在这一段里：整句包含，或几乎一致（排版、标点差异）。"""
    s = " ".join((sentence or "").split())
    if len(s) < 8:
        return None
    a, b = _norm(s), _norm(paragraph)
    if not a or not b:
        return None
    if a in b:
        return s
    m = SequenceMatcher(None, b, a, autojunk=False).find_longest_match(0, len(b), 0, len(a))
    return s if m.size >= 0.9 * len(a) else None


def _clip(s: object, n: int) -> str:
    t = " ".join(str(s or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _context(app: AppContext, paper_id: str, ord_: int) -> tuple[dict, str, set[int]]:
    cur = reader.get_passage(app, paper_id, ord_)
    if cur["kind"] not in ("paragraph", "front", "caption"):
        raise ApiError("bad_request", "这一段不是正文段落（可能是公式、参考文献或图内文字），没有“作用”可分析。")
    if len(cur["text"].strip()) < 40:
        raise ApiError("bad_request", "这一段太短了，没什么可分析的。")
    with app.db.session() as conn:
        paper = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (paper_id,)).fetchone()
        rows = conn.execute(
            reader.PASSAGE_SQL + " WHERE p.paper_id = ? AND p.ord BETWEEN ? AND ? AND p.kind IN ('paragraph','caption') ORDER BY p.ord",
            (paper_id, ord_ - 3, ord_ + 2),
        ).fetchall()
        secs = conn.execute("SELECT title, level FROM sections WHERE paper_id = ? ORDER BY ord LIMIT 60", (paper_id,)).fetchall()
    allowed: set[int] = set()
    parts = [f"【论文】{paper['title']}"]
    if paper["abstract"]:
        parts.append(f"【摘要】{_clip(paper['abstract'], 1200)}")
    if secs:
        parts.append("【章节结构】" + " / ".join(("  " * (max(1, s["level"]) - 1)) + s["title"] for s in secs))
    nb = []
    for r in rows:
        d = reader.passage_dict(r)
        if d["ord"] == ord_:
            continue
        allowed.add(d["ord"])
        tag = "上文" if d["ord"] < ord_ else "下文"
        nb.append(f"[P{d['ord']}] （{tag}，{d['section_title'] or '正文'}）{_clip(d['text'], NEIGHBOR_CHARS)}")
    if nb:
        parts.append("【前后相邻的段落】\n" + "\n".join(nb))
    parts.append(f"【选中的这一段】（所在章节：{cur['section_title'] or '正文'}，第 {cur['page']} 页）\n{cur['text'][:SELF_CHARS]}")
    return cur, "\n\n".join(parts), allowed


def _clean(data: object, cur: dict, allowed: set[int]) -> dict:
    d = data if isinstance(data, dict) else {}
    role = str(d.get("role") or "").strip()
    link = d.get("link") if isinstance(d.get("link"), dict) else {}
    watch = [_clip(w, 60) for w in (d.get("watch") or []) if isinstance(w, str) and w.strip()][:3]
    see: list[dict] = []
    for s in d.get("see") or []:
        if isinstance(s, dict) and isinstance(s.get("ord"), int) and s["ord"] in allowed and s["ord"] != cur["ord"]:
            if all(x["ord"] != s["ord"] for x in see):
                see.append({"ord": s["ord"], "why": _clip(s.get("why"), 50)})
    return {
        "role": role if role in ROLES else "其他",
        "does": _clip(d.get("does"), 90),
        "takeaway": _clip(d.get("takeaway"), 120),
        "key_sentence": _verbatim(str(d.get("key_sentence") or ""), cur["text"]),
        "from": _clip(d.get("from") or link.get("from"), 60),
        "to": _clip(d.get("to") or link.get("to"), 60),
        "watch": watch,
        "see": see[:3],
    }


def _passage_info(cur: dict) -> dict:
    return {"ord": cur["ord"], "page": cur["page"], "bbox": cur["bbox"], "section_title": cur.get("section_title"), "text": cur["text"]}


def get_cached(app: AppContext, paper_id: str, ord_: int) -> dict | None:
    with app.db.session() as conn:
        row = conn.execute("SELECT result_json, model FROM passage_insights WHERE paper_id = ? AND ord = ?", (paper_id, ord_)).fetchone()
    if row is None:
        return None
    cur = reader.get_passage(app, paper_id, ord_)
    return {"passage": _passage_info(cur), "insight": json.loads(row["result_json"]), "model": row["model"], "cached": True}


async def analyze(app: AppContext, paper_id: str, ord_: int, force: bool = False) -> dict:
    if not force:
        hit = get_cached(app, paper_id, ord_)
        if hit:
            return hit
    cur, user, allowed = _context(app, paper_id, ord_)
    notes: list[str] = []
    try:
        data = await ask_json(
            app, system=SYSTEM, user=user, task_type="passage_insight", tier="strong", max_tokens=900, paper_id=paper_id,
            includes={"question": False, "selection": True, "notes": False, "supplements": False, "code": False},
            summary=f"发送了选中的一段原文和前后相邻的段落、摘要、章节结构（约 {len(user):,} 字），请 AI 解释这一段的作用", notes=notes,
        )
    except LLMError as err:
        raise ApiError(err.code, err.message, err.status) from err
    result = _clean(data, cur, allowed)
    if not result["does"] and not result["takeaway"]:
        raise ApiError("provider_error", "模型没有给出可用的分析，请重试。")
    model = ""
    with app.db.session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO passage_insights(paper_id, ord, result_json, model, created_at) VALUES (?,?,?,?,?)",
            (paper_id, ord_, json.dumps(result, ensure_ascii=False), model, utcnow()),
        )
    return {"passage": _passage_info(cur), "insight": result, "model": model, "cached": False, "notes": notes}
