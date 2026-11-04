"""截图辅助：把论文里的区域渲染成 PNG。"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from ..context import AppContext
from ..library import repo
from ..parsing.render import RenderError, render_clip
from ..util import ApiError


def paper_pdf_path(app: AppContext, paper_id: str) -> Path:
    detail = repo.get_detail(app, paper_id)
    path = app.paths.paper_dir(paper_id) / "original.pdf"
    if not detail["has_pdf"] or not path.is_file():
        raise ApiError("not_found", "该论文没有可用的 PDF 文件", 404)
    return path


def crop_png(app: AppContext, paper_id: str, page: int, bbox: tuple[float, float, float, float], dpi: int, pad: float = 6.0) -> bytes:
    try:
        return render_clip(paper_pdf_path(app, paper_id), page, bbox, dpi, pad=pad)
    except RenderError as exc:
        raise ApiError("bad_request", str(exc)) from exc


def crop_png_fit(app: AppContext, paper_id: str, page: int, bbox: tuple[float, float, float, float], *,
                 max_side: int = 1800, min_dpi: int = 110, max_dpi: int = 220) -> bytes:
    """按区域大小自适应分辨率：大图降低 dpi 以控制图片 token，小图提高 dpi 以保留细节。"""
    longest_pt = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) + 12.0
    dpi = int(max(min_dpi, min(max_dpi, max_side / (longest_pt / 72.0))))
    return crop_png(app, paper_id, page, bbox, dpi)


def tiny_test_png() -> bytes:
    """连接测试用的 64×64 灰色小图。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
    pix.clear_with(200)
    return pix.tobytes("png")
