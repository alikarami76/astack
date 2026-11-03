#!/usr/bin/env python3
"""A design system's product knowledge: its pages (screens), functionalities (features) and UX flows, in the store.

  product_map.py ingest <run folder> --system admin-panel [--dry-run]   merge scouts/*.json into the store and export
  product_map.py tree --system admin-panel                               the page hierarchy as an indented list
  product_map.py show <route> --system admin-panel                        one screen with its features and flows

Merging: screens are keyed by (system, route). When two scouts describe the same route, list fields are joined
(unique), text fields keep the longer, more specific value, and the sitemap scout's navigation fields (parent,
nav_group, nav_order, modules) win. Every row gets origin "scan" and the run id; tiers stay as the scouts set them
(code-only or inferred): nothing here is ever marked verified (only the owner's review does that).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import store  # noqa: E402

NAV_FIELDS = ("parent", "nav_group", "nav_order", "modules")
TIERS_OK = ("code-only", "inferred", "proposed", "drift")


def _merge(a: dict, b: dict, nav_from_b: bool) -> dict:
    out = dict(a)
    for k, v in b.items():
        if v in (None, "", [], {}):
            continue
        cur = out.get(k)
        if isinstance(v, list) or isinstance(cur, list):
            seen, merged = set(), []
            for x in (cur or []) + (v if isinstance(v, list) else [v]):
                key = json.dumps(x, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    merged.append(x)
            out[k] = merged
        elif k in NAV_FIELDS and nav_from_b:
            out[k] = v
        elif not cur or (isinstance(v, str) and isinstance(cur, str) and len(v) > len(cur) and k not in NAV_FIELDS):
            out[k] = v
    return out


def _norm_route(r: str) -> str:
    r = "/" + str(r or "").strip().lstrip("/")
    return r.rstrip("/") or "/"


def collect(run: Path) -> dict:
    screens: dict[str, dict] = {}
    features: dict[str, dict] = {}
    flows: dict[str, dict] = {}
    nav = None
    files = sorted((run / "scouts").glob("*.json"))
    files.sort(key=lambda p: p.stem != "sitemap")          # the sitemap first; area scouts add detail on top
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if f.stem == "sitemap":
            nav = {"nav": d.get("nav"), "bottom_nav": d.get("bottom_nav"), "layouts": d.get("layouts")}
        for s in d.get("screens", []):
            if not s.get("route"):
                continue
            s = {**s, "route": _norm_route(s["route"])}
            if s.get("parent"):
                s["parent"] = _norm_route(s["parent"])
            r = s["route"]
            screens[r] = _merge(screens[r], s, nav_from_b=(f.stem == "sitemap")) if r in screens else s
        for kind, bucket in (("features", features), ("flows", flows)):
            for x in d.get(kind, []):
                n = str(x.get("name") or "").strip()
                if n:
                    bucket[n.lower()] = _merge(bucket[n.lower()], x, False) if n.lower() in bucket else x
    return {"screens": screens, "features": features, "flows": flows, "nav": nav}


def ingest(run: Path, system: str, dry: bool = False) -> dict:
    data = collect(run)
    conn = store.connect()
    counts = {"screen": 0, "feature": 0, "flow": 0, "skipped": []}
    for etype, rows in (("screen", data["screens"].values()), ("feature", data["features"].values()), ("flow", data["flows"].values())):
        cols = store.TYPES[etype]["cols"]
        for r in rows:
            row = {k: r.get(k) for k in cols if k in r}
            row["system"], row["run_id"] = system, run.name
            row["tier"] = r.get("tier") if r.get("tier") in TIERS_OK else "inferred"
            for k in store.JSON_COLS & set(cols):
                if k in row and isinstance(row[k], str):
                    row[k] = [row[k]]
            for k in cols:
                if k not in store.JSON_COLS and isinstance(row.get(k), (list, dict)):
                    row[k] = json.dumps(row[k], ensure_ascii=False)
            try:
                if not dry:
                    store.upsert(conn, etype, row, origin="scan", tags=["product-map"])
                counts[etype] += 1
            except (ValueError, TypeError) as e:
                counts["skipped"].append(f"{etype} {r.get('route') or r.get('name')}: {e}")
    if data["nav"] and not dry:
        out = Path(os.environ.get("FLYNN_SYSTEMS_DIR") or HERE.parent / "systems") / system / "navigation.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data["nav"], ensure_ascii=False, indent=1), encoding="utf-8")
    if not dry:
        conn.commit()
        for t in ("screen", "feature", "flow"):
            store.export(conn, etype=t)
    return counts


def _rows(system: str, etype: str) -> list[dict]:
    conn = store.connect()
    table = store.TYPES[etype]["table"]
    cur = conn.execute(f"SELECT * FROM {table} WHERE system = ?", (system,))
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def tree(system: str) -> list[str]:
    rows = {r["route"]: r for r in _rows(system, "screen")}
    kids: dict[str, list] = {}
    for r in rows.values():
        kids.setdefault(r.get("parent") or "", []).append(r)
    out = []

    def walk(parent: str, depth: int):
        for r in sorted(kids.get(parent, []), key=lambda x: (str(x.get("nav_order") or "~"), x["route"])):
            out.append(f"{'  ' * depth}{r['route']}  {r.get('name_fa') or ''} · {r.get('name') or ''}")
            if depth < 8:
                walk(r["route"], depth + 1)
    walk("", 0)
    orphans = [r for p, rs in kids.items() if p and p not in rows for r in rs]
    for r in orphans:
        out.append(f"(parent {r['parent']} not mapped) {r['route']}  {r.get('name') or ''}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest"); i.add_argument("run"); i.add_argument("--system", required=True); i.add_argument("--dry-run", action="store_true")
    t = sub.add_parser("tree"); t.add_argument("--system", required=True)
    s = sub.add_parser("show"); s.add_argument("route"); s.add_argument("--system", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        print(json.dumps(ingest(Path(a.run), a.system, a.dry_run), ensure_ascii=False, indent=1))
    elif a.cmd == "tree":
        print("\n".join(tree(a.system)))
    else:
        r = next((x for x in _rows(a.system, "screen") if x["route"] == _norm_route(a.route)), None)
        if not r:
            print("no such screen"); return 1
        feats = [f["name"] for f in _rows(a.system, "feature") if r["route"] in (f.get("screens") or "")]
        flows = [f["name"] for f in _rows(a.system, "flow") if r["route"] in (f.get("screens") or "")]
        print(json.dumps({**r, "features": feats, "flows": flows}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
