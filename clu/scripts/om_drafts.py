#!/usr/bin/env python3
"""Staged OpenMetadata drafts for pricing data, kept in the pricing knowledge base under om/.

clu cannot write to OpenMetadata (read-only token), so pricing tables, metrics and glossary terms are drafted here in the
catalog's own conventions, and the OM owners load them. Examples:
  om_drafts.py conventions                       derive CONVENTIONS.md and conventions.json from the latest snapshot
  om_drafts.py new table item_prices --target shop_mysql.default.shop.item_prices --from-kb src_...
  om_drafts.py validate [files]                  check drafts against the conventions (exit 1 on errors)
  om_drafts.py render [files]                    rewrite the .md page of each draft from its .json
  om_drafts.py status                            rewrite STATUS.md; an entry is loaded once its FQN is in the snapshot
  om_drafts.py payload <file>                    print the OM request body (semantics merged into descriptions)

Root: $CLU_OM_DRAFTS (default ~/agent-stack-data/kb/om).
Draft file: {"kind": table|metric|glossaryTerm, "status": draft|reviewed, "target_fqn", "proposed": [tags or services
that do not exist in OM yet], "evidence": [doc paths or clu:<kb id>], "notes", "entity": {OM-shaped fields; table columns
carry a separate "semantics" object, tags are plain FQN strings}}.
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "kb"))
import om  # noqa: E402

KINDS = {"table": "tables", "metric": "metrics", "glossaryTerm": "glossary"}
SEM_ENUMS = ("semanticRole", "cardinalityClass", "columnRole", "lookupStrategy", "cutKind")
SEM_BOOLS = ("canFilter", "canBreakdown", "preferredForBreakdown", "preferredForLookupSearch")
TABLE_EXT_ENUMS = ("shape", "sizeClass", "defaultWindow", "timeAdditive", "calendars")
METRIC_EXT_ENUMS = ("metricType", "aggregation", "semanticRole", "businessModelScope", "unit", "isBase")
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|\?\?\?")
FA = re.compile(r"[؀-ۿ]")
MIN_DESC = 30


def root() -> Path:
    return Path(os.environ.get("CLU_OM_DRAFTS") or
                Path.home() / "agent-stack-data/kb/om")


# ---------- conventions ----------

def conventions(write: bool = True) -> dict:
    st = om.state()
    if not st:
        raise FileNotFoundError("no OpenMetadata snapshot; run `om.py pull` first")
    tables, metrics, terms, tags = (om.load(x) for x in ("table", "metric", "glossaryTerm", "tag"))
    sem_vals, sem_keys, sem_n, cols = collections.defaultdict(set), collections.Counter(), 0, []
    for t in tables:
        for c in t.get("columns") or []:
            prose, sem = om.split_semantics(c.get("description"))
            cols.append((t["name"], c, prose, sem))
            sem_n += bool(sem)
            for k, v in sem.items():
                sem_keys[k] += 1
                if k in SEM_ENUMS:
                    sem_vals[k].add(str(v))
    t_ext = collections.Counter(k for t in tables for k in (t.get("extension") or {}))
    t_vals = {k: sorted({str((t.get("extension") or {})[k]) for t in tables if k in (t.get("extension") or {})})
              for k in TABLE_EXT_ENUMS}
    m_ext = collections.Counter(k for m in metrics for k in (m.get("extension") or {}))
    m_vals = {k: sorted({str((m.get("extension") or {})[k]) for m in metrics if k in (m.get("extension") or {})})
              for k in METRIC_EXT_ENUMS}
    tag_set = {x.get("fullyQualifiedName") for x in tags if x.get("fullyQualifiedName")}
    tag_set |= {t["fullyQualifiedName"] for t in terms if t.get("fullyQualifiedName")}  # glossary terms are tags too
    nt, nm = max(len(tables), 1), max(len(metrics), 1)
    conv = {
        "snapshot": st.get("snapshot"), "pulled_at": st.get("pulled_at"),
        "counts": {"tables": len(tables), "columns": len(cols), "columns_with_semantics": sem_n,
                   "metrics": len(metrics), "glossary_terms": len(terms)},
        "column_semantics": {k: sorted(v) for k, v in sem_vals.items()},
        "column_semantic_keys": sorted(sem_keys), "column_semantic_key_counts": dict(sem_keys.most_common()),
        "table_extension_keys": sorted(t_ext), "table_extension_required": sorted(k for k, v in t_ext.items() if v == nt),
        "table_extension_values": t_vals,
        "metric_extension_keys": sorted(m_ext), "metric_extension_required": sorted(k for k, v in m_ext.items() if v == nm),
        "metric_extension_usual": sorted(k for k, v in m_ext.items() if nm > v >= 0.9 * nm),
        "metric_extension_values": m_vals,
        "metricType": sorted({m.get("metricType") for m in metrics if m.get("metricType")}),
        "unitOfMeasurement": sorted({m.get("unitOfMeasurement") for m in metrics if m.get("unitOfMeasurement")}),
        "tags": sorted(tag_set),
        "glossaries": sorted({(t.get("glossary") or {}).get("name") for t in terms if (t.get("glossary") or {}).get("name")}),
    }
    if write:
        r = root()
        r.mkdir(parents=True, exist_ok=True)
        (r / "conventions.json").write_text(json.dumps(conv, ensure_ascii=False, indent=1) + "\n")
        (r / "CONVENTIONS.md").write_text(_conventions_md(conv, tables, metrics, terms, cols))
    return conv


def _ex(text: str, n: int = 260) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _conventions_md(conv, tables, metrics, terms, cols) -> str:
    k = conv["counts"]
    L = ["# OpenMetadata conventions", "",
         f"_Generated by `om_drafts.py conventions` from the OpenMetadata snapshot of {conv['pulled_at']} "
         f"({k['tables']} tables, {k['columns']} columns, {k['metrics']} metrics, {k['glossary_terms']} glossary terms). "
         "Do not edit by hand: re-run the command after a pull. `om_drafts.py validate` enforces the vocabularies below._", "",
         "## Tables", "",
         "A table description says what one row is and what the table answers, then how to use it "
         "(\"Use it to …\"), and names the sibling table for what it does not hold.", ""]
    for t in sorted(tables, key=lambda x: -len(x.get("columns") or []))[:3]:
        L.append(f"- `{t['name']}` — {_ex(t.get('description'), 400)}")
    L += ["", f"Extension fields on every table: {', '.join(f'`{x}`' for x in conv['table_extension_required'])}. "
          f"Others in use: {', '.join(f'`{x}`' for x in conv['table_extension_keys'] if x not in conv['table_extension_required'])}.", ""]
    for key, vals in conv["table_extension_values"].items():
        if vals:
            L.append(f"- `{key}`: {', '.join(f'`{v}`' for v in vals)}")
    L += ["", "## Columns", "",
          f"{k['columns_with_semantics']} of {k['columns']} column descriptions end in a machine-readable block: a blank "
          "line, `[semantics]`, a newline, then one line of JSON. The prose before it says what the column is, which "
          "sentinels mean unknown (`-1`, `19700101`), how to filter it, and what it joins to.", "",
          "Semantics keys, by how often they are used: " +
          ", ".join(f"`{x}` ({n})" for x, n in conv["column_semantic_key_counts"].items()) + ".", ""]
    for key, vals in conv["column_semantics"].items():
        L.append(f"- `{key}`: {', '.join(f'`{v}`' for v in vals)}")
    L += ["", "Examples by semantic role:", ""]
    seen = set()
    for tname, c, prose, sem in cols:
        role = sem.get("semanticRole")
        if role and role not in seen:
            seen.add(role)
            L.append(f"- **{role}** — `{tname}.{c['name']}` ({c.get('dataType')}): {_ex(prose, 220)}  ")
            L.append(f"  `{_ex(json.dumps(sem, ensure_ascii=False), 300)}`")
    L += ["", "## Metrics", "",
          "A metric description defines it in one or two sentences, names what it excludes, and says how to aggregate "
          "it (\"Ratio — recompute per group, never average the averages\"). The SQL expression is in "
          "`metricExpression.code`.", "",
          f"- `metricType`: {', '.join(f'`{v}`' for v in conv['metricType'])}; `unitOfMeasurement`: "
          f"{', '.join(f'`{v}`' for v in conv['unitOfMeasurement'])} (money uses `OTHER` plus `customUnitOfMeasurement`).",
          f"- Extension fields on every metric: {', '.join(f'`{x}`' for x in conv['metric_extension_required'])}; "
          f"on most: {', '.join(f'`{x}`' for x in conv['metric_extension_usual'])}.", ""]
    for key, vals in conv["metric_extension_values"].items():
        if vals:
            L.append(f"- extension `{key}`: {', '.join(f'`{v}`' for v in vals)}")
    L += ["", "Examples:", ""]
    for m in metrics[:4]:
        L.append(f"- `{m['name']}` ({m.get('displayName')}) — {_ex(m.get('description'), 260)} "
                 f"`{_ex((m.get('metricExpression') or {}).get('code'), 120)}`")
    L += ["", "Each metric is tagged with its glossary term (`Shop.<Term>`), `Domain.<domain>`, `Metric.Base` or "
          "`Metric.Derived`, `Semantic.metric` and `AgentAccess.Readable`.", "",
          "## Glossary terms", "",
          f"Glossaries: {', '.join(conv['glossaries'])}. A term has a one-sentence description, synonyms in English and "
          "Farsi (including the spellings and transliterations people type), and a `GlossaryKind` tag "
          "(`metric`, `dimension` or `column`).", ""]
    for t in terms[:4]:
        L.append(f"- **{t['name']}** — {_ex(t.get('description'), 200)} Synonyms: {', '.join((t.get('synonyms') or [])[:8])}")
    L += ["", "## Tags in use", "", ", ".join(f"`{x}`" for x in conv["tags"] if not x.startswith("Shop.")), ""]
    return "\n".join(L)


def _conv() -> dict:
    f = root() / "conventions.json"
    if f.exists():
        return json.loads(f.read_text())
    return conventions(write=False)


# ---------- validation ----------

def _desc_errors(where: str, text: str | None) -> list[str]:
    text = text or ""
    if PLACEHOLDER.search(text) or len(text.strip()) < MIN_DESC:
        return [f"{where}: description is missing, a placeholder or shorter than {MIN_DESC} characters"]
    return []


def validate(d: dict, conv: dict | None = None) -> tuple[list[str], list[str]]:
    conv = conv or _conv()
    errs, warns = [], []
    kind, e = d.get("kind"), d.get("entity") or {}
    if kind not in KINDS:
        return [f"kind must be one of {sorted(KINDS)}"], []
    if not e.get("name"):
        return ["entity.name is missing"], []
    if not d.get("target_fqn"):
        errs.append("target_fqn is missing")
    proposed, known = set(d.get("proposed") or []), set(conv["tags"])
    name = e["name"]

    def tags_ok(where, tags):
        for t in tags or []:
            if t not in known and t not in proposed:
                errs.append(f"{where}: tag {t} does not exist in OpenMetadata (list it under \"proposed\" if it is a new one)")

    tags_ok(name, e.get("tags"))
    errs += _desc_errors(name, e.get("description"))
    if kind in ("table", "metric") and "AgentAccess.Readable" not in (e.get("tags") or []):
        warns.append(f"{name}: every OM table and metric carries AgentAccess.Readable")

    if kind == "table":
        ext = e.get("extension") or {}
        for k in conv["table_extension_required"]:
            if not ext.get(k):
                errs.append(f"{name}: extension.{k} is required")
        for k, v in ext.items():
            if k not in conv["table_extension_keys"] and k not in proposed:
                errs.append(f"{name}: extension.{k} is not a field OpenMetadata uses")
            vals = conv["table_extension_values"].get(k)
            if vals and str(v) not in vals:
                errs.append(f"{name}: extension.{k} {v} is not one of {vals}")
        if not e.get("columns"):
            errs.append(f"{name}: no columns")
        for c in e.get("columns") or []:
            where = f"{name}.{c.get('name')}"
            if not c.get("dataType"):
                errs.append(f"{where}: dataType is missing")
            errs += _desc_errors(where, c.get("description"))
            if "[semantics]" in (c.get("description") or ""):
                errs.append(f"{where}: put the semantics in the \"semantics\" object, not in the description")
            sem = c.get("semantics") or {}
            if not sem.get("semanticRole"):
                errs.append(f"{where}: semantics.semanticRole is required")
            for k, v in sem.items():
                if k not in conv["column_semantic_keys"] and k not in proposed:
                    errs.append(f"{where}: semantics.{k} is not a key OpenMetadata uses")
                if k in SEM_ENUMS and conv["column_semantics"].get(k) and str(v) not in conv["column_semantics"][k]:
                    errs.append(f"{where}: semantics.{k} {v} is not one of {conv['column_semantics'][k]}")
                if k in SEM_BOOLS and not isinstance(v, bool):
                    errs.append(f"{where}: semantics.{k} must be true or false")
            tags_ok(where, c.get("tags"))

    elif kind == "metric":
        if not e.get("displayName"):
            errs.append(f"{name}: displayName is missing")
        if not (e.get("metricExpression") or {}).get("code"):
            errs.append(f"{name}: metricExpression.code is missing")
        if e.get("metricType") not in conv["metricType"]:
            errs.append(f"{name}: metricType {e.get('metricType')} is not one of {conv['metricType']}")
        if e.get("unitOfMeasurement") not in conv["unitOfMeasurement"]:
            errs.append(f"{name}: unitOfMeasurement {e.get('unitOfMeasurement')} is not one of {conv['unitOfMeasurement']}")
        ext = e.get("extension") or {}
        for k in conv["metric_extension_required"]:
            if not ext.get(k):
                errs.append(f"{name}: extension.{k} is required")
        for k in conv.get("metric_extension_usual", []):
            if not ext.get(k):
                warns.append(f"{name}: most metrics set extension.{k}")
        for k, v in ext.items():
            if k not in conv["metric_extension_keys"] and k not in proposed:
                errs.append(f"{name}: extension.{k} is not a field OpenMetadata uses")
            vals = conv["metric_extension_values"].get(k)
            if vals and str(v) not in vals:
                errs.append(f"{name}: extension.{k} {v} is not one of {vals}")
        if not any(t.startswith("Shop.") for t in e.get("tags") or []):
            warns.append(f"{name}: metrics are tagged with their glossary term (Shop.<Term>)")

    else:
        if e.get("glossary") not in conv["glossaries"]:
            errs.append(f"{name}: glossary must be one of {conv['glossaries']}")
        syn = e.get("synonyms") or []
        if not any(FA.search(s) for s in syn):
            errs.append(f"{name}: synonyms need at least one Farsi form")
        if len(syn) < 2:
            warns.append(f"{name}: OM terms usually list several synonyms (English, Farsi, transliterations)")
        if not any(t.startswith("GlossaryKind.") for t in e.get("tags") or []):
            errs.append(f"{name}: a GlossaryKind tag (metric, dimension or column) is required")
    return errs, warns


# ---------- payload, files, status ----------

def payload(d: dict) -> dict:
    e = copy.deepcopy(d["entity"])
    e["tags"] = [{"tagFQN": t} for t in e.get("tags") or []]
    for c in e.get("columns") or []:
        sem = c.pop("semantics", None)
        if sem:
            c["description"] = f"{c.get('description', '').rstrip()}\n\n[semantics]\n{json.dumps(sem, ensure_ascii=False)}"
        c["tags"] = [{"tagFQN": t} for t in c.get("tags") or []]
    return e


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-").lower() or "entry"


def path_for(d: dict) -> Path:
    return root() / KINDS[d["kind"]] / f"{_slug(d['entity']['name'])}.json"


def render(d: dict, errs=None, warns=None) -> str:
    e, kind = d["entity"], d["kind"]
    if errs is None:
        errs, warns = validate(d)
    L = [f"# {e.get('displayName') or e['name']}", "",
         f"- **Kind:** {kind} · **Status:** {d.get('status', 'draft')} · **Target:** `{d.get('target_fqn')}`",
         f"- **Checks:** " + ("pass" if not errs else f"{len(errs)} error(s)") + (f", {len(warns)} warning(s)" if warns else "")]
    if d.get("proposed"):
        L.append(f"- **Needs the OM owners to create:** {', '.join(f'`{x}`' for x in d['proposed'])}")
    if d.get("evidence"):
        L.append(f"- **Evidence:** {', '.join(d['evidence'])}")
    if e.get("tags"):
        L.append(f"- **Tags:** {', '.join(f'`{t}`' for t in e['tags'])}")
    L += ["", e.get("description") or "", ""]
    if d.get("notes"):
        L += [f"> {d['notes']}", ""]
    if kind == "table":
        if e.get("extension"):
            L += ["| Extension | Value |", "|---|---|", *[f"| `{k}` | {v} |" for k, v in e["extension"].items()], ""]
        L += ["| Column | Type | Role | Description |", "|---|---|---|---|"]
        for c in e.get("columns") or []:
            role = (c.get("semantics") or {}).get("semanticRole", "")
            L.append(f"| `{c.get('name')}` | {c.get('dataType', '')} | {role} | {_ex(c.get('description'), 400).replace('|', '/')} |")
        L.append("")
    elif kind == "metric":
        L += [f"- **Expression:** `{(e.get('metricExpression') or {}).get('code')}`",
              f"- **Type / unit:** {e.get('metricType')} · {e.get('unitOfMeasurement')}"
              + (f" ({e.get('customUnitOfMeasurement')})" if e.get("customUnitOfMeasurement") else "")]
        L += [f"- extension `{k}`: {v}" for k, v in (e.get("extension") or {}).items()] + [""]
    else:
        L += [f"- **Glossary:** {e.get('glossary')}", f"- **Synonyms:** {', '.join(e.get('synonyms') or [])}", ""]
    if errs or warns:
        L += ["## Checks", "", *[f"- ❌ {x}" for x in errs], *[f"- ⚠️ {x}" for x in warns or []], ""]
    L += [f"_Source of truth: `{path_for(d).name}`. Regenerate this page with `om_drafts.py render`._", ""]
    return "\n".join(L)


def save(d: dict) -> Path:
    p = path_for(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    d.setdefault("status", "draft")
    d["updated"] = _dt.date.today().isoformat()
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    p.with_suffix(".md").write_text(render(d))
    return p


def drafts() -> list[tuple[Path, dict]]:
    out = []
    for sub in KINDS.values():
        for f in sorted((root() / sub).glob("*.json")):
            out.append((f, json.loads(f.read_text())))
    return out


def status(write: bool = True) -> list[dict]:
    try:
        loaded = {x.get("fullyQualifiedName") for ix in ("table", "metric", "glossaryTerm") for x in om.load(ix)}
    except FileNotFoundError:
        loaded = set()
    conv = _conv()
    rows = []
    for f, d in drafts():
        errs, warns = validate(d, conv)
        st = "loaded" if d.get("target_fqn") in loaded else d.get("status", "draft")
        rows.append({"kind": d["kind"], "name": d["entity"]["name"], "target": d.get("target_fqn"), "status": st,
                     "errors": len(errs), "warnings": len(warns), "path": str(f.relative_to(root())),
                     "updated": d.get("updated", "")})
    if write:
        L = ["# OpenMetadata drafts — status", "",
             f"_Generated by `om_drafts.py status` on {_dt.date.today().isoformat()}. `loaded` means the target name "
             "exists in the latest OpenMetadata snapshot._", "",
             "| Kind | Name | Target | Status | Checks | Updated |", "|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda x: (x["kind"], x["name"])):
            chk = "pass" if not r["errors"] else f"{r['errors']} error(s)"
            L.append(f"| {r['kind']} | [{r['name']}]({Path(r['path']).with_suffix('.md')}) | `{r['target']}` | "
                     f"{r['status']} | {chk} | {r['updated']} |")
        root().mkdir(parents=True, exist_ok=True)
        (root() / "STATUS.md").write_text("\n".join(L) + "\n")
    return rows


def new(kind: str, name: str, target: str, from_kb: list[str] | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    ent: dict = {"name": name, "description": "", "tags": ["AgentAccess.Readable"] if kind != "glossaryTerm" else []}
    if kind == "table":
        ent |= {"extension": {"querySource": "", "grainKey": "", "grain": ""}, "columns": []}
    elif kind == "metric":
        ent |= {"displayName": "", "metricType": "", "unitOfMeasurement": "", "metricExpression": {"code": "", "language": "SQL"},
                "extension": {}}
    else:
        ent |= {"glossary": "Shop", "displayName": name, "synonyms": []}
    d = {"kind": kind, "status": "draft", "target_fqn": target, "proposed": [], "evidence": [], "notes": "", "entity": ent}
    if from_kb:
        import store
        c = store.connect()
        for eid in from_kb:
            row = store.get(c, eid)
            if not row:
                raise ValueError(f"no KB row {eid}")
            d["evidence"].append(f"clu:{eid}")
            if kind == "table" and row["type"] == "source":
                ent["description"] = ent["description"] or (row.get("notes") or "")
                ent["extension"]["grain"] = ent["extension"]["grain"] or (row.get("grain") or "")
                for col in c.execute("SELECT name, type, meaning, pitfalls FROM columns WHERE source_id = ? ORDER BY name", (eid,)):
                    text = " ".join(x for x in (col["meaning"], f"Pitfall: {col['pitfalls']}" if col["pitfalls"] else "") if x)
                    ent["columns"].append({"name": col["name"], "dataType": (col["type"] or "").upper(),
                                           "description": text, "semantics": {}, "tags": []})
            elif kind == "metric" and row["type"] == "metric":
                ent["displayName"] = ent["displayName"] or row["name"]
                ent["description"] = ent["description"] or (row.get("definition") or "")
                ent["metricExpression"]["code"] = ent["metricExpression"]["code"] or (row.get("formula_sql") or "")
            elif kind == "glossaryTerm" and row["type"] == "glossary":
                ent["description"] = ent["description"] or (row.get("meaning") or "")
                ent["synonyms"] += [x for x in (row.get("term_fa"), row.get("term_en")) if x and x != name]
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="om_drafts.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("conventions")
    n = sub.add_parser("new")
    n.add_argument("kind", choices=sorted(KINDS))
    n.add_argument("name")
    n.add_argument("--target", required=True)
    n.add_argument("--from-kb", default="", help="comma-separated clu KB ids to prefill from")
    for cmd in ("validate", "render"):
        x = sub.add_parser(cmd)
        x.add_argument("files", nargs="*")
    sub.add_parser("status")
    pl = sub.add_parser("payload")
    pl.add_argument("file")
    a = p.parse_args(argv)
    try:
        if a.cmd == "conventions":
            conv = conventions()
            print(f"wrote {root() / 'CONVENTIONS.md'} ({conv['counts']})")
        elif a.cmd == "new":
            d = new(a.kind, a.name, a.target, [x for x in a.from_kb.split(",") if x])
            f = path_for(d)
            if f.exists():
                raise ValueError(f"{f} exists; edit it instead")
            print(save(d))
        elif a.cmd in ("validate", "render"):
            items = [(Path(f), json.loads(Path(f).read_text())) for f in a.files] or drafts()
            bad = 0
            for f, d in items:
                errs, warns = validate(d)
                if a.cmd == "render":
                    f.with_suffix(".md").write_text(render(d, errs, warns))
                bad += bool(errs)
                print(f"{'FAIL' if errs else 'ok  '} {f.name}" + "".join(f"\n  - {x}" for x in errs)
                      + "".join(f"\n  ~ {x}" for x in warns))
            if a.cmd == "render":
                status()
            return 1 if bad and a.cmd == "validate" else 0
        elif a.cmd == "status":
            rows = status()
            print(json.dumps(collections.Counter(r["status"] for r in rows)))
        elif a.cmd == "payload":
            print(json.dumps(payload(json.loads(Path(a.file).read_text())), ensure_ascii=False, indent=1))
    except (ValueError, FileNotFoundError) as e:
        print(f"om_drafts.py {a.cmd}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
