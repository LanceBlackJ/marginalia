"""从论文全文里提取代码/数据仓库链接，并给出出处页码。

PDF 里的 URL 经常被换行拆开（`https://github.com/` 换行 `FFrankyy/FINDER`、`https://doi.` 换行 `org/...`），
而解析器会把换行换成空格，所以这里要把被空格拆开的 URL 片段还原。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from ..context import AppContext
from ..util import ApiError

URL_START = re.compile(r"https?://", re.I)
TOKEN_END = re.compile(r"[\s]")
HOST_HINTS = {
    "github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket", "zenodo.org": "zenodo",
    "figshare.com": "figshare", "codeocean.com": "codeocean", "huggingface.co": "huggingface", "osf.io": "osf",
    "doi.org": "doi", "dx.doi.org": "doi", "sourceforge.net": "sourceforge", "kaggle.com": "kaggle",
}
CODE_KINDS = {"github", "gitlab", "bitbucket", "zenodo", "figshare", "codeocean", "huggingface", "osf", "sourceforge"}
TRAIL = ".,;:)]}>”’\"'"


@dataclass
class CodeLink:
    url: str
    kind: str            # github | gitlab | … | doi | other
    supported: bool      # 后端能否直接获取（目前：GitHub 与直链 zip）
    page: int
    ord: int
    context: str         # 上下文片段，便于用户判断是不是“代码可用性声明”
    is_code_statement: bool  # 附近是否出现 Code availability / source code 等字样


def _join_broken(text: str, start: int) -> str:
    """从 start 处读出 URL，并把被空格拆开的片段接回去（最多接 2 次）。"""
    m = TOKEN_END.search(text, start)
    end = m.start() if m else len(text)
    url = text[start:end]
    joins = 0
    pos = end
    while joins < 2 and pos < len(text):
        broken = url.endswith(("/", "-", "_")) or re.search(r"(?:doi|www|github|gitlab|zenodo)\.$", url, re.I)
        if not broken:
            break
        m2 = re.match(r"\s+([A-Za-z0-9][^\s]*)", text[pos:])
        if not m2:
            break
        url += m2.group(1)
        pos += m2.end()
        joins += 1
    return url


def clean_url(url: str) -> str:
    url = url.strip()
    # 去掉结尾标点与不配对的右括号
    while url and url[-1] in TRAIL:
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        url = url[:-1]
    return url


def classify(url: str) -> tuple[str, bool]:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    kind = HOST_HINTS.get(host)
    if kind is None:
        for h, k in HOST_HINTS.items():
            if host.endswith("." + h):
                kind = k
                break
    kind = kind or "other"
    supported = kind == "github" or url.lower().split("?")[0].endswith(".zip")
    return kind, supported


def extract_links(text: str) -> list[tuple[str, int]]:
    """返回 [(url, 起始位置)]，URL 已修复/清理。"""
    out: list[tuple[str, int]] = []
    for m in URL_START.finditer(text):
        raw = _join_broken(text, m.start())
        url = clean_url(raw)
        if len(url) > 12:
            out.append((url, m.start()))
    return out


def find_code_links(app: AppContext, paper_id: str) -> list[dict]:
    with app.db.session() as conn:
        if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
            raise ApiError("not_found", "论文不存在", 404)
        rows = conn.execute(
            "SELECT ord, page, text FROM passages WHERE paper_id = ? ORDER BY ord", (paper_id,)
        ).fetchall()
    found: dict[str, CodeLink] = {}
    for r in rows:
        text = r["text"]
        low = text.lower()
        statement = bool(re.search(r"code availab|source code|code and data|software availab|data availab|代码|开源", low))
        for url, pos in extract_links(text):
            kind, supported = classify(url)
            if kind == "other":
                continue
            # DOI 只有出现在“代码/数据可用性”语境里才有意义（否则是参考文献）
            if kind == "doi" and not statement:
                continue
            key = url.lower().rstrip("/")
            ctx_text = re.sub(r"\s+", " ", text[max(0, pos - 90): pos + len(url) + 40]).strip()
            link = CodeLink(url, kind, supported, r["page"], r["ord"], ctx_text, statement)
            old = found.get(key)
            if old is None or (statement and not old.is_code_statement):
                found[key] = link
    items = sorted(found.values(), key=lambda l: (not l.is_code_statement, l.kind not in CODE_KINDS, l.page))
    return [asdict(l) for l in items]
