#!/usr/bin/env python3
"""Schemas for what clu's helpers hand back, and the mechanical verification verdict.

Each helper writes ONE JSON object to a file in the run folder and replies with its path. The lead
records it with `run_record.py ingest <run> <file>`, which calls `check()` and, for the verifier,
`verdict()`. Stdlib only; no jsonschema dependency.

  kind            written by            file
  objections      clu-challenger        <run>/helpers/challenger-<mode>.json
  runner_result   clu-data-runner       <run>/helpers/runner-<name>.json
  verification    clu-verifier          <run>/verify/result.json   (claims come from <run>/verify/claims.json)
  edit_review     clu-editor            <run>/helpers/editor-<n>.json
  curation        clu-curator           <run>/helpers/curator.json
  causal_plan     clu-methodologist     <run>/helpers/methodologist.json
"""
from __future__ import annotations

import json
from pathlib import Path

SUSTAIN_PCT = 1.0   # |diff| <= 1% is sustained
EXPLAIN_PCT = 5.0   # 1-5% is sustained only with an explained gap; above 5% is refuted

AGENT = {"objections": "clu-challenger", "runner_result": "clu-data-runner", "verification": "clu-verifier",
         "edit_review": "clu-editor", "curation": "clu-curator", "causal_plan": "clu-methodologist"}

# field -> (type or tuple of types, required, allowed values or None)
_STR, _NUM, _LIST, _BOOL, _DICT = str, (int, float), list, bool, dict
_NULLABLE_STR = (str, type(None))
SCHEMAS: dict[str, dict] = {
    "objections": {
        "mode": (_STR, True, ("contract", "conclusions", "causal_plan")),
        "objections": (_LIST, True, None),
        "questions_for_user": (_LIST, False, None),
        "ok": (_LIST, False, None),
    },
    "runner_result": {
        "name": (_STR, True, None), "source": (_STR, True, None), "sql_file": ((str, list), True, None),
        "result_file": (_NULLABLE_STR, True, None), "rows": ((int, type(None)), True, None),
        "capped": ((bool, type(None)), True, None), "runtime_s": ((int, float, type(None)), True, None),
        "checks": (_DICT, False, None), "issues": (_NULLABLE_STR, False, None),
        "status": (_STR, False, ("ok", "blocked")),
    },
    "verification": {
        "numbers": (_LIST, True, None),
        "reconciliation": (_LIST, False, None),
        "spot_checks": (_LIST, False, None),
    },
    "edit_review": {
        "file": (_STR, True, None), "verdict": (_STR, True, ("ready", "fix")), "fixes": (_LIST, True, None),
    },
    "curation": {
        "run_id": (_STR, True, None), "kb_writes": (_LIST, True, None), "lessons": (int, True, None),
        "proposals": (_LIST, True, None), "patterns_noted": (_LIST, False, None), "om_drafts": (_LIST, False, None),
    },
    "causal_plan": {
        "plan_file": (_STR, True, None), "estimand": (_STR, True, None),
        "estimand_type": (_STR, True, ("ITT", "ATT", "ATE", "LATE", "threshold", "other")),
        "recommended": (_STR, True, None), "candidates": (_LIST, True, None),
        "questions_for_user": (_LIST, False, None),
    },
}
# required keys of list items
ITEMS = {
    ("objections", "objections"): {"severity": ("blocker", "major", "minor"), "issue": None, "fix": None},
    ("objections", "questions_for_user"): {"question": None},
    ("verification", "numbers"): {"name": None, "route": None},
    ("verification", "reconciliation"): {"check": None, "status": ("pass", "fail")},
    ("verification", "spot_checks"): {"id": None, "status": ("pass", "fail")},
    ("edit_review", "fixes"): {"location": None, "problem": None, "change": None},
    ("causal_plan", "candidates"): {"design": None, "assumption": None, "command": None},
    ("causal_plan", "questions_for_user"): {"question": None},
}


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def check(obj) -> list[str]:
    """Return the problems with a helper output; an empty list means it is valid."""
    if not isinstance(obj, dict):
        return ["the file must hold one JSON object"]
    kind = obj.get("kind")
    if kind not in SCHEMAS:
        return [f"kind must be one of {sorted(SCHEMAS)}, got {kind!r}"]
    problems = []
    for field, (typ, required, allowed) in SCHEMAS[kind].items():
        if field not in obj:
            if required:
                problems.append(f"{kind}: missing {field}")
            continue
        v = obj[field]
        allowed_types = typ if isinstance(typ, tuple) else (typ,)
        if (isinstance(v, bool) and bool not in allowed_types) or not isinstance(v, typ):  # True is not a row count
            problems.append(f"{kind}.{field}: wrong type {type(v).__name__}")
            continue
        if allowed and v not in allowed:
            problems.append(f"{kind}.{field}: {v!r} not in {allowed}")
        if isinstance(v, list) and (kind, field) in ITEMS:
            for i, it in enumerate(v):
                if not isinstance(it, dict):
                    problems.append(f"{kind}.{field}[{i}]: must be an object")
                    continue
                for k, vals in ITEMS[(kind, field)].items():
                    if it.get(k) in (None, ""):
                        problems.append(f"{kind}.{field}[{i}]: missing {k}")
                    elif vals and it[k] not in vals:
                        problems.append(f"{kind}.{field}[{i}].{k}: {it[k]!r} not in {vals}")
    return problems


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def verdict(result: dict, claims: dict) -> dict:
    """Apply the tolerance rule in code. Claimed values come from the lead's claims.json, never the verifier.

    Per number: diff <= 1% sustained; 1-5% sustained only with `gap_explained`; above 5% refuted;
    missing recomputation is not_recomputed. Overall: not_run when nothing could be recomputed (that is
    "not checked", never "wrong"); sustained when every number is sustained and no reconciliation or spot
    check failed; refuted when at least one number is refuted and none is sustained; otherwise partial.
    """
    claimed = {c["name"]: c for c in claims.get("numbers", [])}
    got = {n["name"]: n for n in result.get("numbers", [])}
    rows = []
    for name, c in claimed.items():
        n = got.get(name, {})
        cv, rv = _num(c.get("value")), _num(n.get("recomputed"))
        if rv is None or cv is None:
            rows.append({"name": name, "claimed": c.get("value"), "recomputed": n.get("recomputed"),
                         "diff_pct": None, "status": "not_recomputed", "route": n.get("route")})
            continue
        diff = 0.0 if cv == rv else (abs(rv - cv) / abs(cv) * 100 if cv else float("inf"))
        if diff <= SUSTAIN_PCT:
            st = "sustained"
        elif diff <= EXPLAIN_PCT and (n.get("gap_explained") or "").strip():
            st = "sustained"
        else:
            st = "refuted"
        rows.append({"name": name, "claimed": cv, "recomputed": rv,
                     "diff_pct": None if diff == float("inf") else round(diff, 3), "status": st,
                     "route": n.get("route"), "gap_explained": n.get("gap_explained")})
    extra = sorted(set(got) - set(claimed))
    failed = [x for x in result.get("reconciliation", []) + result.get("spot_checks", []) if x.get("status") == "fail"]
    sustained = [r for r in rows if r["status"] == "sustained"]
    refuted = [r for r in rows if r["status"] == "refuted"]
    reason = None
    if not rows or all(r["status"] == "not_recomputed" for r in rows):
        overall = "not_run"
        reason = ("no claimed numbers in claims.json" if not rows else
                  "the verifier could not recompute any number: " + "; ".join(
                      f"{r['name']} ({r.get('route') or 'no route'})" for r in rows))
    elif len(sustained) == len(rows) and not failed:
        overall = "sustained"
    elif refuted and not sustained:
        overall = "refuted"
    else:
        overall = "partial"
    return {"result": overall, "reason": reason, "numbers": rows, "failed_checks": failed,
            "discrepancies": [r for r in rows if r["status"] != "sustained"],
            "unclaimed_numbers": extra}


def summary(obj: dict, extra: dict | None = None) -> str:
    """A compact plain-text rendering for the lead (what it needs to act on, nothing more)."""
    k = obj["kind"]
    if k == "objections":
        out = [f"OBJECTIONS ({obj['mode']}): " + ", ".join(
            f"{s}={sum(1 for o in obj['objections'] if o['severity'] == s)}" for s in ("blocker", "major", "minor"))]
        out += [f"{i}. [{o['severity']}] {o['issue']} — fix: {o['fix']}" for i, o in enumerate(obj["objections"], 1)]
        out += [f"Q: {q['question']}" for q in obj.get("questions_for_user", [])]
        return "\n".join(out)
    if k == "runner_result":
        return (f"RUNNER {obj['name']}: {obj.get('status', 'ok')} rows={obj['rows']} capped={obj['capped']} "
                f"runtime_s={obj['runtime_s']} sql={obj['sql_file']} result={obj['result_file']}"
                + (f"\nissues: {obj['issues']}" if obj.get("issues") else ""))
    if k == "verification" and extra:
        out = [f"VERIFICATION: {extra['result']}"]
        out += [f"- {r['name']}: claimed {r['claimed']} | recomputed {r['recomputed']} | diff {r['diff_pct']}% | {r['status']}"
                for r in extra["numbers"]]
        out += [f"- FAILED {x.get('check') or x.get('id')}: {x.get('note', '')}" for x in extra["failed_checks"]]
        if extra["unclaimed_numbers"]:
            out.append(f"- ignored (not in claims.json): {', '.join(extra['unclaimed_numbers'])}")
        return "\n".join(out)
    if k == "edit_review":
        return "\n".join([f"EDIT_REVIEW {obj['file']}: {obj['verdict']}"] +
                         [f"- [{f['location']}] {f['problem']} -> {f['change']}" for f in obj["fixes"]])
    if k == "curation":
        return (f"CURATION {obj['run_id']}: kb_writes={len(obj['kb_writes'])} lessons={obj['lessons']} "
                f"proposals={', '.join(obj['proposals']) or 'none'}"
                + (f" om_drafts={len(obj['om_drafts'])}" if obj.get("om_drafts") else ""))
    if k == "causal_plan":
        return "\n".join([f"CAUSAL_PLAN: recommended {obj['recommended']} ({obj['estimand_type']}); plan {obj['plan_file']}",
                          f"estimand: {obj['estimand']}"] + [f"- {c['design']}" for c in obj["candidates"]])
    return k
