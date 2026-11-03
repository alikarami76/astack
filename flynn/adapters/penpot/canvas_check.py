#!/usr/bin/env python3
"""Check a build on the canvas against the page it came from, so flynn sees its own output before the user does.

  canvas_check.py <run folder>            (the run must have been built: <run>/penpot.json)

For every board flynn built (boards the user edited are skipped), it writes to <run>/canvas-check/:
  <option>-canvas.png   the board as the canvas draws it (the connector's export)
  <option>-page.png     the source page in a browser at the board's width (what the board should look like)
  <option>-diff.png     the canvas picture with the regions that differ outlined in red
and summary.json with findings: layers that failed to build, a height mismatch, regions that differ (boxes in
board pixels, worst first), and the page's own layout checks (fonts that didn't load, overflow, broken images).

The comparison is a coarse, honest signal, not a verdict: it finds missing or shifted blocks, wrong colours,
fallback fonts and overflow; antialiasing differences stay below the threshold. flynn reads the PNGs to judge.
"""
from __future__ import annotations

import os

import argparse
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "html"))

CELL = 24          # comparison grid, in board pixels
CELL_DIFF = 28     # mean absolute difference (0-255) above which a cell counts as different
HEIGHT_TOL = 0.03


TEXTS_JS = """() => {
  const out = [];
  const vis = (el) => { const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity !== 0; };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const t = n.textContent.replace(/\\s+/g, ' ').trim();
    if (!t || !n.parentElement || ['SCRIPT', 'STYLE', 'OPTION'].includes(n.parentElement.tagName)) continue;
    let e = n.parentElement, ok = true;
    for (; e; e = e.parentElement) if (!vis(e)) { ok = false; break; }
    const r = document.createRange(); r.selectNodeContents(n); const b = r.getBoundingClientRect();
    if (ok && b.width > 0.5 && b.height > 0.5) out.push(t);
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (!vis(el) || ['checkbox', 'radio', 'hidden'].includes(el.type)) continue;
    const t = (el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).textContent : (el.value || el.placeholder)) || '';
    if (t.trim()) out.push(t.trim());
  }
  return out; }"""


def _norm(t: str) -> str:
    return re.sub(r"[\s\u200c\u200f\u200e]+", "", t or "")


def missing_texts(page_texts: list[str], canvas_texts: list[str]) -> list[str]:
    """Texts on the page that the board does not have anywhere (whitespace and direction marks ignored)."""
    pool = "\n".join(_norm(t) for t in canvas_texts)
    out = []
    for t in dict.fromkeys(page_texts):
        if _norm(t) and _norm(t) not in pool:
            out.append(t)
    return out


def render_page(page: Path, width: int, out: Path) -> dict:
    """The page at the board's width, full height, 1x (the canvas export is 1x)."""
    from playwright.sync_api import sync_playwright
    import render
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = b.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        pg.goto(page.resolve().as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        checks = pg.evaluate(render.CHECKS, 0.8)
        checks["texts"] = pg.evaluate(TEXTS_JS)
        pg.screenshot(path=str(out), full_page=True)
        b.close()
    return {k: checks.get(k) for k in ("font", "fontFamily", "badImages", "overflow", "width", "height", "texts")}


def compare(canvas_png: bytes, page_png: Path, diff_out: Path) -> dict:
    """Grid comparison of the canvas board and the page picture, both scaled to the page's width."""
    from PIL import Image, ImageChops, ImageDraw, ImageStat
    a = Image.open(io.BytesIO(canvas_png)).convert("RGB")
    b = Image.open(page_png).convert("RGB")
    if a.width != b.width:
        a = a.resize((b.width, round(a.height * b.width / a.width)))
    h = min(a.height, b.height)
    res = {"canvas_h": a.height, "page_h": b.height, "width": b.width,
           "height_mismatch": abs(a.height - b.height) / max(b.height, 1) > HEIGHT_TOL}
    diff = ImageChops.difference(a.crop((0, 0, b.width, h)), b.crop((0, 0, b.width, h))).convert("L")
    cells = []
    for y in range(0, h, CELL):
        for x in range(0, b.width, CELL):
            box = (x, y, min(x + CELL, b.width), min(y + CELL, h))
            m = ImageStat.Stat(diff.crop(box)).mean[0]
            if m > CELL_DIFF:
                cells.append((box, m))
    # merge neighbouring cells into regions (simple flood over the grid)
    grid = {(c[0][0] // CELL, c[0][1] // CELL): c for c in cells}
    seen, regions = set(), []
    for key in grid:
        if key in seen:
            continue
        stack, members = [key], []
        seen.add(key)
        while stack:
            k = stack.pop()
            members.append(grid[k])
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (k[0] + dx, k[1] + dy)
                if n in grid and n not in seen:
                    seen.add(n)
                    stack.append(n)
        x0 = min(m[0][0] for m in members); y0 = min(m[0][1] for m in members)
        x1 = max(m[0][2] for m in members); y1 = max(m[0][3] for m in members)
        regions.append({"box": [x0, y0, x1, y1], "cells": len(members),
                        "strength": round(sum(m[1] for m in members) / len(members))})
    regions.sort(key=lambda r: r["cells"] * r["strength"], reverse=True)
    total = max(1, ((b.width + CELL - 1) // CELL) * ((h + CELL - 1) // CELL))
    res["differs_pct"] = round(100 * len(cells) / total, 1)
    res["regions"] = regions[:12]
    out = a.copy()
    d = ImageDraw.Draw(out)
    for r in regions[:12]:
        d.rectangle(r["box"], outline=(230, 30, 30), width=3)
    if a.height < b.height:
        d.rectangle((0, a.height - 4, b.width, a.height), fill=(230, 30, 30))
    out.save(diff_out)
    return res


def canvas_for(built: dict):
    """flynn's builder browser on the run's file (it never depends on which file the user has open); the user's tab if the
    builder can't reach it."""
    from bridge import Bridge
    if built.get("file"):
        try:
            sys.path.insert(0, str(HERE.parent / "library"))
            import builder
            return builder.open_file(built["file"], built.get("team"))
        except Exception:  # noqa: BLE001
            pass
    return Bridge()


def check(run: Path, bridge=None) -> dict:
    from bridge import Bridge, PenpotError
    run = Path(run).resolve()
    try:
        built = json.loads((run / "penpot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PenpotError(f"{run.name} has not been built on the canvas yet")
    if bridge is None:
        bridge = canvas_for(built)
    out_dir = run / "canvas-check"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    summary = {"run": run.name, "page": built.get("pageName"), "boards": {}, "findings": [],
               "build": {"errors": len(built.get("errors") or []), "first_errors": (built.get("errors") or [])[:5],
                         "warnings": (built.get("warnings") or [])[:5], "saved": built.get("saved"),
                         "library": built.get("library")}}
    if summary["build"]["errors"]:
        summary["findings"].append(f"{summary['build']['errors']} layers failed to build on the canvas "
                                   f"(first: {summary['build']['first_errors'][:2]})")
    for name, b in (built.get("boards") or {}).items():
        if b.get("edited"):
            summary["boards"][name] = {"skipped": "the user edited this board on the canvas"}
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "-", b.get("option") or name).strip("-")[:40] or "board"
        rec: dict = {"option": b.get("option"), "built": b.get("built"), "total": b.get("total")}
        expected = (b.get("total") or 0) - (b.get("in_library") or 0)    # layers drawn by library components
        if b.get("total") and "in_library" in b and b.get("built", 0) < expected:
            summary["findings"].append(f"{b.get('option')}: only {b['built']} of {expected} layers were built")
        try:
            png = bridge.export_png(b["id"])
        except PenpotError as e:
            rec["error"] = f"could not export the board: {e}"
            summary["findings"].append(f"{b.get('option')}: the board is missing or could not be exported")
            summary["boards"][name] = rec
            continue
        (out_dir / f"{slug}-canvas.png").write_bytes(png)
        rec["canvas"] = f"canvas-check/{slug}-canvas.png"
        src = run / (b.get("source") or "")
        if src.is_file():
            try:
                rec["page_checks"] = render_page(src, int(b.get("w") or 1440), out_dir / f"{slug}-page.png")
                rec["page"] = f"canvas-check/{slug}-page.png"
                cmp = compare(png, out_dir / f"{slug}-page.png", out_dir / f"{slug}-diff.png")
                rec["compare"] = cmp
                rec["diff"] = f"canvas-check/{slug}-diff.png"
                pc = rec["page_checks"]
                import push
                read = bridge.run_js(push._js(push.READ_JS, ID=b["id"])) or {}
                miss = missing_texts(pc.pop("texts", None) or [], [x.get("text", "") for x in read.get("texts") or []])
                rec["missing_texts"] = miss[:40]
                if miss:
                    summary["findings"].append(f"{b.get('option')}: {len(miss)} texts on the page are missing on the "
                                               f"board, e.g. {miss[:5]}")
                if pc.get("font") is False:
                    summary["findings"].append(f"{b.get('option')}: the page's font {pc.get('fontFamily')} did not load")
                if pc.get("overflow"):
                    summary["findings"].append(f"{b.get('option')}: page content overflows sideways ({pc['overflow'][:3]})")
                if pc.get("badImages"):
                    summary["findings"].append(f"{b.get('option')}: images did not load ({pc['badImages'][:3]})")
                if cmp["height_mismatch"]:
                    summary["findings"].append(f"{b.get('option')}: the board is {cmp['canvas_h']}px tall, the page "
                                               f"{cmp['page_h']}px: content is missing or spaced differently")
                if cmp["regions"]:
                    top = cmp["regions"][0]["box"]
                    summary["findings"].append(f"{b.get('option')}: {cmp['differs_pct']}% of the board differs from "
                                               f"the page; biggest region at x{top[0]}-{top[2]}, y{top[1]}-{top[3]} "
                                               f"(see {rec['diff']})")
            except Exception as e:  # noqa: BLE001 - the canvas picture alone is still useful
                rec["page_error"] = f"{type(e).__name__}: {e}"[:300]
        summary["boards"][name] = rec
    summary["ok"] = not summary["findings"]
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    a = ap.parse_args(argv)
    from bridge import PenpotError
    try:
        s = check(Path(a.run))
    except PenpotError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({k: s[k] for k in ("ok", "page", "findings", "build")}, ensure_ascii=False, indent=2))
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
