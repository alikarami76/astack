#!/usr/bin/env python3
"""Optional accessibility check for flynn HTML pages: runs axe-core in the Playwright Chromium that render.py
already uses, and reports WCAG 2.1 A and AA violations.

  a11y_check.py <page/index.html> [--width 1440] [--axe <axe.min.js>] [--json]

axe-core is not bundled with flynn. The script looks for axe.min.js in --axe, then $FLYNN_AXE_JS, then
~/.flynn/vendor/axe.min.js. When none exists, or Playwright is not installed, it reports the check as
"skipped" with the reason and exits 0, so a run never fails because the optional tool is missing.

Result: "fail" (exit 1) when any violation has impact "serious" or "critical"; otherwise "pass" (exit 0), with
minor and moderate violations listed in the detail. Each violation names the rule, its impact, how many
elements it hit, a few CSS targets, and, on Admin Panel kit pages, the data-uikit component it sits in:
those come from the panel's own markup and are notes for the developers, not something to redesign."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
SEVERE = {"serious", "critical"}

RUN = """async (tags) => {
  const r = await window.axe.run(document, {runOnly: {type: 'tag', values: tags}});
  return r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help, count: v.nodes.length,
    targets: v.nodes.slice(0, 3).map(n => String(n.target && n.target[0])),
    components: [...new Set(v.nodes.map(n => { try { const el = document.querySelector(n.target[0]);
      const c = el && el.closest('[data-uikit]'); return c ? c.getAttribute('data-uikit') : null; } catch (e) { return null; } })
      .filter(Boolean))]}));
}"""


def find_axe(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for c in (os.environ.get("FLYNN_AXE_JS"), str(Path.home() / ".flynn" / "vendor" / "axe.min.js")):
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


def skipped(page: Path, why: str) -> dict:
    return {"check": "a11y.axe", "file": str(page), "result": "skipped", "detail": why, "violations": []}


def check(page: Path, width: int = 1440, axe: str | None = None) -> dict:
    js = find_axe(axe)
    if js is None:
        return skipped(page, "axe-core not found (looked in --axe, $FLYNN_AXE_JS and ~/.flynn/vendor/axe.min.js); "
                             "optional check skipped")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return skipped(page, "Playwright is not installed; optional check skipped")
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": width, "height": 900})
            pg.goto(page.resolve().as_uri())
            pg.wait_for_load_state("networkidle")
            pg.add_script_tag(path=str(js))
            violations = pg.evaluate(RUN, TAGS)
        finally:
            b.close()
    severe = [v for v in violations if v.get("impact") in SEVERE]
    detail = "; ".join(f"{v['id']} ({v['impact']}, {v['count']})" for v in violations) or "no WCAG A/AA violations"
    return {"check": "a11y.axe", "file": str(page), "result": "fail" if severe else "pass", "detail": detail,
            "violations": violations}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--axe", help="path to axe.min.js")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check(Path(a.page), a.width, a.axe)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"{r['check']}: {r['result']} - {r['detail']}")
        for v in r["violations"]:
            where = f" in {', '.join(v['components'])}" if v.get("components") else ""
            print(f"  {v['impact']}: {v['id']} x{v['count']}{where} - {v['help']} - {', '.join(v['targets'])}")
    return 1 if r["result"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
