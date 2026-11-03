#!/usr/bin/env python3
"""quorra knowledge base CLI. Examples:
  kb.py search "table schema" --type convention
  kb.py add lesson --json '{"rule": "...", "why": "...", "applies_to": "api-app"}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402


def _export(c, etype: str) -> bool:
    try:
        store.export(c, etype=etype)
        return True
    except store.ExportRefused as e:
        print(f"kb.py: saved to the database, but export refused: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add")
    a.add_argument("etype", choices=sorted(store.TYPES))
    a.add_argument("--json", required=True)
    a.add_argument("--origin", default="quorra")
    a.add_argument("--tags", default="")
    am = sub.add_parser("add-many")
    am.add_argument("--file", required=True, help='JSON list of {"type": ..., "row": {...}, "tags": [...]}')
    am.add_argument("--origin", default="quorra")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type")
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.add_argument("--mode", choices=["auto", "lexical", "hybrid"], default="auto",
                   help="auto = hybrid when the vector index exists and the eval baseline shows it at least as good")
    s.add_argument("--rerank", choices=["auto", "on", "off"], default="auto",
                   help="cross-encoder rerank of the fused top 30 (auto = when the eval baseline shows it helps)")
    ls = sub.add_parser("list", help="list convention rows with their ids (use this to get rule ids)")
    ls.add_argument("--type", default="convention", choices=["convention"])
    ls.add_argument("--repo")
    ls.add_argument("--area")
    ls.add_argument("--scope", help='a module path prefix as stored (e.g. "src/Voucher/"); "" means unscoped rules')
    ls.add_argument("--live", action="store_true", help="only rules that are not superseded")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    ex = sub.add_parser("export")
    ex.add_argument("--force", action="store_true", help="overwrite even if rows exist only in the export files")
    sub.add_parser("rebuild")
    sub.add_parser("stats")
    sub.add_parser("embed")
    du = sub.add_parser("dupes", help="clusters of near-duplicate rows from the vectors (reports only)")
    du.add_argument("--threshold", type=float, default=0.92)
    du.add_argument("--type", choices=sorted(store.TYPES))
    du.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "rebuild":
        try:
            c = store.rebuild()
        except FileNotFoundError as e:
            print(f"kb.py rebuild: {e}", file=sys.stderr)
            return 1
        print(json.dumps(store.stats(c)))
        return 1 if store.LAST_REBUILD_SKIPPED else 0
    c = store.connect()
    if args.cmd == "init":
        print(store.db_path())
    elif args.cmd == "add":
        try:
            eid = store.upsert(c, args.etype, json.loads(args.json), origin=args.origin,
                               tags=[t for t in args.tags.split(",") if t])
        except ValueError as e:
            print(f"kb.py add: {e}", file=sys.stderr)
            return 1
        if not _export(c, args.etype):
            return 1
        print(eid)
    elif args.cmd == "add-many":
        out, types, skipped = [], set(), 0
        for it in json.loads(Path(args.file).read_text()):
            try:
                out.append(store.upsert(c, it["type"], it["row"], origin=it.get("origin") or args.origin, tags=it.get("tags") or []))
                types.add(it["type"])
            except (ValueError, KeyError) as e:
                print(f"kb.py add-many: skipped item: {e}", file=sys.stderr)
                skipped += 1
        exported = all([_export(c, t) for t in sorted(types)])
        print(json.dumps(out))
        if skipped or not exported:
            return 1
    elif args.cmd == "search":
        mode, rerank = args.mode, False
        if mode != "lexical":
            try:
                import vec_adapter
                from vecstore import hybrid
                ad = vec_adapter.make(c)
                mode = hybrid.resolve_mode(ad, mode)
                rerank = mode == "hybrid" and hybrid.resolve_rerank(ad, args.rerank)
            except ImportError:  # no numpy or no lib/vecstore: keywords answer
                mode = "lexical"
        res = store.search(c, args.query, etype=args.type, k=args.k, mode=mode, rerank=rerank)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for r in res:
                print(f"{r['id']}  [{r['type']}] {r['title']} — {r['snippet']}")
    elif args.cmd == "list":
        q, params = ("SELECT id, repo, scope, area, rule, tier, source, verified, superseded_by, checked_at_sha "
                     "FROM conventions WHERE 1=1"), []
        for col, val in (("repo", args.repo), ("area", args.area)):
            if val:
                q += f" AND {col} = ?"
                params.append(val)
        if args.scope is not None:
            q += " AND COALESCE(scope, '') = ?"
            params.append(args.scope)
        if args.live:
            q += " AND (superseded_by IS NULL OR superseded_by = '')"
        rows = [dict(r) for r in c.execute(q + " ORDER BY repo, area, id", params)]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            for r in rows:
                where = f"{r['repo']}[{r['scope']}]" if r.get("scope") else r["repo"]
                print(f"{r['id']}  {where}/{r['area']} [{r['tier']}] {r['rule']}")
    elif args.cmd == "show":
        print(json.dumps(store.get(c, args.id), ensure_ascii=False, indent=2))
    elif args.cmd == "export":
        try:
            print("\n".join(store.export(c, force=args.force)))
        except store.ExportRefused as e:
            print(f"kb.py export refused: {e}", file=sys.stderr)
            return 1
    elif args.cmd == "stats":
        print(json.dumps(store.stats(c), indent=2))
    elif args.cmd == "embed":
        import vectors
        print(json.dumps(vectors.index(c, progress=True)))
    elif args.cmd == "dupes":
        import vec_adapter
        from vecstore import dedupe
        rep = dedupe.report(vec_adapter.make(c), args.threshold, args.type)
        if not rep["rows"]:
            print("kb.py dupes: no vectors yet (run `kb.py embed`)", file=sys.stderr)
            return 1
        print(json.dumps(rep, ensure_ascii=False) if args.json else dedupe.render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
