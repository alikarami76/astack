#!/usr/bin/env python3
"""Performance: front-end (Lighthouse lab metrics) and API load (k6), with baselines and regression thresholds.

  perf.py lighthouse <url> --out <dir> [--runs 3] [--approval "<owner's words>"]
  perf.py parse-lh <report.json>... [--out metrics.json]        median of each metric over the runs
  perf.py k6 <script.js> --base-url <url> --scenario smoke|load|stress|soak|spike --out <dir> [--approval ...]
  perf.py parse-k6 <summary.json> [--out metrics.json]
  perf.py compare <metrics.json> --baseline <baseline.json> [--budget budget.yaml]   exit 1 on a regression
  perf.py baseline <metrics.json> --target <name> --env local|demo|stage [--commit sha]   store it in the KB

Targets go through safety.py: a local build is free; demo and stage need the owner's approval for load (shared
environments); production is refused for load unless the owner approved that exact run, and is never written to.
Lab numbers vary, so Lighthouse runs at least 3 times and the median is used; compare a run only with a baseline from
the same machine, build mode and environment.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent / "kb")]
import safety  # noqa: E402

LIGHTHOUSE = "lighthouse@12.6.1"
LH_AUDITS = {"lcp_ms": "largest-contentful-paint", "fcp_ms": "first-contentful-paint", "cls": "cumulative-layout-shift",
             "tbt_ms": "total-blocking-time", "speed_index_ms": "speed-index", "tti_ms": "interactive"}
# web.dev "good" thresholds; TBT stands in for INP in the lab (INP needs real interactions)
DEFAULT_BUDGET = {"lcp_ms": {"max": 2500}, "cls": {"max": 0.1}, "tbt_ms": {"max": 200},
                  "http_p95_ms": {"max_regression": 0.20}, "http_p99_ms": {"max_regression": 0.25},
                  "error_rate": {"max": 0.01}, "perf_score": {"min_regression": 0.05}}
LOWER_IS_BETTER = {"lcp_ms", "fcp_ms", "cls", "tbt_ms", "speed_index_ms", "tti_ms", "http_p50_ms", "http_p90_ms",
                   "http_p95_ms", "http_p99_ms", "error_rate"}


def _gate(url: str, purpose: str, approval: str | None) -> str:
    try:
        return safety.check_target(url, purpose, approval)
    except safety.NeedsApproval as e:
        raise SystemExit(f"NEEDS APPROVAL: {e}. Ask the owner; pass his words with --approval.")
    except safety.Refused as e:
        raise SystemExit(f"REFUSED: {e}")


def lighthouse(url: str, out: Path, runs: int, approval: str | None) -> list[Path]:
    _gate(url, "read", approval)
    safety.check_writable(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(1, runs + 1):
        p = out / f"lighthouse-{i}.json"
        cmd = ["npx", "-y", LIGHTHOUSE, url, "--output=json", f"--output-path={p}", "--quiet",
               "--only-categories=performance,accessibility", "--chrome-flags=--headless=new --no-sandbox"]
        subprocess.run(cmd, check=True, timeout=300)
        paths.append(p)
    return paths


def parse_lh(paths: list[str]) -> dict:
    per = {k: [] for k in [*LH_AUDITS, "perf_score", "a11y_score"]}
    for p in paths:
        r = json.loads(Path(p).read_text(encoding="utf-8"))
        for k, audit in LH_AUDITS.items():
            v = (r.get("audits", {}).get(audit) or {}).get("numericValue")
            if v is not None:
                per[k].append(v)
        for k, cat in (("perf_score", "performance"), ("a11y_score", "accessibility")):
            s = (r.get("categories", {}).get(cat) or {}).get("score")
            if s is not None:
                per[k].append(s)
    return {"kind": "lighthouse", "runs": len(paths),
            "metrics": {k: round(statistics.median(v), 4) for k, v in per.items() if v},
            "spread": {k: round(max(v) - min(v), 4) for k, v in per.items() if len(v) > 1}}


def k6(script: str, base_url: str, scenario: str, out: Path, approval: str | None) -> Path:
    env = _gate(base_url, "load" if scenario != "smoke" else "read", approval)
    if not shutil.which("k6"):
        raise SystemExit("k6 is not installed. Ask the owner before installing it (brew install k6).")
    safety.check_writable(out)
    out.mkdir(parents=True, exist_ok=True)
    summary = out / f"k6-{scenario}-summary.json"
    subprocess.run(["k6", "run", "--summary-export", str(summary), "-e", f"BASE_URL={base_url}",
                    "-e", f"SCENARIO={scenario}", "-e", f"RINZLER_ENV={env}", script], check=False)
    return summary


def parse_k6(path: str) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
    d = m.get("http_req_duration", {})
    d = d.get("values", d)
    failed = m.get("http_req_failed", {})
    failed = failed.get("values", failed)
    reqs = m.get("http_reqs", {})
    reqs = reqs.get("values", reqs)
    pick = lambda *ks: next((d[k] for k in ks if k in d), None)  # noqa: E731
    return {"kind": "k6", "metrics": {k: v for k, v in {
        "http_p50_ms": pick("med", "p(50)"), "http_p90_ms": pick("p(90)"), "http_p95_ms": pick("p(95)"),
        "http_p99_ms": pick("p(99)"), "http_avg_ms": pick("avg"),
        "error_rate": failed.get("rate", failed.get("value")), "rps": reqs.get("rate")}.items() if v is not None}}


def compare(now: dict, base: dict | None, budget: dict) -> dict:
    rows = []
    for k, v in now["metrics"].items():
        b = (base or {}).get("metrics", {}).get(k)
        rule = budget.get(k, {})
        ok, why = True, []
        if "max" in rule and v > rule["max"]:
            ok, why = False, why + [f"over budget {rule['max']}"]
        if "min" in rule and v < rule["min"]:
            ok, why = False, why + [f"under minimum {rule['min']}"]
        if b not in (None, 0) and "max_regression" in rule and k in LOWER_IS_BETTER and v > b * (1 + rule["max_regression"]):
            ok, why = False, why + [f"{(v / b - 1) * 100:.0f}% worse than baseline (limit {rule['max_regression'] * 100:.0f}%)"]
        if b is not None and "min_regression" in rule and k not in LOWER_IS_BETTER and v < b - rule["min_regression"]:
            ok, why = False, why + [f"dropped {b - v:.2f} from baseline (limit {rule['min_regression']})"]
        rows.append({"metric": k, "now": v, "baseline": b, "passed": ok, "why": "; ".join(why) or "ok"})
    return {"rows": rows, "regressed": [r["metric"] for r in rows if not r["passed"]],
            "verdict": "pass" if all(r["passed"] for r in rows) else "fail"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="perf.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lh = sub.add_parser("lighthouse")
    lh.add_argument("url")
    lh.add_argument("--out", required=True)
    lh.add_argument("--runs", type=int, default=3)
    lh.add_argument("--approval")
    pl = sub.add_parser("parse-lh")
    pl.add_argument("reports", nargs="+")
    pl.add_argument("--out")
    k = sub.add_parser("k6")
    k.add_argument("script")
    k.add_argument("--base-url", required=True)
    k.add_argument("--scenario", choices=["smoke", "load", "stress", "soak", "spike"], default="smoke")
    k.add_argument("--out", required=True)
    k.add_argument("--approval")
    pk = sub.add_parser("parse-k6")
    pk.add_argument("summary")
    pk.add_argument("--out")
    c = sub.add_parser("compare")
    c.add_argument("metrics")
    c.add_argument("--baseline")
    c.add_argument("--budget")
    b = sub.add_parser("baseline")
    b.add_argument("metrics")
    b.add_argument("--target", required=True)
    b.add_argument("--env", required=True)
    b.add_argument("--commit", default="")
    a = ap.parse_args(argv)

    def emit(obj, out=None):
        text = json.dumps(obj, indent=1)
        if out:
            Path(out).write_text(text, encoding="utf-8")
        print(text)

    if a.cmd == "lighthouse":
        paths = lighthouse(a.url, Path(a.out), a.runs, a.approval)
        emit(parse_lh([str(p) for p in paths]), Path(a.out) / "metrics.json")
    elif a.cmd == "parse-lh":
        emit(parse_lh(a.reports), a.out)
    elif a.cmd == "k6":
        print(k6(a.script, a.base_url, a.scenario, Path(a.out), a.approval))
    elif a.cmd == "parse-k6":
        emit(parse_k6(a.summary), a.out)
    elif a.cmd == "compare":
        budget = DEFAULT_BUDGET | (yaml.safe_load(Path(a.budget).read_text()) if a.budget else {})
        base = json.loads(Path(a.baseline).read_text()) if a.baseline else None
        res = compare(json.loads(Path(a.metrics).read_text()), base, budget)
        emit(res)
        return 0 if res["verdict"] == "pass" else 1
    elif a.cmd == "baseline":
        import store
        m = json.loads(Path(a.metrics).read_text())
        c = store.connect()
        ids = [store.upsert(c, "baseline", {"metric": k, "target": a.target, "environment": a.env, "value": v,
                                            "commit_sha": a.commit, "source_path": str(Path(a.metrics).resolve())})
               for k, v in m["metrics"].items()]
        store.export(c, "baseline")
        print(json.dumps(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
