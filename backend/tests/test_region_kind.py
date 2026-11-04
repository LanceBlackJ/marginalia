import pymupdf

from paperdesk.parsing.render import region_kind


def _pdf(tmp_path, raster: bool):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    if raster:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
        pix.clear_with(180)
        page.insert_image(pymupdf.Rect(20, 20, 220, 120), pixmap=pix)
    else:
        page.draw_rect(pymupdf.Rect(20, 20, 220, 120), color=(0, 0, 0), fill=(0.8, 0.8, 1))
        page.insert_text((30, 70), "vector label")
    path = tmp_path / ("r.pdf" if raster else "v.pdf")
    doc.save(str(path))
    return path


def test_embedded_bitmap_is_reported_as_raster_with_pixels_and_dpi(tmp_path):
    info = region_kind(_pdf(tmp_path, True), 1, (20, 20, 220, 120))
    assert info["kind"] == "raster" and info["image"]["width"] == 200 and info["image"]["dpi"] == 72


def test_drawn_shapes_are_vector(tmp_path):
    info = region_kind(_pdf(tmp_path, False), 1, (20, 20, 220, 120))
    assert info["kind"] == "vector" and info["image"] is None
