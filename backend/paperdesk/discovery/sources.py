"""各学术数据源的适配：把真实 API 的返回解析成统一的 Work。

每个来源分成“解析函数”（纯函数，便于测试）和“抓取函数”（异步 HTTP）。任何来源失败都只抛 SourceError，
由聚合层降级处理并告知用户，不会影响其它来源。不向任何服务发送用户邮箱或 API Key。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from .models import (
    Work, clean_text, decide_status, inverted_index_to_text, norm_arxiv, norm_doi,
)

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
S2 = "https://api.semanticscholar.org/graph/v1"
ARXIV = "https://export.arxiv.org/api/query"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"

OA_SELECT = (
    "id,doi,title,display_name,publication_year,publication_date,type,primary_location,locations,cited_by_count,fwci,"
    "citation_normalized_percentile,abstract_inverted_index,authorships,primary_topic,open_access,best_oa_location,"
    "biblio,ids,referenced_works,related_works"
)


class SourceError(Exception):
    """某个来源不可用/被限流/返回异常；message 可直接展示给用户。"""


# ======================= OpenAlex =======================
def parse_openalex(d: dict) -> Work:
    loc = d.get("primary_location") or {}
    src = loc.get("source") or {}
    venue_type = src.get("type")
    doi = norm_doi(d.get("doi") or (d.get("ids") or {}).get("doi"))
    work_type = d.get("type")
    status, evidence = decide_status(
        doi=doi, work_type=work_type, venue_type=venue_type, version=loc.get("version"),
    )
    topic = d.get("primary_topic") or {}
    field = topic.get("field") or {}
    domain = topic.get("domain") or {}
    topics = [t for t in [topic.get("display_name"), (topic.get("subfield") or {}).get("display_name")] if t]
    pct = (d.get("citation_normalized_percentile") or {}).get("value")
    pdf = loc.get("pdf_url") or (d.get("best_oa_location") or {}).get("pdf_url")
    arxiv = None
    versions: list[dict] = []
    for l in d.get("locations") or []:
        landing = l.get("landing_page_url") or ""
        s = l.get("source") or {}
        if "arxiv.org/abs/" in landing:
            arxiv = arxiv or norm_arxiv(landing)
        if s.get("type") == "repository" and l is not loc and (l.get("version") in ("submittedVersion", None)):
            versions.append({"kind": "preprint", "url": landing or None, "venue": s.get("display_name"),
                             "arxiv_id": norm_arxiv(landing), "doi": None})
    w = Work(
        title=(d.get("display_name") or d.get("title") or "").strip(),
        authors=[(a.get("author") or {}).get("display_name") for a in d.get("authorships") or []
                 if (a.get("author") or {}).get("display_name")],
        year=d.get("publication_year"), date=d.get("publication_date"),
        venue=src.get("display_name"), venue_type=venue_type, doi=doi, arxiv_id=arxiv,
        pmid=((d.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1] or None,
        abstract=inverted_index_to_text(d.get("abstract_inverted_index")),
        url=loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else d.get("id")),
        pdf_url=pdf, status=status, status_evidence=evidence,
        peer_reviewed=(None if not venue_type else (venue_type in ("journal", "conference") and status != "preprint")),
        core_venue=src.get("is_core"), citations=d.get("cited_by_count"), fwci=d.get("fwci"), percentile=pct,
        field_name=field.get("display_name"), field_id=(field.get("id") or "").rsplit("/", 1)[-1] or None,
        domain=domain.get("display_name"), topics=topics,
        open_access=(d.get("open_access") or {}).get("is_oa"), work_type=work_type, openalex_id=d.get("id"),
        referenced_works=d.get("referenced_works") or [], related_works=d.get("related_works") or [],
        sources=[{"name": "OpenAlex", "id": d.get("id"), "url": d.get("id")}], versions=versions,
    )
    return w


async def fetch_openalex(
    client: httpx.AsyncClient, query: str | None, *, limit: int = 30, sort: str | None = None,
    year_from: int | None = None, year_to: int | None = None, filters: list[str] | None = None,
    sample: int | None = None, seed: int | None = None,
) -> list[Work]:
    flt = list(filters or [])
    if year_from:
        flt.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        flt.append(f"to_publication_date:{year_to}-12-31")
    params: dict = {"per-page": min(200, limit), "select": OA_SELECT}
    if query:
        params["search"] = query
    if flt:
        params["filter"] = ",".join(flt)
    if sort:
        params["sort"] = sort
    if sample:
        params["sample"] = sample
        if seed is not None:
            params["seed"] = seed
    try:
        r = await client.get(f"{OPENALEX}/works", params=params)
    except httpx.HTTPError as exc:
        raise SourceError("OpenAlex 无法连接") from exc
    if r.status_code == 429:
        raise SourceError("OpenAlex 当日免费额度已用尽或被限流")
    if r.status_code != 200:
        raise SourceError(f"OpenAlex 返回 HTTP {r.status_code}")
    return [w for w in (parse_openalex(x) for x in r.json().get("results", [])) if w.title]


async def count_openalex(client: httpx.AsyncClient, filters: list[str]) -> int | None:
    """满足过滤条件的作品总数（meta.count），用于如实报告质量筛选的选择性；失败时返回 None。"""
    try:
        r = await client.get(f"{OPENALEX}/works", params={"filter": ",".join(filters), "per-page": 1, "select": "id"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    count = (r.json().get("meta") or {}).get("count")
    return int(count) if isinstance(count, int) else None


async def fetch_openalex_sources(client: httpx.AsyncClient, search: str, *, limit: int = 10) -> list[dict]:
    """按名称检索 OpenAlex 的“来源”（期刊/会议），返回 [{id, display_name, type, works_count}]。"""
    try:
        r = await client.get(f"{OPENALEX}/sources", params={"search": search, "per-page": limit,
                                                           "select": "id,display_name,type,works_count"})
    except httpx.HTTPError as exc:
        raise SourceError("OpenAlex 无法连接") from exc
    if r.status_code == 429:
        raise SourceError("OpenAlex 当日免费额度已用尽或被限流")
    if r.status_code != 200:
        raise SourceError(f"OpenAlex 返回 HTTP {r.status_code}")
    return [x for x in r.json().get("results", []) if x.get("id")]


async def fetch_openalex_by_ids(client: httpx.AsyncClient, ids: list[str]) -> list[Work]:
    """按 OpenAlex id（W…）批量取回（用于相关工作/参考文献）。"""
    short = [i.rsplit("/", 1)[-1] for i in ids if i]
    if not short:
        return []
    out: list[Work] = []
    for i in range(0, len(short), 50):
        chunk = short[i : i + 50]
        try:
            r = await client.get(f"{OPENALEX}/works", params={
                "filter": "openalex:" + "|".join(chunk), "per-page": 50, "select": OA_SELECT})
        except httpx.HTTPError as exc:
            raise SourceError("OpenAlex 无法连接") from exc
        if r.status_code != 200:
            raise SourceError(f"OpenAlex 返回 HTTP {r.status_code}")
        out.extend(parse_openalex(x) for x in r.json().get("results", []))
    return out


async def fetch_openalex_by_doi(client: httpx.AsyncClient, doi: str) -> Work | None:
    try:
        r = await client.get(f"{OPENALEX}/works/https://doi.org/{quote(doi, safe='/')}", params={"select": OA_SELECT})
    except httpx.HTTPError as exc:
        raise SourceError("OpenAlex 无法连接") from exc
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise SourceError(f"OpenAlex 返回 HTTP {r.status_code}")
    return parse_openalex(r.json())


async def fetch_openalex_citing(client: httpx.AsyncClient, openalex_id: str, limit: int = 25) -> list[Work]:
    short = openalex_id.rsplit("/", 1)[-1]
    try:
        r = await client.get(f"{OPENALEX}/works", params={
            "filter": f"cites:{short}", "sort": "cited_by_count:desc", "per-page": limit, "select": OA_SELECT})
    except httpx.HTTPError as exc:
        raise SourceError("OpenAlex 无法连接") from exc
    if r.status_code != 200:
        raise SourceError(f"OpenAlex 返回 HTTP {r.status_code}")
    return [parse_openalex(x) for x in r.json().get("results", [])]


# ======================= Crossref =======================
CR_SELECT = ("DOI,title,author,issued,container-title,type,is-referenced-by-count,abstract,URL,volume,issue,"
             "page,relation,accepted,published-print,published-online")


def _cr_year(item: dict) -> tuple[int | None, str | None]:
    for key in ("issued", "published-print", "published-online"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            y = parts[0]
            return int(y[0]), "-".join(f"{int(v):02d}" if i else str(int(v)) for i, v in enumerate(y))
    return None, None


def parse_crossref(item: dict) -> Work:
    doi = norm_doi(item.get("DOI"))
    ctype = item.get("type")
    subtype = item.get("subtype")  # 该接口的 select 不支持 subtype，只在完整记录里偶尔出现
    venue = (item.get("container-title") or [None])[0]
    venue_type = {"journal-article": "journal", "proceedings-article": "conference", "book-chapter": "book series"}.get(ctype)
    if ctype == "posted-content":
        venue_type = "repository"
    accepted = bool(item.get("accepted")) and not (item.get("volume") or item.get("issue") or item.get("page")) \
        and not item.get("published-print")
    status, ev = decide_status(
        doi=doi, work_type="preprint" if (ctype == "posted-content" or subtype == "preprint") else ctype,
        venue_type=venue_type, version=None, accepted_signal=accepted,
    )
    year, date = _cr_year(item)
    authors = []
    for a in item.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if name:
            authors.append(name)
    versions: list[dict] = []
    rel = item.get("relation") or {}
    for key, kind in (("has-preprint", "preprint"), ("is-preprint-of", "published")):
        for r in rel.get(key) or []:
            if r.get("id-type") == "doi":
                versions.append({"kind": kind, "doi": norm_doi(r.get("id")), "url": f"https://doi.org/{norm_doi(r.get('id'))}",
                                 "venue": None, "arxiv_id": None})
    title = clean_text((item.get("title") or [""])[0]) or ""
    return Work(
        title=title, authors=authors, year=year, date=date, venue=venue, venue_type=venue_type, doi=doi,
        abstract=clean_text(item.get("abstract")), url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        status=status, status_evidence=ev,
        peer_reviewed=(venue_type in ("journal", "conference") and status != "preprint") if venue_type else None,
        citations=item.get("is-referenced-by-count"), work_type=ctype,
        sources=[{"name": "Crossref", "id": doi, "url": item.get("URL")}], versions=versions,
    )


async def fetch_crossref(
    client: httpx.AsyncClient, query: str, *, limit: int = 20, sort: str | None = None,
    year_from: int | None = None, year_to: int | None = None,
) -> list[Work]:
    params: dict = {"query.bibliographic": query, "rows": limit, "select": CR_SELECT}
    flt = []
    if year_from:
        flt.append(f"from-pub-date:{year_from}")
    if year_to:
        flt.append(f"until-pub-date:{year_to}")
    if flt:
        params["filter"] = ",".join(flt)
    if sort == "date":
        params.update({"sort": "published", "order": "desc"})
    elif sort == "cited":
        params.update({"sort": "is-referenced-by-count", "order": "desc"})
    try:
        r = await client.get(f"{CROSSREF}/works", params=params)
    except httpx.HTTPError as exc:
        raise SourceError("Crossref 无法连接") from exc
    if r.status_code != 200:
        raise SourceError(f"Crossref 返回 HTTP {r.status_code}")
    return [w for w in (parse_crossref(x) for x in r.json().get("message", {}).get("items", [])) if w.title]


# ======================= Semantic Scholar =======================
S2_FIELDS = ("title,abstract,year,venue,publicationVenue,externalIds,authors,citationCount,openAccessPdf,"
             "publicationTypes,url,publicationDate,fieldsOfStudy,journal,isOpenAccess")


def parse_s2(p: dict) -> Work:
    ext = p.get("externalIds") or {}
    doi = norm_doi(ext.get("DOI"))
    arxiv = norm_arxiv(ext.get("ArXiv"))
    venue = (p.get("publicationVenue") or {}).get("name") or p.get("venue") or (p.get("journal") or {}).get("name")
    vtype = (p.get("publicationVenue") or {}).get("type")
    venue_type = {"journal": "journal", "conference": "conference", "repository": "repository"}.get(vtype or "")
    types = p.get("publicationTypes") or []
    hint = "preprint" if (venue or "").lower().startswith("arxiv") and not doi else None
    status, ev = decide_status(
        doi=doi, work_type=("preprint" if hint else ("article" if "JournalArticle" in types or "Conference" in types else None)),
        venue_type=venue_type or ("journal" if "JournalArticle" in types else "conference" if "Conference" in types else None),
        version=None, source_hint=hint,
    )
    pdf = (p.get("openAccessPdf") or {}).get("url")
    fields = p.get("fieldsOfStudy") or []
    return Work(
        title=(p.get("title") or "").strip(), authors=[a.get("name") for a in p.get("authors") or [] if a.get("name")],
        year=p.get("year"), date=p.get("publicationDate"), venue=venue, venue_type=venue_type, doi=doi, arxiv_id=arxiv,
        pmid=ext.get("PubMed"), abstract=clean_text(p.get("abstract")), url=p.get("url"), pdf_url=pdf,
        status=status, status_evidence=ev, citations=p.get("citationCount"),
        open_access=p.get("isOpenAccess"), field_name=fields[0] if fields else None, work_type=(types[0] if types else None),
        sources=[{"name": "Semantic Scholar", "id": p.get("paperId"), "url": p.get("url")}],
    )


async def fetch_s2(client: httpx.AsyncClient, query: str, *, limit: int = 20, year_from: int | None = None) -> list[Work]:
    params: dict = {"query": query, "limit": min(limit, 50), "fields": S2_FIELDS}
    if year_from:
        params["year"] = f"{year_from}-"
    try:
        r = await client.get(f"{S2}/paper/search", params=params)
    except httpx.HTTPError as exc:
        raise SourceError("Semantic Scholar 无法连接") from exc
    if r.status_code == 429:
        raise SourceError("Semantic Scholar 限流，已跳过")
    if r.status_code != 200:
        raise SourceError(f"Semantic Scholar 返回 HTTP {r.status_code}")
    return [w for w in (parse_s2(x) for x in r.json().get("data", []) or []) if w.title]


# ======================= arXiv =======================
_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_ACCEPTED_RE = re.compile(r"\b(accepted|to appear|will appear|camera[- ]ready)\b.{0,40}\b(in|at|to|by|for)\b", re.I)


def parse_arxiv_feed(xml_text: str) -> list[Work]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("arXiv 返回的数据无法解析") from exc
    out: list[Work] = []
    for e in root.findall("a:entry", _NS):
        title = re.sub(r"\s+", " ", e.findtext("a:title", "", _NS) or "").strip()
        if not title or title.lower() == "error":
            continue
        aid = norm_arxiv(e.findtext("a:id", "", _NS))
        pub = e.findtext("a:published", "", _NS) or ""
        doi = norm_doi(e.findtext("x:doi", "", _NS))
        jref = (e.findtext("x:journal_ref", "", _NS) or "").strip()
        comment = (e.findtext("x:comment", "", _NS) or "").strip()
        cat = (e.find("x:primary_category", _NS).get("term") if e.find("x:primary_category", _NS) is not None else None)
        status, ev = "preprint", "arXiv 预印本"
        versions: list[dict] = []
        if jref or doi:
            versions.append({"kind": "published", "doi": doi, "url": f"https://doi.org/{doi}" if doi else None,
                             "venue": jref or None, "arxiv_id": None})
            status, ev = "preprint", "arXiv 预印本；条目标注了期刊引用或 DOI，可能已正式发表"
        if _ACCEPTED_RE.search(comment):
            status, ev = "accepted", f"arXiv 备注写明已接收：{comment[:80]}"
        pdf = next((l.get("href") for l in e.findall("a:link", _NS) if l.get("title") == "pdf"), None)
        out.append(Work(
            title=title, authors=[a.findtext("a:name", "", _NS) for a in e.findall("a:author", _NS)],
            year=int(pub[:4]) if pub[:4].isdigit() else None, date=pub[:10] or None, venue="arXiv 预印本",
            venue_type="repository", doi=doi, arxiv_id=aid,
            abstract=clean_text(e.findtext("a:summary", "", _NS)), url=f"https://arxiv.org/abs/{aid}" if aid else None,
            pdf_url=pdf or (f"https://arxiv.org/pdf/{aid}" if aid else None), status=status, status_evidence=ev,
            peer_reviewed=False if status == "preprint" else None, field_name=cat, work_type="preprint",
            open_access=True, sources=[{"name": "arXiv", "id": aid, "url": f"https://arxiv.org/abs/{aid}" if aid else None}],
            versions=versions,
        ))
    return out


def arxiv_query(terms: list[str]) -> str:
    parts = []
    for t in terms[:6]:
        t = re.sub(r'["\\]', " ", t).strip()
        if not t:
            continue
        parts.append(f'all:"{t}"' if " " in t else f"all:{t}")
    return " AND ".join(parts) if parts else "all:*"


async def fetch_arxiv(client: httpx.AsyncClient, terms: list[str], *, limit: int = 20, sort: str = "relevance") -> list[Work]:
    params = {"search_query": arxiv_query(terms), "max_results": limit,
              "sortBy": "submittedDate" if sort == "date" else "relevance", "sortOrder": "descending"}
    try:
        r = await client.get(ARXIV, params=params)
    except httpx.HTTPError as exc:
        raise SourceError("arXiv 无法连接") from exc
    if r.status_code != 200:
        raise SourceError(f"arXiv 返回 HTTP {r.status_code}")
    return parse_arxiv_feed(r.text)


# ======================= Europe PMC =======================
def parse_epmc(d: dict) -> Work:
    src = d.get("source")
    doi = norm_doi(d.get("doi"))
    jt = ((d.get("journalInfo") or {}).get("journal") or {}).get("title") or d.get("journalTitle")
    preprint = src == "PPR" or "preprint" in (d.get("pubType") or "").lower()
    status, ev = decide_status(
        doi=doi, work_type="preprint" if preprint else "article", venue_type=None if preprint else ("journal" if jt else None),
        version=None, source_hint="preprint" if preprint else None,
    )
    pid = d.get("id")
    return Work(
        title=clean_text(d.get("title")) or "", authors=[a.strip() for a in (d.get("authorString") or "").rstrip(".").split(",") if a.strip()],
        year=int(d["pubYear"]) if str(d.get("pubYear", "")).isdigit() else None, date=d.get("firstPublicationDate"),
        venue=jt or ("预印本" if preprint else None), venue_type="repository" if preprint else ("journal" if jt else None),
        doi=doi, pmid=d.get("pmid"), abstract=clean_text(d.get("abstractText")),
        url=(f"https://europepmc.org/article/{src}/{pid}" if src and pid else None), status=status, status_evidence=ev,
        peer_reviewed=(None if preprint else bool(jt)), citations=d.get("citedByCount"),
        open_access=(d.get("isOpenAccess") == "Y"), work_type="preprint" if preprint else "article",
        field_name="Medicine / Life sciences", sources=[{"name": "Europe PMC", "id": f"{src}:{pid}", "url": None}],
    )


async def fetch_epmc(client: httpx.AsyncClient, query: str, *, limit: int = 20, sort: str | None = None,
                     year_from: int | None = None) -> list[Work]:
    q = query
    if year_from:
        q += f" AND (PUB_YEAR:[{year_from} TO 3000])"
    params = {"query": q, "format": "json", "resultType": "core", "pageSize": min(limit, 100)}
    if sort == "date":
        params["sort"] = "P_PDATE_D desc"
    elif sort == "cited":
        params["sort"] = "CITED desc"
    try:
        r = await client.get(f"{EPMC}/search", params=params)
    except httpx.HTTPError as exc:
        raise SourceError("Europe PMC 无法连接") from exc
    if r.status_code != 200:
        raise SourceError(f"Europe PMC 返回 HTTP {r.status_code}")
    return [w for w in (parse_epmc(x) for x in (r.json().get("resultList") or {}).get("result", [])) if w.title]


