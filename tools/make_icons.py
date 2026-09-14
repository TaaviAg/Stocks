"""Render the home-screen icons into static/icons/. Standard library only.

    .venv\\Scripts\\python.exe tools\\make_icons.py

A rising line on the app's blue-to-green gradient. Written as a script rather
than committed as opaque PNGs alone, so the icon can be regenerated or changed
without an image editor, and so no imaging library becomes a dependency.

Four files, because the platforms disagree:
- apple-touch-icon.png (180)  iOS; must be opaque, iOS rounds the corners itself
- icon-192.png, icon-512.png  Android/desktop "any" purpose, rounded here
- icon-maskable-512.png       Android adaptive icons crop to a circle or
                              squircle, so the artwork stays inside the central
                              80% safe zone and the background runs to the edge
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "icons")

TOP_LEFT = (0x2b, 0x6c, 0xb0)      # --accent
BOTTOM_RIGHT = (0x0f, 0x7b, 0x52)  # --up
LINE = (255, 255, 255)

# The line in unit coordinates (0..1), a noisy climb ending high on the right.
POINTS = [(0.18, 0.70), (0.34, 0.56), (0.47, 0.63), (0.63, 0.42), (0.82, 0.28)]


def _png(path, size, rgba):
    raw = b"".join(b"\x00" + bytes(rgba[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render(size, corner, inset):
    """`corner`: corner radius as a fraction of size (0 = square).
    `inset`: scales the artwork toward the centre (1 = as drawn)."""
    pts = [((0.5 + (x - 0.5) * inset) * size, (0.5 + (y - 0.5) * inset) * size)
           for x, y in POINTS]
    half_w = size * 0.045 * inset
    dot_r = size * 0.075 * inset
    r = corner * size
    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            # Rounded-square mask, antialiased over one pixel.
            qx = max(r - cx, 0, cx - (size - r))
            qy = max(r - cy, 0, cy - (size - r))
            alpha = max(0.0, min(1.0, r - math.hypot(qx, qy) + 0.5)) if r else 1.0
            if alpha <= 0:
                continue
            t = (cx + cy) / (2 * size)
            col = [TOP_LEFT[i] + (BOTTOM_RIGHT[i] - TOP_LEFT[i]) * t for i in range(3)]
            d = min(_seg_dist(cx, cy, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
            cover = max(0.0, min(1.0, half_w - d + 0.5))
            dd = math.hypot(cx - pts[-1][0], cy - pts[-1][1])
            cover = max(cover, max(0.0, min(1.0, dot_r - dd + 0.5)))
            col = [c + (LINE[i] - c) * cover for i, c in enumerate(col)]
            o = (y * size + x) * 4
            px[o:o + 4] = bytes([round(col[0]), round(col[1]), round(col[2]), round(alpha * 255)])
    return px


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("apple-touch-icon.png", 180, 0.0, 1.0),
        ("icon-192.png", 192, 0.22, 1.0),
        ("icon-512.png", 512, 0.22, 1.0),
        ("icon-maskable-512.png", 512, 0.0, 0.72),
    ]
    for name, size, corner, inset in jobs:
        _png(os.path.join(OUT, name), size, render(size, corner, inset))
        print("wrote static/icons/%s (%dpx)" % (name, size))


if __name__ == "__main__":
    main()
