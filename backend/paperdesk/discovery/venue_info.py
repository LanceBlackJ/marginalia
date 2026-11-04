"""来源（期刊/会议）介绍：名单内来源有一句人工写的简介，所有来源的数字都来自 OpenAlex 的真实统计，取不到就不显示。

不编造“影响因子”“分区”“CCF 等级”：这些数字有出处和年份，这里拿不到可靠的来源，所以只给 OpenAlex 能确认的
作品数、总被引、近两年平均被引（和影响因子口径相近，但不是影响因子）、h 指数、出版方。
"""
from __future__ import annotations

import httpx

from . import sources as S
from .venues import BY_ID, match_venue, norm_loose, norm_venue

OPENALEX = S.OPENALEX

# 人工写的简介，只写公认的事实：做什么、谁主办/出版、在领域里的位置。没有写的来源只显示分类和统计数字。
INTRO: dict[str, str] = {
    "nature": "英国 Nature 出版集团的综合性周刊，发表自然科学各领域的重大成果，是影响力最广的学术期刊之一。",
    "science": "美国科学促进会（AAAS）出版的综合性周刊，与 Nature 并列为最具影响力的综合性期刊。",
    "cell": "Cell Press 出版的生命科学旗舰期刊，主要发表生物学、分子与细胞层面的重要研究。",
    "pnas": "美国国家科学院院刊，综合性期刊，覆盖自然科学和社会科学各领域，论文量大、整体水平较高。",
    "nat-mach-intell": "Nature 系列中专注机器学习与智能系统的期刊，兼顾方法和应用。",
    "nat-methods": "Nature 系列，专注生命科学研究方法与技术的开发。",
    "nat-med": "Nature 系列的医学期刊，发表临床和转化医学的重要研究。",
    "lancet": "英国医学期刊，临床医学的顶级刊物之一，尤其重视临床试验和公共卫生。",
    "nejm": "美国医学会刊物《新英格兰医学杂志》，临床医学影响力最大的期刊之一。",
    "jama": "美国医学会的旗舰期刊，临床医学顶级刊物之一。",
    "bmj": "英国医学会出版的综合医学期刊，临床与公共卫生并重。",
    "prl": "美国物理学会的物理学快报期刊，发表各物理分支中重要的短篇成果。",
    "jacs": "美国化学会旗舰期刊，化学领域最具影响力的期刊之一。",
    "aer": "美国经济学会的旗舰期刊，经济学“五大”期刊之一。",
    "qje": "哈佛大学出版的经济学期刊，经济学“五大”期刊之一。",
    "econometrica": "计量经济学会的期刊，经济学“五大”期刊之一，偏重理论和计量方法。",
    "jpe": "芝加哥大学出版的经济学期刊，经济学“五大”期刊之一。",
    "annals-math": "数学领域最负盛名的期刊之一，由普林斯顿大学和高等研究院出版，只发表很重要的结果。",
    "jmlr": "机器学习领域的主要期刊，开放获取，论文偏理论与方法。",
    "tpami": "IEEE 出版的模式分析与机器智能汇刊，计算机视觉与机器学习的顶级期刊。",
    "ijcv": "Springer 出版的计算机视觉国际期刊，视觉领域的主要期刊之一。",
    "tkde": "IEEE 的知识与数据工程汇刊，数据挖掘与数据库方向的主要期刊。",
    "jacm": "ACM 的旗舰期刊，主要发表计算机科学理论与基础方向的长篇论文。",
    "aij": "Elsevier 出版的《Artificial Intelligence》，人工智能领域历史悠久的主要期刊。",
    "pvldb": "VLDB 会议的论文集期刊，是数据库领域的顶级发表渠道之一。",
    "tocl": "ACL 协会的期刊，发表自然语言处理的高质量论文，可与会议论文互补。",
    "neurips": "神经信息处理系统大会，机器学习与人工智能领域规模最大、最有影响力的会议之一。",
    "icml": "国际机器学习大会，机器学习领域最主要的会议之一。",
    "iclr": "国际学习表征大会，以深度学习为核心，近年成为机器学习的顶级会议之一。",
    "aaai": "美国人工智能协会的年会，人工智能领域综合性的主要会议。",
    "ijcai": "国际人工智能联合会议，人工智能领域历史最久的综合性会议之一。",
    "colt": "学习理论会议，聚焦机器学习的数学理论。",
    "uai": "人工智能中的不确定性会议，偏概率模型与推断。",
    "cvpr": "IEEE/CVF 计算机视觉与模式识别大会，计算机视觉领域规模最大、影响力最高的会议。",
    "iccv": "国际计算机视觉大会，两年一届，计算机视觉的三大顶会之一。",
    "eccv": "欧洲计算机视觉大会，两年一届，计算机视觉的三大顶会之一。",
    "acl": "计算语言学协会年会，自然语言处理领域最主要的会议。",
    "emnlp": "自然语言处理经验方法会议，自然语言处理的顶级会议之一。",
    "naacl": "ACL 北美分会的会议，自然语言处理的主要会议之一。",
    "kdd": "ACM 知识发现与数据挖掘大会，数据挖掘领域最主要的会议。",
    "www": "万维网大会，Web 技术、网络数据与应用方向的主要会议。",
    "sigir": "ACM 信息检索大会，信息检索领域的顶级会议。",
    "sigmod": "ACM 数据管理国际会议，数据库领域的两大顶会之一。",
    "icde": "IEEE 国际数据工程会议，数据库领域的主要会议之一。",
    "osdi": "USENIX 操作系统设计与实现研讨会，系统领域的顶级会议之一。",
    "sosp": "ACM 操作系统原理研讨会，系统领域历史最久的顶级会议之一。",
    "nsdi": "USENIX 网络系统设计与实现研讨会，网络与分布式系统的顶级会议。",
    "sigcomm": "ACM 数据通信专业组的年会，计算机网络领域的顶级会议。",
    "usenix-sec": "USENIX 安全研讨会，计算机安全四大顶会之一。",
    "ccs": "ACM 计算机与通信安全会议，计算机安全四大顶会之一。",
    "sp": "IEEE 安全与隐私研讨会，计算机安全四大顶会之一。",
    "isca": "国际计算机体系结构研讨会，体系结构领域的顶级会议。",
    "micro": "IEEE/ACM 微体系结构国际研讨会，体系结构领域的顶级会议之一。",
    "stoc": "ACM 计算理论研讨会，理论计算机科学的两大顶会之一。",
    "focs": "IEEE 计算机科学基础研讨会，理论计算机科学的两大顶会之一。",
    "soda": "ACM-SIAM 离散算法研讨会，算法方向的顶级会议。",
    "icse": "国际软件工程大会，软件工程领域最主要的会议。",
    "pldi": "ACM 程序设计语言设计与实现大会，编程语言领域的顶级会议。",
    "popl": "ACM 编程语言原理研讨会，编程语言理论方向的顶级会议。",
    "siggraph": "ACM 计算机图形学年会，图形学领域最具影响力的会议，论文发表在《ACM Transactions on Graphics》。",
    "chi": "ACM 人机交互大会，人机交互领域规模最大的顶级会议。",
    "uist": "ACM 用户界面软件与技术研讨会，人机交互中偏界面技术的顶级会议。",
}


def curated(name: str | None, venue_type: str | None = None) -> dict | None:
    v = match_venue(name, venue_type)
    if v is None:
        return None
    return {"id": v.id, "name": v.name, "short": v.short, "kind": v.kind, "category": v.category, "level": v.level, "label": v.label, "intro": INTRO.get(v.id) or v.intro}


def _pick(items: list[dict], name: str) -> dict | None:
    """名称归一化后与来源完全相等的才算：先比正式名称，再比 OpenAlex 记录的缩写和别名（论文里常写 “IEEE Trans. Netw. Sci. Eng.”、
    “TNSE” 这样的缩写）；多个同名取作品数最多的。缩写和别名也要整体相等，不做部分匹配。"""
    want = norm_venue(name)
    loose = norm_loose(name)
    hit = [x for x in items if norm_venue(x.get("display_name")) == want]
    if not hit:
        hit = [x for x in items if want in {norm_venue(x.get("abbreviated_title")), *(norm_venue(a) for a in x.get("alternate_titles") or [])}]
    if not hit:
        hit = [x for x in items if loose and loose in {norm_loose(x.get("display_name")), norm_loose(x.get("abbreviated_title"))}]
    return max(hit, key=lambda x: x.get("works_count") or 0) if hit else None


_SELECT = ("id,display_name,abbreviated_title,alternate_titles,type,works_count,cited_by_count,summary_stats,host_organization_name,"
           "country_code,homepage_url,is_oa,is_in_doaj,issn_l")


def _stats_of(src: dict) -> dict:
    ss = src.get("summary_stats") or {}
    return {
        "name": src.get("display_name"), "type": src.get("type"), "publisher": src.get("host_organization_name"),
        "country": src.get("country_code"), "works_count": src.get("works_count"), "cited_by_count": src.get("cited_by_count"),
        "mean_citedness_2y": ss.get("2yr_mean_citedness"), "h_index": ss.get("h_index"),
        "is_oa": src.get("is_oa"), "in_doaj": src.get("is_in_doaj"), "homepage": src.get("homepage_url"), "issn": src.get("issn_l"),
        "openalex_id": src.get("id"), "abbreviation": src.get("abbreviated_title"),
    }


_TYPE_ZH = {"journal": "期刊", "conference": "会议", "repository": "预印本/文库平台", "book series": "丛书", "ebook platform": "电子书平台"}


def describe_stats(name: str, st: dict) -> str:
    """只用 OpenAlex 的统计数字写一两句中文说明：数字照实写，档次只按 h 指数粗分且明说“不是分区或影响因子”。"""
    kind = _TYPE_ZH.get(st.get("type") or "", "来源")
    parts = [f"《{st.get('name') or name}》是{('由 ' + st['publisher'] + ' 出版的') if st.get('publisher') else ''}{kind}"]
    nums = []
    if st.get("works_count"):
        nums.append(f"累计收录 {st['works_count']:,} 篇论文")
    if st.get("cited_by_count"):
        nums.append(f"总被引 {st['cited_by_count']:,} 次")
    if st.get("h_index") is not None:
        nums.append(f"h 指数 {st['h_index']}")
    text = parts[0] + ("，" + "，".join(nums) if nums else "") + "。"
    h = st.get("h_index")
    if h is not None:
        tier = "影响力很高的来源" if h >= 300 else "影响力较高的来源" if h >= 100 else "有一定影响力的来源" if h >= 40 else "规模或影响力较小的来源"
        text += f"按 h 指数粗略看，属于{tier}（只是统计数字，不等于分区或影响因子）。"
    return text


async def venue_info(client: httpx.AsyncClient, name: str, venue_type: str | None = None, *, openalex_id: str | None = None) -> dict:
    """来源介绍。给了 OpenAlex 来源 id（在“期刊与会议”页面检索到的）就直接按 id 取，不用名称猜；否则按名称（含缩写）检索。"""
    out: dict = {"name": name, "listed": curated(name, venue_type), "stats": None, "note": None}
    items: list[dict] = []
    try:
        if openalex_id:
            r = await client.get(f"{OPENALEX}/sources/{openalex_id.rsplit('/', 1)[-1]}", params={"select": _SELECT})
            items = [r.json()] if r.status_code == 200 else []
        else:
            r = await client.get(f"{OPENALEX}/sources", params={"search": name, "per-page": 10, "select": _SELECT})
            items = r.json().get("results", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        out["note"] = "暂时连不上 OpenAlex，没能取到统计数据。"
    src = items[0] if (openalex_id and items) else _pick(items, name)
    if src is None and not openalex_id and out["listed"] is not None and out["note"] is None:
        # 名录里认得这个名称（比如缩写 “IEEE Trans. Netw. Sci. Eng.”），但 OpenAlex 按缩写搜不到：改用名录里的正式名称再搜一次
        full = BY_ID[out["listed"]["id"]].name
        try:
            r = await client.get(f"{OPENALEX}/sources", params={"search": full, "per-page": 10, "select": _SELECT})
            src = _pick(r.json().get("results", []) if r.status_code == 200 else [], full)
        except (httpx.HTTPError, ValueError):
            src = None
    if src:
        out["stats"] = _stats_of(src)
        out["name"] = src.get("display_name") or name
        if out["listed"] is None:  # 用缩写找到的：按正式名称再核对一次名录
            out["listed"] = curated(src.get("display_name"), src.get("type"))
        out["summary"] = describe_stats(name, out["stats"])
    elif out["note"] is None:
        out["note"] = "OpenAlex 里没有找到与这个名称完全对应的来源，不显示统计数据。"
    return out


async def search_sources(client: httpx.AsyncClient, q: str, limit: int = 12) -> list[dict]:
    """在 OpenAlex 里按名称、缩写检索期刊/会议（名录里没有的也能找到）：[{id, name, abbreviation, type, publisher, works_count, cited_by_count, h_index}]。"""
    r = await client.get(f"{OPENALEX}/sources", params={
        "search": q, "per-page": limit, "filter": "type:journal|conference", "select": _SELECT})
    if r.status_code != 200:
        return []
    out = []
    for x in r.json().get("results", []):
        v = match_venue(x.get("display_name"), x.get("type"))
        out.append({
            "id": x.get("id"), "name": x.get("display_name"), "abbreviation": x.get("abbreviated_title"), "type": x.get("type"),
            "publisher": x.get("host_organization_name"), "works_count": x.get("works_count"), "cited_by_count": x.get("cited_by_count"),
            "h_index": (x.get("summary_stats") or {}).get("h_index"),
            "listed_id": v.id if v else None, "label": v.label if v else None,
        })
    return out
