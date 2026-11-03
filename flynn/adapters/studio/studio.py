#!/usr/bin/env python3
"""flynn Studio (sub-project 6): one local place to see every run, compare its options side by side on a
canvas, walk its versions, comment (the review layer), pick the winner and export.

  studio.py serve [--port 8770] [--no-open]           http://127.0.0.1:8770
  studio.py snapshot <run> [--option main] [--label "after round 2"]   freeze the current page as a version
  studio.py requests [--all]                           Figma / Canva exports waiting for flynn
  studio.py done <run> <request id> --link URL         mark an export request done (Studio shows the link)
  studio.py failed <run> <request id> --reason "..."
  studio.py figma-script <run> <request id> <script file>   attach the Figma capture script to a request
  studio.py export <run> <kind> [--option main]        run a local export from the command line

A run's options are page/ ("main") and options/<name>/page/. Versions live in versions/<NN>-<label>/<option>/.
Exports:
  local, done by Studio at once: html (zip), pdf, png (section PNGs, zip), tsx (React component, see
  html_to_tsx.py), pptx (decks, adapters/pptx), eml (newsletters, adapters/html/email_pack.py);
  queued for flynn, who has the Figma and Canva connectors: figma, canva. flynn reads them with
  `studio.py requests`, does the work through the MCP tools, and closes them with `done` or `failed`.
Decisions (the option the user picks) and requests are stored in <run>/studio.json; comments in <run>/feedback.json.
The server listens on 127.0.0.1 only and serves files from inside the runs folder only."""
from __future__ import annotations

import argparse
import datetime as _dt
import errno
import hashlib
import json
import os
import urllib.parse
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "review"))
sys.path.insert(0, str(FLYNN / "scripts"))
import review  # noqa: E402
sys.path.insert(0, str(HERE))
import chat  # noqa: E402
import edits as quick_edits  # noqa: E402
import systems as ds  # noqa: E402  (design systems and the recorder)
import apps  # noqa: E402  (the App builder)
import team  # noqa: E402  (team server: sign-in and each person's own folders)
import fpaths  # noqa: E402
import cover  # noqa: E402  (title cards)

PAGES = {"settings": "settings.html", "systems": "systems.html", "apps": "apps.html"}

STATIC = HERE / "static"
HTML_ADAPTER = FLYNN / "adapters" / "html"
LOCAL_EXPORTS = ("html", "pdf", "png", "tsx", "pptx", "eml", "penpot")
QUEUED_EXPORTS = ("figma", "canva")
_lock = threading.Lock()


def runs_root() -> Path:
    try:
        import fpaths
        return Path(fpaths.runs_dir())
    except Exception:
        return FLYNN / "runs"


# ---------- run model ----------

def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def options(run: Path) -> list[dict]:
    out = []
    if (run / "page" / "index.html").exists():
        out.append({"name": "main", "page": "page/index.html"})
    for d in sorted((run / "options").glob("*/page/index.html")) if (run / "options").exists() else []:
        out.append({"name": d.parent.parent.name, "page": str(d.relative_to(run))})
    return out


def _page_of(run: Path, option: str) -> Path:
    for o in options(run):
        if o["name"] == option:
            return run / o["page"]
    raise KeyError(f"no option {option!r} in {run.name}")


def versions(run: Path) -> list[dict]:
    vd = run / "versions"
    out = []
    for d in sorted(vd.iterdir()) if vd.exists() else []:
        if d.is_dir():
            meta = json.loads((d / "version.json").read_text()) if (d / "version.json").exists() else {}
            out.append({"id": d.name, "label": meta.get("label", d.name), "time": meta.get("time"),
                        "options": sorted(p.name for p in d.iterdir() if p.is_dir())})
    return out


def state(run: Path) -> dict:
    p = run / "studio.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"decision": None, "requests": [], "exports": []}


def save_state(run: Path, st: dict) -> None:
    (run / "studio.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


DOC_TYPES = ("deck", "graphic", "chart")


def classify(meta: dict, run: Path) -> dict:
    """Home's split: UI designs, Docs (and which kind of doc), or other runs (system learning, questions, syncs)."""
    t = (meta.get("type") or "").lower()
    words = f"{run.name} {meta.get('request', '')[:200]} {meta.get('template', '')}".lower()
    if t == "ui" or (meta.get("system") == "admin-panel" and t not in DOC_TYPES and t != "learn-system"):
        return {"area": "ui", "kind": "screen"}
    if t in DOC_TYPES:
        kind = ("newsletter" if any(w in words for w in ("newsletter", "letter")) else
                "release" if "release" in words else
                "deck" if t == "deck" and not any(w in words for w in ("report", "brief")) else
                "chart" if t == "chart" and "report" not in words else "report")
        return {"area": "doc", "kind": kind}
    return {"area": "other", "kind": t or "run"}


def add_link(run: Path, url: str, label: str = "", kind: str = "link") -> dict:
    if not str(url).startswith(("https://", "http://")):
        raise ValueError("a link must be an http(s) URL")
    with _lock:
        st = state(run)
        link = {"url": str(url)[:500], "label": str(label or url)[:120], "kind": str(kind or "link")[:20], "time": _now()}
        st.setdefault("links", [l for l in st.get("links", []) if l.get("url") != link["url"]])
        st["links"] = [l for l in st["links"] if l.get("url") != link["url"]] + [link]
        save_state(run, st)
    return link


_CV: dict = {}


def _content_version(page: Path) -> str:
    """A short hash of a page and its stylesheets, cached by their file times, so unchanged rewrites keep it."""
    files = [page] + sorted(page.parent.glob("*.css"))
    key = tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files if f.exists())
    hit = _CV.get(str(page))
    if hit and hit[0] == key:
        return hit[1]
    h = hashlib.sha1()
    for f in files:
        if f.exists():
            h.update(f.read_bytes())
    _CV[str(page)] = (key, h.hexdigest()[:16])
    return _CV[str(page)][1]


def run_summary(run: Path) -> dict:
    meta = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").exists() else {}
    fb = review.load(run)["comments"]
    st = state(run)
    thumb = next((str(p.relative_to(run)) for p in [run / "cover" / "cover.png", run / "shots" / "full.png"] if p.exists()), None)
    return {"id": run.name, "type": meta.get("type"), "request": meta.get("request", ""), "system": meta.get("system"),
            "outcome": meta.get("outcome"), "closed": bool(meta.get("closed_at")), "date": meta.get("date") or run.name[:10],
            "options": options(run), "versions": versions(run), "thumb": thumb,
            "comments_open": sum(c["status"] == "open" for c in fb), "comments": len(fb),
            "decision": st.get("decision"), "requests": st.get("requests", []), "exports": st.get("exports", []),
            "penpot": _penpot_info(run), "links": st.get("links", []), "cover": cover.status(run, st),
            "edits_pending": len(quick_edits.pending(run)), **{"area": "", "kind": ""}, **classify(meta, run)}


def _penpot_info(run: Path) -> dict | None:
    """Where this run lives in Penpot, if it was built there (adapters/penpot/push.py writes penpot.json)."""
    try:
        pj = json.loads((run / "penpot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"link": pj.get("link"), "page": pj.get("pageName"), "file": pj.get("fileName"), "pushed_at": pj.get("pushed_at"),
            "boards": list((pj.get("boards") or {}).keys())}


def list_runs() -> list[dict]:
    root = runs_root()
    runs = [d for d in root.iterdir() if d.is_dir() and options(d)] if root.exists() else []
    return sorted((run_summary(r) for r in runs), key=lambda r: r["id"], reverse=True)


def snapshot(run: Path, option: str | None = None, label: str = "") -> str:
    vd = run / "versions"
    vd.mkdir(exist_ok=True)
    n = len([d for d in vd.iterdir() if d.is_dir()]) + 1
    vid = f"{n:02d}-{review._now()[:10]}" if not label else f"{n:02d}-" + "".join(ch if ch.isalnum() else "-" for ch in label)[:40].strip("-")
    target = vd / vid
    for o in options(run):
        if option and o["name"] != option:
            continue
        src = (run / o["page"]).parent
        shutil.copytree(src, target / o["name"] / "page", ignore=shutil.ignore_patterns("*.pdf"))
        shots = run / "shots" if o["name"] == "main" else run / "options" / o["name"] / "shots"
        if (shots / "full.png").exists():
            shutil.copy(shots / "full.png", target / o["name"] / "full.png")
    (target / "version.json").write_text(json.dumps({"label": label or vid, "time": _now()}, ensure_ascii=False))
    return vid


def pick(run: Path, option: str, note: str = "") -> dict:
    _page_of(run, option)
    with _lock:
        st = state(run)
        st["decision"] = {"option": option, "note": note, "time": _now()}
        save_state(run, st)
    return st["decision"]


# ---------- exports ----------

def _exports_dir(run: Path) -> Path:
    d = run / "exports"
    d.mkdir(exist_ok=True)
    return d


def _record_export(run: Path, kind: str, option: str, path: Path) -> dict:
    e = {"kind": kind, "option": option, "path": str(path.relative_to(run)), "time": _now()}
    with _lock:
        st = state(run)
        st.setdefault("exports", []).append(e)
        save_state(run, st)
    return e


def _render(page: Path, out: Path, *extra) -> dict:
    r = subprocess.run([sys.executable, str(HTML_ADAPTER / "render.py"), str(page), "--out", str(out), *extra],
                       capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        raise RuntimeError(f"render failed: {r.stderr.strip()[-600:] or r.stdout[-600:]}")


def _layout_mode(html: str) -> str:
    """'slides' for decks, 'pages' for paginated documents (one <section class="page"> per printed page),
    '' for continuous pages."""
    if re.search(r'class="(?:[^"]* )?slide(?: [^"]*)?"', html):
        return "slides"
    if re.search(r'class="(?:[^"]* )?page(?: [^"]*)?"', html):
        return "pages"
    return ""


def export(run: Path, kind: str, option: str = "main") -> dict:
    page = _page_of(run, option)
    stem = f"{run.name}-{option}"
    ex = _exports_dir(run)
    if kind in QUEUED_EXPORTS:
        return queue_request(run, kind, option)
    if kind == "html":
        z = ex / f"{stem}-html.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in page.parent.rglob("*"):
                if f.is_file() and f.suffix != ".pdf":
                    zf.write(f, f.relative_to(page.parent))
        return _record_export(run, kind, option, z)
    if kind in ("pdf", "png"):
        html = page.read_text(encoding="utf-8")
        mode = _layout_mode(html)
        slides = mode == "slides"
        tmp = ex / f"_shots-{option}"
        args = ["--pdf", f"{stem}.pdf"] + ([f"--{mode}"] if mode else [])
        _render(page, tmp, *args)
        pdf = tmp.parent / f"{stem}.pdf"
        if kind == "pdf":
            return _record_export(run, kind, option, pdf)
        z = ex / f"{stem}-png.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            src = tmp / mode if mode and (tmp / mode).exists() else tmp
            for f in sorted(src.glob("*.png")):
                zf.write(f, f.name)
        return _record_export(run, kind, option, z)
    if kind == "tsx":
        sys.path.insert(0, str(HERE))
        import html_to_tsx
        out = ex / f"{stem}-tsx"
        html_to_tsx.convert(page, out)
        return _record_export(run, kind, option, out)
    if kind == "pptx":
        tool = FLYNN / "adapters" / "pptx" / "pptx_export.py"
        if not tool.exists():
            raise RuntimeError("the pptx adapter is not installed")
        out = ex / f"{stem}.pptx"
        r = subprocess.run([sys.executable, str(tool), str(page), "--out", str(out)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"pptx export failed: {(r.stderr or r.stdout).strip()[-600:]}")
        return _record_export(run, kind, option, out)
    if kind == "penpot":
        # Build the run's options in the Penpot file open in the browser (edited boards are kept, see push.py).
        tool = FLYNN / "adapters" / "penpot" / "push.py"
        args = [sys.executable, str(tool), "run", str(run)]
        try:
            prev = json.loads((run / "penpot.json").read_text(encoding="utf-8"))
            if prev.get("file"):
                args += ["--file", prev["file"]]
        except (OSError, ValueError):
            pass
        r = subprocess.run(args, capture_output=True, text=True, timeout=900)
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            raise RuntimeError(f"penpot build failed: {(r.stderr or r.stdout).strip()[-600:]}")
        if not out.get("ok"):
            raise RuntimeError(out.get("error", "penpot build failed"))
        rec = _record_export(run, kind, option, run / "penpot.json")
        rec["link"] = out.get("link")
        return rec
    if kind == "eml":
        shots = run / "shots"
        _render(page, shots, "--width", "720", "--pad", "0")
        r = subprocess.run([sys.executable, str(HTML_ADAPTER / "email_pack.py"), str(run), "--subject", run.name],
                           capture_output=True, text=True, timeout=300)
        eml = run / "newsletter.eml"
        if r.returncode != 0 or not eml.exists():
            raise RuntimeError(f"email pack failed: {(r.stderr or r.stdout).strip()[-600:]}")
        dst = ex / f"{stem}.eml"
        shutil.copy(eml, dst)
        return _record_export(run, kind, option, dst)
    raise ValueError(f"unknown export {kind!r}; local: {LOCAL_EXPORTS}, queued: {QUEUED_EXPORTS}")


def queue_request(run: Path, kind: str, option: str) -> dict:
    with _lock:
        st = state(run)
        rid = f"{kind}-{len(st.get('requests', [])) + 1}"
        req = {"id": rid, "kind": kind, "option": option, "status": "waiting", "time": _now(), "link": None,
               "page": str(_page_of(run, option).relative_to(run))}
        st.setdefault("requests", []).append(req)
        save_state(run, st)
    return req


def close_request(run: Path, rid: str, status: str, link: str | None = None, reason: str | None = None,
                  script: str | None = None) -> dict:
    with _lock:
        st = state(run)
        for r in st.get("requests", []):
            if r["id"] == rid:
                if script is not None:
                    r["capture_script"] = script
                    r["status"] = "capturing"
                else:
                    r.update(status=status, link=link, reason=reason, closed=_now())
                save_state(run, st)
                return r
    raise KeyError(f"no request {rid} in {run.name}")


def pending_requests(include_closed: bool = False) -> list[dict]:
    out = []
    for r in list_runs():
        for q in r["requests"]:
            if include_closed or q["status"] in ("waiting", "capturing"):
                out.append({"run": r["id"], **q})
    return out


# ---------- title cards (cover.py) ----------

def _comments(run: Path) -> list[dict]:
    return review.load(run)["comments"]


COVER_HOST = type("CoverHost", (), {"options": staticmethod(options), "classify": staticmethod(classify), "state": staticmethod(state),
                                    "save_state": staticmethod(save_state), "versions": staticmethod(versions),
                                    "comments": staticmethod(_comments), "lock": _lock, "now": staticmethod(_now)})


# ---------- server ----------

TEAM_PUBLIC = ("/healthz", "/auth/login", "/auth/callback", "/auth/logout", "/flynn/agent/install.sh",
               "/flynn/agent/connect", "/flynn/agent/flynn_agent.py")
TEAM_OFF_PATHS = ("rec",)              # recordings need a person's own Mac (the App builder runs there through their agent)


def make_handler(root: Path):
    root = root.resolve()

    class H(SimpleHTTPRequestHandler):
        me: dict | None = None

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

        def _root(self) -> Path:
            """The runs folder for this request: the signed-in person's own on a team server."""
            own = team.runs_of(self.me) if team.on() else None
            return own.resolve() if own else root

        def end_headers(self):
            if team.on():
                self.send_header("Content-Security-Policy", team.frame_header())
            super().end_headers()

        def _page(self, name: str):
            html = (STATIC / name).read_bytes()
            if team.on():
                html = html.replace(b"</head>", team.sdk_tag().encode() + b"</head>", 1)
            return self._send(html, "text/html; charset=utf-8")

        def _redirect(self, to: str, cookie: str | None = None):
            self.send_response(302)
            self.send_header("Location", to)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- team server: sign-in, the agent hub, and the gate in front of everything else
        def _team(self, method: str) -> bool:
            """True when the request was answered here (sign-in pages, the agent, a refusal)."""
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            parts = [x for x in path.split("/") if x]
            self.me = team.who(self)
            if path == "/healthz":
                self._json({"ok": True})
                return True
            if path == "/auth/login":
                ok, why = team.configured()
                if not ok:
                    self._send(team.page("Sign-in isn't set up yet", f"The server's Karvansara sign-in is {why}."), "text/html; charset=utf-8", 503)
                    return True
                self._redirect(team.login_url((q.get("next") or ["/"])[0]))
                return True
            if path == "/auth/callback":
                if q.get("error"):
                    team.log(f"signin refused {q['error'][0][:60]}")
                    self._send(team.page("Sign-in was cancelled", "Karvansara didn't sign you in.",
                                         '<a class="b" href="/auth/login">Try again</a>'), "text/html; charset=utf-8", 401)
                    return True
                try:
                    person, nxt = team.finish((q.get("code") or [""])[0], (q.get("state") or [""])[0])
                except (ValueError, OSError) as e:
                    team.log(f"signin failed {type(e).__name__}")
                    self._send(team.page("Couldn't sign you in", str(e)[:200], '<a class="b" href="/auth/login">Try again</a>'),
                               "text/html; charset=utf-8", 401)
                    return True
                self._redirect(nxt, team.cookie_header(team.new_session(person)))
                return True
            if path == "/auth/logout" and method == "POST":
                tok = team.cookie_token(self)
                if tok:
                    team.end_session(tok)
                self.send_response(200)
                self.send_header("Set-Cookie", team.cookie_header("", clear=True))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return True
            if path.startswith("/flynn/agent/wheels/"):
                return self._wheels(path[len("/flynn/agent/wheels/"):]) or True
            if path.startswith("/flynn/agent/node/") and method == "GET":     # Node.js for Macs without one (public)
                import pkgcache
                f = pkgcache.node_file(path[len("/flynn/agent/node/"):])
                if not f:
                    self._json({"error": "not cached"}, 404)
                else:
                    self._send(f.read_bytes(), "application/json" if f.suffix == ".json" else "application/gzip")
                return True
            if path.startswith("/flynn/agent/"):
                import agent_hub
                me = None
                if self.me:
                    me = {"email": self.me.get("email", ""), "name": self.me.get("name", ""),
                          "via": "agent" if self.me.get("via") == "agent" else "session"}
                if method == "POST" and me and me["via"] == "session" and not chat.origin_ok(self):
                    self._json({"error": "origin not allowed"}, 403)
                    return True
                agent_hub.handle(self, method, path[len("/flynn"):], q, me)
                return True
            if path == "/admin/maintenance" and method in ("POST", "DELETE"):
                self._maintenance_admin(method)
                return True
            if path.startswith("/static/"):
                return False
            if not self.me:
                if method == "GET" and not path.startswith(("/api/", "/r/", "/npm/", "/mcp")):
                    self._redirect("/auth/login?next=" + urllib.parse.quote(self.path, safe=""))
                else:
                    self._json({"error": "sign in first", "login": "/auth/login"}, 401)
                return True
            if path.startswith("/npm/") and method == "GET":                  # the package cache (signed in or agent)
                import pkgcache
                pkgcache.handle(self, path)
                return True
            if self.me.get("via") == "mcp":                      # an AI app: the MCP endpoint and nothing else
                if path != "/mcp":
                    self._json({"error": "an MCP token opens /mcp only"}, 403)
                    return True
                import mcp_server
                with fpaths.use_runs(team.runs_of(self.me)):
                    mcp_server.handle(self, self.me, team.public_url())
                return True
            if self.me.get("via") == "agent":
                if not (method == "POST" and path == "/api/chat/tool"):
                    self._json({"error": "an agent token can only call Studio's tools"}, 403)
                    return True
                return False
            if method == "POST" and not chat.origin_ok(self):
                self._json({"error": "origin not allowed"}, 403)
                return True
            if parts and (parts[0] in TEAM_OFF_PATHS or parts[:2] in (["api", "record"], ["api", "recordings"])):
                if parts[0] == "api":
                    self._json({"error": "This part of Studio runs on your own Mac, not on the team server."}, 404)
                else:
                    self._send(team.page("Not on the team server", "Design system recordings run in Studio on your own "
                                         "Mac.", '<a class="b" href="/">Back to Studio</a>'),
                               "text/html; charset=utf-8", 404)
                return True
            if path == "/api/me":
                self._json(chat.team_payload(self.me))
                return True
            if path == "/api/maintenance" and method == "GET":
                self._json(team.maintenance_get())
                return True
            if path == "/admin" and method == "GET":
                if not self.me.get("owner"):
                    self._send(team.page("Admins only", "This page is for the design systems owner."), "text/html; charset=utf-8", 403)
                    return True
                state = "Admin mode is on for this session." if self.me.get("admin") else "Enter the admin key to unlock admin mode for this session."
                form = ('<form method="post" action="/auth/admin"><input type="password" name="key" placeholder="Admin key" '
                        'autocomplete="current-password"><button>Unlock</button></form>') if not self.me.get("admin") else '<a class="b" href="/settings">Settings</a>'
                self._send(team.page("Admin", state, form), "text/html; charset=utf-8")
                return True
            if path == "/auth/admin" and method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                key = (parse_qs(self.rfile.read(min(n, 4096)).decode(errors="ignore")).get("key") or [""])[0]
                if self.me.get("owner") and team.check_admin_key(key):
                    team.set_admin(team.cookie_token(self), True)
                    team.log(f"admin on {self.me['uid']}")
                    self._redirect("/settings")
                else:
                    team.log(f"admin refused {self.me.get('uid')}")
                    self._send(team.page("Not unlocked", "That key isn't right, or this account isn't the owner."),
                               "text/html; charset=utf-8", 403)
                return True
            return False

        def _maintenance_admin(self, method: str):
            """POST {message, minutes} announces an update, DELETE clears it. Admin only: the owner's unlocked admin session
            (from this site only), or the STUDIO_ADMIN_KEY in an X-Studio-Admin-Key header (deploy/studio/announce.py)."""
            key = self.headers.get("X-Studio-Admin-Key") or ""
            if key:
                ok = team.check_admin_key(key)
            else:
                ok = bool(self.me and self.me.get("admin")) and chat.origin_ok(self)
            if not ok:
                team.log("maintenance announce refused")
                return self._json({"error": "admin only"}, 403)
            if method == "DELETE":
                team.maintenance_clear()
                team.log("maintenance announcement cleared")
                return self._json({"ok": True})
            try:
                body = self._body()
                minutes = int(body.get("minutes", 3))
            except (ValueError, TypeError):
                return self._json({"error": "send JSON like {\"minutes\": 3, \"message\": \"...\"}"}, 400)
            if not isinstance(body, dict) or not 1 <= minutes <= 120:
                return self._json({"error": "minutes must be between 1 and 120"}, 400)
            message = str(body.get("message") or "").strip()[:300]
            m = team.maintenance_set(message, minutes)
            team.log(f"maintenance announced in {minutes} min")
            return self._json({"ok": True, **m})

        def _wheels(self, name: str):
            d = Path(os.environ.get("FLYNN_WHEELS_DIR", "/opt/flynn-wheels"))
            if not name:
                files = sorted(f.name for f in d.glob("*") if f.is_file()) if d.is_dir() else []
                body = "<html><body>" + "".join(f'<a href="{urllib.parse.quote(f)}">{f}</a><br>' for f in files) + "</body></html>"
                return self._send(body.encode(), "text/html; charset=utf-8")
            f = d / Path(unquote(name)).name
            if not f.is_file():
                return self.send_error(404)
            return self._send(f.read_bytes(), "application/octet-stream")

        def _serve_file(self, f: Path):
            import mimetypes
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return self._send(f.read_bytes(), ctype)

        def _mcp_local(self) -> bool:
            """Studio on this computer: /mcp needs a token made in Settings (a web page can't reach it: no cookie, and
            its Origin is refused)."""
            if urlparse(self.path).path != "/mcp":
                return False
            import mcp_server
            o = self.headers.get("Origin")
            if o and not chat.origin_ok(self):
                self._json({"error": "origin not allowed"}, 403)
                return True
            auth = self.headers.get("Authorization") or ""
            m = mcp_server.who(auth[7:].strip()) if auth.startswith("Bearer ") else None
            if not m or m.get("uid"):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Bearer realm="flynn-studio"')
                self.send_header("Content-Type", "application/json")
                body = b'{"error": "a Studio MCP token is needed (Settings, Connect an AI)"}'
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return True
            mcp_server.handle(self, None, f"http://127.0.0.1:{self.server.server_address[1]}")
            return True

        def _mcp_tokens(self, method: str) -> bool:
            """/api/mcp/tokens: a person's AI app connections (list, create, revoke)."""
            u = urlparse(self.path).path
            if not u.startswith("/api/mcp/tokens"):
                return False
            import mcp_server
            if team.on() and (not self.me or self.me.get("via") != "session"):
                self._json({"error": "sign in first"}, 401)
                return True
            if method == "POST" and not chat.origin_ok(self):
                self._json({"error": "origin not allowed"}, 403)
                return True
            uid = (self.me or {}).get("uid", "") if team.on() else ""
            base = (team.public_url() if team.on() else f"http://127.0.0.1:{self.server.server_address[1]}") + "/mcp"
            if method == "GET":
                self._json({"url": base, "tokens": mcp_server.tokens_of(uid)})
            elif u.endswith("/revoke"):
                self._json({"ok": mcp_server.revoke(uid, str(self._body().get("id") or ""))})
            else:
                name = str(self._body().get("name") or "AI app")
                self._json({"url": base, "token": mcp_server.issue(uid, name), "name": name})
            return True

        def do_DELETE(self):
            if team.on():
                if urlparse(self.path).path == "/admin/maintenance":
                    self.me = team.who(self)
                    return self._maintenance_admin("DELETE")
                self.me = team.who(self)
                if self.me and self.me.get("via") == "mcp" and urlparse(self.path).path == "/mcp":
                    self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                return self.send_error(404)
            if self._mcp_local():
                return
            return self.send_error(404)

        def do_GET(self):
            if team.on():
                if self._team("GET"):
                    return
                if self._mcp_tokens("GET"):
                    return
                with fpaths.use_runs(team.runs_of(self.me)):
                    return self._get()
            if self._mcp_local() or self._mcp_tokens("GET"):
                return
            return self._get()

        def do_POST(self):
            if team.on():
                if self._team("POST"):
                    return
                if self._mcp_tokens("POST"):
                    return
                with fpaths.use_runs(team.runs_of(self.me)):
                    return self._post()
            if self._mcp_local() or self._mcp_tokens("POST"):
                return
            return self._post()

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _run(self, rid: str) -> Path:
            base = self._root()
            p = (base / unquote(rid)).resolve()
            if base not in p.parents or not p.is_dir():
                raise KeyError(f"no run {rid}")
            return p

        def _author(self) -> str:
            return ((self.me or {}).get("name") or (self.me or {}).get("email") or "the user") if team.on() else "the user"

        def _inside(self, base: Path, rel: str) -> Path | None:
            p = (base / unquote(rel)).resolve()
            return p if (p == base or base in p.parents) else None

        def _get(self):
            u = urlparse(self.path)
            parts = [x for x in u.path.split("/") if x]
            try:
                if parts and parts[0] == "rec" and u.path.lower().endswith((".html", ".htm", ".js")):
                    return self.send_error(HTTPStatus.FORBIDDEN)      # recorded product pages are never served live
                if len(parts) == 1 and parts[0] in PAGES:
                    return self._page(PAGES[parts[0]])
                if len(parts) == 2 and parts[0] == "app":
                    return self._page("app.html")
                q = parse_qs(u.query)
                if ds.handle_get(self, parts, q) or apps.handle_get(self, parts, q) or chat.handle_get(self, parts, q):
                    return
                if not parts:
                    return self._page("home.html")
                if parts[0] == "static" and len(parts) == 2:
                    f = self._inside(STATIC, parts[1])
                    if f and f.is_file():
                        ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "html": "text/html; charset=utf-8",
                                 "svg": "image/svg+xml", "woff2": "font/woff2", "png": "image/png"}.get(f.suffix[1:], "application/octet-stream")
                        return self._send(f.read_bytes(), ctype)
                    return self.send_error(404)
                if parts[0] == "run" and len(parts) == 2:
                    self._run(parts[1])
                    return self._page("canvas.html")
                if parts[:2] == ["api", "runs"]:
                    return self._json(list_runs())
                if parts[0] == "api" and parts[1] == "run" and len(parts) == 3:
                    return self._json(run_summary(self._run(parts[2])))
                if parts[0] == "api" and parts[1] == "run" and len(parts) == 4 and parts[3] == "edits":
                    return self._json(quick_edits.load(self._run(parts[2])))
                if parts[0] == "api" and parts[1] == "run" and len(parts) == 4 and parts[3] == "cover":
                    run = self._run(parts[2])
                    return self._json(cover.status(run, state(run)))
                if parts[0] == "r" and len(parts) >= 2:
                    run = self._run(parts[1])
                    rest = "/".join(parts[2:])
                    if rest == "__review/overlay.js":
                        return self._send(review.OVERLAY.read_bytes(), "text/javascript; charset=utf-8")
                    if rest == "__review/api/comments":
                        return self._json(review.load(run))
                    if rest == "__review/api/version":
                        page = parse_qs(u.query).get("page", [None])[0]
                        files = [run / page] if page else [run / o["page"] for o in options(run)]
                        mt = max([f.stat().st_mtime for f in files if f.exists()] +
                                 [c.stat().st_mtime for f in files for c in f.parent.glob("*.css")] + [0])
                        # per page, by content: an upload or the database mirror rewrites files unchanged, and a
                        # file-time version reloaded every frame on the canvas (the white flicker, 2026-10-24)
                        pages = {str(f.relative_to(run)): _content_version(f) for f in files if f.exists()}
                        return self._json({"version": mt, "pages": pages})
                    f = self._inside(run, rest)
                    if f is None:
                        return self.send_error(HTTPStatus.FORBIDDEN)
                    if f.is_file() and f.suffix == ".html":
                        html = f.read_text(encoding="utf-8")
                        q = parse_qs(u.query)
                        rel = str(f.relative_to(run))
                        inject = ""
                        if "thumb" not in q:
                            inject += f'<script src="/r/{run.name}/__review/overlay.js" data-page="{rel}"></script>'
                        if "capture" in q:  # Figma capture script attached to a request by flynn
                            req = next((r for r in state(run).get("requests", []) if r["id"] == q["capture"][0]), None)
                            if req and req.get("capture_script"):
                                inject += f"<script>{req['capture_script']}</script>"
                        html = html.replace("</body>", inject + "\n</body>") if "</body>" in html else html + inject
                        return self._send(html.encode(), "text/html; charset=utf-8")
                    if f.is_file():
                        if team.on():
                            return self._serve_file(f)
                        self.path = "/" + str(f.relative_to(root))
                        return super().do_GET()
                    return self.send_error(404)
            except KeyError as e:
                return self._json({"error": str(e)}, 404)
            return self.send_error(404)

        def _post(self):
            parts = [x for x in urlparse(self.path).path.split("/") if x]
            try:
                if len(parts) >= 2 and parts[0] == "api" and parts[1] in ("chat", "settings", "systems", "record", "apps", "models"):
                    b = self._body()
                    if ds.handle_post(self, parts, b) or apps.handle_post(self, parts, b) or chat.handle_post(self, parts, b):
                        return
                if parts[0] == "r" and len(parts) >= 4 and parts[2] == "__review":
                    run = self._run(parts[1])
                    b = self._body()
                    if parts[3:] == ["api", "comments"]:
                        return self._json(review.add_comment(run, b.get("text", ""), b.get("anchor") or {}, self._author(), b.get("page", "")), 201)
                    if parts[3:5] == ["api", "comments"] and len(parts) == 6:
                        return self._json(review.update(run, int(parts[5]), b.get("status"), b.get("reply"), author=self._author()))
                if parts[0] == "api" and parts[1] == "run" and len(parts) == 5 and parts[3] == "edits":
                    if not chat.origin_ok(self):
                        return self._json({"error": "origin not allowed"}, 403)
                    b = self._body()
                    return self._json(quick_edits.set_status(self._run(parts[2]), int(parts[4]), b.get("status", ""), b.get("note")))
                if parts[0] == "api" and parts[1] == "run" and len(parts) == 4:
                    run = self._run(parts[2])
                    b = self._body()
                    if parts[3] == "cover":      # the title card: made in a background thread, polled with GET
                        if not chat.origin_ok(self):
                            return self._json({"error": "origin not allowed"}, 403)
                        if not options(run):
                            return self._json({"error": "this run has no page yet"}, 400)
                        return self._json(cover.start(run, COVER_HOST, self.me if team.on() else None), 202)
                    if parts[3] == "pick":
                        return self._json(pick(run, b["option"], b.get("note", "")))
                    if parts[3] == "export":
                        return self._json(export(run, b["kind"], b.get("option", "main")))
                    if parts[3] == "edits":
                        if not chat.origin_ok(self):
                            return self._json({"error": "origin not allowed"}, 403)
                        return self._json(quick_edits.add(run, b.get("edits") or []), 201)
                    if parts[3] == "snapshot":
                        return self._json({"version": snapshot(run, b.get("option"), b.get("label", ""))})
            except (KeyError, ValueError, RuntimeError) as e:
                return self._json({"error": str(e)}, 400)
            return self.send_error(404)

    return H


def _stop_maintenance(pidfile: str) -> None:
    """Stop the startup maintenance server (adapters/studio/maintenance.py) named in `pidfile`, right before binding its port."""
    try:
        pid = int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    # No waiting on the pid (a zombie still answers kill 0): _bind keeps trying until the listener has let go.


def _bind(host: str, port: int, root: Path, retry_s: float = 0) -> ThreadingHTTPServer:
    """Bind the port; after a takeover keep trying briefly in case the old listener is still closing (SO_REUSEADDR is
    on for ThreadingHTTPServer, so lingering connections do not block it)."""
    end = time.time() + retry_s
    while True:
        try:
            return ThreadingHTTPServer((host, port), make_handler(root))
        except OSError as e:
            if e.errno != errno.EADDRINUSE or time.time() >= end:
                raise
            time.sleep(0.05)


def serve(port: int = 8770, open_browser: bool = True, root: Path | None = None, host: str = "127.0.0.1",
          takeover_pidfile: str | None = None) -> ThreadingHTTPServer:
    root = root or runs_root()
    if team.on():
        import agent_hub
        agent_hub.RUNS_FOR = team.runs_for_email          # run folders from an agent go to its person's own folder
        agent_hub.APPS_FOR = team.apps_for_email          # and saved copies of their apps
    if team.on():
        team.maintenance_clear()             # an announcement from before this start has done its job
    if takeover_pidfile:
        _stop_maintenance(takeover_pidfile)
    httpd = _bind(host, port, root, retry_s=8 if takeover_pidfile else 0)
    httpd.daemon_threads = True
    chat.STUDIO_PORT = httpd.server_address[1]
    chat.SRV = chat.Srv(Path(root), snapshot, export, run_summary, add_link, list_runs, pick)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"flynn Studio: {url}  (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8770); s.add_argument("--no-open", action="store_true")
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 on a team server (STUDIO_TEAM=1), behind the platform's https")
    s.add_argument("--takeover-pidfile", help="pid file of the startup maintenance server (maintenance.py): stopped just before binding")
    n = sub.add_parser("snapshot"); n.add_argument("run"); n.add_argument("--option"); n.add_argument("--label", default="")
    q = sub.add_parser("requests"); q.add_argument("--all", action="store_true")
    d = sub.add_parser("done"); d.add_argument("run"); d.add_argument("id"); d.add_argument("--link", required=True)
    f = sub.add_parser("failed"); f.add_argument("run"); f.add_argument("id"); f.add_argument("--reason", required=True)
    c = sub.add_parser("figma-script"); c.add_argument("run"); c.add_argument("id"); c.add_argument("script")
    e = sub.add_parser("export"); e.add_argument("run"); e.add_argument("kind"); e.add_argument("--option", default="main")
    a = ap.parse_args(argv)

    def run_path(x):
        p = Path(x)
        return p if p.is_dir() else runs_root() / x

    if a.cmd == "serve":
        if a.host != "127.0.0.1" and not team.on():
            ap.error("--host other than 127.0.0.1 needs STUDIO_TEAM=1 (sign-in); Studio has no sign-in otherwise")
        httpd = serve(a.port, not a.no_open, host=a.host, takeover_pidfile=a.takeover_pidfile)
        chat.repair_all()             # close turns a restart cut off (only the real server, never tests)
        import os
        import signal

        def _bye(*_):
            chat.shutdown()
            apps.stop_all()
            os._exit(0)
        signal.signal(signal.SIGTERM, _bye)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    elif a.cmd == "snapshot":
        print(snapshot(run_path(a.run), a.option, a.label))
    elif a.cmd == "requests":
        for r in pending_requests(a.all):
            print(f"{r['run']}  {r['id']}  {r['kind']}  option={r['option']}  {r['status']}  page={r['page']}" + (f"  link={r['link']}" if r.get("link") else ""))
    elif a.cmd == "done":
        print(json.dumps(close_request(run_path(a.run), a.id, "done", link=a.link), ensure_ascii=False))
    elif a.cmd == "failed":
        print(json.dumps(close_request(run_path(a.run), a.id, "failed", reason=a.reason), ensure_ascii=False))
    elif a.cmd == "figma-script":
        req = close_request(run_path(a.run), a.id, "capturing", script=Path(a.script).read_text(encoding="utf-8"))
        print(f"capture URL: http://127.0.0.1:8770/r/{run_path(a.run).name}/{req['page']}?capture={a.id}")
    elif a.cmd == "export":
        print(json.dumps(export(run_path(a.run), a.kind, a.option), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
