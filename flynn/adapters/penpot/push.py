#!/usr/bin/env python3
"""Push a flynn run's designs into the open Penpot file, as a page named "flynn · <run>".

  push.py run <run folder> [--file <penpot file id>] [--team <team id>] [--width 1440]
  push.py pull <run folder>     bring the user's Penpot edits back (board images + texts) into <run>/penpot-pull/

Each option of the run (page/ is A, options/<name>/page/ the others) becomes one board, side by side.
Pushing again replaces the boards flynn built last time, unless the user edited them in Penpot: an edited
board is kept and the new build is placed beside it as the next version (a fingerprint of every layer is
stored in <run>/penpot.json to tell). Text stays editable (Vazirmatn from the
team fonts), icons come in as vectors, images are uploaded. Layout is absolute in this first version:
boxes become boards nested like the page, positioned exactly; auto layout comes later (module M3).
Writes <run>/penpot.json with the page, boards, counts and any errors."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bridge import Bridge, PenpotError  # noqa: E402

CHUNK = 120              # at most this many layers per request...
CHUNK_BYTES = 60_000     # ...and at most this much JSON: the connector refuses requests over about 100 KB
GAP = 200

SETUP_JS = r"""
const PAGE_NAME = __PAGE_NAME__;
const BOARDS = __BOARDS__;
const REMOVE = __REMOVE__;
let page = penpot.currentFile.pages.find((p) => p.name === PAGE_NAME);
if (!page) { page = penpot.createPage(); page.name = PAGE_NAME; }
if (penpot.currentPage.id !== page.id) await penpot.openPage(page);
const cur = penpot.currentPage;
// Replace an earlier push: remove only the boards flynn built last time and the user has not edited.
for (const id of REMOVE) { const old = cur.getShapeById(id); if (old) { try { old.remove(); } catch (e) {} } }
const out = {};
for (const b of BOARDS) {
  const r = penpot.createBoard();
  r.name = b.name; r.x = b.x; r.y = b.y || 0; r.resize(b.w, b.h);
  r.fills = [{ fillColor: b.bg.hex, fillOpacity: b.bg.a }];
  r.clipContent = true;
  out[b.name] = r.id;
}
return { page: cur.id, pageName: cur.name, roots: out };
"""

REMOVE_JS = r"""
const IDS = __IDS__;
let n = 0;
for (const id of IDS) { const s = penpot.currentPage.getShapeById(id); if (s) { try { s.remove(); n++; } catch (e) {} } }
return n;
"""

FINGERPRINT_JS = r"""
// A fingerprint of each board: every descendant's type, name, geometry, text and fills. If it changed since
// flynn built the board, the user edited it in Penpot.
const IDS = __IDS__;
function h(str) { let x = 5381; for (let i = 0; i < str.length; i++) x = ((x << 5) + x + str.charCodeAt(i)) | 0; return (x >>> 0).toString(16); }
function walk(s, acc) {
  acc.push([s.type, s.name, Math.round(s.x), Math.round(s.y), Math.round(s.width), Math.round(s.height),
            s.type === 'text' ? s.characters : '', JSON.stringify((s.fills || []).map((f) => [f.fillColor, f.fillOpacity]))].join('|'));
  for (const c of (s.children || [])) walk(c, acc);
}
const out = {};
for (const id of IDS) {
  let s = null;
  for (const p of penpot.currentFile.pages) { s = p.getShapeById(id); if (s) break; }
  if (!s) { out[id] = null; continue; }
  const acc = []; walk(s, acc); out[id] = h(acc.join('\n'));
}
return out;
"""

READ_JS = r"""
// Texts of a board, for flynn to see what the user changed in Penpot.
const ID = __ID__;
let s = null;
for (const p of penpot.currentFile.pages) { s = p.getShapeById(ID); if (s) break; }
if (!s) return null;
const texts = [];
(function walk(n) { if (n.type === 'text') texts.push({ name: n.name, text: n.characters, x: Math.round(n.x - s.x), y: Math.round(n.y - s.y) }); for (const c of (n.children || [])) walk(c); })(s);
return { name: s.name, w: Math.round(s.width), h: Math.round(s.height), texts };
"""

UPLOAD_JS = r"""
const IMG = __IMG__;
const bin = Uint8Array.from(atob(IMG.b64), (c) => c.charCodeAt(0));
const d = await penpot.uploadMediaData(IMG.name, bin, IMG.mime);
return { id: d.id, width: d.width, height: d.height, mtype: d.mtype, name: d.name, keepAspectRatio: d.keepAspectRatio };
"""

BUILD_JS = r"""
const PAGE_ID = __PAGE_ID__, ROOT_ID = __ROOT_ID__, ORIGIN = __ORIGIN__;
const NODES = __NODES__, KNOWN = __KNOWN__, IMAGES = __IMAGES__;
if (penpot.currentPage.id !== PAGE_ID) await penpot.openPage(penpot.currentFile.pages.find((p) => p.id === PAGE_ID));
const page = penpot.currentPage;
const root = page.getShapeById(ROOT_ID);
const ids = {}, errors = [];
const fonts = {};
function font(family) {
  if (!(family in fonts)) fonts[family] = penpot.fonts.findByName(family) || null;
  return fonts[family];
}
function variant(f, weight) {
  const w = parseInt(weight, 10) || 400;
  let best = f.variants[0];
  for (const v of f.variants) if (Math.abs(parseInt(v.fontWeight, 10) - w) < Math.abs(parseInt(best.fontWeight, 10) - w) && v.fontStyle !== 'italic') best = v;
  return best;
}
function parentOf(n) {
  if (n.parent < 0) return root;
  const id = ids[n.parent] || KNOWN[n.parent];
  return (id && page.getShapeById(id)) || root;
}
function place(s, n) {
  parentOf(n).appendChild(s);
  s.x = ORIGIN.x + n.box.x; s.y = ORIGIN.y + n.box.y;
}
const fillOf = (c) => [{ fillColor: c.hex, fillOpacity: c.a }];
const nm = (n) => (String(n.name || '').replace(/\//g, ' ').replace(/\s+/g, ' ').trim() || n.type).slice(0, 60);
function instanceOf(n) {
  // A library component instead of loose shapes: the closest variant, sized to the element, with the page's text.
  const L = (penpot.library.connected || []).find((l) => l.id === n.lib.library);
  if (!L) throw new Error('library not connected');
  const comp = L.components.find((c) => c.id === n.lib.id) || L.components.find((c) => c.name === n.lib.name);
  if (!comp) throw new Error('component not in library: ' + n.lib.name);
  const inst = comp.instance();
  if (comp.id !== n.lib.id && n.lib.variant) { try { inst.switchVariant(0, n.lib.variant); } catch (e) {} }
  inst.name = nm(n);
  place(inst, n);
  try { inst.resize(Math.max(n.box.w, 1), Math.max(n.box.h, 1)); } catch (e) {}
  const texts = [];
  const walk = (s) => { if (s.type === 'text') texts.push(s); for (const c of (s.children || [])) walk(c); };
  walk(inst);
  const W = Math.max(inst.width, 1), H = Math.max(inst.height, 1);
  const pos = texts.map((t) => ({ t, cx: (t.x - inst.x + t.width / 2) / W, cy: (t.y - inst.y + t.height / 2) / H }));
  const used = new Set();
  for (const want of n.lib.texts) {
    let best = null, bd = 1e9;
    for (const p of pos) { if (used.has(p.t)) continue; const d = (p.cx - want.cx) ** 2 + (p.cy - want.cy) ** 2; if (d < bd) { bd = d; best = p; } }
    if (best) {
      used.add(best.t);
      try {
        best.t.characters = want.t;
        // the page's own text box, so labels never wrap or drift
        best.t.growType = 'fixed';
        best.t.resize(Math.max(want.w, 1), Math.max(want.h, 1));
        best.t.x = inst.x + want.x - (best.t.align === 'right' || best.t.direction === 'rtl' ? 6 : 0);
        best.t.y = inst.y + want.y;
      } catch (e) {}
    }
  }
  return inst;
}
for (const n of NODES) {
  if (n.skip) continue;
  try {
    if (n.lib) {
      try { ids[n.idx] = instanceOf(n).id; continue; }
      catch (e) { errors.push(n.idx + ' library ' + n.lib.name + ': ' + String(e && e.message || e) + ' (drawn as shapes)'); n.lib = null; }
    }
    if (n.type === 'box') {
      const b = penpot.createBoard();
      b.name = nm(n);
      b.resize(Math.max(n.box.w, 0.01), Math.max(n.box.h, 0.01));
      b.fills = n.fill ? fillOf(n.fill) : [];
      b.clipContent = !!n.clip;
      [b.borderRadiusTopLeft, b.borderRadiusTopRight, b.borderRadiusBottomRight, b.borderRadiusBottomLeft] = n.radius;
      if (n.opacity < 1) b.opacity = n.opacity;
      if (n.shadow) b.shadows = [{ style: 'drop-shadow', offsetX: n.shadow.x, offsetY: n.shadow.y, blur: n.shadow.blur, spread: n.shadow.spread, color: { color: n.shadow.color.hex, opacity: n.shadow.color.a } }];
      place(b, n);
      const s = n.borders;
      const uniform = s.every((x) => x && s[0] && x.w === s[0].w && x.c.hex === s[0].c.hex);
      if (uniform) {
        b.strokes = [{ strokeColor: s[0].c.hex, strokeOpacity: s[0].c.a, strokeWidth: s[0].w, strokeAlignment: 'inner', strokeStyle: s[0].style === 'dashed' ? 'dashed' : s[0].style === 'dotted' ? 'dotted' : 'solid' }];
      } else {
        const geo = [
          [0, 0, n.box.w, s[0] && s[0].w], [n.box.w - (s[1] && s[1].w), 0, s[1] && s[1].w, n.box.h],
          [0, n.box.h - (s[2] && s[2].w), n.box.w, s[2] && s[2].w], [0, 0, s[3] && s[3].w, n.box.h]];
        ['top', 'right', 'bottom', 'left'].forEach((side, i) => {
          if (!s[i]) return;
          const r = penpot.createRectangle();
          r.name = 'border-' + side; r.fills = fillOf(s[i].c);
          r.resize(Math.max(geo[i][2], 0.01), Math.max(geo[i][3], 0.01));
          b.appendChild(r);
          r.x = ORIGIN.x + n.box.x + geo[i][0]; r.y = ORIGIN.y + n.box.y + geo[i][1];
        });
      }
      ids[n.idx] = b.id;
    } else if (n.type === 'text') {
      const t = penpot.createText(n.text);
      if (!t) throw new Error('createText returned null');
      t.name = nm(n);
      const f = font(n.font.family);
      if (f) f.applyToText(t, variant(f, n.font.weight));
      t.fontSize = String(n.font.size);
      t.lineHeight = String(+(n.font.lineHeight / n.font.size).toFixed(3));
      if (n.font.letterSpacing) t.letterSpacing = String(n.font.letterSpacing);
      t.fills = fillOf(n.color);
      t.align = n.align;
      t.direction = n.dir === 'rtl' ? 'rtl' : 'ltr';
      if (n.decoration && n.decoration.includes('underline')) t.textDecoration = 'underline';
      t.growType = 'fixed';
      const extra = n.lines > 1 ? 1 : 6;
      t.resize(n.box.w + extra, n.box.h);
      place(t, n);
      if (n.lines === 1 && n.align === 'right') t.x = ORIGIN.x + n.box.x - extra;
      if (n.lines === 1 && n.align === 'center') t.x = ORIGIN.x + n.box.x - extra / 2;
      ids[n.idx] = t.id;
    } else if (n.type === 'svg') {
      const g = penpot.createShapeFromSvg(n.svg);
      if (!g) throw new Error('svg import failed');
      g.name = nm(n);
      place(g, n);
      g.resize(Math.max(n.box.w, 0.01), Math.max(n.box.h, 0.01));
      ids[n.idx] = g.id;
    } else if (n.type === 'image') {
      const img = IMAGES[n.src];
      if (!img) throw new Error('image not uploaded');
      const r = penpot.createRectangle();
      r.name = nm(n);
      r.resize(Math.max(n.box.w, 0.01), Math.max(n.box.h, 0.01));
      r.fills = [{ fillOpacity: 1, fillImage: img }];
      place(r, n);
      ids[n.idx] = r.id;
    }
  } catch (e) {
    errors.push(n.idx + ' ' + n.type + ' ' + n.name + ': ' + String(e && e.message || e));
  }
}
return { ids, errors };
"""


PART_JS = r"""
const P = __PART__;
storage.flynnUpload = storage.flynnUpload || {};
if (P.first) storage.flynnUpload[P.key] = '';
storage.flynnUpload[P.key] += P.data;
return storage.flynnUpload[P.key].length;
"""
PART_UPLOAD_JS = r"""
const IMG = __IMG__;
const b64 = storage.flynnUpload[IMG.key]; delete storage.flynnUpload[IMG.key];
const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
const d = await penpot.uploadMediaData(IMG.name, bin, IMG.mime);
return { id: d.id, width: d.width, height: d.height, mtype: d.mtype, name: d.name, keepAspectRatio: d.keepAspectRatio };
"""
PART = 60_000
_upload_n = 0


def upload_image(bridge: Bridge, img: dict, name: str) -> dict:
    """Upload one image to the file's media. The connector caps each request at about 100 KB, so larger images go
    up in parts kept in the plugin's storage."""
    global _upload_n
    b64 = img["b64"]
    if len(b64) <= PART:
        return bridge.run_js(_js(UPLOAD_JS, IMG={"b64": b64, "mime": img["mime"], "name": name}))
    _upload_n += 1
    key = f"u{_upload_n}"
    for n, start in enumerate(range(0, len(b64), PART)):
        bridge.run_js(_js(PART_JS, PART={"key": key, "first": n == 0, "data": b64[start:start + PART]}))
    return bridge.run_js(_js(PART_UPLOAD_JS, IMG={"key": key, "mime": img["mime"], "name": name}))


def _js(template: str, **values) -> str:
    code = template
    for k, v in values.items():
        code = code.replace(f"__{k}__", json.dumps(v, ensure_ascii=False))
    return code


def options_of(run: Path) -> list[tuple[str, Path]]:
    out = []
    if (run / "page" / "index.html").is_file():
        out.append(("A", run / "page" / "index.html"))
    opt = run / "options"
    if opt.is_dir():
        for i, d in enumerate(sorted(p for p in opt.iterdir() if (p / "page" / "index.html").is_file())):
            out.append((d.name if len(d.name) <= 12 else chr(ord("B") + i), d / "page" / "index.html"))
    return out


def split_slides(tree: dict) -> list[tuple[str, dict]]:
    """A deck page as one tree per slide (boxes moved to the slide's origin), so each slide is its own board."""
    out = []
    nodes = tree["nodes"]
    for sl in tree.get("slides") or []:
        x0, y0, w, h = sl["x"], sl["y"], sl["w"], sl["h"]
        inside = [i for i, n in enumerate(nodes) if n["box"]["x"] >= x0 - 1 and n["box"]["y"] >= y0 - 1
                  and n["box"]["x"] + n["box"]["w"] <= x0 + w + 1 and n["box"]["y"] + n["box"]["h"] <= y0 + h + 1]
        remap = {old: new for new, old in enumerate(inside)}
        sub = []
        for old in inside:
            n = dict(nodes[old])
            n["parent"] = remap.get(n["parent"], -1)
            n["box"] = {**n["box"], "x": n["box"]["x"] - x0, "y": n["box"]["y"] - y0}
            sub.append(n)
        used = {n.get("src") for n in sub if n["type"] == "image"}
        name = f"{sl['i'] + 1:02d}" + (f" · {sl['title']}" if sl.get("title") else "")
        out.append((name, {**tree, "w": round(w), "h": round(h), "nodes": sub, "slides": [],
                           "images": {k: v for k, v in tree.get("images", {}).items() if k in used}}))
    return out


LIBRARIES = Path.home() / ".flynn" / "libraries"


def _fstore():
    """flynn desk's own state (scripts/fstore.py): today's files, or Postgres with FLYNN_DB_URL."""
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import fstore
    return fstore


def library_for(tree: dict, file_id: str | None = None) -> dict | None:
    """The canvas library that holds this page's components (Admin Panel kit pages tag them with data-uikit).
    A file can only use libraries from its own team, so a file in an org team gets that team's copy."""
    if not any(n.get("uikit") for n in tree["nodes"]):
        return None
    st = _fstore().get("libraries", "admin-panel", None, file=LIBRARIES / "admin-panel.json")
    if not isinstance(st, dict):
        return None
    if file_id:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
        import library
        team = library.team_of_file(file_id)
        if team and team != st.get("team"):
            st = library.for_team("admin-panel", team)
            if not st.get("file"):
                raise PenpotError("this file's team has no copy of the Admin Panel library; put the team in a "
                                  "flynn org (dashboard, org settings) and the copy is made for it")
    return st if st.get("library_id") and st.get("components") else None


def use_library(tree: dict, lib: dict) -> int:
    """Mark each outermost element the library has a component for: it becomes an instance, its inner shapes are
    skipped, and its texts (with their positions inside it) go into the instance. Returns how many were marked."""
    nodes = tree["nodes"]
    marked: dict[int, dict] = {}
    for i, n in enumerate(nodes):
        name = n.get("uikit")
        cands = (lib["components"].get(name) or []) if name and n["type"] == "box" else []
        if not cands:
            continue
        # Only self-contained components become instances. A container (a Card holding the page's own content, or
        # any element with other library components inside) stays as shapes, or its content would be lost.
        if any(c.get("w", 0) > 720 or c.get("h", 0) > 320 for c in cands) or n["box"]["w"] > 720 or n["box"]["h"] > 320:
            continue
        j, nested = i + 1, False
        sub = {i}
        while j < len(nodes):
            if nodes[j]["parent"] in sub:
                sub.add(j)
                if nodes[j].get("uikit") and nodes[j]["uikit"] in lib["components"]:
                    nested = True
            j += 1
        if nested:
            continue
        p, inside = n["parent"], False
        while p >= 0:
            if p in marked:
                inside = True
                break
            p = nodes[p]["parent"]
        if inside:
            continue
        # Fidelity first: only a variant with the same fill, border colour, texts and icons counts as a match.
        subtree = [nodes[k] for k in sorted(sub)]
        sig = {"fill": (n.get("fill") or {}).get("hex"),
               "border": next((b["c"]["hex"] for b in (n.get("borders") or []) if b), None),
               "texts": sum(x["type"] == "text" for x in subtree), "svgs": sum(x["type"] == "svg" for x in subtree)}
        cands = [c for c in cands if c.get("sig") == sig]
        if not cands:
            continue
        bw, bh = n["box"]["w"], n["box"]["h"]
        best = min(cands, key=lambda c: abs((c.get("w") or bw) - bw) + 2 * abs((c.get("h") or bh) - bh))
        marked[i] = {"library": lib["library_id"], "id": best["id"], "name": name, "variant": best.get("variant"), "texts": []}
    if not marked:
        return 0
    owner: dict[int, int] = {}
    for i, n in enumerate(nodes):
        p = n["parent"]
        while p >= 0 and p not in marked:
            p = nodes[p]["parent"]
        if p >= 0:
            owner[i] = p
    for i, root in owner.items():
        n, r = nodes[i], nodes[root]["box"]
        n["skip"] = True
        if n["type"] == "text" and n.get("text"):
            marked[root]["texts"].append({"t": n["text"], "cx": (n["box"]["x"] - r["x"] + n["box"]["w"] / 2) / max(r["w"], 1),
                                          "cy": (n["box"]["y"] - r["y"] + n["box"]["h"] / 2) / max(r["h"], 1),
                                          "x": n["box"]["x"] - r["x"], "y": n["box"]["y"] - r["y"],
                                          "w": n["box"]["w"] + 6, "h": n["box"]["h"]})
    for i, m in marked.items():
        nodes[i]["lib"] = m
    return len(marked)


# The link itself is saved through the backend (the plugin call alone does not persist it); this asks the open file
# to load the library and reports how many of its components it can see.
CONNECT_JS = r"""
const ID = __ID__;
let L = (penpot.library.connected || []).find((l) => l.id === ID);
if (!L || !L.components.length) { try { L = await penpot.library.connectLibrary(ID); } catch (e) {} }
for (let i = 0; i < 20 && !((penpot.library.connected || []).find((l) => l.id === ID) || {components: []}).components.length; i++)
  await new Promise((r) => setTimeout(r, 250));
const C = (penpot.library.connected || []).find((l) => l.id === ID);
return C ? C.components.length : 0;
"""


def connect_library(bridge: Bridge, file_id: str, lib: dict) -> int:
    """Link the file to the library (saved on the backend) and load it into the open file. Returns how many
    library components the file can see; 0 means the file must be reloaded once to load them."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
    import builder as B
    o = B.owner()
    if not any(x["id"] == lib["file"] for x in o("get-file-libraries", {"fileId": file_id})):
        o("link-file-to-library", {"fileId": file_id, "libraryId": lib["file"]})
    return int(bridge.run_js(_js(CONNECT_JS, ID=lib["library_id"])) or 0)


def build_tree(bridge: Bridge, page_id: str, root_id: str, origin: dict, tree: dict, log=print, on_chunk=None) -> dict:
    images = {}
    for src, img in tree["images"].items():
        name = Path(src.split("?")[0]).name[:60] or "image"
        try:
            images[src] = upload_image(bridge, img, name)
        except PenpotError as e:
            log(f"image upload failed: {name}: {e}")
    nodes = [dict(n, idx=i) for i, n in enumerate(tree["nodes"])]
    known, errors = {}, []
    done = 0

    def send(batch: list[dict]) -> None:
        """One request; a batch the connector refuses as too large is split in half and sent again (2026-10-06:
        a design with heavy vector icons went over the limit at 120 layers and the build failed)."""
        nonlocal done
        inside = {n["idx"] for n in batch}
        need = {n["parent"] for n in batch if n["parent"] >= 0 and n["parent"] not in inside}
        try:
            res = bridge.run_js(_js(BUILD_JS, PAGE_ID=page_id, ROOT_ID=root_id, ORIGIN=origin, NODES=batch,
                                    KNOWN={str(k): known[k] for k in need if k in known},
                                    IMAGES={n["src"]: images[n["src"]] for n in batch
                                            if n["type"] == "image" and n["src"] in images}))
        except PenpotError as e:
            if "too large" in str(e) and len(batch) > 1:
                half = len(batch) // 2
                send(batch[:half])
                send(batch[half:])
                return
            raise
        for k, v in (res.get("ids") or {}).items():
            known[int(k)] = v
        errors.extend(res.get("errors") or [])
        done += len(batch)
        log(f"built {done}/{len(nodes)}")
        if on_chunk:
            on_chunk(done, len(nodes))

    batch, size = [], 0
    for n in nodes:                                   # parents always come before their children
        n_size = len(json.dumps(n, ensure_ascii=False))
        if batch and (len(batch) >= CHUNK or size + n_size > CHUNK_BYTES):
            send(batch)
            batch, size = [], 0
        batch.append(n)
        size += n_size
    if batch:
        send(batch)
    return {"built": len(known), "total": len(nodes), "in_library": sum(1 for n in nodes if n.get("skip")), "errors": errors}


def _team_id() -> str | None:
    st = _fstore().get("settings", "penpot", None, file=Path.home() / ".flynn" / "penpot.json")
    return st.get("team") if isinstance(st, dict) else None


def penpot_link(info: dict) -> str | None:
    team = info.get("team") or _team_id()
    if not (team and info.get("file") and info.get("page")):
        return None
    base = (os.environ.get("FLYNN_PUBLIC_URL") or "http://localhost:9001").rstrip("/")
    return f"{base}/#/workspace?team-id={team}&file-id={info['file']}&page-id={info['page']}"


def board_state(run: Path, bridge: Bridge) -> dict:
    """For each board flynn pushed last time: is it still there, and did the user edit it in Penpot?"""
    try:
        prev = json.loads((run / "penpot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    boards = prev.get("boards") or {}
    ids = [b["id"] for b in boards.values() if b.get("id")]
    if not ids:
        return {}
    fps = bridge.run_js(_js(FINGERPRINT_JS, IDS=ids)) or {}
    out = {}
    for name, b in boards.items():
        fp = fps.get(b["id"])
        out[name] = {**b, "exists": fp is not None, "edited": fp is not None and b.get("fp") not in (None, fp), "now_fp": fp}
    return out


def saved_count(file_id: str, page_id: str) -> int:
    """How many layers of a page the server has saved (not what a browser tab shows)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
    import builder as B
    data = B.owner()("get-file", {"id": file_id}).get("data") or {}
    page = (data.get("pagesIndex") or {}).get(page_id) or {}
    return len(page.get("objects") or {})


def wait_saved(file_id: str, page_id: str, want: int, timeout: float = 45, log=print) -> int:
    """A build is done only when the server has it: a tab can draw layers and never save them (2026-10-06, a
    library link reloaded the tab mid-build and 577 layers were lost). Polls until most layers are saved."""
    import time as _t
    end, n = _t.time() + timeout, 0
    while _t.time() < end:
        n = saved_count(file_id, page_id)
        if n >= want * 0.9:
            return n
        _t.sleep(3)
    log(f"saved {n} of {want} layers")
    return n


def link_first(file_id: str, lib: dict) -> bool:
    """Link the library on the server before the file is opened for a build, so the open tab never reloads mid-build.
    Returns True when a new link was made."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
    import builder as B
    o = B.owner()
    if any(x["id"] == lib["file"] for x in o("get-file-libraries", {"fileId": file_id})):
        return False
    o("link-file-to-library", {"fileId": file_id, "libraryId": lib["file"]})
    return True


def push_run(run: Path, file_id: str | None = None, width: int = 1440, bridge: Bridge | None = None, log=print,
             team: str | None = None, progress=None, reopen=None) -> dict:
    """Build a run's pages on the open canvas file. `progress(stage, text, pct)` hears each step: check, capture
    (each option drawn in Chrome), setup, library, build (each batch of layers) and finish; pct runs 0 to 100.
    `reopen()` gives a fresh bridge on the same file (the builder's browser): used after linking a library, so the
    build never runs in a tab that reloads under it. The result counts only layers the server has saved."""
    from pagetree import capture
    say = progress or (lambda *a: None)
    run = Path(run).resolve()
    opts = options_of(run)
    if not opts:
        raise PenpotError(f"no pages in {run.name} (expected page/index.html or options/*/page/index.html)")
    say("check", "Checking the open file", 1)
    bridge = bridge or Bridge()
    cur = bridge.current()
    if file_id and cur.get("file") != file_id:
        raise PenpotError(f"The canvas file open in the browser is \"{cur.get('fileName')}\", not the one this conversation belongs to. Open that file and try again.")
    trees = []
    for i, (label, page) in enumerate(opts):
        log(f"capturing option {label}")
        say("capture", f"Reading option {label} ({i + 1} of {len(opts)})", 2 + 18 * i / len(opts))
        tree = capture(page, width)
        if tree.get("slides"):
            # a deck: one board per slide, labelled "<option> · NN · <slide title>"
            for name, sub in split_slides(tree):
                trees.append((f"{label} · {name}", {**sub, "source": tree["source"], "slide": True}))
        else:
            trees.append((label, tree))
    lib = None
    if trees and os.environ.get("FLYNN_NO_LIBRARY") != "1":
        try:
            lib = library_for(trees[0][1], cur.get("file"))
        except PenpotError as e:
            log(f"library not used: {e}")
    if lib and file_id:
        say("library", f"Linking {lib.get('name') or 'the library'}", 19)
        try:
            if link_first(file_id, lib) and reopen:
                bridge = reopen()                     # a fresh tab that loads the file with the library linked
        except Exception as e:  # noqa: BLE001
            log(f"library link failed: {e}")
    prev = board_state(run, bridge)
    # Boards the user edited in Penpot are kept; the new build goes beside them as the next version.
    kept = {n: b for n, b in prev.items() if b["exists"] and b["edited"]}
    remove = [b["id"] for n, b in prev.items() if b["exists"] and not b["edited"]]
    x = 0
    for b in kept.values():
        x = max(x, b.get("x", 0) + b.get("w", 0) + GAP)
    boards = []
    col, y_slide = 0, 0
    for label, tree in trees:
        title = (tree.get("title") or run.name).strip()
        base = (label if tree.get("slide") else f"{label} · {title}")[:72]
        name, v = base, 1
        while name in kept:
            v += 1
            name = f"{base} · v{v}"
        if tree.get("slide"):
            bx, by = col * (tree["w"] + 80), y_slide
            col += 1
            if col == 4:
                col, y_slide = 0, y_slide + tree["h"] + 120
            boards.append({"name": name, "x": bx, "y": by, "w": tree["w"], "h": tree["h"], "bg": tree["background"], "label": label})
            x = max(x, bx + tree["w"] + GAP)
            continue
        boards.append({"name": name, "x": x, "w": tree["w"], "h": tree["h"], "bg": tree["background"], "label": label})
        x += tree["w"] + GAP
    page_name = f"flynn · {run.name}"
    say("setup", f"Preparing page {page_name}", 20)
    # The old boards stay until the new ones are drawn and saved: a failed build must never leave the user with
    # less than he had (2026-10-06: a refused request wiped both options).
    setup = bridge.run_js(_js(SETUP_JS, PAGE_NAME=page_name, BOARDS=boards, REMOVE=[]))
    try:
        return _finish_push(bridge, run, setup, boards, trees, kept, remove, lib, cur, file_id, team, page_name, say, log)
    except BaseException:
        try:
            bridge.run_js(_js(REMOVE_JS, IDS=list(setup["roots"].values())))
            log("build failed: removed the half-built boards; the earlier boards are untouched")
        except Exception:  # noqa: BLE001
            pass
        raise


def _finish_push(bridge, run, setup, boards, trees, kept, remove, lib, cur, file_id, team, page_name, say, log) -> dict:
    result = {"file": cur.get("file"), "fileName": cur.get("fileName"), "team": team or _team_id(),
              "page": setup["page"], "pageName": page_name, "boards": {},
              "kept_edited": {n: {k: b[k] for k in ("id", "x", "w", "h", "fp", "source") if k in b} for n, b in kept.items()},
              "pushed_at": _dt.datetime.now().isoformat(timespec="seconds"), "errors": [], "warnings": []}
    if lib:
        say("library", f"Loading {lib.get('name') or 'the library'}", 23)
        try:
            seen = connect_library(bridge, cur.get("file"), lib)
            if seen:
                result["library"] = {"name": lib.get("name"), "instances": sum(use_library(t, lib) for _, t in trees)}
                log(f"library {lib.get('name')}: {result['library']['instances']} instances")
            else:
                result["warnings"].append(f"{lib.get('name')} is now linked to this file; reload the file once so its "
                                          "components load, and the next build will use them")
        except Exception as e:  # noqa: BLE001 - the build goes on with plain shapes
            result["warnings"].append(f"library not used: {e}")
    total = sum(len(t["nodes"]) for _, t in trees) or 1
    before = 0
    for k, ((label, tree), b) in enumerate(zip(trees, boards)):
        root = setup["roots"][b["name"]]
        n_board = len(tree["nodes"])

        def chunk(done, n, k=k, before=before, label=label):
            say("build", f"Building {label} ({k + 1} of {len(trees)}): {done} of {n} layers", 25 + 72 * (before + done) / total)
        chunk(0, n_board)
        stats = build_tree(bridge, setup["page"], root, {"x": b["x"], "y": b.get("y", 0)}, tree, log=log, on_chunk=chunk)
        before += n_board
        result["boards"][b["name"]] = {"id": root, "option": label, "x": b["x"], "w": b["w"], "h": b["h"],
                                       "built": stats["built"], "total": stats["total"],
                                       "in_library": stats.get("in_library", 0),
                                       "source": str(Path(tree["source"]).relative_to(run))}
        result["errors"] += stats["errors"]
        result["warnings"] += tree.get("warnings", [])
    if file_id:
        built = sum(b.get("built", 0) for b in result["boards"].values())
        say("finish", "Checking that the canvas saved every layer", 97)
        n = wait_saved(file_id, setup["page"], built + len(result["boards"]), log=log)
        if n < (built + len(result["boards"])) * 0.9:
            raise PenpotError(f"The canvas did not save the build: the server has {n} of {built} layers. Nothing is lost "
                              "on flynn's side; press Try again.")
        result["saved"] = n
    if remove:                                        # the new boards are saved: now the earlier ones can go
        bridge.run_js(_js(REMOVE_JS, IDS=remove))
    say("finish", "Finishing: fingerprints and zoom", 98)
    fps = bridge.run_js(_js(FINGERPRINT_JS, IDS=[b["id"] for b in result["boards"].values()])) or {}
    for b in result["boards"].values():
        b["fp"] = fps.get(b["id"])
    # Edited boards stay tracked, so a later push still leaves them alone.
    for n, b in kept.items():
        result["boards"].setdefault(n, {"id": b["id"], "option": b.get("option"), "x": b.get("x", 0), "w": b.get("w", 0),
                                        "h": b.get("h", 0), "fp": b.get("fp"), "source": b.get("source"), "edited": True})
    try:
        bridge.run_js("penpot.viewport.zoomToFitAll(); return true;")
    except PenpotError:
        pass
    result["link"] = penpot_link(result)
    (run / "penpot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def pull_run(run: Path, bridge: Bridge | None = None) -> dict:
    """Bring the user's Penpot edits back to the run: an image and the texts of every board, and which ones he edited."""
    run = Path(run).resolve()
    bridge = bridge or Bridge()
    state = board_state(run, bridge)
    if not state:
        raise PenpotError(f"{run.name} has not been built on the canvas yet")
    out_dir = run / "penpot-pull"
    out_dir.mkdir(exist_ok=True)
    summary = {"pulled_at": _dt.datetime.now().isoformat(timespec="seconds"), "boards": {}}
    for name, b in state.items():
        if not b["exists"]:
            summary["boards"][name] = {"exists": False}
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "board"
        png = bridge.export_png(b["id"])
        (out_dir / f"{slug}.png").write_bytes(png)
        read = bridge.run_js(_js(READ_JS, ID=b["id"])) or {}
        (out_dir / f"{slug}.json").write_text(json.dumps(read, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["boards"][name] = {"exists": True, "edited_in_penpot": b["edited"], "image": f"penpot-pull/{slug}.png",
                                   "texts": f"penpot-pull/{slug}.json", "source": b.get("source")}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build the run's options in the open Penpot file")
    r.add_argument("run")
    r.add_argument("--file")
    r.add_argument("--team")
    r.add_argument("--width", type=int, default=1440)
    pl = sub.add_parser("pull", help="bring the user's Penpot edits back: board images, texts, which boards he edited")
    pl.add_argument("run")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pull":
            print(json.dumps(pull_run(Path(a.run)), ensure_ascii=False, indent=2))
            return 0
        res = push_run(Path(a.run), a.file, a.width, log=lambda m: print(m, file=sys.stderr), team=a.team)
    except PenpotError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "page": res["pageName"], "link": res.get("link"),
                      "boards": {k: f"{v.get('built', '-')}/{v.get('total', '-')}" + (" (edited by the user, kept)" if v.get("edited") else "") for k, v in res["boards"].items()},
                      "errors": res["errors"][:10], "error_count": len(res["errors"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
