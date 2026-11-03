import json
import datetime as dt

import pytest

import om


def fake_fetch_factory(store):
    """store: {index: [source dicts]}; answers search/query with from/size paging like OM does."""
    def fetch(path, params):
        if path == "search/query":
            rows = store.get(params["index"], [])
            f, s = int(params.get("from", 0)), int(params.get("size", 10))
            return {"hits": {"total": {"value": len(rows)}, "hits": [{"_source": r} for r in rows[f:f + s]]}}
        if path == "system/version":
            return {"version": "1.12.6"}
        raise AssertionError(path)
    return fetch


def test_config_from_mcp_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("OM_URL", raising=False)
    monkeypatch.delenv("OM_TOKEN", raising=False)
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"openmetadata": {"url": "https://host.example/om/mcp",
                                                               "headers": {"Authorization": "Bearer abc"}}}}))
    base, auth = om.config(cfg)
    assert base == "https://host.example/om/api/v1/" and auth == "Bearer abc"


def test_config_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OM_URL", "https://x.example/om")
    monkeypatch.setenv("OM_TOKEN", "tok")
    base, auth = om.config(tmp_path / "missing.json")
    assert base == "https://x.example/om/api/v1/" and auth == "Bearer tok"


def test_config_missing_is_one_line_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OM_URL", raising=False)
    monkeypatch.delenv("OM_TOKEN", raising=False)
    with pytest.raises(ValueError, match="openmetadata"):
        om.config(tmp_path / "missing.json")


def test_pull_pages_and_writes_snapshot(tmp_path):
    tables = [{"fullyQualifiedName": f"S.d.s.t{i}", "name": f"t{i}"} for i in range(250)]
    fetch = fake_fetch_factory({"table": tables, "metric": [{"name": "m"}], "glossaryTerm": [], "tag": [],
                                "domain": [], "classification": []})
    snap = om.pull(tmp_path, fetch=fetch, today=dt.date(2026, 10, 10))
    assert snap == tmp_path / "2026-10-10"
    assert len(json.loads((snap / "table.json").read_text())) == 250
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["snapshot"] == str(snap) and state["counts"]["table"] == 250 and state["om_version"] == "1.12.6"


def test_latest_snapshot_and_age(tmp_path, monkeypatch):
    monkeypatch.setenv("CLU_OM_DIR", str(tmp_path))
    assert om.state() is None
    (tmp_path / "state.json").write_text(json.dumps({"snapshot": str(tmp_path / "2026-10-01"),
                                                     "pulled_at": "2026-10-01T10:00:00"}))
    assert om.age_days(now=dt.datetime(2026, 10, 10, 10, 0)) == 9


def test_strip_semantics():
    text, sem = om.split_semantics('Time key.\n\n[semantics]\n{"semanticRole":"time_key","canFilter":true}')
    assert text == "Time key." and sem == {"semanticRole": "time_key", "canFilter": True}
    assert om.split_semantics("plain") == ("plain", {})
    assert om.split_semantics("bad\n\n[semantics]\n{not json") == ("bad", {})
