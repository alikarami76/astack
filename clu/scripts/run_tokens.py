#!/usr/bin/env python3
"""Token use of one clu run, per agent, from telemetry + Claude Code transcripts.

  run_tokens.py <run_dir> [--write]   # --write stores the totals in run.json as "tokens"
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVENTS = REPO / "telemetry" / "events.jsonl"
FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _usage(path: str, start: str = "", end: str = "9999") -> dict:
    tot, seen = dict.fromkeys(FIELDS, 0), set()
    for line in open(path, errors="ignore"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = e.get("message") or {}
        u = m.get("usage")
        if e.get("type") != "assistant" or not u or m.get("id") in seen:
            continue
        ts = _utc(e.get("timestamp") or "")
        if ts and not (start <= ts <= end):
            continue
        seen.add(m.get("id"))
        for f in FIELDS:
            tot[f] += u.get(f) or 0
    return tot


def _utc(ts: str) -> str:
    """Normalise an ISO timestamp to naive UTC 'YYYY-MM-DDTHH:MM:SS' for comparisons."""
    import datetime as _dt
    if not ts:
        return ""
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:19]
    if d.tzinfo:
        d = d.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return d.isoformat(timespec="seconds")


def _find(agent_id: str, transcript_path: str) -> str | None:
    base = os.path.dirname(transcript_path or "")
    hits = glob.glob(os.path.join(base, "*", "subagents", f"agent-{agent_id}.jsonl"))
    return hits[0] if hits else None


def measure(run_dir: str, events: Path = EVENTS) -> dict:
    rj = json.loads((Path(run_dir) / "run.json").read_text())
    start, end = _utc(rj["created_at"]), _utc(rj.get("ended_at") or "") or "9999"
    subs, mains = {}, {}
    if Path(events).exists():
        for line in open(events):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            atype = str(e.get("agent_type") or "")
            if not atype.startswith("clu") or not (start <= _utc(e.get("ts") or "") <= end):
                continue
            if e.get("agent_id"):
                subs[e["agent_id"]] = (atype, e.get("transcript_path"))
            elif e.get("transcript_path"):
                mains[e["transcript_path"]] = atype
    per = collections.defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    files = [(_find(aid, tp), at) for aid, (at, tp) in subs.items()] + [(tp, at) for tp, at in mains.items()]
    for f, atype in files:
        if f and os.path.exists(f):
            for k, v in _usage(f, start, end).items():
                per[atype][k] += v
    total = {k: sum(p[k] for p in per.values()) for k in FIELDS}
    return {"schema_version": 1, "per_agent": dict(per), "total": total,
            "billable_estimate": total["input_tokens"] + total["output_tokens"] + total["cache_creation_input_tokens"],
            "note": "cache_read tokens bill at ~10% of input; lead-agent tokens after close are not included"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    res = measure(a.run_dir)
    if a.write:
        p = Path(a.run_dir) / "run.json"
        rj = json.loads(p.read_text())
        rj["tokens"] = res
        p.write_text(json.dumps(rj, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
