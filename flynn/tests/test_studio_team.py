"""Studio on a team server (STUDIO_TEAM=1): sign-in through an OIDC provider, each person's own work, agent tokens,
the admin unlock. The provider is faked (no network)."""
import http.client
import importlib
import io
import json
import sys
import tarfile
import threading
import urllib.parse
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))
sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
sys.path.insert(0, str(FLYNN / "scripts"))

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
    monkeypatch.setenv("STUDIO_FRAME_ANCESTORS", "https://idp.example.com")
    monkeypatch.setenv("STUDIO_ADMIN_KEY", "adminkey123")
    monkeypatch.setenv("FLYNN_SYSTEMS_OWNERS", OWNER)
    import team, chat, agent_hub, studio
    for m in (team, chat, studio):
        importlib.reload(m)
    agent_hub.HUB = agent_hub.Hub()
    users = {}

    def fake_post(url, data):
        assert url.endswith("/token") and data["client_secret"] == "s3cret" and data["code_verifier"]
        return {"access_token": "at-" + data["code"]}

    def fake_get(url, tok):
        assert url.endswith("/userinfo")
        return users[tok[3:]]
    monkeypatch.setattr(team, "_post_form", fake_post)
    monkeypatch.setattr(team, "_get_json", fake_get)
    httpd = studio.serve(0, open_browser=False, root=tmp_path / "shared-runs")
    (tmp_path / "shared-runs").mkdir()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def req(method, path, body=None, cookie=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode() if isinstance(body, dict) else body
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.read()
        return r.status, dict(r.getheaders()), out

    def sign_in(code, claims):
        users[code] = claims
        st, h, _ = req("GET", "/auth/login?next=/run/x")
        assert st == 302
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(h["Location"]).query)
        assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["https://studio.example.com/auth/callback"]
        st, h, _ = req("GET", f"/auth/callback?code={code}&state={q['state'][0]}")
        assert st == 302 and h["Location"] == "/run/x"
        ck = h["Set-Cookie"]
        assert "HttpOnly" in ck and "Secure" in ck and "SameSite=None" in ck
        return ck.split(";")[0]

    yield type("S", (), {"req": staticmethod(req), "sign_in": staticmethod(sign_in), "team": team, "chat": chat,
                         "hub": agent_hub, "tmp": tmp_path})
    httpd.shutdown()
    chat.shutdown()
    agent_hub.RUNS_FOR = None
    agent_hub.APPS_FOR = None


def test_gate_and_frame_header(srv):
    st, h, _ = srv.req("GET", "/")
    assert st == 302 and h["Location"].startswith("/auth/login?next=")
    assert srv.req("GET", "/api/runs")[0] == 401
    st, h, body = srv.req("GET", "/healthz")
    assert st == 200 and json.loads(body)["ok"]
    assert "frame-ancestors 'self' https://idp.example.com" in h["Content-Security-Policy"]
    assert "X-Frame-Options" not in h


def test_each_person_sees_only_their_own_runs(srv):
    a = srv.sign_in("a", {"sub": "k1", "sso_sub": "sso-a", "email": "a@example.com", "name": "Sara A"})
    b = srv.sign_in("b", {"sub": "k2", "sso_sub": "sso-b", "email": "b@example.com", "name": "Reza B"})
    pa = srv.team.match({"sub": "k1", "sso_sub": "sso-a"})
    run = srv.team.user_root(pa["uid"]) / "runs" / "2026-10-21-a-run"
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text("<html><body>a</body></html>")
    ids_a = [r["id"] for r in json.loads(srv.req("GET", "/api/runs", cookie=a)[2])]
    ids_b = [r["id"] for r in json.loads(srv.req("GET", "/api/runs", cookie=b)[2])]
    assert ids_a == ["2026-10-21-a-run"] and ids_b == []
    assert srv.req("GET", "/r/2026-10-21-a-run/page/index.html", cookie=b)[0] == 404
    assert srv.req("GET", "/r/2026-10-21-a-run/page/index.html", cookie=a)[0] == 200
    # chats are separate too
    srv.chat.get("home", srv.team.who(type("H", (), {"headers": {"Cookie": a}})())).emit({"t": "user", "text": "mine"})
    st, _, body = srv.req("GET", "/api/chat/state?c=home", cookie=b)
    assert st == 200 and json.loads(body)["events"] == 0


def test_account_found_by_sso_sub_when_sub_changes(srv):
    p1 = srv.team.match({"sub": "old", "sso_sub": "sso-x", "email": "x@example.com"})
    p2 = srv.team.match({"sub": "new", "sso_sub": "sso-x", "email": "x@example.com", "gitlab_id": "77"})
    assert p1["uid"] == p2["uid"] and p2["sub"] == "new" and p2["gitlab_id"] == "77"
    p3 = srv.team.match({"sub": "zzz", "gitlab_id": "77"})
    assert p3["uid"] == p1["uid"]
    # never by email
    p4 = srv.team.match({"sub": "other", "email": "x@example.com"})
    assert p4["uid"] != p1["uid"]


def test_foreign_origin_and_bad_state_refused(srv):
    a = srv.sign_in("a", {"sub": "k1", "email": "a@example.com"})
    st, _, _ = srv.req("POST", "/api/chat/new", {"c": "home"}, cookie=a, headers={"Origin": "https://evil.example"})
    assert st == 403
    assert srv.req("GET", "/auth/callback?code=a&state=forged")[0] == 401


def test_agent_token_only_calls_tools_and_uploads_to_its_person(srv):
    a = srv.sign_in("a", {"sub": "k1", "sso_sub": "sso-a", "email": "a@example.com"})
    tok = srv.hub.issue("a@example.com", "A")
    bearer = {"Authorization": f"Bearer {tok}"}
    assert srv.req("GET", "/api/runs", headers=bearer)[0] == 403
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"<html></html>"
        ti = tarfile.TarInfo("page/index.html")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    import base64
    st, _, _ = srv.req("POST", "/flynn/agent/run-upload", {"run": "2026-10-21-from-agent", "tgz": base64.b64encode(buf.getvalue()).decode()},
                       headers=bearer)
    assert st == 200
    ids = [r["id"] for r in json.loads(srv.req("GET", "/api/runs", cookie=a)[2])]
    assert ids == ["2026-10-21-from-agent"]
    assert not (srv.tmp / "shared-runs" / "2026-10-21-from-agent").exists()


def test_admin_needs_owner_and_key(srv):
    o = srv.sign_in("o", {"sub": "ko", "email": OWNER})
    u = srv.sign_in("u", {"sub": "ku", "email": "u@example.com"})
    org = {"Origin": "https://studio.example.com"}
    assert srv.req("POST", "/api/settings", {"effort": "quick"}, cookie=o, headers=org)[0] == 403
    form = {"Content-Type": "application/x-www-form-urlencoded", **org}
    assert srv.req("POST", "/auth/admin", b"key=adminkey123", cookie=u, headers=form)[0] == 403
    assert srv.req("POST", "/auth/admin", b"key=wrong", cookie=o, headers=form)[0] == 403
    assert srv.req("POST", "/auth/admin", b"key=adminkey123", cookie=o, headers=form)[0] == 302
    assert srv.req("POST", "/api/settings", {"effort": "quick"}, cookie=o, headers=org)[0] == 200
    me = json.loads(srv.req("GET", "/api/me", cookie=o)[2])
    assert me["admin"] is True and me["email"] == OWNER


def test_recorder_off(srv):
    a = srv.sign_in("a", {"sub": "k1", "email": "a@example.com"})
    assert srv.req("GET", "/api/recordings", cookie=a)[0] == 404
    assert srv.req("POST", "/api/record/start", {}, cookie=a, headers={"Origin": "https://studio.example.com"})[0] == 404


def test_sdk_tag_on_pages(srv, monkeypatch):
    monkeypatch.setenv("KARVANSARA_SDK", "https://idp.example.com/sdk/v1/karvansara.global.js")
    a = srv.sign_in("a", {"sub": "k1", "email": "a@example.com"})
    st, _, body = srv.req("GET", "/", cookie=a)
    assert st == 200 and b"karvansara.global.js" in body and b"/static/team.js" in body


def _fake_agent(srv, email, answer):
    """Stand in for a person's connected flynn Studio agent: answer each app job with answer(job)."""
    hub = srv.hub.HUB
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            job = hub.next(email, 0.5, {"host": "test-mac"})
            if job and job.get("kind") == "app":
                hub.events(email, job["id"], [{"t": "result", **answer(job)}])
    threading.Thread(target=loop, daemon=True).start()
    return stop


def test_app_builder_goes_to_the_persons_own_computer(srv):
    a = srv.sign_in("a", {"sub": "k1", "sso_sub": "sso-a", "email": "a@example.com"})
    org = {"Origin": "https://studio.example.com"}
    st, _, body = srv.req("GET", "/api/apps", cookie=a)
    assert st == 503 and json.loads(body)["offline"]                  # computer not connected
    seen = []

    def answer(job):
        seen.append((job["op"], job.get("name"), job.get("args")))
        if job["op"] == "create":
            return {"ok": True, "data": {"name": job["args"]["name"], "preview": {}}}
        if job["op"] == "preview/start":
            return {"ok": True, "data": {"port": 5201, "url": "http://127.0.0.1:5201/"}}
        if job["op"] == "info":
            return {"ok": False, "kind": "key", "error": "no app named zz"}
        return {"ok": True, "data": {"apps": []}}
    stop = _fake_agent(srv, "a@example.com", answer)
    try:
        st, _, body = srv.req("POST", "/api/apps", {"name": "notes", "description": "Take notes"}, cookie=a, headers=org)
        assert st == 201 and json.loads(body)["name"] == "notes"
        st, _, body = srv.req("POST", "/api/apps/notes/preview/start", {}, cookie=a, headers=org)
        assert st == 200 and json.loads(body)["url"] == "http://127.0.0.1:5201/"
        assert srv.req("GET", "/api/apps/zz", cookie=a)[0] == 404
        assert srv.req("GET", "/app/notes", cookie=a)[0] == 200
    finally:
        stop.set()
    assert seen[0][0] == "create" and seen[1][:2] == ("preview/start", "notes")


def test_app_snapshot_kept_in_the_persons_folder(srv):
    srv.sign_in("a", {"sub": "k1", "sso_sub": "sso-a", "email": "a@example.com"})
    tok = srv.hub.issue("a@example.com", "A")
    bearer = {"Authorization": f"Bearer {tok}"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b'{"name": "notes"}'
        ti = tarfile.TarInfo("app.json")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    import base64
    st, _, _ = srv.req("POST", "/flynn/agent/app-upload", {"name": "notes", "tgz": base64.b64encode(buf.getvalue()).decode()}, headers=bearer)
    assert st == 200
    p = srv.team.match({"sub": "k1", "sso_sub": "sso-a"})
    assert (srv.team.user_root(p["uid"]) / "apps" / "notes.tgz").is_file()
    st, _, got = srv.req("GET", "/flynn/agent/app-download?name=notes", headers=bearer)
    assert st == 200 and got == buf.getvalue()
    assert srv.req("GET", "/flynn/agent/app-download?name=../x", headers=bearer)[0] == 404


def test_package_cache_needs_a_person_or_their_agent(srv, monkeypatch, tmp_path):
    import pkgcache
    monkeypatch.setenv("STUDIO_CACHE_DIR", str(tmp_path / "pc"))
    monkeypatch.setattr(pkgcache, "npm_meta", lambda path, accept: (_write(tmp_path / "m.json", b'{"name": "x"}'), {"content-type": "application/json"}))
    assert srv.req("GET", "/npm/-/ping")[0] == 401                      # no anonymous proxy
    srv.sign_in("a", {"sub": "k1", "sso_sub": "sso-a", "email": "a@example.com"})
    tok = srv.hub.issue("a@example.com", "A")
    st, _, body = srv.req("GET", "/npm/-/ping", headers={"Authorization": f"Bearer {tok}"})
    assert st == 200 and json.loads(body)["ok"]
    st, _, body = srv.req("GET", "/npm/left-pad", headers={"Authorization": f"Bearer {tok}"})
    assert st == 200 and json.loads(body)["name"] == "x"
    assert srv.req("GET", "/flynn/agent/node/manifest.json")[0] == 404  # public, but nothing cached in tests


def _write(p, data):
    p.write_bytes(data)
    return p
