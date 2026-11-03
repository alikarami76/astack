#!/usr/bin/env python3
"""flynn HTML adapter: render a page to a full-page PNG, one PNG per [data-shot] block (email-ready),
a single-page PDF, and layout checks.

  render.py <page/index.html> [--out <dir>] [--width 1040] [--pdf <name>.pdf] [--min-ratio 0.8] [--pad 24]
  render.py <page/index.html> --slides [--out <dir>] [--pdf <name>.pdf]      # decks (templates/deck.html)
  render.py <page/index.html> --pages [--out <dir>] [--pdf <name>.pdf]       # paginated documents (A4 reports, briefs)

Checks (a failed check makes the exit code 1):
  font        the page's first font family loaded (the theme uses "Vazirmatn")
  badImages   images that did not load
  overflow    elements wider than the page (horizontal scroll)
  smallShots  screenshots inside .shot shown below --min-ratio of their capture size. Captures are 2x, so
              ratio = displayed CSS width / (natural width / 2). Below 0.8, 13px product text drops under
              about 11px and stops being readable.
Section PNGs are padded by --pad px (default 24) so heavy glyphs at the edges are not cut. Use --pad 0 for
newsletters: their blocks are stacked edge to edge in the email, so padding would repeat slivers of the
neighbouring blocks.

--slides mode (decks): every .slide (1280x720 CSS px) is captured exactly, with no padding, to
<out>/slides/NN.png at 2x (2560x1440), and the PDF gets one 1280x720 page per slide from the deck's
print CSS (@page size and page breaks). The overflow check also lists every slide whose content spills
out of its 1280x720 box (the slide clips it, so it would silently disappear).

--pages mode (paginated documents, such as dk-mckinsey reports and briefs): every <section class="page">
is one printed page. The page's own print CSS sets the size (@page { size: A4; margin: 0 } and a
page-break after each .page), so the PDF has exactly one page per .page, with real page breaks, text
that stays selectable and the running footer each page carries. Each page is also captured exactly to
<out>/pages/NN.png at 2x. The pageOverflow check lists every page whose content spills out of its box,
and pageCount checks that the PDF has as many pages as the document has .page sections (a mismatch means
a page broke in two or two pages merged)."""
import os
import argparse
import json
import sys
from pathlib import Path

CHECKS = """(minRatio) => {
  const fam = getComputedStyle(document.body).fontFamily.split(',')[0].replace(/["']/g, '').trim();
  const r = {font: document.fonts.check(`20px "${fam}"`), fontFamily: fam, badImages: [], overflow: [], smallShots: []};
  for (const img of document.images) if (!img.complete || img.naturalWidth === 0) r.badImages.push(img.getAttribute('src'));
  const vw = document.documentElement.clientWidth;
  for (const el of document.querySelectorAll('main *')) { const b = el.getBoundingClientRect(); if (b.right > vw + 1 || b.left < -1) r.overflow.push(el.tagName + '.' + el.className); }
  for (const img of document.querySelectorAll('.shot img')) {
    if (!img.naturalWidth) continue;
    const ratio = img.getBoundingClientRect().width / (img.naturalWidth / 2);
    if (ratio < minRatio) r.smallShots.push({src: img.getAttribute('src'), ratio: Math.round(ratio * 100) / 100});
  }
  r.width = document.documentElement.scrollWidth;
  r.height = document.documentElement.scrollHeight;
  r.sections = [...document.querySelectorAll('[data-shot]')].map(s => s.dataset.shot);
  return r; }"""


def render(page: Path, out: Path, width: int = 1040, pdf: str | None = None, min_ratio: float = 0.8, pad: int = 24) -> dict:
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sec-*.png"):
        old.unlink()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = b.new_page(viewport={"width": width, "height": 1200}, device_scale_factor=2)
        pg.goto(page.resolve().as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        r = pg.evaluate(CHECKS, min_ratio)
        pg.screenshot(path=str(out / "full.png"), full_page=True)
        for name in r["sections"]:
            bb = pg.locator(f'[data-shot="{name}"]').first.bounding_box()
            pg.screenshot(path=str(out / f"sec-{name}.png"), full_page=True,
                          clip={"x": max(bb["x"] - pad, 0), "y": max(bb["y"] - pad, 0),
                                "width": min(bb["width"] + 2 * pad, width), "height": bb["height"] + 2 * pad})
        if pdf:
            pg.pdf(path=str(out.parent / pdf), width=f"{width}px", height=f"{r['height'] + 2}px", print_background=True)
        b.close()
    r["ok"] = bool(r["font"] and not r["badImages"] and not r["overflow"] and not r["smallShots"])
    return r


SLIDE_CHECKS = """(sel) => {
  const r = {slideOverflow: [], slides: 0};
  const slides = [...document.querySelectorAll(sel)];
  r.slides = slides.length;
  slides.forEach((sl, i) => {
    const box = sl.getBoundingClientRect();
    const bad = new Set();
    if (sl.scrollHeight > sl.clientHeight + 1 || sl.scrollWidth > sl.clientWidth + 1) bad.add('scroll');
    for (const el of sl.querySelectorAll('*')) {
      const b = el.getBoundingClientRect();
      if (!b.width && !b.height) continue;
      if (b.right > box.right + 1 || b.left < box.left - 1 || b.bottom > box.bottom + 1 || b.top < box.top - 1) bad.add(el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : ''));
    }
    if (bad.size) r.slideOverflow.push({slide: i + 1, shot: sl.dataset.shot || '', elements: [...bad].slice(0, 6)});
  });
  return r; }"""


def render_slides(page: Path, out: Path, pdf: str | None = None, min_ratio: float = 0.8,
                  selector: str = ".slide", folder: str = "slides", viewport: tuple[int, int] = (1344, 800)) -> dict:
    """One exact PNG and one PDF page per `selector` box. Decks use .slide; paginated documents use .page
    (see render_pages)."""
    from playwright.sync_api import sync_playwright
    sd = out / folder
    sd.mkdir(parents=True, exist_ok=True)
    for old in sd.glob("*.png"):
        old.unlink()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = b.new_page(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=2)
        pg.goto(page.resolve().as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        # a box wider than the viewport (a 240 mm page is 907 px) would trip the overflow check
        # and be clipped in the PNG; widen the viewport to the widest box
        need = pg.evaluate("(s) => Math.ceil(Math.max(0, ...[...document.querySelectorAll(s)].map(e => e.getBoundingClientRect().width)))", selector)
        if need > viewport[0]:
            pg.set_viewport_size({"width": need, "height": viewport[1]})
            pg.wait_for_timeout(100)
        r = pg.evaluate(CHECKS, min_ratio)
        r |= pg.evaluate(SLIDE_CHECKS, selector)
        slides = pg.locator(selector)
        r["slide_pngs"] = []
        for i in range(slides.count()):
            path = sd / f"{i + 1:02d}.png"
            slides.nth(i).screenshot(path=str(path))
            r["slide_pngs"].append(str(path))
        if pdf:
            pg.pdf(path=str(out.parent / pdf), prefer_css_page_size=True, print_background=True)
        b.close()
    r["ok"] = bool(r["font"] and not r["badImages"] and not r["overflow"] and not r["smallShots"] and not r["slideOverflow"])
    return r


def _pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except ImportError:
        import re
        return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


def render_pages(page: Path, out: Path, pdf: str | None = None, min_ratio: float = 0.8) -> dict:
    """Paginated documents: one PDF page and one PNG per <section class="page">."""
    r = render_slides(page, out, pdf, min_ratio, selector=".page", folder="pages", viewport=(900, 1200))
    r["pages"] = r.pop("slides")
    r["pageOverflow"] = r.pop("slideOverflow")
    r["page_pngs"] = r.pop("slide_pngs")
    if pdf:
        r["pdfPages"] = _pdf_page_count(out.parent / pdf)
        r["pageCount"] = r["pdfPages"] == r["pages"]
    r["ok"] = bool(r["ok"] and r["pages"] and not r["pageOverflow"] and r.get("pageCount", True))
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--out", help="output folder (default: <page folder>/../shots)")
    ap.add_argument("--width", type=int, default=1040)
    ap.add_argument("--pdf", help="PDF file name, written next to the output folder")
    ap.add_argument("--min-ratio", type=float, default=0.8)
    ap.add_argument("--pad", type=int, default=24, help="padding around section PNGs; 0 for newsletters")
    ap.add_argument("--slides", action="store_true", help="deck mode: one exact PNG and one PDF page per .slide")
    ap.add_argument("--pages", action="store_true", help="paginated mode: one exact PNG and one PDF page per .page")
    a = ap.parse_args(argv)
    page = Path(a.page)
    out = Path(a.out) if a.out else page.resolve().parent.parent / "shots"
    if a.pages:
        r = render_pages(page, out, a.pdf, a.min_ratio)
    elif a.slides:
        r = render_slides(page, out, a.pdf, a.min_ratio)
    else:
        r = render(page, out, a.width, a.pdf, a.min_ratio, a.pad)
    print(json.dumps({k: v for k, v in r.items() if k not in ("sections", "slide_pngs", "page_pngs")} | {"sections": len(r["sections"])}, ensure_ascii=False))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
