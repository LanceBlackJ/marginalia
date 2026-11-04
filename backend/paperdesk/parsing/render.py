"""把 PDF 页面或区域渲染成 PNG（供视觉模型识别公式/框架图/表格，或前端预览）。

与解析共用同一把 PyMuPDF 锁：MuPDF 不支持多线程并发。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .pdf_parser import _PARSE_LOCK as PDF_LOCK

MAX_SIDE_PX = 2600


class RenderError(Exception):
    pass


def render_clip(path: Path, page_no: int, bbox: tuple[float, float, float, float] | None, dpi: int = 150, pad: float = 3.0) -> bytes:
    """渲染第 page_no 页（1 起）的 bbox 区域（显示坐标，PDF point，左上角原点）；bbox 为 None 表示整页。"""
    with PDF_LOCK:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise RenderError("无法打开 PDF 文件") from exc
        try:
            if not 1 <= page_no <= doc.page_count:
                raise RenderError("页码超出范围")
            page = doc[page_no - 1]
            rect = page.rect
            if bbox is not None:
                x0, y0, x1, y1 = bbox
                clip = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & rect
                if clip.is_empty or clip.width < 2 or clip.height < 2:
                    raise RenderError("区域无效或超出页面")
            else:
                clip = rect
            # 限制输出尺寸，避免一次渲染出超大图片
            scale = dpi / 72.0
            longest = max(clip.width, clip.height) * scale
            if longest > MAX_SIDE_PX:
                scale *= MAX_SIDE_PX / longest
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()


MAX_SVG_BYTES = 14_000_000


def render_svg(path: Path, page_no: int, bbox: tuple[float, float, float, float], pad: float = 3.0) -> str:
    """把某页的一个区域导出成矢量图（SVG），而不是截图：线条和文字是真正的矢量，嵌入的位图保持原始分辨率，放大不会糊。
    文字转成路径，所以不依赖浏览器里有没有 PDF 里的字体。bbox 是显示坐标（PDF point，左上角原点）。"""
    with PDF_LOCK:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise RenderError("无法打开 PDF 文件") from exc
        try:
            if not 1 <= page_no <= doc.page_count:
                raise RenderError("页码超出范围")
            page = doc[page_no - 1]
            x0, y0, x1, y1 = bbox
            clip = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
            if clip.is_empty or clip.width < 2 or clip.height < 2:
                raise RenderError("区域无效或超出页面")
            try:
                svg = page.get_svg_image(text_as_path=True)
            except Exception as exc:  # noqa: BLE001
                raise RenderError("这张图无法导出为矢量图") from exc
        finally:
            doc.close()
    head_end = svg.find(">", svg.find("<svg"))
    if head_end < 0:
        raise RenderError("这张图无法导出为矢量图")
    root = svg[: head_end + 1]
    import re as _re

    root = _re.sub(r'\s(width|height|viewBox)="[^"]*"', "", root)
    root = root[: -1] + f' width="{clip.width:.2f}" height="{clip.height:.2f}" viewBox="{clip.x0:.2f} {clip.y0:.2f} {clip.width:.2f} {clip.height:.2f}">'
    out = root + svg[head_end + 1:]
    if len(out) > MAX_SVG_BYTES:
        raise RenderError("这张图太复杂，不导出矢量图")
    return out


def region_kind(path: Path, page_no: int, bbox: tuple[float, float, float, float]) -> dict:
    """区域里的图是矢量还是位图：看嵌入的位图占区域的多大比例、最大那张的像素和等效 dpi。
    kind：vector（没有位图）、raster（位图占大部分，放大必糊）、mixed（有位图，也有矢量线条/文字）。"""
    with PDF_LOCK:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise RenderError("无法打开 PDF 文件") from exc
        try:
            if not 1 <= page_no <= doc.page_count:
                raise RenderError("页码超出范围")
            page = doc[page_no - 1]
            clip = pymupdf.Rect(*bbox) & page.rect
            if clip.is_empty:
                raise RenderError("区域无效或超出页面")
            covered = 0.0
            biggest: dict | None = None
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"]) & clip
                if r.is_empty or r.get_area() < 0.04 * clip.get_area():
                    continue
                covered += r.get_area()
                w, h = int(info.get("width", 0)), int(info.get("height", 0))
                full = pymupdf.Rect(info["bbox"])
                dpi = round(w / max(full.width, 1.0) * 72) if w else 0
                if biggest is None or w * h > biggest["width"] * biggest["height"]:
                    biggest = {"width": w, "height": h, "dpi": dpi}
            share = min(1.0, covered / max(clip.get_area(), 1.0))
        finally:
            doc.close()
    kind = "vector" if biggest is None else ("raster" if share >= 0.6 else "mixed")
    return {"kind": kind, "raster_share": round(share, 2), "image": biggest}
