"""The maintenance experience: the startup maintenance server, Studio taking its port over, the admin announcement
routes, and the page being self-contained."""
import http.client
import importlib
import importlib.util
import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
STUDIO = FLYNN / "adapters" / "studio"
sys.path.insert(0, str(STUDIO))
sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
sys.path.insert(0, str(FLYNN / "scripts"))

import maintenance  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port, path, method="GET", body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()


@pytest.fixture
def maint():
    httpd = maintenance.make_server("127.0.0.1", 0, since=1234.5)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_every_path_is_the_503_page(maint):
    for method in ("GET", "POST", "DELETE"):
        for path in ("/", "/run/abc", "/api/runs?x=1", "/auth/login"):
            st, h, body = get(maint, path, method, b"{}" if method == "POST" else None)
            assert st == 503
            assert h["retry-after"] == "30" and h["x-flynn-maintenance"] == "1" and h["cache-control"] == "no-store"
            assert b"Flynn is being updated" in body and h["content-type"].startswith("text/html")


def test_healthz_and_status(maint):
    st, h, body = get(maint, "/healthz")
    assert st == 200 and json.loads(body) == {"ok": True, "maintenance": True}
    st, h, body = get(maint, "/__maintenance/status")
    j = json.loads(body)
    assert st == 200 and j["maintenance"] is True and j["since"] == 1234.5 and j["message"]
    assert h["cache-control"] == "no-store"


def test_assets_are_served_and_nothing_else_in_static(maint):
    st, h, body = get(maint, "/static/flynn-logo.png")
    assert st == 200 and h["content-type"] == "image/png" and body[:4] == b"\x89PNG"
    assert get(maint, "/static/geist.woff2")[0] == 200
    assert get(maint, "/static/chat.js")[0] == 503            # only the fixed asset list
    assert get(maint, "/static/../maintenance.py")[0] == 503


def test_page_is_self_contained():
    page = (STUDIO / "static" / "maintenance.html").read_text()
    assert not re.search(r"https?://|//cdn|@import", page)
    for ref in re.findall(r'(?:src|href)="([^"]+)"', page):
        assert ref.startswith("/static/") and ref[8:] in maintenance.ASSETS, ref
    for ref in re.findall(r'url\("([^"]+)"\)', page):
        assert ref[8:] in maintenance.ASSETS, ref
    assert "/__maintenance/status" in page and "X-Flynn-Maintenance" in page


def _start_maintenance(tmp_path, port):
    pidfile = tmp_path / "m.pid"
    p = subprocess.Popen([sys.executable, str(STUDIO / "maintenance.py"), "--host", "127.0.0.1", "--port", str(port),
                          "--pidfile", str(pidfile)], stdout=subprocess.DEVNULL)
    t0 = time.time()
    while not pidfile.exists() and time.time() - t0 < 5:
        time.sleep(0.02)
    return p, pidfile, time.time() - t0


def test_maintenance_script_starts_fast_and_stops_on_sigterm(tmp_path):
    port = free_port()
    p, pidfile, took = _start_maintenance(tmp_path, port)
    try:
        assert pidfile.exists() and took < 1.5
        assert int(pidfile.read_text()) == p.pid and get(port, "/healthz")[0] == 200
    finally:
        p.send_signal(signal.SIGTERM)
        assert p.wait(5) == 0
    with pytest.raises(OSError):
        get(port, "/healthz")


def test_studio_takes_the_port_over_within_two_seconds(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_TEAM", raising=False)
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    port = free_port()
    p, pidfile, _ = _start_maintenance(tmp_path, port)
    httpd = None
    import chat
    import studio
    try:
        st, h, body = get(port, "/healthz")                          # the maintenance server holds the port
        assert json.loads(body)["maintenance"] is True
        (tmp_path / "runs").mkdir()
        t0 = time.time()
        httpd = studio.serve(port, open_browser=False, root=tmp_path / "runs", host="127.0.0.1", takeover_pidfile=str(pidfile))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        st, h, body = get(port, "/api/runs")
        assert st == 200 and time.time() - t0 < 2 and "x-flynn-maintenance" not in h
        assert p.wait(3) == 0
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if p.poll() is None:
            p.kill()
        chat.shutdown()


def test_takeover_with_a_stale_pidfile_still_binds(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_TEAM", raising=False)
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    (tmp_path / "runs").mkdir()
    (tmp_path / "gone.pid").write_text("999999")
    import chat
    import studio
    httpd = studio.serve(0, open_browser=False, root=tmp_path / "runs", takeover_pidfile=str(tmp_path / "gone.pid"))
    httpd.server_close()
    chat.shutdown()


# ---------------------------------------------------------------- announcement routes (a team server)
OWNER = "owner@example.com"


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_TEAM", "1")
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(tmp_path / "shared-runs"))
    monkeypatch.setenv("STUDIO_PUBLIC_URL", "https://studio.example.com")
    monkeypatch.setenv("KARVANSARA_ISSUER", "https://idp.example.com/api/oauth")
    monkeypatch.setenv("KARVANSARA_CLIENT_ID", "studio")
    monkeypatch.setenv("KARVANSARA_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("STUDIO_ADMIN_KEY", "adminkey123")
    monkeypatch.setenv("FLYNN_SYSTEMS_OWNERS", OWNER)
    import team, chat, agent_hub, studio
    for m in (team, chat, studio):
        importlib.reload(m)
    agent_hub.HUB = agent_hub.Hub()
    (tmp_path / "shared-runs").mkdir()
    users = {}
    monkeypatch.setattr(team, "_post_form", lambda url, data: {"access_token": "at-" + data["code"]})
    monkeypatch.setattr(team, "_get_json", lambda url, tok: users[tok[3:]])
    httpd = studio.serve(0, open_browser=False, root=tmp_path / "shared-runs")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def sign_in(code, claims):
        users[code] = claims
        st, h, _ = get(port, "/auth/login?next=/")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(h["location"]).query)
        st, h, _ = get(port, f"/auth/callback?code={code}&state={q['state'][0]}")
        return h["set-cookie"].split(";")[0]

    yield type("S", (), {"get": staticmethod(lambda *a, **k: get(port, *a, **k)), "sign_in": staticmethod(sign_in),
                         "team": team, "tmp": tmp_path, "studio": studio, "port": port})
    httpd.shutdown()
    chat.shutdown()
    agent_hub.RUNS_FOR = None
    agent_hub.APPS_FOR = None


KEY = {"X-Studio-Admin-Key": "adminkey123"}


def post(srv, body, **h):
    return srv.get("/admin/maintenance", "POST", json.dumps(body).encode(), {"Content-Type": "application/json", **h})


def test_announce_is_admin_only(srv):
    assert post(srv, {"minutes": 3})[0] == 403                           # nobody signed in, no key
    assert post(srv, {"minutes": 3}, **{"X-Studio-Admin-Key": "wrong"})[0] == 403
    user = srv.sign_in("a", {"sub": "k1", "sso_sub": "s-a", "email": "a@example.com", "name": "A"})
    assert post(srv, {"minutes": 3}, Cookie=user)[0] == 403             # signed in, not admin
    assert srv.get("/admin/maintenance", "DELETE", headers={"Cookie": user})[0] == 403
    assert srv.get("/api/maintenance", headers={"Cookie": user})[2] == b"{}"


def test_announce_then_everyone_reads_it_then_clear(srv):
    st, _, body = post(srv, {"minutes": 2}, **KEY)
    assert st == 200 and json.loads(body)["ok"]
    user = srv.sign_in("a", {"sub": "k1", "sso_sub": "s-a", "email": "a@example.com", "name": "A"})
    j = json.loads(srv.get("/api/maintenance", headers={"Cookie": user})[2])
    assert j["minutes"] == 2 and j["ends_at"] - j["at"] == 120 and j["now"] >= j["at"]
    assert (srv.tmp / "home" / "maintenance.json").exists()               # kept in FLYNN_HOME
    assert srv.get("/api/maintenance")[0] == 401                         # normal sign-in still applies
    assert srv.get("/admin/maintenance", "DELETE", headers=KEY)[0] == 200
    assert srv.get("/api/maintenance", headers={"Cookie": user})[2] == b"{}"
    assert not (srv.tmp / "home" / "maintenance.json").exists()


def test_announce_validates_minutes(srv):
    assert post(srv, {"minutes": 0}, **KEY)[0] == 400
    assert post(srv, {"minutes": 500}, **KEY)[0] == 400
    assert post(srv, {"minutes": "soon"}, **KEY)[0] == 400


def test_announcement_expires_ten_minutes_after_it_is_due(srv, monkeypatch):
    team = srv.team
    team.maintenance_set("", 1)
    assert team.maintenance_get()["minutes"] == 1
    real = time.time
    monkeypatch.setattr(team.time, "time", lambda: real() + 60 + team.MAINT_GRACE - 5)
    assert team.maintenance_get()                                         # still showing (the update runs late)
    monkeypatch.setattr(team.time, "time", lambda: real() + 60 + team.MAINT_GRACE + 5)
    assert team.maintenance_get() == {}
    assert not (srv.tmp / "home" / "maintenance.json").exists()


def test_a_new_server_clears_a_stale_announcement(srv, tmp_path):
    srv.team.maintenance_set("", 5)
    assert (tmp_path / "home" / "maintenance.json").exists()
    (tmp_path / "r2").mkdir()
    srv.studio.serve(0, open_browser=False, root=tmp_path / "r2").server_close()
    assert srv.team.maintenance_get() == {}


def test_announce_script_never_prints_the_key(srv, tmp_path, capsys):
    script = FLYNN / "deploy" / "studio" / "announce.py"
    if not script.exists():                       # the public copy leaves the team-server deploy files out
        pytest.skip("deploy/studio is not in this copy")
    spec = importlib.util.spec_from_file_location("announce", script)
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    url = f"http://127.0.0.1:{srv.port}"
    kf = tmp_path / "key"
    kf.write_text("adminkey123\n")
    assert ann.main([url, "--minutes", "4", "--key-file", str(kf)]) == 0
    assert srv.team.maintenance_get()["minutes"] == 4
    assert ann.main([url, "--clear", "--key-file", str(kf)]) == 0
    assert srv.team.maintenance_get() == {}
    kf.write_text("wrong\n")
    assert ann.main([url, "--minutes", "4", "--key-file", str(kf)]) == 1
    out = capsys.readouterr()
    assert "adminkey123" not in out.out + out.err and "wrong" not in out.out + out.err
    assert ann.main([url, "--key-file", str(tmp_path / "missing")]) == 2
