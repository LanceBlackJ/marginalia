"""F3（发现/比较/知识库）测试辅助：按主机路由的模拟网络 + 针对本模块提示词的假 LLM。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

import httpx
from fastapi.testclient import TestClient

from conftest import TOKEN, Api

LLM_HOST = "llm.test"


def sse_ok(text: str, pt: int = 20, ct: int = 20) -> httpx.Response:
    frames = [json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False),
              json.dumps({"choices": [], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}), "[DONE]"]
    return httpx.Response(200, content="".join(f"data: {f}\n\n" for f in frames).encode("utf-8"),
                          headers={"content-type": "text/event-stream"})


def fake_vec(text: str, dim: int = 16) -> list[float]:
    """确定性“嵌入”：按词哈希累加，词重叠越多余弦越高，便于断言排序。"""
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]{3,}", text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    return v


class FakeWorld:
    """同时扮演学术 API 与 LLM 网关。routes: host 子串 → handler(request) -> Response。"""

    def __init__(self):
        self.routes: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list[httpx.Request] = []
        self.llm_calls: list[dict] = []
        self.llm_handlers: list[Callable[[str, str, dict], str | None]] = []
        self.embed_enabled = True
        self.transport = httpx.MockTransport(self.handle)

    def route(self, host: str, handler) -> None:
        self.routes[host] = handler

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == LLM_HOST:
            return self._llm(request)
        for key, h in self.routes.items():
            if key in host:
                return h(request)
        return httpx.Response(404, text=f"no route for {host}")

    def _llm(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/embeddings"):
            if not self.embed_enabled:
                return httpx.Response(500, text="no embeddings")
            data = [{"index": i, "embedding": fake_vec(t)} for i, t in enumerate(body["input"])]
            return httpx.Response(200, json={"data": data})
        msgs = body.get("messages", [])
        system = "\n".join(m["content"] for m in msgs if m["role"] == "system")
        user = "\n".join(m["content"] for m in msgs if m["role"] == "user")
        self.llm_calls.append({"model": body.get("model"), "system": system, "user": user})
        for h in self.llm_handlers:
            out = h(system, user, body)
            if out is not None:
                return sse_ok(out)
        return sse_ok("{}")


def make_world_api(tmp_path: Path, world: FakeWorld, *, configure: bool = True) -> Api:
    from paperdesk.app import create_app

    app = create_app(tmp_path / "data", TOKEN, sync_transport=world.transport, async_transport=world.transport)
    api = Api(TestClient(app), app)
    if configure:
        r = api.put("/settings/ai", {"fast_model": "fast-m", "strong_model": "strong-m", "base_url": f"https://{LLM_HOST}/v1"})
        assert r.status_code == 200, r.text
        api.put("/settings/ai/secret", {"api_key": "sk-test"})
    return api


# ---------------- 构造各来源的模拟响应 ----------------
def oa_work(i: int, title: str, *, doi: str | None, year: int = 2022, cites: int = 10, fwci: float | None = 1.5, pct: float | None = 0.8,
            wtype: str = "article", source: str | None = "Test Journal", stype: str = "journal", core: bool = True, abstract: str = "",
            locations: list | None = None, refs: list | None = None, field: tuple = ("17", "Computer Science"), version: str = "publishedVersion",
            authors: tuple = ("Alice Zhang", "Bob Miller")) -> dict:
    inv: dict[str, list[int]] = {}
    for n, w in enumerate((abstract or "").split()):
        inv.setdefault(w, []).append(n)
    return {
        "id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title, "display_name": title,
        "publication_year": year, "publication_date": f"{year}-06-01", "type": wtype,
        "primary_location": {"is_oa": False, "landing_page_url": f"https://doi.org/{doi}" if doi else None, "pdf_url": None, "version": version,
                             "source": {"id": "https://openalex.org/S1", "display_name": source, "type": stype, "is_core": core} if source else None},
        "locations": locations or [], "cited_by_count": cites, "fwci": fwci,
        "citation_normalized_percentile": {"value": pct} if pct is not None else None, "abstract_inverted_index": inv or None,
        "authorships": [{"author": {"display_name": a}} for a in authors],
        "primary_topic": {"display_name": "Complex Network Analysis", "subfield": {"display_name": "Statistical Physics"},
                          "field": {"id": f"https://openalex.org/fields/{field[0]}", "display_name": field[1]},
                          "domain": {"display_name": "Physical Sciences"}},
        "open_access": {"is_oa": False}, "ids": {"openalex": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{doi}" if doi else None},
        "referenced_works": refs or [], "related_works": [],
    }


def arxiv_feed(entries: list[dict]) -> str:
    parts = []
    for e in entries:
        parts.append(
            f"<entry><id>http://arxiv.org/abs/{e['id']}v1</id><published>{e.get('published', '2023-03-01T00:00:00Z')}</published>"
            f"<title>{e['title']}</title><summary>{e.get('summary', 'An abstract about cascading failure recovery.')}</summary>"
            + "".join(f"<author><name>{a}</name></author>" for a in e.get("authors", ["Alice Zhang"]))
            + (f"<x:doi>{e['doi']}</x:doi>" if e.get("doi") else "")
            + (f"<x:journal_ref>{e['jref']}</x:journal_ref>" if e.get("jref") else "")
            + (f"<x:comment>{e['comment']}</x:comment>" if e.get("comment") else "")
            + "<x:primary_category term='cs.LG'/></entry>")
    return ("<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom' xmlns:x='http://arxiv.org/schemas/atom'>"
            + "".join(parts) + "</feed>")
