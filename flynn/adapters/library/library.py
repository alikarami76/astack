"""flynn's design system libraries on the canvas.

Each design system flynn knows (Admin Panel from code, anything recorded) gets one library file in the
"flynn libraries" project, published as a shared library, so it appears under Libraries and can be connected
to any file. The flynn store stays the source of truth; `sync` brings the library file up to date through the
builder's hidden Chrome (builder.py), so the user's own tab is never touched.

  library.py list                       the libraries and their files
  library.py ensure <system>            create the library file (once) and share it
  library.py sync <system> [--only tokens|components]
  library.py sync <system> --team ID    sync only that team's copy (an org team)

A canvas file can only use libraries from its own team, so each library has a synced copy in every team that
belongs to a flynn org (adapters/orgs). A sync with no team brings the home library and every copy up to date.

Local state (canvas ids, which store rows map to which library items): fstore ns libraries, key <system> or
<system>@<team> (~/.flynn/libraries/<key>.json, or Postgres with FLYNN_DB_URL).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FLYNN / "kb"))
import builder as B  # noqa: E402
sys.path.insert(0, str(FLYNN / "scripts"))
import fstore  # noqa: E402

STATE = Path.home() / ".flynn" / "libraries"
PROJECT = "flynn libraries"
LOG = B.log

TITLES = {"admin-panel": "Admin Panel"}


def title(system: str) -> str:
    base = system[4:] if system.startswith("rec:") else system
    t = TITLES.get(base) or " ".join(w.capitalize() for w in re.split(r"[-_ ]+", base))
    return t + (" (recorded)" if system.startswith("rec:") else "")


def slug(system: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", system.lower()).strip("-")


# fstore ns "libraries"; on files, ~/.flynn/libraries/<key>.json as before (the gallery folders beside them stay files).
fstore.files("libraries", folder=lambda: STATE, indent=1)


def _key(system: str, team: str | None = None) -> str:
    """The home library is <system>; a copy in another team (an org's team) is <system>@<team>."""
    return f"{slug(system)}@{team}" if team else slug(system)


def state(system: str, team: str | None = None) -> dict:
    return fstore.get("libraries", _key(system, team)) or {"system": system}


def save(system: str, st: dict, team: str | None = None) -> None:
    fstore.put("libraries", _key(system, team), st)


def _copy_team(system: str, team: str | None) -> str | None:
    """None for the home library (in the flynn team), else the team the copy lives in."""
    return None if not team or team == state(system).get("team") else team


def org_teams() -> list[str]:
    """Every team in a flynn org. A canvas file can only use libraries from its own team, so each library has a
    synced copy in each of these (orgs.py)."""
    sys.path.insert(0, str(FLYNN / "adapters" / "orgs"))
    import orgs
    d = orgs.load()
    return sorted({t for o in d.get("orgs", []) for t in o.get("teams", [])})


def for_team(system: str, team: str | None) -> dict:
    """The library a file in `team` can use: the home one in its own team, or that team's copy."""
    copy = _copy_team(system, team)
    return state(system, copy) if copy else state(system)


def team_of_file(file_id: str) -> str | None:
    try:
        o = B.owner()
        f = o("get-file", {"id": file_id})
        return f.get("teamId") or o("get-project", {"id": f["projectId"]}).get("teamId")
    except Exception:  # noqa: BLE001
        return None


def ensure(system: str, team: str | None = None) -> dict:
    """The library file for a system: created once in the "flynn libraries" project of its team and shared.
    `team` (another team in an org) makes or finds that team's copy."""
    copy = _copy_team(system, team)
    st = state(system, copy)
    o = B.owner()
    tid = copy or B.team_id(o)
    if st.get("file"):
        try:
            o("get-file", {"id": st["file"]})
            return st
        except Exception:
            st.pop("file", None)                      # deleted by hand: make a new one
    projects = o("get-projects", {"teamId": tid})
    proj = next((p for p in projects if p["name"] == PROJECT), None) or o("create-project", {"teamId": tid, "name": PROJECT})
    name = f"{title(system)} library"
    files = o("get-project-files", {"projectId": proj["id"]})
    f = next((x for x in files if x["name"] == name), None) or o("create-file", {"projectId": proj["id"], "name": name})
    o("set-file-shared", {"id": f["id"], "isShared": True})
    st.update({"system": system, "team": tid, "project": proj["id"], "file": f["id"], "name": name})
    if copy:
        st["copy_of"] = state(system).get("file")
    save(system, st, copy)
    LOG(f"library ensured {system} in {tid} -> {f['id']}")
    return st


# ---------- tokens ----------

def _rows(system: str, kind: str | None = None) -> list[dict]:
    import store
    c = store.connect()
    q = "select * from tokens where system=?" + (" and kind=?" if kind else "")
    return [dict(r) for r in c.execute(q, (system, kind) if kind else (system,))]


def _resolve(rows: dict, name: str, depth: int = 0) -> str | None:
    r = rows.get(name)
    if not r or depth > 8:
        return None
    if r.get("refers_to"):
        return _resolve(rows, r["refers_to"], depth + 1)
    return r["value"]


def _color(v: str) -> tuple[str, float] | None:
    v = (v or "").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})([0-9a-fA-F]{2})?", v)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return "#" + h.lower(), (int(m.group(2), 16) / 255 if m.group(2) else 1.0)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        r, g, b = (int(float(x)) for x in m.groups()[:3])
        a = m.group(4)
        a = (float(a[:-1]) / 100 if a and a.endswith("%") else float(a)) if a else 1.0
        return f"#{r:02x}{g:02x}{b:02x}", round(a, 3)
    return None


def _token_name(name: str) -> str:
    """Penpot reads dots as groups, and a token cannot also be a group ("color.button.primary" next to
    "color.button.primary.hover"), so only the first word becomes the group: color-button-primary-hover ->
    color.button-primary-hover."""
    clean = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9-]+", "-", name)).strip("-")   # letters, digits, hyphens only
    head, _, rest = clean.partition("-")
    return f"{head}.{rest}" if rest else clean


def token_plan(system: str) -> dict:
    """Library colours, typographies and a token set, from the store."""
    rows = {r["name"]: r for r in _rows(system)}
    colors, toks = [], []
    for r in rows.values():
        if r["kind"] != "color":
            continue
        val = _resolve(rows, r["name"])
        c = _color(val or "")
        if not c:
            continue
        words = r["name"].removeprefix("color-").split("-")
        group = ("Palette/" if r["level"] == "raw" else "Semantic/") + (words[0] if len(words) > 1 else "other")
        colors.append({"key": r["id"], "name": r["name"].removeprefix("color-"), "path": group, "color": c[0], "opacity": c[1]})
        ref = r.get("refers_to")
        toks.append({"type": "color", "name": _token_name(r["name"]),
                     "value": "{" + _token_name(ref) + "}" if ref and ref in rows else (val if c[1] < 1 else c[0])})
    typos = []
    for r in rows.values():
        if r["kind"] != "type" or r["level"] != "semantic":
            continue
        try:
            v = json.loads(r["value"])
        except ValueError:
            continue
        if not isinstance(v, dict) or "size" not in v:
            continue
        size = str(v.get("size_lg") or v["size"]).removesuffix("px")
        for suffix, lh in (("", v.get("line_height")), ("-c", v.get("line_height_compact"))):
            if lh is None:
                continue
            typos.append({"key": r["id"] + suffix, "name": r["name"] + suffix, "path": "Text", "size": size,
                          "weight": str(v.get("weight", 400)), "lineHeight": str(lh)})
    for r in rows.values():                      # recorded type styles: "Vazirmatn 12px/25.8px 700"
        m = re.fullmatch(r"(.+?) ([\d.]+)px/([\d.]+)(px)? (\d+)", (r["value"] or "").strip()) if r["kind"] == "type" else None
        if m:
            size, lh = float(m.group(2)), float(m.group(3))
            typos.append({"key": r["id"], "name": r["name"].replace("type/", ""), "path": "Text", "size": f"{size:g}",
                          "weight": m.group(5), "lineHeight": f"{(lh / size if m.group(4) or lh > 4 else lh):.3g}"})
    rec_fam = next((re.match(r"(.+?) [\d.]+px", r["value"]).group(1) for r in rows.values()
                    if r["kind"] == "type" and re.match(r"(.+?) [\d.]+px", r["value"] or "")), None)
    fam = next((r["value"].split(",")[0].strip().strip("'\"") for r in rows.values() if r["name"] == "font-family"), rec_fam or "Vazirmatn")
    kinds = {"space": "spacing", "radius": "borderRadius", "size": "sizing"}
    for r in rows.values():
        t = kinds.get(r["kind"])
        # spacing steps "refer to" the base step but carry their own value; follow references only for aliases
        own = (r["value"] or "").strip()
        val = own if re.fullmatch(r"-?[\d.]+(px|rem)?", own) else _resolve(rows, r["name"])
        if t and val and re.fullmatch(r"-?[\d.]+(px|rem)?", val.strip()):
            toks.append({"type": t, "name": _token_name(r["name"]), "value": val.strip()})
    for r in rows.values():
        if r["kind"] == "type" and r["name"].startswith("font-weight-"):
            toks.append({"type": "fontWeights", "name": _token_name(r["name"]), "value": r["value"]})
    toks.append({"type": "fontFamilies", "name": "font.family", "value": fam})
    # plain values first, references after: a fresh token set refuses a reference to a token it does not hold yet
    toks.sort(key=lambda t: str(t["value"]).startswith("{"))
    return {"colors": colors, "typographies": typos, "family": fam, "tokens": toks, "set": slug(system)}


TOKENS_JS = r"""
const P = __PLAN__;
const lib = penpot.library.local;
const NS = 'flynn';
const keyOf = (x) => { try { return x.getSharedPluginData(NS, 'key'); } catch (e) { return ''; } };
const out = { colors: { made: 0, updated: 0, removed: 0 }, typographies: { made: 0, updated: 0, removed: 0 }, tokens: { made: 0, updated: 0 }, errors: [] };
// colours
const haveC = new Map(lib.colors.map((c) => [keyOf(c), c]));
const wantC = new Set();
for (const c of P.colors) {
  try {
    wantC.add(c.key);
    let x = haveC.get(c.key);
    if (x) out.colors.updated++; else { x = lib.createColor(); x.setSharedPluginData(NS, 'key', c.key); out.colors.made++; }
    x.name = c.name; x.path = c.path; x.color = c.color; x.opacity = c.opacity;
  } catch (e) { out.errors.push('color ' + c.name + ': ' + (e.message || e)); }
}
for (const [k, x] of haveC) if (k && !wantC.has(k)) { try { x.remove(); out.colors.removed++; } catch (e) {} }
// typographies
const font = penpot.fonts.findByName(P.family) || penpot.fonts.findByName('Work Sans');
const variant = (w) => { let b = font.variants[0]; for (const v of font.variants) if (v.fontStyle !== 'italic' && Math.abs(+v.fontWeight - +w) < Math.abs(+b.fontWeight - +w)) b = v; return b; };
const haveT = new Map(lib.typographies.map((t) => [keyOf(t), t]));
const wantT = new Set();
for (const t of P.typographies) {
  try {
    wantT.add(t.key);
    let x = haveT.get(t.key);
    if (x) out.typographies.updated++; else { x = lib.createTypography(); x.setSharedPluginData(NS, 'key', t.key); out.typographies.made++; }
    x.name = t.name; x.path = t.path;
    if (font) x.setFont(font, variant(t.weight));
    x.fontSize = t.size; x.lineHeight = t.lineHeight;
  } catch (e) { out.errors.push('typography ' + t.name + ': ' + (e.message || e)); }
}
for (const [k, x] of haveT) if (k && !wantT.has(k)) { try { x.remove(); out.typographies.removed++; } catch (e) {} }
// token set
try {
  const cat = lib.tokens;
  let set = cat.sets.find((s) => s.name === P.set);
  if (!set) set = cat.addSet({ name: P.set, active: true });
  if (!set.active) set.toggleActive();
  const wantFirst = new Set(P.tokens.map((t) => t.name));
  for (const t of set.tokens) if (!wantFirst.has(t.name)) { try { t.remove(); } catch (e) {} }
  const have = new Map(set.tokens.map((t) => [t.name, t]));
  for (const t of P.tokens) {
    try {
      const old = have.get(t.name);
      if (old && old.type === t.type && String(old.value) === String(t.value)) continue;
      if (old) { old.remove(); out.tokens.updated++; } else out.tokens.made++;
      set.addToken({ type: t.type, name: t.name, value: t.value });
    } catch (e) { out.errors.push('token ' + t.name + ': ' + (e.message || e)); }
  }
  // the set is flynn's own: tokens no longer in the store (or from an older naming) go
  const want = new Set(P.tokens.map((t) => t.name));
  out.tokens.removed = 0;
  for (const t of set.tokens) if (!want.has(t.name)) { try { t.remove(); out.tokens.removed++; } catch (e) {} }
} catch (e) { out.errors.push('token set: ' + (e.message || e)); }
out.errors = out.errors.slice(0, 15);
return out;
"""


def _js(template: str, **values) -> str:
    code = template
    for k, v in values.items():
        code = code.replace(f"__{k}__", json.dumps(v, ensure_ascii=False))
    return code


def sync_tokens(system: str, bridge) -> dict:
    plan = token_plan(system)
    return bridge.run_js(_js(TOKENS_JS, PLAN=plan))


def sync_components(system: str, bridge, prev: dict | None = None) -> dict:
    import components as C
    if system.startswith("rec:"):
        cap = C.recorded_samples(system)             # the real recorded elements, as flynn's review named them
    else:
        samples = C.kit_samples()
        folder = STATE / slug(system) / "gallery"
        cap = [c for c in C.capture_samples(C.gallery(samples, folder), samples) if c["tree"]["nodes"]]
    if not cap:
        return {"components": 0, "variants": 0, "errors": ["no samples (record the product first)"], "map": {}}
    if prev and all(o.get("main") for v in prev.values() for o in v):
        return C.update(bridge, cap, prev, log=lambda m: LOG(m))      # keeps component ids
    return C.build(bridge, cap, log=lambda m: LOG(m))


def sync(system: str, only: str | None = None, force: bool = False, team: str | None = None) -> dict:
    """Sync one library file. With no team: the home library, then its copy in every org team."""
    copy = _copy_team(system, team)
    st = ensure(system, copy)
    b = B.open_file(st["file"], st["team"])
    result = {"file": st["file"], "name": st["name"]}
    if only in (None, "tokens"):
        result["tokens"] = sync_tokens(system, b)
    if only in (None, "components"):
        import hashlib
        import components as C
        if system.startswith("rec:"):
            src = [p.read_bytes() for p in sorted((C.RECORDINGS / C.slugify(system[4:])).glob("*/*.json"))
                   if p.name in ("mine.json", "review.json")]
            digest = hashlib.sha1(b"".join(src)).hexdigest()
        else:
            digest = hashlib.sha1("".join(x["file"] + x["markup"] for x in C.kit_samples()).encode()).hexdigest()
        if st.get("components") and st.get("components_hash") == digest and not force:
            result["components"] = {"skipped": "the source has not changed since the last sync"}
        else:
            # in place when possible (same ids, so instances in other files stay linked); --force rebuilds all
            comp = sync_components(system, b, None if force else st.get("components"))
            st["components"] = comp.pop("map", {})
            st["components_hash"] = digest
            st["library_id"] = b.run_js("return penpot.library.local.id")
            result["components"] = comp
    st["synced"] = time.strftime("%Y-%m-%d %H:%M")
    save(system, st, copy)
    LOG(f"library synced {system} in {st['team']}: {json.dumps(result)[:300]}")
    if team is None:
        for t in org_teams():
            if t == st["team"]:
                continue
            try:
                r = sync(system, only, force, team=t)
                result.setdefault("copies", {})[t] = {"file": r["file"], "components": r.get("components")}
            except Exception as e:  # noqa: BLE001 - one team's copy failing never stops the others
                result.setdefault("copies", {})[t] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return result


def libraries() -> list[dict]:
    """The home libraries (one per system). Copies in org teams are reached with for_team()."""
    return [v for k, v in fstore.list("libraries").items() if "@" not in k]


def copies(system: str) -> list[dict]:
    return [v for k, v in fstore.list("libraries").items() if k.startswith(f"{slug(system)}@")]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="library.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("ensure")
    e.add_argument("system")
    sy = sub.add_parser("sync")
    sy.add_argument("system")
    sy.add_argument("--only", choices=["tokens", "components"])
    sy.add_argument("--force", action="store_true", help="rebuild components even if the kit is unchanged (breaks instance links)")
    sy.add_argument("--team", help="sync only this team's copy")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        print(json.dumps(sync(a.system, a.only, a.force, a.team), indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "list":
        for lib in libraries():
            print(f"{lib['system']:24} {lib.get('name', '')}  {lib.get('file', '')}  synced {lib.get('synced', 'never')}")
            for c in copies(lib["system"]):
                print(f"{'':24}   copy in team {c.get('team', '')[:8]}  {c.get('file', '')}  synced {c.get('synced', 'never')}")
    elif a.cmd == "ensure":
        print(json.dumps(ensure(a.system), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
