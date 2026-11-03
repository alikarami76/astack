#!/usr/bin/env python3
"""Render convention rows into the human-readable rulebook kb/conventions/<repo>.md.
  render_conventions.py            # every repo that has rules
  render_conventions.py --repo api-app
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

TIER_ORDER = {t: i for i, t in enumerate(store.TIERS)}
TIER_TEXT = {"enforced": "Enforced — tooling blocks violations", "dominant": "Dominant — follow it",
             "transitional": "Transitional — new style in new code, leave old code unless the task touches it",
             "stated_only": "Stated only — written down but not visible in the code"}


def _flat(v) -> str:
    return " ".join(str(v if v is not None else "").split())


def _code(v) -> str:
    return "`" + _flat(v).replace("`", "'") + "`"


def _items(raw) -> list[dict]:
    """Parse a JSON list column; anything malformed yields fewer items, never an error."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _loc(i: dict) -> str | None:
    if not i.get("file"):
        return None
    return f"{i['file']}:{i['line']}" if i.get("line") not in (None, "") else str(i["file"])


def _refuted_this_run(r: dict) -> bool:
    """The rule carries a refuted:<run>:<round> tag from the run that last wrote it (refuted once, not yet re-verified)."""
    try:
        tags = json.loads(r.get("tags") or "[]")
    except ValueError:
        return False
    run = r.get("run_id") or ""
    return bool(run) and isinstance(tags, list) and any(isinstance(t, str) and t.startswith(f"refuted:{run}:") for t in tags)


def render(conn, repo: str) -> str:
    rows = [dict(r) for r in conn.execute("SELECT * FROM conventions WHERE repo = ? AND (superseded_by IS NULL OR superseded_by = '')", (repo,))]
    for r in rows:
        r["area"] = _flat(r.get("area")) or "general"
        r["rule"] = _flat(r.get("rule")) or "(unnamed rule)"
        r["tier"] = r.get("tier") if r.get("tier") in TIER_ORDER else "stated_only"
    shas = sorted({r["checked_at_sha"][:7] for r in rows if r.get("checked_at_sha")})
    out = [f"# {repo} conventions", "",
           f"Generated from the quorra KB (`kb/export/convention.jsonl`); do not edit by hand. "
           f"Rules checked at {', '.join(f'`{s}`' for s in shas) or 'no commit'}.", ""]
    if repo != "shared-rules":
        out += ["Rules shared by every framework repo live in [shared-rules.md](shared-rules.md).", ""]
    for r in rows:
        r["scope"] = _flat(r.get("scope"))
    multi = len({r["scope"] for r in rows}) > 1  # a large repo researched per module: keep the modules apart
    for scope, area in sorted({(r["scope"], r["area"]) for r in rows}):
        out += [f"## {area} — {scope or 'whole repo'}" if multi else f"## {area}", ""]
        for r in sorted((r for r in rows if r["area"] == area and r["scope"] == scope), key=lambda r: (TIER_ORDER[r["tier"]], r["rule"])):
            flag = "" if r.get("verified") else (" _(refuted once)_" if _refuted_this_run(r) else " _(unverified)_")
            out += [f"### {r['rule']}", "", f"**{TIER_TEXT[r['tier']]}.** Source: {_flat(r.get('source'))}; checked at `{(r.get('checked_at_sha') or '')[:7]}`.{flag}", ""]
            if r.get("rationale"):
                out += [" ".join(_flat(line) for line in str(r["rationale"]).splitlines() if line.strip()), ""]
            for ex in _items(r.get("examples")):
                loc = _loc(ex)
                if loc:
                    snip = _flat(ex.get("snippet"))[:100]
                    out.append(f"- {_code(loc)}" + (f" — {_code(snip)}" if snip else ""))
            cex = [x for x in (_loc(c) for c in _items(r.get("counter_examples"))) if x]
            if cex:
                out.append("- Counter-examples: " + ", ".join(_code(c) for c in cex))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="render_conventions.py")
    ap.add_argument("--repo")
    a = ap.parse_args(argv)
    if a.repo is not None and (not a.repo or "/" in a.repo or "\\" in a.repo or ".." in a.repo):
        ap.error(f"invalid --repo {a.repo!r}: a repo name, not a path")
    c = store.connect()
    repos = [a.repo] if a.repo else [r[0] for r in c.execute("SELECT DISTINCT repo FROM conventions")]
    d = Path(__file__).resolve().parent / "conventions"
    d.mkdir(exist_ok=True)
    for repo in repos:
        (d / f"{repo}.md").write_text(render(c, repo), encoding="utf-8")
        print(d / f"{repo}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
