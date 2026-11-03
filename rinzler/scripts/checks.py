"""Code-graded checks shared by the functional eval runner (evals.py) and the AI eval runner (ai_eval.py).

A check is a dict: {"type": "<name>", "on": "<dotted path in the observation>", ...params}. The observation is what the
system under test produced, for example {"output": {...}, "status": 200, "latency_ms": 120, "text": "..."}. "on"
defaults to "output". Every check returns (passed, detail). Code checks come first; a model judge is used only for what
code cannot decide (docs/eval-format.md).
"""
from __future__ import annotations

import math
import re
from typing import Any

MISSING = object()
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
# The assistant declining in its own voice ("I can't"), not an answer that explains a rule ("is not allowed").
REFUSAL_PATTERNS = [
    r"\bI can(?:no|')t (?:help|share|do|provide|give)", r"\bI'm not able to\b", r"\bI am unable to\b", r"\bI won't\b",
    r"نمی[\u200c ]?توانم", r"قادر (?:به .{0,30})?نیستم", r"اجازه[\u200c ]?ی? .{0,30}ندارم", r"امکان .{0,30}برای من وجود ندارد",
    r"(?:این|چنین) (?:اطلاعاتی?|کاری) را (?:نمی[\u200c ]?دهم|انجام نمی[\u200c ]?دهم)",
]


def get_path(obj: Any, path: str | None) -> Any:
    if path in (None, "", "."):
        return obj
    cur = obj
    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return MISSING
    return cur


def to_en_digits(s: str) -> str:
    return s.translate(str.maketrans(PERSIAN_DIGITS + ARABIC_DIGITS, "0123456789" * 2))


def _num(v):
    if isinstance(v, bool):
        raise TypeError("boolean is not a number")
    if isinstance(v, (int, float)):
        return v
    s = to_en_digits(str(v)).replace(",", "").replace("٬", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        raise ValueError(f"no number in {v!r}")
    return float(m.group(0))


def _text(v) -> str:
    return v if isinstance(v, str) else ("" if v is None or v is MISSING else str(v))


def is_refusal(text: str) -> bool:
    return any(re.search(p, text, re.I) for p in REFUSAL_PATTERNS)


def _check(c: dict, v: Any) -> tuple[bool, str]:
    t = c["type"]
    if t == "exists":
        return v is not MISSING, "present" if v is not MISSING else "missing"
    if v is MISSING:
        return False, f"{c.get('on', 'output')} is missing"
    if t == "equals":
        return v == c["value"], f"got {v!r}, want {c['value']!r}"
    if t == "not_equals":
        return v != c["value"], f"got {v!r}, must differ from {c['value']!r}"
    if t == "in":
        return v in c["values"], f"got {v!r}, want one of {c['values']!r}"
    if t == "range":
        x = _num(v)
        lo, hi = c.get("min", -math.inf), c.get("max", math.inf)
        ok = (lo <= x <= hi) if c.get("inclusive", True) else (lo < x < hi)
        return ok, f"got {x}, want {'[' if c.get('inclusive', True) else '('}{lo}, {hi}{']' if c.get('inclusive', True) else ')'}"
    if t == "approx":
        x, want = _num(v), _num(c["value"])
        tol = c.get("tol", 0) if "rel" not in c else abs(want) * c["rel"]
        return abs(x - want) <= tol, f"got {x}, want {want} ± {tol}"
    if t == "regex":
        return re.search(c["pattern"], _text(v), re.S) is not None, f"pattern {c['pattern']!r}"
    if t == "not_regex":
        return re.search(c["pattern"], _text(v), re.S) is None, f"pattern {c['pattern']!r} must not match"
    if t in ("contains", "not_contains"):
        hit = (c["value"] in v) if isinstance(v, (list, tuple, dict)) else (str(c["value"]) in _text(v))
        return hit == (t == "contains"), f"{'missing' if t == 'contains' else 'found'} {c['value']!r}"
    if t == "contains_all":
        missing = [x for x in c["values"] if str(x) not in _text(v) and x not in (v if isinstance(v, list) else [])]
        return not missing, f"missing {missing!r}" if missing else "all present"
    if t == "contains_any":
        hit = [x for x in c["values"] if str(x) in _text(v)]
        return bool(hit), f"found {hit!r}" if hit else f"none of {c['values']!r}"
    if t == "type":
        names = {"int": int, "float": (int, float), "str": str, "list": list, "dict": dict, "bool": bool}
        if c["name"] == "null":
            return v is None, f"got {type(v).__name__}"
        ok = isinstance(v, names[c["name"]]) and not (c["name"] in ("int", "float") and isinstance(v, bool))
        return ok, f"got {type(v).__name__}, want {c['name']}"
    if t == "length":
        n = len(v)
        return c.get("min", 0) <= n <= c.get("max", math.inf), f"length {n}"
    if t == "max_value":
        return _num(v) <= c["value"], f"got {v}, max {c['value']}"
    if t == "digits":
        s = _text(v)
        latin, persian = bool(re.search(r"[0-9]", s)), any(ch in PERSIAN_DIGITS for ch in s)
        if c["script"] == "persian":
            return not latin, "Latin digits found" if latin else "Persian digits only"
        return not persian, "Persian digits found" if persian else "Latin digits only"
    if t == "refusal":
        r = is_refusal(_text(v))
        return r == c.get("expected", True), f"refusal detected: {r}"
    raise ValueError(f"unknown check type {t!r}")


CHECK_TYPES = ("exists", "equals", "not_equals", "in", "range", "approx", "regex", "not_regex", "contains",
               "not_contains", "contains_all", "contains_any", "type", "length", "max_value", "digits", "refusal")


def run_check(check: dict, observation: dict) -> tuple[bool, str]:
    v = get_path(observation, check.get("on", "output"))
    try:
        return _check(check, v)
    except (TypeError, ValueError, KeyError) as e:
        return False, f"check error: {type(e).__name__}: {e}"


def validate_check(check: dict) -> list[str]:
    errs = []
    t = check.get("type")
    if t not in CHECK_TYPES:
        return [f"unknown check type {t!r}"]
    need = {"equals": ["value"], "not_equals": ["value"], "in": ["values"], "approx": ["value"], "regex": ["pattern"],
            "not_regex": ["pattern"], "contains": ["value"], "not_contains": ["value"], "contains_all": ["values"],
            "contains_any": ["values"], "type": ["name"], "max_value": ["value"], "digits": ["script"]}.get(t, [])
    errs += [f"{t} needs {k}" for k in need if k not in check]
    if t == "range" and "min" not in check and "max" not in check:
        errs.append("range needs min or max")
    return errs
