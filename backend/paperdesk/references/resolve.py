"""联网查参考文献的题录与摘要：先用 DOI/arXiv 精确查，其次用“标题 + 第一作者 + 年份”在 OpenAlex / Crossref 里做书目查询，
Semantic Scholar 只作补充摘要来源。

只发出参考文献自己的题录信息（标题、作者、DOI），不发论文正文。匹配可信度要诚实：并排比较“原文参考文献”和“查到的题录”，
相似度不够就报 low，查不到就如实说查不到，绝不编造。
"""
from __future__ import annotations

import difflib
import re
from urllib.parse import quote

import httpx

from ..context import AppContext
from ..util import utcnow
from . import store
from .parse import first_author_surname

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
S2 = "https://api.semanticscholar.org/graph/v1"
OA_SELECT = "id,doi,title,display_name,publication_year,authorships,primary_location,abstract_inverted_index,cited_by_count,best_oa_location,open_access"


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def abstract_from_inverted(inv: dict | None) -> str:
    """OpenAlex 的 abstract_inverted_index（词 → 位置列表）还原成文本。"""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)).strip()


def _strip_jats(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _first_author_family(authors: list[str]) -> str:
    for a in authors or []:
        a = (a or "").strip()
        if a:
            return re.sub(r"[^\w\-]", "", (a.split(",")[0] if "," in a else a.split()[-1])).lower()
    return ""


def score_match(ref: dict, cand: dict) -> dict:
    """比较“原文参考文献”和“查到的题录”：返回 {score, level, reason}。"""
    if ref.get("doi") and cand.get("doi") and ref["doi"].lower() == cand["doi"].lower():
        return {"score": 1.0, "level": "high", "reason": "DOI 完全一致"}
    ctitle = cand.get("title") or ""
    if ref.get("title"):
        sim = title_similarity(ref["title"], ctitle)
        why = f"标题相似度 {sim:.2f}"
    else:  # 原文没解析出标题：看查到的标题是不是整段出现在原文里
        nt, raw = norm_title(ctitle), norm_title(ref.get("raw") or "")
        sim = 1.0 if nt and nt in raw else (difflib.SequenceMatcher(None, nt, raw).find_longest_match(0, len(nt), 0, len(raw)).size / len(nt) if nt else 0.0)
        why = f"查到的标题在原文中的覆盖率 {sim:.2f}"
    bonus = 0.0
    want = first_author_surname(ref)
    got = _first_author_family(cand.get("authors") or [])
    if want and got:
        if want == got:
            bonus += 0.05
            why += "，第一作者一致"
        else:
            sim -= 0.15
            why += "，第一作者不一致"
    if ref.get("year") and cand.get("year"):
        if abs(int(ref["year"]) - int(cand["year"])) <= 1:
            bonus += 0.03
        else:
            sim -= 0.1
            why += f"，年份不同：原文 {ref['year']}，题录 {cand['year']}"
    score = max(0.0, min(1.0, sim + bonus))
    level = "high" if score >= 0.9 else "medium" if score >= 0.75 else "low"
    return {"score": round(score, 3), "level": level, "reason": why}


RESOLVE_VERSION = 2  # 旧版本的缓存（没有摘要一致性检查）会被忽略并重新查询

_STOP = set(
    "a an and are as at be been but by can for from has have in into is it its of on or our over than that the their then these this those "
    "through to toward towards under up upon via was we were what when where which while who why will with within without not no do does "
    "how also both each other such only very more most".split()
)
# 太泛、几乎任何摘要里都会出现的词：不能用来证明“摘要说的是这篇”
_GENERIC = set(
    "game games human humans knowledge learning learn network networks model models method methods approach approaches analysis study studies "
    "using use based system systems data new problem problems algorithm algorithms deep neural theory design complex large scale general efficient "
    "fast simple review survey introduction application applications framework performance evaluation case between towards improved towards "
    "research paper work results result evidence role effects effect structure structures dynamics process processes information".split()
)


def _stem(w: str) -> str:
    for suf in ("ization", "ations", "ation", "ings", "ing", "ness", "ments", "ment", "ies", "ied", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", (text or "").lower())


def _same_word(t: str, a: str) -> bool:
    """标题里的词是否出现在摘要里：短词必须整词相同（避免 “go” 命中 “gomoku”），长词词干前 5 位相同即可。"""
    if t == a:
        return True
    ts, as_ = _stem(t), _stem(a)
    if ts == as_:
        return True
    return len(ts) >= 5 and len(as_) >= 5 and ts[:5] == as_[:5]


def abstract_consistency(title: str, abstract: str) -> dict:
    """摘要和标题是否像是同一篇：标题实词（去停用词、词干化）有多少出现在摘要里，并单独看“不泛的核心词”。

    返回 {level: ok|weak|mismatch|unknown, coverage, rare_found, rare_total, reason}。
    mismatch：加权覆盖率 < 0.30；或核心词 ≥ 2 个而一个都没出现；或只有 1 个核心词没出现且覆盖率 < 0.5。
    只是词汇层面的检查：中文标题用字符二元组；标题和摘要语言不同（无法比较）返回 unknown，按可信处理。
    """
    title, abstract = (title or "").strip(), (abstract or "").strip()
    if not title or not abstract:
        return {"level": "unknown", "coverage": None, "rare_found": 0, "rare_total": 0, "reason": "没有可比较的内容"}
    cjk_t = re.findall(r"[一-鿿]", title)
    if len(cjk_t) >= 4:
        cjk_a = re.findall(r"[一-鿿]", abstract)
        if len(cjk_a) < 8:
            return {"level": "unknown", "coverage": None, "rare_found": 0, "rare_total": 0, "reason": "标题是中文、摘要不是，无法比较"}
        bigrams = {"".join(cjk_t[i:i + 2]) for i in range(len(cjk_t) - 1)}
        joined = "".join(cjk_a)
        cov = sum(1 for b in bigrams if b in joined) / max(1, len(bigrams))
        level = "mismatch" if cov < 0.15 else "weak" if cov < 0.35 else "ok"
        return {"level": level, "coverage": round(cov, 3), "rare_found": 0, "rare_total": 0, "reason": f"标题字符二元组在摘要中的覆盖率 {cov:.2f}"}
    words = [w for w in _tokens(title) if w not in _STOP and len(w) >= 2]
    if len(words) < 2:
        return {"level": "unknown", "coverage": None, "rare_found": 0, "rare_total": 0, "reason": "标题太短，无法比较"}
    atoks = _tokens(abstract)
    weight = {w: max(1, len(w) - 3) for w in words}
    found = {w for w in words if any(_same_word(w, a) for a in atoks)}
    cov = sum(weight[w] for w in found) / sum(weight.values())
    rare = [w for w in words if w not in _GENERIC]
    rare_found = sum(1 for w in rare if w in found)
    if cov < 0.30 or (len(rare) >= 2 and rare_found == 0) or (len(rare) == 1 and rare_found == 0 and cov < 0.5):
        level = "mismatch"
    elif cov < 0.5:
        level = "weak"
    else:
        level = "ok"
    missing = [w for w in rare if w not in found][:4]
    return {"level": level, "coverage": round(cov, 3), "rare_found": rare_found, "rare_total": len(rare),
            "reason": f"标题实词在摘要中的加权覆盖率 {cov:.2f}；核心词 {rare_found}/{len(rare)} 出现" + (f"，缺 {', '.join(missing)}" if missing else "")}


SOURCE_NAME = {"openalex": "OpenAlex", "crossref": "Crossref", "semantic_scholar": "Semantic Scholar"}


def _from_openalex(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
    oa = w.get("best_oa_location") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
    return {
        "title": w.get("title") or w.get("display_name") or "",
        "authors": [(a.get("author") or {}).get("display_name", "") for a in (w.get("authorships") or [])][:12],
        "year": w.get("publication_year"), "venue": src.get("display_name") or "", "doi": doi,
        "url": (f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or "")),
        "open_access_url": (oa.get("pdf_url") or oa.get("landing_page_url") or "") if isinstance(oa, dict) else "",
        "abstract": abstract_from_inverted(w.get("abstract_inverted_index")), "abstract_source": "openalex",
        "citations": w.get("cited_by_count"), "source": "openalex",
    }


def _from_crossref(m: dict) -> dict:
    doi = (m.get("DOI") or "").lower()
    issued = ((m.get("issued") or {}).get("date-parts") or [[None]])[0]
    authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (m.get("author") or [])][:12]
    return {
        "title": ((m.get("title") or [""])[0]) or "", "authors": authors, "year": issued[0] if issued else None,
        "venue": ((m.get("container-title") or [""])[0]) or "", "doi": doi, "url": f"https://doi.org/{doi}" if doi else "",
        "open_access_url": "", "abstract": _strip_jats(m.get("abstract") or ""), "abstract_source": "crossref" if m.get("abstract") else "",
        "citations": m.get("is-referenced-by-count"), "source": "crossref",
    }


def _from_s2(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    doi = (ext.get("DOI") or "").lower()
    return {
        "title": p.get("title") or "", "authors": [a.get("name", "") for a in (p.get("authors") or [])][:12], "year": p.get("year"),
        "venue": p.get("venue") or "", "doi": doi, "url": f"https://doi.org/{doi}" if doi else (p.get("url") or ""),
        "open_access_url": ((p.get("openAccessPdf") or {}).get("url") or ""), "abstract": p.get("abstract") or "",
        "abstract_source": "semantic_scholar" if p.get("abstract") else "", "citations": p.get("citationCount"), "source": "semantic_scholar",
    }


async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    try:
        r = await client.get(url, params=params)
        if r.status_code == 200:
            return r.json()
    except (httpx.HTTPError, ValueError):
        pass
    return None


async def _candidates(client: httpx.AsyncClient, ref: dict) -> list[dict]:
    out: list[dict] = []
    doi = ref.get("doi") or ""
    if not doi and ref.get("arxiv"):
        doi = f"10.48550/arxiv.{ref['arxiv']}"
    if doi:  # 精确：DOI（或 arXiv 的 DataCite DOI）
        w = await _get_json(client, f"{OPENALEX}/works/https://doi.org/{quote(doi, safe='/')}", {"select": OA_SELECT})
        if w and w.get("id"):
            out.append(_from_openalex(w))
        if not out:
            c = await _get_json(client, f"{CROSSREF}/works/{quote(doi, safe='/')}")
            if c and c.get("message"):
                out.append(_from_crossref(c["message"]))
    query = ref.get("title") or ""
    if not out and (query or ref.get("raw")):  # 书目查询：标题（或整条原文）
        q = query or (ref.get("raw") or "")[:260]
        params = {"search": q, "per-page": 5, "select": OA_SELECT}
        if ref.get("year"):
            params["filter"] = f"publication_year:{int(ref['year']) - 1}-{int(ref['year']) + 1}"
        d = await _get_json(client, f"{OPENALEX}/works", params)
        out.extend(_from_openalex(w) for w in ((d or {}).get("results") or []))
        c = await _get_json(client, f"{CROSSREF}/works", {"query.bibliographic": q, "rows": 3})
        out.extend(_from_crossref(m) for m in (((c or {}).get("message") or {}).get("items") or []))
    return out


async def _other_sources(client: httpx.AsyncClient, best: dict) -> list[tuple[str, str, str]]:
    """OpenAlex 的摘要不可信或缺失时，再向别的来源要同一篇的摘要：Semantic Scholar（DOI，其次标题）、Crossref（DOI）。
    返回 [(来源, 摘要, 该来源自己的标题)]。"""
    out: list[tuple[str, str, str]] = []
    doi = best.get("doi") or ""
    fields = "title,abstract,year,citationCount,externalIds,openAccessPdf,venue,authors"
    s2 = await _get_json(client, f"{S2}/paper/DOI:{quote(doi, safe='/')}", {"fields": fields}) if doi else None
    if not (s2 and s2.get("abstract")) and best.get("title"):
        d = await _get_json(client, f"{S2}/paper/search", {"query": best["title"], "limit": 1, "fields": fields})
        hit = ((d or {}).get("data") or [None])[0]
        if hit and hit.get("abstract") and title_similarity(hit.get("title", ""), best["title"]) > 0.9:
            s2 = hit
    if s2 and s2.get("abstract") and title_similarity(s2.get("title", ""), best["title"]) > 0.9:
        out.append(("semantic_scholar", s2["abstract"], s2.get("title", "")))
    if doi and best.get("source") != "crossref":
        c = await _get_json(client, f"{CROSSREF}/works/{quote(doi, safe='/')}")
        msg = (c or {}).get("message") or {}
        if msg.get("abstract") and title_similarity(((msg.get("title") or [""])[0]) or "", best["title"]) > 0.9:
            out.append(("crossref", _strip_jats(msg["abstract"]), ((msg.get("title") or [""])[0]) or ""))
    return out


async def _choose_abstract(client: httpx.AsyncClient, best: dict, scored: list, result: dict) -> None:
    """挑一份可信的摘要写进 result：每份摘要都和标题做一致性检查，不相符的不用（OpenAlex 偶尔把别的论文的摘要挂在这条记录上）。

    顺序：匹配到的记录自己的摘要 → 同一篇的其它候选 → Semantic Scholar / Crossref。都不可信就不给摘要并如实说明。
    """
    title = best.get("title") or ""
    cands: list[tuple[str, str]] = []
    if best.get("abstract"):
        cands.append((best.get("abstract_source") or best.get("source") or "", best["abstract"]))
    for _m2, c2 in scored[1:]:
        if c2.get("abstract") and title_similarity(c2["title"], title) > 0.9:
            cands.append((c2.get("abstract_source") or c2.get("source") or "", c2["abstract"]))
    ignored: list[tuple[str, dict]] = []
    chosen: tuple[str, str, dict] | None = None
    weak: tuple[str, str, dict] | None = None

    def consider(src: str, text: str) -> bool:
        nonlocal chosen, weak
        chk = abstract_consistency(title, text)
        if chk["level"] == "mismatch":
            ignored.append((src, chk))
        elif chk["level"] == "weak":
            weak = weak or (src, text, chk)
        else:
            chosen = (src, text, chk)
            return True
        return False

    for src, text in cands:
        if consider(src, text):
            break
    if chosen is None and (not cands or ignored or weak):  # 没有、或不可信/可疑：向别的来源再要一份
        for src, text, _t in await _other_sources(client, best):
            if any(src == s for s, _ in cands):
                continue
            if consider(src, text):
                break
    notes: list[str] = []
    pick = chosen or weak
    if pick:
        src, text, chk = pick
        result["abstract"], result["abstract_source"], result["abstract_check"] = text, src, chk
        for isrc, _c in ignored:
            notes.append(f"{SOURCE_NAME.get(isrc, isrc)} 的摘要与这篇的标题不相符，已忽略，改用 {SOURCE_NAME.get(src, src)} 的摘要。")
        if pick is weak and chosen is None:
            notes.append("摘要与标题的词汇重合度偏低，可能不完全对应这篇，请对照原文确认。")
    else:
        result["abstract"] = None
        result["abstract_source"] = ""
        if ignored:
            result["abstract_mismatch"] = True
            result["abstract_check"] = ignored[0][1]
            result["abstract_reason"] = "检索到的摘要与这篇的标题不相符，已忽略；请去原文查看。"
            notes.append("、".join(SOURCE_NAME.get(s, s) for s, _ in ignored) + " 返回的摘要与标题不相符，已忽略。")
        else:
            result["abstract_reason"] = "查到了题录，但 OpenAlex、Crossref、Semantic Scholar 都没有这篇的摘要。"
    result["abstract_notes"] = notes


async def resolve(app: AppContext, paper_id: str, index: int, force: bool = False) -> dict:
    """查参考文献的题录与摘要。结果缓存；force=True 重新联网。"""
    ref = store.get_reference(app, paper_id, index)
    if not force:
        hit = store.cached_resolution(app, paper_id, index)
        if hit is not None and hit.get("v") == RESOLVE_VERSION:  # 旧版本的缓存没有做过摘要一致性检查，重新查
            return {**hit, "cached": True, "in_library": _in_library(app, hit)}
    result: dict = {"index": index, "found": False, "reason": "", "query": {"title": ref.get("title"), "doi": ref.get("doi"), "arxiv": ref.get("arxiv")},
                    "network": "OpenAlex / Crossref / Semantic Scholar", "checked_at": utcnow()}
    if not (ref.get("title") or ref.get("doi") or ref.get("arxiv") or ref.get("raw")):
        result["reason"] = "这条参考文献没有可用于查询的题录信息。"
    else:
        async with app.async_http(25.0) as client:
            cands = await _candidates(client, ref)
            scored = sorted(((score_match(ref, c), c) for c in cands if c.get("title")), key=lambda t: -t[0]["score"])
            if not scored:
                result["reason"] = "在 OpenAlex 和 Crossref 里没有查到这篇参考文献，可能是书籍、网页，或解析出的标题不准。"
            else:
                match, best = scored[0]
                result.update(best)
                result["match"] = match
                result["found"] = True
                await _choose_abstract(client, best, scored, result)
    result["v"] = RESOLVE_VERSION
    store.save_resolution(app, paper_id, index, result)
    return {**result, "cached": False, "in_library": _in_library(app, result)}


def _in_library(app: AppContext, res: dict) -> dict | None:
    """这篇参考文献是否已经在文库里（按 DOI 或标题）。"""
    doi, title = (res.get("doi") or "").lower(), norm_title(res.get("title") or "")
    with app.db.session() as conn:
        if doi:
            r = conn.execute("SELECT id, title FROM papers WHERE lower(doi) = ? AND parent_id IS NULL LIMIT 1", (doi,)).fetchone()
            if r:
                return {"paper_id": r["id"], "title": r["title"]}
        if title:
            for r in conn.execute("SELECT id, title FROM papers WHERE parent_id IS NULL"):
                if norm_title(r["title"]) == title:
                    return {"paper_id": r["id"], "title": r["title"]}
    return None
