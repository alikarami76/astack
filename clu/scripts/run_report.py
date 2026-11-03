#!/usr/bin/env python3
"""The analysis record of a clu run: methods.md (every step, data source, query and aggregation) and
reproduce.ipynb (one standalone Jupyter notebook that explains and recreates the headline numbers).

  run_report.py scaffold <run>                         # write an analysis_steps.json skeleton to fill in
  run_report.py build <run>                            # methods.md + reproduce.ipynb (not executed)
  run_report.py check <run> [--cached] [--timeout S]   # rebuild the notebook, execute it, save its outputs,
                                                       # compare its numbers with the reported ones, record it

The lead writes <run>/analysis_steps.json: the narrative, every query, and the exact aggregation code.
Everything else is generated from it, so no query or number is left out and nobody edits notebook JSON by
hand. The notebook alternates explanation cells (what the next code does and why) with code cells. It
depends only on pandas and the read-only data tools you configure (see references/source-routing.md), never on clu's code or the run
folder. `check` executes it with nbclient in clu's .venv-causal and stores the outputs in the notebook, so
it opens with every table and result already shown. <run> is a run folder path or a bare run id.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import run_record  # noqa: E402

NOTEBOOK = "reproduce.ipynb"
TOLERANCE_PCT = 1.0
VENV_PY = REPO / ".venv-causal" / "bin" / "python"
SOURCE_KINDS = ("mysql", "metabase", "clickhouse", "ods", "file", "growthbook", "cstat")
TEMPLATE_MARK = "<fill in"
STEPS_TEMPLATE = {
    "question": "", "decision": "<fill in: what the answer is for>",
    "definitions": [{"term": "<fill in>", "meaning": "<fill in: numerator, denominator, population, window>"}],
    "steps": [{"step": "contract", "what": "<fill in>", "why": "<fill in>"}],
    "data": [{"source": "mysql:warehouse", "table": "<fill in>", "grain": "", "window": "", "filters": "", "rows": None}],
    "queries": [{"name": "q1", "source": "mysql:warehouse", "sql_file": "queries/q1.sql",
                 "purpose": "<fill in: one line>", "explain": "<fill in: what the query does and why, in plain words>",
                 "output": "results/q1.json"}],
    "aggregations": [{"name": "a1", "inputs": ["q1"], "how": "<fill in: one line>",
                      "explain": "<fill in: the logic in plain words>",
                      "code": ["# <fill in: the exact pandas code; the query results are DataFrames named after each query>"]}],
    "headline": [{"name": "<fill in>", "value": None, "unit": "<fill in>", "expr": "<fill in: python expression>"}],
    "caveats": [],
}


def _steps(d: Path) -> dict:
    p = d / "analysis_steps.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing: run `run_report.py scaffold {d}` and fill it in")
    return json.loads(p.read_text())


def _sql_files(q: dict) -> list[str]:
    f = q.get("sql_file") or []
    return [f] if isinstance(f, str) else list(f)


def _code(item: dict, d: Path) -> str:
    """An item's code: `code` as a string or a list of lines, or `code_file` relative to the run."""
    if item.get("code_file"):
        p = d / item["code_file"]
        return p.read_text().rstrip() if p.exists() else f"raise FileNotFoundError({str(p)!r})"
    c = item.get("code") or ""
    return ("\n".join(c) if isinstance(c, list) else str(c)).rstrip()


def _var(name: str) -> str:
    v = re.sub(r"\W", "_", name).lower()
    return v if v and not v[0].isdigit() else f"q_{v}"


def problems(steps: dict, d: Path) -> list[str]:
    out = []
    for k in ("question", "steps", "data", "queries", "aggregations", "headline"):
        if not steps.get(k):
            out.append(f"analysis_steps.json: {k} is empty")
    if TEMPLATE_MARK in json.dumps(steps):
        out.append("analysis_steps.json still holds template text (<fill in ...>)")
    for q in steps.get("queries", []):
        kind = str(q.get("source", "")).split(":", 1)[0]
        if kind not in SOURCE_KINDS:
            out.append(f"query {q.get('name')}: source must start with one of {SOURCE_KINDS} (e.g. mysql:warehouse)")
        if kind in ("growthbook", "cstat") and not (q.get("code") or q.get("code_file")):
            out.append(f"query {q.get('name')}: a {kind} step needs `code` (the call that fetches or computes it)")
        if kind in ("mysql", "metabase", "clickhouse", "ods") and not _sql_files(q):
            out.append(f"query {q.get('name')}: needs sql_file")
        for f in _sql_files(q):
            if not (d / f).exists():
                out.append(f"query {q.get('name')}: {f} does not exist")
        if not q.get("explain"):
            out.append(f"query {q.get('name')}: explain is empty (the notebook's text cell for it)")
    listed = {f for q in steps.get("queries", []) for f in _sql_files(q)}
    for f in sorted((d / "queries").glob("*.sql")) if (d / "queries").exists() else []:
        rel = str(f.relative_to(d))
        if rel not in listed:
            out.append(f"{rel} is in the run but not listed in analysis_steps.json queries")
    for a in steps.get("aggregations", []):
        if not _code(a, d):
            out.append(f"aggregation {a.get('name')}: code is empty")
    for h in steps.get("headline", []):
        if not isinstance(h.get("value"), (int, float)) or isinstance(h.get("value"), bool):
            out.append(f"headline {h.get('name')!r}: value must be a plain number")
        if not h.get("expr"):
            out.append(f"headline {h.get('name')!r}: expr is empty (the notebook expression that computes it)")
    return out


def _md_table(rows: list[dict], cols: list[str], labels: list[str] | None = None) -> list[str]:
    if not rows:
        return ["(none)"]
    out = ["| " + " | ".join(labels or cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c) if r.get(c) is not None else "").replace("|", "\\|").replace("\n", " ")
                                     for c in cols) + " |")
    return out


# ---------------------------------------------------------------- methods.md
def build_methods(run) -> tuple[Path, list[str]]:
    d = run_record.resolve(run)
    rj, st = run_record._load(d), _steps(d)
    warn = problems(st, d) + claims_mismatch(st, d)
    contract = (d / "contract.md").read_text().strip() if (d / "contract.md").exists() else "(no contract.md)"
    contract = re.sub(r"^(#+)\s", lambda m: "#" * min(len(m.group(1)) + 2, 6) + " ", contract, flags=re.M)
    L = [f"# Analysis record: {st.get('question') or rj['question']}", "",
         f"Run `{rj['run_id']}` · type {rj['type']} · tier {rj.get('tier') or '?'} · opened {rj['created_at'][:16]}"
         + (f" · closed {rj['ended_at'][:16]}" if rj.get("ended_at") else ""), "",
         f"**Decision this serves:** {st.get('decision', '')}", "", "## Headline results", ""]
    L += _md_table(st.get("headline", []), ["name", "value", "unit"])
    if st.get("definitions"):
        L += ["", "## Definitions", ""] + [f"- **{x.get('term')}**: {x.get('meaning')}" for x in st["definitions"]]
    L += ["", "## Agreed measurement contract", "",
          f"Confirmed: {rj.get('contract_confirmed_at') or ('skipped: ' + str(rj.get('contract_skipped')) if rj.get('contract_skipped') else 'no')}",
          "", contract, "", "## Steps", ""]
    for i, s in enumerate(st.get("steps", []), 1):
        L.append(f"{i}. **{s.get('step', '')}**: {s.get('what', '')}" + (f" (why: {s['why']})" if s.get("why") else ""))
    L += ["", "## Data used", ""]
    L += _md_table(st.get("data", []), ["source", "table", "grain", "window", "filters", "rows"])
    L += ["", "## Queries", ""]
    meta = {q.get("id"): q for q in rj.get("queries", []) if isinstance(q, dict)}
    for q in st.get("queries", []):
        m = meta.get(q["name"], {})
        facts = ", ".join(x for x in [f"source `{q.get('source')}`",
                                      f"{m['rows']} rows" if m.get("rows") is not None else "",
                                      f"{m['runtime_s']} s" if m.get("runtime_s") is not None else "",
                                      f"output `{q['output']}`" if q.get("output") else ""] if x)
        L += [f"### {q['name']}: {q.get('purpose', '')}", "", q.get("explain", ""), "", facts, ""]
        for f in _sql_files(q):
            p = d / f
            L += [f"`{f}`", "", "```sql", p.read_text().strip() if p.exists() else "-- file missing", "```", ""]
        if q.get("code") or q.get("code_file"):
            L += ["```python", _code(q, d), "```", ""]
    L += ["## Aggregations", ""]
    for a in st.get("aggregations", []):
        L += [f"### {a.get('name', '')} (from {', '.join(a.get('inputs', []))})", "", a.get("how", ""), "",
              a.get("explain", ""), "", "```python", _code(a, d), "```", ""]
    v = rj.get("verification") or {}
    L += ["## Verification", "", f"Overall: **{v.get('result') or v.get('status') or 'not recorded'}**"
          + (f" ({v['reason']})" if v.get("reason") else ""), ""]
    nums = v.get("numbers")
    if isinstance(nums, list) and all(isinstance(n, dict) for n in nums):
        L += _md_table(nums, ["name", "claimed", "recomputed", "diff_pct", "status", "route"])
    elif isinstance(nums, dict):  # older runs recorded {name: value}
        L += [f"- {k}: {val}" for k, val in nums.items()]
    if rj.get("challenges"):
        L += ["", "Challenger rounds: " + "; ".join(f"{c.get('mode')} ({c.get('blockers')} blockers, {c.get('majors')} majors)"
                                                   for c in rj["challenges"] if isinstance(c, dict))]
    if rj.get("corrections"):
        L += ["", "## Corrections from the user", ""] + [
            f"- {c.get('quote', '')} (step {c.get('step', '?')})" if isinstance(c, dict) else f"- {c}"
            for c in rj["corrections"]]
    if st.get("caveats"):
        L += ["", "## Caveats", ""] + [f"- {c}" for c in st["caveats"]]
    r = rj.get("reproduction") or {}
    L += ["", "## Reproduce", "",
          f"`{NOTEBOOK}` in this folder explains and re-runs every query and aggregation above against the same "
          "read-only sources, then compares its numbers with the reported ones. Open it in Jupyter or VS Code.",
          "", f"Last check: **{r.get('status', 'not run')}**"
          + (f" on {r['checked_at'][:16]} ({r.get('mode')})" if r.get("checked_at") else "")]
    if warn:
        L += ["", "## Gaps in this record", ""] + [f"- {w}" for w in warn]
    out = d / "methods.md"
    out.write_text("\n".join(L).rstrip() + "\n")
    return out, warn


# ---------------------------------------------------------------- reproduce.ipynb
_SETUP = '''import json
import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

pd.set_option("display.max_columns", 50)
SKILLS = Path.home() / ".claude" / "skills"
# The data tools run under the interpreter that has their database drivers.
TOOL_PY = {tool_py!r} if Path({tool_py!r}).exists() else "python3"
CAUSAL_PY = Path("{venv_py}")
CSTAT = Path("{cstat}")
TOOLS = {{}}  # source kind -> (tool script, flag that selects the engine/database); filled from the sources file
_CFG = Path(os.environ.get("CLU_SOURCES_FILE", Path.home() / ".claude" / "clu-sources.json"))
if _CFG.exists():
    for _k, _v in json.loads(_CFG.read_text()).items():
        TOOLS[_k] = (Path(_v["tool"]).expanduser(), _v.get("flag", "--engine"))
CACHE = Path(os.environ["REPRO_CACHE"]) if os.environ.get("REPRO_CACHE") else None


def _frame(obj):
    rows = obj.get("rows", obj) if isinstance(obj, dict) else obj
    return pd.DataFrame(rows)


def _read(p):
    p = Path(p)
    if p.suffix == ".csv":
        return pd.read_csv(p)
    if p.suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    return _frame(json.loads(p.read_text()))


def run_sql(source, sql, cache_file=None, max_limit=None, database=None):
    """Run one read-only query. source is "mysql:<engine>", "metabase:<db>", "clickhouse:<engine>",
    "ods:<engine>" or "file:<csv or xlsx path>". Returns a DataFrame. With REPRO_CACHE set it reads the
    saved result instead, and never queries a live source."""
    kind, _, target = source.partition(":")
    if kind == "file":
        return _read(Path(target).expanduser())
    if CACHE:
        if cache_file and (CACHE / cache_file).exists():
            return _read(CACHE / cache_file)
        raise FileNotFoundError(f"cached mode: no saved result ({{cache_file or 'none recorded'}}) for this "
                                f"{{source}} query; run the notebook live, or save the result to that path")
    if kind not in TOOLS:
        raise RuntimeError(f"no tool configured for source kind {{kind!r}}; add it to {{_CFG}}")
    tool, flag = TOOLS[kind]
    with tempfile.TemporaryDirectory() as t:
        sql_p, out_p = Path(t) / "q.sql", Path(t) / "out.json"
        sql_p.write_text(sql)
        cmd = [TOOL_PY, str(tool), "--quiet"] + (["--max-limit", str(max_limit)] if max_limit else []) \
            + (["--database", str(database)] if database and kind != "metabase" else [])
        if kind == "metabase":
            cmd += ["query", flag, target, "--file", str(sql_p), "--output", str(out_p)]
        else:
            cmd += [flag, target, "query", "--file", str(sql_p), "--output", str(out_p)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"{{source}} query failed: {{(r.stderr or r.stdout).strip()[-500:]}}")
        return _frame(json.loads(out_p.read_text()))


def cstat(*args):
    """Run a clu causal-toolkit command (the same tested estimators) and return its JSON result."""
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "r.json"
        subprocess.run([str(CAUSAL_PY), str(CSTAT), *args, "--out", str(out)], check=True, capture_output=True, text=True)
        return json.loads(out.read_text())'''

_CHECK = '''EXPECTED = {expected}
UNITS = {units}
rows = []
for name, want in EXPECTED.items():
    got = headline.get(name)
    diff = None if got is None else (0.0 if got == want else abs(got - want) / abs(want) * 100 if want else float("inf"))
    rows.append({{"number": name, "reported": want, "reproduced": got, "unit": UNITS.get(name, ""),
                 "difference %": None if diff is None else round(diff, 3), "reproduced within {tol}%": diff is not None and diff <= {tol}}})
print("REPRO_RESULT " + json.dumps({{"headline": headline}}, default=float))
pd.DataFrame(rows)'''


def _src(text: str) -> list[str]:
    lines = text.split("\n")
    return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]


def _md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": _src(text.strip())}


def _py(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _src(text.strip())}


def _sql_literal(sql: str) -> str:
    """The SQL as a Python literal that reproduces it exactly: a readable triple-quoted block when that is
    safe, otherwise an escaped string (for backslashes or triple quotes inside the SQL)."""
    sql = sql.strip()
    if "\\" in sql or '"""' in sql or sql.endswith('"'):
        return json.dumps(sql, ensure_ascii=False)
    return '"""\n' + sql + '\n"""'


def notebook(run) -> Path:
    d = run_record.resolve(run)
    rj, st = run_record._load(d), _steps(d)
    q_text = (st.get("question") or rj["question"]).strip()
    heads = st.get("headline", [])
    cells = [_md(
        f"# {q_text}\n\n"
        f"**Run** `{rj['run_id']}` · {rj['created_at'][:10]} · analysed by clu\n\n"
        f"**Why this was asked:** {st.get('decision', '')}\n\n"
        "This notebook recreates the answer from scratch. Each step below starts with a short explanation "
        "of what the code does and why, followed by the code itself. It runs every query again against the "
        "same read-only source, repeats the same calculations, and at the end compares its numbers with the "
        "ones that were reported.\n\n"
        "**To run it:** use a Python kernel with pandas. The queries go through the read-only data "
        "tools listed in your clu sources file, with their usual credentials. To reuse the saved query results instead "
        "of querying again, set the environment variable `REPRO_CACHE` to the run folder before starting.")]
    lines = ["## The answer being reproduced", ""] + _md_table(heads, ["name", "value", "unit"], ["Number", "Reported value", "Unit"])
    if st.get("definitions"):
        lines += ["", "### Definitions", ""] + [f"- **{x.get('term')}**: {x.get('meaning')}" for x in st["definitions"]]
    cells.append(_md("\n".join(lines)))
    lines = ["## Data used", ""] + _md_table(st.get("data", []), ["source", "table", "grain", "window", "filters"],
                                               ["Source", "Table", "One row is", "Window", "Filters"])
    lines += ["", "## How the analysis went", ""] + [f"{i}. **{s.get('step', '')}**: {s.get('what', '')}"
                                                      + (f" Why: {s['why']}" if s.get("why") else "")
                                                      for i, s in enumerate(st.get("steps", []), 1)]
    cells.append(_md("\n".join(lines)))
    cells.append(_md("## Setup\n\nHelper functions: `run_sql` sends one read-only query to a configured source and "
                     "returns a table (a pandas DataFrame); `cstat` runs clu's tested statistics toolkit for causal "
                     "estimates. Nothing here writes to any database."))
    cells.append(_py(_SETUP.format(tool_py=sys.executable, venv_py=VENV_PY, cstat=REPO / "scripts/causal/cstat.py")))
    for q in st.get("queries", []):
        var, files, src = _var(q["name"]), _sql_files(q), q.get("source", "")
        note = [f"## Query `{q['name']}`: {q.get('purpose', '')}", "", q.get("explain", ""), "", f"Source: `{src}`."]
        if len(files) > 1:
            note.append(f"The query runs in {len(files)} chunks, which are stacked into one table, because the "
                        "source caps how many rows or how much time one query may use.")
        cells.append(_md("\n".join(note)))
        if q.get("code") or q.get("code_file"):
            cells.append(_py(_code(q, d)))
            continue
        if src.startswith("file:"):
            fp = Path(src[5:]).expanduser()
            fp = fp if fp.is_absolute() else (d / fp)  # the notebook runs elsewhere, so pin the path
            cells.append(_py(f'{var} = run_sql({"file:" + str(fp.resolve())!r}, "")\n{var}.head(20)'))
            continue
        sqls = [(d / f).read_text() if (d / f).exists() else f"-- missing {f}" for f in files]
        outs = q.get("output") or []
        outs = [outs] if isinstance(outs, str) else list(outs)
        extra = "".join(f", {k}={q[k]!r}" for k in ("max_limit", "database") if q.get(k))
        if len(sqls) == 1:
            cells.append(_py(f"SQL_{var.upper()} = {_sql_literal(sqls[0])}\n"
                             f'{var} = run_sql("{src}", SQL_{var.upper()}, cache_file={(outs or [None])[0]!r}{extra})\n'
                             f"print(len({var}), 'rows')\n{var}.head(20)"))
        else:
            body = "\n".join(f"SQL_{var.upper()}_{i + 1} = {_sql_literal(s)}" for i, s in enumerate(sqls))
            pairs = ", ".join(f"(SQL_{var.upper()}_{i + 1}, {(outs[i] if i < len(outs) else None)!r})"
                              for i in range(len(sqls)))
            cells.append(_py(f"{body}\n{var} = pd.concat([run_sql(\"{src}\", s, cache_file=c{extra}) for s, c in [{pairs}]], "
                             f"ignore_index=True)\nprint(len({var}), 'rows')\n{var}.head(20)"))
    for a in st.get("aggregations", []):
        cells.append(_md(f"## Calculation `{a.get('name')}`: {a.get('how', '')}\n\n{a.get('explain', '')}\n\n"
                         f"Uses: {', '.join('`' + i + '`' for i in a.get('inputs', []))}."))
        cells.append(_py(_code(a, d)))
    cells.append(_md("## The headline numbers\n\nThese are the numbers the analysis reported, computed from the "
                     "steps above." + ("\n\n" + "\n".join(f"- **{h['name']}** ({h.get('unit', '')}) = `{h.get('expr')}`"
                                                          for h in heads))))
    cells.append(_py("headline = {\n" + "".join(f"    {h['name']!r}: float({h.get('expr')}),\n" for h in heads)
                     + "}\npd.Series(headline, name=\"value\").to_frame()"))
    cells.append(_md(f"## Check against the reported numbers\n\nA number reproduces when it is within {TOLERANCE_PCT}% "
                     "of the reported value. A larger difference means either the data changed since the analysis "
                     "(for example, rows were updated or purged) or a step above differs from what was done."))
    cells.append(_py(_CHECK.format(expected=repr({h["name"]: h.get("value") for h in heads}),
                                   units=repr({h["name"]: h.get("unit", "") for h in heads}),
                                   tol=TOLERANCE_PCT)))
    tail = ["## Notes", "", "The full record of this analysis (the agreed definitions, every query, the independent "
            "verification and any corrections) is in `methods.md` in the same folder."]
    if st.get("caveats"):
        tail += ["", "Caveats:"] + [f"- {c}" for c in st["caveats"]]
    cells.append(_md("\n".join(tail)))
    nb = {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"},
                       "clu": {"run_id": rj["run_id"], "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}}}
    out = d / NOTEBOOK
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n")
    return out


def build(run) -> tuple[list[Path], list[str]]:
    d = run_record.resolve(run)
    methods, warn = build_methods(d)
    return [methods, notebook(d)], warn


def scaffold(run) -> str:
    d = run_record.resolve(run)
    sp = d / "analysis_steps.json"
    if sp.exists():
        return f"{sp} already exists; fill it in and run `run_report.py check {d}`"
    t = json.loads(json.dumps(STEPS_TEMPLATE))
    t["question"] = run_record._load(d)["question"]
    sp.write_text(json.dumps(t, ensure_ascii=False, indent=2) + "\n")
    return f"wrote {sp}: fill it in (replace every <fill in ...>), then run `run_report.py check {d}`"


def expected(d: Path) -> dict:
    """Reported headline values, keyed as the notebook keys them (analysis_steps.json `headline`)."""
    return {n["name"]: n["value"] for n in _steps(d).get("headline", [])}


def claims_mismatch(steps: dict, d: Path) -> list[str]:
    """The verifier checked verify/claims.json; the notebook reproduces `headline`. They must agree."""
    c = d / "verify" / "claims.json"
    if not c.exists():
        return []
    claims = {n.get("name"): n.get("value") for n in json.loads(c.read_text()).get("numbers", [])}
    heads = {h.get("name"): h.get("value") for h in steps.get("headline", [])}
    out = [f"claims.json names {name!r}, which analysis_steps.json headline lacks (use the same names)"
           for name in claims if name not in heads]
    out += [f"headline {n!r} = {heads[n]} but claims.json says {v}" for n, v in claims.items() if n in heads and heads[n] != v]
    return out


_EXEC = '''import json, sys
import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError
path, timeout = sys.argv[1], int(sys.argv[2])
nb = nbformat.read(path, as_version=4)
err = None
try:
    NotebookClient(nb, timeout=timeout, kernel_name="python3", allow_errors=False,
                   resources={"metadata": {"path": "/tmp"}}).execute()
except CellExecutionError as e:
    err = str(e).strip().splitlines()[-1][:300]
nbformat.write(nb, path)
text = "".join(o.get("text", "") for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", []) if o.get("output_type") == "stream")
print(json.dumps({"error": err, "stdout": text[-20000:]}))
'''


def _execute(nb: Path, env: dict, timeout: int) -> tuple[str, str | None]:
    """Execute the notebook in place (outputs saved). Falls back to running its code cells as a script."""
    if VENV_PY.exists() and subprocess.run([str(VENV_PY), "-c", "import nbclient, ipykernel"], capture_output=True).returncode == 0:
        try:
            cells = sum(c["cell_type"] == "code" for c in json.loads(nb.read_text())["cells"])
            # nbclient's timeout is per cell; the whole run may take up to one timeout per code cell
            r = subprocess.run([str(VENV_PY), "-c", _EXEC, str(nb), str(timeout)], capture_output=True, text=True,
                               env=env, timeout=timeout * max(cells, 1) + 120)
        except subprocess.TimeoutExpired:
            return "", f"timed out after {timeout}s (use --cached to check the calculations on saved results)"
        if r.returncode:
            return "", (r.stderr.strip().splitlines() or ["notebook runner failed"])[-1][:300]
        res = json.loads(r.stdout.strip().splitlines()[-1])
        return res["stdout"], res["error"]
    code = "\n\n".join("".join(c["source"]) for c in json.loads(nb.read_text())["cells"] if c["cell_type"] == "code")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=timeout, cwd="/tmp")
    except subprocess.TimeoutExpired:
        return "", f"timed out after {timeout}s"
    return r.stdout, None if r.returncode == 0 else (r.stderr.strip().splitlines() or ["failed"])[-1][:300]


def check(run, cached: bool = False, timeout: int = 1800) -> dict:
    d = run_record.resolve(run)
    st = _steps(d)
    probs = [p for p in problems(st, d) if any(k in p for k in ("template", "is empty", "needs", "does not exist",
                                                                   "plain number", "source must"))]
    probs += claims_mismatch(st, d)
    if probs:
        raise ValueError("analysis_steps.json is incomplete: " + "; ".join(probs))
    nb = notebook(d)
    env = {k: v for k, v in os.environ.items() if k != "REPRO_CACHE"}
    if cached:
        env["REPRO_CACHE"] = str(d)
    out, err = _execute(nb, env, timeout)
    got, numbers = None, []
    m = re.search(r"^REPRO_RESULT (.+)$", out, re.M)
    if m:
        got = json.loads(m.group(1)).get("headline", {})
    for name, w in expected(d).items():
        g = (got or {}).get(name)
        ok = isinstance(g, (int, float)) and not isinstance(g, bool) and (
            g == w or bool(w) and abs(g - w) / abs(w) * 100 <= TOLERANCE_PCT)
        numbers.append({"name": name, "reported": w, "reproduced": g, "ok": bool(ok)})
    status = "pass" if err is None and got is not None and numbers and all(n["ok"] for n in numbers) else "fail"
    res = {"status": status, "mode": "cached" if cached else "live", "notebook": NOTEBOOK,
           "checked_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "numbers": numbers}
    if status == "fail":
        res["error"] = err or ("numbers differ" if got is not None else "no REPRO_RESULT line")
    run_record.set_field(d, "reproduction", json.dumps(res))
    build_methods(d)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_report.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scaffold").add_argument("run")
    sub.add_parser("build").add_argument("run")
    c = sub.add_parser("check")
    c.add_argument("run")
    c.add_argument("--cached", action="store_true")
    c.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "scaffold":
            print(scaffold(a.run))
        elif a.cmd == "build":
            paths, warn = build(a.run)
            print("\n".join(map(str, paths)))
            for w in warn:
                print(f"gap: {w}", file=sys.stderr)
        else:
            r = check(a.run, a.cached, a.timeout)
            print(f"reproduction {r['status']} ({r['mode']}); notebook executed and saved with its outputs")
            for n in r["numbers"]:
                print(f"- {n['name']}: reported {n['reported']} | reproduced {n['reproduced']} | {'ok' if n['ok'] else 'DIFFERS'}")
            if r.get("error"):
                print(f"error: {r['error']}", file=sys.stderr)
            return 0 if r["status"] == "pass" else 1
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"run_report.py {a.cmd}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
