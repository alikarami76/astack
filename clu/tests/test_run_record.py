import json
import pytest
import run_record
import store


def test_new_creates_skeleton(kbenv, tmp_path):
    d = run_record.new("aov-check", "analysis", "What is AOV?", runs_dir=tmp_path / "runs")
    rj = json.loads((d / "run.json").read_text())
    assert rj["schema_version"] == 1 and rj["type"] == "analysis" and rj["queries"] == []
    assert (d / "contract.md").exists() and (d / "queries").is_dir()
    d2 = run_record.new("aov-check", "analysis", "again", runs_dir=tmp_path / "runs")
    assert d2.name.endswith("-2")


def test_append_set_validate_close(kbenv, tmp_path):
    d = run_record.new("x", "lookup", "leaf cat id?", runs_dir=tmp_path / "runs", tier="quick")
    run_record.append(d, "queries", {"id": "q1", "source": "mysql:warehouse", "runtime_s": 1.2, "rows": 1})
    run_record.append(d, "corrections", {"quote": "by product lists I meant plps", "step": "contract"})
    run_record.set_field(d, "verification", '{"result": "sustained", "discrepancies": []}')
    assert run_record.validate(d) == []
    rid = run_record.close(d, "answered")
    c = store.connect()
    row = c.execute("select * from runs_index where run_id=?", (rid,)).fetchone()
    assert row["corrections_count"] == 1 and row["verify_result"] == "sustained"


def test_validate_catches_bad_type(kbenv, tmp_path):
    d = run_record.new("y", "lookup", "q", runs_dir=tmp_path / "runs")
    run_record.set_field(d, "type", "nonsense")
    assert any("type" in p for p in run_record.validate(d))
    with pytest.raises(ValueError):
        run_record.append(d, "not_a_list", {})


def test_replay_grader():
    import replay
    case = {"id": "x", "must": ["nature", "flash_offer"], "must_not": ["tags\\s*=\\s*'flash_offer'"]}
    assert replay.grade("use nature = 'flash_offer'", case)["pass"]
    assert not replay.grade("use tags = 'flash_offer'", case)["pass"]


def test_log_batches_set_and_append(kbenv, tmp_path):
    d = run_record.new("b", "analysis", "q", runs_dir=tmp_path / "runs")
    run_record.log(d, {"set": {"topics": ["ai"], "contract_confirmed_at": "2026-09-26T07:47:10Z"},
                       "append": {"subagents": [{"name": "clu-challenger", "purpose": "contract"}],
                                  "sources": [{"table": "orders", "engine": "warehouse"}]}})
    rj = json.loads((d / "run.json").read_text())
    assert rj["topics"] == ["ai"] and rj["contract_confirmed_at"] == "2026-09-26T07:47:10Z"
    assert rj["subagents"][0]["name"] == "clu-challenger" and rj["sources"][0]["table"] == "orders"
    assert run_record.validate(d) == []


def _run(tmp_path, tier="standard", rtype="analysis"):
    return run_record.new("p", rtype, "q", runs_dir=tmp_path / "runs", tier=tier)


def test_close_refuses_missing_tier_contract_and_verdict(kbenv, tmp_path):
    d = run_record.new("t", "analysis", "q", runs_dir=tmp_path / "runs")
    with pytest.raises(ValueError, match="tier"):
        run_record.close(d, "x", commit=False)
    run_record.set_field(d, "tier", "standard")
    with pytest.raises(ValueError, match="contract not confirmed") as e:
        run_record.close(d, "x", commit=False)
    assert "verification.result" in str(e.value)
    run_record.log(d, {"set": {"contract_skipped": "the user: just run it"}})
    run_record.set_field(d, "verification", '{"result": "not_run", "discrepancies": []}')
    with pytest.raises(ValueError, match="needs a reason"):
        run_record.close(d, "x", commit=False)
    run_record.set_field(d, "verification", '{"result": "not_run", "reason": "lookup of a dashboard value", "discrepancies": []}')
    with pytest.raises(ValueError, match="methods.md is missing") as e:
        run_record.close(d, "x", commit=False)
    assert "has not passed" in str(e.value)
    for f in ("analysis_steps.json", "methods.md", "reproduce.ipynb"):
        (d / f).write_text("{}")
    run_record.set_field(d, "reproduction", '{"status": "skipped", "reason": "source table purged"}')
    assert run_record.close(d, "done", commit=False) == d.name
    assert json.loads((d / "run.json").read_text())["ended_at"]


def test_quick_run_closes_without_contract_or_verifier(kbenv, tmp_path):
    d = _run(tmp_path, tier="quick", rtype="lookup")
    assert run_record.validate(d, closing=True) == []
    d2 = _run(tmp_path, tier="quick")
    assert all("reproduce" in p or "missing" in p for p in run_record.validate(d2, closing=True))


def test_resolve_accepts_run_id_and_explains_bad_path(kbenv, tmp_path, monkeypatch):
    monkeypatch.setattr(run_record, "REPO", tmp_path)
    d = _run(tmp_path)
    assert run_record.resolve(d.name) == d
    with pytest.raises(FileNotFoundError, match="run folder path"):
        run_record.resolve("2026-01-01-nope")
    assert run_record.main(["append", "2026-01-01-nope", "steps", "{}"]) == 1


def test_cli_bad_json_is_a_clear_error(kbenv, tmp_path, capsys):
    d = _run(tmp_path)
    assert run_record.main(["log", str(d), "{set: 1}"]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return p


def test_ingest_verification_applies_tolerance_in_code(kbenv, tmp_path):
    d = _run(tmp_path)
    res = {"kind": "verification", "numbers": [
        {"name": "aov", "recomputed": 100.5, "route": "clickhouse"},
        {"name": "orders", "recomputed": 1030, "route": "sum of days", "gap_explained": "3 late-synced days"},
        {"name": "gmv", "recomputed": 120, "route": "dashboard export"}],
        "spot_checks": [{"id": "o1", "status": "pass"}]}
    _write(d / "verify/result.json", res)
    with pytest.raises(ValueError, match="claims.json"):
        run_record.ingest(d, d / "verify/result.json")
    _write(d / "verify/claims.json", {"numbers": [{"name": "aov", "value": 100}, {"name": "orders", "value": 1000},
                                                   {"name": "gmv", "value": 100}]})
    out = run_record.ingest(d, "verify/result.json")
    v = json.loads((d / "run.json").read_text())["verification"]
    st = {n["name"]: n["status"] for n in v["numbers"]}
    assert st == {"aov": "sustained", "orders": "sustained", "gmv": "refuted"} and v["result"] == "partial"
    assert "VERIFICATION: partial" in out
    rj = json.loads((d / "run.json").read_text())
    assert rj["subagents"][-1]["name"] == "clu-verifier"


def test_ingest_rejects_invalid_and_records_other_kinds(kbenv, tmp_path):
    d = _run(tmp_path)
    bad = _write(d / "helpers/challenger-contract.json", {"kind": "objections", "mode": "contract",
                                                           "objections": [{"severity": "huge", "issue": "x"}]})
    with pytest.raises(ValueError, match="severity"):
        run_record.ingest(d, bad)
    _write(bad, {"kind": "objections", "mode": "contract", "objections": [
        {"severity": "blocker", "issue": "denominator is all sellers", "fix": "use eligible sellers"}]})
    assert "blocker=1" in run_record.ingest(d, bad)
    cur = _write(d / "helpers/curator.json", {"kind": "curation", "run_id": d.name, "kb_writes": ["fac_1", "les_2"],
                                              "lessons": 1, "proposals": ["fix-x"]})
    run_record.ingest(d, cur)
    rj = json.loads((d / "run.json").read_text())
    assert [k["id"] for k in rj["kb_writes"]] == ["fac_1", "les_2"] and rj["proposals"][0]["id"] == "fix-x"
    assert rj["challenges"][0]["blockers"] == 1
    assert [s["name"] for s in rj["subagents"]] == ["clu-challenger", "clu-curator"]
