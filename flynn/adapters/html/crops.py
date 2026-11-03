#!/usr/bin/env python3
"""flynn HTML adapter: crop product screenshots to the region a caption talks about and place numbered
highlight boxes on the exact spot.

  crops.py <spec.json> --img <image folder> [--display 980]      # writes crop-<name>.png, prints the markup
  crops.py --grid <image.png> <out.png> [x0,y0,x1,y1]            # % grid overlay for choosing boxes

spec.json maps a source image name (without .png) to
  {"crop": [x0, y0, x1, y1], "boxes": [[x0, y0, x1, y1, "۱"], ...], "corner": {"۱": "tl"}, "fade": "l"}
All numbers are % of the ORIGINAL image; boxes are converted to % of the crop. "corner": "tl" puts a
badge on the top-left corner (default top-right); use it when text sits to the badge's right. "fade": "l"
fades the left edge, for scroll strips cut by the crop.

Rules the crop must follow (lessons from 2026-09-30):
  - cut on whitespace or row boundaries, never through a line of text;
  - boxes wrap only the target element and must not cover neighbouring words;
  - at the display width the crop must keep ratio >= 0.8 (captures are 2x); the script warns below it."""
import argparse
import json
import sys
from pathlib import Path


def box_to_crop(crop, box):
    """Convert a box given in % of the original image into % of the crop."""
    x0, y0, x1, y1 = crop
    bx0, by0, bx1, by1 = box[:4]
    cw, ch = x1 - x0, y1 - y0
    return {"l": round((bx0 - x0) / cw * 100, 2), "t": round((by0 - y0) / ch * 100, 2),
            "w": round((bx1 - bx0) / cw * 100, 2), "h": round((by1 - by0) / ch * 100, 2)}


def ratio(native_px: int, display_css: int) -> float:
    """Display ratio of a 2x capture shown at display_css CSS px (never above 1, since it is capped by max-width)."""
    return round(min(1.0, display_css / (native_px / 2)), 2)


def markup(name: str, spec: dict, alt: str = "") -> str:
    boxes = []
    for b in spec.get("boxes", []):
        pos = box_to_crop(spec["crop"], b)
        corner = " tl" if spec.get("corner", {}).get(b[4]) == "tl" else ""
        boxes.append(f'<div class="hl{corner}" style="left:{pos["l"]}%;top:{pos["t"]}%;width:{pos["w"]}%;height:{pos["h"]}%"><span>{b[4]}</span></div>')
    fade = " fade-l" if spec.get("fade") == "l" else ""
    return f'<div class="shot{fade}"><img src="img/crop-{name}.png" alt="{alt}">{"".join(boxes)}</div>'


def crop_all(spec_path: Path, img_dir: Path, display: int) -> dict:
    from PIL import Image
    specs = json.loads(spec_path.read_text(encoding="utf-8"))
    out = {}
    for name, spec in specs.items():
        im = Image.open(img_dir / f"{name}.png")
        w, h = im.size
        x0, y0, x1, y1 = spec["crop"]
        c = im.crop((round(w * x0 / 100), round(h * y0 / 100), round(w * x1 / 100), round(h * y1 / 100)))
        c.save(img_dir / f"crop-{name}.png", optimize=True)
        shown = min(display, spec.get("max_width", display))
        r = ratio(c.size[0], shown)
        out[name] = {"markup": markup(name, spec, spec.get("alt", "")), "size": c.size, "ratio": r}
        if r < 0.8:
            print(f"warning: {name} shows at ratio {r} at {shown}px; crop tighter or show it narrower", file=sys.stderr)
    return out


def grid(src: str, dst: str, region=(0, 0, 100, 100)) -> None:
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = region
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im = im.crop((int(w * x0 / 100), int(h * y0 / 100), int(w * x1 / 100), int(h * y1 / 100)))
    sc = min(1.0, 1600 / im.size[0])
    im = im.resize((int(im.size[0] * sc), int(im.size[1] * sc)))
    d = ImageDraw.Draw(im)
    step = 5 if (x1 - x0) > 40 else 2
    for p in range(0, 101, step):
        if x0 <= p <= x1:
            X = (p - x0) / (x1 - x0) * im.size[0]
            d.line([(X, 0), (X, im.size[1])], fill=(0, 160, 255), width=1)
            d.text((X + 2, 2), str(p), fill=(0, 90, 200))
        if y0 <= p <= y1:
            Y = (p - y0) / (y1 - y0) * im.size[1]
            d.line([(0, Y), (im.size[0], Y)], fill=(255, 120, 0), width=1)
            d.text((2, Y + 2), str(p), fill=(200, 80, 0))
    im.save(dst)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--img")
    ap.add_argument("--display", type=int, default=980)
    ap.add_argument("--grid", nargs="+", metavar="ARG")
    a = ap.parse_args(argv)
    if a.grid:
        region = tuple(float(v) for v in a.grid[2].split(",")) if len(a.grid) > 2 else (0, 0, 100, 100)
        grid(a.grid[0], a.grid[1], region)
        return 0
    if not (a.spec and a.img):
        ap.error("give <spec.json> --img <folder>, or --grid")
    print(json.dumps(crop_all(Path(a.spec), Path(a.img), a.display), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
