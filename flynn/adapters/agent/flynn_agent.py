#!/usr/bin/env python3
"""flynn agent: runs your flynn desk turns on this computer, with your own Claude login.

  flynn-agent login <server URL> <token>   remember the server and your connection token (from /flynn/agent/connect)
  flynn-agent start                        stay connected (the installer runs this in the background at login)
  flynn-agent update                       download flynn's latest files from the server
  flynn-agent status                       is it set up, and can it reach the server?
  flynn-agent import-letter <run folder> --type release-note [--title T] [--team ID]
                                           bring a page made in local flynn (Studio) into flynn desk as a letter

How it works: the agent asks the server for work (long polling over HTTPS, so nothing has to reach in). A turn
arrives with the prompt, the session to resume, flynn's instructions and the allowed tools; the agent runs it with
the Claude Agent SDK (or the claude CLI) in ~/.flynn-agent/flynn, and sends the panel events back as they come.
The desk tools call the server directly with your token. Design system recordings run here too: flynn's Chrome
opens on this computer, and the recording goes to the server when it ends (~/.flynn-agent/recordings). Run folders are made here; the agent uploads each run
folder the turn changed before it reports the turn finished, so the canvas builds from the same files.

Files: ~/.flynn-agent/config.json (mode 600), ~/.flynn-agent/flynn (flynn's files), ~/.flynn-agent/agent.log.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import re
import shutil
import socket
import sys
import tarfile
import threading
import time
import urllib.error
import uuid
import urllib.request
from pathlib import Path

VERSION = "1"
HOME = Path(os.environ.get("FLYNN_AGENT_HOME", str(Path.home() / ".flynn-agent")))
ROOT = HOME / "flynn"
CONFIG = HOME / "config.json"
LOG = HOME / "agent.log"


def log(msg: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
    if os.environ.get("FLYNN_AGENT_VERBOSE"):
        print(msg, flush=True)


# ---------- the outbox and the offline record ----------
# A small team server restarts on every deploy, and a laptop drops off the VPN: events and run folders that can't be
# delivered are saved here (one file per turn, in order) and sent when the server is back, instead of being lost
# after three minutes (2026-10-24). The batch id makes a resend harmless: the server never applies a batch twice.

OUTBOX = HOME / "outbox"
OUTBOX_KEEP = 1800.0          # a turn the server doesn't know: keep trying this long (it may still be restarting)
_outbox_lock = threading.RLock()


def outbox_add(jid: str, entry: dict) -> None:
    with _outbox_lock:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        with (OUTBOX / f"{jid}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({**entry, "at": entry.get("at") or time.time()}, ensure_ascii=False) + "\n")


def outbox_jobs() -> list[str]:
    try:
        return sorted(f.stem for f in OUTBOX.glob("*.jsonl") if f.stat().st_size)
    except OSError:
        return []


def outbox_has(jid: str) -> bool:
    f = OUTBOX / f"{jid}.jsonl"
    return f.is_file() and f.stat().st_size > 0


def deliver_events(srv: "Server", jid: str, evs: list[dict], bid: str, age: float = 0.0,
                   tries: tuple = (1, 2, 4)) -> bool:
    """Send one batch: True when it arrived, or when the server has not known this turn for OUTBOX_KEEP (give up)."""
    sent = srv.send_events(jid, evs, bid)
    if sent is True:
        return True
    if sent is False:
        return age > OUTBOX_KEEP
    for i, delay in enumerate((0,) + tuple(tries)):
        if delay:
            time.sleep(delay)
        try:
            srv.post("/flynn/agent/events", {"job": jid, "events": evs, "bid": bid}, timeout=60)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return age > OUTBOX_KEEP
        except (urllib.error.URLError, OSError):
            pass
    return False


def outbox_flush(srv: "Server") -> int:
    """Send what is saved, oldest first, stopping at the first failure in each turn so the order holds. Returns how
    many entries are still waiting."""
    left = 0
    with _outbox_lock:
        for jid in outbox_jobs():
            f = OUTBOX / f"{jid}.jsonl"
            try:
                entries = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
            except (OSError, ValueError):
                entries = []
            done = 0
            for e in entries:
                age = time.time() - float(e.get("at") or 0)
                try:
                    if e.get("run"):
                        run = ROOT / "runs" / str(e["run"])
                        if run.is_dir():
                            upload_run(srv, run)
                        ok = True
                    else:
                        ok = deliver_events(srv, jid, e.get("events") or [], str(e.get("bid") or ""), age, tries=())
                except (urllib.error.URLError, OSError):
                    ok = age > 6 * 3600                # a run folder: keep trying for a working day
                if not ok:
                    break
                done += 1
            rest = entries[done:]
            if rest:
                f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rest), encoding="utf-8")
                left += len(rest)
            else:
                f.unlink(missing_ok=True)
            if done:
                log(f"turn {jid[:8]}: delivered {done} saved item(s)" + (f", {len(rest)} still waiting" if rest else ""))
    return left


NET: dict = {"since": 0.0, "reason": "", "last": None}
REASONS = {
    "dns": "couldn't find the Studio server's address (is the VPN on?)",
    "tls": "the secure connection failed (a proxy or the computer's clock?)",
    "refused": "the server refused the connection (it may be restarting)",
    "server": "the server answered with an error (it may be restarting)",
    "timeout": "the server didn't answer in time (a slow or busy network)",
    "network": "no network connection",
}


def why_offline(e: BaseException) -> str:
    t = f"{type(e).__name__} {e} {getattr(e, 'reason', '')}".lower()
    if isinstance(e, urllib.error.HTTPError):
        return "server" if e.code >= 500 else "network"
    if isinstance(getattr(e, "reason", None), socket.gaierror) or isinstance(e, socket.gaierror) or \
            "nodename nor servname" in t or "name or service not known" in t or "getaddrinfo" in t:
        return "dns"
    if "ssl" in t or "certificate" in t:
        return "tls"
    if "refused" in t:
        return "refused"
    if "timed out" in t or "timeout" in t:
        return "timeout"
    return "network"


def net_down(e: BaseException) -> None:
    if not NET["since"]:
        NET["since"], NET["reason"] = time.time(), why_offline(e)
        log(f"offline: {REASONS[NET['reason']]} ({type(e).__name__}: {e})"[:300])


def net_up() -> None:
    if NET["since"]:
        iso = lambda x: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))   # noqa: E731
        NET["last"] = {"since": iso(NET["since"]), "until": iso(time.time()), "reason": NET["reason"],
                       "text": REASONS.get(NET["reason"], ""), "minutes": round((time.time() - NET["since"]) / 60, 1)}
        log(f"back online after {NET['last']['minutes']} min ({NET['reason']})")
        NET["since"], NET["reason"] = 0.0, ""


def link_info(worker) -> dict:
    """What the agent tells the server with every status: its turn, what it still has to deliver, its last outage."""
    return {"busy": worker.job or "", "outbox": ",".join(outbox_jobs()), "offline": NET["last"] or {}}


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(c: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)


_CTX: list = []


def _ssl():
    """The same certificate checks as the live connection (wsproto.ssl_context): the computer's trust store, else
    certifi. A python.org Python on a Mac otherwise refuses every https server until "Install Certificates" runs."""
    if not _CTX:
        import ssl
        ctx = None
        try:
            import truststore
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # noqa: BLE001
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                ctx = ssl.create_default_context()
        _CTX.append(ctx)
    return _CTX[0]


class Server:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token
        self.ws = None                    # the live connection (wsproto.WebSocket) while it is open
        self.acks: dict[int, list] = {}   # batch number -> [event, delivered?]
        self._seq = 0
        self._seq_lock = threading.Lock()

    def send_events(self, job: str, evs: list[dict], bid: str, timeout: float = 10) -> bool | None:
        """Send a batch over the live connection and wait for the server's receipt. True: delivered. False: the
        server no longer has this job. None: not delivered this way (no connection, or no receipt in time); the
        caller then posts it over HTTPS with the same batch id, which the server never applies twice."""
        ws = self.ws
        if ws is None:
            return None
        with self._seq_lock:
            self._seq += 1
            n = self._seq
        slot = [threading.Event(), None]
        self.acks[n] = slot
        try:
            ws.send_json({"type": "events", "seq": n, "job": job, "events": evs, "bid": bid})
        except Exception:  # noqa: BLE001 - the connection just closed
            self.acks.pop(n, None)
            return None
        slot[0].wait(timeout)
        self.acks.pop(n, None)
        return slot[1]

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 60, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": f"flynn-agent/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
            out = r.read()
        return out if raw else json.loads(out or b"{}")

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, body, **kw):
        return self._req("POST", path, body, **kw)


# ---------- files ----------

def update(srv: Server) -> str:
    """Download flynn's files and unpack them over ~/.flynn-agent/flynn (runs/ is never touched)."""
    data = srv.get("/flynn/agent/bundle.tgz", raw=True, timeout=600)
    tmp = HOME / "flynn.new"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                continue
            tf.extract(m, tmp)
    runs = ROOT / "runs"
    if runs.is_dir():
        runs.rename(tmp / "runs")
    old = HOME / "flynn.old"
    shutil.rmtree(old, ignore_errors=True)
    if ROOT.exists():
        ROOT.rename(old)
    tmp.rename(ROOT)
    shutil.rmtree(old, ignore_errors=True)
    (ROOT / "runs").mkdir(exist_ok=True)
    v = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file() else ""
    log(f"flynn files updated ({v})")
    return v


def pack_run(run: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for f in sorted(run.rglob("*")):
            if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts:
                tf.add(f, arcname=str(f.relative_to(run)), recursive=False)
    return buf.getvalue()


UPLOAD_PIECE = 600_000      # bytes per request before base64: under the ~1 MB a proxy in front of the server allows


def post_tgz(srv: Server, path: str, fields: dict, data: bytes) -> dict:
    """Send a gzip tar, in pieces when it is large (the server puts them back together)."""
    if len(data) <= UPLOAD_PIECE:
        return srv.post(path, {**fields, "tgz": base64.b64encode(data).decode()}, timeout=600)
    import uuid
    uid, n = uuid.uuid4().hex, -(-len(data) // UPLOAD_PIECE)
    r = {}
    for i in range(n):
        piece = data[i * UPLOAD_PIECE:(i + 1) * UPLOAD_PIECE]
        r = srv.post(path, {**fields, "tgz": base64.b64encode(piece).decode(), "upload": uid, "part": i, "parts": n},
                     timeout=600)
    return r


def upload_run(srv: Server, run: Path) -> None:
    post_tgz(srv, "/flynn/agent/run-upload", {"run": run.name}, pack_run(run))
    log(f"uploaded runs/{run.name}")


# What a letter needs from a local run: the page and its sources. Old versions, shots and exports stay local.
LETTER_KEEP = ("page/", "content.md", "content-brief.md", "feedback.md", "crops.json", "contract.md")
LETTER_SKIP = re.compile(r"\.v\d[^/]*\.(html|css)$")      # page/index.v3-approved.html and the like


def pack_letter(run: Path) -> bytes:
    # Pictures go only when the page uses them: unused screenshots (which can show real data) stay on this computer.
    used = "".join((run / "page" / n).read_text(errors="ignore") for n in ("index.html", "style.css")
                   if (run / "page" / n).is_file())
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for f in sorted(run.rglob("*")):
            rel = f.relative_to(run).as_posix()
            if rel.startswith("page/img/") and f.name not in used:
                continue
            if (f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts
                    and rel.startswith(LETTER_KEEP) and not LETTER_SKIP.search(rel)):
                tf.add(f, arcname=rel, recursive=False)
    return buf.getvalue()


def import_letter(srv: Server, run: Path, kind: str, title: str, team: str | None) -> dict:
    """Make a letter on the server, then put this run's page in place of the template before its first opening
    (the canvas builds from the page the first time the file opens)."""
    if not (run / "page" / "index.html").is_file():
        raise ValueError(f"{run} has no page/index.html")
    body = {"type": kind, "title": title}
    if team:
        body["team"] = team
    meta = srv.post("/flynn/api/letters/new", body, timeout=120)
    if meta.get("error"):
        raise ValueError(meta["error"])
    name = Path(meta["run"]).name
    post_tgz(srv, "/flynn/agent/run-upload", {"run": name}, pack_letter(run))
    meta["link"] = f"{srv.url}/#/workspace?team-id={meta.get('team')}&file-id={meta['file']}"
    return meta


def runs_changed(since: float) -> list[Path]:
    out = []
    for run in sorted((ROOT / "runs").glob("*")):
        if run.is_dir() and any(f.is_file() and f.stat().st_mtime >= since for f in run.rglob("*")):
            out.append(run)
    return out


def place_files(files: dict, text: str) -> str:
    """Files on the server the prompt points at: save them here and point the prompt at the copies."""
    for remote, b64 in (files or {}).items():
        d = HOME / "inbox" / hashlib.sha1(remote.encode()).hexdigest()[:10]
        d.mkdir(parents=True, exist_ok=True)
        local = d / Path(remote).name
        local.write_bytes(base64.b64decode(b64))
        text = text.replace(remote, str(local))
    return text


RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*[a-z0-9]$")


def fetch_run(srv: Server, rid: str) -> int:
    """The server's copy of a run (comments, picks and edits made in the browser) into runs/<rid> before a turn."""
    if not RUN_NAME.match(rid or ""):
        return 0
    try:
        data = srv.get(f"/flynn/agent/run-download?run={rid}", raw=True, timeout=600)
    except (urllib.error.URLError, OSError) as e:
        log(f"could not fetch runs/{rid}: {e}")
        return 0
    dest = ROOT / "runs" / rid
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or not m.isfile():
                continue
            tf.extract(m, dest)
            n += 1
    log(f"fetched runs/{rid} ({n} files)")
    return n


def local_mcp(servers: dict, sub, srv: Server) -> dict:
    """A turn's MCP servers as they run here: flynn's files at this computer's copy, this Python, and Studio's tools
    calling the server with this agent's token."""
    out = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        c = dict(cfg)
        args = [sub(str(a)) for a in c.get("args") or []]
        c["args"] = args
        cmd = sub(str(c.get("command") or ""))
        c["command"] = cmd if cmd and Path(cmd).exists() else sys.executable
        env = {k: sub(str(v)) for k, v in (c.get("env") or {}).items()}
        if "STUDIO_CONV" in env:
            env.update({"STUDIO_URL": srv.url, "STUDIO_BEARER": srv.token, "STUDIO_RUNS": str(ROOT / "runs")})
            env.pop("STUDIO_TOKEN", None)
        c["env"] = env
        out[name] = c
    return out


# ---------- the App builder (Studio on a team server: apps live and run on this computer) ----------

APPS = HOME / "apps"
APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
APP_SKIP = {"node_modules", "dist", ".vite"}


def _apps():
    os.environ["FLYNN_APPS_DIR"] = str(APPS)
    sys.path.insert(0, str(ROOT / "adapters" / "studio"))
    import apps
    return apps


def pack_app(d: Path) -> bytes:
    """The app's source and its history (.git), without installed packages or builds."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d)
            if APP_SKIP & set(rel.parts) or (rel.parts[0] == ".flynn" and f.suffix == ".log"):
                continue
            if f.is_file() and not f.is_symlink():
                tf.add(f, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


def save_app(srv: Server, name: str) -> None:
    d = APPS / name
    if d.is_dir():
        post_tgz(srv, "/flynn/agent/app-upload", {"name": name}, pack_app(d))
        log(f"saved app {name} on the server")


def fetch_app(srv: Server, name: str) -> bool:
    """An app made on another computer: bring its saved copy here (its packages install again on first preview)."""
    d = APPS / name
    if d.is_dir() or not APP_NAME.match(name or ""):
        return d.is_dir()
    try:
        data = srv.get(f"/flynn/agent/app-download?name={name}", raw=True, timeout=600)
    except (urllib.error.URLError, OSError):
        return False
    tmp = APPS / f".{name}.new"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                continue
            tf.extract(m, tmp)
    tmp.rename(d)
    log(f"fetched app {name} from the server")
    return True


def use_package_cache(srv: Server) -> bool:
    """When the server keeps a package cache (Studio's /npm/), npm runs started by this agent install through it.
    The agent's own npm settings file (npm_config_userconfig), so the person's ~/.npmrc and their other projects are
    never touched; lockfiles keep public addresses (apps.normalize_lock); a failed install retries from npmjs.org."""
    try:
        j = srv.get("/npm/-/ping", timeout=15)
    except Exception:  # noqa: BLE001 - no cache there (flynn desk, or an older server)
        return False
    if not j.get("ok"):
        return False
    from urllib.parse import urlsplit
    reg = srv.url + "/npm/"
    host = urlsplit(reg)
    rc = HOME / "npmrc"
    rc.write_text(f"registry={reg}\n//{host.netloc}{host.path}:_authToken={srv.token}\n"
                  "audit=false\nfund=false\nupdate-notifier=false\nprefer-offline=true\n")
    os.chmod(rc, 0o600)
    os.environ.update({"npm_config_userconfig": str(rc), "FLYNN_NPM_PROXY": reg})
    # Node: the launcher already put flynn's private copy first only when it is newer than the Mac's own.
    log("packages install through the server's cache")
    return True


def send_event(srv: Server, jid: str, ev: dict) -> None:
    """One event for a job: over the live connection, else over HTTPS (retried)."""
    bid = uuid.uuid4().hex
    if srv.send_events(jid, [ev], bid) is not None:
        return
    for delay in (1, 2, 4, 8, 15):
        try:
            srv.post("/flynn/agent/events", {"job": jid, "events": [ev], "bid": bid}, timeout=60)
            return
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(delay)


def app_job(srv: Server, job: dict) -> None:
    op, name, args = str(job.get("op") or ""), job.get("name"), job.get("args") or {}
    try:
        apps = _apps()
        if name:
            fetch_app(srv, str(name))
        data = apps.local_op(op, name, args)
        res = {"t": "result", "ok": True, "data": data}
        if op in apps.CHANGES:
            try:
                save_app(srv, str(name or (data or {}).get("name") or ""))
            except Exception as e:  # noqa: BLE001 - the change itself worked
                log(f"could not save app {name} on the server: {e}")
        if op == "list":                     # apps saved on the server but not on this computer yet
            here = {a.get("name") for a in data.get("apps", [])}
            for extra in job.get("saved") or []:
                if extra not in here:
                    data["apps"].append({"name": extra, "description": "Saved on the server; opens here on first use",
                                         "remote": True})
    except Exception as e:  # noqa: BLE001 - goes back to the browser
        try:
            res = _apps().op_error(e)
        except Exception:  # noqa: BLE001
            res = {"ok": False, "kind": "runtime", "error": f"{type(e).__name__}: {e}"[:400]}
        res["t"] = "result"
        log(f"app {op} {name}: {res['error'][:120]}")
    send_event(srv, job["id"], res)


_CLAUDE: dict = {}


def _version(cmd: str) -> tuple:
    try:
        import subprocess
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, ValueError, Exception):  # noqa: BLE001
        return ()
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def best_claude() -> str:
    """The newest Claude Code on this computer: the person's own (on the PATH or ~/.local/bin) or the one that comes
    with the Agent SDK. Checked once per agent start; a newer one installed later is used after the next restart."""
    if "path" not in _CLAUDE:
        cands = [shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude")]
        try:
            import claude_agent_sdk
            cands.append(str(Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"))
        except Exception:  # noqa: BLE001
            pass
        found = [(c, _version(c)) for c in dict.fromkeys(c for c in cands if c and os.access(c, os.X_OK))]
        found = [f for f in found if f[1]]
        _CLAUDE["path"] = max(found, key=lambda f: f[1])[0] if found else (shutil.which("claude") or "claude")
        if found:
            log(f"Claude Code: {_CLAUDE['path']} ({'.'.join(map(str, max(f[1] for f in found)))})")
    return _CLAUDE["path"]


# ---------- turns ----------

def _engines():
    sys.path.insert(0, str(ROOT / "adapters" / "penpot" / "panel"))
    import engines
    return engines


class Worker:
    def __init__(self, srv: Server):
        self.srv = srv
        self.engine = None
        self.job: str | None = None

    def run(self, job: dict) -> None:
        # Busy at once: the server takes an agent that calls in idle while a turn is open as having lost the turn's end
        # (agent_hub ORPHAN, 20 s), so nothing slow may come before this (2026-10-21: version checks took 25 s here).
        self.job = job["id"]
        try:
            self._run(job)
        except Exception as e:  # noqa: BLE001 - setup failed before the turn began: say so in the chat, end the turn
            log(f"turn {job['id'][:8]} could not start: {type(e).__name__}: {e}")
            send_event(self.srv, job["id"], {"t": "error", "text": f"flynn agent: the turn could not start "
                                                                   f"({type(e).__name__}: {e})"[:400]})
            send_event(self.srv, job["id"], {"t": "turn_end", "ok": False, "cost": None})
        finally:
            if self.job == job["id"]:
                self.job, self.engine = None, None

    def _run(self, job: dict) -> None:
        engines = _engines()
        t, root = job["turn"], job.get("root") or ""
        app = job.get("app") if APP_NAME.match(str(job.get("app") or "")) else None
        if app:
            fetch_app(self.srv, app)
        app_dir = str(APPS / app) if app else ""

        def sub(s: str) -> str:
            s = s.replace(root, str(ROOT)) if root else s
            return s.replace("__APP_DIR__", app_dir) if app else s
        prompt = place_files(job.get("files") or {}, sub(t["prompt"]))
        extra = {k: t[k] for k in ("agent", "model", "effort", "budget_usd") if k in t}
        if isinstance(t.get("mcp"), dict):
            extra["mcp"] = local_mcp(t["mcp"], sub, self.srv)
        for rid in job.get("sync_runs") or []:
            fetch_run(self.srv, rid)
        turn = engines.Turn(prompt=prompt, session=t.get("session"), system=sub(t.get("system") or ""),
                            allow=[sub(a) for a in t.get("allow") or []], deny=[sub(a) for a in t.get("deny") or []],
                            file_id=t.get("file_id") or "", cwd=app_dir or str(ROOT),
                            claude=best_claude(),
                            env={**(t.get("env") or {})}, **extra)
        self.job = job["id"]
        started = time.time()
        buf: list[dict] = []
        lock = threading.Lock()
        state = {"end": None, "missing_session": False}

        send_lock = threading.Lock()

        def flush():
            """Send buffered events in order. A server restart or a dropped VPN must not lose them (2026-10-07: a
            502 dropped a turn's end; 2026-10-24: events were lost after three minutes), so what can't be sent in a
            few seconds goes to the outbox, and everything after it queues behind it so the order holds."""
            with send_lock:
                with lock:
                    evs, buf[:] = list(buf), []
                if not evs:
                    return
                bid = uuid.uuid4().hex
                if outbox_has(job["id"]):
                    outbox_add(job["id"], {"bid": bid, "events": evs})
                    outbox_flush(self.srv)
                    return
                if deliver_events(self.srv, job["id"], evs, bid):
                    return
                outbox_add(job["id"], {"bid": bid, "events": evs})
                log(f"turn {job['id'][:8]}: {len(evs)} events saved to send when the server is back")

        def emit(ev: dict):
            if ev.get("t") == "turn_end":
                state["end"] = ev                       # held back until the run folders are uploaded
                return
            if ev.get("t") == "error" and "No conversation found" in str(ev.get("text")):
                state["missing_session"] = True         # this session was made on another computer
                return
            with lock:
                buf.append(ev)
            if ev.get("t") != "delta" or len(buf) > 40:
                flush()

        stop_flush = threading.Event()

        def ticker():                           # streamed text: every 0.1s on the live connection, 0.4s over HTTPS
            while not stop_flush.wait(0.1 if self.srv.ws else 0.4):
                flush()
        threading.Thread(target=ticker, daemon=True).start()
        os.environ.update({"FLYNN_DESK_PANEL": self.srv.url + "/flynn", "FLYNN_DESK_TOKEN": self.srv.token,
                           "FLYNN_DESK_RUNS": str(ROOT / "runs"), "FLYNN_ENGINE": "local"})
        try:
            self.engine = engines.choose("local")
            log(f"turn {job['id'][:8]} start ({self.engine.name})")
            self.engine.run(turn, emit)
            if state["missing_session"] and turn.session:
                emit({"t": "info", "title": "New session", "rows": [["Note", "This conversation started on another "
                      "computer, so flynn continues it in a new session here."]]})
                state["end"], state["missing_session"] = None, False
                turn.session = None
                self.engine.run(turn, emit)
        except Exception as e:  # noqa: BLE001 - shown in the tab
            emit({"t": "error", "text": f"flynn agent: {type(e).__name__}: {e}"[:400]})
        finally:
            stop_flush.set()
            for run in runs_changed(started):
                if outbox_has(job["id"]):
                    outbox_add(job["id"], {"run": run.name})
                    continue
                try:
                    upload_run(self.srv, run)
                except (urllib.error.URLError, OSError) as e:      # sent later, before the turn's end
                    log(f"runs/{run.name} saved to send later: {e}")
                    outbox_add(job["id"], {"run": run.name})
                except Exception as e:  # noqa: BLE001
                    emit({"t": "error", "text": f"Could not send runs/{run.name} to the server: {e}"[:300]})
            with lock:
                buf.append(state["end"] or {"t": "turn_end", "ok": False, "cost": None})
            flush()
            log(f"turn {job['id'][:8]} end")
            self.engine, self.job = None, None

    def stop(self, job_id: str) -> None:
        if self.engine and self.job == job_id:
            self.engine.stop()


# ---------- recordings ----------

RECORDINGS: dict[str, "Recorder"] = {}


class Recorder:
    """A design system recording the server asked for: flynn's Chrome opens here, the person browses and presses
    Snap, and when it ends the folder goes to the server, which mines it and builds the canvas page."""

    def __init__(self, srv: Server, job: dict):
        self.srv, self.job = srv, job
        self.session = None

    def post(self, ev: dict) -> None:
        bid = uuid.uuid4().hex
        if self.srv.send_events(self.job["id"], [ev], bid) is not None:
            return
        for delay in (1, 2, 4, 8, 15, 15, 15):
            try:
                self.srv.post("/flynn/agent/events", {"job": self.job["id"], "events": [ev], "bid": bid}, timeout=60)
                return
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(delay)

    def run(self) -> None:
        jid = self.job["id"]
        RECORDINGS[jid] = self
        err = ""
        try:
            try:
                import selenium  # noqa: F401
            except ImportError:                       # agents installed before recordings ran here
                self.post({"t": "rec-status", "state": "starting", "current": "installing the recorder (once)"})
                import subprocess
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                "--find-links", self.srv.url + "/flynn/agent/wheels/", "selenium"], check=True)
            sys.path.insert(0, str(ROOT / "adapters" / "recorder"))
            import recorder
            recorder.ROOT = HOME / "recordings"       # outside flynn's files, which an update replaces
            s = recorder.Session(self.job["cfg"])
            s.id = str(self.job.get("session") or s.id)
            s.folder = recorder.ROOT / recorder.slug(s.cfg["system"]) / s.id
            self.session = s
            log(f"recording {s.id} start ({s.cfg.get('how')})")
            s.start()
            while s._thread and s._thread.is_alive():
                self.post({"t": "rec-status", "state": s.state, "error": s.error, "current": s.current,
                           "pages": s.pages})
                s._thread.join(2)
            err = s.error
            post_tgz(self.srv, "/flynn/agent/rec-upload", {"job": jid}, pack_run(s.folder))
            log(f"recording {s.id} uploaded ({len(s.pages)} pages)")
        except Exception as e:  # noqa: BLE001 - shown in the toolbar
            err = f"{type(e).__name__}: {e}"[:400]
            log(f"recording failed: {err}")
        finally:
            folder = str(self.session.folder) if self.session else ""
            self.post({"t": "rec-done", "root": folder, "error": err})
            RECORDINGS.pop(jid, None)


WS_TIMEOUT = 70.0       # no message from the server this long (it pings every 20s): the connection is dead
POLL_FALLBACK = 300.0   # a server or network that can't do WebSockets: poll this long before trying again


def handle_job(srv: Server, worker: "Worker", job: dict) -> None:
    if job.get("kind") == "stop":
        worker.stop(job.get("id"))
    elif job.get("kind") == "record":
        threading.Thread(target=Recorder(srv, job).run, daemon=True).start()
    elif job.get("kind") in ("rec-snap", "rec-stop"):
        r = RECORDINGS.get(job.get("id") or "")
        if r and r.session:
            (r.session.capture_now if job["kind"] == "rec-snap" else (lambda: r.session.stop(wait=0)))()
    elif job.get("kind") == "app":
        threading.Thread(target=app_job, args=(srv, job), daemon=True).start()
    elif job.get("kind") == "turn":
        threading.Thread(target=worker.run, args=(job,), daemon=True).start()


class Refused(Exception):
    """The server refused this computer's token."""


def live(srv: Server, worker: "Worker", host: str, on_bundle) -> None:
    """The live connection: jobs arrive the moment they are queued, events go up as they happen. Returns when the
    connection drops (the caller reconnects)."""
    sys.path.insert(0, str(ROOT / "adapters" / "penpot" / "panel"))
    import wsproto
    try:
        ws = wsproto.connect(srv.url + "/flynn/agent/ws", {"Authorization": f"Bearer {srv.token}",
                                                           "User-Agent": f"flynn-agent/{VERSION}"})
    except ConnectionRefusedError as e:
        if "HTTP 401" in str(e) or "HTTP 403" in str(e):
            raise Refused(str(e)) from e
        raise
    ws.settimeout(WS_TIMEOUT)
    srv.ws = ws
    log("live connection open")
    net_up()
    stop = threading.Event()

    def status():
        last, n = None, 0
        while not stop.wait(1):
            busy, n = (worker.job or "", ",".join(outbox_jobs())), n + 1
            if busy != last or n >= 10:
                try:
                    ws.send_json({"type": "status", "host": host, "version": VERSION, **link_info(worker)})
                except Exception:  # noqa: BLE001
                    return
                last, n = busy, 0
    try:
        ws.send_json({"type": "hello", "host": host, "version": VERSION, **link_info(worker)})
        threading.Thread(target=status, daemon=True).start()
        while True:
            m = ws.recv_json()
            kind = m.get("type") if isinstance(m, dict) else None
            if kind == "ack":
                slot = srv.acks.get(m.get("seq"))
                if slot:
                    slot[1] = bool(m.get("ok"))
                    slot[0].set()
            elif kind == "ping":
                on_bundle(m.get("bundle"))
            elif kind == "job" and isinstance(m.get("job"), dict):
                handle_job(srv, worker, m["job"])
    except (wsproto.Closed, OSError, ValueError) as e:
        log(f"live connection closed ({type(e).__name__}: {e})")
    finally:
        stop.set()
        srv.ws = None
        ws.close()
        for slot in list(srv.acks.values()):    # unanswered batches go over HTTPS instead
            slot[0].set()


def poll_once(srv: Server, worker: "Worker", host: str, on_bundle) -> None:
    """One long poll (the fallback when WebSockets are blocked)."""
    try:
        j = srv.get(f"/flynn/agent/next?wait=25&host={urllib.request.quote(host)}&version={VERSION}"
                    f"&busy={worker.job or ''}&outbox={','.join(outbox_jobs())}"
                    f"&offline={urllib.request.quote(json.dumps(NET['last'] or {}))}", timeout=45)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Refused("HTTP 401") from e
        raise
    net_up()
    if not j.get("job"):
        on_bundle(j.get("bundle"))
        return
    handle_job(srv, worker, j["job"])


def prefer_https(srv: Server) -> None:
    """A server address saved as http:// that redirects to https:// (a proxy in front of the server): use https from
    now on. Over http every POST was redirected into a GET and the WebSocket was refused, so turn events never arrived
    (2026-10-21)."""
    if not srv.url.startswith("http://"):
        return
    https = "https://" + srv.url[len("http://"):]
    try:
        req = urllib.request.Request(srv.url + "/flynn/agent/hello", headers={"Authorization": f"Bearer {srv.token}"})
        with urllib.request.urlopen(req, timeout=20, context=_ssl()) as r:
            final = r.geturl()
    except Exception:  # noqa: BLE001 - keep the saved address
        return
    if final.startswith(https):
        srv.url = https
        c = config()
        c["url"] = https
        save_config(c)
        log(f"the server moved to {https}; using it from now on")


def serve(srv: Server) -> int:
    prefer_https(srv)
    hello = srv.get("/flynn/agent/hello", timeout=30)
    log(f"connected to {srv.url} as {hello.get('email')}")
    state = {"v": (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file() else ""}
    if hello.get("bundle") and hello["bundle"] != state["v"]:
        state["v"] = update(srv)
    use_package_cache(srv)
    threading.Thread(target=best_claude, daemon=True).start()     # checked before the first turn needs it
    worker = Worker(srv)
    host = socket.gethostname()[:60]

    def on_bundle(stamp):
        """The server has new flynn files (a redeploy): update, then restart on the new agent code."""
        if not stamp or stamp == state["v"] or worker.job is not None or RECORDINGS:
            return
        try:
            if update(srv) != stamp:
                raise RuntimeError("the downloaded files have another version")   # never restart in a loop
            log("restarting on the updated agent")
            if srv.ws:
                srv.ws.close()
            os.execv(sys.executable, [sys.executable, str(ROOT / "adapters" / "agent" / "flynn_agent.py"), "start"])
        except Exception as e:  # noqa: BLE001 - keep serving on the old files
            log(f"update failed: {e}")
            state["v"] = stamp

    def flusher():                                   # saved events and runs go out as soon as the server is back
        while True:
            time.sleep(10)
            if outbox_jobs():
                try:
                    outbox_flush(srv)
                except Exception as e:  # noqa: BLE001 - try again next time
                    log(f"outbox: {type(e).__name__}: {e}")
    threading.Thread(target=flusher, daemon=True).start()

    print(f"flynn agent connected to {srv.url} as {hello.get('email')}. Leave this running.", flush=True)
    backoff, live_after = 2.0, 0.0
    while True:
        try:
            if time.time() >= live_after:
                try:
                    opened = time.time()
                    live(srv, worker, host, on_bundle)
                    if time.time() - opened > 30:
                        backoff = 2.0
                    time.sleep(min(backoff, 5))      # reconnect at once after a normal drop
                    continue
                except ConnectionRefusedError as e:  # the server or a proxy can't do WebSockets
                    log(f"live connection unavailable ({e}); polling for {POLL_FALLBACK:.0f}s")
                    live_after = time.time() + POLL_FALLBACK
            poll_once(srv, worker, host, on_bundle)
            backoff = 2.0
        except Refused:
            log("the server refused the token; create a new command at /flynn/agent/connect")
            print("The server refused this computer's token. Create a new command at "
                  f"{srv.url}/flynn/agent/connect and run it.", file=sys.stderr)
            return 2
        except (urllib.error.URLError, OSError) as e:
            if not NET["since"]:
                log(f"server not reachable: {e}")
            net_down(e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "status"
    c = config()
    if cmd == "login":
        if len(a) < 3:
            print("usage: flynn-agent login <server URL> <token>")
            return 2
        srv = Server(a[1], a[2])
        prefer_https(srv)
        try:
            who = srv.get("/flynn/agent/hello", timeout=30)
        except urllib.error.HTTPError as e:
            print(f"The server refused the token (HTTP {e.code}). Create a new command in the app.")
            return 2
        except (urllib.error.URLError, OSError) as e:
            print(f"Can't reach {a[1]}: {e}")
            return 2
        save_config({"url": srv.url, "token": srv.token, "email": who.get("email")})
        print(f"Signed in to {srv.url} as {who.get('email')}.")
        return 0
    if not c.get("url") or not c.get("token"):
        print("Not set up. Run the command from the app's connect page (/flynn/agent/connect).")
        return 2
    srv = Server(c["url"], c["token"])
    if cmd == "update":
        print("flynn files:", update(srv))
        return 0
    if cmd == "start":
        if not (ROOT / "VERSION").is_file():
            update(srv)
        return serve(srv)
    if cmd == "import-letter":
        import argparse
        ap = argparse.ArgumentParser(prog="flynn-agent import-letter")
        ap.add_argument("run")
        ap.add_argument("--type", default="release-note", choices=["release-note", "report", "deck", "newsletter"])
        ap.add_argument("--title")
        ap.add_argument("--team")
        o = ap.parse_args(a[1:])
        run = Path(o.run).expanduser().resolve()
        try:
            m = import_letter(srv, run, o.type, o.title or run.name, o.team)
        except (ValueError, urllib.error.URLError, OSError) as e:
            print(f"Could not import the letter: {e}")
            return 1
        print(f"Letter \"{m['title']}\" is in flynn desk (run {Path(m['run']).name}).\nOpen it: {m['link']}")
        return 0
    if cmd == "status":
        try:
            who = srv.get("/flynn/agent/hello", timeout=15)
            print(f"Set up for {who.get('email')} on {srv.url}. Server reachable.")
        except Exception as e:  # noqa: BLE001
            print(f"Set up for {c.get('email')} on {srv.url}, but the server didn't answer: {e}")
        sdk = shutil.which("claude")
        print(f"claude: {sdk or 'not found'}; python {platform.python_version()}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
