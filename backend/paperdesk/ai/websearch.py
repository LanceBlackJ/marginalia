"""提问时的联网补充：论文里没有的内容，去公开网站找资料（维基百科、OpenAlex 学术库），不需要任何 Key。

- 只在用户开着“联网”时才发请求；发出去的只有问题（和选中文字的开头），记入隐私日志；
- 任何一个来源失败都不影响回答，只是少一些资料；
- 查到的每条资料带编号 [W1]、[W2]…，回答里引用它们时前端能点开原网页，和论文原文 [P序号] 严格分开。
"""
from __future__ import annotations

import asyncio
import re

import httpx

from ..context import AppContext

WIKI_CHARS = 700
MAX_ITEMS = 6
# 维基媒体的接口要求 User-Agent 带上工具名和联系网址，否则一律 403
UA = {"User-Agent": "Marginalia/0.1 (https://github.com/paperdesk/paperdesk; personal literature reading tool)"}
_ASK_WORDS_ZH = re.compile(r"请问|什么是|是什么|是啥|为什么|为啥|怎么样|怎么|如何|有什么|有哪些|能不能|可不可以|请你|解释一下|解释|讲讲|说说|介绍一下|介绍|一下|吗|呢|啊|吧|的区别|区别")
_ASK_WORDS_EN = re.compile(r"^\s*(?:what\s+(?:is|are)|how\s+(?:does|do|to)|why\s+(?:is|are|does|do)|explain|tell me about)\s+", re.I)


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))


def _query(question: str, selection: str) -> str:
    q = re.sub(r"\s+", " ", question or "").strip()
    s = re.sub(r"\s+", " ", selection or "").strip()
    # 问题很短（“这个是为什么呢”）时，选中的文字才是真正的主题
    if len(q) < 12 and s:
        q = f"{s[:80]} {q}".strip()
    cleaned = _ASK_WORDS_EN.sub("", _ASK_WORDS_ZH.sub(" ", q))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?？。.，,")
    return (cleaned if len(cleaned) >= 2 else q)[:140]


async def _wikipedia(client: httpx.AsyncClient, q: str, lang: str) -> list[dict]:
    r = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": q, "gsrlimit": 3,
            "prop": "extracts|info", "exintro": 1, "explaintext": 1, "exchars": WIKI_CHARS, "inprop": "url", "redirects": 1,
        },
        headers=UA,
    )
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 99)):
        text = re.sub(r"\s+", " ", p.get("extract") or "").strip()
        if text and p.get("fullurl"):
            out.append({"title": p.get("title", ""), "url": p["fullurl"], "source": "维基百科", "snippet": text})
    return out


async def _openalex(client: httpx.AsyncClient, q: str) -> list[dict]:
    r = await client.get(
        "https://api.openalex.org/works",
        params={"search": q, "per-page": 4, "select": "id,title,publication_year,doi,primary_location,abstract_inverted_index,cited_by_count"},
        headers=UA,
    )
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        title = (w.get("title") or "").strip()
        idx = w.get("abstract_inverted_index") or {}
        words: list[tuple[int, str]] = [(pos, word) for word, poss in idx.items() for pos in poss]
        abstract = " ".join(word for _p, word in sorted(words))[:500]
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        url = w.get("doi") or w.get("id")
        if title and url:
            meta = " · ".join(x for x in (venue, str(w.get("publication_year") or "")) if x)
            out.append({"title": title, "url": url, "source": "OpenAlex", "snippet": f"{meta}。{abstract}".strip("。 ")})
    return out


async def search(app: AppContext, question: str, selection_text: str = "") -> list[dict]:
    """返回 [{n, title, url, source, snippet}]，编号从 1 开始；一个都没查到就返回空列表。"""
    q = _query(question, selection_text)
    if not q:
        return []
    lang = "zh" if _has_cjk(q) else "en"
    async with app.async_http(8.0) as client:
        got = await asyncio.gather(_wikipedia(client, q, lang), _openalex(client, q), return_exceptions=True)
    items: list[dict] = []
    for g in got:
        if isinstance(g, list):
            items.extend(g)
    items = items[:MAX_ITEMS]
    for i, it in enumerate(items, 1):
        it["n"] = i
    return items


def render_for_prompt(items: list[dict]) -> str:
    return "\n".join(f"[W{it['n']}] （{it['source']}）{it['title']}：{it['snippet'][:600]}\n网址：{it['url']}" for it in items)


WEB_CITE_RE = re.compile(r"\[W(\d+)\]")


def cited_web(text: str, items: list[dict]) -> list[dict]:
    """回答里真正引用到的网络资料（编号必须存在，编造的编号丢掉）。"""
    by_n = {it["n"]: it for it in items}
    seen: list[int] = []
    for m in WEB_CITE_RE.finditer(text):
        n = int(m.group(1))
        if n in by_n and n not in seen:
            seen.append(n)
    return [{"n": n, "title": by_n[n]["title"], "url": by_n[n]["url"], "source": by_n[n]["source"]} for n in seen]
