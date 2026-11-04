from __future__ import annotations

import asyncio

import httpx

from paperdesk.context import OPENALEX_KEY, redact_url_keys

KEY = "test-openalex-key-123"


def _client(api):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    api.ctx.async_transport = httpx.MockTransport(handler)
    api.ctx.sync_transport = httpx.MockTransport(handler)
    return seen


def test_key_is_only_sent_to_openalex(api):
    seen = _client(api)
    assert api.put("/settings/openalex-key", {"api_key": KEY}).json() == {"has_key": True}
    assert api.get("/settings/openalex-key").json() == {"has_key": True}

    async def go():
        async with api.ctx.async_http() as c:
            await c.get("https://api.openalex.org/works", params={"search": "x"})
            await c.get("https://api.crossref.org/works", params={"query": "x"})
            await c.get("https://export.arxiv.org/api/query")

    asyncio.run(go())
    oa, cr, ax = seen
    assert oa.url.params["api_key"] == KEY and oa.url.params["search"] == "x"
    assert "api_key" not in cr.url.params and KEY not in str(cr.url)
    assert KEY not in str(ax.url)
    with api.ctx.http() as c:  # 同步客户端（DOI 元数据）同样只对 OpenAlex 带上 key
        c.get("https://api.openalex.org/works/https://doi.org/10.1/x")
        c.get("https://api.crossref.org/works/10.1/x")
    assert seen[3].url.params["api_key"] == KEY and "api_key" not in seen[4].url.params


def test_no_key_means_no_param_and_delete_removes_it(api):
    seen = _client(api)
    assert api.get("/settings/openalex-key").json() == {"has_key": False}
    asyncio.run(_get(api, "https://api.openalex.org/works"))
    assert "api_key" not in seen[0].url.params
    api.put("/settings/openalex-key", {"api_key": KEY})
    assert api.c.delete("/api/settings/openalex-key", headers={"Authorization": "Bearer test-token"}).json() == {"has_key": False}
    assert not api.ctx.secrets.has(OPENALEX_KEY)
    asyncio.run(_get(api, "https://api.openalex.org/works"))
    assert "api_key" not in seen[1].url.params


async def _get(api, url):
    async with api.ctx.async_http() as c:
        await c.get(url)


def test_empty_key_is_rejected(api):
    r = api.put("/settings/openalex-key", {"api_key": "   "})
    assert r.status_code == 400


def test_error_text_never_contains_the_key():
    msg = "Client error '429 Too Many Requests' for url 'https://api.openalex.org/works?search=x&api_key=abc123XYZ&per-page=5'"
    out = redact_url_keys(msg)
    assert "abc123XYZ" not in out and "api_key=***" in out and "per-page=5" in out
    assert redact_url_keys(ValueError("api_key=secretvalue")) == "api_key=***"
