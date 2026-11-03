#!/usr/bin/env python3
"""Turn an image into a halftone dot SVG (one circle per cell, radius scaled by brightness).

    python3 halftone.py <src> -o out.svg --cell 8 --color "#4FD1FF" [--crop x,y,w,h] [--width N]
"""
import argparse
import sys

from PIL import Image


def _num(v):
    return f"{round(v, 1):.1f}"


def halftone_svg(src, cell=8, color="#FFFFFF", threshold=0.08, crop=None, width=None):
    img = Image.open(src).convert("L")
    if crop:
        x, y, w, h = crop
        img = img.crop((x, y, x + w, y + h))
    if width and width != img.width:
        img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
    W, H = img.size
    px = img.load()
    circles = []
    for cy in range(0, H, cell):
        for cx in range(0, W, cell):
            x1, y1 = min(cx + cell, W), min(cy + cell, H)
            total = sum(px[i, j] for j in range(cy, y1) for i in range(cx, x1))
            b = total / ((x1 - cx) * (y1 - cy)) / 255.0
            if b > threshold:
                circles.append(
                    f'<circle cx="{_num(cx + cell / 2)}" cy="{_num(cy + cell / 2)}" r="{_num(cell / 2 * b)}"/>'
                )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
        f'<g fill="{color}">{"".join(circles)}</g></svg>'
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--cell", type=int, default=8)
    ap.add_argument("--color", default="#FFFFFF")
    ap.add_argument("--threshold", type=float, default=0.08)
    ap.add_argument("--crop", help="x,y,w,h")
    ap.add_argument("--width", type=int)
    a = ap.parse_args(argv)
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    if crop and len(crop) != 4:
        ap.error("--crop needs x,y,w,h")
    svg = halftone_svg(a.src, a.cell, a.color, a.threshold, crop, a.width)
    with open(a.out, "w") as f:
        f.write(svg)
    print(f"{a.out}: {svg.count('<circle')} dots")


if __name__ == "__main__":
    sys.exit(main())
