"""Shared result format for the clu causal toolkit. Every command returns one dict built by `result()`."""
from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA_VERSION = 1


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) else f


def check(name: str, status: str, value=None, note: str = "") -> dict:
    """One diagnostic. status is pass, warn or fail."""
    assert status in ("pass", "warn", "fail"), status
    return {"name": name, "status": status, "value": _num(value) if not isinstance(value, (list, dict, str)) else value,
            "note": note}


def estimate(value, ci_low=None, ci_high=None, p_value=None, se=None, unit: str = "") -> dict:
    return {"value": _num(value), "ci_low": _num(ci_low), "ci_high": _num(ci_high), "p_value": _num(p_value),
            "se": _num(se), "unit": unit}


def verdict(est: dict, mde_abs: float | None = None) -> str:
    """effect detected | no effect larger than <mde> | inconclusive. 'No effect' is only allowed when powered."""
    lo, hi = est.get("ci_low"), est.get("ci_high")
    if lo is None or hi is None:
        return "inconclusive"
    if lo > 0 or hi < 0:
        return "effect detected"
    if mde_abs is not None and max(abs(lo), abs(hi)) < abs(mde_abs):
        return f"no effect larger than {abs(mde_abs):g}"
    return "inconclusive"


def result(command: str, est: dict | None, estimand: str, diagnostics: list[dict], warnings: list[str],
           extra: dict | None = None, mde_abs: float | None = None, status: str = "ok") -> dict:
    failed = [d["name"] for d in diagnostics if d["status"] == "fail"]
    out = {"schema_version": SCHEMA_VERSION, "command": command, "status": "diagnostic_failed" if failed and status == "ok" else status,
           "estimate": est, "estimand": estimand, "verdict": verdict(est, mde_abs) if est else None,
           "diagnostics": diagnostics, "warnings": list(warnings)}
    if failed:
        out["warnings"].append(f"Failed diagnostics: {', '.join(failed)}. Report the result with this failure; do not call it an effect without a caveat.")
    if extra:
        out["extra"] = extra
    return out


def unavailable(command: str, why: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "command": command, "status": "unavailable", "estimate": None,
            "estimand": "", "verdict": None, "diagnostics": [], "warnings": [why]}


def load_table(path: str):
    import pandas as pd
    p = Path(path)
    return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)


def write(res: dict, out: str | None) -> None:
    text = json.dumps(res, ensure_ascii=False, indent=2, default=lambda o: _num(o) if not hasattr(o, "tolist") else o.tolist())
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n")
    e = res.get("estimate") or {}
    print(f"{res['command']}: status={res['status']} verdict={res.get('verdict')} "
          f"estimate={e.get('value')} ci=[{e.get('ci_low')}, {e.get('ci_high')}] p={e.get('p_value')}"
          + (f" -> {out}" if out else ""))
    for w in res.get("warnings", []):
        print(f"  warning: {w}")
