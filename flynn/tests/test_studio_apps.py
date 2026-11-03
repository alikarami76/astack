import json
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))

FAKE_NPM = r'''#!/usr/bin/env python3
import os, sys, http.server, socketserver
a = sys.argv[1:]
if a[:1] == ["install"]:
    os.makedirs("node_modules", exist_ok=True)
    open("node_modules/.ok", "w").write("1")
    print("added 1 package")
    if os.environ.get("FAKE_NPM_FAIL"):
        print("boom"); sys.exit(3)
elif a[:2] == ["run", "dev"]:
    port = int(a[a.index("--port") + 1])
    socketserver.TCPServer.allow_reuse_address = True
    print("dev server on", port, flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler).serve_forever()
'''


@pytest.fixture
def apps(tmp_path, monkeypatch):
    fake = tmp_path / "npm"
    fake.write_text(FAKE_NPM)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FLYNN_NPM", str(fake))
    monkeypatch.setenv("FLYNN_APPS_DIR", str(tmp_path / "apps"))
    import apps as mod
    yield mod
    mod.stop_all()


def wait(fn, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        if fn():
            return True
        time.sleep(0.05)
    return False


def reachable(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def test_create_files_and_commit(apps, tmp_path):
    info = apps.create_app("my-app", 'A "quoted" <thing>')
    d = tmp_path / "apps" / "my-app"
    for f in ("package.json", "index.html", "src/main.tsx", "src/App.tsx", "src/index.css", "tsconfig.json",
              "vite.config.ts", ".gitignore", "app.json"):
        assert (d / f).is_file(), f
    pkg = json.loads((d / "package.json").read_text())
    assert pkg["name"] == "my-app" and pkg["dependencies"]["react"] == "18.3.1"
    assert '127.0.0.1' in (d / "vite.config.ts").read_text()
    assert 'A \\"quoted\\" <thing>' in (d / "src" / "App.tsx").read_text()
    assert "__" not in (d / "index.html").read_text()
    gi = (d / ".gitignore").read_text()
    assert "node_modules" in gi and "dist" in gi and ".flynn" in gi
    assert json.loads((d / "app.json").read_text())["description"] == 'A "quoted" <thing>'
    assert info["head"]["message"] == "Create my-app" and info["installed"] is False
    assert not (d / "src" / "theme.css").exists()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout == ""
    assert [a["name"] for a in apps.list_apps()] == ["my-app"]


@pytest.mark.parametrize("system,needle", [("admin-panel", "--color-neutral-000"), ("editorial", "--ink")])
def test_theme_css(apps, tmp_path, system, needle, monkeypatch):
    from test_studio_systems import write_fixture_system
    monkeypatch.setattr(apps, "SYSTEMS_DIR", write_fixture_system(tmp_path / "systems"))
    apps.create_app("themed", "x", system)
    d = tmp_path / "apps" / "themed" / "src"
    css = (d / "theme.css").read_text()
    assert needle in css and css.lstrip().startswith(":root")
    assert 'import "./theme.css"' in (d / "main.tsx").read_text()
    if system == "editorial":
        assert css.count("{") == 1 and "body {" not in css
    assert apps.app_info("themed")["system"] == system


def test_unknown_system_skips_theme(apps, tmp_path):
    apps.create_app("plain", "x", "nope")
    assert not (tmp_path / "apps" / "plain" / "src" / "theme.css").exists()


@pytest.mark.parametrize("bad", ["A", "a", "-ab", "ab_c", "../x", "x" * 42, "Ab", ""])
def test_slug_validation(apps, bad):
    with pytest.raises(ValueError):
        apps.create_app(bad, "x")


def test_duplicate(apps):
    apps.create_app("dup", "x")
    with pytest.raises(FileExistsError):
        apps.create_app("dup", "y")


def test_install_state(apps, tmp_path, monkeypatch):
    apps.create_app("inst", "x")
    assert apps.install_state("inst")["state"] == "idle"
    apps.install("inst")
    assert wait(lambda: apps.install_state("inst")["state"] != "running")
    st = apps.install_state("inst")
    assert st["state"] == "done" and st["code"] == 0 and "added 1 package" in st["tail"]
    assert (tmp_path / "apps" / "inst" / "node_modules" / ".ok").exists()
    assert apps.app_info("inst")["installed"] is True
    assert apps.versions("inst")[0]["message"] == "Create inst"      # node_modules is ignored
    monkeypatch.setenv("FAKE_NPM_FAIL", "1")
    apps.install("inst")
    assert wait(lambda: apps.install_state("inst")["state"] != "running")
    assert apps.install_state("inst")["state"] == "failed" and apps.install_state("inst")["code"] == 3


def test_preview(apps):
    apps.create_app("prev", "x")
    with pytest.raises(RuntimeError):
        apps.start_preview("prev")
    apps.install("prev")
    assert wait(lambda: apps.install_state("prev")["state"] == "done")
    r = apps.start_preview("prev")
    assert 5200 <= r["port"] <= 5299 and r["url"] == f"http://127.0.0.1:{r['port']}/"
    assert wait(lambda: reachable(r["port"]))
    s = apps.preview_state("prev")
    assert s["running"] and s["port"] == r["port"]
    assert apps.start_preview("prev")["port"] == r["port"]            # already running
    apps.stop_preview("prev")
    assert not apps.preview_state("prev")["running"]
    assert wait(lambda: not reachable(r["port"]))


def test_files_and_read_file_safety(apps, tmp_path):
    apps.create_app("fl", "x")
    d = tmp_path / "apps" / "fl"
    (d / "node_modules").mkdir()
    (d / "node_modules" / "a.js").write_text("x")
    (d / ".flynn").mkdir()
    (d / ".flynn" / "install.log").write_text("x")
    (d / "bin.dat").write_bytes(b"\x00\x01")
    (d / "big.txt").write_text("a" * (300 * 1024 + 1))
    (tmp_path / "secret.txt").write_text("s")
    paths = {f["path"]: f["size"] for f in apps.files("fl")}
    assert "src/App.tsx" in paths and paths["bin.dat"] == 2
    assert not any(p.startswith(("node_modules", ".git/", ".flynn", "dist")) for p in paths)
    assert "FL" not in apps.read_file("fl", "src/App.tsx") and "fl" in apps.read_file("fl", "src/App.tsx")
    for bad in ("../secret.txt", "../../etc/passwd", "/etc/passwd", "node_modules/a.js", ".git/HEAD", "nope.txt"):
        with pytest.raises(KeyError):
            apps.read_file("fl", bad)
    with pytest.raises(ValueError):
        apps.read_file("fl", "bin.dat")
    with pytest.raises(ValueError):
        apps.read_file("fl", "big.txt")
    with pytest.raises(KeyError):
        apps.app_info("missing")


def test_commit_and_restore(apps, tmp_path):
    apps.create_app("ver", "x")
    d = tmp_path / "apps" / "ver"
    assert apps.commit("ver", "nothing") is None
    first = apps.versions("ver")[0]["hash"]
    (d / "src" / "App.tsx").write_text("// changed\n")
    c = apps.commit("ver", "Change app")
    assert c["message"] == "Change app" and len(apps.versions("ver")) == 2
    apps.restore("ver", first[:7])
    assert "changed" not in (d / "src" / "App.tsx").read_text()
    vs = apps.versions("ver")
    assert [v["message"] for v in vs] == [f"Restore {first[:7]}", "Change app", "Create ver"]
    with pytest.raises(KeyError):
        apps.restore("ver", "deadbeef")
    with pytest.raises(ValueError):
        apps.restore("ver", "--hard")
    assert len(apps.versions("ver", limit=1)) == 1


def test_health(apps):
    h = {c["id"]: c for c in apps.health()}
    assert set(h) == {"node", "npm", "git"} and h["git"]["status"] == "ok"
    assert set(h["npm"]) == {"id", "label", "status", "detail", "fix"}


# ---------- HTTP ----------

@pytest.fixture
def server(apps):
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

        def _go(self, post):
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.split("/") if p]
            if post:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                ok = apps.handle_post(self, parts, body)
            else:
                ok = apps.handle_get(self, parts, parse_qs(u.query))
            if not ok:
                self._json({"error": "nope"}, 404)

        do_GET = lambda self: self._go(False)
        do_POST = lambda self: self._go(True)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def req(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data, {"Content-Type": "application/json", **(headers or {})},
                               method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http_flow(server, apps, tmp_path):
    assert req(server, "/api/apps") == (200, {"apps": []})
    code, j = req(server, "/api/apps", {"name": "web", "description": "d", "system": "editorial"})
    assert code == 201 and j["name"] == "web" and j["system"] == "editorial"
    assert req(server, "/api/apps", {"name": "web", "description": "d"})[0] == 409
    assert req(server, "/api/apps", {"name": "Bad Name"})[0] == 400
    assert req(server, "/api/apps")[1]["apps"][0]["name"] == "web"
    assert req(server, "/api/apps/web")[1]["installed"] is False
    assert req(server, "/api/apps/nope")[0] == 404
    assert req(server, "/api/apps/web/files")[1]["files"]
    code, j = req(server, "/api/apps/web/file?path=src/App.tsx")
    assert code == 200 and "web" in j["content"]
    assert req(server, "/api/apps/web/file?path=../../x")[0] == 404
    assert req(server, "/api/apps/web/file?path=bin")[0] == 404
    assert req(server, "/api/apps/web/preview/start", {})[0] == 409          # not installed
    assert req(server, "/api/apps/web/install", {})[0] == 200
    assert wait(lambda: req(server, "/api/apps/web/install")[1]["state"] == "done")
    code, j = req(server, "/api/apps/web/preview/start", {})
    assert code == 200 and wait(lambda: reachable(j["port"]))
    assert req(server, "/api/apps/web")[1]["preview"]["running"] is True
    assert req(server, "/api/apps/web/preview/stop", {})[1]["running"] is False
    (tmp_path / "apps" / "web" / "src" / "App.tsx").write_text("// edit\n")
    code, j = req(server, "/api/apps/web/commit", {"message": "Edit"})
    assert code == 200 and j["commit"]["message"] == "Edit"
    assert req(server, "/api/apps/web/commit", {"message": "Edit"})[1] == {"commit": None}
    assert req(server, "/api/apps/web/commit", {"message": ""})[0] == 400
    vs = req(server, "/api/apps/web/versions")[1]["versions"]
    assert [v["message"] for v in vs] == ["Edit", "Create web"]
    assert req(server, "/api/apps/web/restore", {"hash": vs[1]["hash"]})[0] == 200
    assert req(server, "/api/apps/web/restore", {"hash": "abcdef12"})[0] == 404
    assert req(server, "/api/apps/web/bogus", {})[0] == 404


def test_origin_and_host_refused(server):
    assert req(server, "/api/apps", {"name": "evil"}, {"Origin": "https://evil.example"})[0] == 403
    assert req(server, "/api/apps")[1] == {"apps": []}
    assert req(server, "/api/apps", headers={"Host": "evil.example"})[0] == 403


def test_new_apps_have_ai_and_old_ones_can_enable_it(apps, tmp_path):
    a = apps.create_app("smart", "x")
    d = tmp_path / "apps" / "smart"
    assert a["ai"] is True and (d / "flynn-ai.ts").exists() and (d / "src" / "lib" / "ai.ts").exists()
    assert "flynnAi()" in (d / "vite.config.ts").read_text()
    assert "@anthropic-ai/claude-agent-sdk" in json.loads((d / "package.json").read_text())["dependencies"]
    # an app made before AI existed: no files, plain vite config, ai off
    apps.create_app("old", "y")
    o = tmp_path / "apps" / "old"
    (o / "flynn-ai.ts").unlink(); (o / "src" / "lib" / "ai.ts").unlink()
    (o / "vite.config.ts").write_text('import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n});\n')
    meta = json.loads((o / "app.json").read_text()); meta["ai"] = False; (o / "app.json").write_text(json.dumps(meta))
    pkg = json.loads((o / "package.json").read_text()); pkg["dependencies"].pop("@anthropic-ai/claude-agent-sdk"); (o / "package.json").write_text(json.dumps(pkg))
    info = apps.enable_ai("old")
    assert info["ai"] is True and (o / "flynn-ai.ts").exists() and (o / "src" / "lib" / "ai.ts").exists()
    vc = (o / "vite.config.ts").read_text()
    assert 'import { flynnAi } from "./flynn-ai";' in vc and "plugins: [react(), flynnAi()]" in vc
    assert "@anthropic-ai/claude-agent-sdk" in json.loads((o / "package.json").read_text())["dependencies"]
    assert apps.versions("old")[0]["message"].startswith("Enable AI")


def test_ai_usage_sums_the_log(apps, tmp_path):
    apps.create_app("spend", "x")
    log = tmp_path / "apps" / "spend" / ".flynn" / "ai-usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    import datetime as dt
    today = dt.date.today().isoformat()
    log.write_text(json.dumps({"at": f"{today}T10:00:00Z", "cost": 0.01}) + "\n" + json.dumps({"at": "2020-01-01T10:00:00Z", "cost": 0.02}) + "\nbad\n")
    u = apps.ai_usage("spend")
    assert u == {"calls": 2, "cost": 0.03, "today": 0.01}
