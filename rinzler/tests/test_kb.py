import json

import pytest

import kb
import run_record
import store


def test_upsert_search_export_rebuild():
    c = store.connect()
    a = store.upsert(c, "bug", {"title": "Discount percent shown one point low", "feature": "Flash discount form",
                                "severity": "S3", "priority": "P3", "status": "draft"})
    b = store.upsert(c, "bug", {"title": "Discount percent shown one point low", "feature": "Flash discount form",
                                "status": "verified"})
    assert a == b and store.get(c, a)["status"] == "verified" and store.get(c, a)["severity"] == "S3"
    assert store.search(c, "percent rounding discount", "bug")[0]["id"] == a
    store.upsert(c, "lesson", {"rule": "Use round prices in simulations", "why": "floats"})
    store.export(c)
    c2 = store.rebuild()
    assert store.stats(c2)["bug"] == 1 and store.stats(c2)["lesson"] == 1


@pytest.mark.parametrize("etype,row", [
    ("bug", {"title": "x y z w", "severity": "high"}), ("flaky", {"test_id": "t", "status": "retry"}),
    ("asset", {"kind": "movie", "name": "n"}), ("lesson", {"why": "no rule"}), ("nope", {}),
])
def test_validation(etype, row):
    with pytest.raises(ValueError):
        store.upsert(store.connect(), etype, row)


def test_export_refuses_to_drop_rows(tmp_path):
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "r1"})
    store.export(c)
    (store.export_dir() / "lesson.jsonl").write_text(
        (store.export_dir() / "lesson.jsonl").read_text() + json.dumps({"id": "les_other", "rule": "r2"}) + "\n")
    with pytest.raises(RuntimeError):
        store.export(c, "lesson")


def test_cli(capsys):
    assert kb.main(["add", "flaky", "--json", '{"test_id": "spec > a", "symptom": "timeout", "status": "quarantined"}']) == 0
    assert kb.main(["list", "flaky", "--status", "quarantined"]) == 0
    assert "spec > a" in capsys.readouterr().out
    assert kb.main(["add", "flaky", "--json", '{"test_id": "x", "status": "bad"}']) == 1


def test_run_record_lifecycle():
    d = run_record.new("demo", "eval", "test request", "Feature")
    run_record.append(d, "bugs", {"draft": "x"})
    with pytest.raises(KeyError):
        run_record.append(d, "outcome", "x")
    with pytest.raises(ValueError):
        run_record.new("../bad", "eval", "r")
    with pytest.raises(ValueError):
        run_record.new("ok", "dance", "r")
    assert run_record.new("demo", "eval", "again").name.endswith("-2")
    run = run_record.close(d, "done")
    assert run["outcome"] == "done"
    c = store.connect()
    assert store.rows(c, "run")[0]["bugs_found"] == 1
