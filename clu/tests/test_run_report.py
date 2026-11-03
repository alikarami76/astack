import json

import pytest

import run_record
import run_report


def _analysis(tmp_path, value=120.0):
    d = run_record.new("aov", "analysis", "What is AOV by month?", runs_dir=tmp_path / "runs", tier="standard")
    csv = tmp_path / "orders.csv"
    csv.write_text("month,orders,payable\n1,10,1000\n2,20,2600\n")
    steps = {"question": "What is AOV by month?", "decision": "set the coupon floor",
             "definitions": [{"term": "AOV", "meaning": "payable price per order"}],
             "steps": [{"step": "execute", "what": "monthly orders and payable", "why": "AOV needs both"}],
             "data": [{"source": "file:" + str(csv), "table": "orders export", "grain": "month", "window": "2 months"}],
             "queries": [{"name": "q1", "source": "file:" + str(csv), "purpose": "monthly totals",
                          "explain": "Reads the monthly order totals exported from the dashboard."}],
             "aggregations": [{"name": "aov", "inputs": ["q1"], "how": "sum of payable / sum of orders",
                               "explain": "Weighted by orders, so busy months count more.",
                               "code": ["aov = q1.payable.sum() / q1.orders.sum()", "aov"]}],
             "headline": [{"name": "aov", "value": value, "unit": "dollars per order", "expr": "aov"}],
             "caveats": ["Excludes B2B."]}
    (d / "analysis_steps.json").write_text(json.dumps(steps))
    return d, steps


def test_scaffold_writes_template_that_check_refuses(kbenv, tmp_path):
    d = run_record.new("t", "analysis", "q?", runs_dir=tmp_path / "runs", tier="quick")
    assert "fill it in" in run_report.scaffold(d)
    assert "already exists" in run_report.scaffold(d)
    with pytest.raises(ValueError, match="template"):
        run_report.check(d)


def test_notebook_is_valid_ipynb_with_explanations(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    (d / "queries").mkdir(exist_ok=True)
    (d / "queries/q2.sql").write_text("SELECT 1 AS x FROM t WHERE id < 10")
    st = json.loads((d / "analysis_steps.json").read_text())
    st["queries"].append({"name": "q2", "source": "mysql:warehouse", "sql_file": "queries/q2.sql", "purpose": "p",
                          "explain": "Counts rows."})
    (d / "analysis_steps.json").write_text(json.dumps(st))
    paths, gaps = run_report.build(d)
    nb = json.loads((d / "reproduce.ipynb").read_text())
    assert nb["nbformat"] == 4 and gaps == []
    kinds = [c["cell_type"] for c in nb["cells"]]
    assert kinds.count("markdown") >= kinds.count("code")  # every code cell is explained
    text = json.dumps(nb)
    assert "Weighted by orders" in text and "id < 10" in text and "Counts rows." in text
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            compile("".join(c["source"]), "cell", "exec")
    md = (d / "methods.md").read_text()
    assert "## Queries" in md and "id < 10" in md and "aov = q1.payable.sum()" in md and "Excludes B2B." in md


def test_check_executes_and_records(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    r = run_report.check(d)
    assert r["status"] == "pass", r
    rj = json.loads((d / "run.json").read_text())
    assert rj["reproduction"]["status"] == "pass"
    nb = json.loads((d / "reproduce.ipynb").read_text())
    if run_report.VENV_PY.exists():  # executed with nbclient: outputs are saved in the notebook
        assert any(c.get("outputs") for c in nb["cells"] if c["cell_type"] == "code")
    assert "Last check: **pass**" in (d / "methods.md").read_text()


def test_check_fails_when_numbers_differ(kbenv, tmp_path):
    d, _ = _analysis(tmp_path, value=150.0)
    r = run_report.check(d)
    assert r["status"] == "fail" and r["numbers"][0]["ok"] is False and r["numbers"][0]["reproduced"] == 120.0


def test_build_lists_unlisted_sql_and_handles_older_run_shapes(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    (d / "queries/extra.sql").write_text("SELECT 1")
    (d / "contract.md").write_text("# Measurement contract\n## Revisions\nx\n")
    run_record.set_field(d, "verification", json.dumps({"status": "sustained", "numbers": {"entitled": 1065}}))
    run_record.set_field(d, "corrections", json.dumps(["plain string correction"]))
    out, gaps = run_report.build_methods(d)
    md = out.read_text()
    assert any("extra.sql" in g for g in gaps)
    assert "- entitled: 1065" in md and "**sustained**" in md and "- plain string correction" in md
    assert "\n### Measurement contract" in md and "\n#### Revisions" in md


def test_sql_with_backslashes_and_quotes_is_embedded_exactly(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    (d / "queries").mkdir(exist_ok=True)
    sql = "SELECT REGEXP_REPLACE(title, '\\\\d+', '') AS t, 'O\\'Reilly' AS n FROM x WHERE id < 5"
    (d / "queries/q2.sql").write_text(sql)
    st = json.loads((d / "analysis_steps.json").read_text())
    st["queries"].append({"name": "q2", "source": "mysql:warehouse", "sql_file": "queries/q2.sql", "purpose": "p", "explain": "e"})
    (d / "analysis_steps.json").write_text(json.dumps(st))
    nb = json.loads(run_report.notebook(d).read_text())
    cell = next("".join(c["source"]) for c in nb["cells"] if "SQL_Q2 =" in "".join(c["source"]))
    ns = {}
    exec(cell.split("\nq2 = ")[0], ns)
    assert ns["SQL_Q2"] == sql


def test_cached_mode_never_queries_live(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    (d / "queries").mkdir(exist_ok=True)
    (d / "results").mkdir(exist_ok=True)
    for i in (1, 2):
        (d / f"queries/c{i}.sql").write_text(f"SELECT {i} AS x FROM t WHERE id = {i}")
    (d / "results/c1.csv").write_text("x\n1\n")
    (d / "results/c2.json").write_text(json.dumps({"rows": [{"x": 2}]}))
    st = json.loads((d / "analysis_steps.json").read_text())
    st["queries"].append({"name": "chunked", "source": "mysql:warehouse", "sql_file": ["queries/c1.sql", "queries/c2.sql"],
                          "output": ["results/c1.csv", "results/c2.json"], "purpose": "p", "explain": "e"})
    st["aggregations"][0]["code"].append("chunked_total = int(chunked.x.sum())")
    st["headline"].append({"name": "chunked_total", "value": 3, "unit": "n", "expr": "chunked_total"})
    (d / "analysis_steps.json").write_text(json.dumps(st))
    assert run_report.check(d, cached=True)["status"] == "pass"
    (d / "results/c2.json").unlink()
    r = run_report.check(d, cached=True)
    assert r["status"] == "fail" and "cached mode" in r["error"]


def test_check_refuses_claims_that_disagree_with_headline(kbenv, tmp_path):
    d, _ = _analysis(tmp_path)
    (d / "verify").mkdir()
    (d / "verify/claims.json").write_text(json.dumps({"numbers": [{"name": "Average order value", "value": 120}]}))
    with pytest.raises(ValueError, match="same names"):
        run_report.check(d)


def test_check_refuses_non_numeric_headline(kbenv, tmp_path):
    d, _ = _analysis(tmp_path, value=None)
    with pytest.raises(ValueError, match="plain number"):
        run_report.check(d)
