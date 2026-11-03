#!/usr/bin/env python3
"""Rinzler knowledge store CLI.
  kb.py search "discount percent rounding" [--type bug] [--k 8] [--json]
  kb.py add bug --json '{"title": "...", "feature": "...", "severity": "S3", "status": "draft"}'
  kb.py add-many --file items.json        # [{"type": "lesson", "row": {...}, "tags": [...]}]
  kb.py list flaky [--status quarantined]
  kb.py show <id> | stats | export | rebuild | init
Types: asset (test assets), bug (past bugs, for duplicate checks), flaky (flaky tests and their quarantine), lesson
(corrections from the owner), baseline (perf and eval baselines), run (one row per closed run). See docs/kb-schema.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add")
    a.add_argument("etype", choices=sorted(store.TYPES))
    a.add_argument("--json", required=True)
    a.add_argument("--tags", default="")
    a.add_argument("--origin", default="rinzler")
    am = sub.add_parser("add-many")
    am.add_argument("--file", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type", choices=sorted(store.TYPES))
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("etype", choices=sorted(store.TYPES))
    ls.add_argument("--status")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    for c in ("export", "rebuild", "stats"):
        sub.add_parser(c)
    args = p.parse_args(argv)

    if args.cmd == "rebuild":
        print(json.dumps(store.stats(store.rebuild())))
        return 0
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
        store.export(c, args.etype)
        print(eid)
    elif args.cmd == "add-many":
        ids, bad, types = [], 0, set()
        for it in json.loads(Path(args.file).read_text(encoding="utf-8")):
            try:
                ids.append(store.upsert(c, it["type"], it["row"], origin=it.get("origin", "rinzler"), tags=it.get("tags")))
                types.add(it["type"])
            except (ValueError, KeyError) as e:
                print(f"kb.py add-many: skipped: {e}", file=sys.stderr)
                bad += 1
        for t in sorted(types):
            store.export(c, t)
        print(json.dumps(ids))
        return 1 if bad else 0
    elif args.cmd == "search":
        res = store.search(c, args.query, args.type, args.k)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for r in res:
                print(f"{r['id']}  [{r['type']}] {r['title']} — {r['snippet']}")
    elif args.cmd == "list":
        where = {"status": args.status} if args.status and args.etype in ("bug", "flaky") else {}
        res = store.rows(c, args.etype, **where)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            key = store.TYPES[args.etype]["key"]
            for r in res:
                print(f"{r['id']}  " + " | ".join(str(r.get(k) or "") for k in key) +
                      (f"  [{r['status']}]" if r.get("status") else ""))
    elif args.cmd == "show":
        print(json.dumps(store.get(c, args.id), ensure_ascii=False, indent=2))
    elif args.cmd == "export":
        print("\n".join(store.export(c)))
    elif args.cmd == "stats":
        print(json.dumps(store.stats(c), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
