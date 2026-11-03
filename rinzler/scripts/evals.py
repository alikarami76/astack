#!/usr/bin/env python3
"""Functional eval suites: validate, run, report, compare with a baseline (format: docs/eval-format.md).

  evals.py validate <suite.yaml>...
  evals.py run <suite.yaml> --out results.json [--allow-network] [--playwright-results pw.json] [--manual-results m.yaml]
  evals.py report <results.json> [--out report.md]
  evals.py diff <baseline.json> <results.json>          exit 1 when a case that passed now fails
  evals.py trace <suite.yaml>... --requirements req.yaml [--results r.json]   requirement -> cases -> results matrix

Adapters (case.target.adapter): python (call a function), node (call a JavaScript function with node), http (call an
endpoint on local/demo/stage only, through safety.py), playwright (read a Playwright JSON report), manual (read
results a person recorded). Cases whose adapter cannot run here are reported as not_run, never as passed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import importlib.util
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402
import safety  # noqa: E402

ADAPTERS = ("python", "node", "http", "playwright", "manual")
KINDS = ("acceptance", "functional", "edge", "negative", "regression", "exploratory")
STATUSES = ("draft", "ready", "retired")
RESULT_STATES = ("pass", "fail", "error", "not_run", "skipped")


def load(path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a suite is a mapping with suite, feature and cases")
    data["_path"] = str(Path(path).resolve())
    return data


def validate(suite: dict) -> list[str]:
    errs = [f"missing {k}" for k in ("suite", "feature", "cases") if not suite.get(k)]
    seen = set()
    for i, c in enumerate(suite.get("cases") or []):
        where = f"case {c.get('id') or i}"
        for k in ("id", "title", "requirement_ids", "expected", "target"):
            if not c.get(k):
                errs.append(f"{where}: missing {k}")
        if c.get("id") in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(c.get("id"))
        if c.get("kind", "functional") not in KINDS:
            errs.append(f"{where}: kind must be one of {KINDS}")
        if c.get("status", "ready") not in STATUSES:
            errs.append(f"{where}: status must be one of {STATUSES}")
        if not isinstance(c.get("requirement_ids", []), list):
            errs.append(f"{where}: requirement_ids must be a list")
        t = c.get("target") or {}
        if t and t.get("adapter") not in ADAPTERS:
            errs.append(f"{where}: adapter must be one of {ADAPTERS}")
        if t.get("adapter") in ("python", "node", "http") and not c.get("checks"):
            errs.append(f"{where}: an automated case needs checks")
        for ch in c.get("checks") or []:
            errs += [f"{where}: {e}" for e in checks.validate_check(ch)]
    return errs


# ---------- adapters ----------
def _python(case, target, base: Path) -> dict:
    mod_name, func = target["ref"].split(":")
    candidate = base / f"{mod_name}.py"
    if candidate.exists():
        spec = importlib.util.spec_from_file_location(f"rinzler_case_{mod_name}", candidate)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(mod_name)
    t0 = time.perf_counter()
    try:
        out = getattr(mod, func)(**(case.get("input") or {}))
        return {"output": out, "latency_ms": (time.perf_counter() - t0) * 1000}
    except Exception as e:  # the system under test raising is an observation, not a runner crash
        return {"output": None, "error": f"{type(e).__name__}: {e}", "latency_ms": (time.perf_counter() - t0) * 1000}


NODE_SHIM = """
const fs = require('fs');
const [src, fn, input] = [process.argv[1], process.argv[2], JSON.parse(process.argv[3])];
const mod = {}; new Function('module', 'exports', fs.readFileSync(src, 'utf8'))(mod, mod.exports = {});
const f = (mod.exports || {})[fn];
if (typeof f !== 'function') { console.error('no function ' + fn); process.exit(2); }
const out = f(...(Array.isArray(input) ? input : [input]));
process.stdout.write(JSON.stringify(out === undefined ? null : out));
"""


def _node(case, target, base: Path) -> dict:
    src = (base / target["source"]).resolve()
    args = case.get("input")
    if isinstance(args, dict) and "args" in args:
        args = args["args"]
    t0 = time.perf_counter()
    p = subprocess.run(["node", "-e", NODE_SHIM, str(src), target["function"], json.dumps(args)],
                       capture_output=True, text=True, timeout=30)
    ms = (time.perf_counter() - t0) * 1000
    if p.returncode != 0:
        return {"output": None, "error": p.stderr.strip()[:500], "latency_ms": ms}
    return {"output": json.loads(p.stdout or "null"), "latency_ms": ms}


def _http(case, target, allow_network: bool) -> dict | None:
    if not allow_network:
        return None
    method = target.get("method", "GET").upper()
    safety.check_target(target["url"], "read" if method in ("GET", "HEAD") else "write", target.get("approval"))
    body = json.dumps(case.get("input")).encode() if method not in ("GET", "HEAD") and case.get("input") else None
    req = urllib.request.Request(target["url"], data=body, method=method,
                                 headers={"Content-Type": "application/json", **(target.get("headers") or {})})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=target.get("timeout", 20)) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    ms = (time.perf_counter() - t0) * 1000
    try:
        out = json.loads(raw)
    except ValueError:
        out = raw
    return {"status": status, "output": out, "text": safety.redact(raw[:2000]), "latency_ms": ms}


def _playwright_index(path) -> dict[str, str]:
    """title -> passed|failed from a Playwright JSON report (npx playwright test --reporter=json)."""
    idx: dict[str, str] = {}

    def walk(suite):
        for spec in suite.get("specs", []):
            states = [r.get("status") for t in spec.get("tests", []) for r in t.get("results", [])[-1:]]
            ok = bool(states) and all(s in ("passed", "expected") for s in states)
            idx[spec.get("title", "")] = "pass" if ok else "fail"
        for s in suite.get("suites", []):
            walk(s)

    for s in json.loads(Path(path).read_text(encoding="utf-8")).get("suites", []):
        walk(s)
    return idx


def run_suite(suite: dict, allow_network=False, playwright_results=None, manual_results=None) -> dict:
    base = Path(suite["_path"]).parent
    pw = _playwright_index(playwright_results) if playwright_results else {}
    manual = yaml.safe_load(Path(manual_results).read_text(encoding="utf-8")) if manual_results else {}
    out = []
    for case in suite["cases"]:
        rec = {"id": case["id"], "title": case["title"], "requirement_ids": case.get("requirement_ids", []),
               "tags": case.get("tags", []), "kind": case.get("kind", "functional"), "checks": []}
        if case.get("status") == "retired":
            out.append(rec | {"status": "skipped", "note": "retired"})
            continue
        t = case["target"]
        a = t["adapter"]
        obs = None
        try:
            if a == "python":
                obs = _python(case, t, base)
            elif a == "node":
                obs = _node(case, t, base)
            elif a == "http":
                obs = _http(case, t, allow_network)
            elif a == "playwright":
                st = pw.get(t.get("grep") or case["title"])
                rec |= {"status": st or "not_run", "note": "from Playwright report" if st else "no Playwright result"}
            elif a == "manual":
                m = manual.get(case["id"])
                st = (m.get("result") if isinstance(m, dict) else m) if m else None
                rec |= {"status": st if st in ("pass", "fail") else "not_run",
                        "note": (m.get("notes", "") if isinstance(m, dict) else "") or ("" if m else "needs a person or rinzler-explorer")}
        except (safety.NeedsApproval, safety.Refused) as e:
            rec |= {"status": "skipped", "note": str(e)}
        except Exception as e:  # noqa: BLE001
            rec |= {"status": "error", "note": f"{type(e).__name__}: {e}"}
        if a in ("python", "node", "http") and "status" not in rec:
            if obs is None:
                rec |= {"status": "not_run", "note": "network calls need --allow-network"}
            else:
                for ch in case.get("checks", []):
                    ok, detail = checks.run_check(ch, obs)
                    rec["checks"].append({"type": ch["type"], "on": ch.get("on", "output"), "passed": ok, "detail": detail})
                rec["status"] = "pass" if all(c["passed"] for c in rec["checks"]) else "fail"
                rec["observation"] = {k: v for k, v in obs.items() if k != "text"}
                rec["latency_ms"] = round(obs.get("latency_ms", 0), 2)
        out.append(rec)
    return {"suite": suite["suite"], "feature": suite["feature"], "suite_path": suite["_path"],
            "run_at": _dt.datetime.now().isoformat(timespec="seconds"), "cases": out, "summary": summarize(out)}


def summarize(cases: list[dict]) -> dict:
    n = defaultdict(int)
    for c in cases:
        n[c["status"]] += 1
    graded = n["pass"] + n["fail"] + n["error"]
    return {**{s: n[s] for s in RESULT_STATES}, "total": len(cases), "graded": graded,
            "pass_rate": round(n["pass"] / graded, 4) if graded else None}


def by_key(cases, key) -> dict[str, dict]:
    groups = defaultdict(list)
    for c in cases:
        for k in c.get(key) or ["(none)"]:
            groups[k].append(c)
    return {k: summarize(v) for k, v in sorted(groups.items(), key=lambda kv: natural(kv[0]))}


def natural(s: str):
    import re as _re
    return [int(x) if x.isdigit() else x for x in _re.split(r"(\d+)", str(s))]


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def report(results: dict) -> str:
    s = results["summary"]
    lines = [f"# Eval report: {results['suite']}", "",
             f"Feature: {results['feature']}. Run at {results['run_at']}.", "",
             f"**Pass rate {_pct(s['pass_rate'])}** ({s['pass']} of {s['graded']} graded cases passed; "
             f"{s['fail']} failed, {s['error']} errors, {s['not_run']} not run, {s['skipped']} skipped).", "",
             "## By requirement", "", "| Requirement | Pass | Fail | Not run | Pass rate |", "|---|---|---|---|---|"]
    for k, v in by_key(results["cases"], "requirement_ids").items():
        lines.append(f"| {k} | {v['pass']} | {v['fail'] + v['error']} | {v['not_run'] + v['skipped']} | {_pct(v['pass_rate'])} |")
    lines += ["", "## By tag", "", "| Tag | Pass | Fail | Pass rate |", "|---|---|---|---|"]
    for k, v in by_key(results["cases"], "tags").items():
        lines.append(f"| {k} | {v['pass']} | {v['fail'] + v['error']} | {_pct(v['pass_rate'])} |")
    fails = [c for c in results["cases"] if c["status"] in ("fail", "error")]
    lines += ["", "## Failures", ""]
    if not fails:
        lines.append("None.")
    for c in fails:
        why = "; ".join(ch["detail"] for ch in c["checks"] if not ch["passed"]) or c.get("note", "")
        lines.append(f"- **{c['id']}** ({', '.join(c['requirement_ids'])}) {c['title']}: {why}")
    nr = [c for c in results["cases"] if c["status"] == "not_run"]
    if nr:
        lines += ["", "## Not run here", ""] + [f"- {c['id']}: {c['title']} ({c.get('note', '')})" for c in nr]
    return "\n".join(lines) + "\n"


def diff(base: dict, new: dict) -> dict:
    b = {c["id"]: c["status"] for c in base["cases"]}
    n = {c["id"]: c["status"] for c in new["cases"]}
    return {"regressed": sorted(i for i in n if b.get(i) == "pass" and n[i] in ("fail", "error")),
            "fixed": sorted(i for i in n if b.get(i) in ("fail", "error") and n[i] == "pass"),
            "still_failing": sorted(i for i in n if b.get(i) in ("fail", "error") and n[i] in ("fail", "error")),
            "added": sorted(set(n) - set(b)), "removed": sorted(set(b) - set(n)),
            "pass_rate": {"baseline": base["summary"]["pass_rate"], "now": new["summary"]["pass_rate"]}}


def trace(suites: list[dict], requirements: list[dict], results: list[dict] | None = None) -> str:
    status = {c["id"]: c["status"] for r in (results or []) for c in r["cases"]}
    cases_by_req = defaultdict(list)
    for s in suites:
        for c in s["cases"]:
            for rid in c.get("requirement_ids", []):
                cases_by_req[rid].append(c["id"])
    lines = ["| Requirement | Text | Cases | Results |", "|---|---|---|---|"]
    gaps = []
    for r in requirements:
        ids = cases_by_req.get(r["id"], [])
        if not ids:
            gaps.append(r["id"])
        res = ", ".join(f"{status.get(i, 'not_run')}" for i in ids) if results else "-"
        counts = defaultdict(int)
        for i in ids:
            counts[status.get(i, "not_run")] += 1
        res = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) if results else "-"
        lines.append(f"| {r['id']} | {r['text'][:90]} | {len(ids)}: {', '.join(ids[:6])}{' …' if len(ids) > 6 else ''} | {res} |")
    known = {r["id"] for r in requirements}
    orphans = sorted(rid for rid in cases_by_req if rid not in known)
    lines += ["", f"Requirements without a case: {', '.join(gaps) or 'none'}.",
              f"Cases pointing at unknown requirements: {', '.join(orphans) or 'none'}."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evals.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("suites", nargs="+")
    r = sub.add_parser("run")
    r.add_argument("suite")
    r.add_argument("--out", required=True)
    r.add_argument("--allow-network", action="store_true")
    r.add_argument("--playwright-results")
    r.add_argument("--manual-results")
    rp = sub.add_parser("report")
    rp.add_argument("results")
    rp.add_argument("--out")
    d = sub.add_parser("diff")
    d.add_argument("baseline")
    d.add_argument("results")
    t = sub.add_parser("trace")
    t.add_argument("suites", nargs="+")
    t.add_argument("--requirements", required=True)
    t.add_argument("--results", action="append")
    t.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        bad = 0
        for p in a.suites:
            errs = validate(load(p))
            print(f"{p}: {'ok' if not errs else str(len(errs)) + ' problem(s)'}")
            for e in errs:
                print(f"  - {e}")
            bad += bool(errs)
        return 1 if bad else 0
    if a.cmd == "run":
        suite = load(a.suite)
        errs = validate(suite)
        if errs:
            print("invalid suite:\n  " + "\n  ".join(errs), file=sys.stderr)
            return 2
        res = run_suite(suite, a.allow_network, a.playwright_results, a.manual_results)
        safety.check_writable(a.out)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps(res["summary"]))
        return 0
    if a.cmd == "report":
        text = report(json.loads(Path(a.results).read_text(encoding="utf-8")))
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        print(text if not a.out else a.out)
        return 0
    if a.cmd == "diff":
        dd = diff(*(json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.baseline, a.results)))
        print(json.dumps(dd, indent=1))
        return 1 if dd["regressed"] else 0
    if a.cmd == "trace":
        reqs = yaml.safe_load(Path(a.requirements).read_text(encoding="utf-8"))["requirements"]
        res = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.results or []]
        text = trace([load(p) for p in a.suites], reqs, res)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
