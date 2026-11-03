#!/usr/bin/env python3
"""Lookup evaluation against hand-checked goldens (spec §10.2: top-5 hit rate >= 0.90).
  eval_lookup.py run --min 0.9
  eval_lookup.py draft --out tests/eval/lookup_goldens.draft.jsonl [--force]
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

GOLDENS = HERE.parent / "tests" / "eval" / "lookup_goldens.jsonl"


def _matches(hit: dict, exp: dict) -> bool:
    # A grep-only hit (name "") never satisfies a symbol golden
    if hit.get("name") == "" and "symbol" in exp:
        return False
    return hit.get("repo") == exp["repo"] and hit.get("file") == exp["file"] and (
        "symbol" not in exp or hit.get("name") == exp["symbol"])


def _validate_goldens(goldens: list[dict], known_repos: set[str]) -> list[str]:
    """Validate goldens up front. Returns list of error strings; empty if valid."""
    errors = []
    seen_ids = set()
    for g in goldens:
        # Guard against non-dict lines
        if not isinstance(g, dict):
            errors.append(f"malformed golden (not a dict): {g}")
            continue
        
        gid = g.get("id")
        if gid in seen_ids:
            errors.append(f"golden {gid}: duplicate id")
        seen_ids.add(gid)
        
        # Check required fields
        for field in ["id", "query", "repos", "expect"]:
            if field not in g:
                errors.append(f"golden {gid}: missing required field '{field}'")
        
        # Check repos is non-empty list
        if not isinstance(g.get("repos"), list) or not g["repos"]:
            errors.append(f"golden {gid}: repos must be a non-empty list")
        
        # Check expect is non-empty list
        if not isinstance(g.get("expect"), list) or not g["expect"]:
            errors.append(f"golden {gid}: expect must be a non-empty list")
        
        # Validate each expected entry has repo and file
        for exp in g.get("expect", []):
            if "repo" not in exp or "file" not in exp:
                errors.append(f"golden {gid}: expect entries must have repo and file")
        
        # Check repos exist and collect all missing repos
        missing_repos = [repo for repo in g.get("repos", []) if repo not in known_repos]
        for repo in missing_repos:
            errors.append(f"golden {gid}: unknown repo {repo}; known: {sorted(known_repos)}")
    
    return errors


def score(goldens: list[dict], run_one) -> dict:
    items = []
    errors = []
    stale = []
    
    for g in goldens:
        result = run_one(g)
        
        # Normalize run_one's return into (hits, stale_list, error)
        if isinstance(result, dict) and "error" in result:
            error_msg = result["error"]
            # Prefix error with golden id
            items.append({"id": g["id"], "query": g["query"], "phrasing": g.get("phrasing"), 
                         "error": f"{g['id']}: {error_msg}"})
            errors.append(f"{g['id']}: {error_msg}")
            continue
        
        # Extract hits, stale from result
        if isinstance(result, dict) and "hits" in result:
            hits = result["hits"]
            stale_list = result.get("stale", [])
        else:
            hits = result if isinstance(result, list) else []
            stale_list = []
        
        # Collect stale repos
        stale.extend(stale_list)
        
        # Truncate to top 5 on every path
        hits = hits[:5]
        
        rank = next((i + 1 for i, h in enumerate(hits) if any(_matches(h, e) for e in g["expect"])), None)
        items.append({"id": g["id"], "query": g["query"], "phrasing": g.get("phrasing"), "hit": rank is not None,
                      "rank": rank, "top": [f"{h.get('repo')}:{h.get('file')}:{h.get('line')} {h.get('name')}" for h in hits]})
    
    # Only count hits in items without errors
    hit_count = sum(i["hit"] for i in items if "error" not in i)
    total_count = len(items) - len(errors)
    
    res = {
        "rate": (hit_count / total_count) if total_count > 0 else 0.0,
        "items": items
    }
    if errors:
        res["errors"] = errors
    if stale:
        res["stale"] = sorted(set(stale))
    
    return res


def draft(conn) -> list[dict]:
    out, n = [], 0
    for row in conn.execute("SELECT name_en, names, implementations FROM concepts"):
        name_en, names_raw, impls_json = row[0], row[1] or "{}", row[2] or "[]"
        try:
            names = json.loads(names_raw)
        except ValueError:
            names = names_raw  # legacy bare-text names
        impls = json.loads(impls_json)

        # A string-valued name is the English phrasing
        if isinstance(names, str):
            names = {"en": [names]}
        if not isinstance(names, dict):
            print(f"warning: concept '{name_en}' has non-dict names; skipped", file=sys.stderr)
            continue

        # Skip implementations missing repo/file
        impls = [i for i in impls if "repo" in i and "file" in i]
        if not impls:
            continue
        
        expect = [{"repo": i["repo"], "file": i["file"], **({"symbol": i["symbol"]} if i.get("symbol") else {})} for i in impls]
        repos = sorted({i["repo"] for i in impls})
        for phrasing in ("en", "fa", "codename"):
            for q in (names.get(phrasing, [])[:1] if isinstance(names.get(phrasing), list) else []):
                n += 1
                out.append({"id": f"d{n:03d}", "query": q, "phrasing": phrasing, "repos": repos, "expect": expect})
    return out


def _runner():
    from qindex.lookup import lookup
    all_repos, kb = qpaths.load_repos(), store.connect()
    known_repos = set(all_repos.keys())

    def run_one(g):
        # Check for unknown/missing repos and report all of them
        missing = [r for r in g.get("repos", []) if r not in known_repos]
        if missing:
            return {"error": f"no index for {', '.join(missing)}"}
        
        repos = {n: {"path": all_repos[n]["path"], "ref": all_repos[n]["ref"], "db": qpaths.index_path(n)} for n in g["repos"]}
        result = lookup(g["query"], repos, kb, k=10)
        
        # Check for missing (unindexed) repos and collect stale
        unindexed = result.get("missing", [])
        stale = result.get("stale", [])
        
        if unindexed:
            return {"error": f"no index for {', '.join(unindexed)}"}
        
        hits = result.get("hits", [])
        if stale:
            return {"hits": hits, "stale": stale}
        
        return hits
    
    return run_one


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval_lookup.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--goldens", default=str(GOLDENS)); r.add_argument("--min", type=float, default=0.9)
    r.add_argument("--out")
    d = sub.add_parser("draft"); d.add_argument("--out", required=True); d.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    
    if a.cmd == "draft":
        out_path = Path(a.out)
        if out_path.exists() and not a.force:
            print(f"error: {a.out} already exists; use --force to overwrite", file=sys.stderr)
            return 2
        rows = draft(store.connect())
        out_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
        print(f"{len(rows)} draft goldens -> {a.out}")
        return 0
    
    # Run command
    goldens_path = Path(a.goldens)
    if not goldens_path.exists():
        print(f"error: no goldens found at {a.goldens}", file=sys.stderr)
        return 2
    
    try:
        goldens = [json.loads(line) for line in goldens_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except json.JSONDecodeError as e:
        print(f"error: {a.goldens}:{e.lineno}: {e.msg}", file=sys.stderr)
        return 2
    
    if not goldens:
        print(f"error: no goldens found at {a.goldens}", file=sys.stderr)
        return 2
    
    # Validate goldens
    known_repos = set(qpaths.load_repos().keys())
    validation_errors = _validate_goldens(goldens, known_repos)
    if validation_errors:
        for err in validation_errors:
            print(f"error: {err}", file=sys.stderr)
        return 2
    
    res = score(goldens, _runner())
    text = json.dumps(res, ensure_ascii=False, indent=1)
    Path(a.out).write_text(text, encoding="utf-8") if a.out else print(text)
    
    # Report stale repos
    if res.get("stale"):
        print(f"warning: stale repos: {', '.join(res['stale'])}", file=sys.stderr)
    
    # Report errors (exit 2 if any errors)
    if res.get("errors"):
        print(f"error: {len(res['errors'])} goldens could not be evaluated", file=sys.stderr)
        return 2
    
    print(f"top-5 hit rate: {res['rate']:.2%} over {len(goldens)} goldens (min {a.min:.0%})", file=sys.stderr)
    return 0 if res["rate"] >= a.min else 1


if __name__ == "__main__":
    raise SystemExit(main())
