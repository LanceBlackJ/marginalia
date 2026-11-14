"""主题知识库 / 个人知识画像 / 全局笔记 / 导出 API。契约见 docs/API-discovery.md。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..context import AppContext
from ..jobs import JobManager
from ..knowledge import export as kb_export
from ..knowledge import extract, interest, store
from ..util import ApiError
from .deps import get_ctx

router = APIRouter(tags=["knowledge"])


def register_jobs(manager: JobManager) -> None:
    extract.register_jobs(manager)
    from ..ai import prompts

    app = manager.app
    prompts.EXTRA_SYSTEM_HINTS["profile"] = lambda: extract.prompt_hint(app)  # 默认关闭，用户开启后才生效


# ---------------- 主题 ----------------
class TopicBody(BaseModel):
    name: str
    description: str = ""


class TopicPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/kb/topics")
def topics(ctx: AppContext = Depends(get_ctx)):
    return {"items": store.list_topics(ctx)}


@router.post("/kb/topics")
def create_topic(body: TopicBody, ctx: AppContext = Depends(get_ctx)):
    return store.create_topic(ctx, body.name, body.description)


@router.patch("/kb/topics/{tid}")
def patch_topic(tid: str, body: TopicPatch, ctx: AppContext = Depends(get_ctx)):
    return store.update_topic(ctx, tid, body.name, body.description)


@router.delete("/kb/topics/{tid}")
def delete_topic(tid: str, ctx: AppContext = Depends(get_ctx)):
    store.delete_topic(ctx, tid)
    return {"ok": True}


class TagBody(BaseModel):
    name: str


@router.post("/papers/{paper_id}/tags")
def add_tag(paper_id: str, body: TagBody, ctx: AppContext = Depends(get_ctx)):
    """给论文加标签（标签即知识库主题）：{id, name}。不存在的名字会自动创建。"""
    return store.add_paper_tag(ctx, paper_id, body.name)


@router.delete("/papers/{paper_id}/tags/{tid}")
def remove_tag(paper_id: str, tid: str, ctx: AppContext = Depends(get_ctx)):
    store.remove_paper_topic(ctx, paper_id, tid)
    return {"ok": True}


@router.get("/kb/topics/{tid}/papers")
def topic_papers(tid: str, ctx: AppContext = Depends(get_ctx)):
    return {"items": store.topic_papers(ctx, tid)}


class PaperTopicBody(BaseModel):
    paper_id: str


@router.post("/kb/topics/{tid}/papers")
def add_paper(tid: str, body: PaperTopicBody, ctx: AppContext = Depends(get_ctx)):
    store.set_paper_topic(ctx, body.paper_id, tid)
    return {"ok": True}


@router.delete("/kb/topics/{tid}/papers/{pid}")
def remove_paper(tid: str, pid: str, ctx: AppContext = Depends(get_ctx)):
    store.remove_paper_topic(ctx, pid, tid)
    return {"ok": True}


@router.post("/kb/topics/suggest")
async def suggest_topics(ctx: AppContext = Depends(get_ctx)):
    await extract.suggest_topics(ctx)
    return {"items": extract.list_topic_suggestions(ctx)}


@router.get("/kb/topic-suggestions")
def topic_suggestions(ctx: AppContext = Depends(get_ctx)):
    return {"items": extract.list_topic_suggestions(ctx)}


@router.post("/kb/topic-suggestions/{tid}/accept")
def accept_topic(tid: str, ctx: AppContext = Depends(get_ctx)):
    return extract.accept_topic_suggestion(ctx, tid)


@router.post("/kb/topic-suggestions/{tid}/reject")
def reject_topic(tid: str, ctx: AppContext = Depends(get_ctx)):
    extract.reject_topic_suggestion(ctx, tid)
    return {"ok": True}


# ---------------- 条目 ----------------
class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str | None = None
    ord: int | None = None
    kind: str | None = None
    note_id: int | None = None


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    content: str = ""
    kind: str = "other"
    topic_ids: list[str] = []
    source_paper_id: str | None = None
    evidence: list[EvidenceRef] = []
    card_id: str | None = None
    note_id: str | None = None


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = None
    content: str | None = None
    kind: str | None = None
    topic_ids: list[str] | None = None


@router.get("/kb/items")
def items(topic_id: str | None = None, paper_id: str | None = None, q: str | None = None, unassigned: bool = False,
          ctx: AppContext = Depends(get_ctx)):
    return {"items": store.list_items(ctx, topic_id, paper_id, q, unassigned)}


@router.post("/kb/items")
def create_item(body: ItemBody, ctx: AppContext = Depends(get_ctx)):
    return store.create_item(
        ctx, title=body.title, content=body.content, kind=body.kind, topic_ids=body.topic_ids, source_paper_id=body.source_paper_id,
        evidence_refs=[e.model_dump(exclude_none=True) for e in body.evidence], card_id=body.card_id, note_id=body.note_id)


@router.get("/kb/items/{iid}")
def get_item(iid: str, ctx: AppContext = Depends(get_ctx)):
    return store.get_item(ctx, iid)


@router.patch("/kb/items/{iid}")
def patch_item(iid: str, body: ItemPatch, ctx: AppContext = Depends(get_ctx)):
    return store.update_item(ctx, iid, body.model_dump(exclude_unset=True))


@router.delete("/kb/items/{iid}")
def delete_item(iid: str, ctx: AppContext = Depends(get_ctx)):
    store.delete_item(ctx, iid)
    return {"ok": True}


# ---------------- 候选 ----------------
class ExtractBody(BaseModel):
    paper_id: str


@router.post("/kb/extract")
def extract_(body: ExtractBody, ctx: AppContext = Depends(get_ctx)):
    with ctx.db.session() as conn:
        p = conn.execute("SELECT title FROM papers WHERE id = ?", (body.paper_id,)).fetchone()
    if p is None:
        raise ApiError("not_found", "论文不存在", 404)
    return {"job_id": ctx.jobs.submit("kb_extract", f"整理知识：{p['title']}", {"paper_id": body.paper_id}, body.paper_id)}


@router.get("/kb/candidates")
def candidates(paper_id: str | None = None, status: Literal["pending", "accepted", "rejected"] = "pending", ctx: AppContext = Depends(get_ctx)):
    return {"items": store.list_candidates(ctx, paper_id, status)}


class AcceptBody(BaseModel):
    topic_id: str | None = None
    new_topic: str | None = None
    title: str | None = None
    content: str | None = None
    kind: str | None = None


@router.post("/kb/candidates/{cid}/accept")
def accept(cid: str, body: AcceptBody, ctx: AppContext = Depends(get_ctx)):
    return store.accept_candidate(ctx, cid, topic_id=body.topic_id, new_topic=body.new_topic, title=body.title,
                                  content=body.content, kind=body.kind)


@router.post("/kb/candidates/{cid}/reject")
def reject(cid: str, ctx: AppContext = Depends(get_ctx)):
    store.reject_candidate(ctx, cid)
    return {"ok": True}


# ---------------- 关系 ----------------
class RelationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_kind: Literal["item", "paper"] = "item"
    from_id: str
    to_kind: Literal["item", "paper"] = "item"
    to_id: str
    relation: str
    description: str = ""
    evidence: list[EvidenceRef] = Field(min_length=1)


@router.get("/kb/relations")
def relations(topic_id: str | None = None, status: str | None = None, ctx: AppContext = Depends(get_ctx)):
    return {"items": store.list_relations(ctx, topic_id, status)}


@router.post("/kb/relations")
def create_relation(body: RelationBody, ctx: AppContext = Depends(get_ctx)):
    return store.create_relation(
        ctx, from_kind=body.from_kind, from_id=body.from_id, to_kind=body.to_kind, to_id=body.to_id, relation=body.relation,
        description=body.description, evidence_refs=[e.model_dump(exclude_none=True) for e in body.evidence])


@router.post("/kb/relations/suggest")
async def suggest_relations(body: dict, ctx: AppContext = Depends(get_ctx)):
    tid = body.get("topic_id")
    if not tid:
        raise ApiError("bad_request", "缺少 topic_id")
    return await extract.suggest_relations(ctx, tid)


@router.post("/kb/relations/{rid}/confirm")
def confirm_relation(rid: str, ctx: AppContext = Depends(get_ctx)):
    return store.set_relation_status(ctx, rid, "confirmed")


@router.delete("/kb/relations/{rid}")
def delete_relation(rid: str, ctx: AppContext = Depends(get_ctx)):
    store.delete_relation(ctx, rid)
    return {"ok": True}


# ---------------- 个人知识画像 ----------------
class MetaBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interests: list[str] | None = None
    explanation_depth: str | None = None
    use_in_prompts: bool | None = None


@router.get("/profile")
def profile(ctx: AppContext = Depends(get_ctx)):
    return {"meta": store.get_meta(ctx), "concepts": store.list_concepts(ctx), "suggestions": store.list_suggestions(ctx),
            "states": store.STATES, "prompt_hint": extract.prompt_hint(ctx)}


@router.put("/profile/meta")
def put_meta(body: MetaBody, ctx: AppContext = Depends(get_ctx)):
    return store.put_meta(ctx, body.model_dump(exclude_unset=True))


class ConceptBody(BaseModel):
    name: str
    state: str
    note: str = ""


class ConceptPatch(BaseModel):
    name: str | None = None
    state: str | None = None
    note: str | None = None


@router.post("/profile/concepts")
def add_concept(body: ConceptBody, ctx: AppContext = Depends(get_ctx)):
    return store.upsert_concept(ctx, body.name, body.state, body.note)


@router.patch("/profile/concepts/{cid}")
def patch_concept(cid: str, body: ConceptPatch, ctx: AppContext = Depends(get_ctx)):
    return store.update_concept(ctx, cid, body.state, body.note, body.name)


@router.delete("/profile/concepts/{cid}")
def delete_concept(cid: str, ctx: AppContext = Depends(get_ctx)):
    store.delete_concept(ctx, cid)
    return {"ok": True}


@router.get("/profile/interests")
def profile_interests(include_read_only: bool = False, ctx: AppContext = Depends(get_ctx)):
    """根据你的笔记、重点、收藏、提问和阅读深度推测的兴趣主题（随使用自动更新）。只是推测，不改动你确认过的画像。"""
    return {"items": interest.topic_interests(ctx, include_read_only=include_read_only)}


@router.post("/profile/interests/{topic_id}/mute")
def mute_interest(topic_id: str, ctx: AppContext = Depends(get_ctx)):
    """标记“不是我的兴趣”：之后不再显示，也不参与推荐。"""
    interest.set_muted(ctx, topic_id, True)
    return {"ok": True}


@router.delete("/profile/interests/{topic_id}/mute")
def unmute_interest(topic_id: str, ctx: AppContext = Depends(get_ctx)):
    interest.set_muted(ctx, topic_id, False)
    return {"ok": True}


@router.post("/profile/suggest")
def suggest_profile(ctx: AppContext = Depends(get_ctx)):
    return {"items": extract.suggest_profile(ctx)}


class ResolveBody(BaseModel):
    state: str | None = None


@router.post("/profile/suggestions/{sid}/accept")
def accept_suggestion(sid: str, body: ResolveBody, ctx: AppContext = Depends(get_ctx)):
    return extract.resolve_suggestion(ctx, sid, True, body.state)


@router.post("/profile/suggestions/{sid}/dismiss")
def dismiss_suggestion(sid: str, ctx: AppContext = Depends(get_ctx)):
    extract.resolve_suggestion(ctx, sid, False)
    return {"ok": True}


# ---------------- 全局笔记 ----------------
@router.get("/notes/all")
def all_notes(q: str | None = None, paper_id: str | None = None, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
              ctx: AppContext = Depends(get_ctx)):
    where, args = [], []
    if q and q.strip():
        where.append("(lower(n.text) LIKE ? OR lower(COALESCE(n.quote,'')) LIKE ?)")
        args += [f"%{q.strip().lower()}%"] * 2
    if paper_id:
        where.append("n.paper_id = ?")
        args.append(paper_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with ctx.db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM notes n{clause}", args).fetchone()[0]
        rows = conn.execute(
            "SELECT n.id, n.paper_id, n.page, n.quote, n.passage_ord, n.text, n.card_id, n.created_at, n.updated_at, p.title AS paper_title, "
            f"p.year AS paper_year FROM notes n JOIN papers p ON p.id = n.paper_id{clause} ORDER BY n.updated_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


# ---------------- 导出 ----------------
class ExportBody(BaseModel):
    kind: Literal["topic", "notes", "cards", "viewpoints"]
    topic_id: str | None = None
    paper_ids: list[str] | None = None
    include_evidence: bool = True


@router.post("/export/markdown")
def export_markdown(body: ExportBody, ctx: AppContext = Depends(get_ctx)):
    if body.kind == "topic":
        if not body.topic_id:
            raise ApiError("bad_request", "导出主题需要 topic_id")
        return kb_export.export_topic(ctx, body.topic_id, body.include_evidence)
    if body.kind == "viewpoints":
        return kb_export.export_viewpoints(ctx, body.topic_id)
    if body.kind == "notes":
        return kb_export.export_notes(ctx, body.paper_ids)
    return kb_export.export_cards(ctx, body.paper_ids)


# ---------------- 关系图与批量操作 ----------------
@router.get("/kb/graph")
def kb_graph(topic_id: str | None = None, kinds: str | None = Query(default=None, description="逗号分隔的条目类型"),
             ctx: AppContext = Depends(get_ctx)):
    """知识库关系图：节点（条目/主题/论文）与边（已确认关系实线、待确认虚线、归属/来源结构线）。"""
    from ..knowledge import graph

    return graph.build_graph(ctx, topic_id, [k for k in (kinds or "").split(",") if k] or None)


class BulkItemsBody(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["add_topic", "remove_topic", "delete"]
    topic_id: str | None = None


@router.post("/kb/items/bulk")
def kb_items_bulk(body: BulkItemsBody, ctx: AppContext = Depends(get_ctx)):
    from ..knowledge import graph

    return graph.bulk_items(ctx, body.item_ids, body.action, body.topic_id)


class MergeTopicsBody(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=100)
    target_id: str


@router.post("/kb/topics/merge")
def kb_topics_merge(body: MergeTopicsBody, ctx: AppContext = Depends(get_ctx)):
    from ..knowledge import graph

    return graph.merge_topics(ctx, body.source_ids, body.target_id)
