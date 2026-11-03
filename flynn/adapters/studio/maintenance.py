#!/usr/bin/env python3
"""Flynn's startup maintenance server: a tiny stand-in for Studio while a team-server deploy is starting up.

deploy/studio/start.sh runs this first (pure standard library, no flynn imports, so it listens in well under a second),
and Studio takes the port over when it is ready (`studio.py serve --takeover-pidfile`). Until then:

  /healthz               200 {"ok": true, "maintenance": true}   (the platform's readiness check passes, people see our
                                                                  page and not the proxy's bare 503)
  /__maintenance/status  200 {"maintenance": true, "since": <epoch>, "message": "..."}
  /static/<asset>        the logo and fonts the page needs (a fixed list, from adapters/studio/static)
  everything else        the maintenance page: 503, Retry-After: 30, X-Flynn-Maintenance: 1, no-store

Usage: maintenance.py [--host 0.0.0.0] [--port 8080] [--pidfile FILE] [--message TEXT]
SIGTERM/SIGINT end it at once (the listening socket closes with the process).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"flynn-logo.png": "image/png", "flynn-logo-64.png": "image/png", "geist.woff2": "font/woff2",
          "geist-mono.woff2": "font/woff2", "vazirmatn-variable.woff2": "font/woff2"}
DEFAULT_MESSAGE = "Flynn is being updated"
FALLBACK_PAGE = (b'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                 b'<title>Flynn is being updated</title><body style="font:16px system-ui;background:#0b0e13;color:#c9d4dd;'
                 b'display:grid;place-items:center;min-height:100vh;margin:0"><p>Flynn is being updated. This page will '
                 b'reload by itself.</p><script>setTimeout(()=>location.reload(),5000)</script>')


def make_handler(message: str, since: float):
    try:
        page = (STATIC / "maintenance.html").read_bytes()
    except OSError:
        page = FALLBACK_PAGE
    assets = {}
    for name in ASSETS:
        try:
            assets[name] = (STATIC / name).read_bytes()
        except OSError:
            pass

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "flynn-maintenance"

        def log_message(self, *a):
            pass

        def _reply(self, code, body, ctype, extra=None, head=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _handle(self, head=False):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path == "/healthz":
                body = json.dumps({"ok": True, "maintenance": True}).encode()
                return self._reply(200, body, "application/json", {"Cache-Control": "no-store", "X-Flynn-Maintenance": "1"}, head)
            if path == "/__maintenance/status":
                body = json.dumps({"maintenance": True, "since": since, "message": message}).encode()
                return self._reply(200, body, "application/json", {"Cache-Control": "no-store", "X-Flynn-Maintenance": "1"}, head)
            if path.startswith("/static/") and path[8:] in assets and self.command in ("GET", "HEAD"):
                n = path[8:]
                return self._reply(200, assets[n], ASSETS[n], {"Cache-Control": "public, max-age=3600"}, head)
            return self._reply(503, page, "text/html; charset=utf-8",
                               {"Retry-After": "30", "X-Flynn-Maintenance": "1", "Cache-Control": "no-store"}, head)

        def do_GET(self):
            self._handle()

        def do_HEAD(self):
            self._handle(head=True)

        def do_POST(self):
            self._drain()
            self._handle()

        do_PUT = do_DELETE = do_PATCH = do_POST

        def do_OPTIONS(self):
            self._handle()

        def _drain(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 1 << 20:
                    self.rfile.read(n)
            except (ValueError, OSError):
                pass

    return H


def make_server(host: str, port: int, message: str = DEFAULT_MESSAGE, since: float | None = None) -> ThreadingHTTPServer:
    class Srv(ThreadingHTTPServer):
        allow_reuse_address = True       # Studio binds the same port right after this one stops
        daemon_threads = True
    return Srv((host, port), make_handler(message, since or time.time()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--pidfile", help="written once the port is bound; Studio's --takeover-pidfile reads it")
    ap.add_argument("--message", default=DEFAULT_MESSAGE)
    a = ap.parse_args(argv)
    httpd = make_server(a.host, a.port, a.message)
    if a.pidfile:
        tmp = a.pidfile + ".tmp"
        Path(tmp).write_text(str(os.getpid()))
        os.replace(tmp, a.pidfile)

    def bye(*_):
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    print(f"flynn maintenance: serving :{httpd.server_address[1]}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
