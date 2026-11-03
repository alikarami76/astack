#!/usr/bin/env python3
"""Data validation by simulation, and reconciliation against real data.

  datasim.py run <scenario.yaml> --out results.json [--md report.md]      generate inputs, compare impl to oracle
  datasim.py generate <scenario.yaml> --out cases.jsonl                    synthetic dataset with expected outputs
  datasim.py source-check <scenario.yaml>                                  is the copied code still what the repo has?
  datasim.py reconcile <expected.csv> <actual.csv> --key id --cols a,b [--tol 0]
  datasim.py sqlcheck <query.sql>                                          SELECT-only gate before a db skill runs it

A scenario names an oracle (what the requirement says, scripts/product_rules.py or a module next to the scenario), an
implementation to test (python function, or a JavaScript function run by node), and how to generate inputs: seeded
random values plus the boundaries, so every run is reproducible. Mismatches are shrunk to the smallest example.
Real data never comes in here directly: reconciliation compares CSV exports that the your-db-skill or
your-db-analyst skill produced with a SELECT the sqlcheck gate passed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import random
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rpaths  # noqa: E402
import safety  # noqa: E402


def load(path) -> dict:
    s = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    s["_dir"] = str(Path(path).resolve().parent)
    return s


def _func(ref: str, base_dir: str):
    mod_name, fn = ref.split(":")
    for cand in (Path(base_dir) / f"{mod_name}.py", HERE / f"{mod_name}.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location(f"rinzler_sim_{mod_name}", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, fn)
    raise FileNotFoundError(f"module {mod_name} not found next to the scenario or in scripts/")


def _values(name: str, spec: dict, row: dict, rng: random.Random) -> int | float | str:
    t = spec.get("type", "int")
    if t == "choice":
        return rng.choice(spec["values"])
    if t == "int":
        lo = row[spec["min_field"]] if "min_field" in spec else spec.get("min", 0)
        hi = row[spec["max_field"]] if "max_field" in spec else spec["max"]
        step = spec.get("step", 1)
        if hi < lo:
            return lo
        return lo + step * rng.randint(0, (hi - lo) // step)
    if t == "expr":  # derived from earlier fields, e.g. "base * (100 - pct) // 100"; no builtins available
        return eval(spec["expr"], {"__builtins__": {}}, dict(row))  # noqa: S307 (scenario files are Rinzler's own)
    raise ValueError(f"field {name}: unknown type {t}")


def generate_inputs(s: dict) -> list[dict]:
    g = s["generate"]
    rng = random.Random(g.get("seed", 0))
    fields = g["fields"]
    rows = []
    # every combination of declared boundary values first (small by design), then seeded random rows
    bnames = [n for n, f in fields.items() if f.get("boundaries")]
    for combo in itertools.product(*[fields[n]["boundaries"] for n in bnames]):
        row = dict(zip(bnames, combo))
        for n, f in fields.items():
            if n not in row:
                row[n] = _values(n, f, row, rng)
        rows.append(row)
    for _ in range(g.get("count", 100)):
        row = {}
        for n, f in fields.items():
            row[n] = _values(n, f, row, rng)
        rows.append(row)
    for extra in g.get("include", []):
        rows.append(dict(extra))
    return rows


NODE_BATCH = """
const fs = require('fs');
const [src, fn, order] = [process.argv[1], process.argv[2], JSON.parse(process.argv[3])];
const mod = {exports: {}}; new Function('module', 'exports', fs.readFileSync(src, 'utf8'))(mod, mod.exports);
const f = mod.exports[fn];
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(rows.map(r => { const v = f(...order.map(k => r[k])); return v === undefined ? null : v; })));
"""


def _args(row: dict, names) -> dict:
    return {k: row[k] for k in (names or row.keys())}


def run_impl(s: dict, rows: list[dict]) -> list:
    impl = s["impl"]
    if impl["adapter"] == "python":
        f = _func(impl["ref"], s["_dir"])
        return [f(**{k: r[k] for k in impl.get("args", r.keys())}) for r in rows]
    if impl["adapter"] == "node":
        src = Path(s["_dir"]) / impl["source"]
        p = subprocess.run(["node", "-e", NODE_BATCH, str(src), impl["function"], json.dumps(impl["args"])],
                           input=json.dumps(rows), capture_output=True, text=True, timeout=120)
        if p.returncode:
            raise RuntimeError(f"node failed: {p.stderr[:400]}")
        return json.loads(p.stdout)
    raise ValueError(f"unknown impl adapter {impl['adapter']}")


def _size(row: dict) -> float:
    return sum(abs(v) for v in row.values() if isinstance(v, (int, float)))


def simulate(s: dict) -> dict:
    rows = generate_inputs(s)
    oracle = _func(s["oracle"]["ref"], s["_dir"])
    oargs = s["oracle"].get("args")
    expected = [oracle(**{k: r[k] for k in (oargs or r.keys())}) for r in rows]
    actual = run_impl(s, rows)
    tol = s.get("tolerance", 0)
    bad = []
    for r, e, a in zip(rows, expected, actual):
        same = e == a or (tol and isinstance(e, (int, float)) and isinstance(a, (int, float)) and abs(e - a) <= tol)
        if not same:
            bad.append({"input": r, "expected": e, "actual": a})
    bad.sort(key=lambda m: _size(m["input"]))
    return {"scenario": s["scenario"], "feature": s.get("feature", ""), "requirement_ids": s.get("requirement_ids", []),
            "oracle": s["oracle"]["ref"], "impl": s["impl"], "inputs": len(rows), "mismatches": len(bad),
            "mismatch_rate": round(len(bad) / len(rows), 6) if rows else None,
            "smallest": bad[0] if bad else None, "examples": bad[:25], "verdict": "pass" if not bad else "fail"}


def report_md(res: dict) -> str:
    L = [f"# Data simulation: {res['scenario']}", "",
         f"Feature: {res['feature']} ({', '.join(res['requirement_ids'])}). Oracle `{res['oracle']}`, implementation "
         f"`{res['impl'].get('ref') or res['impl'].get('source') + ':' + res['impl'].get('function', '')}` "
         f"({res['impl']['adapter']}).", "",
         f"**{res['verdict'].upper()}: {res['mismatches']} of {res['inputs']} inputs disagree "
         f"({(res['mismatch_rate'] or 0) * 100:.3f}%).**", ""]
    if res["smallest"]:
        m = res["smallest"]
        L += [f"Smallest example: input {json.dumps(m['input'], ensure_ascii=False)}, the requirement gives "
              f"{m['expected']!r}, the implementation gives {m['actual']!r}.", "", "| Input | Expected | Actual |",
              "|---|---|---|"]
        L += [f"| {json.dumps(x['input'], ensure_ascii=False)} | {x['expected']!r} | {x['actual']!r} |" for x in res["examples"][:15]]
    return "\n".join(L) + "\n"


def source_check(s: dict) -> dict:
    sc = s.get("source_check")
    if not sc:
        return {"status": "none"}
    p = Path(str(sc["file"]).replace("$RINZLER_WORK", str(rpaths.work_dir()))).expanduser()
    if not p.exists():
        return {"status": "no-copy", "file": str(p), "note": "build the local copy first (scripts/local/app.sh prepare)"}
    a, b = (int(x) for x in str(sc["lines"]).split("-"))
    text = "\n".join(p.read_text(encoding="utf-8").splitlines()[a - 1:b])
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return {"status": "same" if digest == sc["sha256"] else "changed", "file": str(p), "lines": sc["lines"],
            "sha256": digest, "expected": sc["sha256"]}


def reconcile(expected: str, actual: str, key: str, cols: list[str], tol: float = 0) -> dict:
    def read(p):
        with open(p, newline="", encoding="utf-8") as f:
            return {r[key]: r for r in csv.DictReader(f)}

    e, a = read(expected), read(actual)
    diffs = []
    for k in sorted(set(e) & set(a)):
        for c in cols:
            ev, av = e[k].get(c), a[k].get(c)
            try:
                same = abs(float(ev) - float(av)) <= tol
            except (TypeError, ValueError):
                same = (ev or "").strip() == (av or "").strip()
            if not same:
                diffs.append({"key": k, "column": c, "expected": ev, "actual": av})
    return {"rows_expected": len(e), "rows_actual": len(a), "missing_in_actual": sorted(set(e) - set(a))[:50],
            "unexpected_in_actual": sorted(set(a) - set(e))[:50], "value_mismatches": len(diffs), "examples": diffs[:50],
            "verdict": "pass" if not diffs and set(e) == set(a) else "fail"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datasim.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--out", required=True)
    r.add_argument("--md")
    g = sub.add_parser("generate")
    g.add_argument("scenario")
    g.add_argument("--out", required=True)
    sc = sub.add_parser("source-check")
    sc.add_argument("scenario")
    rc = sub.add_parser("reconcile")
    rc.add_argument("expected")
    rc.add_argument("actual")
    rc.add_argument("--key", required=True)
    rc.add_argument("--cols", required=True)
    rc.add_argument("--tol", type=float, default=0)
    q = sub.add_parser("sqlcheck")
    q.add_argument("sql")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        s = load(a.scenario)
        res = simulate(s) | {"source_check": source_check(s)}
        safety.check_writable(a.out)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        if a.md:
            Path(a.md).write_text(report_md(res), encoding="utf-8")
        print(json.dumps({k: res[k] for k in ("scenario", "inputs", "mismatches", "verdict")}))
        return 0 if res["verdict"] == "pass" else 1
    if a.cmd == "generate":
        s = load(a.scenario)
        rows = generate_inputs(s)
        oracle = _func(s["oracle"]["ref"], s["_dir"])
        with open(a.out, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({"input": row, "expected": oracle(**_args(row, s["oracle"].get("args")))}, ensure_ascii=False) + "\n")
        print(f"{len(rows)} rows -> {a.out}")
        return 0
    if a.cmd == "source-check":
        res = source_check(load(a.scenario))
        print(json.dumps(res))
        return 1 if res["status"] == "changed" else 0
    if a.cmd == "reconcile":
        res = reconcile(a.expected, a.actual, a.key, a.cols.split(","), a.tol)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["verdict"] == "pass" else 1
    if a.cmd == "sqlcheck":
        ok, why = safety.sql_is_readonly(Path(a.sql).read_text(encoding="utf-8"))
        print(why)
        return 0 if ok else 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
