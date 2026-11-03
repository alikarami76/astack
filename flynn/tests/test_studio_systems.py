"""Studio's systems module: listing, recording (with a fake Session, never a browser) and its HTTP routes."""
import importlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))
sys.path.insert(0, str(FLYNN / "kb"))


@pytest.fixture
def sy(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_STUDIO_CHATS", str(tmp_path / "chats"))
    monkeypatch.setenv("FLYNN_RECORDINGS_DIR", str(tmp_path / "rec"))
    import systems as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "SYSTEMS_DIR", write_fixture_system(tmp_path / "systems"))
    monkeypatch.setattr(mod, "THEMES_DIR", write_fixture_themes(tmp_path / "themes"))
    mod._ACTIVE.clear()
    yield mod
    mod._ACTIVE.clear()


def add_rows():
    import store
    c = store.connect()
    for name, tier in (("Button", "code-only"), ("Card", "verified"), ("Chip", "code-only")):
        store.upsert(c, "component", {"system": "admin-panel", "name": name, "tier": tier,
                                      "description": "d" * 300})
    store.upsert(c, "pattern", {"system": "admin-panel", "name": "Empty state", "description": "x"})
    store.upsert(c, "token", {"system": "admin-panel", "name": "color.primary", "kind": "color", "level": "raw",
                              "value": "#00f", "tier": "code-only"})
    store.upsert(c, "component", {"system": "your company", "name": "Header", "tier": "verified"})


class FakeSession:
    made = []

    def __init__(self, cfg):
        self.cfg, self.state, self.pages, self.error = dict(cfg), "starting", [], ""
        self.id = "2026-10-20-120000-example-com"
        self.snaps = 0
        FakeSession.made.append(self)

    def start(self):
        self.state = "recording"
        return self

    def capture_now(self):
        self.snaps += 1

    def stop(self, wait=60):
        self.state = "stopped"


@pytest.fixture
def fake(sy, monkeypatch):
    FakeSession.made = []
    monkeypatch.setattr(sy, "_recorder", lambda: type("R", (), {"Session": FakeSession}))
    return sy


GOOD = {"url": "https://seller.example.com/home", "system": "my-product", "mode": "watch", "rights": True}


# ---- listing
def test_list_systems_combines_store_folders_and_themes(sy):
    add_rows()
    items = {i["id"]: i for i in sy.list_systems()}
    sc = items["admin-panel"]
    assert sc["kind"] == "library"
    assert sc["counts"]["component"] == {"code-only": 2, "verified": 1}
    assert sc["counts"]["pattern"] == {"none": 1}
    assert sc["counts"]["token"] == {"code-only": 1}
    assert sc["total"] == 5
    assert sc["guide"].endswith("admin-panel/GUIDE.md")
    assert sc["source"]["repo"] == "web-app" and len(sc["source"]["commit"]) >= 7
    assert sc["recordings"] == 0 and isinstance(sc["proposals"], int)
    assert items["your company"]["kind"] == "library" and items["your company"]["total"] == 1   # store only
    assert items["editorial"]["kind"] == "theme" and items["dk-mckinsey"]["guide"].endswith("README.md")
    assert "dk-mckinsey" in items


def test_system_detail(sy):
    add_rows()
    d = sy.system_detail("admin-panel")
    assert {c["name"] for c in d["components"]} == {"Button", "Card", "Chip"}
    assert all(len(c["description"]) <= 140 for c in d["components"])
    assert d["patterns"][0]["name"] == "Empty state"
    assert d["tokens"] == [{"name": "color.primary", "value": "#00f", "tier": "code-only"}]
    assert 0 < len(d["guide_excerpt"].splitlines()) <= 60
    t = sy.system_detail("editorial")
    assert "style.css" in t["theme_files"]


def test_unknown_system(sy):
    with pytest.raises(KeyError):
        sy.system_detail("nope")


# ---- recording
@pytest.mark.parametrize("patch", [{"url": "ftp://x.com"}, {"url": "seller.example.com"}, {"rights": False},
                                   {"rights": "yes"}, {"system": "Bad Slug"}, {"system": "a"}, {"mode": "scan"},
                                   {"widths": [10]}, {"widths": "1440"}])
def test_start_validation(fake, patch):
    with pytest.raises(ValueError):
        fake.start_recording({**GOOD, **patch})
    assert FakeSession.made == []


def test_start_state_capture_stop(fake):
    assert fake.recording_state() == {"state": "idle"}
    st = fake.start_recording({**GOOD, "widths": [1440, 390]})
    assert st["state"] == "recording" and st["system"] == "my-product" and st["mode"] == "watch"
    cfg = FakeSession.made[0].cfg
    assert cfg["how"] == "watch" and cfg["sizes"] == [1440, 390] and cfg["url"] == GOOD["url"]
    with pytest.raises(RuntimeError):
        fake.start_recording(GOOD)
    fake.capture_now()
    assert FakeSession.made[0].snaps == 1
    assert fake.stop_recording()["state"] == "stopped"
    with pytest.raises(RuntimeError):
        fake.stop_recording()
    with pytest.raises(RuntimeError):
        fake.capture_now()
    fake.start_recording(GOOD)                      # a finished one does not block the next
    assert len(FakeSession.made) == 2


def make_session(sy, system="my-product", session="2026-10-20-120000-example-com"):
    d = sy.recordings_root() / system / session
    shot = d / "pages" / "001-home" / "w1440"
    shot.mkdir(parents=True)
    (shot / "viewport.png").write_bytes(b"\x89PNG-fake")
    (shot / "dom.json").write_text("{}")
    (d / "session.json").write_text(json.dumps({
        "id": session, "system": system, "how": "crawl", "url": "https://x.com/", "state": "stopped",
        "pages": [{"title": "Home", "url": "https://x.com/", "folder": str(d / "pages" / "001-home"),
                   "time": "2026-10-20T12:00:05"}]}))
    (sy.recordings_root() / "secret.txt").write_text("no")
    return d


def test_list_recordings_and_counts(sy):
    make_session(sy)
    make_session(sy, "other", "2026-10-21-090000-y")
    recs = sy.list_recordings()
    assert [r["session"] for r in recs] == ["2026-10-21-090000-y", "2026-10-20-120000-example-com"]
    r = sy.list_recordings("my-product")[0]
    assert r["mode"] == "crawl" and r["pages"][0]["thumb"] == "pages/001-home/w1440/viewport.png"
    assert r["started"] == "2026-10-20T12:00:05"
    assert sy.list_recordings("../x") == []
    assert sy._sessions("my-product") == 1


def test_recording_file_safety(sy):
    make_session(sy)
    s = "2026-10-20-120000-example-com"
    assert sy.recording_file("my-product", s, "pages/001-home/w1440/dom.json").read_text() == "{}"
    for args in (("my-product", s, "../../secret.txt"), ("my-product", s, "/etc/passwd"), ("my-product", s, "pages"),
                 ("my-product", "..", "secret.txt"), ("..", s, "x"), ("my-product", s, ""), ("my-product/x", s, "a"),
                 ("my-product", s, "nope.png")):
        with pytest.raises(KeyError):
            sy.recording_file(*args)


# ---- health
def test_health_format(sy):
    checks = sy.health()
    assert {c["id"] for c in checks} == {"selenium", "chrome"}
    assert all(set(c) == {"id", "label", "status", "detail", "fix"} for c in checks)


# ---- HTTP
@pytest.fixture
def web(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj).encode(), "application/json", code)

        def do_GET(self):
            u = urlparse(self.path)
            if not fake.handle_get(self, [p for p in u.path.split("/") if p], parse_qs(u.query)):
                self._json({"error": "unrouted"}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not fake.handle_post(self, [p for p in urlparse(self.path).path.split("/") if p], body):
                self._json({"error": "unrouted"}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", fake
    srv.shutdown()


def req(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data, {"Content-Type": "application/json", **(headers or {})},
                               method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return f.status, f.headers.get("Content-Type"), f.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_http_systems(web):
    base, _ = web
    add_rows()
    code, _, body = req(base, "/api/systems")
    assert code == 200 and "admin-panel" in {i["id"] for i in json.loads(body)}
    code, _, body = req(base, "/api/systems/admin-panel")
    assert code == 200 and json.loads(body)["components"]
    assert req(base, "/api/systems/nope")[0] == 404
    assert req(base, "/api/systems", headers={"Host": "evil.example"})[0] == 403


def test_http_recording_flow(web):
    base, sy = web
    assert json.loads(req(base, "/api/record/state")[2]) == {"state": "idle"}
    assert req(base, "/api/record/start", GOOD, {"Origin": "http://evil.example"})[0] == 403
    code, _, body = req(base, "/api/record/start", {**GOOD, "rights": False})
    assert code == 400 and "error" in json.loads(body)
    assert req(base, "/api/record/start", GOOD)[0] == 200
    assert req(base, "/api/record/start", GOOD)[0] == 409
    assert json.loads(req(base, "/api/record/state")[2])["state"] == "recording"
    assert req(base, "/api/record/capture", {})[0] == 200
    assert req(base, "/api/record/stop", {})[0] == 200
    assert req(base, "/api/record/stop", {})[0] == 409


def test_http_recordings_and_files(web):
    base, sy = web
    make_session(sy)
    s = "2026-10-20-120000-example-com"
    code, _, body = req(base, "/api/recordings?system=my-product")
    assert code == 200 and json.loads(body)[0]["session"] == s
    assert json.loads(req(base, "/api/recordings?system=zzz")[2]) == []
    code, ctype, body = req(base, f"/rec/my-product/{s}/pages/001-home/w1440/viewport.png")
    assert code == 200 and ctype == "image/png" and body.startswith(b"\x89PNG")
    code, ctype, _ = req(base, f"/rec/my-product/{s}/pages/001-home/w1440/dom.json")
    assert code == 200 and ctype.startswith("application/json")
    assert req(base, f"/rec/my-product/{s}/..%2F..%2Fsecret.txt")[0] == 404
    assert req(base, f"/rec/my-product/{s}/missing.png")[0] == 404
    assert req(base, f"/rec/my-product/{s}/session.json", headers={"Host": "evil.example"})[0] == 403


def write_fixture_system(root):
    """A small fixture system (a guide naming its source repo and commit, and tokens), so tests never read real systems."""
    d = root / "admin-panel"
    d.mkdir(parents=True, exist_ok=True)
    (d / "GUIDE.md").write_text("# Admin Panel guide\n\nRead this first. Everything here comes from `web-app` at "
                                "`936f50dce` (2026-09-30).\n\n## Rules\n- Use the kit.\n", encoding="utf-8")
    (d / "tokens.json").write_text('[{"name": "color-neutral-000", "value": "#ffffff", "css_var": "--color-neutral-000"}]', encoding="utf-8")
    return root


def write_fixture_themes(root):
    """Two fixture HTML themes: one with a README guide, one without."""
    for name, readme in (("editorial", False), ("dk-mckinsey", True)):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "style.css").write_text(":root { --ink: #111; }\n", encoding="utf-8")
        if readme:
            (d / "README.md").write_text("# Theme\n\nHow to use it.\n", encoding="utf-8")
    return root
