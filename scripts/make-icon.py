"""生成 Marginalia 应用图标（原创的黑白图形：黑色圆角方块里一张折角白纸和几行文字线）。

用法：backend\\.venv\\Scripts\\python.exe scripts\\make-icon.py
输出：build/icon.ico（多尺寸 PNG 内嵌）与 build/icon.png（512px，供安装包与窗口使用）。
只依赖 numpy 与标准库。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "build"
SS = 4  # 超采样倍数，用来抗锯齿


def render(size: int) -> np.ndarray:
    n = size * SS
    ys, xs = np.mgrid[0:n, 0:n]
    x = (xs + 0.5) / n
    y = (ys + 0.5) / n
    img = np.zeros((n, n), dtype=np.float32)  # 1 = 白，0 = 黑

    # 外形：黑色圆角方块
    r = 0.20
    cx = np.clip(x, r, 1 - r)
    cy = np.clip(y, r, 1 - r)
    inside = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    alpha = inside.astype(np.float32)

    # 白纸：竖向矩形，右上角被折掉
    x0, x1, y0, y1, fold = 0.27, 0.73, 0.17, 0.83, 0.16
    sheet = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    cut = (x - (x1 - fold)) + ((y0 + fold) - y) < 0  # 右上角斜切
    corner = (x > x1 - fold) & (y < y0 + fold)
    sheet &= ~(corner & ~cut)
    img[sheet] = 1.0

    # 折角：一个黑色小三角的描边，让纸角有折叠的感觉
    tri = corner & cut & (x >= x1 - fold) & (y <= y0 + fold)
    edge = tri & ((x - (x1 - fold)) + ((y0 + fold) - y) < 0.045)
    img[edge] = 0.0

    # 文字线
    for yy, w in ((0.42, 0.34), (0.54, 0.34), (0.66, 0.22)):
        line = (x >= 0.35) & (x <= 0.35 + w) & (y >= yy - 0.017) & (y <= yy + 0.017)
        img[line & sheet] = 0.0

    gray = img.reshape(size, SS, size, SS).mean(axis=(1, 3))
    a = alpha.reshape(size, SS, size, SS).mean(axis=(1, 3))
    v = (gray * 255).round().astype(np.uint8)
    rgba = np.stack([v, v, v, (a * 255).round().astype(np.uint8)], axis=-1)
    return rgba


def png_bytes(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [256, 128, 64, 48, 32, 16]
    pngs = [png_bytes(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries, offset = b"", 6 + 16 * len(sizes)
    for s, p in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", 0 if s == 256 else s, 0 if s == 256 else s, 0, 0, 1, 32, len(p), offset)
        offset += len(p)
    (OUT / "icon.ico").write_bytes(header + entries + b"".join(pngs))
    (OUT / "icon.png").write_bytes(png_bytes(render(512)))
    print("已生成", OUT / "icon.ico", "和", OUT / "icon.png")


if __name__ == "__main__":
    main()
