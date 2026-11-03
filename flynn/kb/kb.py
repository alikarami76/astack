#!/usr/bin/env python3
"""flynn knowledge store CLI. Examples:
  kb.py search "data table empty state" --type component
  kb.py add lesson --json '{"rule": "...", "why": "...", "applies_to": "compose"}'
  kb.py list --type component --system admin-panel
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


def _vec_adapter(c):
    """The vecstore adapter, or None in the deploy tree (no lib/vecstore), without numpy, or on Postgres."""
    if store._is_pg(c):
        return None
    try:
        import vec_adapter
        return vec_adapter.make(c)
    except ImportError:
        return None


def _search(c, args):
    ad = _vec_adapter(c) if args.mode != "lexical" else None
    if ad is None:
        return store.search(c, args.query, etype=args.type, k=args.k)
    from vecstore import hybrid
    return hybrid.search(ad, args.query, args.type, args.k, args.mode, rerank=args.rerank)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("sync-systems", help="make the shipped design systems in the store match kb/export (systems/systems.json)")
    a = sub.add_parser("add")
    a.add_argument("etype", choices=sorted(store.TYPES))
    a.add_argument("--json", required=True)
    a.add_argument("--origin", default="flynn")
    a.add_argument("--tags", default="")
    am = sub.add_parser("add-many")
    am.add_argument("--file", required=True, help='JSON list of {"type": ..., "row": {...}, "tags": [...]}')
    am.add_argument("--origin", default="flynn")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type", choices=sorted(store.TYPES))
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.add_argument("--mode", choices=["auto", "lexical", "hybrid"], default="auto",
                   help="auto = hybrid when the vector index exists and the eval baseline shows it at least as good")
    s.add_argument("--rerank", choices=["auto", "on", "off"], default="auto",
                   help="cross-encoder rerank of the fused top 30 (auto = when the eval baseline shows it helps)")
    sub.add_parser("embed", help="embed new or changed rows for hybrid search (needs agent-stack lib/vecstore)")
    du = sub.add_parser("dupes", help="clusters of near-duplicate rows from the vectors (reports only)")
    du.add_argument("--threshold", type=float, default=0.92)
    du.add_argument("--type", choices=sorted(store.TYPES))
    du.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--type", required=True, choices=sorted(store.TYPES))
    ls.add_argument("--system")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    ex = sub.add_parser("export")
    ex.add_argument("--force", action="store_true", help="overwrite even if rows exist only in the export files")
    sub.add_parser("rebuild")
    sub.add_parser("stats")
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
    if args.cmd == "sync-systems":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import systems_policy
        if not systems_policy.on_server():
            print("kb.py sync-systems: refused. It makes a server's store match kb/export and deletes rows the export "
                  "lacks; on an owner's own copy the store is the source of truth. (Set FLYNN_SERVER=1 on a server.)",
                  file=sys.stderr)
            return 2
        r = store.sync_systems(c, systems_policy.shipped())
        print(json.dumps({**r, "systems": systems_policy.shipped(), "skipped": r["skipped"][:5]}))
        return 0
    if args.cmd == "init":
        print("postgres schema kb" if store.pg_url() else store.db_path())
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
        res = _search(c, args)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for r in res:
                print(f"{r['id']}  [{r['type']}] {r['title']} — {r['snippet']}")
    elif args.cmd in ("embed", "dupes"):
        ad = _vec_adapter(c)
        if ad is None:
            print(f"kb.py {args.cmd}: needs agent-stack lib/vecstore, numpy and the SQLite store", file=sys.stderr)
            return 1
        from vecstore import client, dedupe
        from vecstore.vecfile import VecFile
        if args.cmd == "embed":
            print(json.dumps(VecFile(ad.vec_path).sync(ad.entries(), client.embed, progress=True)))
            return 0
        rep = dedupe.report(ad, args.threshold, args.type)
        if not rep["rows"]:
            print("kb.py dupes: no vectors yet (run `kb.py embed`)", file=sys.stderr)
            return 1
        print(json.dumps(rep, ensure_ascii=False) if args.json else dedupe.render(rep))
    elif args.cmd == "list":
        rows = store.list_rows(c, args.type, args.system)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            key = store.TYPES[args.type]["key"]
            for r in rows:
                tier = f" [{r['tier']}]" if r.get("tier") else ""
                print(f"{r['id']}  {' / '.join(str(r.get(k)) for k in key)}{tier}")
    elif args.cmd == "show":
        row = store.get(c, args.id)
        if row is None:
            print(f"kb.py show: no row with id {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(row, ensure_ascii=False, indent=2))
    elif args.cmd == "export":
        try:
            print("\n".join(store.export(c, force=args.force)))
        except store.ExportRefused as e:
            print(f"kb.py export refused: {e}", file=sys.stderr)
            return 1
    elif args.cmd == "stats":
        print(json.dumps(store.stats(c), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
