"""向量嵌入（可选增强）：调用与聊天模型同一网关的 /embeddings，失败一律返回 None，调用方回退到词面匹配。"""
from __future__ import annotations

import hashlib
import math
import struct

import httpx

from ..ai.settings import SECRET_NAME, load_settings
from ..context import USER_AGENT, AppContext
from ..db import register_schema
from ..util import loads

register_schema("discovery_embeddings", """
CREATE TABLE IF NOT EXISTS discovery_embeddings (
  model TEXT NOT NULL,
  hash TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY (model, hash)
);
""")

DEFAULT_EMBEDDING_MODEL = "bge-m3"
MAX_BATCH = 32


def discovery_settings(app: AppContext) -> dict:
    with app.db.session() as conn:
        row = conn.execute("SELECT value_json FROM settings WHERE key = 'discovery'").fetchone()
    return {"embedding_model": DEFAULT_EMBEDDING_MODEL, "use_embeddings": True, **(loads(row["value_json"], {}) if row else {})}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def sim_to_unit(cos: float) -> float:
    """bge-m3 对相关文本的余弦多在 0.35–0.8；映射到 0..1 便于与词面相关性混合。"""
    return max(0.0, min(1.0, (cos - 0.25) / 0.5))


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


async def embed_texts(app: AppContext, texts: list[str]) -> list[list[float]] | None:
    """返回与 texts 等长的向量列表；任何问题（无 Key、接口不支持、超时）都返回 None。"""
    ds = discovery_settings(app)
    if not ds.get("use_embeddings") or not texts:
        return None
    cfg = load_settings(app)
    key = app.secrets.get(SECRET_NAME)
    if not key or cfg.get("provider_kind") != "openai_compatible":
        return None
    model = ds["embedding_model"]
    hashes = [_h(t) for t in texts]
    cached: dict[str, list[float]] = {}
    with app.db.session() as conn:
        for i in range(0, len(hashes), 500):
            part = hashes[i : i + 500]
            rows = conn.execute(
                f"SELECT hash, dim, vec FROM discovery_embeddings WHERE model = ? AND hash IN ({','.join('?' * len(part))})",
                (model, *part),
            ).fetchall()
            for r in rows:
                cached[r["hash"]] = list(struct.unpack(f"{r['dim']}f", r["vec"]))
    missing = [(h, t) for h, t in zip(hashes, texts) if h not in cached]
    seen: set[str] = set()
    missing = [(h, t) for h, t in missing if not (h in seen or seen.add(h))]
    if missing:
        try:
            async with httpx.AsyncClient(timeout=40, transport=app.async_transport, headers={"User-Agent": USER_AGENT}) as client:
                for i in range(0, len(missing), MAX_BATCH):
                    batch = missing[i : i + MAX_BATCH]
                    r = await client.post(
                        f"{cfg['base_url'].rstrip('/')}/embeddings", headers={"Authorization": f"Bearer {key}"},
                        json={"model": model, "input": [t[:2000] for _, t in batch]},
                    )
                    if r.status_code != 200:
                        return None
                    data = r.json().get("data") or []
                    if len(data) != len(batch):
                        return None
                    with app.db.session() as conn:
                        for (h, _t), item in zip(batch, sorted(data, key=lambda d: d.get("index", 0))):
                            vec = item["embedding"]
                            cached[h] = vec
                            conn.execute(
                                "INSERT OR REPLACE INTO discovery_embeddings(model, hash, dim, vec) VALUES (?,?,?,?)",
                                (model, h, len(vec), struct.pack(f"{len(vec)}f", *vec)),
                            )
        except (httpx.HTTPError, KeyError, ValueError):
            return None
    return [cached[h] for h in hashes]
