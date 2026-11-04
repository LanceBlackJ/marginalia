"""引文导出：BibTeX、RIS、CSL JSON、GB/T 7714、APA。

字段只来自文库里的元数据（标题、作者、年份、期刊/会议、DOI、arXiv、网址）；缺失的字段一律省略，不编造（库里没有卷、期、页码，所以输出里也没有）。
条目类型由来源名称做保守判断：能看出是会议/期刊就用对应类型，看不出来的有 arXiv 编号按预印本，否则按“杂项”。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from ..context import AppContext
from ..util import ApiError, loads

FORMATS = {
    "bibtex": ("BibTeX", "bib", "application/x-bibtex"),
    "ris": ("RIS", "ris", "application/x-research-info-systems"),
    "csl": ("CSL JSON", "json", "application/json"),
    "gbt7714": ("GB/T 7714", "txt", "text/plain"),
    "apa": ("APA 第 7 版", "txt", "text/plain"),
    "mla": ("MLA 第 9 版", "txt", "text/plain"),
    "ieee": ("IEEE", "txt", "text/plain"),
}

_CONF = re.compile(
    r"\b(conference|proceedings|proc\.|workshop|symposium|neurips|nips|icml|iclr|cvpr|iccv|eccv|acl|emnlp|naacl|aaai|ijcai|kdd|www|sigir|"
    r"sigmod|vldb|osdi|sosp|nsdi|stoc|focs|chi|icse|ccs|isca|uai|aistats|colt|wsdm|cikm|icdm)\b|neural information processing systems",
    re.I,
)
_CJK = re.compile(r"[一-鿿]")


@dataclass
class Person:
    family: str
    given: str = ""

    @property
    def literal(self) -> str:
        return (self.family + (" " + self.given if self.given else "")).strip()


def parse_person(name: str) -> Person:
    """“姓, 名”与“名 姓”两种写法都规范成 (姓, 名)；中文名不拆；带“Jr./III”等后缀时保留在姓里。"""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return Person("")
    if _CJK.search(name):
        return Person(name)
    if "," in name:
        family, _, given = name.partition(",")
        return Person(family.strip(), given.strip())
    parts = name.split(" ")
    if len(parts) == 1:
        return Person(parts[0])
    suffix = ""
    if parts[-1].lower().rstrip(".") in {"jr", "sr", "ii", "iii", "iv"} and len(parts) > 2:
        suffix = " " + parts.pop()
    # 小写前缀（van、de、von、der、la…）属于姓
    i = len(parts) - 1
    while i > 1 and parts[i - 1].islower():
        i -= 1
    return Person(" ".join(parts[i:]) + suffix, " ".join(parts[:i]))


def kind_of(p: dict) -> str:
    """article | inproceedings | preprint | misc"""
    venue = (p.get("venue") or "").strip()
    if venue:
        return "inproceedings" if _CONF.search(venue) else "article"
    if p.get("arxiv_id"):
        return "preprint"
    return "misc"


# ---------------------------------------------------------------- 取数据
def load_papers(app: AppContext, *, paper_ids: list[str] | None = None, tag_id: str | None = None) -> list[dict]:
    """按选中的论文 / 标签 / 全库取元数据（补充材料子论文不导出）。顺序：选中顺序，其余按年份、标题。"""
    with app.db.session() as conn:
        if paper_ids:
            marks = ",".join("?" * len(paper_ids))
            rows = conn.execute(f"SELECT * FROM papers WHERE id IN ({marks})", list(paper_ids)).fetchall()
            order = {pid: i for i, pid in enumerate(paper_ids)}
            rows = sorted(rows, key=lambda r: order.get(r["id"], 0))
        elif tag_id:
            rows = conn.execute(
                "SELECT p.* FROM papers p JOIN kb_paper_topics t ON t.paper_id = p.id "
                "WHERE t.topic_id = ? AND t.status = 'confirmed' ORDER BY p.year, p.title", (tag_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM papers ORDER BY year, title").fetchall()
        sup = supplement_child_ids(conn)
    out = []
    for r in rows:
        if r["id"] in sup:
            continue
        out.append({"id": r["id"], "title": (r["title"] or "").strip(), "authors": loads(r["authors_json"], []), "year": r["year"],
                    "venue": (r["venue"] or "").strip() or None, "doi": (r["doi"] or "").strip() or None,
                    "arxiv_id": (r["arxiv_id"] or "").strip() or None, "url": (r["source_url"] or "").strip() or None})
    return out


def supplement_child_ids(conn) -> set[str]:
    """补充材料子论文（作为主论文的附件存在，不单独导出）。"""
    ids = {r[0] for r in conn.execute("SELECT id FROM papers WHERE parent_id IS NOT NULL")}
    ids |= {r[0] for r in conn.execute("SELECT child_paper_id FROM supplementary_assets WHERE child_paper_id IS NOT NULL")}
    return ids


# ---------------------------------------------------------------- 工具
_BIB_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "_": r"\_", "#": r"\#", "$": r"\$", "^": r"\^{}", "~": r"\~{}"}


def bib_escape(s: str) -> str:
    return "".join(_BIB_ESC.get(ch, ch) for ch in s)


def _ascii_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]", "", s)


_STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "from", "by", "at", "is", "are", "toward", "towards"}


def make_keys(papers: list[dict]) -> dict[str, str]:
    """稳定的 BibTeX 键：第一作者姓 + 年份 + 标题首个实词；同键加 a、b、c… 后缀（按输入顺序，保证重复导出结果一致）。"""
    used: dict[str, int] = {}
    keys: dict[str, str] = {}
    for p in papers:
        first = parse_person(p["authors"][0]).family if p["authors"] else ""
        fam = _ascii_key(first.split(" ")[-1] if first and not _CJK.search(first) else first) or "anon"
        words = [w for w in re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", p["title"])) if w.lower() not in _STOP]
        key = f"{fam}{p['year'] or ''}{(words[0] if words else '').capitalize()}"
        n = used.get(key, 0)
        used[key] = n + 1
        keys[p["id"]] = key if n == 0 else key + chr(ord("a") + n - 1) if n <= 26 else f"{key}{n}"
    return keys


def _doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}"


# ---------------------------------------------------------------- BibTeX
def to_bibtex(papers: list[dict]) -> str:
    keys = make_keys(papers)
    out = []
    for p in papers:
        kind = kind_of(p)
        etype = {"article": "article", "inproceedings": "inproceedings", "preprint": "misc", "misc": "misc"}[kind]
        fields: list[tuple[str, str]] = [("title", "{" + bib_escape(p["title"]) + "}")]
        if p["authors"]:
            fields.append(("author", "{" + " and ".join(_bib_person(a) for a in p["authors"]) + "}"))
        if p["year"]:
            fields.append(("year", "{" + str(p["year"]) + "}"))
        if p["venue"]:
            fields.append(("booktitle" if etype == "inproceedings" else "journal", "{" + bib_escape(p["venue"]) + "}"))
        if p["doi"]:
            fields.append(("doi", "{" + p["doi"] + "}"))
        if p["arxiv_id"]:
            fields.append(("eprint", "{" + p["arxiv_id"] + "}"))
            fields.append(("archiveprefix", "{arXiv}"))
        if p["url"] and not p["doi"]:
            fields.append(("url", "{" + p["url"] + "}"))
        body = ",\n".join(f"  {k} = {v}" for k, v in fields)
        out.append(f"@{etype}{{{keys[p['id']]},\n{body}\n}}")
    return "\n\n".join(out) + ("\n" if out else "")


def _bib_person(name: str) -> str:
    per = parse_person(name)
    if _CJK.search(per.family):  # 中文名整体作为一个姓，避免 BibTeX 拆分
        return "{" + per.family + "}"
    return bib_escape(per.family) + (", " + bib_escape(per.given) if per.given else "")


# ---------------------------------------------------------------- RIS
def to_ris(papers: list[dict]) -> str:
    out = []
    for p in papers:
        kind = kind_of(p)
        ty = {"article": "JOUR", "inproceedings": "CONF", "preprint": "UNPB", "misc": "GEN"}[kind]
        lines = [f"TY  - {ty}", f"TI  - {p['title']}"]
        for a in p["authors"]:
            per = parse_person(a)
            lines.append(f"AU  - {per.family}" + (f", {per.given}" if per.given else ""))
        if p["year"]:
            lines.append(f"PY  - {p['year']}")
        if p["venue"]:
            lines.append(f"{'T2' if kind == 'inproceedings' else 'JO'}  - {p['venue']}")
        if p["doi"]:
            lines.append(f"DO  - {p['doi']}")
            lines.append(f"UR  - {_doi_url(p['doi'])}")
        elif p["url"]:
            lines.append(f"UR  - {p['url']}")
        if p["arxiv_id"]:
            lines.append(f"N1  - arXiv:{p['arxiv_id']}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- CSL JSON
def to_csl(papers: list[dict]) -> str:
    keys = make_keys(papers)
    items = []
    for p in papers:
        kind = kind_of(p)
        it: dict = {"id": keys[p["id"]], "type": {"article": "article-journal", "inproceedings": "paper-conference",
                                                  "preprint": "article", "misc": "document"}[kind], "title": p["title"]}
        if p["authors"]:
            it["author"] = [({"literal": per.family} if _CJK.search(per.family) else {"family": per.family, **({"given": per.given} if per.given else {})})
                            for per in map(parse_person, p["authors"])]
        if p["year"]:
            it["issued"] = {"date-parts": [[p["year"]]]}
        if p["venue"]:
            it["event-title" if kind == "inproceedings" else "container-title"] = p["venue"]
        if p["doi"]:
            it["DOI"] = p["doi"]
            it["URL"] = _doi_url(p["doi"])
        elif p["url"]:
            it["URL"] = p["url"]
        if p["arxiv_id"]:
            it["number"] = p["arxiv_id"]
            it["publisher"] = "arXiv"
        items.append(it)
    return json.dumps(items, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------- 文本参考文献
def _initials(given: str) -> str:
    return " ".join(w[0].upper() for w in re.split(r"[\s\-]+", given) if w)


def _gbt_person(name: str) -> str:
    per = parse_person(name)
    if _CJK.search(per.family):
        return per.family
    return per.family.upper() + (" " + _initials(per.given) if per.given else "")


def to_gbt7714(papers: list[dict]) -> str:
    """GB/T 7714—2015 顺序编码制：[序号] 作者. 题名[J/C/EB/OL]. 来源, 年份. DOI."""
    lines = []
    for i, p in enumerate(papers, 1):
        kind = kind_of(p)
        mark = {"article": "J", "inproceedings": "C", "preprint": "EB/OL", "misc": "Z"}[kind]
        names = [_gbt_person(a) for a in p["authors"]]
        who = ", ".join(names[:3]) + (", et al" if len(names) > 3 else "") if names else ""
        parts = []
        if who:
            parts.append(who + ".")
        parts.append(f"{p['title']}[{mark}].")
        src = ", ".join(x for x in (p["venue"], str(p["year"]) if p["year"] else None) if x)
        if p["venue"] and kind == "inproceedings":
            src = f"{p['venue']}, {p['year']}" if p["year"] else p["venue"]
        elif not p["venue"] and p["year"]:
            src = f"{p['year']}"
        if src:
            parts.append(src + ".")
        if p["doi"]:
            parts.append(f"DOI:{p['doi']}.")
        elif p["arxiv_id"]:
            parts.append(f"arXiv:{p['arxiv_id']}.")
        lines.append(f"[{i}] " + " ".join(parts))
    return "\n".join(lines) + ("\n" if lines else "")


def _apa_person(name: str) -> str:
    per = parse_person(name)
    if _CJK.search(per.family):
        return per.family
    ini = " ".join("-".join(f"{w[0].upper()}." for w in part.split("-") if w) for part in per.given.split() if part)
    return per.family + (f", {ini}" if ini else "")


def to_apa(papers: list[dict]) -> str:
    lines = []
    for p in sorted(papers, key=lambda x: ((parse_person(x["authors"][0]).family if x["authors"] else x["title"]).lower(), x["year"] or 0)):
        names = [_apa_person(a) for a in p["authors"]]
        if not names:
            who = ""
        elif len(names) == 1:
            who = names[0]
        elif len(names) <= 20:
            who = ", ".join(names[:-1]) + ", & " + names[-1]
        else:
            who = ", ".join(names[:19]) + ", ... " + names[-1]
        year = f"({p['year']})" if p["year"] else "(n.d.)"
        head = (who + " " if who else "") + year + "."
        title = p["title"].rstrip(".")
        tail = f"{p['venue']}." if p["venue"] else ""
        if p["venue"] and kind_of(p) == "inproceedings":
            tail = f"In {p['venue']}."
        link = _doi_url(p["doi"]) if p["doi"] else (f"https://arxiv.org/abs/{p['arxiv_id']}" if p["arxiv_id"] else (p["url"] or ""))
        pre = " arXiv." if p["arxiv_id"] and not p["venue"] else ""
        lines.append(" ".join(x for x in (head, title + ".", tail + pre if (tail or pre) else "", link) if x).replace(" .", "."))
    return "\n".join(lines) + ("\n" if lines else "")


def _link_of(p: dict) -> str:
    return _doi_url(p["doi"]) if p["doi"] else (f"https://arxiv.org/abs/{p['arxiv_id']}" if p["arxiv_id"] else (p["url"] or ""))


def _mla_person(name: str, first: bool) -> str:
    per = parse_person(name)
    if _CJK.search(per.family) or not per.given:
        return per.family
    return f"{per.family}, {per.given}" if first else f"{per.given} {per.family}"


def to_mla(papers: list[dict]) -> str:
    """MLA 第 9 版（纯文本，容器名不加斜体）：作者. “题名.” 来源, 年份, 链接."""
    lines = []
    for p in papers:
        names = [_mla_person(a, i == 0) for i, a in enumerate(p["authors"])]
        if not names:
            who = ""
        elif len(names) == 1:
            who = names[0]
        elif len(names) == 2:
            who = f"{names[0]}, and {names[1]}"
        else:
            who = f"{names[0]}, et al"
        title = p["title"].rstrip(".")
        src = [x for x in (p["venue"] or ("arXiv" if p["arxiv_id"] else None), str(p["year"]) if p["year"] else None, _link_of(p)) if x]
        parts = []
        if who:
            parts.append(who.rstrip(".") + ".")
        parts.append(f"“{title}.”")
        if src:
            parts.append(", ".join(src) + ".")
        lines.append(" ".join(parts))
    return "\n".join(lines) + ("\n" if lines else "")


def _ieee_person(name: str) -> str:
    per = parse_person(name)
    if _CJK.search(per.family) or not per.given:
        return per.family
    ini = " ".join("-".join(f"{w[0].upper()}." for w in part.split("-") if w) for part in per.given.split() if part)
    return f"{ini} {per.family}"


def to_ieee(papers: list[dict]) -> str:
    """IEEE：[序号] A. B. Last, C. Last, and D. Last, “题名,” 来源, 年份, doi: …（超过 6 位作者只写第一作者加 et al.）。"""
    lines = []
    for i, p in enumerate(papers, 1):
        names = [_ieee_person(a) for a in p["authors"]]
        if not names:
            who = ""
        elif len(names) > 6:
            who = f"{names[0]} et al."
        elif len(names) == 1:
            who = names[0]
        elif len(names) == 2:
            who = f"{names[0]} and {names[1]}"
        else:
            who = ", ".join(names[:-1]) + ", and " + names[-1]
        title = p["title"].rstrip(".,")
        bits = [x for x in (p["venue"] or ("arXiv preprint" if p["arxiv_id"] else None), str(p["year"]) if p["year"] else None) if x]
        tail = ", ".join(bits)
        if p["doi"]:
            tail = (tail + ", " if tail else "") + f"doi: {p['doi']}"
        elif p["arxiv_id"]:
            tail = (tail + ", " if tail else "") + f"arXiv:{p['arxiv_id']}"
        elif p["url"]:
            tail = (tail + ", " if tail else "") + p["url"]
        head = (who + ", " if who else "") + f"“{title},”"
        lines.append(f"[{i}] " + head + (" " + tail if tail else "") + ".")
    return "\n".join(lines) + ("\n" if lines else "")


RENDERERS = {"bibtex": to_bibtex, "ris": to_ris, "csl": to_csl, "gbt7714": to_gbt7714, "apa": to_apa, "mla": to_mla, "ieee": to_ieee}


def export_citations(app: AppContext, fmt: str, *, paper_ids: list[str] | None = None, tag_id: str | None = None) -> dict:
    if fmt not in RENDERERS:
        raise ApiError("bad_request", "不支持的引文格式")
    papers = load_papers(app, paper_ids=paper_ids, tag_id=tag_id)
    if not papers:
        raise ApiError("bad_request", "没有可导出的论文")
    label, ext, mime = FORMATS[fmt]
    scope = "选中论文" if paper_ids else ("标签" if tag_id else "全部文献")
    return {"filename": f"Marginalia_{scope}_{fmt}.{ext}", "content": RENDERERS[fmt](papers), "count": len(papers), "format": fmt,
            "label": label, "mime": mime}
