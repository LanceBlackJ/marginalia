"""查找论文代码：GitHub 仓库搜索。只有在 README 里找到论文标题 / arXiv 号 / DOI（且不是论文清单类仓库）时才标“高匹配”，否则只是“候选”；清单类仓库标“仅提及”。"""
from __future__ import annotations

import base64
import re

import httpx

from ..context import AppContext
from ..util import ApiError
from .models import norm_arxiv, norm_doi, norm_title
from .search import http_client

GITHUB = "https://api.github.com"
# 论文清单 / 综述类仓库：README 里当然会出现论文标题，但它们不是该论文的实现
_LIST_RE = re.compile(r"awesome|paper-?list|papers?|reading-?list|survey|curated|collection|resources|list", re.I)


def is_list_repo(item: dict) -> bool:
    text = " ".join([str(item.get("name") or ""), str(item.get("description") or ""), " ".join(item.get("topics") or [])])
    return bool(_LIST_RE.search(text))


class GithubLimited(Exception):
    pass


def match_evidence(readme: str, *, title: str | None, doi: str | None, arxiv_id: str | None) -> list[str]:
    """在 README 里查找可核对的线索。返回证据说明列表（空表示没有找到）。"""
    ev: list[str] = []
    low = readme.lower()
    nr = norm_title(readme)
    if title and len(norm_title(title)) > 12 and norm_title(title) in nr:
        ev.append("README 中出现了论文标题")
    if arxiv_id and re.search(rf"arxiv[^\n]{{0,40}}{re.escape(arxiv_id)}|{re.escape(arxiv_id)}", low):
        ev.append(f"README 中出现了 arXiv 编号 {arxiv_id}")
    if doi and doi.lower() in low:
        ev.append(f"README 中出现了 DOI {doi}")
    return ev


async def _gh_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict | list:
    try:
        r = await client.get(f"{GITHUB}{path}", params=params, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError as exc:
        raise ApiError("network", "无法连接 GitHub", 502) from exc
    if r.status_code in (403, 429):
        raise GithubLimited("GitHub 未登录时每分钟只允许约 10 次搜索，请稍后再试")
    if r.status_code != 200:
        raise ApiError("network", f"GitHub 返回 HTTP {r.status_code}", 502)
    return r.json()


async def find_code(app: AppContext, *, title: str | None, doi: str | None, arxiv_id: str | None) -> dict:
    doi, arxiv_id = norm_doi(doi), norm_arxiv(arxiv_id)
    if not (title or doi or arxiv_id):
        raise ApiError("bad_request", "需要标题、DOI 或 arXiv 编号")
    queries: list[str] = []
    if arxiv_id:
        queries.append(f'"{arxiv_id}" in:readme')
    if title:
        queries.append(f'"{title[:120]}" in:readme,description')
    warnings: list[str] = []
    repos: dict[str, dict] = {}
    async with http_client(app) as client:
        for q in queries:
            try:
                data = await _gh_get(client, "/search/repositories", {"q": q, "per_page": 5, "sort": "stars"})
            except GithubLimited as exc:
                warnings.append(str(exc))
                break
            for it in data.get("items", []):  # type: ignore[union-attr]
                repos.setdefault(it["full_name"], it)
        cands = []
        for full_name, it in list(repos.items())[:6]:
            ev: list[str] = []
            try:
                rd = await _gh_get(client, f"/repos/{full_name}/readme")
                text = base64.b64decode(rd.get("content", "")).decode("utf-8", "replace") if isinstance(rd, dict) else ""
                ev = match_evidence(text, title=title, doi=doi, arxiv_id=arxiv_id)
            except GithubLimited as exc:
                warnings.append(str(exc))
            except ApiError:
                pass
            if ev and is_list_repo(it):
                match = "mention"
                ev = ev + ["这是论文清单/综述类仓库，只是提到了这篇论文，不是它的代码实现"]
            else:
                match = "high" if ev else "candidate"
            cands.append({
                "full_name": full_name, "url": it.get("html_url"), "description": it.get("description"),
                "stars": it.get("stargazers_count"), "language": it.get("language"), "updated": it.get("pushed_at"),
                "match": match,
                "evidence": ev or ["仅仓库名称/描述与检索词相近；未在 README 中找到论文标题、arXiv 编号或 DOI，不能确认是该论文的官方实现"],
            })
    order = {"high": 0, "candidate": 1, "mention": 2}
    cands.sort(key=lambda c: (order[c["match"]], -(c["stars"] or 0)))
    return {"candidates": cands, "queries": queries, "warnings": list(dict.fromkeys(warnings))}
