#!/usr/bin/env python3
"""Research helpers: verification sample, refresh status, rule diff from snapshots.
  research_tools.py sample --repo pricing --run-id 2026-09-30-research-pricing
  research_tools.py status
  research_tools.py snapshot --repo pricing --out snap.json
  research_tools.py diff --repo pricing --before snap.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import gitsrc  # noqa: E402
import qpaths  # noqa: E402
import store  # noqa: E402

WEAK = ("transitional", "stated_only")


def pick_sample(rows: list[dict], run_id: str, frac: float = 0.30) -> list[str]:
    weak = [r["id"] for r in rows if r["tier"] in WEAK]
    rest = sorted(r["id"] for r in rows if r["tier"] not in WEAK)
    k = math.ceil(frac * len(rest))
    return sorted(weak + random.Random(run_id).sample(rest, k))


def _live(conn, repo: str, run_id: str | None = None) -> list[dict]:
    q = "SELECT * FROM conventions WHERE repo = ? AND (superseded_by IS NULL OR superseded_by = '')"
    args: list = [repo]
    if run_id:
        q += " AND run_id = ?"
        args.append(run_id)
    return [dict(r) for r in conn.execute(q, args)]


def snapshot(conn, repo: str) -> list[dict]:
    rows = _live(conn, repo)
    # "scope" appears only for module-scoped rules, so whole-repo snapshots keep their old shape
    return [{"id": r["id"], **({"scope": r["scope"]} if r.get("scope") else {}), "area": r["area"], "rule": r["rule"], "tier": r["tier"]}
            for r in rows]


def status(conn, repos: dict, threshold: int = 200) -> list[dict]:
    out = []
    for name, cfg in repos.items():
        rows = _live(conn, name)
        refuted = sum("refuted-in-use" in json.loads(r.get("tags") or "[]") for r in rows)
        repo_path = Path(cfg["path"])

        # Find the oldest checked_at_sha by picking the one with largest count_commits
        shas = [r["checked_at_sha"] for r in rows if r.get("checked_at_sha")]
        research = None
        unknown_sha = False

        try:
            current = gitsrc.resolve(repo_path, cfg["ref"])
        except gitsrc.GitError:
            current = None

        if not shas:
            # never_researched: no live rows with a sha
            unknown_sha = True
        else:
            # Try each distinct sha and pick the one with largest count_commits
            best_sha = None
            best_count = -1
            for sha in set(shas):
                try:
                    count = gitsrc.count_commits(repo_path, sha, current) if current else 0
                    if count > best_count:
                        best_count = count
                        best_sha = sha
                except gitsrc.GitError:
                    # sha is not in repo; skip it
                    continue
            research = best_sha if best_sha else None
            if not research:
                unknown_sha = True

        # Compute other metrics
        since = None
        diverged = False
        if current and research:
            try:
                since = gitsrc.count_commits(repo_path, research, current)
                # Check if research is an ancestor of current
                # If count_commits(current, research) > 0, research is NOT an ancestor
                try:
                    backward_count = gitsrc.count_commits(repo_path, current, research)
                    if backward_count > 0:
                        diverged = True
                except gitsrc.GitError:
                    # current not an ancestor of research; they're diverged
                    diverged = True
            except gitsrc.GitError:
                unknown_sha = True
                since = None

        suggest_rerun = bool((since or 0) > threshold or refuted or diverged or unknown_sha)

        out.append({"repo": name, "research_sha": research, "current_sha": current, "commits_since": since,
                    "refuted_in_use": refuted, "diverged": diverged, "unknown_sha": unknown_sha,
                    "never_researched": not shas, "suggest_rerun": suggest_rerun})
    return out


def diff_rules(old_rows: list[dict], new_rows: list[dict]) -> dict:
    key = lambda r: (r.get("scope") or "", r["area"], r["rule"])  # noqa: E731
    old, new = {key(r): r for r in old_rows}, {key(r): r for r in new_rows}
    pick = lambda r: {**({"scope": r["scope"]} if r.get("scope") else {}), "area": r["area"], "rule": r["rule"], "tier": r["tier"]}  # noqa: E731
    return {"new": [pick(new[k]) for k in sorted(new.keys() - old.keys())],
            "missing": [pick(old[k]) for k in sorted(old.keys() - new.keys())],
            "tier_changed": [{**({"scope": k[0]} if k[0] else {}), "area": k[1], "rule": k[2], "old": old[k]["tier"], "new": new[k]["tier"]}
                             for k in sorted(old.keys() & new.keys()) if old[k]["tier"] != new[k]["tier"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_tools.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--repo", required=True); s.add_argument("--run-id", required=True)
    sub.add_parser("status")
    sn = sub.add_parser("snapshot"); sn.add_argument("--repo", required=True); sn.add_argument("--out", required=True)
    d = sub.add_parser("diff"); d.add_argument("--repo", required=True); d.add_argument("--before", required=True)
    a = ap.parse_args(argv)
    conn = store.connect()
    if a.cmd == "sample":
        print(json.dumps(pick_sample(_live(conn, a.repo, a.run_id), a.run_id)))
    elif a.cmd == "status":
        repos = {n: {"path": c["path"], "ref": c["ref"]} for n, c in qpaths.load_repos().items()}
        print(json.dumps(status(conn, repos), indent=1))
    elif a.cmd == "snapshot":
        snap = snapshot(conn, a.repo)
        with open(a.out, "w") as f:
            json.dump(snap, f, indent=1)
    else:  # a.cmd == "diff"
        with open(a.before) as f:
            old = json.load(f)
        new = snapshot(conn, a.repo)
        print(json.dumps(diff_rules(old, new), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
