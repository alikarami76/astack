#!/usr/bin/env python3
"""Backfill the "aliases" and "questions" enrichment fields of curated KB rows, without calling any model.

  enrich.py worklist [--types metric,fact,...] [--limit N] [--out wl.jsonl]   rows that lack enrichment
  enrich.py apply --file filled.jsonl [--overwrite] [--dry-run]               write the filled lines back
  enrich.py stats                                                              how many rows have each field

An agent fills the worklist: for every line it sets "aliases" (other names a person might type for this row,
Farsi and English, one per ';') and "questions" (likely questions the row answers, one per '|'). `apply` then
validates each line, writes the two fields onto the row (nothing else changes), re-indexes the row for keyword
search, and re-exports the type. Run `kb.py embed` afterwards so the vectors pick the new text up.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

INSTRUCTIONS = (
    "Fill 'aliases' and 'questions' for each line, using only the row's own 'context' (do not invent facts). "
    "aliases: up to 8 other names a person might type for this row, in Farsi and English, separated by ';'. "
    "questions: 2 to 4 short questions this row answers, as an analyst would ask them, separated by '|'. "
    "Leave a field empty if you cannot do it honestly. Keep every other key unchanged. Return the same JSONL.")
MAX_ALIASES, MAX_QUESTIONS = 400, 700
ORDER = ("metric", "glossary", "lesson", "source", "fact")
CONF_RANK = {"verified": 0, "observed": 1, "assumed": 2}
CONTEXT_CHARS = 500


def _eligible(row: dict) -> bool:
    origin = str(row.get("origin") or "")
    return not (origin.startswith(store.NO_EXPORT_ORIGINS) or origin.startswith(store.OM_ORIGIN))


def _context(conn, etype: str, row: dict) -> dict:
    spec = store.TYPES[etype]
    ctx = {c: str(row[c])[:CONTEXT_CHARS] for c in spec["text"]
           if c not in ("aliases", "questions") and row.get(c) not in (None, "")}
    if etype == "source":
        cols = [r[0] for r in conn.execute("SELECT name FROM columns WHERE source_id = ? ORDER BY rowid LIMIT 12",
                                           (row["id"],)) if r[0]]
        if cols:
            ctx["columns"] = ", ".join(cols)
    return ctx


def worklist(conn, types=None, limit: int | None = None) -> list[dict]:
    """Curated rows (not schema-cache, not OpenMetadata, no superseded facts) missing aliases or questions."""
    types = list(types) if types else list(ORDER)
    for t in types:
        if t not in store.ENRICHED:
            raise ValueError(f"{t} cannot be enriched; expected one of {', '.join(store.ENRICHED)}")
    out = []
    for t in sorted(types, key=lambda x: ORDER.index(x)):
        spec = store.TYPES[t]
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']} ORDER BY id")]
        rows = [r for r in rows if _eligible(r) and not (t == "fact" and r.get("superseded_by"))
                and not (str(r.get("aliases") or "").strip() and str(r.get("questions") or "").strip())]
        rows.sort(key=lambda r: CONF_RANK.get(str(r.get("confidence") or ""), 3))
        for r in rows:
            out.append({"id": r["id"], "type": t, "title": store._title(t, r), "context": _context(conn, t, r),
                        "tags": json.loads(r.get("tags") or "[]"),
                        "aliases": str(r.get("aliases") or ""), "questions": str(r.get("questions") or "")})
    return out[:limit] if limit else out


def _join(v, sep: str) -> str:
    items = v if isinstance(v, list) else [x for x in str(v or "").replace("\n", sep.strip()).split(sep.strip())]
    return sep.join(dict.fromkeys(" ".join(str(x).split()) for x in items if str(x).strip()))


def apply(conn, lines: list[str], overwrite: bool = False, dry_run: bool = False) -> dict:
    res = {"applied": 0, "skipped": 0, "errors": [], "types": set()}
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            it = json.loads(line)
            if not isinstance(it, dict):
                raise ValueError("not an object")
        except ValueError as e:
            res["errors"].append(f"line {n}: not valid JSON ({e})")
            continue
        if "_instructions" in it:
            continue
        aliases, questions = _join(it.get("aliases"), "; "), _join(it.get("questions"), " | ")
        if len(aliases) > MAX_ALIASES or len(questions) > MAX_QUESTIONS:
            res["errors"].append(f"line {n}: aliases over {MAX_ALIASES} or questions over {MAX_QUESTIONS} characters")
            continue
        row = store.get(conn, str(it.get("id") or ""))
        if not row or row["type"] not in store.ENRICHED or (it.get("type") and it["type"] != row["type"]):
            res["skipped"] += 1
            continue
        new = {}
        for col, val in (("aliases", aliases), ("questions", questions)):
            if val and (overwrite or not str(row.get(col) or "").strip()):
                new[col] = val
        if not new:
            res["skipped"] += 1
            continue
        if not dry_run:
            store.upsert(conn, row["type"], {**row, **new})
        res["applied"] += 1
        res["types"].add(row["type"])
    if not dry_run:
        for t in res["types"]:
            store.export(conn, etype=t)
    res["types"] = sorted(res["types"])
    return res


def stats(conn) -> dict:
    out = {}
    for t in store.ENRICHED:
        tbl = store.TYPES[t]["table"]
        out[t] = {"rows": conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0],
                  "aliases": conn.execute(f"SELECT count(*) FROM {tbl} WHERE coalesce(aliases,'') != ''").fetchone()[0],
                  "questions": conn.execute(f"SELECT count(*) FROM {tbl} WHERE coalesce(questions,'') != ''").fetchone()[0]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="enrich.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worklist")
    w.add_argument("--types", default="")
    w.add_argument("--limit", type=int)
    w.add_argument("--out", help="write JSONL here (default: stdout)")
    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--overwrite", action="store_true", help="replace fields that already have text")
    a.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    args = ap.parse_args(argv)
    c = store.connect()
    try:
        if args.cmd == "worklist":
            rows = worklist(c, [t for t in args.types.split(",") if t] or None, args.limit)
            text = "".join(json.dumps(x, ensure_ascii=False) + "\n"
                           for x in [{"_instructions": INSTRUCTIONS}, *rows])
            if args.out:
                Path(args.out).write_text(text)
                by = {}
                for r in rows:
                    by[r["type"]] = by.get(r["type"], 0) + 1
                print(f"{len(rows)} rows to enrich -> {args.out} {json.dumps(by)}")
            else:
                sys.stdout.write(text)
            return 0
        if args.cmd == "apply":
            res = apply(c, Path(args.file).read_text().splitlines(), args.overwrite, args.dry_run)
            for e in res["errors"]:
                print(f"enrich.py: {e}", file=sys.stderr)
            print(f"{'would apply' if args.dry_run else 'applied'} {res['applied']}, skipped {res['skipped']}, "
                  f"errors {len(res['errors'])}" + ("" if args.dry_run or not res["applied"]
                                                   else "; run `kb.py embed` to refresh the vectors"))
            return 1 if res["errors"] else 0
        print(json.dumps(stats(c), indent=2))
        return 0
    except (ValueError, OSError) as e:
        print(f"enrich.py {args.cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
