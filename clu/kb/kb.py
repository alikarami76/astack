#!/usr/bin/env python3
"""clu knowledge base CLI. Examples:
  kb.py search "flash sale share gmv"
  kb.py search "seller commission per order" --lane schema     (tables)   --lane runs  (past run passages)
  kb.py embed --lane all                                        (curated, schema and runs vectors, incremental)
  kb.py add fact --json '{"statement": "...", "source_id": "src_...", "confidence": "observed", "evidence": "..."}'
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402
import writethrough  # noqa: E402


def _refresh_vectors(c) -> None:
    """Keep the optional vector index current after bulk writes. Never fails the write itself."""
    if not store.vectors_ready():
        return
    try:
        import vectors
        vectors.index(c)
    except Exception as e:  # noqa: BLE001
        print(f"kb.py: vector index not refreshed ({type(e).__name__}); run `kb.py embed` later", file=sys.stderr)


LANES = ("curated", "schema", "runs", "om")


def spawn_embed(lane: str = "all") -> str:
    """Start `kb.py embed --lane <lane>` detached, logging to kb/embed.log. Returns a one-line status and never
    raises: vectors are a convenience, never a reason for a write, a close or a scan to fail.
    CLU_BACKGROUND_EMBED=0 turns it off (the tests do)."""
    if os.environ.get("CLU_BACKGROUND_EMBED") == "0":
        return "vectors: background embed off (CLU_BACKGROUND_EMBED=0)"
    try:
        log = store.db_path().with_name("embed.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(f"--- {_dt.datetime.now().isoformat(timespec='seconds')} embed --lane {lane}\n")
            f.flush()
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "embed", "--lane", lane, "--quiet"],
                             stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True)
        return f"vectors: {lane} lane(s) updating in the background (log {log})"
    except Exception as e:  # noqa: BLE001
        return f"vectors: background embed not started ({type(e).__name__}: {e})"[:200]


def _lane_sections(c, query: str) -> None:
    """The two recall sections after the curated ones. Silent when a lane cannot answer at all."""
    try:
        import vec_lanes
        import vectors
    except Exception:  # noqa: BLE001 - numpy or vecstore missing: the curated sections already answered
        return
    try:
        rows = vectors.search_lane(c, "schema", query, k=8)
        if rows:
            print("## Tables that may hold this")
            for r in rows:
                why = vec_lanes.why_table(c, r["id"], query)
                print(f"{r['id']}  [{r['type']}] {r['title']}" + (f" — {why}" if why else ""))
    except Exception as e:  # noqa: BLE001
        print(f"kb.py recall: table lane skipped ({type(e).__name__})", file=sys.stderr)
    try:
        rows = vectors.search_lane(c, "runs", query, k=4)
        if rows:
            print("## Past run passages")
            for r in rows:
                head = (r.get("heading") or "").rsplit(" › ", 1)[-1][:70]
                snip = " ".join((r.get("snippet") or "").split())[:110]
                print(f"runs/{r['path']}" + (f" › {head}" if head else "") + f" — {snip}")
    except Exception as e:  # noqa: BLE001
        print(f"kb.py recall: run lane skipped ({type(e).__name__})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add")
    a.add_argument("etype", choices=sorted(store.TYPES))
    a.add_argument("--json", required=True)
    a.add_argument("--origin", default="clu")
    a.add_argument("--tags", default="")
    a.add_argument("--no-mirror", action="store_true")
    am = sub.add_parser("add-many")
    am.add_argument("--file", required=True, help='JSON list of {"type": ..., "row": {...}, "tags": [...]}')
    am.add_argument("--origin", default="clu")
    am.add_argument("--no-mirror", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type")
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--brief", action="store_true", help="ids and titles only")
    s.add_argument("--json", action="store_true")
    s.add_argument("--mode", choices=["auto", "lexical", "hybrid"], default="auto",
                   help="auto = hybrid when the vector index exists, else lexical")
    s.add_argument("--lane", choices=LANES, default="curated",
                   help="curated = KB rows (default); schema = one entry per table; runs = past run passages; "
                        "om = OpenMetadata metrics and glossary terms")
    s.add_argument("--rerank", choices=["auto", "on", "off"], default="auto",
                   help="cross-encoder rerank of the fused top 30: auto = only when the eval baseline shows it at "
                        "least as good as hybrid (curated lane); falls back to the fused order if unavailable")
    dp = sub.add_parser("dupes", help="cluster near-duplicate rows by cosine similarity (reports only, never merges)")
    dp.add_argument("--lane", choices=["curated", "schema", "om"], default="curated")
    dp.add_argument("--threshold", type=float, default=0.92)
    dp.add_argument("--type", help="only rows of this type")
    dp.add_argument("--json", action="store_true")
    sub.add_parser("reindex", help="rewrite the keyword index of every row (after a change of normalisation)")
    rc = sub.add_parser("recall", help="the standard recall bundle in one call: all types, metrics, lessons, runs")
    rc.add_argument("query")
    em = sub.add_parser("embed", help="embed new or changed rows into the local vector index")
    em.add_argument("--quiet", action="store_true")
    em.add_argument("--lane", choices=[*LANES, "all"], default="curated")
    em.add_argument("--background", action="store_true", help="start the embed detached and return at once")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    v = sub.add_parser("verify")
    v.add_argument("id")
    v.add_argument("--evidence", default="")
    su = sub.add_parser("supersede")
    su.add_argument("id")
    su.add_argument("--by", required=True)
    ln = sub.add_parser("link")
    ln.add_argument("from_id")
    ln.add_argument("to_id")
    sub.add_parser("export")
    sub.add_parser("rebuild")
    sub.add_parser("stats")
    args = p.parse_args(argv)
    try:
        return _run(args)
    except (KeyError, ValueError, FileNotFoundError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print(f"kb.py {args.cmd}: {msg}", file=sys.stderr)
        return 1


STANDING = ("step:contract", "step:report", "all")


def _is_om(c, r: dict) -> bool:
    tbl = store.TYPES.get(r["type"], {}).get("table")
    row = c.execute(f"SELECT origin, tags FROM {tbl} WHERE id = ?", (r["id"],)).fetchone() if tbl else None
    return bool(row) and (str(row["origin"] or "").startswith("om:") or '"om"' in (row["tags"] or ""))


def _om_section(seen: set[str]) -> None:
    """Snapshot age and the OM-vs-clu conflicts touching the printed rows (layer rule: clu-core step 1)."""
    try:
        sys.path.insert(0, str(store.REPO / "scripts"))
        import om
        st = om.state()
    except Exception:  # noqa: BLE001
        return
    if not st:
        return
    age = om.age_days()
    hits = [x for x in st.get("conflicts") or [] if x.get("om_id") in seen or x.get("clu_id") in seen]
    stale = age is not None and age > om.STALE_DAYS
    if not (stale or hits):
        return
    print("## OpenMetadata")
    if stale:
        print(f"snapshot is {age} days old: refresh with `python3 \"$CLU/scripts/om.py\" pull && "
              f"python3 \"$CLU/kb/kb.py\" import-legacy --only om`")
    for x in hits:
        print(f"conflict: {x['name']} — clu {x['clu_id']} vs OM {x['om_id']} ({x['om_name']}); OM wins on its "
              f"warehouse tables, clu's verified row on operational tables")


def _dupes(c, args) -> int:
    """Clusters of near-identical rows in one lane's vector file. Reports only: merging is a human decision."""
    import vec_lanes
    from vecstore import dedupe
    from vecstore.vecfile import VecFile
    a = vec_lanes.make(c, args.lane)
    vf = VecFile(a.vec_path)
    ids, mat = vf.matrix(args.type)
    if mat is None or not len(ids):
        print(f"kb.py dupes: no vectors for the {args.lane} lane (run `kb.py embed --lane {args.lane}`)",
              file=sys.stderr)
        return 1
    keep = [i for i, e in enumerate(ids) if a.keep(e)]  # superseded facts are already retired
    cl = dedupe.clusters([ids[i] for i in keep], mat[keep], args.threshold)
    rows = []
    for x in cl:
        members = []
        for eid in x["ids"]:
            h = a.hydrate(eid) or {}
            members.append({"id": eid, "type": h.get("type"), "title": " ".join(str(h.get("title") or "").split())[:100]})
        rows.append({**x, "members": members})
    if args.json:
        print(json.dumps({"lane": args.lane, "threshold": args.threshold, "rows": len(keep), "clusters": rows},
                         ensure_ascii=False))
        return 0
    print(f"{len(rows)} cluster(s) of near-duplicates at cosine >= {args.threshold} among {len(keep)} {args.lane} rows"
          " (nothing is merged; review each and supersede or edit by hand)")
    for n, x in enumerate(rows, 1):
        flag = " (chain: some pairs are less similar than the threshold)" if x["chain"] else ""
        print(f"\n#{n}  {len(x['ids'])} rows, similarity {x['min_sim']}-{x['max_sim']}{flag}")
        for m in x["members"]:
            print(f"  {m['id']}  [{m['type']}] {m['title']}")
    return 0


def _need(c, eid: str) -> dict:
    row = store.get(c, eid)
    if not row:
        raise KeyError(f"no row with id {eid}; find ids with `kb.py search \"<text>\" --brief`")
    return row


def _run(args) -> int:

    if args.cmd == "rebuild":
        c = store.rebuild()
        _refresh_vectors(c)
        print(json.dumps(store.stats(c)))
        return 0
    c = store.connect()
    if args.cmd == "init":
        print(store.db_path())
    elif args.cmd == "add":
        row = json.loads(args.json)
        tags = [t for t in args.tags.split(",") if t]
        try:
            eid = store.upsert(c, args.etype, row, origin=args.origin, tags=tags)
        except ValueError as e:
            print(f"kb.py add: {e}", file=sys.stderr)
            return 1
        if not args.no_mirror:
            writethrough.mirror(c, args.etype, eid)
        store.export(c, etype=args.etype)
        print(eid)
    elif args.cmd == "add-many":
        items = json.loads(Path(args.file).read_text())
        if not isinstance(items, list):
            raise ValueError('the file must hold a JSON list of {"type": ..., "row": {...}, "tags": [...]}')
        out, types, skipped = [], set(), 0
        for i, it in enumerate(items):
            try:
                eid = store.upsert(c, it["type"], it["row"], origin=it.get("origin") or args.origin, tags=it.get("tags") or [])
            except (ValueError, KeyError, TypeError) as e:
                skipped += 1
                hint = "" if isinstance(e, ValueError) else ' (each item needs "type" and "row")'
                print(f"kb.py add-many: skipped item {i}: {e}{hint}", file=sys.stderr)
                continue
            if not args.no_mirror:
                writethrough.mirror(c, it["type"], eid)
            types.add(it["type"])
            out.append(eid)
        for t_ in types:
            store.export(c, etype=t_)
        _refresh_vectors(c)
        print(json.dumps(out))
        if skipped:
            print(f"kb.py add-many: {skipped} of {len(items)} items skipped; fix them and re-run add-many with only those",
                  file=sys.stderr)
            return 1
    elif args.cmd == "search" and args.lane != "curated":
        import vectors
        res = vectors.search_lane(c, args.lane, args.query, k=args.k, mode=args.mode,
                                  rerank=vectors.want_rerank(c, args.lane, args.rerank)
                                  if args.mode != "lexical" else False)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        for r in ([] if args.json else res):
            if args.lane == "runs":
                print(f"runs/{r['path']}  {r.get('heading') or ''}" + ("" if args.brief else f" — {r.get('snippet')}"))
            else:
                print(f"{r['id']}  [{r['type']}] {r['title']}" + ("" if args.brief else f" — {r.get('snippet')}"))
    elif args.cmd == "search":
        mode = args.mode
        if mode == "auto":
            mode = "hybrid" if store.vectors_ready() else "lexical"
        rerank = False
        if mode == "hybrid":
            import vectors
            rerank = vectors.want_rerank(c, "curated", args.rerank)
        res = store.search(c, args.query, etype=args.type, k=args.k, mode=mode, rerank=rerank)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        elif args.brief:
            for r in res:
                print(f"{r['id']}  [{r['type']}] {r['title']}")
        else:
            for r in res:
                print(f"{r['id']}  [{r['type']}] {r['title']} — {r['snippet']}")
    elif args.cmd == "recall":
        mode = "hybrid" if store.vectors_ready() else "lexical"
        seen: set[str] = set()
        for label, et, k in (("top", None, 8), ("metrics", "metric", 5), ("lessons", "lesson", 5), ("runs", "run", 3),
                             ("experiments", "experiment", 3)):
            rows = [r for r in store.search(c, args.query, etype=et, k=k + len(seen), mode=mode) if r["id"] not in seen][:k]
            seen |= {r["id"] for r in rows}
            print(f"## {label}")
            for r in rows:
                mark = " [OM]" if _is_om(c, r) else ""
                print(f"{r['id']}  [{r['type']}]{mark} {r['title'][:110]}")
        try:
            import vectors
            om_rows = vectors.search_lane(c, "om", args.query, k=5)
        except Exception:  # noqa: BLE001 - numpy or vecstore missing: keywords answer
            om_rows = store._lexical(c, args.query, None, 5, om="only")
        if om_rows:
            print("## OpenMetadata definitions (certified catalog; the authority for the warehouse tables it curates)")
            for r in om_rows:
                print(f"{r['id']}  [{r['type']}] [OM] {r['title'][:110]}")
            seen |= {r["id"] for r in om_rows}
        _om_section(seen)
        # Lessons that apply to every contract and report, whatever the query (follow them).
        qs = ",".join("?" * len(STANDING))
        standing = [r for r in c.execute(f"SELECT id, rule, applies_to FROM lessons WHERE applies_to IN ({qs}) ORDER BY id",
                                         STANDING) if r["id"] not in seen]
        print("## standing lessons (contract, report, all)")
        for r in standing:
            print(f"{r['id']}  [{r['applies_to']}] {(r['rule'] or '')[:110]}")
        _lane_sections(c, args.query)
    elif args.cmd == "embed":
        if args.background:
            print(spawn_embed(args.lane))
            return 0
        import vectors
        if args.lane == "curated":  # unchanged output for the default lane
            print(json.dumps(vectors.index(c, progress=not args.quiet)))
            return 0
        res = vectors.index_lanes(c, LANES if args.lane == "all" else [args.lane], progress=not args.quiet)
        print(json.dumps(res))
        return 1 if any(isinstance(v, dict) and "error" in v for v in res.values()) else 0
    elif args.cmd == "reindex":
        print(json.dumps({"reindexed": store.reindex(c)}))
    elif args.cmd == "dupes":
        return _dupes(c, args)
    elif args.cmd == "show":
        print(json.dumps(_need(c, args.id), ensure_ascii=False, indent=2))
    elif args.cmd == "verify":
        row = _need(c, args.id)
        store.verify(c, args.id, args.evidence)
        store.export(c, etype=row["type"])
    elif args.cmd == "supersede":
        old, _ = _need(c, args.id), _need(c, args.by)
        if old["type"] != "fact":
            raise ValueError(f"{args.id} is a {old['type']}; only facts are superseded. To correct a {old['type']}, "
                             "re-add it with the same key fields (add or add-many upserts and replaces the text)")
        store.supersede(c, args.id, args.by)
        store.export(c, etype="fact")
    elif args.cmd == "link":
        _need(c, args.from_id), _need(c, args.to_id)
        store.link(c, args.from_id, args.to_id)
        store.export(c, etype="fact")
    elif args.cmd == "export":
        print("\n".join(store.export(c)))
    elif args.cmd == "stats":
        print(json.dumps(store.stats(c), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
