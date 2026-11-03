#!/usr/bin/env python3
"""Capture a rendered flynn page as a node tree for Penpot.

  pagetree.py <page/index.html> [--width 1440] [--out tree.json]

Renders the page in headless Chromium (Playwright), waits for fonts, runs html2pnode.js and returns
{"w", "h", "background", "nodes": [...], "images": {src: {"mime", "b64"}}, "warnings"}.
Images are read from local files (file://) or fetched through the page; images over 8 MB are skipped."""
from __future__ import annotations

import os

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
WALKER = (HERE / "html2pnode.js").read_text(encoding="utf-8")
MAX_IMAGE = 8 * 1024 * 1024


def _image_bytes(page, src: str) -> tuple[bytes, str] | None:
    u = urlparse(src)
    if u.scheme == "file":
        p = Path(unquote(u.path))
        if p.is_file() and p.stat().st_size <= MAX_IMAGE:
            return p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"
        return None
    if u.scheme == "data":
        head, _, data = src.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return (base64.b64decode(data) if ";base64" in head else unquote(data).encode()), mime
    if u.scheme in ("http", "https"):
        try:
            r = page.request.get(src, timeout=15000)
            if r.ok and len(r.body()) <= MAX_IMAGE:
                return r.body(), (r.headers.get("content-type") or "image/png").split(";")[0]
        except Exception:
            return None
    return None


def capture(page_path: Path, width: int = 1440) -> dict:
    from playwright.sync_api import sync_playwright
    page_path = Path(page_path).resolve()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        page.goto(page_path.as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(300)
        # Make the window as tall as the page, so fixed elements sit where a full-page screenshot shows them.
        full_h = page.evaluate("Math.ceil(Math.max(document.documentElement.scrollHeight, document.body.scrollHeight))")
        page.set_viewport_size({"width": width, "height": max(900, min(full_h, 20000))})
        page.wait_for_timeout(200)
        tree = page.evaluate(WALKER)
        # deck pages: each .slide becomes its own board on the canvas (see push.split_slides)
        tree["slides"] = page.evaluate("""() => [...document.querySelectorAll('.slide')].map((el, i) => {
            const r = el.getBoundingClientRect();
            const h = el.querySelector('h1, h2, .slide-title');
            return { i, x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height, title: h ? h.textContent.trim().slice(0, 60) : '' }; })""")
        images = {}
        for n in tree["nodes"]:
            if n["type"] == "image" and n["src"] not in images:
                got = _image_bytes(page, n["src"])
                if got:
                    images[n["src"]] = {"mime": got[1], "b64": base64.b64encode(got[0]).decode()}
                else:
                    tree["warnings"].append(f"image not loaded: {n['src'][:120]}")
        browser.close()
    tree["images"] = images
    tree["source"] = str(page_path)
    return tree


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    tree = capture(Path(a.page), a.width)
    text = json.dumps(tree, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    counts = {}
    for n in tree["nodes"]:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    print(json.dumps({"w": tree["w"], "h": tree["h"], "nodes": counts, "images": len(tree["images"]), "warnings": tree["warnings"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
