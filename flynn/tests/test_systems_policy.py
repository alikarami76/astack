"""Design systems are part of flynn's core: the manifest says which ship and who owns them; servers keep them read-only."""
import json
import sys
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FLYNN / "scripts"), str(FLYNN / "kb"), str(FLYNN / "adapters" / "library")]
import systems_policy  # noqa: E402


@pytest.fixture(autouse=True)
def fixture_manifest(tmp_path, monkeypatch):
    m = tmp_path / "systems.json"
    m.write_text(json.dumps({"owners": ["owner@example.com"], "changes": "local-only",
                             "ship": ["admin-panel", "editorial", "rec:admin-panel"],
                             "ship_generated": ["rec:admin-panel"]}))
    real = systems_policy.MANIFEST
    monkeypatch.setattr(systems_policy, "MANIFEST", m)
    yield real


def test_manifest_lists_owner_and_shipped_systems(fixture_manifest):
    assert systems_policy.manifest()["changes"] == "local-only" and systems_policy.owners() == ["owner@example.com"]
    assert systems_policy.ship_generated() == {"rec:admin-panel"}
    if fixture_manifest.exists():          # the owner's copy: the real manifest is consistent too
        real = json.loads(fixture_manifest.read_text())
        assert real["owners"] and real["changes"] == "local-only"
        assert set(real.get("ship_generated", [])) <= set(real["ship"])


def test_changes_are_local_only(monkeypatch):
    monkeypatch.delenv("FLYNN_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("FLYNN_SERVER", raising=False)
    monkeypatch.setenv("FLYNN_SYSTEMS_OWNERS", "owner@example.com")
    assert systems_policy.can_change()[0] is True                       # the person at this computer
    assert systems_policy.can_change("owner@example.com")[0] is True
    assert systems_policy.can_change("someone@example.com")[0] is False
    monkeypatch.setenv("FLYNN_REQUIRE_AUTH", "1")                        # a server: read-only for everyone
    assert systems_policy.can_change("owner@example.com")[0] is False
    with pytest.raises(systems_policy.SystemsLocked):
        systems_policy.check_change()


def test_change_proposals_and_approvals_refuse_on_a_server(monkeypatch):
    import changes
    monkeypatch.setenv("FLYNN_REQUIRE_AUTH", "1")
    with pytest.raises(systems_policy.SystemsLocked):
        changes.propose({"system": "admin-panel", "title": "x", "ops": []})
    with pytest.raises(systems_policy.SystemsLocked):
        changes.approve("nope")
    with pytest.raises(systems_policy.SystemsLocked):
        changes.reject("nope")


def _row(system, name, origin="flynn"):
    return {"system": system, "name": name, "kind": "color", "level": "raw", "value": "#000", "tier": "inferred"}, origin


def test_export_ships_generated_rows_only_for_listed_systems(tmp_path):
    import store
    c = store.connect()
    for (row, origin) in (_row("rec:admin-panel", "a", "generated"), _row("rec:test-kit", "b", "generated"),
                          _row("admin-panel", "c")):
        store.upsert(c, "token", row, origin=origin)
    store.export(c, str(tmp_path), etype="token", force=True)
    names = {json.loads(l)["name"] for l in (tmp_path / "token.jsonl").read_text().splitlines()}
    assert names == {"a", "c"}


def test_sync_systems_matches_the_export_and_leaves_other_rows(tmp_path, monkeypatch):
    import store
    c = store.connect()
    for (row, origin) in (_row("admin-panel", "keep"), _row("admin-panel", "gone"), _row("editorial", "other")):
        store.upsert(c, "token", row, origin=origin)
    exp = tmp_path / "export"
    store.export(c, str(exp), etype="token", force=True)
    lines = [l for l in (exp / "token.jsonl").read_text().splitlines() if '"gone"' not in l]
    (exp / "token.jsonl").write_text("\n".join(lines) + "\n")
    r = store.sync_systems(c, ["admin-panel"], str(exp))
    names = {x[0] for x in c.execute("SELECT name FROM tokens")}
    assert "gone" not in names and {"keep", "other"} <= names and r["deleted"] == 1


def test_kb_sync_systems_refuses_off_a_server(monkeypatch):
    import kb
    monkeypatch.delenv("FLYNN_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("FLYNN_SERVER", raising=False)
    assert kb.main(["sync-systems"]) == 2


def test_studio_refuses_system_work_on_a_server(tmp_path, monkeypatch):
    import importlib
    import threading
    import urllib.error
    import urllib.request
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_STUDIO_CHATS", str(tmp_path / "chats"))
    monkeypatch.setenv("FLYNN_REQUIRE_AUTH", "1")
    sys.path.insert(0, str(FLYNN / "adapters" / "studio"))
    import chat
    importlib.reload(chat)
    spec = importlib.util.spec_from_file_location("studio", FLYNN / "adapters" / "studio" / "studio.py")
    studio = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(studio)
    (tmp_path / "runs").mkdir(exist_ok=True)
    srv = studio.serve(port=0, open_browser=False, root=tmp_path / "runs")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body):
        r = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                return f.status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        assert post("/api/chat/send", {"c": "system:admin-panel", "text": "update it"}) == 403
        assert post("/api/record/start", {"url": "https://x.example", "system": "abc", "mode": "watch", "rights": True}) == 403
        with urllib.request.urlopen(base + "/api/settings", timeout=10) as f:
            assert json.load(f)["systems"]["editable"] is False
    finally:
        srv.shutdown()
