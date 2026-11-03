#!/usr/bin/env python3
"""clu causal and experiment statistics. Run with the toolkit environment:

  "$CLU/.venv-causal/bin/python" "$CLU/scripts/causal/cstat.py" <command> [options] --out <run>/results/<name>.json

Commands: power, srm, abtest, multitest, permute, evalue, did, rdd, its, synth, gbcheck.
Every command writes one JSON result (estimate, estimand, verdict, diagnostics, warnings) and prints a one-line summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cs_common import load_table, unavailable, write  # noqa: E402


def _floats(s: str | None):
    return [float(x) for x in s.split(",")] if s else None


def _list(s: str | None):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cstat.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--out", help="result JSON path")
        return p

    p = cmd("power")
    p.add_argument("--metric", choices=["mean", "proportion", "ratio"], required=True)
    p.add_argument("--baseline", type=float, required=True, help="control mean, rate or ratio")
    p.add_argument("--sd", type=float)
    p.add_argument("--mde", type=float, help="relative MDE, e.g. 0.02")
    p.add_argument("--n-per-arm", type=float)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--arms", type=int, default=2)
    p.add_argument("--cluster-size", type=float)
    p.add_argument("--icc", type=float)
    p.add_argument("--units-per-day", type=float)
    p.add_argument("--smallest-effect", type=float, help="relative effect that matters for the decision")

    p = cmd("srm")
    p.add_argument("--counts", required=True)
    p.add_argument("--weights")

    p = cmd("abtest")
    p.add_argument("--data", required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--control", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--den")
    p.add_argument("--pre")
    p.add_argument("--weights")
    p.add_argument("--winsorize", type=float)
    p.add_argument("--mde", type=float, help="relative MDE; enables the 'no effect larger than' verdict")

    p = cmd("multitest")
    p.add_argument("--p", required=True)
    p.add_argument("--names")
    p.add_argument("--method", choices=["bh", "holm"], default="bh")

    p = cmd("permute")
    p.add_argument("--data", required=True)
    p.add_argument("--cluster", required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--treated", default="1")
    p.add_argument("--iters", type=int, default=10000)

    p = cmd("evalue")
    p.add_argument("--rr", type=float)
    p.add_argument("--smd", type=float)
    p.add_argument("--ci-low", type=float)
    p.add_argument("--ci-high", type=float)

    p = cmd("did")
    p.add_argument("--data", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--time", required=True)
    p.add_argument("--first-treat", required=True, help="column: first treated period, 0 = never treated")
    p.add_argument("--method", choices=["cs", "sa", "imputation", "twfe"], default="cs")
    p.add_argument("--control", choices=["never", "notyet"], default="never")
    p.add_argument("--cluster")
    p.add_argument("--covariates")
    p.add_argument("--placebo-shift", type=int)
    p.add_argument("--honest-m", type=float, default=1.0)
    p.add_argument("--mde-abs", type=float)
    p.add_argument("--n-bootstrap", type=int, default=0,
                   help="multiplier-bootstrap draws for the CIs (e.g. 999); use with few treated clusters")

    p = cmd("rdd")
    p.add_argument("--data", required=True)
    p.add_argument("--x", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--cutoff", type=float, required=True)
    p.add_argument("--fuzzy")
    p.add_argument("--covariates")
    p.add_argument("--placebo-cutoffs")
    p.add_argument("--mde-abs", type=float)

    p = cmd("its")
    p.add_argument("--data", required=True)
    p.add_argument("--time", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--period", type=int, default=7)
    p.add_argument("--controls")
    p.add_argument("--mde-abs", type=float)

    p = cmd("synth")
    p.add_argument("--data", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--time", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--treated-unit", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--method", choices=["sc", "sdid"], default="sc")
    p.add_argument("--donors")
    p.add_argument("--mde-abs", type=float)

    p = cmd("gbcheck")
    p.add_argument("--arms", required=True, help="JSON file: list of per-arm aggregates, control first")
    p.add_argument("--metric", choices=["mean", "proportion", "ratio"], required=True)
    p.add_argument("--reported", help="JSON file: {percent_change, ci_low, ci_high} as fractions")
    p.add_argument("--tolerance", type=float, default=0.1)
    return ap


def run(a) -> dict:
    if a.cmd == "power":
        from cs_experiments import power
        return power(a.metric, a.baseline, a.sd, a.mde, a.n_per_arm, a.alpha, a.power, a.arms, a.cluster_size, a.icc,
                     a.units_per_day, a.smallest_effect)
    if a.cmd == "srm":
        from cs_experiments import srm
        return srm(_floats(a.counts), _floats(a.weights))
    if a.cmd == "abtest":
        from cs_experiments import abtest
        df = load_table(a.data)
        control = type(df[a.arm].iloc[0])(a.control) if not isinstance(df[a.arm].iloc[0], str) else a.control
        return abtest(df, a.arm, a.y, control, a.den, a.pre, _floats(a.weights), a.winsorize, a.mde)
    if a.cmd == "multitest":
        from cs_experiments import multitest
        return multitest(_floats(a.p), _list(a.names), a.method)
    if a.cmd == "permute":
        from cs_inference import permute
        return permute(load_table(a.data), a.cluster, a.arm, a.y, _coerce(a.treated), a.iters)
    if a.cmd == "evalue":
        from cs_inference import evalue
        return evalue(a.rr, a.ci_low, a.ci_high, a.smd)
    try:
        if a.cmd == "did":
            from cs_did import did
            return did(load_table(a.data), a.y, a.unit, a.time, a.first_treat, a.method, a.control, a.cluster,
                       _list(a.covariates), a.placebo_shift, a.honest_m, a.mde_abs, a.n_bootstrap)
        if a.cmd == "rdd":
            from cs_rdd import rdd
            return rdd(load_table(a.data), a.x, a.y, a.cutoff, a.fuzzy, _list(a.covariates), _floats(a.placebo_cutoffs), a.mde_abs)
        if a.cmd == "its":
            from cs_its import its
            return its(load_table(a.data), a.time, a.y, _coerce(a.start), a.period, _list(a.controls), a.mde_abs)
        if a.cmd == "synth":
            from cs_synth import synth
            return synth(load_table(a.data), a.unit, a.time, a.y, _coerce(a.treated_unit), _coerce(a.start), a.method,
                         [_coerce(x) for x in _list(a.donors)] if a.donors else None, a.mde_abs)
        if a.cmd == "gbcheck":
            from cs_gbcheck import gbcheck
            rep = json.loads(Path(a.reported).read_text()) if a.reported else None
            return gbcheck(json.loads(Path(a.arms).read_text()), a.metric, rep, tolerance=a.tolerance)
    except ModuleNotFoundError as e:
        return unavailable(a.cmd, f"missing package {e.name}; run scripts/causal/setup.sh")
    raise SystemExit(f"unknown command {a.cmd}")


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    res = run(a)
    write(res, a.out)
    return 0 if res["status"] in ("ok", "diagnostic_failed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
