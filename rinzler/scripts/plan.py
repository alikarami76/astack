#!/usr/bin/env python3
"""From a feature or a PRD to numbered requirements, a risk-based test plan, charters and a regression selection.

  plan.py features [--grep discount]                               list features in the product map
  plan.py extract --feature "Flash discount form" --out requirements.yaml
  plan.py extract --prd prd.md --feature "My feature" --out requirements.yaml
  plan.py plan requirements.yaml --out test-plan.md [--charters charters.md]
  plan.py regress --changed changed.txt --suites a.yaml b.yaml [--map-dir <scouts>]

The scripts do the mechanical part: numbering, a first risk score from the wording, the plan skeleton and traceable
ids. The rinzler-planner agent then corrects the scores with a written reason, adds what the text leaves out, and turns
ambiguities into questions for the owner. Risk = likelihood x impact, each 1-5 (docs/research.md, section 1).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rpaths  # noqa: E402

IMPACT_WORDS = [
    (5, r"price|discount|commission|payment|pay|wallet|credit|invoice|refund|bid|rial|toman|fee|penalt|settle|money|cost"),
    (4, r"eligib|limit|at least|at most|up to|must|cannot|only|block|validat|quota|stock|quantity"),
    (3, r"status|state|transition|result|winner|loser|announce|deadline|hours?|days?"),
    (2, r"show|display|label|badge|message|hint|tooltip|text"),
]
LIKELIHOOD_WORDS = [r"\d", r"round|end with|multiple|percent|%", r"before|after|within|hours?|days?|same day",
                    r"or|and|unless|except|if|when", r"change|later|auto|automatic|raise", r"wallet|credit|external|gateway"]
SFDIPOT = {"Structure": r"step|page|screen|wizard|component", "Function": r"calculat|compute|raise|auto|validat",
           "Data": r"price|percent|quantity|number|amount|digit|excel", "Interfaces": r"api|wallet|gateway|excel|payment",
           "Platform": r"mobile|desktop|browser|module", "Operations": r"seller|bulk|many|all variants",
           "Time": r"hour|day|before|after|slot|deadline|period"}


def score(text: str) -> dict:
    t = text.lower()
    impact = next((v for v, pat in IMPACT_WORDS if re.search(pat, t)), 2)
    likelihood = min(5, 1 + sum(bool(re.search(p, t)) for p in LIKELIHOOD_WORDS))
    s = impact * likelihood
    return {"impact": impact, "likelihood": likelihood, "score": s,
            "level": "high" if s >= 15 else "medium" if s >= 8 else "low",
            "rationale": "first guess from the wording; rinzler-planner must confirm or correct it"}


def load_map(map_dir=None) -> list[tuple[str, dict]]:
    d = Path(map_dir or rpaths.product_map())
    out = []
    for f in sorted(d.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        out += [(f.name, x) for x in data.get("features", [])]
    return out


def _flows_for(feature: dict, map_dir=None) -> list[dict]:
    d = Path(map_dir or rpaths.product_map())
    screens = set(feature.get("screens", []))
    flows = []
    for f in sorted(d.glob("*.json")):
        for fl in json.loads(f.read_text(encoding="utf-8")).get("flows", []):
            if screens & set(fl.get("screens", [])):
                flows.append(fl)
    return flows


def extract_feature(name: str, map_dir=None) -> dict:
    hits = [(f, x) for f, x in load_map(map_dir) if x.get("name", "").lower() == name.lower()]
    if not hits:
        raise SystemExit(f"no feature named {name!r}; try `plan.py features --grep <word>`")
    src, ft = hits[0]
    reqs = []
    for i, rule in enumerate(ft.get("business_rules", []), 1):
        reqs.append({"id": f"R{i}", "text": rule, "kind": "business-rule",
                     "source": f"product map {src} / {ft['name']} / business_rules[{i - 1}]", "risk": score(rule)})
    n = len(reqs)
    for fl in _flows_for(ft, map_dir):
        for e in fl.get("edge_cases", []):
            n += 1
            reqs.append({"id": f"R{n}", "text": e, "kind": "edge-case",
                         "source": f"product map flow '{fl['name']}' / edge_cases", "risk": score(e)})
    return {"feature": ft["name"], "feature_fa": ft.get("name_fa", ""), "description": ft.get("description", ""),
            "screens": ft.get("screens", []), "apis": ft.get("apis", []), "evidence": ft.get("evidence", []),
            "source": f"{rpaths.product_map()}/{src} (tier: {ft.get('tier', '?')})", "requirements": reqs,
            "open_questions": []}


def extract_prd(path: str, feature: str) -> dict:
    reqs, n = [], 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:[-*]|\d+[.)])\s+(?:\*\*)?((?:[A-Z]{1,5}-?)?\d+(?:\.\d+)?)?(?:\*\*)?[:.)\s-]*\s*(.+)", line)
        if not m or len(m.group(2)) < 12:
            continue
        n += 1
        rid = m.group(1) if m.group(1) and not m.group(1).isdigit() else f"R{n}"
        reqs.append({"id": rid, "text": m.group(2).strip(), "kind": "prd", "source": f"{path}:{line.strip()[:40]}",
                     "risk": score(m.group(2))})
    return {"feature": feature, "source": path, "requirements": reqs, "open_questions": []}


def _approach(level: str) -> str:
    return {"high": "unit or data simulation of the rule + API check + one E2E path + exploratory charter",
            "medium": "API or component check + E2E where the UI holds the rule",
            "low": "exploratory checklist item"}[level]


def sfdipot(text: str) -> list[str]:
    return [k for k, p in SFDIPOT.items() if re.search(p, text.lower())] or ["Function"]


def render_plan(reqs: dict) -> str:
    rs = sorted(reqs["requirements"], key=lambda r: -r["risk"]["score"])
    lines = [f"# Test plan: {reqs['feature']}" + (f" ({reqs['feature_fa']})" if reqs.get("feature_fa") else ""), "",
             "Status: draft. Owner review needed before execution.", "",
             "## What is being tested and why", "", reqs.get("description", "(add the PRD summary)"), "",
             f"Source of requirements: {reqs['source']}.", "",
             "## Scope", "", "In scope: " + ", ".join(f"`{s}`" for s in reqs.get("screens", [])) + "." if reqs.get("screens")
             else "In scope: (list the screens and APIs)", "",
             "APIs touched: " + ", ".join(f"`{a}`" for a in reqs.get("apis", [])) + "." if reqs.get("apis") else "", "",
             "Out of scope: production data and production endpoints; load tests on shared environments without the "
             "owner's approval.", "",
             "## Risks, highest first", "", "| Req | Requirement | Impact | Likelihood | Risk | Approach |",
             "|---|---|---|---|---|---|"]
    for r in rs:
        k = r["risk"]
        lines.append(f"| {r['id']} | {r['text'][:110]} | {k['impact']} | {k['likelihood']} | {k['score']} {k['level']} | "
                     f"{_approach(k['level'])} |")
    lines += ["", "Risk scores are first guesses from the wording. Each one the planner changes carries a reason here.", "",
              "## Approach by level", "",
              "1. Rules and money: data simulation of each calculation against an oracle written from the requirement "
              "(`datasim.py`), plus functional evals (`evals.py`).",
              "2. API: contract and status checks against the local build's API proxy (demo), read-only unless the "
              "case needs a write on demo.",
              "3. Browser: Playwright on Rinzler's local web-app build with stubbed or demo data; evidence "
              "(screenshot, console, network, trace) on every failure.",
              "4. Exploration: time-boxed charters for the high-risk areas (below).",
              "5. Standing checks on every touched page: right-to-left layout, Persian digits, axe WCAG 2.2 AA, "
              "loading/empty/error states, mobile width.", "",
              "## Environments", "", "- Local web-app build in `~/rinzler-work/web-app` "
              "(dev proxy to `demo-api.example.com`). The owner logs in by hand when a real session is needed.",
              "- Demo or stage for API checks. Never production.", "",
              "## Entry and exit criteria", "",
              "- Entry: requirements numbered and reviewed; open questions answered or recorded as assumptions; build "
              "health check green.",
              "- Exit: every high-risk requirement has at least one passing or failing executed case (not 'not run'); "
              "no open S1/S2 bug without an owner decision; traceability table attached.", "",
              "## Traceability", "", "Generated by `evals.py trace` after the run; each requirement lists its cases "
              "and their latest results.", "",
              "## Open questions for the owner", ""]
    lines += [f"- {q}" for q in reqs.get("open_questions") or ["(planner adds ambiguities here)"]]
    return "\n".join(lines) + "\n"


def render_charters(reqs: dict, minutes: int = 45) -> str:
    high = [r for r in reqs["requirements"] if r["risk"]["level"] == "high"]
    lines = [f"# Exploratory charters: {reqs['feature']}", "",
             "Template (Hendrickson): Explore <target> with <resources> to discover <information>. "
             f"Time box {minutes} minutes each; debrief notes go in the run folder.", ""]
    for i, r in enumerate(high, 1):
        lines += [f"## C{i} ({r['id']})", "",
                  f"Explore {r['text'][:100]} with boundary values, Persian and Latin digits, slow network and a second "
                  f"tab, to discover where the rule is not enforced or is shown wrongly.", "",
                  f"- Areas (SFDIPOT): {', '.join(sfdipot(r['text']))}",
                  "- Oracles (HICCUPPS): the requirement text (Claims), the same rule on other screens (Product), "
                  "the user's expectation (User desires), your company's other promotion flows (Comparable products).",
                  f"- Time box: {minutes} minutes", "- Debrief: what was covered, bugs, questions, what is left.", ""]
    return "\n".join(lines) + "\n"


def regress(changed: list[str], suites: list[dict], map_dir=None) -> dict:
    """Pick the features whose code the change touches (product map evidence paths), then their cases."""
    changed = [c.strip() for c in changed if c.strip()]
    touched = {}
    for _, ft in load_map(map_dir):
        dirs = {re.sub(r":\d.*$", "", e).rsplit("/", 1)[0] for e in ft.get("evidence", [])}
        hit = [c for c in changed if any(c.startswith(d) or d.startswith(c.rsplit("/", 1)[0]) for d in dirs if d)]
        if hit:
            touched[ft["name"]] = hit
    picked, always = [], []
    for s in suites:
        for c in s["cases"]:
            if "smoke" in (c.get("tags") or []):
                always.append(c["id"])
            elif s.get("feature") in touched and c.get("status") != "retired":
                picked.append(c["id"])
    return {"touched_features": touched, "selected_cases": picked, "smoke_cases": always,
            "note": "plus every case tagged smoke; add cases for features the change could reach indirectly"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("features")
    f.add_argument("--grep", default="")
    f.add_argument("--map-dir")
    e = sub.add_parser("extract")
    e.add_argument("--feature", required=True)
    e.add_argument("--prd")
    e.add_argument("--map-dir")
    e.add_argument("--out", required=True)
    p = sub.add_parser("plan")
    p.add_argument("requirements")
    p.add_argument("--out", required=True)
    p.add_argument("--charters")
    r = sub.add_parser("regress")
    r.add_argument("--changed", required=True, help="file with one changed path per line (git diff --name-only)")
    r.add_argument("--suites", nargs="+", required=True)
    r.add_argument("--map-dir")
    a = ap.parse_args(argv)
    if a.cmd == "features":
        for src, ft in load_map(a.map_dir):
            if a.grep.lower() in json.dumps(ft, ensure_ascii=False).lower():
                print(f"{src:22} {ft['name']}  ({len(ft.get('business_rules', []))} rules)")
    elif a.cmd == "extract":
        reqs = extract_prd(a.prd, a.feature) if a.prd else extract_feature(a.feature, a.map_dir)
        Path(a.out).write_text(yaml.safe_dump(reqs, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        print(f"{len(reqs['requirements'])} requirements -> {a.out}")
    elif a.cmd == "plan":
        reqs = yaml.safe_load(Path(a.requirements).read_text(encoding="utf-8"))
        Path(a.out).write_text(render_plan(reqs), encoding="utf-8")
        if a.charters:
            Path(a.charters).write_text(render_charters(reqs), encoding="utf-8")
        print(a.out)
    elif a.cmd == "regress":
        import evals
        res = regress(Path(a.changed).read_text().splitlines(), [evals.load(s) for s in a.suites], a.map_dir)
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
