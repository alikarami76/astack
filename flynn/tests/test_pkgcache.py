"""Studio's package cache: npm tarballs fetched from the upstream once, metadata refreshed and served stale when the
upstream fails, macOS-only lockfile warm list, Node downloads limited to known names. Disk-only (no Postgres)."""
import importlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))


@pytest.fixture
def reg(tmp_path, monkeypatch):
    hits = {"n": 0, "down": False}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits["n"] += 1
            if hits["down"]:
                self.send_response(503); self.end_headers(); return
            if self.path.endswith(".tgz"):
                body = b"TARBALL" + self.path.encode()
                ctype = "application/octet-stream"
            elif self.path == "/left-pad":
                body = json.dumps({"name": "left-pad", "accept": self.headers.get("Accept")}).encode()
                ctype = "application/json"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("STUDIO_NPM_UPSTREAM", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("STUDIO_CACHE_DB_URL", raising=False)
    import pkgcache
    pkgcache = importlib.reload(pkgcache)
    yield pkgcache, hits
    srv.shutdown()


def test_tarball_fetched_once(reg):
    pc, hits = reg
    a = pc.npm_tarball("left-pad/-/left-pad-1.3.0.tgz")
    b = pc.npm_tarball("left-pad/-/left-pad-1.3.0.tgz")
    assert a == b and a.read_bytes().startswith(b"TARBALL") and hits["n"] == 1
    hits["down"] = True
    assert pc.npm_tarball("left-pad/-/left-pad-1.3.0.tgz").is_file()       # served while the upstream is down


def test_metadata_refreshes_and_serves_stale(reg, monkeypatch):
    pc, hits = reg
    p, hdr = pc.npm_meta("left-pad", "application/vnd.npm.install-v1+json")
    assert json.loads(p.read_text())["accept"].startswith("application/vnd.npm.install-v1") and hits["n"] == 1
    pc.npm_meta("left-pad", "application/vnd.npm.install-v1+json")
    assert hits["n"] == 1                                                  # fresh: no second request
    monkeypatch.setattr(pc, "META_TTL", 0.0)
    hits["down"] = True
    p2, _ = pc.npm_meta("left-pad", "application/vnd.npm.install-v1+json")
    assert p2.is_file() and hits["n"] == 2                                 # tried, failed, served the old copy
    hits["down"] = False
    assert pc.npm_meta("no-such-package", "") is None                     # a 404 upstream is a 404 here


def test_lock_warm_list_is_mac_and_platform_free():
    import pkgcache
    lock = json.loads((FLYNN / "adapters" / "studio" / "app_template" / "package-lock.json").read_text())
    ts = pkgcache.lock_tarballs(lock)
    assert any(t.startswith("react/-/react-18.3.1") for t in ts)
    assert not any("linux" in t or "win32" in t for t in ts)
    assert lock["name"] == "__NAME__"


def test_node_names_and_keys_are_checked(reg):
    pc, _ = reg
    assert pc.node_file("../../etc/passwd") is None
    assert pc.node_file("node-v22.1.0-linux-x64.tar.gz") is None
    with pytest.raises(ValueError):
        pc._path("npm/../../x")
