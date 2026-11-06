"""研究卡片与分析链的持久化。"""
from __future__ import annotations

import hashlib
import re
import sqlite3

from ..context import AppContext
from ..db import register_column, register_schema
from ..util import ApiError, dumps, loads, new_id, utcnow

# 一篇论文可以开多个对话：对话之间互相独立（各自的前文只在自己里面传递），不同论文的对话更不会混
register_schema("conversations", """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_paper ON conversations(paper_id, updated_at);
""")
register_column("conversation", "insight_cards", "conversation_id", "TEXT")
register_column("attachments", "insight_cards", "attachments_json", "TEXT")

DEFAULT_CONV_TITLE = "新对话"


def short_label(text: str, limit: int = 40) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def object_key_for(selection: dict | None, ords: list[int]) -> tuple[str, str, str]:
    """返回 (object_type, object_key, object_label)。"""
    if not selection or not (selection.get("text") or "").strip():
        return "free", "free", "整篇论文"
    label = short_label(selection["text"], 60)
    if ords:
        return "selection", f"passage:{min(ords)}", label
    digest = hashlib.sha1(re.sub(r"\s+", " ", selection["text"]).strip().lower()[:200].encode()).hexdigest()[:10]
    return "selection", f"sel:{digest}", label


def resolve_chain(
    conn: sqlite3.Connection, paper_id: str, chain_id: str | None, object_type: str, object_key: str, object_label: str,
) -> tuple[str, str]:
    now = utcnow()
    if chain_id:
        row = conn.execute("SELECT id, title FROM insight_chains WHERE id = ? AND paper_id = ?", (chain_id, paper_id)).fetchone()
        if row is None:
            raise ApiError("not_found", "分析链不存在", 404)
        conn.execute("UPDATE insight_chains SET updated_at = ? WHERE id = ?", (now, chain_id))
        return row["id"], row["title"]
    row = conn.execute(
        "SELECT id, title FROM insight_chains WHERE paper_id = ? AND object_key = ?", (paper_id, object_key)
    ).fetchone()
    if row:
        conn.execute("UPDATE insight_chains SET updated_at = ? WHERE id = ?", (now, row["id"]))
        return row["id"], row["title"]
    cid = new_id()
    title = "自由提问" if object_key == "free" else short_label(object_label, 36)
    conn.execute(
        "INSERT INTO insight_chains(id, paper_id, title, object_type, object_key, object_label, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (cid, paper_id, title, object_type, object_key, object_label, now, now),
    )
    return cid, title


def create_card(
    conn: sqlite3.Connection, *, card_id: str, paper_id: str, chain_id: str, object_type: str, object_label: str,
    selection: dict | None, question: str, intent: str, task_type: str, model: str, saved: bool,
    conversation_id: str | None = None, attachments: list[dict] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO insight_cards(id, paper_id, chain_id, object_type, object_label, selection_json, question, intent, "
        "task_type, model, status, saved, created_at, conversation_id, attachments_json) VALUES (?,?,?,?,?,?,?,?,?,?,'streaming',?,?,?,?)",
        (card_id, paper_id, chain_id, object_type, object_label, dumps(selection) if selection else None,
         question, intent, task_type, model, int(saved), utcnow(), conversation_id, dumps(attachments) if attachments else None),
    )


# ---------------------------------------------------------------- 修改最后一次提问
def edit_target(conn: sqlite3.Connection, paper_id: str, card_id: str) -> dict:
    """可以被“修改后重新回答”的卡片：必须属于这篇论文、属于某个对话、不在生成中，并且是这个对话里最后一张。
    只允许改最后一次提问——改中间的会让后面那些基于旧回答的追问失效。返回它的完整旧内容（失败时用来还原）。"""
    row = conn.execute("SELECT * FROM insight_cards WHERE id = ? AND paper_id = ?", (card_id, paper_id)).fetchone()
    if row is None:
        raise ApiError("not_found", "要修改的提问不存在", 404)
    if not row["conversation_id"]:
        raise ApiError("bad_request", "这条提问不属于任何对话，不能修改")
    if row["status"] == "streaming":
        raise ApiError("conflict", "这条提问还在生成回答，请等它结束或先停止", 409)
    last = conn.execute(
        "SELECT id FROM insight_cards WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (row["conversation_id"],)
    ).fetchone()
    if last is None or last["id"] != card_id:
        raise ApiError("bad_request", "只能修改对话里最后一次提问")
    return dict(row)


def reset_card_for_edit(
    conn: sqlite3.Connection, card_id: str, *, question: str, intent: str, selection: dict | None, task_type: str, model: str,
    attachments: list[dict] | None,
) -> None:
    """同一张卡片原地改写：换上新的问题，清掉旧回答，重新进入生成中。id 不变，所以知识库来源、思维导图关联等指向它的引用不会断。"""
    conn.execute(
        "UPDATE insight_cards SET question=?, intent=?, selection_json=?, task_type=?, model=?, status='streaming', answer_json=NULL, "
        "raw_markdown='', evidence_json='[]', verification_json=NULL, usage_json=NULL, attachments_json=? WHERE id=?",
        (question, intent, dumps(selection) if selection else None, task_type, model, dumps(attachments) if attachments else None, card_id),
    )


def restore_card(conn: sqlite3.Connection, snap: dict) -> None:
    """重新回答没有成功（出错、取消）：把卡片还原成修改前的样子，原来的问题和回答一个字都不丢。"""
    cols = ("question", "intent", "selection_json", "task_type", "model", "status", "answer_json", "raw_markdown", "evidence_json",
            "verification_json", "usage_json", "attachments_json")
    conn.execute(f"UPDATE insight_cards SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*[snap[c] for c in cols], snap["id"]))


# ---------------------------------------------------------------- 对话
def _conv_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "paper_id": r["paper_id"], "title": r["title"], "card_count": r["n"], "updated_at": r["updated_at"],
            "created_at": r["created_at"]}


def _adopt_orphans(conn: sqlite3.Connection, paper_id: str) -> None:
    """有这个功能之前留下的卡片（没有归属的对话）：归到一个“对话 1”里，不丢。"""
    orphans = conn.execute(
        "SELECT COUNT(*) FROM insight_cards WHERE paper_id = ? AND conversation_id IS NULL", (paper_id,)
    ).fetchone()[0]
    if not orphans:
        return
    first = conn.execute("SELECT MIN(created_at) FROM insight_cards WHERE paper_id = ?", (paper_id,)).fetchone()[0]
    cid = new_id()
    now = utcnow()
    conn.execute(
        "INSERT INTO conversations(id, paper_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
        (cid, paper_id, "对话 1", first or now, now),
    )
    conn.execute("UPDATE insight_cards SET conversation_id = ? WHERE paper_id = ? AND conversation_id IS NULL", (cid, paper_id))


def list_conversations(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        _adopt_orphans(conn, paper_id)
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM insight_cards k WHERE k.conversation_id = c.id AND k.status != 'streaming') AS n "
            "FROM conversations c WHERE c.paper_id = ? ORDER BY c.updated_at DESC, c.rowid DESC", (paper_id,),
        ).fetchall()
    return [_conv_dict(r) for r in rows]


def create_conversation(app: AppContext, paper_id: str, title: str | None = None) -> dict:
    with app.db.session() as conn:
        _adopt_orphans(conn, paper_id)
        cid, now = new_id(), utcnow()
        conn.execute(
            "INSERT INTO conversations(id, paper_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (cid, paper_id, (title or "").strip()[:60] or DEFAULT_CONV_TITLE, now, now),
        )
        r = conn.execute(
            "SELECT c.*, 0 AS n FROM conversations c WHERE c.id = ?", (cid,)
        ).fetchone()
    return _conv_dict(r)


def resolve_conversation(conn: sqlite3.Connection, paper_id: str, conversation_id: str | None) -> str:
    """提问归到哪个对话：指定了就用它（必须是这篇论文的）；没指定用最近的一个，一个都没有就新建。"""
    _adopt_orphans(conn, paper_id)
    if conversation_id:
        row = conn.execute("SELECT id FROM conversations WHERE id = ? AND paper_id = ?", (conversation_id, paper_id)).fetchone()
        if row is None:
            raise ApiError("not_found", "对话不存在", 404)
        return row["id"]
    row = conn.execute("SELECT id FROM conversations WHERE paper_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1", (paper_id,)).fetchone()
    if row:
        return row["id"]
    cid, now = new_id(), utcnow()
    conn.execute("INSERT INTO conversations(id, paper_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
                 (cid, paper_id, DEFAULT_CONV_TITLE, now, now))
    return cid


def touch_conversation(conn: sqlite3.Connection, conversation_id: str, first_question: str | None = None) -> None:
    """有了新提问：更新时间；对话还叫默认名时，用第一个问题给它起名。"""
    row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        return
    title = row["title"]
    if first_question and first_question.strip() and (title == DEFAULT_CONV_TITLE):
        title = short_label(first_question, 24)
    conn.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, utcnow(), conversation_id))


def patch_conversation(app: AppContext, conversation_id: str, title: str) -> dict:
    title = title.strip()
    if not title:
        raise ApiError("bad_request", "名字不能为空")
    with app.db.session() as conn:
        cur = conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title[:60], conversation_id))
        if cur.rowcount == 0:
            raise ApiError("not_found", "对话不存在", 404)
        r = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM insight_cards k WHERE k.conversation_id = c.id AND k.status != 'streaming') AS n "
            "FROM conversations c WHERE c.id = ?", (conversation_id,)
        ).fetchone()
    return _conv_dict(r)


def delete_conversation(app: AppContext, conversation_id: str) -> None:
    """删除对话：里面没保存的卡片一起删；保存成研究卡片的留下（它们属于分析链，不该因为关了一次对话就没了）。"""
    with app.db.session() as conn:
        row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            raise ApiError("not_found", "对话不存在", 404)
        conn.execute("DELETE FROM insight_cards WHERE conversation_id = ? AND saved = 0", (conversation_id,))
        conn.execute("UPDATE insight_cards SET conversation_id = NULL WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.execute("DELETE FROM insight_chains WHERE id NOT IN (SELECT DISTINCT chain_id FROM insight_cards)")


def finalize_card(
    conn: sqlite3.Connection, card_id: str, *, status: str, model: str, raw: str, answer: dict | None,
    evidence: list, verification: dict | None, usage: dict, task_type: str,
) -> None:
    conn.execute(
        "UPDATE insight_cards SET status=?, model=?, raw_markdown=?, answer_json=?, evidence_json=?, "
        "verification_json=?, usage_json=?, task_type=? WHERE id=?",
        (status, model, raw, dumps(answer) if answer else None, dumps(evidence),
         dumps(verification) if verification else None, dumps(usage), task_type, card_id),
    )


def card_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "paper_id": r["paper_id"], "chain_id": r["chain_id"], "object_type": r["object_type"],
        "object_label": r["object_label"], "selection": loads(r["selection_json"]), "question": r["question"],
        "intent": r["intent"], "task_type": r["task_type"], "model": r["model"], "status": r["status"],
        "saved": bool(r["saved"]), "conversation_id": r["conversation_id"], "attachments": loads(r["attachments_json"], []),
        "answer": loads(r["answer_json"]) or {"direct": "", "detail": None, "evidence_notes": [], "inferences": [], "follow_ups": [],
                                              "outside": None, "web": []},
        "raw_markdown": r["raw_markdown"], "evidence": loads(r["evidence_json"], []),
        "verification": loads(r["verification_json"]) or {"status": "unverified", "invalid_citations": [], "notes": []},
        "usage": loads(r["usage_json"]) or {"prompt_tokens": 0, "completion_tokens": 0},
        "created_at": r["created_at"],
    }


def get_card(app: AppContext, card_id: str) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT * FROM insight_cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        raise ApiError("not_found", "卡片不存在", 404)
    return card_to_dict(row)


def list_cards(app: AppContext, paper_id: str, chain_id: str | None, saved: bool | None, conversation_id: str | None = None) -> list[dict]:
    with app.db.session() as conn:
        _adopt_orphans(conn, paper_id)
    where, args = ["paper_id = ?", "status != 'streaming'"], [paper_id]
    if chain_id:
        where.append("chain_id = ?")
        args.append(chain_id)
    if conversation_id:
        where.append("conversation_id = ?")
        args.append(conversation_id)
    if saved is not None:
        where.append("saved = ?")
        args.append(int(saved))
    with app.db.session() as conn:
        rows = conn.execute(
            f"SELECT * FROM insight_cards WHERE {' AND '.join(where)} ORDER BY created_at, rowid", args
        ).fetchall()
    return [card_to_dict(r) for r in rows]


def recent_history(app: AppContext, conversation_id: str | None, limit: int = 4, answer_chars: int = 900, exclude_id: str | None = None) -> list[dict]:
    """这个对话里最近几轮已经答完的问答（旧的在前），随新问题一起发给模型，让对话连得上。
    只取同一个对话：别的对话、别的论文的问答不会带进来。exclude_id：正在被修改重答的那张卡片，不能把它自己的旧问答当上文。"""
    if not conversation_id:
        return []
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT question, selection_json, answer_json, attachments_json FROM insight_cards "
            "WHERE conversation_id = ? AND status = 'complete' AND id != ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, exclude_id or "", limit),
        ).fetchall()
    out: list[dict] = []
    for r in reversed(rows):
        ans = loads(r["answer_json"]) or {}
        text = "\n\n".join(x for x in (ans.get("direct"), ans.get("detail"), ans.get("outside")) if x)
        if not text.strip():
            continue
        sel = loads(r["selection_json"]) or {}
        out.append({
            "question": (r["question"] or "").strip(), "selection": (sel.get("text") or "").strip(),
            "attachments": [a["name"] for a in loads(r["attachments_json"], [])],
            "answer": text[:answer_chars] + ("…" if len(text) > answer_chars else ""),
        })
    return out


def list_chains(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        rows = conn.execute(
            "SELECT c.id, c.title, c.object_type, c.object_label, c.updated_at, "
            "(SELECT COUNT(*) FROM insight_cards k WHERE k.chain_id = c.id AND k.saved = 1 AND k.status = 'complete') AS n "
            "FROM insight_chains c WHERE c.paper_id = ? ORDER BY c.updated_at DESC", (paper_id,),
        ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "object_type": r["object_type"], "object_label": r["object_label"],
         "card_count": r["n"], "updated_at": r["updated_at"]}
        for r in rows if r["n"] > 0
    ]


def patch_card(app: AppContext, card_id: str, saved: bool | None, chain_id: str | None) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT paper_id FROM insight_cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise ApiError("not_found", "卡片不存在", 404)
        if chain_id is not None:
            ch = conn.execute("SELECT id FROM insight_chains WHERE id = ? AND paper_id = ?", (chain_id, row["paper_id"])).fetchone()
            if ch is None:
                raise ApiError("not_found", "分析链不存在", 404)
            conn.execute("UPDATE insight_cards SET chain_id = ? WHERE id = ?", (chain_id, card_id))
        if saved is not None:
            conn.execute("UPDATE insight_cards SET saved = ? WHERE id = ?", (int(saved), card_id))
    return get_card(app, card_id)


def delete_card(app: AppContext, card_id: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM insight_cards WHERE id = ?", (card_id,))
        if cur.rowcount == 0:
            raise ApiError("not_found", "卡片不存在", 404)
        conn.execute("DELETE FROM insight_chains WHERE id NOT IN (SELECT DISTINCT chain_id FROM insight_cards)")


def patch_chain(app: AppContext, chain_id: str, title: str) -> dict:
    title = title.strip()
    if not title:
        raise ApiError("bad_request", "标题不能为空")
    with app.db.session() as conn:
        cur = conn.execute("UPDATE insight_chains SET title = ?, updated_at = ? WHERE id = ?", (title[:120], utcnow(), chain_id))
        if cur.rowcount == 0:
            raise ApiError("not_found", "分析链不存在", 404)
        r = conn.execute(
            "SELECT c.id, c.title, c.object_type, c.object_label, c.updated_at, "
            "(SELECT COUNT(*) FROM insight_cards k WHERE k.chain_id = c.id AND k.saved = 1) AS n FROM insight_chains c WHERE c.id = ?",
            (chain_id,),
        ).fetchone()
    return {"id": r["id"], "title": r["title"], "object_type": r["object_type"], "object_label": r["object_label"],
            "card_count": r["n"], "updated_at": r["updated_at"]}


def delete_chain(app: AppContext, chain_id: str) -> None:
    with app.db.session() as conn:
        cur = conn.execute("DELETE FROM insight_chains WHERE id = ?", (chain_id,))
        if cur.rowcount == 0:
            raise ApiError("not_found", "分析链不存在", 404)
