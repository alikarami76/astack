#!/usr/bin/env python3
"""Load a design system folder (systems/<system>/) into the knowledge store.

  ingest_system.py systems/admin-panel --run 2026-09-30-admin-panel-library [--dry-run]

The folder holds components/<Name>.json, patterns/<name>.json and tokens.json in the
format of systems/admin-panel/SPEC-FORMAT.md. The JSON files stay the full record
(imports, examples, TSX snippets, live matches); the store gets the searchable row,
with a pointer back to the file. Rows merge on (system, name), so re-running is safe.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FLYNN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FLYNN / "kb"))
import store  # noqa: E402

TIERS = {"verified", "figma-only", "code-only", "drift", "inferred", "proposed"}
COMPONENT_REQUIRED = ("system", "name", "description", "code_path", "tier")


def _j(v):
    return None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))


def check_component(d: dict, path: Path) -> list[str]:
    errs = [f"{path.name}: missing {k}" for k in COMPONENT_REQUIRED if not d.get(k)]
    if d.get("tier") and d["tier"] not in TIERS:
        errs.append(f"{path.name}: unknown tier {d['tier']!r}")
    u = d.get("usage") or {}
    if isinstance(u, dict) and u and u.get("source") not in ("code", "inferred"):
        errs.append(f"{path.name}: usage.source must be 'code' or 'inferred'")
    return errs


def _variants(v):
    """The store wants {axis: [str, ...]}; specs sometimes write an axis as a note."""
    if not isinstance(v, dict):
        return None
    return {k: [str(x) for x in (vals if isinstance(vals, list) else [vals])] for k, vals in v.items()}


def _props(p):
    if not isinstance(p, dict):
        return None
    return {k: (v if isinstance(v, dict) else {"type": str(v)}) for k, v in p.items()}


def component_row(d: dict, rel: str, run: str) -> dict:
    usage = d.get("usage")
    desc = d["description"].strip()
    if "deprecated" in (d.get("tags") or []):
        desc = "DEPRECATED, not for new designs. " + desc
    if d.get("scope") == "page-local":
        # one page's own component (not exported for reuse): flynn may draw it on that page, never reuse it elsewhere
        where = ", ".join((d.get("pages") or [])[:3])
        desc = f"Page-specific, not a library component (on {where or 'one page'}). " + desc
        if d.get("built_from"):
            desc += f" Built from: {', '.join(d['built_from'])}."
    elif d.get("pages"):
        desc += f" Used on {len(d['pages'])} pages."
    if d.get("import"):
        desc += f" Import: {d['import']}"
    desc += f" Full spec: {rel}"
    return {
        "system": d["system"], "name": d["name"], "description": desc,
        "anatomy": _j(d.get("anatomy")), "variants": _j(_variants(d.get("variants"))), "props": _j(_props(d.get("props"))),
        "states": _j(d.get("states") if isinstance(d.get("states"), list) else None), "usage": _j(usage), "rtl_notes": d.get("rtl_notes"),
        "code_path": d.get("code_path"), "code_props": d.get("code_props"),
        "figma_key": d.get("figma_key"), "tier": d["tier"], "drift_notes": d.get("drift_notes"), "run_id": run,
    }


def pattern_row(d: dict, rel: str, run: str) -> dict:
    rules = d.get("rules") or ""
    if isinstance(rules, (list, dict)):
        rules = json.dumps(rules, ensure_ascii=False)
    extra = {k: d[k] for k in ("anatomy", "states", "pages", "best_snaps") if d.get(k)}
    return {
        "system": d["system"], "name": d["name"],
        "description": f"{d.get('description', '').strip()} Full spec: {rel}",
        "components": _j(d.get("components") or []),
        "rules": rules + ("\n\n" + json.dumps(extra, ensure_ascii=False) if extra else ""),
        "reference_ids": _j(d.get("reference_ids") or []), "run_id": run,
    }


KIND_MAP = {"shadow": "elevation", "z": "other", "breakpoint": "other"}


def token_row(t: dict, system: str, run: str) -> dict:
    notes = " ".join(x for x in [
        t.get("notes") or "",
        f"css {t['css_var']}" if t.get("css_var") else "",
        f"scss {t['scss_var']}" if t.get("scss_var") else "",
        f"live: {t['live_check']}" if t.get("live_check") else "",
    ] if x).strip()
    return {
        "system": system, "name": t["name"], "kind": KIND_MAP.get(t.get("kind"), t.get("kind") or "other"), "level": t.get("level", "raw"),
        "value": _j(t.get("value")), "refers_to": t.get("refers_to"), "tier": t.get("tier", "code-only"),
        "source": t.get("source"), "notes": notes or None, "run_id": run,
    }


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(FLYNN))
    except ValueError:
        return str(p)


def collect(folder: Path, run: str) -> tuple[list[dict], list[str]]:
    items, errs = [], []
    system = folder.name
    for p in sorted((folder / "components").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        e = check_component(d, p)
        errs += e
        if not e:
            items.append({"type": "component", "row": component_row(d, _rel(p), run),
                          "tags": [system, d.get("category") or "uncategorised", *(d.get("tags") or []),
                                   *([d["scope"]] if d.get("scope") else [])]})
    for p in sorted((folder / "components" / "local").glob("*.jsonl")):     # page-specific components, light records
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            d = json.loads(line)
            e = check_component(d, p)
            errs += [f"{x} (line {i})" for x in e]
            if not e:
                items.append({"type": "component", "row": component_row(d, f"{_rel(p)}:{i}", run),
                              "tags": [system, d.get("category") or "uncategorised", "page-local"]})
    for p in sorted((folder / "patterns").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d.get("name"):
            errs.append(f"{p.name}: missing name")
            continue
        d.setdefault("system", system)
        items.append({"type": "pattern", "row": pattern_row(d, _rel(p), run), "tags": [system]})
    tp = folder / "tokens.json"
    if tp.exists():
        seen = set()
        for t in json.loads(tp.read_text(encoding="utf-8")):
            if not t.get("name"):
                errs.append("tokens.json: a token has no name")
                continue
            if t["name"] in seen:
                errs.append(f"tokens.json: duplicate token {t['name']}")
                continue
            seen.add(t["name"])
            items.append({"type": "token", "row": token_row(t, system, run), "tags": [system, t.get("kind") or "other"]})
    return items, errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--run", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    folder = Path(a.folder).resolve()
    items, errs = collect(folder, a.run)
    for e in errs:
        print("error:", e, file=sys.stderr)
    counts = {}
    for it in items:
        counts[it["type"]] = counts.get(it["type"], 0) + 1
    if a.dry_run or errs:
        print(json.dumps({"would_add": counts, "errors": len(errs)}))
        return 1 if errs else 0
    bad = []
    for it in items:  # validate everything before writing anything
        try:
            store._validate(it["type"], it["row"])
        except ValueError as e:
            bad.append(f"{it['type']} {it['row'].get('name')}: {e}")
    if bad:
        for b in bad:
            print("error:", b, file=sys.stderr)
        return 1
    c = store.connect()
    for it in items:
        store.upsert(c, it["type"], it["row"], origin="flynn", tags=it["tags"])
    for etype in {it["type"] for it in items}:
        store.export(c, etype=etype)
    print(json.dumps({"added_or_merged": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
