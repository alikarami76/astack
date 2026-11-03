#!/usr/bin/env python3
"""Does this already exist? Examples:
  lookup.py calculateOrderTotal
  lookup.py "قیمت بای باکس" --repo pricing --repo api-app
  lookup.py coupon --kind route --json
  lookup.py --endpoints
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import qpaths  # noqa: E402
import store  # noqa: E402
from index_repo import refresh  # noqa: E402
from qindex.lookup import endpoint_pairs, lookup  # noqa: E402

BUILD_HINT = "build indexes: for r in <repos>; do python3 scripts/index_repo.py $r --fetch; done"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lookup.py")
    ap.add_argument("query", nargs="?")
    ap.add_argument("--repo", action="append")
    ap.add_argument("--kind")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--no-grep", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--vectors", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--endpoints", action="store_true", help="list frontend API calls matched to backend routes")
    a = ap.parse_args(argv)
    all_repos = qpaths.load_repos()
    for n in a.repo or []:
        if n not in all_repos:
            print(f"unknown repo {n}; known: {', '.join(sorted(all_repos))}", file=sys.stderr)
            return 2
    names = a.repo or list(all_repos)  # every configured repo: a missing index is built by refresh, or reported
    if not a.no_refresh and (a.query or a.endpoints):
        for n in names:
            try:
                refresh(n)  # incremental, or a full build when the index is missing; no network
            except Exception as e:  # noqa: BLE001 - keep going with the stale (or missing) index
                print(f"warning: refresh of {n} failed ({type(e).__name__}: {e}); using the existing index", file=sys.stderr)
    have = {n for n in names if qpaths.index_path(n).exists()}
    missing = [n for n in names if n not in have]
    if a.endpoints:
        fronts = [n for n in names if all_repos[n]["lang"] == "ts" and n in have]
        backs = {n: qpaths.index_path(n) for n in names if all_repos[n]["lang"] in ("php", "java") and n in have}
        pairs = []
        for fr in fronts:
            pairs += endpoint_pairs(qpaths.index_path(fr), backs)
        if missing:
            print(f"warning: skipped repos without an index: {', '.join(missing)}. {BUILD_HINT}", file=sys.stderr)
        out = {"pairs": pairs, "skipped": missing} if a.json else pairs
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if not a.query:
        ap.error("query is required unless --endpoints")
    repos = {n: {"path": all_repos[n]["path"], "ref": all_repos[n]["ref"], "db": qpaths.index_path(n)} for n in names}
    res = lookup(a.query, repos, store.connect(), kind=a.kind, k=a.k, use_grep=not a.no_grep, use_vectors=a.vectors)
    res["warnings"] = []
    if res.get("missing"):
        res["warnings"].append(f"no index built for {', '.join(res['missing'])}; results do not cover them. {BUILD_HINT}")
    if res["stale"]:
        res["warnings"].append(f"index behind the latest fetched commit for {', '.join(res['stale'])}")
    if res.get("grep_failed"):
        res["warnings"].append(f"git grep failed for {', '.join(res['grep_failed'])}; grep hits do not cover them")
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    for w in res["warnings"]:
        print(f"warning: {w}")
    if len(res["terms"]) > 1:
        print(f"searched: {' | '.join(res['terms'])}")
    for h in res["hits"]:
        print(f"{h['repo']}:{h['file']}:{h['line']}  [{h['kind'] or '?'}] {h['name']}  {h['signature'][:90]}  ({'+'.join(h['sources'])})")
    if not res["hits"]:
        print("no matches in the index, the concept map or git grep"
              + (f" (not searched: {', '.join(res['missing'])})" if res.get("missing") else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.stdout.flush()
    except BrokenPipeError:
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        rc = 0
    raise SystemExit(rc)
