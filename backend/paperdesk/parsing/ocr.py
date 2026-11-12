"""扫描页的本地离线 OCR（RapidOCR / PP-OCR，ONNX Runtime）。

- 完全在本机运行：页面图像不会发往云端；模型文件打包在 rapidocr-onnxruntime 里（约 14 MB），不需要联网下载；
- 只处理没有文字层的“扫描页”（见 pdf_parser._scan_pages）；混合型文档按页处理；
- 识别结果转成行级 Span，交给现有的解析流程（分栏、标题、段落切分都沿用 layout.py），
  OCR 页的段落在库里标注 source='ocr'；
- 渲染用 render.render_clip（与解析共用 PyMuPDF 锁），识别本身在锁外进行。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pymupdf

from .layout import Span
from .pdf_parser import _PARSE_LOCK
from .render import render_clip

DPI = 150  # 实测 110–150 dpi 的词召回/精度最好，更高分辨率不会更准，只会更慢
MIN_SCORE = 0.35  # 置信度低于此值的行直接丢弃（多半是噪点）

# 识别函数：PNG 字节 → [(四点多边形像素坐标, 文本, 置信度)]。测试可以注入假引擎。
OcrFn = Callable[[bytes], "list[tuple[list[list[float]], str, float]]"]

_engine: OcrFn | None = None
_override: OcrFn | None = None
_lock = threading.Lock()


class OcrUnavailable(Exception):
    """本地 OCR 组件没有安装。"""


def set_engine(fn: OcrFn | None) -> None:
    """仅供测试：注入识别函数（None 恢复真实引擎）。"""
    global _override
    _override = fn


def available() -> bool:
    if _override is not None:
        return True
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _get_engine() -> OcrFn:
    global _engine
    if _override is not None:
        return _override
    with _lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception as exc:  # noqa: BLE001
                raise OcrUnavailable("没有安装本地 OCR 组件（rapidocr-onnxruntime）。") from exc
            eng = RapidOCR()

            def run(png: bytes) -> list:
                res, _ = eng(png)
                return [(r[0], str(r[1]), float(r[2])) for r in (res or [])]

            _engine = run
        return _engine


def _page_geometry(path: Path, page_no: int) -> tuple[pymupdf.Matrix, pymupdf.Rect]:
    """(显示坐标→原生坐标的反旋转矩阵, 显示坐标下的页面矩形)。"""
    with _PARSE_LOCK:
        doc = pymupdf.open(str(path))
        try:
            page = doc[page_no - 1]
            return pymupdf.Matrix(page.derotation_matrix), pymupdf.Rect(page.rect)
        finally:
            doc.close()


def ocr_page(path: Path, page_no: int) -> list[Span]:
    """识别第 page_no 页（1 起），返回行级 Span（原生页面坐标，与 extract_spans 一致）。"""
    derot, rect = _page_geometry(path, page_no)
    png = render_clip(path, page_no, None, dpi=DPI, pad=0)
    scale = DPI / 72.0
    # render_clip 可能为限制尺寸而缩小：用实际像素尺寸反推比例
    try:
        pix = pymupdf.Pixmap(png)
        scale_x, scale_y = pix.width / rect.width, pix.height / rect.height
    except Exception:  # noqa: BLE001
        scale_x = scale_y = scale
    spans: list[Span] = []
    for poly, text, score in _get_engine()(png):
        text = (text or "").strip()
        if not text or score < MIN_SCORE:
            continue
        pts = [pymupdf.Point(p[0] / scale_x, p[1] / scale_y) * derot for p in poly]
        x0, x1 = min(p.x for p in pts), max(p.x for p in pts)
        y0, y1 = min(p.y for p in pts), max(p.y for p in pts)
        h = max(1.0, y1 - y0)
        if len(text) >= 6 and h > 2.5 * max(1.0, x1 - x0):
            continue  # 竖排的页边戳记（arXiv 编号、期刊水印）：框比宽高得多，不是正文，留着会被当成超大字号的标题
        spans.append(Span(x0=x0, y0=y0, x1=x1, y1=y1, baseline=y1 - 0.2 * h, text=text, size=round(h * 0.82, 2),
                          bold=False, italic=False, font="ocr", math=False))
    _normalise_sizes(spans)
    return spans


def _normalise_sizes(spans: list[Span]) -> None:
    """OCR 的行框高度有抖动（上标、下标、字母高低不一）。行高接近本页中位数的行统一成正文字号，
    只有明显更大的行（标题、题名）才保留大字号，避免把普通行误判成章节标题。"""
    if len(spans) < 5:
        return
    heights = sorted(s.y1 - s.y0 for s in spans if len(s.text) >= 12) or sorted(s.y1 - s.y0 for s in spans)
    med = heights[len(heights) // 2]
    for s in spans:
        if (s.y1 - s.y0) <= 1.28 * med:
            s.size = round(med * 0.82, 2)
