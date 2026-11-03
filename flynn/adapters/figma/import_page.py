"""Build an imported Figma page on the canvas (flynn desk) as real, editable elements.

flynn (the agent) does the reading: through the user's Figma connection it checks the link is a page he can open,
reads every top-level frame (structure, design code, screenshot) and writes each frame as a standalone HTML page,
checking its render against the Figma screenshot. This script then turns those pages into canvas elements with
the same converter that builds flynn's designs, on a new page, with every frame at its Figma position.

Input: an import folder (normally runs/<date>-figma-<slug>/) holding frames.json:
  {"figma": {"url": "...", "file_key": "...", "page_id": "0:1", "page_name": "Checkout"},
   "frames": [{"name": "Cart", "x": 0, "y": 0, "w": 1440, "h": 1024, "html": "frames/01-cart/index.html"}, ...]}

  import_page.py <folder> [--page-name NAME] [--file FILE_ID]

The canvas file must be the one open in the user's tab (or pass a bridge from the builder).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "penpot"))
import push  # noqa: E402
from bridge import Bridge, PenpotError  # noqa: E402

PAGE_JS = r"""
const NAME = __NAME__;
let name = NAME, n = 2;
while (penpot.currentFile.pages.some((p) => p.name === name)) name = NAME + ' ' + (n++);
const page = penpot.createPage(); page.name = name;
await penpot.openPage(page);
return { page: page.id, name };
"""

BOARD_JS = r"""
const F = __F__;
const b = penpot.createBoard();
b.name = F.name; b.x = F.x; b.y = F.y; b.resize(Math.max(F.w, 1), Math.max(F.h, 1));
b.fills = F.bg ? [{ fillColor: F.bg.hex, fillOpacity: F.bg.a }] : [];
b.clipContent = true;
return { id: b.id };
"""


def run(folder: Path, page_name: str | None = None, file_id: str | None = None, bridge: Bridge | None = None,
        log=print) -> dict:
    from pagetree import capture
    folder = Path(folder).resolve()
    spec = json.loads((folder / "frames.json").read_text(encoding="utf-8"))
    frames = spec.get("frames") or []
    if not frames:
        raise ValueError("frames.json has no frames")
    bridge = bridge or Bridge()
    cur = bridge.current()
    if file_id and cur.get("file") != file_id:
        raise PenpotError(f"The file open in flynn desk is \"{cur.get('fileName')}\", not the one to import into.")
    name = page_name or f"Imported · {(spec.get('figma') or {}).get('page_name') or 'Figma page'}"
    page = bridge.run_js(push._js(PAGE_JS, NAME=name))
    result = {"file": cur.get("file"), "page": page["page"], "pageName": page["name"], "frames": [], "errors": []}
    for f in frames:
        html = folder / f["html"]
        w = int(round(f.get("w") or 1440))
        log(f"import: capturing {f['name']}")
        tree = capture(html, w)
        h = int(round(f.get("h") or tree["h"]))
        board = bridge.run_js(push._js(BOARD_JS, F={"name": f["name"], "x": f.get("x", 0), "y": f.get("y", 0), "w": w, "h": max(h, tree["h"]),
                                                     "bg": tree.get("background")}))
        stats = push.build_tree(bridge, page["page"], board["id"], {"x": f.get("x", 0), "y": f.get("y", 0)}, tree, log=lambda m: None)
        result["frames"].append({"name": f["name"], "id": board["id"], "built": stats["built"], "total": stats["total"]})
        result["errors"] += stats["errors"][:5]
        log(f"import: {f['name']} {stats['built']}/{stats['total']} elements")
    try:
        bridge.run_js("penpot.viewport.zoomToFitAll(); return true;")
    except PenpotError:
        pass
    (folder / "canvas.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="import_page.py")
    ap.add_argument("folder")
    ap.add_argument("--page-name")
    ap.add_argument("--file")
    a = ap.parse_args()
    print(json.dumps(run(Path(a.folder), a.page_name, a.file), ensure_ascii=False, indent=1))
