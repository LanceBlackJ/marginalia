"""语义向量检索：为论文段落建立嵌入索引（OpenAI 兼容 /embeddings，默认 bge-m3），查询时与 BM25 融合。

要点：
- bge-m3 是多语言嵌入，中文问题可以直接检索英文论文，不再需要额外调用模型把问题翻译成英文检索词；
- 索引在后台任务里建（`embed_paper`），向量存 SQLite（float32 BLOB）；查询时用 numpy 余弦相似度；
- 任何一步失败都只降级为纯 BM25 检索，不影响问答；
- 发给云端的段落文本同样记入隐私日志。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ..context import AppContext
from ..db import register_schema
from ..jobs import JobContext, JobManager
from ..util import ApiError, utcnow
from . import usage as usage_log
from .providers import LLMError, LLMProvider, OpenAICompatibleProvider, estimate_tokens
from .settings import load_settings

register_schema("embeddings", """
CREATE TABLE IF NOT EXISTS passage_embeddings (
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, ord, model)
);
""")

EMBED_KINDS = ("paragraph", "caption", "table")  # 参考文献、图内文字、公式不建索引
MAX_CHARS = 1000
MIN_CHARS = 20  # 太短的段落（编号、孤立短语）没有检索价值，不建索引
BATCH = 24


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


async def embed_batch(provider: LLMProvider, model: str, texts: list[str]) -> list[list[float]]:
    """调用 /embeddings。只支持 OpenAI 兼容接口。"""
    if not isinstance(provider, OpenAICompatibleProvider):
        raise LLMError("not_supported", "当前提供商类型不支持嵌入接口，已回退到关键词检索。", status=400)
    last: LLMError | None = None
    for attempt in range(provider.max_retries + 1):
        try:
            async with provider._client() as client:
                r = await client.post(f"{provider.base_url}/embeddings", headers=provider._headers(),
                                      json={"model": model, "input": texts})
            if r.status_code == 200:
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                vecs = [d["embedding"] for d in data]
                if len(vecs) != len(texts):
                    raise LLMError("provider_error", "嵌入接口返回的向量数量不符。")
                return vecs
            if r.status_code in (401, 403):
                raise LLMError("auth_failed", "API Key 无效或没有访问嵌入模型的权限。", status=401)
            if r.status_code == 404:
                raise LLMError("provider_error", "嵌入模型或接口不存在，请在设置里检查嵌入模型名称。")
            last = LLMError("provider_error", f"嵌入接口返回错误 {r.status_code}。", retriable=r.status_code >= 500 or r.status_code == 429)
        except LLMError as err:
            if not err.retriable:
                raise
            last = err
        except Exception as exc:  # noqa: BLE001  网络/超时
            last = provider._transport_error(exc)  # type: ignore[arg-type]
        if attempt >= provider.max_retries or not (last and last.retriable):
            break
        await provider._sleep_backoff(attempt)
    raise last or LLMError("provider_error", "嵌入调用失败")


def _enabled(app: AppContext) -> tuple[bool, dict]:
    cfg = load_settings(app)
    return bool(cfg.get("semantic_retrieval", True) and cfg.get("embedding_model")), cfg


def index_status(app: AppContext, paper_id: str) -> dict:
    cfg = load_settings(app)
    model = cfg.get("embedding_model") or ""
    with app.db.session() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM passages WHERE paper_id = ? AND length(trim(text)) >= {MIN_CHARS} "
            f"AND kind IN ({','.join('?' * len(EMBED_KINDS))})",
            (paper_id, *EMBED_KINDS),
        ).fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM passage_embeddings WHERE paper_id = ? AND model = ?", (paper_id, model)
        ).fetchone()[0] if model else 0
    return {"model": model, "indexed": done, "total": total, "complete": bool(total) and done >= total}


def enqueue_embedding(app: AppContext, paper_id: str) -> str | None:
    """已配置嵌入模型且索引不完整时创建后台任务；否则返回 None。"""
    enabled, _cfg = _enabled(app)
    if not enabled:
        return None
    st = index_status(app, paper_id)
    if not st["total"] or st["complete"]:
        return None
    with app.db.session() as conn:
        title = (conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone() or {"title": ""})["title"]
    return app.jobs.submit("embed_paper", f"建立语义索引：{title}", {"paper_id": paper_id}, paper_id)


async def _embed_paper(app: AppContext, ctx: JobContext, paper_id: str) -> int:
    from .service import AIService

    cfg = load_settings(app)
    model = cfg.get("embedding_model") or ""
    if not model:
        raise ApiError("not_configured", "尚未配置嵌入模型。")
    try:
        provider, _ = AIService(app).provider(cfg)
    except LLMError as err:
        raise ApiError(err.code, err.message) from err
    with app.db.session() as conn:
        rows = conn.execute(
            f"SELECT p.ord, p.text FROM passages p WHERE p.paper_id = ? AND p.kind IN ({','.join('?' * len(EMBED_KINDS))}) "
            "AND p.ord NOT IN (SELECT ord FROM passage_embeddings WHERE paper_id = ? AND model = ?) ORDER BY p.ord",
            (paper_id, *EMBED_KINDS, paper_id, model),
        ).fetchall()
    todo = [(r["ord"], r["text"][:MAX_CHARS]) for r in rows if len(r["text"].strip()) >= MIN_CHARS]
    if not todo:
        return 0
    chars = sum(len(t) for _o, t in todo)
    usage_log.record_request(
        app, task_type="embedding", model=model, paper_id=paper_id, passage_ords=[o for o, _t in todo[:2000]],
        chars_sent=chars, includes={"question": False, "selection": False, "notes": False, "supplements": False, "code": False},
        summary=f"发送了 {len(todo)} 段原文（约 {chars:,} 字）用于建立语义索引",
    )
    done = 0
    for i in range(0, len(todo), BATCH):
        ctx.check_cancelled()
        batch = todo[i : i + BATCH]
        vecs = await embed_batch(provider, model, [t for _o, t in batch])
        now = utcnow()
        with app.db.session() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO passage_embeddings(paper_id, ord, model, dim, vec, created_at) VALUES (?,?,?,?,?,?)",
                [(paper_id, o, model, len(v), pack(v), now) for (o, _t), v in zip(batch, vecs)],
            )
        usage_log.record_usage(app, "embedding", model, sum(estimate_tokens(t) for _o, t in batch), 0, estimated=True,
                               paper_id=paper_id, job_id=ctx.job_id)
        done += len(batch)
        ctx.progress(done / len(todo), f"已索引 {done}/{len(todo)} 段")
    return done


def handle_embed_paper(ctx: JobContext) -> dict:
    n = ctx.run_async(_embed_paper(ctx.app, ctx, ctx.payload["paper_id"]))
    return {"embedded": n}


def register(manager: JobManager) -> None:
    manager.register("embed_paper", handle_embed_paper)


# ---------------------------------------------------------------- 查询
@dataclass
class VectorHit:
    ord: int
    score: float  # 余弦相似度


async def embed_query(app: AppContext, provider: LLMProvider, cfg: dict, paper_id: str, text: str) -> list[float] | None:
    """查询向量；未启用、索引为空、或调用失败时返回 None（调用方退回关键词检索）。"""
    if not (cfg.get("semantic_retrieval", True) and cfg.get("embedding_model")) or not text.strip():
        return None
    if index_status(app, paper_id)["indexed"] == 0:
        return None
    try:
        usage_log.record_request(
            app, task_type="embedding", model=cfg["embedding_model"], paper_id=paper_id, passage_ords=[],
            chars_sent=len(text[:MAX_CHARS]),
            includes={"question": True, "selection": False, "notes": False, "supplements": False, "code": False},
            summary=f"发送了问题/选中文字（{len(text[:MAX_CHARS])} 字）用于语义检索",
        )
        vecs = await embed_batch(provider, cfg["embedding_model"], [text[:MAX_CHARS]])
        return vecs[0]
    except LLMError:
        return None


def vector_search(app: AppContext, paper_id: str, query_vec: list[float], k: int = 24) -> list[VectorHit]:
    import numpy as np

    model = load_settings(app).get("embedding_model") or ""
    with app.db.session() as conn:
        rows = conn.execute("SELECT ord, dim, vec FROM passage_embeddings WHERE paper_id = ? AND model = ?", (paper_id, model)).fetchall()
    if not rows:
        return []
    dim = len(query_vec)
    rows = [r for r in rows if r["dim"] == dim]
    if not rows:
        return []
    mat = np.frombuffer(b"".join(r["vec"] for r in rows), dtype="<f4").reshape(len(rows), dim)
    q = np.asarray(query_vec, dtype="<f4")
    norms = np.linalg.norm(mat, axis=1) * (np.linalg.norm(q) or 1.0)
    sims = (mat @ q) / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-sims)[:k]
    return [VectorHit(rows[int(i)]["ord"], float(sims[int(i)])) for i in order]
