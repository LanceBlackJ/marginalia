"""自动寻找补充材料候选（只提出候选，绝不自动关联；不绕过付费墙/机构认证）。

线索来源（每个候选都带“匹配依据”）：
1. 出版商落地页（doi.org 跳转后的页面）里的 Supplementary information / MOESM / ESM / Source data 链接；
2. Crossref 记录里的 relation（is-supplemented-by 等）；
3. arXiv 页面的 Ancillary files；
4. 论文正文提到的代码仓库（GitHub，经 GitHub API 取默认分支与大小）。
不能只按文件名判定属于某篇论文——候选必须来自该论文自己的页面/记录/正文，且最终由用户确认。
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import httpx

from . import files as F

SUPP_HINT = re.compile(r"supplement|supporting|moesm|\besm\b|source[\s_-]?data|extended[\s_-]?data|additional[\s_-]?file|ancillary|补充|附件", re.I)
FILE_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".tsv", ".zip", ".txt", ".tex", ".json", ".gz", ".tar", ".mp4"}
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", re.I)
MAX_CANDIDATES = 40


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def _anchors(html: str, base: str) -> list[tuple[str, str]]:
    p = _Anchors()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001  畸形 HTML 不应让发现流程失败
        pass
    return [(urljoin(base, h), t) for h, t in p.links if h and not h.startswith(("javascript:", "mailto:", "#"))]


def _cid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _guess_category(filename: str, text: str = "") -> str:
    from .classify import rule_category

    return rule_category(filename, text)[0]


def _candidate(url: str, *, source: str, basis: str, label: str = "", size: int | None = None) -> dict:
    name = F.sanitize_filename(unquote(urlparse(url).path.rsplit("/", 1)[-1]) or label or "download")
    return {
        "id": _cid(url), "url": url, "filename": name, "size": size, "mime": F.mime_of(name), "source": source,
        "basis": basis, "kind": F.kind_of(name), "category_guess": _guess_category(name, label),
        "label": label[:120],
    }


def _from_landing(client: httpx.Client, doi: str, notes: list[str]) -> list[dict]:
    out: list[dict] = []
    try:
        r = client.get(f"https://doi.org/{doi}", timeout=15, headers={"Accept": "text/html,application/xhtml+xml"})
    except httpx.HTTPError as exc:
        notes.append("无法访问出版商页面")
        return out
    if r.status_code in (401, 403):
        notes.append("出版商页面拒绝了自动访问。如果你有机构权限，请在浏览器中下载后用“添加本地文件”导入。")
        return out
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        notes.append("出版商页面没有返回可解析的网页")
        return out
    host = urlparse(str(r.url)).netloc
    seen = set()
    for href, text in _anchors(r.text, str(r.url)):
        ext = F.ext_of(urlparse(href).path)
        looks_file = ext in FILE_EXT or "mediaobjects" in href.lower() or "/esm/" in href.lower()
        if not looks_file or not SUPP_HINT.search(f"{href} {text}"):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(_candidate(href, source="publisher", label=text,
                              basis=f"出版商页面 {host} 上的补充材料链接" + (f"：“{text[:50]}”" if text else "")))
    if not out:
        notes.append(f"在出版商页面 {host} 上没有找到补充材料链接")
    return out


def _from_crossref(client: httpx.Client, doi: str, notes: list[str]) -> list[dict]:
    out: list[dict] = []
    try:
        r = client.get(f"https://api.crossref.org/works/{doi}", timeout=12)
    except httpx.HTTPError:
        return out
    if r.status_code != 200:
        return out
    msg = r.json().get("message", {})
    rel = msg.get("relation") or {}
    for key in ("is-supplemented-by", "has-supplement", "is-supplement-to"):
        for item in rel.get(key, []) or []:
            ident, kind = item.get("id", ""), item.get("id-type", "")
            if kind == "doi":
                url = f"https://doi.org/{ident}"
            elif kind == "uri" and ident.startswith("http"):
                url = ident
            else:
                continue
            c = _candidate(url, source="crossref", label=key,
                           basis=f"Crossref 记录里的关联 {key}")
            c["kind"] = "other"
            out.append(c)
    return out


def _from_arxiv(client: httpx.Client, arxiv_id: str, notes: list[str]) -> list[dict]:
    out: list[dict] = []
    try:
        r = client.get(f"https://arxiv.org/abs/{arxiv_id}", timeout=12)
    except httpx.HTTPError:
        return out
    if r.status_code != 200:
        return out
    for href, text in _anchors(r.text, str(r.url)):
        if f"/src/{arxiv_id}/anc/" in href or "/anc/" in href:
            out.append(_candidate(href, source="arxiv", label=text, basis="arXiv 页面的附加文件"))
    return out


def repos_in_text(text: str) -> list[tuple[str, str]]:
    seen, out = set(), []
    for owner, repo in GITHUB_RE.findall(text or ""):
        repo = re.sub(r"(\.git)?[.,;:)\]]*$", "", repo)
        key = (owner.lower(), repo.lower())
        if repo and key not in seen and owner.lower() not in ("sponsors", "topics", "features"):
            seen.add(key)
            out.append((owner, repo))
    return out[:5]


def _from_code_repos(client: httpx.Client, text: str, notes: list[str]) -> list[dict]:
    out: list[dict] = []
    for owner, repo in repos_in_text(text):
        branch, size = "main", None
        try:
            r = client.get(f"https://api.github.com/repos/{owner}/{repo}", timeout=10,
                           headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                j = r.json()
                branch = j.get("default_branch") or "main"
                size = (j.get("size") or 0) * 1024 or None
            elif r.status_code == 404:
                notes.append(f"论文提到的仓库 {owner}/{repo} 在 GitHub 上已不存在或为私有")
                continue
        except httpx.HTTPError:
            notes.append(f"无法访问 GitHub API，{owner}/{repo} 的大小未知")
        url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        c = _candidate(url, source="code", label=f"{owner}/{repo}",
                       basis=f"论文正文提到的代码仓库 github.com/{owner}/{repo}", size=size)
        c["filename"] = f"{repo}-{branch}.zip"
        c["kind"] = "archive"
        c["category_guess"] = "代码"
        out.append(c)
    return out


def discover(client: httpx.Client, *, doi: str | None, arxiv_id: str | None, body_text: str) -> dict:
    notes: list[str] = []
    cands: list[dict] = []
    if doi:
        cands += _from_landing(client, doi, notes)
        cands += _from_crossref(client, doi, notes)
    if arxiv_id:
        cands += _from_arxiv(client, arxiv_id, notes)
    cands += _from_code_repos(client, body_text, notes)
    if not doi and not arxiv_id:
        notes.append("这篇论文没有 DOI/arXiv 编号，只能根据正文里提到的代码仓库给出候选")
    uniq: dict[str, dict] = {}
    for c in cands:
        uniq.setdefault(c["url"], c)
    out = list(uniq.values())[:MAX_CANDIDATES]
    for c in out:  # 尽力探测大小，用于决定是否需要批准
        if c["size"] is None and c["kind"] != "other":
            c["size"] = F.probe_size(client, c["url"])
    return {"candidates": out, "notes": notes}
