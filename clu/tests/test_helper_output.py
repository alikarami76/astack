import helper_output as ho


def test_every_kind_has_a_valid_minimal_example():
    examples = [
        {"kind": "objections", "mode": "conclusions", "objections": []},
        {"kind": "runner_result", "name": "q1", "source": "mysql", "sql_file": "a.sql", "result_file": "a.json",
         "rows": 3, "capped": False, "runtime_s": 1.5},
        {"kind": "verification", "numbers": [{"name": "x", "recomputed": 1, "route": "r"}]},
        {"kind": "edit_review", "file": "r.pdf", "verdict": "ready", "fixes": []},
        {"kind": "curation", "run_id": "r", "kb_writes": [], "lessons": 0, "proposals": []},
        {"kind": "causal_plan", "plan_file": "p.md", "estimand": "e", "estimand_type": "ATT", "recommended": "did",
         "candidates": [{"design": "did", "assumption": "parallel trends", "command": "cstat.py did"}]},
    ]
    for e in examples:
        assert ho.check(e) == [], e["kind"]
        assert ho.summary(e, ho.verdict(e, {"numbers": []}) if e["kind"] == "verification" else None)


def test_check_catches_types_bools_and_unknown_kind():
    assert "kind must be" in ho.check({"kind": "prose"})[0]
    bad = {"kind": "runner_result", "name": "q", "source": "s", "sql_file": "a", "result_file": "b",
           "rows": True, "capped": "no", "runtime_s": 1}
    probs = ho.check(bad)
    assert any("rows" in p for p in probs) and any("capped" in p for p in probs)


def test_verdict_edges():
    claims = {"numbers": [{"name": "a", "value": 0}, {"name": "b", "value": 50}, {"name": "c", "value": 10}]}
    res = {"numbers": [{"name": "a", "recomputed": 0, "route": "r"}, {"name": "b", "recomputed": 52, "route": "r"},
                       {"name": "extra", "recomputed": 1, "route": "r"}]}
    v = ho.verdict(res, claims)
    st = {n["name"]: n["status"] for n in v["numbers"]}
    assert st == {"a": "sustained", "b": "refuted", "c": "not_recomputed"}  # 4% gap without an explanation
    assert v["result"] == "partial" and v["unclaimed_numbers"] == ["extra"]
    ok = ho.verdict({"numbers": [{"name": "a", "recomputed": 0, "route": "r"}],
                     "reconciliation": [{"check": "parts sum", "status": "fail"}]}, {"numbers": [{"name": "a", "value": 0}]})
    assert ok["result"] == "partial"  # a failed check blocks "sustained"
    nothing = ho.verdict({"numbers": [{"name": "a", "recomputed": None, "route": "no access"}]},
                         {"numbers": [{"name": "a", "value": 5}]})
    assert nothing["result"] == "not_run" and "could not recompute" in nothing["reason"]  # not checked, not wrong
    assert ho.verdict({"numbers": []}, {"numbers": []})["result"] == "not_run"
    wrong = ho.verdict({"numbers": [{"name": "a", "recomputed": 9, "route": "r"}]}, {"numbers": [{"name": "a", "value": 5}]})
    assert wrong["result"] == "refuted"
    blocked = {"kind": "runner_result", "name": "q", "status": "blocked", "source": "mysql:warehouse",
               "sql_file": ["queries/q-1.sql", "queries/q-2.sql"], "result_file": None, "rows": None,
               "capped": None, "runtime_s": None, "issues": "timeout"}
    assert ho.check(blocked) == []
