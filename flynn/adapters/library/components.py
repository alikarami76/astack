"""Library components from real markup: every sample becomes a canvas component, samples of one name become variants.

Source of markup:
  - A design-system kit (adapters/kit/kit/*.html), when you supply one: real scrubbed markup with the product's compiled CSS.
  - Recorded systems: recorder sessions keep each candidate's live element (see adapters/recorder), rendered the
    same way.
How: all samples go on one gallery page, each in its own wrapper at its natural width. The page is captured once
with the same converter that builds flynn's designs (html2pnode via pagetree), the captured shapes are split by
wrapper, and each sample is drawn into its own board on the library's "Components" page. The board becomes a
library component; samples that share a name are grouped into one variant set, with variant values read off the
product's own modifier classes (Button__filledPrimary -> filledPrimary) when it has them.
"""
from __future__ import annotations

import os

import html
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "penpot"))
import pagetree  # noqa: E402
import push  # noqa: E402

KIT = FLYNN / "adapters" / "kit" / "kit"
BUILD = FLYNN / "adapters" / "kit" / "build"
GAP = 64

VARIANT_WORD = re.compile(r"^(x?small|medium|x?large|xs|sm|md|lg|xl|filled\w*|outlined\w*|text\w*|ghost\w*|neutral\w*|"
                          r"primary|secondary|tertiary|error|danger|warning|success|info|brand|active|selected|disabled|"
                          r"rounded|round|block|dense|compact)$", re.I)


def variant_label(markup: str, name: str) -> str:
    """The sample's variant, from the modifier classes on its outer element: 'filledPrimary · medium'."""
    m = re.search(r'class="([^"]*)"', markup)
    words = []
    for c in (m.group(1).split() if m else []):
        c = re.sub(r"-[A-Za-z0-9_]{5,7}$", "", c)
        for piece in re.split(r"__|--", c)[1:]:
            p = piece.split("-")[0]
            if VARIANT_WORD.match(p) and p not in words:
                words.append(p)
    return " · ".join(words[:3])


def kit_samples(limit_w: int = 1400) -> list[dict]:
    if not (KIT / "kit.json").exists():
        idx = []   # no kit supplied: only approved samples are used
    else:
        idx = json.loads((KIT / "kit.json").read_text(encoding="utf-8"))
    out = []
    for e in idx:
        if e.get("kind") != "component" or not e.get("file"):
            continue
        f = KIT / e["file"]
        if not f.exists() or e.get("w", 0) > limit_w:
            continue
        markup = f.read_text(encoding="utf-8")
        out.append({"name": e["name"], "file": e["file"], "w": max(1, int(e.get("w") or 0)), "h": int(e.get("h") or 0),
                    "markup": markup, "variant": variant_label(markup, e["name"])})
    # samples the user approved through a design system change (changes.py component.sample)
    for f in sorted((FLYNN / "systems" / "admin-panel" / "library-samples").glob("*.html")):
        name, _, var = f.stem.rpartition("-")
        markup = f.read_text(encoding="utf-8")
        out.append({"name": name or f.stem, "file": f"approved/{f.name}", "w": 0, "h": 0, "markup": markup,
                    "variant": var.replace("-", " ") if name else (variant_label(markup, f.stem) or "sample")})
    return out


def gallery(samples: list[dict], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if (BUILD / "admin-panel.css").exists():
        shutil.copy(BUILD / "admin-panel.css", folder / "admin-panel.css")
    if (BUILD / "fonts").exists():
        shutil.copytree(BUILD / "fonts", folder / "fonts", dirs_exist_ok=True)
    sprite = (KIT / "icons.svg").read_text(encoding="utf-8") if (KIT / "icons.svg").exists() else ""
    cells = "\n".join(
        f'<div class="fx-sample" data-i="{i}" style="{f"width:{s["w"]}px" if s["w"] else "width:max-content"}">{s["markup"]}</div>'
        for i, s in enumerate(samples))
    doc = f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<link rel="stylesheet" href="admin-panel.css">
<style>body{{margin:0;font-family:Vazirmatn,sans-serif;background:#fff}}
.gal{{display:flex;flex-wrap:wrap;gap:{GAP}px;padding:{GAP}px;align-items:flex-start}}
.fx-sample{{position:relative;flex:none}}</style></head>
<body>{sprite}<div class="gal">{cells}</div></body></html>"""
    p = folder / "index.html"
    p.write_text(doc, encoding="utf-8")
    return p


def capture_samples(page: Path, samples: list[dict]) -> list[dict]:
    """Capture the gallery once and split its shapes per sample (by the sample's box)."""
    from playwright.sync_api import sync_playwright
    import base64
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        pg.goto(page.resolve().as_uri(), wait_until="load")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        full_h = pg.evaluate("Math.ceil(document.documentElement.scrollHeight)")
        pg.set_viewport_size({"width": 1600, "height": max(900, min(full_h, 30000))})
        pg.wait_for_timeout(200)
        boxes = pg.evaluate("""() => [...document.querySelectorAll('.fx-sample')].map((w) => {
            const el = w.firstElementChild || w; const r = el.getBoundingClientRect(), q = w.getBoundingClientRect();
            const x0 = Math.min(r.left, q.left), y0 = Math.min(r.top, q.top);
            const x1 = Math.max(r.right, q.left + 1), y1 = Math.max(r.bottom, q.top + 1);
            return { i: +w.dataset.i, x: x0 + scrollX, y: y0 + scrollY, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) }; })""")
        tree = pg.evaluate(pagetree.WALKER)
        images = {}
        for n in tree["nodes"]:
            if n["type"] == "image" and n["src"] not in images:
                got = pagetree._image_bytes(pg, n["src"])
                if got:
                    images[n["src"]] = {"mime": got[1], "b64": base64.b64encode(got[0]).decode()}
        browser.close()
    nodes = tree["nodes"]
    out = []
    for b in boxes:
        s = samples[b["i"]]
        inside = []
        for i, n in enumerate(nodes):
            x, y, w, h = n["box"]["x"], n["box"]["y"], n["box"]["w"], n["box"]["h"]
            if x >= b["x"] - 1 and y >= b["y"] - 1 and x + w <= b["x"] + b["w"] + 1 and y + h <= b["y"] + b["h"] + 1:
                inside.append(i)
        keep = set(inside)
        sub = []
        remap = {old: new for new, old in enumerate(inside)}
        for old in inside:
            n = dict(nodes[old])
            img = images.get(n.get("src")) if n["type"] == "image" else None
            if n["type"] == "image" and (not img or "svg" in img["mime"]):
                # the kit's image placeholders are SVGs, which the canvas will not take as an image fill
                n = {"type": "box", "parent": n["parent"], "name": n.get("name") or "image", "uikit": None,
                     "box": n["box"], "fill": {"hex": "#e0e0e2", "a": 1}, "borders": [None, None, None, None],
                     "radius": [8, 8, 8, 8], "shadow": None, "clip": False, "opacity": 1}
            n["parent"] = remap.get(n["parent"], -1) if n["parent"] in keep else -1
            n["box"] = {**n["box"], "x": n["box"]["x"] - b["x"], "y": n["box"]["y"] - b["y"]}
            sub.append(n)
        used = {n["src"] for n in sub if n["type"] == "image"}
        out.append({**s, "box": b, "tree": {"w": round(b["w"]), "h": round(b["h"]), "nodes": sub,
                                            "images": {k: v for k, v in images.items() if k in used}}})
    return out


PAGE_JS = r"""
const NAME = __NAME__;
let page = penpot.currentFile.pages.find((p) => p.name === NAME);
if (!page) { page = penpot.createPage(); page.name = NAME; }
if (penpot.currentPage.id !== page.id) await penpot.openPage(page);
// flynn owns this page: clear what it built last time
for (const s of [...(penpot.currentPage.root.children || [])]) { try { s.remove(); } catch (e) {} }
return { page: penpot.currentPage.id };
"""

BOARD_JS = r"""
const B = __B__;
const b = penpot.createBoard();
b.name = B.name; b.x = B.x; b.y = B.y; b.resize(Math.max(B.w, 1), Math.max(B.h, 1));
b.fills = []; b.clipContent = false;
return { id: b.id };
"""

FINISH_JS = r"""
const G = __G__;
const page = penpot.currentPage;
const out = { components: 0, variants: 0, errors: [] };
const NS = 'flynn';
for (const g of G) {
  try {
    const boards = g.boards.map((x) => page.getShapeById(x.id)).filter(Boolean);
    const comps = [];
    g.ids = [];
    boards.forEach((b, k) => {
      const c = penpot.library.local.createComponent([b]);
      g.ids.push(c.id);
      c.name = g.name; c.path = g.path;
      c.setSharedPluginData(NS, 'key', g.boards[k].key);
      comps.push(c); out.components++;
    });
    if (comps.length > 1) {
      const mains = comps.map((c) => c.mainInstance()).filter(Boolean);
      const cont = penpot.createVariantFromComponents(mains);
      cont.name = g.name;
      try { cont.variants.renameProperty(0, 'Variant'); } catch (e) {}
      comps.forEach((c, k) => { try { c.setVariantProperty(0, g.boards[k].variant); } catch (e) { out.errors.push(g.name + ' variant: ' + (e.message || e)); } });
      out.variants++;
    }
    const label = penpot.createText(g.name);
    if (label) { label.fontSize = '20'; label.fontWeight = '600'; label.x = g.x; label.y = g.y - 44; label.name = 'label ' + g.name; }
  } catch (e) { out.errors.push(g.name + ': ' + (e.message || e)); }
  out.map = out.map || {};
  out.map[g.name] = (g.ids || []).map((id, k) => ({ id, variant: g.boards[k].variant, key: g.boards[k].key, w: g.boards[k].w, h: g.boards[k].h, sig: g.boards[k].sig, fp: g.boards[k].fp, main: g.boards[k].id }));
}
out.errors = out.errors.slice(0, 20);
return out;
"""


def signature(tree: dict) -> dict:
    """What an element looks like at a glance: its own fill and border colour, and how many texts and icons it holds.
    Used to place an instance only where the library has a variant that really matches."""
    nodes = tree["nodes"]
    root = next((n for n in nodes if n["parent"] < 0 and n["type"] == "box"), None)
    fill = ((root or {}).get("fill") or {}).get("hex")
    border = next((b["c"]["hex"] for b in ((root or {}).get("borders") or []) if b), None)
    return {"fill": fill, "border": border, "texts": sum(n["type"] == "text" for n in nodes),
            "svgs": sum(n["type"] == "svg" for n in nodes)}


def fingerprint(tree: dict) -> str:
    import hashlib
    return hashlib.sha1(json.dumps({"n": tree["nodes"], "w": tree["w"], "h": tree["h"]}, sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()[:16]


OPEN_JS = r"""
const NAME = __NAME__;
let page = penpot.currentFile.pages.find((p) => p.name === NAME);
if (!page) { page = penpot.createPage(); page.name = NAME; }
if (penpot.currentPage.id !== page.id) await penpot.openPage(page);
let bottom = 0;
for (const s of penpot.currentPage.root.children || []) bottom = Math.max(bottom, s.y + s.height);
return { page: penpot.currentPage.id, bottom };
"""


def build(bridge, samples: list[dict], log=print, page_name: str = "Components", clear: bool = True,
          y_start: int = 120) -> dict:
    if clear:
        page_id = bridge.run_js(push._js(PAGE_JS, NAME=page_name))["page"]
    else:
        page_id = bridge.run_js(push._js(OPEN_JS, NAME=page_name))["page"]
    groups: dict[str, list] = {}
    for s in samples:
        groups.setdefault(s["name"], []).append(s)
    x0, y, row_h, maxw = 0, y_start, 0, 3200
    plan, errors = [], []
    x = x0
    for name, ss in groups.items():
        gw = sum(s["tree"]["w"] for s in ss) + GAP * (len(ss) - 1)
        gh = max(s["tree"]["h"] for s in ss)
        if x > x0 and x + gw > maxw:
            x, y = x0, y + row_h + 160
            row_h = 0
        bx, entry = x, {"name": name, "path": name.split(".")[0] if "." in name else "", "x": x, "y": y, "boards": []}
        labels_seen: dict[str, int] = {}
        for k, s in enumerate(ss, 1):
            label = s["variant"] or f"sample {k}"
            labels_seen[label] = labels_seen.get(label, 0) + 1
            if labels_seen[label] > 1:
                label = f"{label} {labels_seen[label]}"
            bid = bridge.run_js(push._js(BOARD_JS, B={"name": name, "x": bx, "y": y, "w": s["tree"]["w"], "h": s["tree"]["h"]}))["id"]
            res = push.build_tree(bridge, page_id, bid, {"x": bx, "y": y}, s["tree"], log=lambda m: None)
            errors += res["errors"][:3]
            entry["boards"].append({"id": bid, "key": f"kit:{s['file']}", "variant": label,
                                    "w": s["tree"]["w"], "h": s["tree"]["h"], "sig": signature(s["tree"]),
                                    "fp": fingerprint(s["tree"])})
            bx += s["tree"]["w"] + GAP
        plan.append(entry)
        x += gw + 120
        row_h = max(row_h, gh)
        log(f"components: drew {name} ({len(ss)} sample(s))")
    fin = {"components": 0, "variants": 0, "errors": [], "map": {}}
    for start in range(0, len(plan), 10):
        r = bridge.run_js(push._js(FINISH_JS, G=plan[start:start + 10]))
        fin["components"] += r["components"]
        fin["variants"] += r["variants"]
        fin["errors"] += r["errors"]
        fin["map"].update(r.get("map") or {})
    fin["draw_errors"] = errors[:20]
    fin["names"] = list(groups)
    return fin


# ---------- recorded systems ----------

RECORDINGS = FLYNN / "refs" / "recordings"


def _within(b: dict, x0, y0, w, h, pad: float = 1) -> bool:
    return b["x"] >= x0 - pad and b["y"] >= y0 - pad and b["x"] + b["w"] <= x0 + w + pad and b["y"] + b["h"] <= y0 + h + pad


def _subtree(pnode: dict, box: list, full, dpr: float, key: str, fixed: list | None = None) -> dict:
    """The recorded elements inside one example's box, moved to (0, 0), with images cut from the screenshot.
    Fixed elements (a floating assistant button, a sticky bar) that only overlap the example are left out."""
    import base64
    import io
    x0, y0, w, h = box
    nodes = pnode["nodes"]
    over = [f for f in (fixed or []) if not (x0 >= f[0] - 1 and y0 >= f[1] - 1 and x0 + w <= f[0] + f[2] + 1 and y0 + h <= f[1] + f[3] + 1)]
    inside = [i for i, n in enumerate(nodes)
              if _within(n["box"], x0, y0, w, h)
              and not any(_within(n["box"], *f) for f in over)]
    remap = {old: new for new, old in enumerate(inside)}
    sub, images = [], {}
    for old in inside:
        n = dict(nodes[old])
        n["parent"] = remap.get(n["parent"], -1)
        b = n["box"]
        if n["type"] == "image":
            crop = full.crop((int(b["x"] * dpr), int(b["y"] * dpr), int((b["x"] + b["w"]) * dpr), int((b["y"] + b["h"]) * dpr)))
            buf = io.BytesIO()
            crop.save(buf, format="PNG", optimize=True)
            k = f"{key}-img{len(images)}"
            images[k] = {"mime": "image/png", "b64": base64.b64encode(buf.getvalue()).decode()}
            n["src"] = k
        n["box"] = {**b, "x": b["x"] - x0, "y": b["y"] - y0}
        sub.append(n)
    return {"w": round(w), "h": round(h), "nodes": sub, "images": images}


def recorded_samples(system: str) -> list[dict]:
    """Library samples for a recorded system (rec:<name>): every kept candidate's variant examples, as the real
    recorded elements. flynn's review (<session>/review.json) names, merges and drops candidates; without a review
    every candidate is used under its mined name. Later sessions override earlier ones for the same component."""
    from PIL import Image
    base = system.removeprefix("rec:")
    out: dict[str, list] = {}
    for mj in sorted((RECORDINGS / slugify(base)).glob("*/mine.json")):
        session = mj.parent
        mined = json.loads(mj.read_text(encoding="utf-8"))
        review = {}
        if (session / "review.json").exists():
            review = json.loads((session / "review.json").read_text(encoding="utf-8"))
        rename = {}
        for c in review.get("components", []):
            for src in c.get("from") or [c.get("name")]:
                rename[src] = c if c.get("keep", True) else None
        cache, here = {}, {}
        for cand in mined.get("components", []):
            spec = rename.get(cand["name"], {"name": cand["name"]}) if review else {"name": cand["name"]}
            if spec is None:
                continue                                    # flynn's review dropped it
            name = spec.get("name") or cand["name"]
            labels = spec.get("variants") or {}
            samples = []
            for k, v in enumerate(cand["variants"], 1):
                ex = v["example"]
                d = Path(ex["dir"])
                if not (d / "pnode.json").exists():
                    continue
                if str(d) not in cache:
                    dom = json.loads((d / "dom.json").read_text(encoding="utf-8")) if (d / "dom.json").exists() else {"els": []}
                    fixed = [e["box"] for e in dom.get("els", []) if e.get("fixed") and e["box"][2] * e["box"][3] < 400_000]
                    cache[str(d)] = (json.loads((d / "pnode.json").read_text(encoding="utf-8")), Image.open(d / "full.png").convert("RGB"), fixed)
                pnode, full, fixed = cache[str(d)]
                tree = _subtree(pnode, ex["box"], full, ex.get("dpr") or 1, f"{session.name}-{name}-{k}", fixed)
                if not tree["nodes"]:
                    continue
                label = labels.get(str(k)) or labels.get(f"{v['look']} · {v['size']}") or f"{v['look']} · {v['size']}"
                samples.append({"name": name, "file": f"{session.name}/{cand['name']}/{k}", "variant": label, "tree": tree})
            if samples:
                here.setdefault(name, []).extend(samples)   # merged candidates add up within a session
        out.update(here)                                     # a later session replaces a component wholesale
    return [s for ss in out.values() for s in ss]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "system"


# ---------- in-place updates ----------

REDRAW_JS = r"""
const M = __M__;
const page = penpot.currentFile.pages.find((p) => p.name === __PAGE__);
if (page && penpot.currentPage.id !== page.id) await penpot.openPage(page);
const b = penpot.currentPage.getShapeById(M.main);
if (!b) return { ok: false };
for (const c of [...(b.children || [])]) { try { c.remove(); } catch (e) {} }
b.resize(Math.max(M.w, 1), Math.max(M.h, 1));
return { ok: true, x: b.x, y: b.y, page: penpot.currentPage.id };
"""

REMOVE_JS = r"""
const IDS = __IDS__;
const page = penpot.currentFile.pages.find((p) => p.name === __PAGE__);
if (page && penpot.currentPage.id !== page.id) await penpot.openPage(page);
let n = 0;
for (const id of IDS) { const s = penpot.currentPage.getShapeById(id); if (s) { try { s.remove(); n++; } catch (e) {} } }
for (const t of [...penpot.currentPage.root.children]) if (t.type === 'text' && IDS.length && /^label /.test(t.name) && __NAMES__.includes(t.name.slice(6))) { try { t.remove(); } catch (e) {} }
return n;
"""


def update(bridge, samples: list[dict], prev: dict, log=print, page_name: str = "Components") -> dict:
    """Bring library components up to date without changing their ids where possible.

    - a sample whose fingerprint changed: its main component is redrawn in place (same component id);
    - a component group whose samples were added or removed: only that group is rebuilt (new ids for it);
    - a group no longer in the source: removed; an unchanged sample: left alone."""
    groups: dict[str, list] = {}
    for x in samples:
        groups.setdefault(x["name"], []).append(x)
    out = {"redrawn": 0, "unchanged": 0, "rebuilt": [], "removed": [], "errors": [], "map": {}}
    rebuild = []
    for name, ss in groups.items():
        old = prev.get(name) or []
        old_by_key = {o["key"]: o for o in old}
        keys = [f"kit:{x['file']}" for x in ss]
        if not old or set(keys) != set(old_by_key) or not all(o.get("main") for o in old):
            rebuild.append(name)
            continue
        entries = []
        for x, key in zip(ss, keys):
            o = old_by_key[key]
            fp = fingerprint(x["tree"])
            if o.get("fp") == fp:
                out["unchanged"] += 1
                entries.append(o)
                continue
            r = bridge.run_js(push._js(REDRAW_JS, M={"main": o["main"], "w": x["tree"]["w"], "h": x["tree"]["h"]}, PAGE=page_name))
            if not r.get("ok"):
                rebuild.append(name)
                break
            res = push.build_tree(bridge, r["page"], o["main"], {"x": r["x"], "y": r["y"]}, x["tree"], log=lambda m: None)
            out["errors"] += res["errors"][:3]
            out["redrawn"] += 1
            entries.append({**o, "fp": fp, "w": x["tree"]["w"], "h": x["tree"]["h"], "sig": signature(x["tree"])})
        else:
            out["map"][name] = entries
    gone = [n for n in prev if n not in groups]
    stale = [n for n in rebuild if n in prev] + gone
    if stale:
        ids = [o["main"] for n in stale for o in prev[n] if o.get("main")]
        bridge.run_js(push._js(REMOVE_JS, IDS=ids, PAGE=page_name, NAMES=stale))
        out["removed"] = gone
    if rebuild:
        bottom = bridge.run_js(push._js(OPEN_JS, NAME=page_name))["bottom"]
        r = build(bridge, [x for n in rebuild for x in groups[n]], log=log, page_name=page_name, clear=False,
                  y_start=int(bottom) + 200)
        out["map"].update(r["map"])
        out["rebuilt"] = rebuild
        out["errors"] += r["errors"] + r.get("draw_errors", [])
    out["errors"] = out["errors"][:20]
    return out
