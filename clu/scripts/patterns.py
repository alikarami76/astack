#!/usr/bin/env python3
"""Cross-run pattern report for clu-curator."""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kb"))
import store  # noqa: E402


HELPERS = ("clu-challenger", "clu-data-runner", "clu-verifier", "clu-editor", "clu-curator", "clu-methodologist")


def _since(days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


def report(since_days: int = 30, events: Path | None = None, conn=None) -> str:
    c = conn or store.connect()
    cut = _since(since_days)
    out = [f"# clu patterns since {cut}", "", "## Lessons by area"]
    for area, n in c.execute("SELECT coalesce(applies_to,'?'), count(*) FROM lessons GROUP BY 1 ORDER BY 2 DESC"):
        out.append(f"- {area}: {n}")
    out += ["", "## Runs with more than one correction"]
    for r in c.execute("SELECT run_id, corrections_count, question FROM runs_index WHERE corrections_count > 1 AND date >= ? ORDER BY corrections_count DESC", (cut,)):
        out.append(f"- {r[0]} ({r[1]} corrections): {r[2][:100]}")
    out += ["", "## Verification not sustained"]
    for r in c.execute("SELECT run_id, verify_result FROM runs_index WHERE verify_result IS NOT NULL AND verify_result != 'sustained' AND date >= ?", (cut,)):
        out.append(f"- {r[0]}: {r[1]}")
    out += ["", "## Tool failures (telemetry)"]
    ev = Path(events) if events else REPO / "telemetry/events.jsonl"
    fails, spawns = collections.Counter(), collections.defaultdict(set)
    if ev.exists():
        for line in ev.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (e.get("ts") or "")[:10] < cut:
                continue
            if e.get("event") == "PostToolUseFailure":
                fails[(e.get("tool_name"), (e.get("error") or "")[:40])] += 1
            if e.get("agent_id") and str(e.get("agent_type") or "").startswith("clu-"):
                spawns[e["agent_type"]].add(e["agent_id"])
    for (tool, err), n in fails.most_common(10):
        out.append(f"- {tool} — {err}: {n}")
    out += ["", "## Helper use (distinct spawns; a helper never used is a candidate for removal)"]
    for name in HELPERS:
        n = len(spawns.get(name, ()))
        out.append(f"- {name}: {n}" + ("  <- never used in this window" if n == 0 else ""))
    out += ["", "## Sources written by more than one run"]
    by_table: dict[str, int] = {}
    for r in c.execute("SELECT table_name, count(DISTINCT origin) FROM sources WHERE origin LIKE 'run:%' AND updated_at >= ? GROUP BY table_name HAVING count(DISTINCT origin) > 1", (cut,)):
        by_table[r[0]] = max(by_table.get(r[0], 0), r[1])
    # A source's origin only records the FIRST run that touched it (a curated
    # origin sticks), so also count distinct fact.run_id per source table --
    # this fires even when the source's own origin never changed.
    for r in c.execute(
        "SELECT s.table_name, count(DISTINCT f.run_id) FROM facts f "
        "JOIN sources s ON f.source_id = s.id "
        "WHERE f.run_id IS NOT NULL AND f.updated_at >= ? "
        "GROUP BY s.table_name HAVING count(DISTINCT f.run_id) > 1",
        (cut,),
    ):
        by_table[r[0]] = max(by_table.get(r[0], 0), r[1])
    for table_name, n in sorted(by_table.items()):
        out.append(f"- {table_name}: {n} runs")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="patterns.py")
    ap.add_argument("--since", type=int, default=30)
    print(report(ap.parse_args(argv).since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
