"""The team server's agent route: sign-in gate, connect command, a turn handed to the person's agent and back,
run folder upload, and the bundle."""
import base64
import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_PANEL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_PANEL_CONTEXT", "0")
    monkeypatch.setenv("FLYNN_ENGINE", "remote")
    monkeypatch.setenv("FLYNN_REQUIRE_AUTH", "1")
    monkeypatch.setenv("FLYNN_PUBLIC_URL", "https://desk.example")
    monkeypatch.delenv("FLYNN_DB_URL", raising=False)
    import session_auth
    import agent_hub
    people = {"good-cookie": {"id": "u1", "email": "sara@example.com", "name": "Sara"}}
    monkeypatch.setattr(session_auth, "_fetch_profile", lambda tok: people.get(tok))
    monkeypatch.setattr(agent_hub, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(agent_hub, "HUB", agent_hub.Hub())
    spec = importlib.util.spec_from_file_location("panel_server", FLYNN / "adapters/penpot/panel/panel_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = mod.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{srv.server_port}", tmp_path, agent_hub
    srv.shutdown()


def call(base, path, body=None, cookie=None, token=None, raw=False):
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = f"auth-token={cookie}"
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None, h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
            return (r.status, data) if raw else (r.status, json.loads(data or b"{}"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_sign_in_is_required_on_the_team_server(team):
    _, base, _, _ = team
    assert call(base, "/flynn/api/health")[0] == 200
    assert call(base, "/flynn/api/history?file=f1")[0] == 401
    assert call(base, "/flynn/api/history?file=f1", cookie="stolen")[0] == 401
    st, me = call(base, "/flynn/api/me", cookie="good-cookie")
    assert st == 200 and me["email"] == "sara@example.com" and me["must_change_password"] is False
    assert call(base, "/flynn/api/settings", {"engine": "local"}, cookie="good-cookie")[0] == 403


def test_connect_command_and_agent_token(team):
    _, base, _, hub = team
    st, out = call(base, "/flynn/agent/token", {}, cookie="good-cookie")
    assert st == 200 and out["command"].startswith("curl -fsSL https://desk.example/flynn/agent/install.sh | bash -s -- fla_")
    tok = out["command"].rsplit(" ", 1)[1]
    st, hello = call(base, "/flynn/agent/hello", token=tok)
    assert st == 200 and hello["email"] == "sara@example.com"
    assert call(base, "/flynn/agent/hello", token="fla_nope")[0] == 401
    assert call(base, "/flynn/agent/hello", cookie="good-cookie")[0] == 401      # agent routes need the token
    call(base, "/flynn/agent/token", {}, cookie="good-cookie")                   # a new command retires the old one
    assert call(base, "/flynn/agent/hello", token=tok)[0] == 401
    st, raw = call(base, "/flynn/agent/install.sh", raw=True)
    assert st == 200 and b"https://desk.example" in raw and b"__FLYNN_URL__" not in raw


def test_a_turn_runs_on_the_persons_agent(team):
    _, base, tmp, hub = team
    # no agent connected: the tab says how to connect
    call(base, "/flynn/api/send", {"file": "f1", "text": "hello"}, cookie="good-cookie")
    for _ in range(50):
        h = call(base, "/flynn/api/history?file=f1", cookie="good-cookie")[1]
        if not h["running"] and any(e["t"] == "turn_end" for e in h["events"]):
            break
        time.sleep(0.1)
    assert any(e["t"] == "error" and "agent/connect" in e["text"] for e in h["events"])

    tok = call(base, "/flynn/agent/token", {}, cookie="good-cookie")[1]["command"].rsplit(" ", 1)[1]
    got = {}

    def agent():
        for _ in range(3):
            st, j = call(base, "/flynn/agent/next?wait=5&host=mac", token=tok)
            job = j.get("job")
            if job and job["kind"] == "turn":
                got["job"] = job
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                    data = b"<html>hi</html>"
                    ti = tarfile.TarInfo("page/index.html"); ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
                    evil = tarfile.TarInfo("../escape.txt"); evil.size = 1
                    tf.addfile(evil, io.BytesIO(b"x"))
                st, up = call(base, "/flynn/agent/run-upload",
                              {"run": "2026-10-06-test-run", "tgz": base64.b64encode(buf.getvalue()).decode()}, token=tok)
                got["upload"] = up
                call(base, "/flynn/agent/events", {"job": job["id"], "events": [
                    {"t": "delta", "text": "Hi from "}, {"t": "delta", "text": "your Mac"},
                    {"t": "turn_end", "ok": True, "cost": 0.01}]}, token=tok)
                return
    t = threading.Thread(target=agent, daemon=True)
    t.start()
    for _ in range(50):                                   # wait until the agent has called in once
        if hub.HUB.connected("sara@example.com"):
            break
        time.sleep(0.1)
    call(base, "/flynn/api/send", {"file": "f1", "text": "make a card"}, cookie="good-cookie")
    t.join(20)
    for _ in range(100):
        h = call(base, "/flynn/api/history?file=f1", cookie="good-cookie")[1]
        if not h["running"] and sum(e["t"] == "turn_end" for e in h["events"]) >= 2:
            break
        time.sleep(0.1)
    assert "make a card" in got["job"]["turn"]["prompt"]
    assert got["job"]["root"] == str(FLYNN)
    assert "".join(e.get("text", "") for e in h["events"] if e["t"] == "delta") == "Hi from your Mac"
    assert h["events"][-1]["t"] == "turn_end" and h["events"][-1]["ok"] is True
    assert got["upload"]["files"] == 1
    assert (tmp / "runs/2026-10-06-test-run/page/index.html").read_text() == "<html>hi</html>"
    assert not (tmp / "runs/escape.txt").exists()


def test_bundle_has_project_level_agents_with_local_paths(tmp_path, monkeypatch):
    import agent_hub
    root = tmp_path / "flynn"
    (root / "agents").mkdir(parents=True)
    (root / "agents/flynn.md").write_text(f"`$FLYNN` = `{agent_hub.HOME_PATH}`.")
    (root / "skills/flynn-core").mkdir(parents=True)
    (root / "skills/flynn-core/SKILL.md").write_text(f"see {agent_hub.HOME_PATH}/kb")
    (root / "scripts").mkdir()
    (root / "scripts/x.py").write_text("print(1)")
    (root / "adapters/penpot/stack").mkdir(parents=True)
    (root / "adapters/penpot/stack/big.bin").write_bytes(b"0" * 10)
    data = agent_hub.build_bundle(root)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = set(tf.getnames())
        agent = tf.extractfile(".claude/agents/flynn.md").read().decode()
    assert {"agents/flynn.md", ".claude/agents/flynn.md", ".claude/skills/flynn-core/SKILL.md", "scripts/x.py", "VERSION"} <= names
    assert "adapters/penpot/stack/big.bin" not in names
    assert "$HOME/.flynn-agent/flynn" in agent and agent_hub.HOME_PATH not in agent


def test_a_busy_backend_does_not_sign_people_out(monkeypatch):
    import session_auth
    monkeypatch.setattr(session_auth, "_cache", {})
    calls = {"n": 0}

    def fetch(tok):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "u1", "email": "sara@example.com", "name": "Sara"}
        raise OSError("timed out")
    assert session_auth.profile_for_cookie("c1", fetch)["email"] == "sara@example.com"
    monkeypatch.setattr(session_auth, "TTL", 0.0)                 # force a re-check
    assert session_auth.profile_for_cookie("c1", fetch)["email"] == "sara@example.com"   # stale but valid
    assert session_auth.profile_for_cookie("never-valid", fetch) is None


def test_a_turn_whose_end_was_lost_is_closed(monkeypatch):
    import types
    import agent_hub
    monkeypatch.setattr(agent_hub, "ORPHAN", 0.0)
    hub = agent_hub.Hub()
    hub.seen["sara@example.com"] = time.time()
    eng = agent_hub.RemoteEngine(hub)
    events = []
    turn = types.SimpleNamespace(user="sara@example.com", prompt="hi", session=None, system="", allow=[], deny=[],
                                 file_id="f1", env={})

    def agent():                                    # takes the job, then reports idle without ever sending its end
        job = hub.next("sara@example.com", 5)
        time.sleep(0.2)
        hub.next("sara@example.com", 0, {"busy": "", "at": time.time() + 1})
    threading.Thread(target=agent, daemon=True).start()
    monkeypatch.setattr(agent_hub.threading.Event, "wait", lambda self, t=None: (time.sleep(0.3), self.is_set())[1])
    eng.run(turn, events.append)
    assert events[-1]["t"] == "turn_end" and "didn't reach" in events[-2]["text"]


def test_remote_recording_runs_on_the_agent_and_comes_back(tmp_path):
    """On the team server a recording runs on the person's computer (the recorder needs their screen): the server
    queues it for their agent, relays Snap and Stop, receives the folder, and points page folders at its copy."""
    sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
    import agent_hub
    hub = agent_hub.Hub()

    class Local:                      # stands in for recorder.Session: only names the id and folder
        id, folder, cfg = "2026-10-07-120000-example", tmp_path / "server" / "rec", {
            "system": "shop", "url": "https://shop.example", "how": "watch", "max_pages": 3, "sizes": [1440],
            "target": {"mode": "current"}, "file": "f1"}

    r = agent_hub.RemoteRecording(Local(), "Sara@example.com", hub=hub)
    with pytest.raises(RuntimeError, match="not connected"):
        r.start()
    hub.next("sara@example.com", 0)                       # the agent calls in
    r.start()
    job = hub.next("sara@example.com", 0)
    assert job["kind"] == "record" and job["session"] == Local.id and "target" not in job["cfg"]
    r.capture_now()
    r.stop()
    assert [hub.next("sara@example.com", 0)["kind"] for _ in range(2)] == ["rec-snap", "rec-stop"]

    agent_root = "/Users/sara/.flynn-agent/recordings/shop/" + Local.id
    page = {"n": 1, "url": "https://shop.example/", "folder": agent_root + "/pages/01-home"}
    assert hub.events("sara@example.com", r.job, [{"t": "rec-status", "state": "recording", "pages": [page]}])
    assert r.status()["pages"] == 1 and not r.done.is_set()
    assert not hub.events("someone@example.com", r.job, [{"t": "rec-done"}])     # only its owner's agent

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in {"session.json": json.dumps({"id": Local.id, "pages": [page]}).encode(),
                           "pages/01-home/w1440/full.png": b"png"}.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        evil = tarfile.TarInfo("../escape.txt")
        evil.size = 1
        tf.addfile(evil, io.BytesIO(b"x"))
    assert r.receive(buf.getvalue()) == 2
    assert not (tmp_path / "server" / "escape.txt").exists()
    hub.events("sara@example.com", r.job, [{"t": "rec-done", "root": agent_root}])
    r._thread.join(10)
    assert r.done.is_set() and r.status()["state"] == "stopped"
    assert r.pages[0]["folder"] == str(Local.folder / "pages/01-home")
    assert r.job not in hub.records


def test_live_connection_carries_jobs_and_acknowledged_events(team):
    """The agent's WebSocket: a turn arrives the moment it is queued, events come back with a receipt, and a batch
    resent over HTTPS (its receipt was lost) is not shown twice."""
    sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
    import wsproto
    _, base, _, hub = team
    tok = call(base, "/flynn/agent/token", {}, cookie="good-cookie")[1]["command"].rsplit(" ", 1)[1]
    with pytest.raises(ConnectionRefusedError, match="401"):
        wsproto.connect(base + "/flynn/agent/ws", {"Authorization": "Bearer fla_wrong"})
    ws = wsproto.connect(base + "/flynn/agent/ws", {"Authorization": f"Bearer {tok}"})
    ws.settimeout(10)
    ws.send_json({"type": "hello", "host": "mac", "version": "1", "busy": ""})
    for _ in range(50):
        if hub.HUB.info.get("sara@example.com", {}).get("host") == "mac":
            break
        time.sleep(0.05)
    assert hub.HUB.connected("sara@example.com") and hub.HUB.info["sara@example.com"]["transport"] == "websocket"

    started = time.time()
    call(base, "/flynn/api/send", {"file": "f2", "text": "draw a card"}, cookie="good-cookie")
    m = ws.recv_json()
    while m.get("type") != "job":
        m = ws.recv_json()
    assert time.time() - started < 3                       # pushed, not polled
    job = m["job"]
    assert job["kind"] == "turn" and "draw a card" in job["turn"]["prompt"]
    ws.send_json({"type": "status", "host": "mac", "version": "1", "busy": job["id"]})
    ws.send_json({"type": "events", "seq": 1, "bid": "b1", "job": job["id"],
                  "events": [{"t": "delta", "text": "Live "}, {"t": "delta", "text": "text"}]})
    ack = ws.recv_json()
    while ack.get("type") != "ack":
        ack = ws.recv_json()
    assert ack == {"type": "ack", "seq": 1, "ok": True}
    # the same batch again over HTTPS: accepted, not applied twice
    assert call(base, "/flynn/agent/events", {"job": job["id"], "bid": "b1", "events": [
        {"t": "delta", "text": "Live "}, {"t": "delta", "text": "text"}]}, token=tok)[0] == 200
    ws.send_json({"type": "events", "seq": 2, "bid": "b2", "job": job["id"],
                  "events": [{"t": "turn_end", "ok": True, "cost": 0.01}]})
    for _ in range(100):
        h = call(base, "/flynn/api/history?file=f2", cookie="good-cookie")[1]
        if not h["running"] and any(e["t"] == "turn_end" for e in h["events"]):
            break
        time.sleep(0.05)
    assert "".join(e.get("text", "") for e in h["events"] if e["t"] == "delta") == "Live text"
    ws.close()


def test_job_taken_for_a_closed_connection_is_not_lost():
    sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
    import agent_hub
    hub = agent_hub.Hub()
    hub.put("a@x", {"id": "1"})
    hub.put("a@x", {"id": "2"})
    j = hub.next("a@x", 0)
    hub.unshift("a@x", j)
    assert [hub.next("a@x", 0)["id"], hub.next("a@x", 0)["id"]] == ["1", "2"]


def test_panel_live_connection(team):
    """The chat panel's WebSocket: past and new events come down in order, an action goes up and is answered with
    the caller's own sign-in, the selection is pushed, and a panel that isn't signed in is refused."""
    sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
    import wsproto
    _, base, _, _ = team
    with pytest.raises(ConnectionRefusedError, match="401"):
        wsproto.connect(base + "/flynn/api/ws?file=f4&since=0")
    call(base, "/flynn/api/command", {"file": "f4", "name": "settings"}, cookie="good-cookie")
    ws = wsproto.connect(base + "/flynn/api/ws?file=f4&since=0", {"Cookie": "auth-token=good-cookie"})
    ws.settimeout(10)
    ws.send_json({"type": "visible", "on": True})
    seen = {"event": [], "selection": []}

    def until(pred, n=50):
        for _ in range(n):
            m = ws.recv_json()
            if m.get("type") in seen:
                seen[m["type"]].append(m)
            if pred(m):
                return m
        raise AssertionError("not received")
    until(lambda m: m.get("type") == "event" and m["ev"]["t"] == "settings")
    assert seen["event"][0]["id"] == 0
    ws.send_json({"type": "call", "id": 7, "method": "POST", "path": "/api/command", "body": {"file": "f4", "name": "usage"}})
    r = until(lambda m: m.get("type") == "reply")
    assert r["id"] == 7 and r["status"] == 200 and r["body"]["ok"] is True
    if not any(m["ev"]["t"] == "info" for m in seen["event"]):              # the action's result, pushed
        until(lambda m: m.get("type") == "event" and m["ev"]["t"] == "info")
    if not seen["selection"]:
        until(lambda m: m.get("type") == "selection")
    ws.send_json({"type": "call", "id": 8, "method": "POST", "path": "/agent/token", "body": {}})
    assert until(lambda m: m.get("type") == "reply")["status"] == 400        # only /api routes
    ws.close()


def test_upload_in_pieces_is_put_back_together(tmp_path):
    """A proxy refuses requests over ~1 MB, so the agent sends big uploads in pieces (2026-10-10)."""
    import base64
    import io
    import os
    import tarfile
    import agent_hub as H
    src = tmp_path / "src"
    (src / "page" / "img").mkdir(parents=True)
    (src / "page" / "img" / "big.png").write_bytes(os.urandom(1_500_000))   # random: gzip can't shrink it
    (src / "page" / "index.html").write_text("<p>hi</p>")
    data = H.pack_dir(src)
    piece = 600_000
    n = -(-len(data) // piece)
    assert n >= 3
    out = None
    for i in reversed(range(n)):                                  # any order
        b = {"tgz": base64.b64encode(data[i * piece:(i + 1) * piece]).decode(), "upload": "ab12cd34", "part": i, "parts": n}
        r = H.assemble("the user@x.com", b)
        assert (r is None) == (i != 0)
        out = r or out
    assert out == data
    assert H.unpack_into(tmp_path / "dest", out) == 2
    assert (tmp_path / "dest" / "page" / "img" / "big.png").stat().st_size == 1_500_000
    assert H.assemble("a@x.com", {"tgz": base64.b64encode(b"x").decode()}) == b"x"   # whole uploads still work
    import pytest
    with pytest.raises(ValueError):
        H.assemble("a@x.com", {"tgz": "", "upload": "../../etc", "part": 0, "parts": 2})
