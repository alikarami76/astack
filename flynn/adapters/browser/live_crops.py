#!/usr/bin/env python3
"""Crop live examples of components out of browser snaps.

  live_crops.py <system folder> --out <dir> [--max 2]

For each components/<Name>.json with a `live` list ({"snap", "css_class_prefix"}), re-renders
the snap's saved page.html in Chromium (with <base> set to the page's origin, so the site's own
CSS loads), finds visible elements whose class starts with the prefix, and saves a 2x element
screenshot as <out>/<Name>-<n>.png. Writes <out>/crops.json mapping names to files.

Snaps show real seller data: the crops stay local (refs/ is git-ignored). Never upload them.
"""
from __future__ import annotations

import os

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

FLYNN = Path(__file__).resolve().parents[2]
SNAPS = FLYNN / "refs" / "snaps"

FIND_JS = """(prefix) => {
  const out = [];
  for (const el of document.querySelectorAll('[class]')) {
    const cls = typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '');
    if (!cls.split(/\\s+/).some(c => c.startsWith(prefix))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.height < 12 || r.width > 1500) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
    // keep only the outermost match
    let p = el.parentElement, nested = false;
    while (p) { const pc = typeof p.className === 'string' ? p.className : ''; if (pc.split(/\\s+/).some(c => c.startsWith(prefix))) { nested = true; break; } p = p.parentElement; }
    if (nested) continue;
    out.push({x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height});
    if (out.length >= 6) break;
  }
  return out;
}"""


def norm_prefix(raw: str) -> str | None:
    """Turn a spec's css_class_prefix note ("Accordion-<hash> / Accordion__exit", "Chip-<hash> (module)") into a class prefix."""
    m = re.match(r"\s*([A-Za-z][\w-]*?)(<hash>|__|-|\b)", raw or "")
    if not m or raw.strip().startswith(("(", "data-")):
        return None
    base, tail = m.group(1), m.group(2)
    if base[0].islower() and base not in ("btn", "rmdp", "highcharts", "skeleton"):
        return None  # utility classes (text-, color-, bg-) match everything
    if tail == "<hash>":
        return base if base.endswith("-") else base + "-"
    return base + (tail if tail in ("__", "-") else "")


def snap_folder(name: str) -> Path | None:
    hits = sorted(SNAPS.glob(f"*/{name}*"))
    return hits[0] if hits else None


def prepared_html(folder: Path) -> Path:
    meta = json.loads((folder / "meta.json").read_text())
    u = urlsplit(meta["url"])
    html = (folder / "page.html").read_text(encoding="utf-8")
    base = f'<base href="{u.scheme}://{u.netloc}/">'
    html = re.sub(r"<head([^>]*)>", lambda m: f"<head{m.group(1)}>{base}", html, count=1) if "<head" in html else base + html
    out = folder / ".render.html"
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("system")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=2)
    ap.add_argument("--width", type=int, default=1432)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, list[tuple[str, str]]] = {}
    for p in sorted((Path(a.system) / "components").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for L in (d.get("live") or [])[:3]:
            pre = norm_prefix(L.get("css_class_prefix") or "")
            if L.get("snap") and pre and len(pre) > 3:
                wanted.setdefault(L["snap"][:6], []).append((d["name"], pre))
    result: dict[str, list[str]] = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        ctx = b.new_context(viewport={"width": a.width, "height": 900}, device_scale_factor=2, java_script_enabled=False)
        page = ctx.new_page()

        def _font(route):  # the site's fonts refuse file:// origins; add the CORS header ourselves
            try:
                r = route.fetch()
                route.fulfill(response=r, headers={**r.headers, "access-control-allow-origin": "*"})
            except Exception:
                route.continue_()
        page.route(re.compile(r".*\.(woff2?|ttf|otf)(\?.*)?$"), _font)
        for snap, comps in wanted.items():
            folder = snap_folder(snap)
            if not folder:
                print(f"skip {snap}: snap not found", file=sys.stderr)
                continue
            src = prepared_html(folder)
            try:
                page.goto(src.as_uri(), wait_until="networkidle", timeout=45000)
            except Exception as e:  # slow CDN: use what loaded
                print(f"{snap}: {e.__class__.__name__}, using what loaded", file=sys.stderr)
            for name, prefix in comps:
                have = result.setdefault(name, [])
                if len(have) >= a.max:
                    continue
                for box in page.evaluate(FIND_JS, prefix):
                    if len(have) >= a.max:
                        break
                    f = out / f"{name}-{len(have) + 1}.png"
                    page.screenshot(path=str(f), full_page=True, clip={"x": max(box["x"] - 8, 0), "y": max(box["y"] - 8, 0),
                                                                      "width": box["w"] + 16, "height": min(box["h"] + 16, 1400)})
                    have.append(f.name)
            src.unlink(missing_ok=True)
        b.close()
    (out / "crops.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({"components": len(result), "crops": sum(len(v) for v in result.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
