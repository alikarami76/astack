#!/usr/bin/env python3
"""Generate draft functional eval cases from numbered requirements (the mechanical half of eval authoring).

  eval_gen.py <requirements.yaml> --out <suite.yaml> [--suite name]

For each requirement it writes one acceptance case, and for every limit it can read from the text it writes boundary
cases (just below, on, just above each limit), negative cases (not a whole number, not a multiple of 100, empty,
letters) and a Persian-digits case. Statuses and other lists become one case per value. Every generated case has
status: draft and adapter: manual. rinzler-eval-author then reviews them, fixes wrong guesses, adds what a reader of
the requirement would test, and binds the ready ones to an adapter (python, node, http or playwright).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402

MULT = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "هزار": 1e3, "میلیون": 1e6, "میلیارد": 1e9}
NUM = r"(\d[\d,]*(?:\.\d+)?)(?:\s*(thousand|million|billion))?"


def _n(num: str, mult: str | None) -> float:
    v = float(num.replace(",", "")) * MULT.get((mult or "").lower(), 1)
    return int(v) if v == int(v) else v


def parse_limits(text: str) -> list[dict]:
    """Find limits in plain English requirement text. Returns [{field, min, max, min_exclusive, max_exclusive}]."""
    t = checks.to_en_digits(text)
    out = []
    for m in re.finditer(r"between\s+" + NUM + r"\s+and\s+" + NUM, t, re.I):
        out.append({"min": _n(m.group(1), m.group(2)), "max": _n(m.group(3), m.group(4)), "span": m.span()})
    for m in re.finditer(r"(?<![\d.,])" + NUM + r"\s*(?:-|to)\s*" + NUM + r"\s*(percent|%)", t, re.I):
        out.append({"min": _n(m.group(1), m.group(2)), "max": _n(m.group(3), m.group(4)), "unit": "percent", "span": m.span()})
    for m in re.finditer(r"(at least|minimum(?: of)?|min\.?)\s+" + NUM, t, re.I):
        out.append({"min": _n(m.group(2), m.group(3)), "span": m.span()})
    for m in re.finditer(r"(at most|up to|maximum(?: of)?|max\.?|no more than)\s+" + NUM, t, re.I):
        out.append({"max": _n(m.group(2), m.group(3)), "span": m.span()})
    for m in re.finditer(r"(under|less than|below)\s+" + NUM, t, re.I):
        out.append({"max": _n(m.group(2), m.group(3)), "max_exclusive": True, "span": m.span()})
    for o in out:  # "up to 48 hours" is a timing rule, not an input limit
        if re.match(r"\s*(hours?|days?|minutes?|seconds?|weeks?|months?)\b", t[o["span"][1]:o["span"][1] + 12], re.I):
            o["unit"] = "time"
    # drop limits that sit inside a range already found
    ranges = [o["span"] for o in out if "min" in o and "max" in o]
    kept = [o for o in out if ("min" in o and "max" in o) or not any(a <= o["span"][0] < b for a, b in ranges)]
    return [{k: v for k, v in o.items() if k != "span"} for o in kept]


def parse_flags(text: str) -> dict:
    t = text.lower()
    return {"integer": bool(re.search(r"whole number|integer|عدد صحیح", t)),
            "multiple": 100 if re.search(r"end(?:s)? with 00|multiple of 100|با ۰۰ تمام", t) else None,
            "digits_only": "digits only" in t}


def parse_values(text: str) -> list[str]:
    m = re.search(r"(?:statuses|states|types|price types|tools|plan types)\s*:\s*(.+)$", text, re.I)
    if not m:
        return []
    body = re.split(r";", m.group(1))[0]
    return [v.strip(" .'\"") for v in re.split(r",|\bor\b|\band\b", body) if v.strip(" .'\"")]


def _case(rid, n, title, kind, tags, expected, input_=None, valid=None):
    c = {"id": f"{rid}-{n:03d}", "title": title, "requirement_ids": [rid], "kind": kind, "tags": tags,
         "status": "draft", "expected": expected, "target": {"adapter": "manual"}}
    if input_ is not None:
        c["input"] = input_
    if valid is not None:
        c["expect_valid"] = valid
    return c


def generate(reqs: dict) -> dict:
    cases = []
    for r in reqs["requirements"]:
        rid, text = r["id"], r["text"]
        field = r.get("field", "value")
        n = 1
        cases.append(_case(rid, n, f"Acceptance: {text[:80]}", "acceptance", ["acceptance"],
                           f"The product does what the requirement says: {text}"))
        flags = parse_flags(text)
        limits = parse_limits(text)
        for lim in [x for x in limits if x.get("unit") == "time"]:
            n += 1
            when = lim.get("max", lim.get("min"))
            cases.append(_case(rid, n, f"Timing: just inside and just outside {when:g} (time limit)", "edge",
                               ["boundary", "time"], f"behaves as the rule says at {when:g} and one unit either side "
                               "(needs controlled clock or demo data)"))
        limits = [x for x in limits if x.get("unit") != "time"]
        for lim in limits:
            step = 1 if flags["integer"] or flags["multiple"] or all(
                isinstance(lim.get(k, 0), int) for k in ("min", "max")) else 0.01
            if flags["multiple"]:
                step = flags["multiple"]
            if "min" in lim:
                lo = lim["min"]
                for v, ok, label in ((lo - step, False, "just below the minimum"), (lo, True, "on the minimum"),
                                     (lo + step, True, "just above the minimum")):
                    n += 1
                    cases.append(_case(rid, n, f"{field} {label} ({v:g})", "edge", ["boundary"],
                                       "accepted" if ok else f"rejected with the minimum message (min {lo:g})",
                                       {field: v}, ok))
            if "max" in lim:
                hi = lim["max"]
                hi_ok = not lim.get("max_exclusive")
                for v, ok, label in ((hi - step, True, "just below the maximum"), (hi, hi_ok, "on the maximum"),
                                     (hi + step, False, "just above the maximum")):
                    n += 1
                    cases.append(_case(rid, n, f"{field} {label} ({v:g})", "edge", ["boundary"],
                                       "accepted" if ok else f"rejected with the maximum message (max {hi:g})",
                                       {field: v}, ok))
        anchor = next((lim.get("min", lim.get("max")) for lim in limits), None)
        if flags["integer"]:
            n += 1
            v = (anchor or 1) + 0.5
            cases.append(_case(rid, n, f"{field} is not a whole number ({v:g})", "negative", ["negative", "type"],
                               "rejected: must be a whole number", {field: v}, False))
        if flags["multiple"]:
            n += 1
            base = int(anchor or 10_000)
            cases.append(_case(rid, n, f"{field} not a multiple of {flags['multiple']} ({base + 50})", "negative",
                               ["negative", "format"], f"rejected: must end with 00", {field: base + 50}, False))
            n += 1
            cases.append(_case(rid, n, f"{field} a multiple of {flags['multiple']} ({base - base % 100 + 100})", "edge",
                               ["format"], "accepted", {field: base - base % 100 + 100}, True))
        if limits or flags["integer"] or flags["digits_only"]:
            n += 1
            sample = int(anchor) if anchor and float(anchor).is_integer() else 10
            fa = str(sample).translate(str.maketrans("0123456789", checks.PERSIAN_DIGITS))
            cases.append(_case(rid, n, f"{field} typed with Persian digits ({fa})", "edge", ["rtl", "persian-digits"],
                               "treated exactly like the same number in Latin digits", {field: fa}, None))
            for bad, label in (("", "empty"), ("abc", "letters")):
                n += 1
                cases.append(_case(rid, n, f"{field} {label}", "negative", ["negative"],
                                   "rejected with a clear message; nothing is saved", {field: bad}, False))
        for val in parse_values(text):
            n += 1
            cases.append(_case(rid, n, f"Shows the value '{val}' correctly", "functional", ["states"],
                               f"'{val}' is shown with its own label and allowed actions, in Persian, right-to-left"))
    return {"suite": reqs.get("suite") or re.sub(r"\W+", "-", reqs["feature"].lower()).strip("-") + "-functional",
            "feature": reqs["feature"], "source": reqs.get("source", ""), "generated_by": "eval_gen.py", "cases": cases}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval_gen.py")
    ap.add_argument("requirements")
    ap.add_argument("--out", required=True)
    ap.add_argument("--suite")
    a = ap.parse_args(argv)
    reqs = yaml.safe_load(Path(a.requirements).read_text(encoding="utf-8"))
    if a.suite:
        reqs["suite"] = a.suite
    suite = generate(reqs)
    Path(a.out).write_text(yaml.safe_dump(suite, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"{len(suite['cases'])} draft cases -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
