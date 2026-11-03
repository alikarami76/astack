"""Build a recording onto the flynn canvas (the open Penpot file), through the MCP bridge.

On the chosen page (the page the user was on, or a new page with his name for it), to the right of anything already
there, it places:
  - a title block: the system, the date and the user's description of what was recorded;
  - "Components": one card per candidate, with a cropped example of each variant and what was seen;
  - "Tokens": colour swatches, type samples, spacing, radii and shadows, with their CSS variable names;
  - "Screens": one board per recorded page and width, holding the full-page screenshot.
Nothing already on the page is moved or changed.

CLI:  canvas.py <session folder> [--new-page "Name" | --page-id ID]
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "penpot"))
from bridge import Bridge, PenpotError  # noqa: E402
import push  # noqa: E402

INK, MUTED, LINE, PAPER, CARD = "#14181f", "#6b7480", "#e3e7ec", "#f6f7f9", "#ffffff"
FONT = "Work Sans"
TILE = 1200            # tall screenshots go in slices of this many CSS pixels (the connector caps request size)

SETUP_JS = r"""
const T = __TARGET__;
let page;
if (T.mode === 'new') { page = penpot.createPage(); page.name = T.page_name || 'Recording'; }
else page = penpot.currentFile.pages.find((p) => p.id === T.page_id) || penpot.currentPage;
if (penpot.currentPage.id !== page.id) await penpot.openPage(page);
const cur = penpot.currentPage;
let right = null, top = 0;
for (const s of cur.root.children || []) { right = Math.max(right ?? -1e9, s.x + s.width); top = Math.min(top, s.y); }
return { page: cur.id, pageName: cur.name, x0: right === null ? 0 : Math.round(right + 400), y0: Math.round(top) };
"""

# The connector caps each request at about 100 KB, so images go up in base64 parts kept in the plugin's
# `storage` object, then one call decodes and uploads them.
PART_JS = r"""
const P = __PART__;
storage.flynnUpload = storage.flynnUpload || {};
if (P.first) storage.flynnUpload[P.key] = '';
storage.flynnUpload[P.key] += P.data;
return storage.flynnUpload[P.key].length;
"""
PART = 60_000

UPLOAD_JS = r"""
const IMG = __IMG__;
if (IMG.key) { IMG.b64 = storage.flynnUpload[IMG.key]; delete storage.flynnUpload[IMG.key]; }
const bin = Uint8Array.from(atob(IMG.b64), (c) => c.charCodeAt(0));
const d = await penpot.uploadMediaData(IMG.name, bin, IMG.mime);
return { id: d.id, width: d.width, height: d.height, mtype: d.mtype, name: d.name, keepAspectRatio: d.keepAspectRatio };
"""

BUILD_JS = r"""
const PAGE_ID = __PAGE_ID__, NODES = __NODES__, IMAGES = __IMAGES__, KNOWN = __KNOWN__;
if (penpot.currentPage.id !== PAGE_ID) await penpot.openPage(penpot.currentFile.pages.find((p) => p.id === PAGE_ID));
const page = penpot.currentPage;
const ids = {}, errors = [];
const fam = penpot.fonts.findByName(__FONT__);
function variant(w) {
  if (!fam) return null;
  let best = fam.variants[0];
  for (const v of fam.variants) if (v.fontStyle !== 'italic' && Math.abs(parseInt(v.fontWeight, 10) - w) < Math.abs(parseInt(best.fontWeight, 10) - w)) best = v;
  return best;
}
const parentOf = (n) => (n.parent >= 0 && page.getShapeById(ids[n.parent] || KNOWN[n.parent])) || null;
for (const n of NODES) {
  try {
    let s;
    if (n.k === 'board') {
      s = penpot.createBoard(); s.clipContent = !!n.clip;
    } else if (n.k === 'rect') {
      s = penpot.createRectangle();
    } else if (n.k === 'image') {
      s = penpot.createRectangle();
      const img = IMAGES[n.img];
      if (!img) throw new Error('image not uploaded: ' + n.img);
      s.fills = [{ fillOpacity: 1, fillImage: img }];
    } else if (n.k === 'text') {
      s = penpot.createText(n.text);
      if (!s) throw new Error('createText returned null');
      const v = variant(n.weight || 400);
      if (fam && v) fam.applyToText(s, v);
      s.fontSize = String(n.size || 14);
      s.fills = [{ fillColor: n.color || '#14181f', fillOpacity: 1 }];
      s.direction = n.dir === 'rtl' ? 'rtl' : 'ltr';
      if (n.w) { s.growType = 'auto-height'; }
      if (n.align) s.textAlign = n.align;
    }
    s.name = String(n.name || n.k).replace(/\//g, ' ').slice(0, 80);
    if (n.k !== 'text' || n.w) s.resize(Math.max(n.w || 1, 1), Math.max(n.h || 1, 1));
    if (n.fill && n.k !== 'image' && n.k !== 'text') s.fills = [{ fillColor: n.fill.slice(0, 7), fillOpacity: n.fill.length > 7 ? parseInt(n.fill.slice(7, 9), 16) / 255 : 1 }];
    if (n.k === 'board' && !n.fill) s.fills = [];
    if (n.stroke) s.strokes = [{ strokeColor: n.stroke, strokeOpacity: 1, strokeWidth: 1, strokeAlignment: 'inner', strokeStyle: 'solid' }];
    if (n.radius) s.borderRadius = n.radius;
    if (n.hidden) s.hidden = true;
    if (n.shadow) s.shadows = [{ style: 'drop-shadow', offsetX: 0, offsetY: 4, blur: 16, spread: 0, color: { color: '#000000', opacity: 0.12 } }];
    const p = parentOf(n);
    if (p) p.appendChild(s);
    s.x = n.x; s.y = n.y;
    ids[n.i] = s.id;
  } catch (e) {
    errors.push(n.i + ' ' + n.k + ' ' + (n.name || '') + ': ' + String(e && e.message || e));
  }
}
return { ids, errors };
"""


def _js(template: str, **values) -> str:
    code = template
    for k, v in values.items():
        code = code.replace(f"__{k}__", json.dumps(v, ensure_ascii=False))
    return code


class Layout:
    """A flat list of shapes in page coordinates; parents are earlier indexes."""

    def __init__(self):
        self.nodes: list[dict] = []
        self.images: dict[str, dict] = {}       # key -> {path, crop (x, y, w, h) in source pixels or None}
        self.screens: list[dict] = []           # boards that get the page's real elements

    def add(self, k: str, parent: int = -1, **kw) -> int:
        i = len(self.nodes)
        self.nodes.append({"i": i, "k": k, "parent": parent, **kw})
        return i

    def text(self, parent, x, y, text, size=14, weight=400, color=INK, w=None, name=None, dir=None, align=None):
        if dir is None:
            dir = "rtl" if any("؀" <= ch <= "ۿ" for ch in str(text)) else "ltr"
        return self.add("text", parent, x=x, y=y, text=str(text), size=size, weight=weight, color=color, w=w,
                        h=size * 1.5, name=name or str(text)[:40], dir=dir, **({"align": align} if align else {}))

    def image(self, parent, x, y, w, h, path: Path, crop=None, name=None, scale_to: int | None = None) -> int:
        key = f"{path}#{crop}"
        self.images[key] = {"path": str(path), "crop": crop, "width": scale_to}
        return self.add("image", parent, x=x, y=y, w=w, h=h, img=key, name=name or path.stem)


def _wrap(text: str, width_chars: int) -> str:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def plan(session: Path, mine: dict | None, x0: int, y0: int) -> Layout:
    """mine=None: screens only (no components or tokens boards)."""
    from PIL import Image
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    L = Layout()
    # ---- title ----
    L.text(-1, x0, y0, f"Recording · {meta['system']}", size=40, weight=600, name="Recording title")
    sub = f"{meta['id'][:10]} · {len(meta['pages'])} page(s) · {meta.get('how', 'watch')} · {meta['url']}"
    L.text(-1, x0, y0 + 64, sub, size=16, color=MUTED, name="Recording details")
    if meta.get("description"):
        L.text(-1, x0, y0 + 100, _wrap(meta["description"], 120), size=18, w=1440, name="What was recorded")
    y = y0 + 220

    # ---- components and tokens (only when the recording makes a design system) ----
    if mine is None:
        comp_h = sections_h = 0
    else:
        # ---- components ----
        comps = mine.get("components", [])
        cw, gap, pad = 1440, 24, 32
        card_w = (cw - pad * 2 - gap) // 2
        cards, cy_col = [], [0, 0]
        for c in comps:
            vs = [v for v in c["variants"] if v.get("crop")][:4]
            thumbs, tw = [], 0
            for v in vs:
                im = Image.open(v["crop"])
                k = min(1.0, 300 / im.width * 2, 150 / im.height * 2) / 2        # crops are 2x; show at 1x, at most 300x150
                thumbs.append((v, int(im.width * k), int(im.height * k)))
            rows_h, row_w, row_h, lay = 0, 0, 0, []
            for v, w, h in thumbs:
                slot = max(w, int(len(f"{v['look']} · {v['size']} · {v['color']}") * 6.4))   # room for the caption
                if row_w + slot > card_w - 48 and row_w:
                    rows_h += row_h + 40
                    row_w, row_h = 0, 0
                lay.append((v, row_w, rows_h, w, h))
                row_w += slot + 24
                row_h = max(row_h, h)
            rows_h += row_h + 40
            h = 96 + rows_h
            col = 0 if cy_col[0] <= cy_col[1] else 1
            cards.append((c, col, cy_col[col], h, lay))
            cy_col[col] += h + gap
        comp_h = max(cy_col) + pad * 2 + 72 if comps else 200
        board = L.add("board", -1, x=x0, y=y, w=cw, h=comp_h, fill=PAPER, radius=16, name="Components")
        L.text(board, x0 + pad, y + 24, f"Components · {len(comps)} candidates", size=22, weight=600, name="Components title")
        for c, col, cy, h, lay in cards:
            cx, top = x0 + pad + col * (card_w + gap), y + 72 + pad + cy
            card = L.add("board", board, x=cx, y=top, w=card_w, h=h, fill=CARD, radius=12, stroke=LINE, name=c["name"])
            L.text(card, cx + 24, top + 20, c["name"], size=18, weight=600)
            seen = f"seen {c['count']}× on {len(c['pages'])} page(s)"
            if c.get("class_variants"):
                seen += " · classes: " + ", ".join(c["class_variants"][:5])
            if "disabled" in c.get("states", []):
                seen += " · disabled seen"
            L.text(card, cx + 24, top + 48, seen[:110], size=12, color=MUTED)
            for v, vx, vy, w, hh in lay:
                L.image(card, cx + 24 + vx, top + 88 + vy, w, hh, Path(v["crop"]), name=f"{c['name']} {v['look']} {v['size']}")
                L.text(card, cx + 24 + vx, top + 88 + vy + hh + 6, f"{v['look']} · {v['size']} · {v['color']}", size=11, color=MUTED)
        tokens_x = x0 + cw + 120

        # ---- tokens ----
        toks = mine.get("tokens", [])
        tw = 1100
        by = {k: [t for t in toks if t["kind"] == k] for k in ("color", "type", "space", "radius", "elevation")}
        colors = by["color"]
        ty = y + 72
        nodes_after = []
        col_rows = (len(colors) + 5) // 6
        sections_h = 72 + (col_rows * 120 + 60 if colors else 0) + (len(by["type"]) * 44 + 60 if by["type"] else 0) \
            + (len(by["space"]) * 28 + 60 if by["space"] else 0) + (160 if by["radius"] else 0) + (len(by["elevation"]) * 28 + 60 if by["elevation"] else 0)
        tb = L.add("board", -1, x=tokens_x, y=y, w=tw, h=max(sections_h + 40, 240), fill=PAPER, radius=16, name="Tokens")
        L.text(tb, tokens_x + 32, y + 24, f"Tokens · {len(toks)} found · {mine.get('css_vars', 0)} CSS variables", size=22, weight=600, name="Tokens title")
        if colors:
            L.text(tb, tokens_x + 32, ty, "Colour", size=14, weight=600, color=MUTED)
            ty += 32
            for i, t in enumerate(colors):
                cx, cy = tokens_x + 32 + (i % 6) * 176, ty + (i // 6) * 120
                L.add("rect", tb, x=cx, y=cy, w=160, h=56, fill=t["value"], radius=8, stroke=LINE, name=t["name"])
                L.text(tb, cx, cy + 62, t["name"][:24], size=11, weight=500)
                L.text(tb, cx, cy + 80, f"{t['value']} · {t.get('role', '')} ×{t['uses']}", size=10, color=MUTED)
            ty += col_rows * 120 + 28
        if by["type"]:
            L.text(tb, tokens_x + 32, ty, "Type", size=14, weight=600, color=MUTED)
            ty += 32
            for t in by["type"]:
                size = float(str(t["name"].split("/")[1].split("px")[0]))
                weight = int(t["name"].rsplit("-", 1)[1]) if t["name"].rsplit("-", 1)[1].isdigit() else 400
                L.text(tb, tokens_x + 32, ty, f"Aa  {t['value']}  ×{t['uses']}", size=max(10, min(size, 32)), weight=weight)
                ty += 44
            ty += 28
        if by["space"]:
            L.text(tb, tokens_x + 32, ty, "Spacing", size=14, weight=600, color=MUTED)
            ty += 32
            for t in by["space"]:
                v = float(t["value"].rstrip("px"))
                L.add("rect", tb, x=tokens_x + 32, y=ty + 4, w=max(1, v * 4), h=12, fill="#8fb8d8", radius=2, name=t["name"])
                L.text(tb, tokens_x + 32 + max(1, v * 4) + 12, ty, f"{t['value']} ×{t['uses']}", size=11, color=MUTED)
                ty += 28
            ty += 28
        if by["radius"]:
            L.text(tb, tokens_x + 32, ty, "Radius", size=14, weight=600, color=MUTED)
            ty += 32
            for i, t in enumerate(by["radius"][:8]):
                v = float(t["value"].rstrip("px"))
                L.add("rect", tb, x=tokens_x + 32 + i * 120, y=ty, w=88, h=64, fill=CARD, stroke="#9aa4ae", radius=min(v, 32), name=t["name"])
                L.text(tb, tokens_x + 32 + i * 120, ty + 72, f"{t['value']} ×{t['uses']}", size=11, color=MUTED)
            ty += 128
        if by["elevation"]:
            L.text(tb, tokens_x + 32, ty, "Shadows", size=14, weight=600, color=MUTED)
            ty += 32
            for t in by["elevation"]:
                L.text(tb, tokens_x + 32, ty, f"{t['value'][:110]}  ×{t['uses']}", size=11, color=MUTED)
                ty += 28

    # ---- screens ----
    sy = y + 130 if mine is None else y + max(comp_h, sections_h + 40) + 240   # screens only: right under the title
    L.text(-1, x0, sy - 130, "Screens", size=28, weight=600, name="Screens title")
    sx = x0
    for p in meta["pages"]:
        for size in p["sizes"]:
            d = Path(size["dir"])
            full = d / "full.png"
            if not full.exists():
                continue
            im = Image.open(full)
            k = size.get("dpr") or 2
            w, h = int(im.width / k), int(im.height / k)
            h_shown = min(h, 12000)
            b = L.add("board", -1, x=sx, y=sy, w=w, h=h_shown, fill="#ffffff", clip=True, shadow=True,
                      name=f"{p['n']:02d} · {p['title'] or p['url']} · {size['width']}")
            # Number and width (left to right) and the page title (in its own direction, right-aligned) are two
            # texts: one mixed-direction line garbles the order, and auto-width RTL text grows the wrong way
            # (penpot#11523), so both have fixed widths.
            L.text(-1, sx, sy - 36, f"{p['n']:02d} · {size['width']}px", size=14, color=MUTED, w=160, dir="ltr",
                   name=f"{p['n']:02d} label")
            L.text(-1, sx + 180, sy - 36, (p['title'] or p['url'])[:80], size=14, color=MUTED, w=max(w - 180, 120),
                   align="right" if any("؀" <= ch <= "ۿ" for ch in (p['title'] or '')) else "left",
                   name=f"{p['n']:02d} title")
            # the screenshot is a hidden reference layer at the bottom; the real elements go on top (see build)
            for top in range(0, h_shown, TILE):
                th = min(TILE, h_shown - top)
                t = L.image(b, sx, sy + top, w, th, full, crop=(0, int(top * k), im.width, int((top + th) * k)),
                            name=f"screenshot (reference) {top}", scale_to=w)
                L.nodes[t]["hidden"] = (d / "pnode.json").exists()
            if (d / "pnode.json").exists():
                L.screens.append({"board": b, "x": sx, "y": sy, "dir": str(d), "dpr": k, "h": h_shown})
            sx += w + 160
    return L


def _encode(path: str, crop, width: int | None = None) -> dict:
    """Screenshots go at 1x as JPEG (small enough for the connector); component crops stay sharp PNGs."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if crop:
        im = im.crop(crop)
    if width and im.width > width:
        im = im.resize((width, max(1, int(im.height * width / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    big = im.width * im.height > 250_000
    im.save(buf, format="JPEG" if big else "PNG", quality=82, optimize=True)
    return {"b64": base64.b64encode(buf.getvalue()).decode(), "mime": "image/jpeg" if big else "image/png",
            "name": Path(path).stem[:40]}


def screen_tree(d: Path, dpr: float, max_h: int) -> dict:
    """The recorded page's real elements (pnode.json), with every image cut from the stitched screenshot by its
    box, so images always load (even from other domains) and look exactly as recorded."""
    from PIL import Image
    tree = json.loads((d / "pnode.json").read_text(encoding="utf-8"))
    full = Image.open(d / "full.png").convert("RGB")
    nodes = tree["nodes"]
    for n in nodes:                     # below the board's height: skipped (indexes must not move)
        if n["box"]["y"] >= max_h:
            n["skip"] = True
    images = {}
    for i, n in enumerate(nodes):
        if n["type"] != "image" or n.get("skip"):
            continue
        b = n["box"]
        box = (int(b["x"] * dpr), int(b["y"] * dpr), int((b["x"] + b["w"]) * dpr), int((b["y"] + b["h"]) * dpr))
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            continue
        crop = full.crop(box)
        if crop.width > 1600:
            crop = crop.resize((1600, int(crop.height * 1600 / crop.width)))
        buf = io.BytesIO()
        big = crop.width * crop.height > 250_000
        crop.save(buf, format="JPEG" if big else "PNG", quality=85, optimize=True)
        key = f"rec-image-{i}"
        images[key] = {"mime": "image/jpeg" if big else "image/png", "b64": base64.b64encode(buf.getvalue()).decode()}
        n["src"] = key
    return {**tree, "nodes": nodes, "images": images}


def upload(bridge: Bridge, img: dict, key: str) -> dict:
    """Upload one image to the file's media, in parts when it is larger than one request allows."""
    b64 = img["b64"]
    if len(b64) <= PART:
        return bridge.run_js(_js(UPLOAD_JS, IMG=img))
    for n, start in enumerate(range(0, len(b64), PART)):
        bridge.run_js(_js(PART_JS, PART={"key": key, "first": n == 0, "data": b64[start:start + PART]}))
    return bridge.run_js(_js(UPLOAD_JS, IMG={**img, "b64": "", "key": key}))


def build(session: Path, target: dict, bridge: Bridge | None = None, log=print, screens_only: bool = False) -> dict:
    """target: {mode: 'current'|'new', page_id?, page_name?}. Returns {page, pageName, shapes, errors}.
    screens_only: put just the recorded screens on the page (no components or tokens boards, no mine.json needed)."""
    bridge = bridge or Bridge()
    mine = None if screens_only else json.loads((session / "mine.json").read_text(encoding="utf-8"))
    setup = bridge.run_js(_js(SETUP_JS, TARGET=target))
    L = plan(session, mine, setup["x0"], setup["y0"])
    log(f"canvas: {len(L.nodes)} shapes, {len(L.images)} images on '{setup['pageName']}'")
    images = {}
    for i, (key, spec) in enumerate(L.images.items(), 1):
        images[key] = upload(bridge, _encode(spec["path"], spec["crop"], spec.get("width")), f"img{i}")
        if i % 10 == 0:
            log(f"canvas: uploaded {i}/{len(L.images)} images")
    ids, errors = {}, []
    for start in range(0, len(L.nodes), 100):
        chunk = L.nodes[start:start + 100]
        used = {n["img"] for n in chunk if n["k"] == "image"}
        known = {n["parent"]: ids[n["parent"]] for n in chunk if n["parent"] >= 0 and n["parent"] in ids}
        out = bridge.run_js(_js(BUILD_JS, PAGE_ID=setup["page"], NODES=chunk, FONT=FONT,
                                IMAGES={k: images[k] for k in used}, KNOWN=known))
        ids.update({int(k): v for k, v in out["ids"].items()})
        errors += out["errors"]
    elements = 0
    for sc in L.screens:
        tree = screen_tree(Path(sc["dir"]), sc["dpr"], sc["h"])
        res = push.build_tree(bridge, setup["page"], ids[sc["board"]], {"x": sc["x"], "y": sc["y"]}, tree, log=lambda m: None)
        elements += res["built"]
        errors += res["errors"][:5]
        log(f"canvas: {res['built']}/{res['total']} real elements on {Path(sc['dir']).parent.name}")
    try:
        bridge.run_js("penpot.viewport.zoomToFitAll(); return true;")
    except PenpotError:
        pass
    result = {"page": setup["page"], "pageName": setup["pageName"], "shapes": len(ids), "elements": elements, "errors": errors[:20]}
    (session / "canvas.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="canvas.py")
    ap.add_argument("session")
    ap.add_argument("--new-page")
    ap.add_argument("--page-id")
    ap.add_argument("--screens-only", action="store_true")
    a = ap.parse_args()
    t = {"mode": "new", "page_name": a.new_page} if a.new_page else {"mode": "current", "page_id": a.page_id}
    print(json.dumps(build(Path(a.session), t, screens_only=a.screens_only), ensure_ascii=False, indent=1))
