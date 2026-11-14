"""缩略语表等“一条一行”的列表：MinerU 把它们并成了一段，这里用 PDF 文字层把每条的换行找回来。

只处理标题是 Abbreviations / Acronyms / Glossary / Nomenclature / 缩略语 … 的章节，并且要求能从文字层认出
“缩写<Tab>”“全称”两行一条的结构，认不出就原样不动（宁可不改，不改错）。"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from .pdf_parser import _PARSE_LOCK
from .pdf_parser import ParsedDoc

log = logging.getLogger(__name__)

LIST_HEAD = re.compile(
    r"(?i)^\s*(?:list of\s+)?(?:abbreviations?|acronyms?|glossary|nomenclature|notations?|symbols?)\b"
    r"|^\s*(?:缩略语|缩写|术语表|符号表|符号说明|名词解释)"
)
ENTRY_GAP = " "  # 缩写和全称之间：em 空格，不会被“折叠换行和空格”的显示规则吃掉
_TRIM = " \t  "


def list_entries(page, bbox) -> list[str]:
    """文字层里每条是“缩写<Tab>”“<BEL>全称”两行（或一行写完）；认出这种结构才返回条目，否则返回空。"""
    x0, y0, x1, y1 = bbox
    raw = page.get_text("text", clip=pymupdf.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2))
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        a = lines[i]
        clean = a.replace("\x07", "").strip(_TRIM)
        if a.rstrip(" ").endswith("\t") and i + 1 < len(lines):
            b = lines[i + 1].replace("\x07", "").strip(_TRIM)
            out.append(f"{clean}{ENTRY_GAP}{b}")
            i += 2
            continue
        out.append(clean)
        i += 1
    tabbed = sum(1 for ln in lines if ln.rstrip(" ").endswith("\t"))
    return out if tabbed >= 2 else []


def _squash(t: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", t).lower()


def restore_lists_safe(parsed: ParsedDoc, path: Path) -> None:
    try:
        titles = {s.ord: s.title for s in parsed.sections}
        targets = [p for p in parsed.passages if p.kind == "paragraph" and LIST_HEAD.match(titles.get(p.section_ord, "") or "")]
        if not targets:
            return
        with _PARSE_LOCK:
            doc = pymupdf.open(str(path))
            try:
                for p in targets:
                    if not 1 <= p.page <= doc.page_count or doc[p.page - 1].rotation:
                        continue
                    entries = list_entries(doc[p.page - 1], p.bbox)
                    title = (titles.get(p.section_ord) or "").strip().lower()
                    if entries and entries[0].strip().lower() == title:  # 取样框把章节标题那一行也框进来了
                        entries = entries[1:]
                    # 字母数字完全一致才替换：只是把换行找回来，不改任何词
                    if len(entries) >= 2 and _squash("".join(entries)) == _squash(p.text):
                        p.text = "\n".join(entries)
            finally:
                doc.close()
    except Exception as exc:  # noqa: BLE001  只是增强，失败不影响导入
        log.warning("还原缩略语表换行失败，跳过：%s", exc)
