"""从 PDF 的另一条文字提取路径修复字体映射损坏的段落。

PyMuPDF 对部分嵌入字体会把重音字符读成 U+FFFD。Poppler 的 pdftotext
通常使用另一套 ToUnicode 映射，适合做保守回退：只在编号和页码能对上的
参考文献段落上替换，匹配不上就不猜。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pymupdf


def repair_reference_passages(pdf: Path, passages: list[dict]) -> None:
    if not any("�" in str(p.get("text") or "") for p in passages):
        return
    exe = shutil.which("pdftotext")
    if not exe or not pdf.is_file():
        return
    try:
        if pdf.read_bytes()[:5] != b"%PDF-":
            return
    except OSError:
        return
    pages = sorted({int(p["page"]) for p in passages if p.get("page") is not None})
    if not pages:
        return
    blocks = _pymupdf_reference_blocks(pdf, pages[0], pages[-1])
    if not blocks:
        blocks = _pdftotext_reference_blocks(exe, pdf, pages[0], pages[-1])
    if not blocks:
        return
    for p in passages:
        m = re.match(r"\s*(?:\[(\d+)\]|(\d+)[.)])", str(p.get("text") or ""))
        if not m:
            continue
        idx = int(m.group(1) or m.group(2))
        candidate = blocks.get(idx)
        if candidate and "�" not in candidate and len(candidate) >= 30:
            p["text"] = candidate


def _normalise_reference_block(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-[ \t\r\n]+(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pymupdf_reference_blocks(pdf: Path, first: int, last: int) -> dict[int, str]:
    """按 PDF 文字层的真实行顺序提取参考文献，避免双栏 pdftotext 的串栏。"""
    blocks: dict[int, str] = {}
    try:
        doc = pymupdf.open(str(pdf))
        lines: list[str] = []
        started = False
        for page_no in range(max(1, first), min(doc.page_count, last) + 1):
            for line in doc[page_no - 1].get_text("text").splitlines():
                stripped = line.strip()
                if not started and re.fullmatch(r"references", stripped, re.I):
                    started = True
                if started:
                    lines.append(line)
        doc.close()
    except Exception:  # noqa: BLE001  文字层不可读时退回 Poppler
        return {}
    current: int | None = None
    pieces: list[str] = []
    for line in lines:
        if re.fullmatch(r"acknowledgements?|author contributions|funding|competing interests", line.strip(), re.I):
            break
        m = re.match(r"^\s*(\d{1,3})\.\s*(.*)$", line)
        if m:
            if current is not None:
                body = _normalise_reference_block(" ".join(pieces))
                blocks.setdefault(current, f"{current}. {body}".strip())
            current = int(m.group(1))
            pieces = [m.group(2)] if m.group(2) else []
        elif current is not None:
            pieces.append(line.strip())
    if current is not None:
        body = _normalise_reference_block(" ".join(pieces))
        blocks.setdefault(current, f"{current}. {body}".strip())
    return blocks


def _pdftotext_reference_blocks(exe: str, pdf: Path, first: int, last: int) -> dict[int, str]:
    """Poppler 回退路径；仅在 PyMuPDF 没有可用参考文献块时使用。"""
    try:
        proc = subprocess.run(
            [exe, "-f", str(first), "-l", str(last), "-enc", "UTF-8", "-layout", str(pdf), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if not proc.stdout:
        return {}
    starts = list(re.finditer(r"(?m)^\s*(\d{1,3})\.\s+", proc.stdout))
    blocks: dict[int, str] = {}
    for i, m in enumerate(starts):
        idx = int(m.group(1))
        block = proc.stdout[m.start() : starts[i + 1].start() if i + 1 < len(starts) else len(proc.stdout)]
        block = _normalise_reference_block(block)
        blocks.setdefault(idx, block)
    return blocks
