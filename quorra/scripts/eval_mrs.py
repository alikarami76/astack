#!/usr/bin/env python3
"""Held-out MR convention check (spec §10.3). GitLab GET only.
  eval_mrs.py fetch pricing --after 2026-09-30T00:00:00Z --limit 10 --exclude-from runs/<run>/pricing/reviews.jsonl --out runs/<run>/pricing/heldout
  eval_mrs.py score runs/<run>/mr-labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
import urllib.parse
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_mr_reviews as fmr  # noqa: E402
import qpaths  # noqa: E402
from qindex.redact import scrub, scrub_obj  # noqa: E402

THRESHOLD = 0.15
BINDING = ("enforced", "dominant")
LABELS = ("follows", "breaks", "n/a")
TIERS = ("enforced", "dominant", "transitional", "stated_only")  # same as kb/store.py TIERS


def parse_ts(text: str) -> datetime:
    """ISO-8601 to an aware UTC datetime; a trailing Z is accepted and a naive value is taken as UTC."""
    dt = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _merged(m: dict):
    try:
        return parse_ts(m.get("merged_at") or "")
    except ValueError:
        return None


def fetch(project: str, ref: str, merged_after_iso: str, limit: int, exclude_iids: set, get_json=fmr.http_get_json) -> list[dict]:
    after = parse_ts(merged_after_iso)  # ValueError on a malformed cutoff
    pid = urllib.parse.quote(project, safe="")
    picked, page = [], 1
    while len(picked) < limit and page <= 10:
        batch = get_json(f"/projects/{pid}/merge_requests", {"state": "merged", "target_branch": ref, "order_by": "updated_at",
                                                              "sort": "desc", "per_page": 100, "page": page})
        if not batch:
            break
        picked += [m for m in batch if (_merged(m) or after) > after and m["iid"] not in exclude_iids]
        if len(batch) < 100:
            break
        page += 1
    picked = sorted({m["iid"]: m for m in picked}.values(), key=_merged, reverse=True)[:limit]
    out = []
    for m in picked:
        ch = get_json(f"/projects/{pid}/merge_requests/{m['iid']}/changes", {}) or {}
        changes = [{"new_path": c.get("new_path"), "old_path": c.get("old_path"), "diff": scrub(c.get("diff") or "")}
                   for c in ch.get("changes", [])]
        out.append({"iid": m["iid"], "title": scrub(m.get("title") or ""), "merged_at": m["merged_at"], "changes": changes,
                    "notes": scrub_obj(fmr.mr_notes(pid, m["iid"], get_json))})
    return out


def _wrong(row: dict) -> bool:
    if row.get("kind") == "uncovered":
        return True
    if row["label"] == "breaks":
        return row.get("tier") in BINDING and not row.get("reviewer_objected") and not row.get("exception")
    return row["label"] == "follows" and bool(row.get("reviewer_objected"))


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(labels: list) -> tuple[list, list]:
    """Split label rows into (valid, invalid). Every row needs a non-empty repo and an integer mr_iid. A rule row also
    needs a string rule_id, a tier from TIERS and a label in follows/breaks/n/a. Anything else is invalid."""
    good, bad = [], []
    for r in labels:
        ok = isinstance(r, dict) and isinstance(r.get("repo"), str) and bool(r["repo"]) and _int(r.get("mr_iid")) and (
            r.get("kind") == "uncovered" or (r.get("kind") == "rule" and r.get("label") in LABELS
                                             and isinstance(r.get("rule_id"), str) and bool(r["rule_id"])
                                             and r.get("tier") in TIERS))
        (good if ok else bad).append(r)
    return good, bad


def score(labels: list[dict]) -> dict:
    by: dict[str, list] = defaultdict(list)
    for r in validate(labels)[0]:
        if r["kind"] == "uncovered" or r["label"] != "n/a":
            by[r["repo"]].append(r)
    out = {}
    for repo, rows in sorted(by.items()):
        wrong = [r for r in rows if _wrong(r)]
        rate = len(wrong) / len(rows) if rows else 0.0
        # a second view next to the spec rate: wrong predictions over the rows that can be wrong in the costly way
        # (a rule broken, a convention no rule covers); None when there are none
        flagged = sum(r["kind"] == "uncovered" or r["label"] == "breaks" for r in rows)
        out[repo] = {"judgments": len(rows), "wrong": len(wrong), "rate": round(rate, 4), "fail": rate > THRESHOLD,
                     "wrong_over_breaks_uncovered": round(len(wrong) / flagged, 4) if flagged else None,
                     "wrong_items": wrong}
    return out


def read_jsonl(path: Path) -> tuple[list, list]:
    rows, errors = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            errors.append(f"{path}:{n}: malformed JSON")
    return rows, errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval_mrs.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("repo"); f.add_argument("--after", required=True)
    f.add_argument("--limit", type=int, default=10); f.add_argument("--exclude-from"); f.add_argument("--out", required=True)
    s = sub.add_parser("score"); s.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        try:
            parse_ts(a.after)
        except ValueError:
            ap.error(f"--after {a.after!r} is not an ISO-8601 timestamp")
        cfg = qpaths.repo_cfg(a.repo)
        exclude, skipped = set(), 0
        if a.exclude_from and Path(a.exclude_from).exists():
            rows, errs = read_jsonl(Path(a.exclude_from))
            skipped = len(errs)
            for r in rows:
                if isinstance(r, dict) and isinstance(r.get("mr_iid"), int):
                    exclude.add(r["mr_iid"])
                else:
                    skipped += 1
        if skipped:
            print(f"warning: {skipped} malformed row(s) in {a.exclude_from} skipped", file=sys.stderr)
        mrs = fetch(cfg["gitlab"], cfg["ref"], a.after, a.limit, exclude)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        for m in mrs:
            (out / f"{m['iid']}.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"fetched": [m["iid"] for m in mrs]}))
        return 0
    labels, errors = read_jsonl(Path(a.file))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    res = score(labels)
    invalid = validate(labels)[1]
    print(json.dumps({**res, **({"_invalid": invalid} if invalid else {})}, ensure_ascii=False, indent=1))
    if invalid:
        print(f"error: {len(invalid)} invalid label row(s)", file=sys.stderr)
        return 2
    if not any(v["judgments"] for v in res.values()):
        print(f"error: no judgments in {a.file}", file=sys.stderr)
        return 2
    return 1 if any(v["fail"] for v in res.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
