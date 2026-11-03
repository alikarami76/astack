"""Turn a recording into design system candidates: tokens and components, with evidence.

Reads every page's dom.json and full.png in a session folder and writes:
  <session>/mine.json                      tokens, components (with variants, states, pages, crops), stats
  <session>/components/<Name>/*.png        cropped examples, one per variant
  <session>/components/<Name>/snippet.html one real, scrubbed HTML example
and stores the results in the flynn store under system "rec:<system>", tier "inferred", origin "generated"
(so they never reach the git export, and never merge into a verified library of the same name).

How components are found (no source code needed):
- controls and roles: button, text field, select, checkbox, radio, switch, tab, table, dialog, menu, navigation;
- component-like class names (card, badge, chip, tag, avatar, alert, pagination, breadcrumb, ...), with
  CSS-module hashes and BEM element/modifier parts removed, seen at least twice.
Variants come from look (filled, outlined, text), colour and size; states from what was seen (disabled).
These are candidates: flynn reviews and names them with the user before anything moves into a real library.

CLI:  mine.py <session folder> [--no-store]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "kb"))
sys.path.insert(0, str(FLYNN / "adapters" / "browser"))

KEYWORDS = ["button", "btn", "card", "chip", "badge", "tag", "pill", "avatar", "tabs", "tab", "toggle", "switch",
            "checkbox", "radio", "select", "dropdown", "menu", "input", "textfield", "field", "search", "modal",
            "dialog", "drawer", "tooltip", "popover", "toast", "snackbar", "alert", "banner", "notice", "breadcrumb",
            "pagination", "pager", "stepper", "accordion", "table", "listitem", "list-item", "navbar", "sidebar",
            "header", "footer", "toolbar", "filter", "empty", "skeleton", "spinner", "loader", "progress", "divider"]
KW = re.compile("|".join(sorted(KEYWORDS, key=len, reverse=True)), re.I)
UTILITY = re.compile(r"^(-?[mp][trblxy]?-|w-|h-|min-|max-|text-|bg-|flex|grid|gap-|items-|justify-|self-|rounded|border|font-|leading-|tracking-|shadow|opacity-|z-|top-|left-|right-|bottom-|inset|hidden|block|inline|absolute|relative|fixed|sticky|overflow|cursor|transition|duration|ease|hover:|focus:|sm:|md:|lg:|xl:|dark:)")
PARTS = {"root", "container", "wrapper", "main", "base", "component", "box", "inner"}
LAYOUT_WORDS = {"navigation", "nav", "navbar", "table", "header", "footer", "sidebar", "side", "toolbar", "banner",
                "tabs", "dialog", "modal", "drawer", "alert", "filter", "menu"}
LAYOUT_OK = {"Navigation", "Table", "Header", "Footer", "Sidebar", "Navbar", "Toolbar", "Banner", "Tabs", "Dialog",
             "Modal", "Drawer", "Alert", "Filter"}


def pascal(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", s) if w)


KEYSET = {k.replace("-", "") for k in KEYWORDS} | {"fab", "chip", "tag", "list", "item", "tile", "panel", "widget", "label"}
NOT_COMPONENT_START = {"hide", "show", "user", "visually", "only", "is", "has", "js", "u", "util", "text"}
VARIANT_WORDS = re.compile(r"^(x?small|medium|x?large|xs|sm|md|lg|xl|filled\w*|outlined\w*|text\w*|ghost\w*|primary|secondary|"
                           r"tertiary|neutral\w*|error|danger|warning|success|info|brand|active|selected|disabled|"
                           r"radius\w*|rounded|round|block|full\w*|dense|compact|icon\w*)$", re.I)


def _hashless(tok: str) -> str:
    """Remove a CSS-module hash suffix: 'ReportCard-dQesSG' -> 'ReportCard'. A suffix counts as a hash only when
    it has a digit, an underscore, or mixed case, so plain words ('hide-tablet') are kept."""
    m = re.match(r"^(.+?)[-_]([A-Za-z0-9_-]{5,8})$", tok)
    if m:
        h = m.group(2)
        if re.search(r"\d|_", h) or (re.search(r"[a-z]", h) and re.search(r"[A-Z]", h)):
            return m.group(1)
    return tok


def _words(name: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", name)]


def parse_class(tok: str) -> tuple[str, str | None] | None:
    """('Block', None) for a component's own class, ('Block', 'part') for a part or modifier, or None."""
    if UTILITY.match(tok):
        return None
    t = _hashless(tok)
    part = None
    for sep in ("__", "--"):
        if sep in t:
            t, part = t.split(sep, 1)
            part = _hashless(part)
            break
    if re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)+", t):           # namespaced snake paths: keep the last word
        t = t.rsplit("_", 1)[1]
    words = _words(t)
    if not words or words[0] in NOT_COMPONENT_START or not any(w in KEYSET for w in words):
        return None
    name = pascal("-".join(w for w in words if not w.isdigit()))
    return (name[:40], part) if name else None


def class_stem(cls: str) -> str | None:
    """The component a class list names, if any (its own class, not a part)."""
    stems = [r[0] for r in map(parse_class, cls.split()) if r and r[1] is None]
    return stems[0] if stems else None


def class_variants(cls: str) -> list[str]:
    """Variant-like parts and modifiers in the class list: 'Button__filledPrimary' -> 'filledPrimary'."""
    out = []
    for r in map(parse_class, cls.split()):
        part = r[1].split("-")[0] if r and r[1] else ""      # tolerate any leftover hash suffix
        if part and VARIANT_WORDS.match(part):
            out.append(part)
    return out


def semantic_kind(e: dict) -> str | None:
    tag, role, typ = e["tag"], e["role"].lower(), e["type"].lower()
    if tag == "button" or role == "button" or (tag == "input" and typ in ("button", "submit", "reset")):
        return "Button"
    if tag == "input":
        return {"checkbox": "Checkbox", "radio": "Radio", "search": "SearchField", "range": "Slider",
                "file": "FileInput"}.get(typ, "TextField" if typ not in ("hidden", "image", "color") else None)
    if tag == "select" or role in ("combobox", "listbox"):
        return "Select"
    if tag == "textarea":
        return "TextArea"
    return {"switch": "Switch", "tab": "Tab", "tablist": "Tabs", "dialog": "Dialog", "alertdialog": "Dialog",
            "menu": "Menu", "menuitem": "MenuItem", "progressbar": "Progress", "slider": "Slider", "tooltip": "Tooltip",
            "alert": "Alert", "navigation": "Navigation", "checkbox": "Checkbox", "radio": "Radio",
            "table": "Table", "grid": "Table"}.get(role) or {"table": "Table", "nav": "Navigation", "dialog": "Dialog",
                                                               "progress": "Progress", "hr": "Divider"}.get(tag)


# ---------- colours and values ----------

def norm_color(c: str) -> str | None:
    if not c:
        return None
    m = re.match(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)", c)
    if not m:
        return None
    r, g, b = (int(float(x)) for x in m.groups()[:3])
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    if a == 0:
        return None
    hexv = f"#{r:02x}{g:02x}{b:02x}"
    return hexv if a >= 0.995 else f"{hexv}{round(a * 255):02x}"


def px(v: str) -> float | None:
    m = re.match(r"^(-?[\d.]+)px$", (v or "").strip())
    return float(m.group(1)) if m else None


def has_border(s: dict) -> bool:
    return (px(s.get("borderTopWidth")) or 0) > 0 and s.get("borderTopStyle") not in ("none", "hidden")


def look(e: dict) -> str:
    s = e["s"]
    if norm_color(s.get("backgroundColor")) or "gradient" in (s.get("backgroundImage") or ""):
        return "filled"
    if has_border(s):
        return "outlined"
    return "text"


# ---------- reading a session ----------

def load_pages(session: Path) -> list[dict]:
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    pages = []
    for p in meta.get("pages", []):
        for size in p.get("sizes", []):
            d = Path(size["dir"])
            if (d / "dom.json").exists():
                pages.append({"page": p, "width": size["width"], "dir": d,
                              "dom": json.loads((d / "dom.json").read_text(encoding="utf-8"))})
    return pages


def scrub_snippet(html: str) -> str:
    import capture
    html = capture.scrub_html(html)
    html = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "name@example.com", html)
    html = re.sub(r"(?<![#\w])\d{5,}", lambda m: "0" * len(m.group(0)), html)          # ids, phones, amounts
    html = re.sub(r"[۰-۹]{5,}", lambda m: "۰" * len(m.group(0)), html)  # Persian digits
    html = re.sub(r'(src|href)="(?!#)[^"]*"', r'\1="#"', html)
    return html


# ---------- mining ----------

def mine_tokens(pages: list[dict]) -> dict:
    var_by_value: dict[str, list[str]] = defaultdict(list)
    vars_all: dict[str, str] = {}
    use = {"text": Counter(), "surface": Counter(), "border": Counter()}
    typ, space, radius, shadow = Counter(), Counter(), Counter(), Counter()
    fonts = Counter()
    for pg in pages:
        dom = pg["dom"]
        for k, v in dom.get("vars", {}).items():
            vars_all.setdefault(k, v)
        for f in dom.get("fonts", []):
            fonts[f] += 1
        for e in dom["els"]:
            s = e["s"]
            if e["text"]:
                c = norm_color(s.get("color"))
                if c:
                    use["text"][c] += 1
                fam = (s.get("fontFamily") or "").split(",")[0].strip("\"' ")
                typ[(fam, s.get("fontSize"), s.get("fontWeight"), s.get("lineHeight"))] += 1
            bg = norm_color(s.get("backgroundColor"))
            if bg:
                use["surface"][bg] += 1
            if has_border(s):
                bc = norm_color(s.get("borderTopColor"))
                if bc:
                    use["border"][bc] += 1
            for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "gap", "marginTop", "marginBottom"):
                v = px(s.get(k))
                if v and 0 < v <= 96:
                    space[v] += 1
            r = px((s.get("borderRadius") or "").split(" ")[0])
            if r and r > 0:
                radius[min(r, 999)] += 1
            sh = s.get("boxShadow")
            if sh and sh != "none":
                shadow[sh] += 1
    for k, v in vars_all.items():
        c = norm_color(v) if v.startswith("rgb") else (v.lower() if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v) else None)
        if c:
            var_by_value[c if len(c) != 4 else "#" + "".join(ch * 2 for ch in c[1:])].append(k)

    tokens = []
    for kind, counter in use.items():
        for i, (c, n) in enumerate(counter.most_common(16), 1):
            if n < 3:
                break
            names = sorted(var_by_value.get(c, []), key=len)
            tokens.append({"name": names[0] if names else f"color/{kind}-{i}", "kind": "color", "value": c,
                           "uses": n, "role": kind, "css_vars": names[:4]})
    for i, ((fam, size, weight, lh), n) in enumerate(typ.most_common(12), 1):
        if n < 3:
            break
        tokens.append({"name": f"type/{size}-{weight}", "kind": "type", "value": f"{fam} {size}/{lh} {weight}", "uses": n})
    for v, n in sorted(space.most_common(12)):
        if n >= 5:
            tokens.append({"name": f"space/{v:g}", "kind": "space", "value": f"{v:g}px", "uses": n})
    for v, n in sorted(radius.most_common(8)):
        if n >= 3:
            tokens.append({"name": f"radius/{v:g}", "kind": "radius", "value": f"{v:g}px", "uses": n})
    for i, (v, n) in enumerate(shadow.most_common(6), 1):
        if n >= 2:
            tokens.append({"name": f"shadow/{i}", "kind": "elevation", "value": v, "uses": n})
    # de-duplicate names (two colours can share the fallback-free var name in odd themes)
    seen, out = set(), []
    for t in tokens:
        if (t["kind"], t["name"]) in seen:
            continue
        seen.add((t["kind"], t["name"]))
        out.append(t)
    return {"tokens": out, "css_vars": vars_all, "fonts": [f for f, _ in fonts.most_common(20)]}


def mine_components(pages: list[dict]) -> list[dict]:
    groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    widest = max(pg["width"] for pg in pages)
    for pg in [p for p in pages if p["width"] == widest]:      # one count per page; other widths are evidence only
        doc_w = (pg["dom"].get("doc") or [pg["width"]])[0] or pg["width"]
        els = pg["dom"]["els"]
        for e in els:
            stem = class_stem(e["cls"]) if e["cls"] else None
            name = stem or semantic_kind(e)                      # the product's own name beats the generic role
            if not name:
                continue
            w, h = e["box"][2], e["box"][3]
            if w > 0.9 * doc_w and not (set(_words(name)) & LAYOUT_WORDS):
                continue
            if w < 8 or h < 8:
                continue
            groups[name].append((e, pg))
    comps = []
    for name, items in groups.items():
        semantic = any(semantic_kind(e) for e, _ in items)
        layout = bool(set(_words(name)) & LAYOUT_WORDS)
        if not semantic and not layout and len(items) < 2:
            continue
        variants: dict[tuple, list] = defaultdict(list)
        for e, pg in items:
            s = e["s"]
            key = (look(e), norm_color(s.get("backgroundColor")) or "", norm_color(s.get("color")) or "",
                   norm_color(s.get("borderTopColor")) if has_border(s) else "", round((e["box"][3]) / 4) * 4,
                   s.get("fontSize"), s.get("borderRadius"))
            variants[key].append((e, pg))
        ranked = sorted(variants.items(), key=lambda kv: -len(kv[1]))[:8]
        heights = sorted({k[4] for k, _ in ranked})
        size_name = {}
        if len(heights) == 1:
            size_name = {heights[0]: "medium"}
        else:
            labels = ["small", "medium", "large", "x-large"] if len(heights) <= 4 else [f"h{h}" for h in heights]
            size_name = {h: labels[min(i, len(labels) - 1)] for i, h in enumerate(heights)}
        vlist = []
        for key, members in ranked:
            lk, bg, fg, bc, hgt, fs, rad = key
            e, pg = members[0]
            vlist.append({"classes": class_variants(e["cls"]), "look": lk, "color": bg or bc or fg, "text": fg, "size": size_name.get(hgt, f"h{hgt}"),
                          "height": hgt, "font_size": fs, "radius": rad, "count": len(members),
                          "example": {"page": pg["page"]["n"], "width": pg["width"], "box": e["box"], "dir": str(pg["dir"]),
                                      "dpr": pg["dom"].get("dpr", 1), "html": e.get("html", "")},
                          "label": e["aria"] or e["text"]})
        states = ["default"] + (["disabled"] if any(e["disabled"] for e, _ in items) else [])
        pages_seen = sorted({pg["page"]["n"] for _, pg in items})
        classes = Counter(class_stem(e["cls"]) for e, _ in items if e["cls"])
        named = Counter(v for e, _ in items for v in class_variants(e["cls"]))
        comps.append({"name": name, "count": len(items), "pages": pages_seen, "semantic": semantic,
                      "class_names": [c for c, _ in classes.most_common(3) if c], "class_variants": [v for v, _ in named.most_common(12)],
                      "states": states, "variants": vlist})
    comps.sort(key=lambda c: (-len(c["pages"]), -c["count"]))
    return comps[:60]


def crop_examples(session: Path, comps: list[dict]) -> None:
    from PIL import Image
    cache: dict[str, Image.Image] = {}
    for c in comps:
        out = session / "components" / c["name"]
        out.mkdir(parents=True, exist_ok=True)
        snippet_done = False
        for i, v in enumerate(c["variants"], 1):
            ex = v["example"]
            full = Path(ex["dir"]) / "full.png"
            if str(full) not in cache:
                if len(cache) > 6:
                    cache.clear()
                cache[str(full)] = Image.open(full).convert("RGB")
            img = cache[str(full)]
            k = ex.get("dpr") or 1               # full.png is in device pixels; boxes are CSS pixels
            x, y, w, h = ex["box"]
            pad = 12
            box = (max(0, int((x - pad) * k)), max(0, int((y - pad) * k)),
                   min(img.width, int((x + w + pad) * k)), min(img.height, int((y + min(h, 900) + pad) * k)))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            path = out / f"{i:02d}-{v['look']}-{v['size']}.png"
            img.crop(box).save(path)
            v["crop"] = str(path)
            if ex.get("html") and not snippet_done:
                (out / "snippet.html").write_text(scrub_snippet(ex["html"]), encoding="utf-8")
                c["snippet"] = str(out / "snippet.html")
                snippet_done = True
            ex.pop("html", None)


def store_results(session: Path, meta: dict, toks: dict, comps: list[dict]) -> dict:
    import store
    c = store.connect()
    system = f"rec:{meta['system']}"
    src = f"recorder:{meta['id']}"
    n_tok = n_cmp = 0
    for t in toks["tokens"]:
        store.upsert(c, "token", {"system": system, "name": t["name"], "kind": t["kind"], "level": "raw", "value": t["value"],
                                  "tier": "inferred", "source": src,
                                  "notes": f"Recorded: used {t['uses']} times" + (f" as {t['role']}" if t.get("role") else "")},
                     origin="generated", tags=["recorder"])
        n_tok += 1
    for cp in comps:
        axes = {"look": sorted({v["look"] for v in cp["variants"]}), "size": sorted({v["size"] for v in cp["variants"]})}
        store.upsert(c, "component", {
            "system": system, "name": cp["name"],
            "description": f"Recorded candidate: seen {cp['count']} times on {len(cp['pages'])} page(s). {meta.get('description', '')[:300]}".strip(),
            "anatomy": {"class_names": cp["class_names"], "snippet": cp.get("snippet"),
                        "examples": [v.get("crop") for v in cp["variants"] if v.get("crop")]},
            "variants": axes, "props": {}, "states": cp["states"],
            "usage": {"pages": cp["pages"], "count": cp["count"], "session": str(session)},
            "tier": "inferred", "code_path": "", "drift_notes": "Recorded from the live product; not reviewed yet."},
            origin="generated", tags=["recorder"])
        n_cmp += 1
    return {"system": system, "tokens": n_tok, "components": n_cmp}


def mine(session: Path, store_rows: bool = True) -> dict:
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    pages = load_pages(session)
    if not pages:
        raise ValueError("this recording has no pages")
    toks = mine_tokens(pages)
    comps = mine_components(pages)
    crop_examples(session, comps)
    result = {"session": meta["id"], "system": meta["system"], "description": meta.get("description", ""),
              "pages": [{"n": p["n"], "title": p["title"], "url": p["url"]} for p in meta["pages"]],
              "tokens": toks["tokens"], "fonts": toks["fonts"], "css_vars": len(toks["css_vars"]), "components": comps}
    (session / "css_vars.json").write_text(json.dumps(toks["css_vars"], ensure_ascii=False, indent=1), encoding="utf-8")
    if store_rows:
        result["stored"] = store_results(session, meta, toks, comps)
    (session / "mine.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="mine.py")
    ap.add_argument("session")
    ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args()
    r = mine(Path(a.session), store_rows=not a.no_store)
    print(json.dumps({"tokens": len(r["tokens"]), "components": [(c["name"], c["count"]) for c in r["components"]],
                      "stored": r.get("stored")}, ensure_ascii=False, indent=1))
