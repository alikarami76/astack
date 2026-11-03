import json
import pathlib
import sys

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "library"))
sys.path.insert(0, str(FLYNN / "kb"))
import changes  # noqa: E402
import store  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = store.connect(str(tmp_path / "kb.sqlite"))
    store.upsert(conn, "token", {"system": "demo", "name": "radius-medium", "kind": "radius", "level": "raw", "value": "8px",
                                 "tier": "verified", "source": "code"}, origin="flynn")
    monkeypatch.setattr(changes, "_conn", lambda: conn)
    monkeypatch.setattr(changes, "DIR", tmp_path / "changes")
    monkeypatch.setattr(changes, "SAMPLES", tmp_path / "systems")
    monkeypatch.setattr(store, "export", lambda *a, **k: [])
    return conn


def test_validation_names_what_is_missing(env):
    errs = changes.validate({"system": "demo", "title": "x", "ops": [{"op": "token.set", "name": "a"}, {"op": "nope"}]})
    assert any("kind and value" in e for e in errs) and any("unknown op" in e for e in errs)


def test_propose_records_before_and_approve_applies(env):
    c = changes.propose({"system": "demo", "title": "Radius 10", "why": "the user", "ops": [
        {"op": "token.set", "name": "radius-medium", "kind": "radius", "value": "10px"},
        {"op": "token.set", "name": "color-accent", "kind": "color", "value": "#ff7a00"},
        {"op": "component.sample", "name": "Chip", "variant": "warning", "markup": "<span>!</span>"}]})
    assert c["status"] == "pending" and c["ops"][0]["before"]["value"] == "8px" and c["ops"][1]["before"] is None
    done = changes.approve(c["id"], sync=False)
    assert done["status"] == "approved"
    row = env.execute("select value, tier from tokens where system='demo' and name='radius-medium'").fetchone()
    assert row["value"] == "10px" and row["tier"] == "verified"          # an update keeps the tier
    new = env.execute("select tier from tokens where system='demo' and name='color-accent'").fetchone()
    assert new["tier"] == "proposed"                                     # a new token enters as proposed
    assert (changes.SAMPLES / "demo" / "library-samples" / "Chip-warning.html").exists()
    with pytest.raises(ValueError):
        changes.approve(c["id"], sync=False)                             # only once


def test_reject_changes_nothing(env):
    c = changes.propose({"system": "demo", "title": "Remove radius", "ops": [{"op": "token.remove", "name": "radius-medium"}]})
    changes.reject(c["id"], "no")
    assert env.execute("select count(*) from tokens where name='radius-medium'").fetchone()[0] == 1
    assert changes.load(c["id"])["status"] == "rejected"
