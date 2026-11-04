"""在线题录解析：Crossref / OpenAlex（DOI）、arXiv，以及 PDF 下载。

所有函数都容忍网络失败：返回 None / 带 errors 的结果，由调用方决定如何向用户说明。
不绕过任何出版社或机构访问限制：只使用开放获取的 PDF 链接。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from ..util import normalize_doi

MAX_PDF_BYTES = 300 * 1024 * 1024
ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?", re.I
)
_TAG_RE = re.compile(r"<[^>]+>")


class NotFound(Exception):
    pass


@dataclass
class ResolvedPaper:
    doi: str | None = None
    arxiv_id: str | None = None
    metadata: dict = field(default_factory=dict)
    pdf_urls: list[str] = field(default_factory=list)
    access_info: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: str = "crossref"


def _strip_tags(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip() or None


def _year_from(parts) -> int | None:
    try:
        return int(parts["date-parts"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def fetch_crossref(client: httpx.Client, doi: str) -> dict | None:
    """返回 Crossref 题录；DOI 不存在抛 NotFound；网络问题返回 None。"""
    try:
        r = client.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    except httpx.HTTPError:
        return None
    if r.status_code == 404:
        raise NotFound(doi)
    if r.status_code != 200:
        return None
    msg = r.json().get("message", {})
    authors = []
    for a in msg.get("author", []) or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if name:
            authors.append(name)
    year = (
        _year_from(msg.get("issued") or {}) or _year_from(msg.get("published-print") or {})
        or _year_from(msg.get("published-online") or {})
    )
    return {
        "title": _strip_tags((msg.get("title") or [""])[0]) or "",
        "authors": authors,
        "year": year,
        "venue": _strip_tags((msg.get("container-title") or [None])[0]),
        "abstract": _strip_tags(msg.get("abstract")),
        "url": msg.get("URL"),
    }


def fetch_openalex(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = client.get(f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='/')}")
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    w = r.json()
    pdf_urls: list[str] = []
    landing: list[str] = []
    locations = list(w.get("locations") or [])
    if w.get("best_oa_location"):
        locations.insert(0, w["best_oa_location"])
    for loc in locations:
        if loc.get("pdf_url") and loc["pdf_url"] not in pdf_urls:
            pdf_urls.append(loc["pdf_url"])
        if loc.get("landing_page_url") and loc["landing_page_url"] not in landing:
            landing.append(loc["landing_page_url"])
    oa = (w.get("open_access") or {}).get("oa_url")
    if oa and oa.lower().endswith(".pdf") and oa not in pdf_urls:
        pdf_urls.append(oa)
    authors = [
        (a.get("author") or {}).get("display_name") for a in w.get("authorships") or []
        if (a.get("author") or {}).get("display_name")
    ]
    src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    return {
        "title": w.get("title") or w.get("display_name") or "",
        "authors": authors,
        "year": w.get("publication_year"),
        "venue": src,
        "pdf_urls": pdf_urls,
        "landing": landing,
        "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
    }


def resolve_doi(client: httpx.Client, raw_doi: str) -> ResolvedPaper:
    doi = normalize_doi(raw_doi)
    res = ResolvedPaper(doi=doi)
    if not doi:
        res.errors.append("无法识别的 DOI 格式")
        return res
    res.access_info.append({"label": "出版商页面", "url": f"https://doi.org/{doi}"})
    try:
        cr = fetch_crossref(client, doi)
    except NotFound:
        res.errors.append("Crossref 中未找到该 DOI")
        cr = None
    oa = fetch_openalex(client, doi)
    if cr is None and oa is None and not res.errors:
        res.errors.append("无法连接学术数据源，请检查网络后重试")
    meta: dict = {}
    if cr:
        meta.update({k: v for k, v in cr.items() if v})
    if oa:
        for k in ("title", "authors", "year", "venue"):
            if not meta.get(k) and oa.get(k):
                meta[k] = oa[k]
        res.pdf_urls.extend(oa["pdf_urls"])
        for url in oa["landing"]:
            res.access_info.append({"label": "开放获取页面", "url": url})
        res.source = "crossref" if cr else "openalex"
    res.metadata = meta
    return res


# ---- arXiv ----
def parse_arxiv_url(url: str) -> str | None:
    m = ARXIV_URL_RE.search(url)
    return m.group("id") if m else None


def fetch_arxiv(client: httpx.Client, arxiv_id: str) -> ResolvedPaper:
    res = ResolvedPaper(arxiv_id=arxiv_id, source="arxiv")
    res.access_info.append({"label": "arXiv 页面", "url": f"https://arxiv.org/abs/{arxiv_id}"})
    res.pdf_urls.append(f"https://arxiv.org/pdf/{arxiv_id}")
    try:
        r = client.get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id})
    except httpx.HTTPError:
        res.errors.append("无法连接 arXiv 元数据接口，仅能下载 PDF")
        return res
    if r.status_code != 200:
        return res
    try:
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        entry = ET.fromstring(r.text).find("a:entry", ns)
        if entry is None:
            return res
        title = re.sub(r"\s+", " ", (entry.findtext("a:title", "", ns) or "")).strip()
        if title.lower() == "error":
            res.errors.append("arXiv 中未找到该编号")
            res.pdf_urls.clear()
            return res
        published = entry.findtext("a:published", "", ns) or ""
        res.metadata = {
            "title": title,
            "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "venue": "arXiv 预印本",
            "abstract": re.sub(r"\s+", " ", entry.findtext("a:summary", "", ns) or "").strip() or None,
        }
        doi = entry.findtext("x:doi", "", ns)
        if doi:
            res.doi = normalize_doi(doi)
    except ET.ParseError:
        res.errors.append("arXiv 返回的元数据无法解析")
    return res


# ---- 下载 ----
class DownloadError(Exception):
    pass


def download_pdf(client: httpx.Client, url: str, dest: Path, max_bytes: int = MAX_PDF_BYTES) -> int:
    """下载并校验 PDF，返回字节数。仅接受 http(s)，校验 %PDF 文件头与大小上限。"""
    if urlparse(url).scheme not in ("http", "https"):
        raise DownloadError("只支持 http/https 链接")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise DownloadError(f"下载失败 {r.status_code}：该文件可能需要登录或机构权限")
            declared = int(r.headers.get("content-length") or 0)
            if declared > max_bytes:
                raise DownloadError(f"文件过大（{declared // 1024 // 1024} MB）")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError("文件超过大小上限")
                    f.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise DownloadError(f"网络错误：{type(exc).__name__}") from exc
    except DownloadError:
        dest.unlink(missing_ok=True)
        raise
    with open(dest, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" not in head:
        dest.unlink(missing_ok=True)
        raise DownloadError("下载内容不是 PDF，可能是登录页或付费墙页面")
    return total
