import json
import shutil

import pytest
import yaml

import checks
import eval_gen
import evals


def test_checks_basics():
    obs = {"output": {"price": 5500, "label": "۴۵ درصد"}, "latency_ms": 12}
    assert checks.run_check({"type": "equals", "on": "output.price", "value": 5500}, obs)[0]
    assert checks.run_check({"type": "range", "on": "output.price", "min": 5000, "max": 6000}, obs)[0]
    assert checks.run_check({"type": "digits", "on": "output.label", "script": "persian"}, obs)[0]
    assert not checks.run_check({"type": "equals", "on": "output.missing", "value": 1}, obs)[0]
    assert checks.run_check({"type": "range", "on": "output.label", "min": 40, "max": 50}, obs)[0]  # Persian digits parsed
    assert checks.validate_check({"type": "nope"}) and checks.validate_check({"type": "equals"})
    assert checks.is_refusal("متأسفانه نمی‌توانم کمک کنم") and not checks.is_refusal("این نام مجاز نیست")


@pytest.fixture
def suite_dir(tmp_path):
    (tmp_path / "impl.py").write_text("def pct(base, price):\n    return int(100 - price * 100 // base)\n")
    (tmp_path / "impl.js").write_text("module.exports = { add: (a, b) => a + b };\n")
    suite = {"suite": "s", "feature": "F", "cases": [
        {"id": "A", "title": "python pass", "requirement_ids": ["R1"], "expected": "45", "tags": ["smoke"],
         "target": {"adapter": "python", "ref": "impl:pct"}, "input": {"base": 10000, "price": 5500},
         "checks": [{"type": "equals", "value": 45}]},
        {"id": "B", "title": "node fail", "requirement_ids": ["R2"], "expected": "3",
         "target": {"adapter": "node", "source": "impl.js", "function": "add"}, "input": {"args": [1, 1]},
         "checks": [{"type": "equals", "value": 3}]},
        {"id": "C", "title": "browser case", "requirement_ids": ["R2"], "expected": "ok",
         "target": {"adapter": "playwright", "grep": "browser case"}},
        {"id": "D", "title": "explore", "requirement_ids": ["R3"], "expected": "ok", "target": {"adapter": "manual"}},
        {"id": "E", "title": "api", "requirement_ids": ["R3"], "expected": "200",
         "target": {"adapter": "http", "url": "https://seller.example.com/x", "method": "POST"},
         "checks": [{"type": "equals", "on": "status", "value": 200}]},
    ]}
    p = tmp_path / "suite.yaml"
    p.write_text(yaml.safe_dump(suite, allow_unicode=True))
    return tmp_path, p


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_run_report_diff_trace(suite_dir):
    d, p = suite_dir
    pw = {"suites": [{"specs": [{"title": "browser case", "tests": [{"results": [{"status": "passed"}]}]}]}]}
    (d / "pw.json").write_text(json.dumps(pw))
    (d / "manual.yaml").write_text("D: {result: fail, notes: seen in session}\n")
    s = evals.load(p)
    assert evals.validate(s) == []
    res = evals.run_suite(s, allow_network=True, playwright_results=d / "pw.json", manual_results=d / "manual.yaml")
    st = {c["id"]: c["status"] for c in res["cases"]}
    assert st == {"A": "pass", "B": "fail", "C": "pass", "D": "fail", "E": "skipped"}  # E: production write refused
    assert "Pass rate 50%" in evals.report(res)
    base = json.loads(json.dumps(res))
    base["cases"][1]["status"] = "pass"
    dd = evals.diff(base, res)
    assert dd["regressed"] == ["B"]
    tr = evals.trace([s], [{"id": "R1", "text": "a"}, {"id": "R9", "text": "b"}], [res])
    assert "Requirements without a case: R9" in tr and "unknown requirements: R2, R3" in tr


def test_not_run_is_never_pass(suite_dir):
    d, p = suite_dir
    res = evals.run_suite(evals.load(p))
    st = {c["id"]: c["status"] for c in res["cases"]}
    assert st["C"] == "not_run" and st["D"] == "not_run" and st["E"] == "not_run"


def test_validate_catches_problems():
    bad = {"suite": "s", "feature": "f", "cases": [
        {"id": "A", "title": "t", "requirement_ids": ["R1"], "expected": "x", "target": {"adapter": "python", "ref": "m:f"}},
        {"id": "A", "title": "t", "requirement_ids": "R1", "expected": "x", "target": {"adapter": "ftp"}, "kind": "odd"}]}
    errs = evals.validate(bad)
    assert any("needs checks" in e for e in errs) and any("duplicate" in e for e in errs)
    assert any("adapter" in e for e in errs) and any("kind" in e for e in errs)


def test_eval_gen_limits_and_cases():
    lim = eval_gen.parse_limits("Quantity: whole number, at least 1, up to the stock and at most 1000")
    assert {"min": 1} in lim and {"max": 1000} in lim
    assert eval_gen.parse_limits("New price: at least 1,000 and under 20 billion rials") == [
        {"min": 1000}, {"max": 20_000_000_000, "max_exclusive": True}]
    assert eval_gen.parse_limits("discount 0-99 percent") == [{"min": 0, "max": 99, "unit": "percent"}]
    assert eval_gen.parse_limits("announced up to 48 hours before")[0]["unit"] == "time"
    suite = eval_gen.generate({"feature": "F", "requirements": [
        {"id": "R1", "field": "qty", "text": "Quantity: whole number, at least 1 and at most 1000"},
        {"id": "R2", "text": "Statuses: pending, live, lost"},
        {"id": "R3", "field": "price", "text": "price must end with 00"}]})
    titles = [c["title"] for c in suite["cases"]]
    assert "qty just below the minimum (0)" in titles and "qty just above the maximum (1001)" in titles
    assert any("Persian digits (۱)" in t for t in titles) and any("'lost'" in t for t in titles)
    assert any("not a multiple of 100" in t for t in titles)
    assert all(c["status"] == "draft" for c in suite["cases"])
    assert evals.validate(suite) == []
