import shutil

import pytest
import yaml

import datasim
import product_rules


def test_oracles():
    assert product_rules.percent_from_price(10000, 5500) == 45
    assert product_rules.js_percent_from_price(10000, 5500) == 44  # the float bug, reproduced in Python
    assert product_rules.percent_from_price(10000, 10100) is None and product_rules.percent_from_price(10000, 0) is None
    assert product_rules.price_from_percent(77735000, 18) == 63742700 and product_rules.js_price_from_percent(77735000, 18) == 63742650
    assert product_rules.seats_valid(8, 50) and not product_rules.seats_valid(9, 50)
    assert not product_rules.seats_valid(8, 7) and not product_rules.seats_valid(1.5, 7) and not product_rules.seats_valid(True, 7)
    assert product_rules.shows_valid(["mon", "mon"], []) and not product_rules.shows_valid(["mon", "tue"], [])
    assert not product_rules.shows_valid([], []) and not product_rules.shows_valid(["mon"], [3])
    assert product_rules.deposit_range(7) == (2, 4)


def scenario(tmp_path, impl):
    s = {"scenario": "t", "requirement_ids": ["R6"], "oracle": {"ref": "product_rules:percent_from_price", "args": ["base", "price"]},
         "impl": impl, "generate": {"seed": 1, "count": 50, "fields": {
             "base": {"type": "choice", "values": [10000], "boundaries": [10000, 1000000]},
             "pct": {"type": "int", "min": 1, "max": 99, "boundaries": [10, 45]},
             "price": {"type": "expr", "expr": "base * (100 - pct) // 100"}}}}
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(s))
    return datasim.load(p)


def test_simulation_python_port_finds_and_shrinks(tmp_path):
    res = datasim.simulate(scenario(tmp_path, {"adapter": "python", "ref": "product_rules:js_percent_from_price", "args": ["base", "price"]}))
    assert res["verdict"] == "fail" and res["smallest"]["input"]["base"] == 10000 and res["smallest"]["input"]["pct"] == 45
    assert "FAIL" in datasim.report_md(res)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_simulation_node_matches_python_port(tmp_path):
    (tmp_path / "f.js").write_text("module.exports = {p: (b, v) => { const r = Math.floor(100 - (v / b) * 100); "
                                   "return (r > 90 || r < 5) ? null : r; }};")
    node = datasim.simulate(scenario(tmp_path, {"adapter": "node", "source": "f.js", "function": "p", "args": ["base", "price"]}))
    py = datasim.simulate(scenario(tmp_path, {"adapter": "python", "ref": "product_rules:js_percent_from_price", "args": ["base", "price"]}))
    assert node["mismatches"] == py["mismatches"] > 0


def test_oracle_against_itself_passes(tmp_path):
    res = datasim.simulate(scenario(tmp_path, {"adapter": "python", "ref": "product_rules:percent_from_price", "args": ["base", "price"]}))
    assert res["verdict"] == "pass" and res["inputs"] == 2 * 2 + 50


def test_reconcile_and_sqlcheck(tmp_path):
    (tmp_path / "e.csv").write_text("id,price\n1,100\n2,200\n3,300\n")
    (tmp_path / "a.csv").write_text("id,price\n1,100\n2,201\n4,400\n")
    r = datasim.reconcile(tmp_path / "e.csv", tmp_path / "a.csv", "id", ["price"])
    assert r["value_mismatches"] == 1 and r["missing_in_actual"] == ["3"] and r["unexpected_in_actual"] == ["4"]
    assert datasim.reconcile(tmp_path / "e.csv", tmp_path / "a.csv", "id", ["price"], tol=1)["value_mismatches"] == 0
    (tmp_path / "q.sql").write_text("DELETE FROM promotions")
    assert datasim.main(["sqlcheck", str(tmp_path / "q.sql")]) == 4


def test_source_check(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "repo").mkdir(parents=True)
    (work / "repo" / "f.ts").write_text("a\nb\nc\n")
    import hashlib
    sha = hashlib.sha256("a\nb".encode()).hexdigest()[:16]
    s = {"source_check": {"file": "$RINZLER_WORK/repo/f.ts", "lines": "1-2", "sha256": sha}}
    assert datasim.source_check(s)["status"] == "same"
    (work / "repo" / "f.ts").write_text("a\nB\nc\n")
    assert datasim.source_check(s)["status"] == "changed"
