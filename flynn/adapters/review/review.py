#!/usr/bin/env python3
"""flynn review: open a run's page in the browser with a comment layer, so the user can pin feedback on the exact spot
and flynn can read it, reply and resolve it. The first piece of Studio (sub-project 6).

  review.py serve <run> [--page page/index.html] [--port 8765] [--no-open]
  review.py list <run> [--all] [--json]         open comments (or all), oldest first
  review.py reply <run> <n> "text"              flynn's reply in the thread (shown live in the page)
  review.py resolve <run> <n> [--note "..."]    mark done (optionally with a note of what changed)
  review.py reopen <run> <n>

In the page: press C (or the Comment button), click anything, type, and press Save (Cmd/Ctrl+Enter).
Pins stay attached to the element they were dropped on, so they follow layout changes. The page reloads
itself, keeping the scroll position, whenever flynn re-renders it.

Comments are stored in <run>/feedback.json and mirrored in <run>/feedback.md (the user's words, for the curator).
The server listens on 127.0.0.1 only and serves files from the run folder only."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OVERLAY = HERE / "overlay.js"
PREFIX = "/__review/"
_lock = threading.Lock()


# ---------- store ----------

def _fb(run: Path) -> Path:
    return run / "feedback.json"


def load(run: Path) -> dict:
    p = _fb(run)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"comments": []}


def save(run: Path, data: dict) -> None:
    _fb(run).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# Feedback", "", "Written by flynn's review UI; the user's words verbatim.", ""]
    for c in data["comments"]:
        where = c["anchor"].get("shot") or c["anchor"].get("selector", "")
        lines.append(f"## {c['n']}. [{c['status']}] {where}")
        lines.append(f"**{c['author']}** ({c['time']}): {c['text']}")
        for r in c.get("replies", []):
            lines.append(f"- **{r['author']}** ({r['time']}): {r['text']}")
        lines.append("")
    (run / "feedback.md").write_text("\n".join(lines), encoding="utf-8")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def add_comment(run: Path, text: str, anchor: dict, author: str = "the user", page: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty comment")
    with _lock:
        data = load(run)
        n = max([c["n"] for c in data["comments"]] or [0]) + 1
        c = {"n": n, "text": text, "author": author, "time": _now(), "status": "open", "page": page,
             "anchor": {k: anchor.get(k) for k in ("selector", "dx", "dy", "px", "py", "shot", "snippet")}, "replies": []}
        data["comments"].append(c)
        save(run, data)
    return c


def update(run: Path, n: int, status: str | None = None, reply: str | None = None, author: str = "flynn") -> dict:
    with _lock:
        data = load(run)
        for c in data["comments"]:
            if c["n"] == n:
                if reply:
                    c.setdefault("replies", []).append({"author": author, "text": reply.strip(), "time": _now()})
                if status:
                    if status not in ("open", "resolved"):
                        raise ValueError("status must be open or resolved")
                    c["status"] = status
                save(run, data)
                return c
    raise KeyError(f"no comment {n}")


# ---------- server ----------

def make_handler(run: Path, page_rel: str):
    run = run.resolve()

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(run), **kw)

        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _inside(self, path: str) -> Path | None:
            p = (run / path.lstrip("/").split("?")[0]).resolve()
            return p if (p == run or run in p.parents) else None

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/" + page_rel)
                self.end_headers()
                return
            if path == PREFIX + "overlay.js":
                body = OVERLAY.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == PREFIX + "api/comments":
                return self._json(load(run))
            if path == PREFIX + "api/version":
                pg = self._inside(page_rel)
                css = sorted(pg.parent.glob("*.css")) if pg else []
                v = max([pg.stat().st_mtime] + [c.stat().st_mtime for c in css]) if pg and pg.exists() else 0
                return self._json({"version": v})
            target = self._inside(path)
            if target is None:
                return self.send_error(HTTPStatus.FORBIDDEN)
            if target.suffix == ".html" and target.exists():
                html = target.read_text(encoding="utf-8")
                tag = f'<script src="{PREFIX}overlay.js" data-page="{path.lstrip("/")}"></script>'
                html = html.replace("</body>", tag + "\n</body>") if "</body>" in html else html + tag
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                if path == PREFIX + "api/comments":
                    b = self._body()
                    return self._json(add_comment(run, b.get("text", ""), b.get("anchor") or {}, "the user", b.get("page", "")), 201)
                if path.startswith(PREFIX + "api/comments/"):
                    n = int(path.rsplit("/", 1)[1])
                    b = self._body()
                    return self._json(update(run, n, b.get("status"), b.get("reply"), author="the user"))
            except (ValueError, KeyError) as e:
                return self._json({"error": str(e)}, 400)
            self.send_error(HTTPStatus.NOT_FOUND)

    return H


def serve(run: Path, page_rel: str = "page/index.html", port: int = 8765, open_browser: bool = True) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(run, page_rel))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/{page_rel}"
    print(f"review UI: {url}  (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("run"); s.add_argument("--page", default="page/index.html")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--no-open", action="store_true")
    l = sub.add_parser("list"); l.add_argument("run"); l.add_argument("--all", action="store_true"); l.add_argument("--json", action="store_true")
    r = sub.add_parser("reply"); r.add_argument("run"); r.add_argument("n", type=int); r.add_argument("text")
    v = sub.add_parser("resolve"); v.add_argument("run"); v.add_argument("n", type=int); v.add_argument("--note")
    o = sub.add_parser("reopen"); o.add_argument("run"); o.add_argument("n", type=int)
    a = ap.parse_args(argv)
    run = Path(a.run)
    if a.cmd == "serve":
        httpd = serve(run, a.page, a.port, not a.no_open)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    elif a.cmd == "list":
        cs = [c for c in load(run)["comments"] if a.all or c["status"] == "open"]
        if a.json:
            print(json.dumps(cs, ensure_ascii=False, indent=1))
        for c in cs if not a.json else []:
            where = c["anchor"].get("shot") or c["anchor"].get("selector")
            snip = (c["anchor"].get("snippet") or "")[:60]
            print(f"#{c['n']} [{c['status']}] ({where}) on «{snip}»: {c['text']}")
            for rp in c.get("replies", []):
                print(f"    {rp['author']}: {rp['text']}")
    elif a.cmd == "reply":
        update(run, a.n, reply=a.text)
    elif a.cmd == "resolve":
        update(run, a.n, status="resolved", reply=a.note)
    elif a.cmd == "reopen":
        update(run, a.n, status="open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
