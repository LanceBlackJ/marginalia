from __future__ import annotations

import io

import pymupdf
import pytest

from paperdesk.parsing import render


def _pdf(tmp_path, rotate: int, n_lines: int = 6):
    p = tmp_path / f"r{rotate}.pdf"
    d = pymupdf.open()
    pg = d.new_page()
    for i in range(n_lines):
        pg.insert_text((100 + i * 14, 700) if rotate else (60, 100 + i * 14), "Evaluation of the discovered formula", fontsize=9, rotate=rotate)
    d.save(p)
    return p


def test_text_rotation_detects_sideways_tables(tmp_path):
    for rot, expect in ((0, 0), (90, 90), (270, 270)):
        pg = pymupdf.open(_pdf(tmp_path, rot))[0]
        assert render.text_rotation(pg, pymupdf.Rect(0, 0, 595, 842)) == expect, rot


def test_render_clip_turns_a_sideways_region_upright(tmp_path):
    p = _pdf(tmp_path, 90)
    png = render.render_clip(p, 1, (90, 300, 200, 700), 72, pad=2)
    pix = pymupdf.Pixmap(io.BytesIO(png))
    assert pix.width > pix.height  # 竖着排的区域转正后变成横的
    plain = render.render_clip(p, 1, (90, 300, 200, 700), 72, pad=2, upright=False)
    assert pymupdf.Pixmap(io.BytesIO(plain)).width < pymupdf.Pixmap(io.BytesIO(plain)).height


def test_render_svg_defers_sideways_regions_to_upright_png(tmp_path):
    p = _pdf(tmp_path, 90)
    with pytest.raises(render.RenderError, match="转正"):
        render.render_svg(p, 1, (90, 300, 200, 700), pad=2)


def test_render_clip_turns_a_rotated_raster_region_upright(tmp_path):
    p = tmp_path / "raster.pdf"
    d = pymupdf.open()
    page = d.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pix.clear_with(0xFFFFFF)
    page.insert_image(pymupdf.Rect(100, 100, 300, 200), pixmap=pix, rotate=90)
    d.save(p)
    bbox = (170, 95, 230, 205)
    upright = pymupdf.Pixmap(io.BytesIO(render.render_clip(p, 1, bbox, 72, pad=0)))
    sideways = pymupdf.Pixmap(io.BytesIO(render.render_clip(p, 1, bbox, 72, pad=0, upright=False)))
    assert upright.width > upright.height
    assert sideways.width < sideways.height


def test_a_few_vertical_words_do_not_rotate_a_normal_region(tmp_path):
    p = tmp_path / "mix.pdf"
    d = pymupdf.open()
    pg = d.new_page()
    for i in range(6):
        pg.insert_text((60, 100 + i * 14), "A perfectly normal horizontal line of text here", fontsize=9)
    pg.insert_text((30, 300), "y axis", fontsize=9, rotate=90)
    d.save(p)
    assert render.text_rotation(pymupdf.open(p)[0], pymupdf.Rect(0, 0, 595, 842)) == 0
