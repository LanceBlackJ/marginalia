"""用 PDF 自带的内部超链接识别正文里的引用：LaTeX（hyperref）、Word、出版社排版出来的 PDF 里，正文每个引用标记通常都是一个指向参考文献条目的链接。

比正则可靠得多（“[64–66]”“(Peters et al., 2018a)”“Smith (2020)”不用猜写法），也不受编号风格影响。做法：
1. 每个内部链接有一个目的地（页码 + 坐标）；落在某条参考文献的段落上，就知道它引用的是哪一条；落在图表、章节上的链接忽略。
2. 链接在源页上的矩形框里的文字就是引用标记的文字；回到所在段落里定位它。
3. 和正则识别的结果取并集（链接优先）：没有链接的 PDF（扫描版、转换过的）仍然靠正则。
"""
from __future__ import annotations

import re
from pathlib import Path

DEST_TOL = 16.0  # 目的地坐标离条目文字框的最大距离（pt）：LaTeX 的锚点常在条目首行上方几个点
SRC_TOL = 4.0


def _dist(x: float, y: float, b: list[float]) -> float:
    dx = max(b[0] - x, 0.0, x - b[2])
    dy = max(b[1] - y, 0.0, y - b[3])
    return (dx * dx + dy * dy) ** 0.5


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _consistent(text: str, entry: dict, style: str) -> bool:
    """链接指向的条目要和正文里看得见的标记文字对得上：对不上的（目的地坐标不准、条目分错）宁可不要，交给正则识别。"""
    nums = [int(x) for x in re.findall(r"\d{1,3}", text)]
    if style == "numeric":
        if not nums:
            return True
        r = entry["index"]
        return r in nums or any(a <= r <= b for a, b in zip(nums, nums[1:]))
    letters = re.sub(r"[^a-z]", "", text.lower())
    first = next((a for a in entry.get("authors") or [] if a and a != "et al."), "")
    sn = re.sub(r"[^a-z]", "", first.split(",")[0].lower())
    year = str(entry.get("year") or "")
    if letters:
        return bool(sn) and sn[:4] in letters
    return bool(year) and year in text


def link_markers(pdf: Path, passages: list[dict], entries: list[dict], style: str = "numeric") -> list[dict]:
    """passages：全部段落 [{ord, page, kind, text, bbox}]；entries：参考文献 [{index, ords}]。返回 [{ord, markers}]（markers 里的 kind 为 'link' 或 'sup'）。"""
    if not entries or not pdf.is_file():
        return []
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        return []
    ref_index = {o: e["index"] for e in entries for o in e["ords"]}
    by_index = {e["index"]: e for e in entries}
    refs_by_page: dict[int, list[dict]] = {}
    body_by_page: dict[int, list[dict]] = {}
    for p in passages:
        if p["kind"] == "reference" and p["ord"] in ref_index:
            refs_by_page.setdefault(p["page"], []).append(p)
        elif p["kind"] in ("paragraph", "caption", "front"):
            body_by_page.setdefault(p["page"], []).append(p)
    found: dict[int, list[tuple[float, float, str, list[int], bool]]] = {}  # ord → [(y, x, 文字, refs, 是否紧贴在词后的上标)]
    try:
        doc = pymupdf.open(pdf)
    except Exception:  # noqa: BLE001
        return []
    with doc:
        for pg in doc:
            page_no = pg.number + 1
            body = body_by_page.get(page_no, [])
            if not body:
                continue
            words_on_page = pg.get_text("words")
            for lk in pg.get_links():
                if lk.get("kind") not in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) or lk.get("page") is None or lk.get("to") is None:
                    continue
                dest_page = lk["page"] + 1
                cands = refs_by_page.get(dest_page)
                if not cands:
                    continue
                to = lk["to"]
                # 命名目的地（LaTeX/hyperref、出版社排版几乎都是这种）的坐标 PyMuPDF 不翻转，原点在页面左下角；显式目的地已转成左上角原点
                ty = doc[lk["page"]].rect.height - to.y if lk["kind"] == pymupdf.LINK_NAMED else to.y
                # LaTeX/出版社的锚点在目标条目首行的上方几个点（可能落在上一条的下缘里）：先取锚点下方同栏最近的一条，没有再取包含锚点的
                below = [p for p in cands if p["bbox"][1] >= ty - 3 and p["bbox"][0] - 25 <= to.x <= p["bbox"][2] + 25 and p["bbox"][1] - ty <= 40]
                inside = [p for p in cands if _dist(to.x, ty, p["bbox"]) == 0]
                if below:
                    best = min(below, key=lambda p: p["bbox"][1] - ty)
                elif inside:
                    best = inside[0]
                else:
                    best = min(cands, key=lambda p: _dist(to.x, ty, p["bbox"]))
                    if _dist(to.x, ty, best["bbox"]) > DEST_TOL:
                        continue
                rect = lk["from"]
                txt = " ".join(w[4] for w in words_on_page if rect.x0 - 1 <= (w[0] + w[2]) / 2 <= rect.x1 + 1 and rect.y0 - 1 <= (w[1] + w[3]) / 2 <= rect.y1 + 1)
                if not txt or len(txt) > 80:
                    continue
                cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
                near = [p for p in body if _dist(cx, cy, p["bbox"]) <= SRC_TOL + 30 and _norm(txt) in _norm(p["text"])]
                if not near:
                    continue
                src = min(near, key=lambda p: _dist(cx, cy, p["bbox"]))
                if not _consistent(txt, by_index[ref_index[best["ord"]]], style):
                    continue
                found.setdefault(src["ord"], []).append((rect.y0, rect.x0, txt, [ref_index[best["ord"]]], False))
    by_ord = {p["ord"]: p for ps in body_by_page.values() for p in ps}
    out: list[dict] = []
    for o, items in found.items():
        text = by_ord[o]["text"]
        items.sort(key=lambda t: (round(t[0] / 3), t[1]))
        marks: list[dict] = []
        cursor = 0
        for _y, _x, txt, refs, _sup in items:
            # 文字在段落里的位置：忽略空白差异地从上一个标记之后往后找
            pat = r"\s*".join(re.escape(ch) for ch in txt.replace(" ", ""))
            m = re.compile(pat).search(text, cursor)
            if not m:
                continue
            s, e = m.start(), m.end()
            if s > 0 and e < len(text) and text[s - 1] == "[" and text[e] == "]":  # 链接只框住括号里的数字：把方括号也算进标记
                s, e = s - 1, e + 1
            elif s > 0 and e < len(text) and text[s - 1] == "(" and text[e] == ")" and re.fullmatch(r"\d{1,3}", txt):
                s, e = s - 1, e + 1
            kind = "sup" if re.fullmatch(r"\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*", txt) and s > 0 and text[s - 1].isalpha() else "link"
            marks.append({"kind": kind, "text": text[s:e], "start": s, "end": e, "refs": refs, "pre": text[max(0, s - 10):s]})
            cursor = e
        # 同一个标记的几段链接（“(Peters et al.,” 和 “2018)” 各一段）指向同一条：相邻的并成一个
        merged: list[dict] = []
        for mk in marks:
            if merged and merged[-1]["refs"] == mk["refs"] and mk["kind"] == "link" and merged[-1]["kind"] == "link" and 0 <= mk["start"] - merged[-1]["end"] <= 2:
                p = merged[-1]
                p["end"] = mk["end"]
                p["text"] = text[p["start"]:p["end"]]
            else:
                merged.append(mk)
        if merged:
            out.append({"ord": o, "markers": merged})
    out.sort(key=lambda d: d["ord"])
    return out


_YEAR_ONLY = re.compile(r"^\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?[,;.]?$")
_AUTHOR_BEFORE = re.compile(
    r"((?:(?:van|von|de|der|den|di|da|del|la|le)\s+)*[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]+(?:\s+et al\.?|\s+(?:and|&)\s+[A-ZÀ-ÖØ-Þ][\w’'\-À-ÿ]+)?)[,;]?\s*\(?\s*$"
)


_YEAR_AFTER = re.compile(r"^\s*[,;]?\s*(?:19|20)\d{2}[a-z]?(?:\s*\))?")


def _tidy(text: str, mk: dict) -> dict:
    """作者-年份引用的收尾：只框住名字没框年份的，向右补上年份；去掉链接框多带进来的句尾标点。"""
    t = mk["text"]
    end = mk["end"]
    if re.search(r"[A-Za-z]", t) and not re.search(r"(?:19|20)\d{2}", t):
        ym = _YEAR_AFTER.match(text[end:end + 20])
        if ym:
            end += ym.end()
    seg = text[mk["start"]:end]
    while seg and seg[-1] in ",;.:" :
        seg = seg[:-1]
    start = mk["start"]
    if seg.startswith("(") and ")" not in seg:  # 括号只有一半（另一半在相邻的链接里）：不带进标记
        seg, start = seg[1:], start + 1
    elif seg.endswith(")") and "(" not in seg:
        seg = seg[:-1]
    end = start + len(seg)
    if start == mk["start"] and end == mk["end"] and seg == t:
        return mk
    return {**mk, "start": start, "end": end, "text": seg}


def _widen(text: str, mk: dict) -> dict:
    """作者-年份引用里，PDF 常只把“年份”做成链接（Devlin et al. (2019) 里只有 2019 有链接）：向左补上前面的作者名，悬停的范围才是完整的引用。"""
    if not _YEAR_ONLY.match(mk["text"].strip()):
        return mk
    m = _AUTHOR_BEFORE.search(text[max(0, mk["start"] - 80):mk["start"]])
    if not m:
        return mk
    start = mk["start"] - (len(text[max(0, mk["start"] - 80):mk["start"]]) - m.start(1))
    end = mk["end"]
    seg = text[start:end]
    if "(" in seg and ")" not in seg and text[end:end + 1] == ")":  # “Smith (2020a” 少了右括号：补上
        end += 1
    return {**mk, "kind": "link", "start": start, "end": end, "text": text[start:end], "pre": text[max(0, start - 10):start]}


def merge_markers(regex_items: list[dict], link_items: list[dict], texts: dict[int, str] | None = None) -> list[dict]:
    """链接识别的标记和正则识别的标记合并。

    - 指向同一条参考文献、范围有重叠：取两者的并集（完整的“Smith et al. (2020)”，而不是只有年份那一小段）；
    - 只被一方识别的保留（没有链接的引用靠正则，正则没认出来的靠链接）；
    - 范围重叠但引用的不是同一条：以链接为准（链接是排版时写进 PDF 的）。
    texts：段落原文（按 ord），用来把只有年份的链接向左补成完整引用。
    """
    texts = texts or {}
    by: dict[int, list[dict]] = {}
    for d in link_items:
        ms = [_tidy(texts[d["ord"]], _widen(texts[d["ord"]], m)) if d["ord"] in texts else m for m in d["markers"]]
        by[d["ord"]] = ms
    for d in regex_items:
        cur = by.setdefault(d["ord"], [])
        text = texts.get(d["ord"], "")
        for mk in d["markers"]:
            over = [c for c in cur if not (mk["end"] <= c["start"] or mk["start"] >= c["end"])]
            if not over:
                cur.append(mk)
                continue
            if all(set(c["refs"]) & set(mk["refs"]) for c in over) and text:
                # 同一条引用：并成更完整的那一段
                start, end = min([mk["start"], *(c["start"] for c in over)]), max([mk["end"], *(c["end"] for c in over)])
                refs = sorted({r for c in (*over, mk) for r in c["refs"]})
                for c in over:
                    cur.remove(c)
                cur.append({"kind": "link", "text": text[start:end], "start": start, "end": end, "refs": refs, "pre": text[max(0, start - 10):start]})
    return [{"ord": o, "markers": sorted(ms, key=lambda m: (m["start"], -m["end"]))} for o, ms in sorted(by.items()) if ms]
