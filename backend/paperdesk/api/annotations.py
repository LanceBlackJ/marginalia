"""书签、笔记、高亮批注。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..context import AppContext
from ..library import repo
from ..util import ApiError, dumps, loads, utcnow
from .deps import get_ctx

router = APIRouter(tags=["annotations"])


def _check_paper(ctx: AppContext, paper_id: str) -> None:
    with ctx.db.session() as conn:
        repo.get_row(conn, paper_id)


# ---- 书签 ----
class BookmarkBody(BaseModel):
    page: int = Field(ge=1)
    label: str = ""


def _bookmark(r) -> dict:
    return {"id": r["id"], "page": r["page"], "label": r["label"], "created_at": r["created_at"]}


@router.get("/papers/{paper_id}/bookmarks")
def list_bookmarks(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    with ctx.db.session() as conn:
        rows = conn.execute("SELECT * FROM bookmarks WHERE paper_id = ? ORDER BY page, id", (paper_id,)).fetchall()
    return {"items": [_bookmark(r) for r in rows]}


@router.post("/papers/{paper_id}/bookmarks")
def add_bookmark(paper_id: str, body: BookmarkBody, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    with ctx.db.session() as conn:
        cur = conn.execute(
            "INSERT INTO bookmarks(paper_id, page, label, created_at) VALUES (?,?,?,?)",
            (paper_id, body.page, body.label.strip()[:200], utcnow()),
        )
        return _bookmark(conn.execute("SELECT * FROM bookmarks WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/papers/{paper_id}/bookmarks/{bid}")
def delete_bookmark(paper_id: str, bid: int, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        cur = conn.execute("DELETE FROM bookmarks WHERE id = ? AND paper_id = ?", (bid, paper_id))
    if cur.rowcount == 0:
        raise ApiError("not_found", "书签不存在", 404)
    return {"ok": True}


# ---- 笔记 ----
class NoteBody(BaseModel):
    text: str = ""
    page: int | None = None
    quote: str | None = None
    passage_ord: int | None = None
    card_id: str | None = None


class NotePatch(BaseModel):
    text: str


def _note(r) -> dict:
    return {
        "id": r["id"], "paper_id": r["paper_id"], "page": r["page"], "quote": r["quote"],
        "passage_ord": r["passage_ord"], "text": r["text"], "card_id": r["card_id"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        # 重新解析后引用位置对不上：保留引用原文与页码，标为“位置已失效”（见 library/reparse.py）
        "stale": bool(r["stale"]) if "stale" in r.keys() else False,
    }


@router.get("/papers/{paper_id}/notes")
def list_notes(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    with ctx.db.session() as conn:
        rows = conn.execute("SELECT * FROM notes WHERE paper_id = ? ORDER BY created_at DESC, id DESC", (paper_id,)).fetchall()
    return {"items": [_note(r) for r in rows]}


@router.post("/papers/{paper_id}/notes")
def add_note(paper_id: str, body: NoteBody, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    if not body.text.strip() and not (body.quote or "").strip():
        raise ApiError("bad_request", "笔记内容不能为空")
    now = utcnow()
    with ctx.db.session() as conn:
        cur = conn.execute(
            "INSERT INTO notes(paper_id, page, quote, passage_ord, text, card_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (paper_id, body.page, (body.quote or None) and body.quote[:4000], body.passage_ord,
             body.text[:20000], body.card_id, now, now),
        )
        return _note(conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.patch("/notes/{nid}")
def patch_note(nid: int, body: NotePatch, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        cur = conn.execute("UPDATE notes SET text = ?, updated_at = ? WHERE id = ?", (body.text[:20000], utcnow(), nid))
        if cur.rowcount == 0:
            raise ApiError("not_found", "笔记不存在", 404)
        return _note(conn.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone())


@router.delete("/notes/{nid}")
def delete_note(nid: int, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
    if cur.rowcount == 0:
        raise ApiError("not_found", "笔记不存在", 404)
    return {"ok": True}


# ---- 高亮 ----
class AnnotationBody(BaseModel):
    page: int = Field(ge=1)
    kind: Literal["highlight"] = "highlight"
    color: Literal["yellow", "blue", "green", "pink"] = "yellow"
    text: str = ""
    rects: list[list[float]]
    note: str | None = None


def _annotation(r) -> dict:
    return {
        "id": r["id"], "page": r["page"], "kind": r["kind"], "color": r["color"], "text": r["text"],
        "rects": loads(r["rects_json"], []), "note": r["note"], "created_at": r["created_at"],
    }


@router.get("/papers/{paper_id}/annotations")
def list_annotations(paper_id: str, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    with ctx.db.session() as conn:
        rows = conn.execute("SELECT * FROM annotations WHERE paper_id = ? ORDER BY page, id", (paper_id,)).fetchall()
    return {"items": [_annotation(r) for r in rows]}


@router.post("/papers/{paper_id}/annotations")
def add_annotation(paper_id: str, body: AnnotationBody, ctx: AppContext = Depends(get_ctx)):
    _check_paper(ctx, paper_id)
    if not body.rects or any(len(r) != 4 for r in body.rects):
        raise ApiError("bad_request", "高亮区域无效")
    with ctx.db.session() as conn:
        cur = conn.execute(
            "INSERT INTO annotations(paper_id, page, kind, color, text, rects_json, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (paper_id, body.page, body.kind, body.color, body.text[:4000], dumps(body.rects), body.note, utcnow()),
        )
        return _annotation(conn.execute("SELECT * FROM annotations WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/papers/{paper_id}/annotations/{aid}")
def delete_annotation(paper_id: str, aid: int, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        cur = conn.execute("DELETE FROM annotations WHERE id = ? AND paper_id = ?", (aid, paper_id))
    if cur.rowcount == 0:
        raise ApiError("not_found", "批注不存在", 404)
    return {"ok": True}
