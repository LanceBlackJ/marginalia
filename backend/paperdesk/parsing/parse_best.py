"""统一的解析入口：MinerU 可用就用它，任何失败回退到规则解析（parse_pdf）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import pymupdf

from . import font_style, mineru_adapter, mineru_client
from .pdf_parser import _PARSE_LOCK, ParsedDoc, PdfError, parse_pdf

log = logging.getLogger("paperdesk.parse")


def _probe(path: Path) -> tuple[list[tuple[float, float]], dict, int]:
    """页面尺寸（显示方向）、文档元数据、文字层字符数。"""
    with _PARSE_LOCK:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise PdfError(f"无法打开 PDF：{exc}") from exc
        try:
            if doc.needs_pass:
                raise PdfError("PDF 已加密")
            sizes = [(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc]
            chars = sum(len(doc[i].get_text()) for i in range(min(doc.page_count, 6)))
            return sizes, dict(doc.metadata or {}), chars
        finally:
            doc.close()


def parse_with_mineru(path: Path, say: Callable[[str], None] | None = None) -> ParsedDoc | None:
    """成功返回 ParsedDoc；MinerU 不可用、扫描件、结果太差都返回 None（调用方走规则解析）。"""
    if not mineru_client.available():
        return None
    try:
        sizes, meta_raw, chars = _probe(path)
        if chars < 200:        # 没有文字层的扫描件：沿用现有 OCR 流程
            return None
        mid = mineru_client.parse_pdf_file(path, say)
        parsed = mineru_adapter.build(mid, sizes, meta_raw)
        if len(parsed.passages) < 5:
            return None
        font_style.annotate_safe(parsed, path)
        return parsed
    except Exception as exc:  # noqa: BLE001  MinerU 只是增强，任何失败都不影响导入
        log.warning("MinerU 解析失败，改用规则解析：%s", exc)
        if say:
            say("MinerU 解析失败，改用内置规则解析")
        return None


def parse_best(path: Path, say: Callable[[str], None] | None = None, **kwargs) -> ParsedDoc:
    parsed = parse_with_mineru(path, say)
    return parsed if parsed is not None else parse_pdf(path, **kwargs)
