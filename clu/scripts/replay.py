#!/usr/bin/env python3
"""Replay past-mistake cases against clu (contract only) and grade them."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "tests/replay/cases"
PREFIX = ("REPLAY TEST — contract only. Do steps 1 (recall, using kb.py) and 2 (draft the measurement contract "
          "including your challenger's objections and your questions for the user). Do NOT open a run, run any "
          "data query, or spawn data runners. Print the full contract and questions, then stop.\n\nRequest: ")


def grade(text: str, case: dict) -> dict:
    miss = [p for p in case["must"] if not re.search(p, text, re.I | re.S)]
    bad = [p for p in case.get("must_not", []) if re.search(p, text, re.I | re.S)]
    return {"id": case["id"], "pass": not miss and not bad, "missing": miss, "forbidden_hits": bad}


def run_case(case: dict) -> str:
    r = subprocess.run(["claude", "-p", "--agent", "clu", "--max-turns", "12", PREFIX + case["prompt"]],
                       capture_output=True, text=True, timeout=900, cwd="/tmp")
    return r.stdout + r.stderr


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    a = ap.parse_args(argv)
    cases = [json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))]
    if a.case:
        cases = [c for c in cases if c["id"] == a.case]
    results = []
    for c in cases:
        out = run_case(c)
        g = grade(out, c)
        g["output_tail"] = out[-3000:]
        results.append(g)
        print(f"{'PASS' if g['pass'] else 'FAIL'}  {c['id']}  missing={g['missing']} forbidden={g['forbidden_hits']}")
    (REPO / "tests/replay/last-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    passed = sum(r["pass"] for r in results)
    print(f"{passed}/{len(results)} passed")
    return 0 if passed >= min(5, len(results)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
