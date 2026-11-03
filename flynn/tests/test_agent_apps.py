"""The flynn Studio agent's App builder jobs: apps are created and kept on this computer, results go back as one
event, and changed apps are saved on the server (faked here)."""
import importlib
import io
import json
import sys
import tarfile
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "agent"))


class FakeServer:
    url, token, ws = "https://studio.example.com", "fla_test", None

    def __init__(self):
        self.events, self.uploads, self.saved = [], [], {}

    def send_events(self, job, evs, bid, timeout=10):
        self.events.extend(evs)
        return True

    def post(self, path, body, **kw):
        self.uploads.append((path, body.get("name")))
        return {"ok": True}

    def get(self, path, raw=False, **kw):
        name = path.split("name=")[-1]
        if name in self.saved:
            return self.saved[name]
        import urllib.error
        raise urllib.error.URLError("no saved copy")


def test_create_list_and_restore_on_a_new_computer(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_AGENT_HOME", str(tmp_path / "agent"))
    import flynn_agent as fa
    fa = importlib.reload(fa)
    monkeypatch.setattr(fa, "ROOT", FLYNN)
    srv = FakeServer()
    fa.app_job(srv, {"id": "j1", "kind": "app", "op": "create", "name": None,
                     "args": {"name": "notes", "description": "Take notes"}})
    ev = srv.events[-1]
    assert ev["t"] == "result" and ev["ok"] and ev["data"]["name"] == "notes"
    assert (tmp_path / "agent" / "apps" / "notes" / "app.json").is_file()
    assert ("/flynn/agent/app-upload", "notes") in srv.uploads          # saved on the server after the change
    fa.app_job(srv, {"id": "j2", "kind": "app", "op": "list", "name": None, "args": {}, "saved": ["notes", "other"]})
    names = {a["name"]: a for a in srv.events[-1]["data"]["apps"]}
    assert set(names) == {"notes", "other"} and names["other"]["remote"]
    fa.app_job(srv, {"id": "j3", "kind": "app", "op": "info", "name": "missing-app", "args": {}})
    assert srv.events[-1]["ok"] is False and srv.events[-1]["kind"] == "key"
    # another computer: the saved copy comes back before the first operation
    srv.saved["notes"] = fa.pack_app(tmp_path / "agent" / "apps" / "notes")
    with tarfile.open(fileobj=io.BytesIO(srv.saved["notes"]), mode="r:gz") as tf:
        assert not any("node_modules" in m.name for m in tf.getmembers())
    import shutil
    shutil.rmtree(tmp_path / "agent" / "apps" / "notes")
    fa.app_job(srv, {"id": "j4", "kind": "app", "op": "versions", "name": "notes", "args": {}})
    assert srv.events[-1]["ok"] and srv.events[-1]["data"]["versions"]


def test_http_address_that_redirects_to_https_is_upgraded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_AGENT_HOME", str(tmp_path / "agent"))
    import flynn_agent as fa
    fa = importlib.reload(fa)
    fa.save_config({"url": "http://studio.example.com", "token": "fla_x"})

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return "https://studio.example.com/flynn/agent/hello"
    monkeypatch.setattr(fa.urllib.request, "urlopen", lambda *a, **k: R())
    srv = fa.Server("http://studio.example.com", "fla_x")
    fa.prefer_https(srv)
    assert srv.url == "https://studio.example.com" and fa.config()["url"] == "https://studio.example.com"
    srv2 = fa.Server("https://studio.example.com", "fla_x")
    fa.prefer_https(srv2)
    assert srv2.url == "https://studio.example.com"


def test_turn_setup_failure_ends_the_turn_and_frees_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_AGENT_HOME", str(tmp_path / "agent"))
    import flynn_agent as fa
    fa = importlib.reload(fa)
    monkeypatch.setattr(fa, "ROOT", FLYNN)
    srv = FakeServer()
    w = fa.Worker(srv)
    w.run({"id": "j9", "kind": "turn"})                     # no "turn": setup fails before the engine starts
    assert [e["t"] for e in srv.events] == ["error", "turn_end"] and w.job is None
