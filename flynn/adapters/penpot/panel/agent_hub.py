"""flynn agents: each person's own Claude runs their flynn desk turns, on their own computer.

On the team server the AI does not run in the container. Each person installs the flynn agent once
(`curl -fsSL <server>/flynn/agent/install.sh | bash -s -- <token>`, from the connect page). The agent connects out
to the server (HTTPS long polling, so it works behind any firewall or proxy), takes the turns of conversations its
person starts, runs them with the Claude Agent SDK on that person's own Claude login, and sends the panel events
back. The desk tools (canvas build, ask, show result...) still run on the server: the agent's desk MCP server calls
/flynn/api/tool with the agent token.

Pieces here:
  tokens        issue / who / revoke, stored hashed in $FLYNN_HOME/agent-tokens.json;
  HUB           per person: a job queue, when their agent last called in, and the turns in flight;
  RemoteEngine  the engine (engines.py) that hands a turn to the person's agent and relays its events;
  RemoteRecording  a design system recording run by the person's agent (the recorder needs their screen);
  bundle        the tarball the installer unpacks: flynn's scripts, store export, systems, adapters, agents, skills;
  routes        GET  /agent/install.sh  /agent/bundle.tgz  /agent/connect  /agent/status
                POST /agent/token (signed-in person: a new connection command)
                GET  /agent/ws  (the live connection: jobs down, events up; WebSocket)     (agent token)
                GET  /agent/hello  /agent/next?wait=25  /agent/run-download?run=&sub=   (agent token)
                POST /agent/events  /agent/run-upload  /agent/rec-upload                  (agent token)

Workspace: run folders are made on the agent's computer. Before a desk tool that names a run, and at the end of a
turn, the agent uploads the run folder here (runs/<id>), so the canvas builds from the same files; canvas_pull
results come back the other way. Paths in a turn that point at this server (flynn's folder, uploads, the selection
picture) are rewritten by the agent to its own copies; files under the panel state folder travel with the job.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import tarfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[2]
RUNS_DIR = FLYNN / "runs"
RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*[a-z0-9]$")
ONLINE = 45.0          # an agent that called in within this many seconds is connected
SILENT = 150.0         # a turn whose agent has been silent this long has failed
AWAY = 1800.0          # a turn keeps waiting this long for an agent that dropped off (it saves what it can't send)
ORPHAN = 20.0          # an agent idle this long after a turn was sent means the turn's end was lost
FILE_MAX = 25 * 1024 * 1024
UPLOAD_MAX = 300 * 1024 * 1024
RUNS_FOR = None        # a server where each person has their own runs folder sets this: email -> Path (Studio, team.py)
APPS_FOR = None        # and where their App builder snapshots are kept: email -> Path (Studio, team.py)
APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def runs_dir_for(email: str) -> Path:
    if RUNS_FOR is not None:
        own = RUNS_FOR(email)
        if own is None:
            raise ValueError("unknown person")
        return Path(own)
    return RUNS_DIR


# Which app installs this agent: flynn desk (default) or flynn Studio on its own server, side by side on one computer.
AGENT_PROFILES = {
    "desk": {"home": ".flynn-agent", "cmd": "flynn-agent", "label": "com.flynn.agent", "app": "flynn desk"},
    "studio": {"home": ".flynn-studio", "cmd": "flynn-studio-agent", "label": "com.flynn.studio-agent", "app": "flynn Studio"},
}


def agent_profile() -> dict:
    return AGENT_PROFILES.get(os.environ.get("FLYNN_AGENT_PROFILE", "desk"), AGENT_PROFILES["desk"])


def flynn_home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


# ---------- tokens ----------

_tlock = threading.Lock()


def _tokens_path() -> Path:
    return flynn_home() / "agent-tokens.json"


def _tokens() -> dict:
    try:
        return json.loads(_tokens_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_tokens(d: dict) -> None:
    p = _tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(email: str, name: str = "") -> str:
    """A new agent token for this person; their earlier tokens stop working (one agent per person)."""
    email = email.lower()
    tok = "fla_" + secrets.token_urlsafe(32)
    with _tlock:
        d = {k: v for k, v in _tokens().items() if v.get("email") != email}
        d[_h(tok)] = {"email": email, "name": name or email, "created": int(time.time())}
        _save_tokens(d)
    return tok


def who(token: str) -> dict | None:
    if not token.startswith("fla_"):
        return None
    v = _tokens().get(_h(token))
    return {"email": v["email"], "name": v.get("name") or v["email"]} if v else None


def revoke(email: str) -> int:
    email = email.lower()
    with _tlock:
        d = _tokens()
        keep = {k: v for k, v in d.items() if v.get("email") != email}
        _save_tokens(keep)
    return len(d) - len(keep)


# ---------- the hub ----------

class Hub:
    def __init__(self):
        self.cond = threading.Condition()
        self.queues: dict[str, list[dict]] = {}
        self.seen: dict[str, float] = {}
        self.info: dict[str, dict] = {}
        self.turns: dict[str, dict] = {}
        self.records: dict[str, "RemoteRecording"] = {}

    def connected(self, email: str) -> bool:
        return time.time() - self.seen.get(email.lower(), 0) < ONLINE

    def status(self, email: str) -> dict:
        email = email.lower()
        return {"connected": self.connected(email), "last_seen": self.seen.get(email), **self.info.get(email, {}),
                "has_token": any(v.get("email") == email for v in _tokens().values()),
                # the AI runs on people's own computers here (the team server), so the app offers the agent
                "remote": os.environ.get("FLYNN_ENGINE") == "remote"}

    def put(self, email: str, job: dict) -> None:
        with self.cond:
            self.queues.setdefault(email.lower(), []).append(job)
            self.cond.notify_all()

    def unshift(self, email: str, job: dict) -> None:
        """Put a job back at the front (it was taken for a connection that closed before it was sent)."""
        with self.cond:
            self.queues.setdefault(email.lower(), []).insert(0, job)
            self.cond.notify_all()

    def next(self, email: str, wait: float, info: dict | None = None) -> dict | None:
        email = email.lower()
        end = time.time() + max(0.0, min(wait, 30.0))
        with self.cond:
            self.seen[email] = time.time()
            if info:
                self.info[email] = info
            while True:
                q = self.queues.get(email) or []
                if q:
                    return q.pop(0)
                left = end - time.time()
                if left <= 0:
                    return None
                self.cond.wait(min(left, 5.0))
                self.seen[email] = time.time()

    def _seen_batch(self, holder: dict, bid) -> bool:
        """True when this batch was applied already (sent over the live connection, then again over HTTPS
        because its receipt was lost)."""
        if not bid:
            return False
        seen = holder.setdefault("bids", [])
        if bid in seen:
            return True
        seen.append(bid)
        del seen[:-500]
        return False

    def events(self, email: str, job_id: str, evs: list[dict], bid: str | None = None) -> bool:
        r = self.records.get(job_id)
        if r is not None:
            if r.email != email.lower():
                return False
            self.seen[email.lower()] = time.time()
            if not self._seen_batch(r.__dict__, bid):
                r.update(evs)
            return True
        t = self.turns.get(job_id)
        if not t or t["email"] != email.lower():
            return False
        t["last"] = time.time()
        self.seen[email.lower()] = time.time()
        if self._seen_batch(t, bid):
            return True
        for ev in evs:
            if not isinstance(ev, dict) or not isinstance(ev.get("t"), str):
                continue
            t["emit"](ev)
            if ev["t"] == "turn_end":
                t["done"].set()
        return True


HUB = Hub()


# ---------- turns that outlive a server restart ----------
# A turn runs on the person's own computer, so a server restart (a deploy, a crash) needn't end it: the server records
# each turn it hands out, and after a restart the conversation waits for the agent to come back and takes its events.

def _turns_file() -> Path:
    return flynn_home() / "remote-turns.json"


_tlock = threading.Lock()


def open_turns() -> dict:
    try:
        d = json.loads(_turns_file().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_turns(d: dict) -> None:
    p = _turns_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def note_turn(jid: str, rec: dict | None) -> None:
    with _tlock:
        d = open_turns()
        if rec is None:
            d.pop(jid, None)
        else:
            d[jid] = rec
        _save_turns(d)


def busy_with(email: str, jid: str, hub: "Hub | None" = None) -> bool:
    """The agent says it is still on this job: running it, or still delivering its saved events."""
    info = (hub or HUB).info.get(email.lower()) or {}
    return info.get("busy") == jid or jid in str(info.get("outbox") or "").split(",")


class Reattach:
    """After a restart: wait for the person's agent to deliver the rest of a turn that was running. Ends the turn
    when its turn_end arrives; when the agent comes back idle and has nothing saved for it; or after `patience`."""
    name, label = "remote", "Your computer (flynn agent)"

    def __init__(self, jid: str, email: str, emit, patience: float = 1800.0, hub: "Hub | None" = None):
        self.jid, self.email, self.emit, self.hub, self.patience = jid, email.lower(), emit, hub or HUB, patience
        self.done = threading.Event()

    def run(self) -> None:
        rec = {"email": self.email, "emit": self._emit, "done": self.done, "last": time.time()}
        self.hub.turns[self.jid] = rec
        start, seen_idle = time.time(), None
        try:
            while not self.done.wait(5):
                if self.hub.connected(self.email):
                    if busy_with(self.email, self.jid, self.hub) or time.time() - rec["last"] < ORPHAN:
                        seen_idle = None
                    else:
                        seen_idle = seen_idle or time.time()
                        if time.time() - seen_idle > 60:
                            self.emit({"t": "error", "text": "Flynn finished on your computer while Studio was "
                                       "restarting, and its last messages didn't arrive. Say continue to pick it up."})
                            self.emit({"t": "turn_end", "ok": False, "cost": None})
                            return
                elif time.time() - start > self.patience:
                    self.emit({"t": "error", "text": "Studio restarted while Flynn was working, and your computer "
                               "didn't come back within 30 minutes. Send your message again when it's connected."})
                    self.emit({"t": "turn_end", "ok": False, "cost": None})
                    return
        finally:
            self.hub.turns.pop(self.jid, None)
            note_turn(self.jid, None)

    def _emit(self, ev):
        self.emit(ev)
        if ev.get("t") == "turn_end":
            self.done.set()

    def stop(self) -> None:
        self.hub.put(self.email, {"kind": "stop", "id": self.jid})


def call(email: str, job: dict, timeout: float = 90.0, hub: "Hub | None" = None) -> dict | None:
    """Ask the person's agent to do one thing and wait for its answer (one {"t": "result", ...} event). None when the
    agent isn't connected or didn't answer in time."""
    hub = hub or HUB
    email = (email or "").lower()
    if not email or not hub.connected(email):
        return None
    jid = uuid.uuid4().hex
    done, box = threading.Event(), {}

    def emit(ev):
        if ev.get("t") == "result":
            box.update(ev)
            done.set()
    hub.turns[jid] = {"email": email, "emit": emit, "done": done, "last": time.time()}
    hub.put(email, {**job, "id": jid})
    try:
        return box if done.wait(timeout) else None
    finally:
        hub.turns.pop(jid, None)


# ---------- the live connection (WebSocket) ----------

WS_PING = 20.0         # the server sends a heartbeat this often when there is no job; the agent expects one


def serve_ws(h, email: str) -> None:
    """One agent's live connection. Jobs go down the moment they are queued (no polling); events come up as they
    happen and each batch is acknowledged, so the agent knows it arrived. The agent falls back to /agent/next and
    /agent/events over plain HTTPS when a network blocks WebSockets."""
    import wsproto
    ws = wsproto.accept(h)
    if ws is None:
        return h._json({"error": "websocket upgrade expected"}, 400)
    email = email.lower()
    alive = threading.Event()
    alive.set()
    info = {"host": "", "version": "", "busy": "", "at": time.time(), "transport": "websocket"}
    HUB.seen[email] = time.time()
    HUB.info[email] = dict(info)

    def sender():
        while alive.is_set():
            job = HUB.next(email, WS_PING, None)
            if job and not alive.is_set():
                HUB.unshift(email, job)
                return
            try:
                ws.send_json({"type": "job", "job": job} if job else
                             {"type": "ping", "bundle": current_stamp(), "t": time.time()})
            except wsproto.Closed:
                if job:
                    HUB.unshift(email, job)
                return
    threading.Thread(target=sender, name=f"agent-ws-{email}", daemon=True).start()
    try:
        ws.settimeout(WS_PING * 4)
        while True:
            m = ws.recv_json()
            HUB.seen[email] = time.time()
            kind = m.get("type") if isinstance(m, dict) else None
            if kind in ("hello", "status"):
                info.update({k: str(m.get(k) or "")[:80] for k in ("host", "version", "busy")})
                info["outbox"] = str(m.get("outbox") or "")[:2000]
                if isinstance(m.get("offline"), dict):
                    info["offline"] = {k: str(v)[:200] for k, v in m["offline"].items()}
                info["at"] = time.time()
                HUB.info[email] = dict(info)
            elif kind == "events":
                ok = HUB.events(email, str(m.get("job") or ""), m.get("events") or [], m.get("bid"))
                ws.send_json({"type": "ack", "seq": m.get("seq"), "ok": ok})
    except (wsproto.Closed, OSError, ValueError):
        pass
    finally:
        alive.clear()
        ws.close()
        with HUB.cond:
            HUB.cond.notify_all()


# ---------- the engine ----------

def server_files(text: str) -> dict[str, str]:
    """Files on this server that a turn's prompt points at (uploads, the selection picture): path -> base64."""
    roots = [str(flynn_home())]
    st = os.environ.get("FLYNN_PANEL_STATE")
    if st:
        roots.append(st)
    out: dict[str, str] = {}
    for root in roots:
        for m in re.finditer(re.escape(root) + r"/[^\s\"'<>)]+", text):
            p = Path(m.group(0).rstrip(".,;:"))
            try:
                if p.is_file() and p.stat().st_size <= FILE_MAX and str(p) not in out:
                    out[str(p)] = base64.b64encode(p.read_bytes()).decode()
            except OSError:
                continue
    return out


def connect_hint() -> str:
    return ("Your flynn agent is not connected, so flynn can't answer yet. flynn's AI runs on your own computer with "
            "your own Claude login. Open /flynn/agent/connect in this app, copy the one-line command, and run it in "
            "Terminal; then send your message again.")


class RemoteEngine:
    """Hands the turn to the signed-in person's flynn agent (engines.py `remote`)."""
    name, label = "remote", "Your computer (flynn agent)"

    def __init__(self, hub: Hub | None = None):
        self.hub = hub or HUB
        self.job: str | None = None
        self.email: str | None = None

    def run(self, t, emit) -> None:
        email = (getattr(t, "user", "") or "").lower()
        if not email:
            emit({"t": "error", "text": "flynn doesn't know who sent this message. Sign in again and retry."})
            emit({"t": "turn_end", "ok": False, "cost": None})
            return
        if not self.hub.connected(email):
            emit({"t": "error", "text": connect_hint()})
            emit({"t": "turn_end", "ok": False, "cost": None})
            return
        jid = uuid.uuid4().hex
        self.job, self.email = jid, email
        done = threading.Event()
        rec = {"email": email, "emit": emit, "done": done, "last": time.time()}
        self.hub.turns[jid] = rec
        self.hub.put(email, {
            "kind": "turn", "id": jid, "root": str(FLYNN),
            "turn": {"prompt": t.prompt, "session": t.session, "system": t.system, "allow": list(t.allow),
                     "deny": list(t.deny), "file_id": t.file_id, "env": dict(t.env),
                     # Studio's turns also name the agent, model, effort, spend cap and their own tools (MCP servers)
                     **{k: getattr(t, k) for k in ("agent", "model", "effort", "budget_usd", "mcp")
                        if getattr(t, k, None) not in (None, "flynn")},
                     **({"agent": ""} if getattr(t, "agent", "flynn") == "" else {})},
            "sync_runs": [r for r in getattr(t, "sync_runs", []) or [] if RUN_NAME.match(r)],
            **({"app": t.app} if getattr(t, "app", None) else {}),
            "files": server_files(t.prompt)})
        note_turn(jid, {"email": email, "conv": getattr(t, "conv_key", ""), "uid": getattr(t, "conv_uid", ""),
                        "at": time.time()})
        sent = time.time()
        away = False
        try:
            while not done.wait(5):
                info = self.hub.info.get(email) or {}
                # Say which link is down while the turn waits: the person's computer (asleep, off the VPN) vs a
                # working computer still delivering. The agent keeps the turn running and saves what it can't send.
                gone = not self.hub.connected(email) and time.time() - self.hub.seen.get(email, 0) > 15
                if gone != away:
                    away = gone
                    emit({"t": "link", "state": "waiting" if gone else "ok",
                          "text": "Waiting for your computer to reconnect (is it awake and on the VPN?). flynn keeps "
                                  "working there and sends everything when it's back." if gone else
                                  "Your computer is back; flynn continues."})
                # The agent says which turn it is running each time it calls in. Idle while this turn is still open
                # here means its end was lost on the way (a server restart, a full disk): close it, don't wait forever.
                if info.get("at", 0) > sent + ORPHAN and not busy_with(email, jid, self.hub) and time.time() - rec["last"] > ORPHAN:
                    emit({"t": "error", "text": "flynn finished on your computer, but its last messages didn't reach "
                                                "the server. Send your message again, or say continue."})
                    emit({"t": "turn_end", "ok": False, "cost": None})
                    return
                if time.time() - max(rec["last"], self.hub.seen.get(email, 0)) > AWAY:
                    emit({"t": "error", "text": "Your computer has been away for 30 minutes (asleep, or off the VPN), "
                                                "so this turn was closed here. Anything flynn finished there arrives "
                                                "when it reconnects; send your message again to continue."})
                    emit({"t": "turn_end", "ok": False, "cost": None})
                    return
        finally:
            self.hub.turns.pop(jid, None)
            note_turn(jid, None)
            self.job = None

    def stop(self) -> None:
        if self.job and self.email:
            self.hub.put(self.email, {"kind": "stop", "id": self.job})

    def detach(self) -> None:
        """The server is shutting down: leave the turn running on the person's computer (it is picked up again after
        the restart, see Reattach). No stop is sent."""
        self.job = None


# ---------- recordings ----------

class RemoteRecording:
    """A design system recording run by the person's flynn agent, on their own computer (record_api uses it on the
    team server). The recorder opens a visible Chrome and the person browses in it, so it has to run where the
    person sits: the server has no screen, and the sites to record are signed in on the person's computer.
    The same interface as recorder.Session (start, capture_now, stop, status, pages, folder, id, cfg, _thread),
    so record_api processes the result unchanged: the agent uploads the folder here when the recording ends."""

    def __init__(self, local, email: str, hub: Hub | None = None):
        self.local = local                      # a recorder.Session, never started here: it names the id and folder
        self.id, self.folder, self.cfg = local.id, local.folder, local.cfg
        self.email, self.hub = email.lower(), hub or HUB
        self.job = uuid.uuid4().hex
        self.pages: list[dict] = []
        self.state, self.error, self.current = "starting", "", ""
        self.last = time.time()
        self.done = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "RemoteRecording":
        if not self.hub.connected(self.email):
            raise RuntimeError("Recording runs on your own computer, through the flynn agent, and yours is not "
                               "connected. Open /flynn/agent/connect, run the command it gives you, then try again.")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hub.records[self.job] = self
        cfg = {k: v for k, v in self.cfg.items() if k in ("system", "description", "url", "how", "max_pages", "sizes")}
        self.hub.put(self.email, {"kind": "record", "id": self.job, "session": self.id, "cfg": cfg})
        self._thread = threading.Thread(target=self._wait, name=f"remote-recording-{self.id}", daemon=True)
        self._thread.start()
        return self

    def _wait(self) -> None:
        try:
            while not self.done.wait(5):
                if time.time() - max(self.last, self.hub.seen.get(self.email, 0)) > SILENT:
                    self.state = "error"
                    self.error = ("Your flynn agent stopped answering (the computer slept, or it lost the connection), "
                                  "so the recording ended.")
                    return
        finally:
            self.hub.records.pop(self.job, None)

    def update(self, evs: list[dict]) -> None:
        self.last = time.time()
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            if ev.get("t") == "rec-status":
                self.state = str(ev.get("state") or self.state)[:20]
                self.error = str(ev.get("error") or "")[:400]
                self.current = str(ev.get("current") or "")[:400]
                if isinstance(ev.get("pages"), list):
                    self.pages = ev["pages"]
            elif ev.get("t") == "rec-done":
                self._load(str(ev.get("root") or ""))
                if ev.get("error") and not self.error:
                    self.error = str(ev["error"])[:400]
                self.state = "stopped" if self.pages else "error"
                self.done.set()

    def receive(self, data: bytes) -> int:
        """The recording folder from the agent."""
        return unpack_into(self.folder, data, UPLOAD_MAX)

    def _load(self, root: str) -> None:
        try:
            s = json.loads((self.folder / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        # page folders name the agent's computer (`root` is the recording folder there); point them at the copies
        def here(pg: dict) -> dict:
            f = str(pg.get("folder") or "")
            if root and f.startswith(root.rstrip("/") + "/"):
                return {**pg, "folder": str(self.folder / f[len(root.rstrip("/")) + 1:])}
            return pg
        self.pages = [here(pg) for pg in s.get("pages") or [] if isinstance(pg, dict)]
        if s.get("error"):
            self.error = str(s["error"])[:400]

    def capture_now(self) -> None:
        self.hub.put(self.email, {"kind": "rec-snap", "id": self.job})

    def stop(self, wait: float = 0) -> None:
        self.hub.put(self.email, {"kind": "rec-stop", "id": self.job})

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "pages": len(self.pages),
                "current": self.current, "folder": str(self.folder), "how": self.cfg.get("how", "watch")}


# ---------- run folders ----------

def _safe_members(tf: tarfile.TarFile, limit: int):
    total = 0
    for m in tf.getmembers():
        if m.name.startswith("/") or ".." in Path(m.name).parts:
            continue                                # never outside the run folder
        name = m.name.removeprefix("./")
        if not name:
            continue
        if not (m.isfile() or m.isdir()):
            continue                                # no links, devices or fifos
        total += m.size
        if total > limit:
            raise ValueError("the run folder is too large")
        m.name = name
        yield m


# Uploads arrive in pieces: a proxy in front of the server (the hosting platform's) refuses requests over about 1 MB (2026-10-10:
# HTTP 413 on a release note's screenshots). The agent sends {"upload": id, "part": i, "parts": n, "tgz": piece}.
_PARTS: dict = {}
_PARTS_LOCK = threading.Lock()
UPLOAD_ID = re.compile(r"^[a-f0-9]{8,64}$")


def assemble(email: str, b: dict) -> bytes | None:
    """The whole upload once every piece is here (None until then). A request without "upload" is whole already."""
    data = base64.b64decode(b.get("tgz") or "")
    uid = b.get("upload")
    if not uid:
        return data
    parts, i = int(b.get("parts") or 0), int(b.get("part") if b.get("part") is not None else -1)
    if not UPLOAD_ID.match(str(uid)) or not 0 < parts <= 1000 or not 0 <= i < parts:
        raise ValueError("bad upload piece")
    now = time.time()
    with _PARTS_LOCK:
        for k in [k for k, v in _PARTS.items() if now - v["t"] > 3600]:
            del _PARTS[k]
        e = _PARTS.setdefault((email.lower(), uid), {"parts": parts, "got": {}, "t": now})
        e["got"][i], e["t"] = data, now
        if sum(len(x) for x in e["got"].values()) > UPLOAD_MAX:
            del _PARTS[(email.lower(), uid)]
            raise ValueError("the folder is too large")
        if len(e["got"]) < e["parts"]:
            return None
        del _PARTS[(email.lower(), uid)]
        return b"".join(e["got"][k] for k in range(e["parts"]))


def unpack_into(dest: Path, data: bytes, limit: int = UPLOAD_MAX) -> int:
    """Unpack a gzip tar the agent sent into `dest`: plain files and folders only, never outside `dest`."""
    if len(data) > limit:
        raise ValueError("the folder is too large")
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in _safe_members(tf, limit * 3):
            target = dest / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(src.read())
            tmp.replace(target)
            if m.mtime:
                os.utime(target, (m.mtime, m.mtime))
            n += 1
    return n


def receive_run(run: str, data: bytes, runs_dir: Path | None = None) -> int:
    """Unpack a run folder the agent uploaded (a gzip tar of the folder's contents) into runs/<run>."""
    if not RUN_NAME.match(run):
        raise ValueError("bad run name")
    return unpack_into((runs_dir or RUNS_DIR) / run, data)


def pack_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if d.is_dir():
            for f in sorted(d.rglob("*")):
                if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts:
                    tf.add(f, arcname=str(f.relative_to(d)), recursive=False)
    return buf.getvalue()


def send_run(run: str, sub: str = "", runs_dir: Path | None = None) -> bytes:
    if not RUN_NAME.match(run) or (sub and not re.match(r"^[A-Za-z0-9._-]+$", sub)):
        raise ValueError("bad run name")
    d = (runs_dir or RUNS_DIR) / run
    return pack_dir(d / sub if sub else d)


# ---------- the bundle ----------

BUNDLE_DIRS = ["agents", "skills", "scripts", "kb", "systems", "docs", "adapters"]
BUNDLE_SKIP = {"__pycache__", ".git", "runs", "refs", "node_modules", ".pytest_cache"}
BUNDLE_SKIP_PATHS = {"adapters/penpot/stack", "adapters/browser/profile", "kb/flynn.db"}
HOME_PATH = "$HOME/agent-stack/flynn"
AGENT_ROOT = "$HOME/" + agent_profile()["home"] + "/flynn"
_block = threading.Lock()


def _bundle_files(root: Path):
    for top in BUNDLE_DIRS:
        base = root / top
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            rel = f.relative_to(root).as_posix()
            if any(p in BUNDLE_SKIP for p in f.parts) or any(rel == s or rel.startswith(s + "/") for s in BUNDLE_SKIP_PATHS):
                continue
            if f.is_file() and not f.name.endswith((".pyc", ".db", ".db-wal", ".db-shm")):
                yield f, rel


def _rewrite(text: str) -> str:
    """flynn's agent and skill files name its folder on the user's Mac; on an agent it lives in ~/.flynn-agent/flynn."""
    return text.replace(HOME_PATH, AGENT_ROOT).replace(str(FLYNN), AGENT_ROOT)


def bundle_stamp(root: Path | None = None) -> str:
    return str(int(max((f.stat().st_mtime for f, _ in _bundle_files(root or FLYNN)), default=0)))


_stamp: dict = {}


def current_stamp() -> str:
    """bundle_stamp, re-read at most once a minute (agents ask on every poll)."""
    if time.time() - _stamp.get("at", 0) > 60:
        _stamp.update(at=time.time(), v=bundle_stamp())
    return _stamp["v"]


def build_bundle(root: Path | None = None, stamp: str = "") -> bytes:
    """flynn for an agent: its files, plus .claude/agents and .claude/skills (project level, so they never touch
    the person's own ~/.claude) with paths pointing at ~/.flynn-agent/flynn."""
    root = root or FLYNN
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        def add_bytes(name: str, data: bytes, mode: int = 0o644):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.mtime = len(data), mode, int(time.time())
            tf.addfile(ti, io.BytesIO(data))
        for f, rel in _bundle_files(root):
            if rel.startswith(("agents/", "skills/")) and f.suffix == ".md":
                text = _rewrite(f.read_text(encoding="utf-8"))
                add_bytes(rel, text.encode())
                add_bytes(".claude/" + rel, text.encode())
            else:
                tf.add(f, arcname=rel, recursive=False)
                if rel.startswith("skills/"):
                    tf.add(f, arcname=".claude/" + rel, recursive=False)
        add_bytes("VERSION", (stamp or bundle_stamp(root)).encode())
    return buf.getvalue()


def bundle(root: Path | None = None) -> bytes:
    """The bundle, cached on the data volume until flynn's files change (a new image)."""
    root = root or FLYNN
    stamp = bundle_stamp(root)
    cache = flynn_home() / "cache" / f"agent-bundle-{stamp}.tgz"
    with _block:
        if cache.is_file():
            return cache.read_bytes()
        data = build_bundle(root, stamp)
        cache.parent.mkdir(parents=True, exist_ok=True)
        for old in cache.parent.glob("agent-bundle-*.tgz"):
            old.unlink(missing_ok=True)
        cache.write_bytes(data)
        return data


# ---------- installer and connect page ----------

def public_url(headers) -> str:
    u = os.environ.get("FLYNN_PUBLIC_URL")
    if u:
        return u.rstrip("/")
    proto = headers.get("X-Forwarded-Proto") or "http"
    host = headers.get("X-Forwarded-Host") or headers.get("Host") or "127.0.0.1:9001"
    return f"{proto}://{host}"


def installer(url: str) -> str:
    p = FLYNN / "deploy" / "agent" / "install.sh"
    pr = agent_profile()
    return (p.read_text(encoding="utf-8").replace("__FLYNN_URL__", url).replace("__AGENT_HOME__", pr["home"])
            .replace("__AGENT_CMD__", pr["cmd"]).replace("__AGENT_LABEL__", pr["label"]).replace("__AGENT_APP__", pr["app"]))


def install_command(url: str, token: str) -> str:
    return f"curl -fsSL {url}/flynn/agent/install.sh | bash -s -- {token}"


CONNECT_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect flynn</title>
<style>
:root{--bg:#0e0f11;--card:#17181b;--line:#2a2c31;--text:#ececef;--muted:#9a9ca3;--accent:#ffffff;--ok:#3ecf8e;--warn:#f5b04c}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f6f6f7;--card:#fff;--line:#e3e4e8;--text:#17181b;--muted:#5f6168;--accent:#17181b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Vazirmatn,sans-serif}
main{max-width:640px;margin:0 auto;padding:56px 16px}
h1{font-size:26px;line-height:1.25;margin:0 0 8px;font-weight:650;letter-spacing:-.01em}
p{margin:0 0 14px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:24px 0}
.status{display:flex;align-items:center;gap:10px;font-weight:550}
.dot{width:10px;height:10px;border-radius:50%;background:var(--warn)}.dot.on{background:var(--ok)}
ol{margin:0;padding-left:20px;color:var(--muted)}li{margin:6px 0}
button{appearance:none;border:0;border-radius:8px;background:var(--accent);color:var(--bg);font:inherit;font-weight:600;padding:10px 16px;cursor:pointer}
button.ghost{background:transparent;color:var(--text);border:1px solid var(--line)}
pre{white-space:pre-wrap;word-break:break-all;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;font:13px/1.5 ui-monospace,Menlo,monospace;margin:12px 0}
.row{display:flex;gap:10px;flex-wrap:wrap}.small{font-size:13px}
</style></head><body><main>
<h1>Connect flynn to your Claude</h1>
<p>flynn's AI runs on your own computer, with your own Claude login. The flynn agent is a small program that keeps your
computer connected to this app while you work, so the chat can answer.</p>
<div class="card"><div class="status"><span class="dot" id="dot"></span><span id="st">Checking…</span></div></div>
<div class="card">
 <ol>
  <li>Create your connection command. It is for you only; creating a new one stops the old one.</li>
  <li>Open Terminal on your Mac, paste the command, and press Return. It installs Python packages, Claude Code and the agent, then asks you to sign in to Claude if you aren't yet.</li>
  <li>Come back here. The status turns green, and the chat answers.</li>
 </ol>
 <div class="row" style="margin-top:16px"><button id="make">Create my command</button></div>
 <div id="out" hidden><pre id="cmd"></pre><div class="row"><button id="copy">Copy</button></div>
 <p class="small" style="margin-top:12px">The agent needs macOS or Linux, and a Claude subscription or API key.</p></div>
</div>
</main>
<script>
const $=s=>document.querySelector(s);
async function status(){try{const r=await fetch('/flynn/agent/status',{credentials:'same-origin'});const j=await r.json();
 if(j.error){$('#st').textContent='Sign in to flynn first, then reload this page.';return}
 $('#dot').classList.toggle('on',!!j.connected);
 $('#st').textContent=j.connected?('Connected'+(j.host?' from '+j.host:'')):(j.has_token?'Not connected right now. Start it with: flynn-agent start':'Not set up yet');
}catch(e){$('#st').textContent='The app is not reachable.'}}
$('#make').onclick=async()=>{const r=await fetch('/flynn/agent/token',{method:'POST',credentials:'same-origin'});const j=await r.json();
 if(j.error){$('#st').textContent=j.error;return}$('#cmd').textContent=j.command;$('#out').hidden=false;$('#make').textContent='Create a new command';$('#make').className='ghost'};
$('#copy').onclick=()=>{navigator.clipboard.writeText($('#cmd').textContent);$('#copy').textContent='Copied'};
status();setInterval(status,4000);
</script></body></html>"""


# ---------- routes ----------

def handle(h, method: str, path: str, q: dict, me: dict | None):
    """Serve /agent/*. `h` is the request handler (panel_server.Handler); `me` the caller, or None."""
    def raw(code: int, body: bytes, ctype: str):
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        h.wfile.write(body)

    def body() -> dict:
        n = int(h.headers.get("Content-Length") or 0)
        if n > UPLOAD_MAX * 2:
            raise ValueError("too large")
        try:
            return json.loads(h.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    url = public_url(h.headers)
    if method == "GET" and path == "/agent/install.sh":
        return raw(200, installer(url).encode(), "text/x-shellscript; charset=utf-8")
    if method == "GET" and path == "/agent/flynn_agent.py":
        return raw(200, (FLYNN / "adapters" / "agent" / "flynn_agent.py").read_bytes(), "text/x-python; charset=utf-8")
    if method == "GET" and path == "/agent/connect":
        return raw(200, CONNECT_HTML.encode(), "text/html; charset=utf-8")
    if not me:
        return h._json({"error": "sign in first"}, 401)
    agent = me.get("via") == "agent"
    email = me["email"]
    if method == "GET" and path == "/agent/status":
        return h._json(HUB.status(email))
    if method == "POST" and path == "/agent/token":
        if agent:
            return h._json({"error": "create a command from the app, signed in"}, 403)
        tok = issue(email, me.get("name") or "")
        return h._json({"command": install_command(url, tok)})
    if not agent:
        return h._json({"error": "agent token required"}, 401)
    if method == "GET" and path == "/agent/ws":
        return serve_ws(h, email)
    if method == "GET" and path == "/agent/bundle.tgz":
        return raw(200, bundle(), "application/gzip")
    if method == "GET" and path == "/agent/hello":
        return h._json({"email": email, "name": me.get("name"), "url": url, "bundle": bundle_stamp()})
    if method == "GET" and path == "/agent/next":
        try:
            wait = float((q.get("wait") or ["25"])[0])
        except ValueError:
            wait = 25.0
        info = {k: (q.get(k) or [""])[0][:80] for k in ("host", "version", "busy")}
        info["outbox"] = (q.get("outbox") or [""])[0][:2000]
        try:
            off = json.loads((q.get("offline") or ["{}"])[0][:2000])
        except ValueError:
            off = {}
        if isinstance(off, dict) and off:
            info["offline"] = {k: str(v)[:200] for k, v in off.items()}
        info["at"] = time.time()
        return h._json({"job": HUB.next(email, wait, info), "bundle": current_stamp()})
    if method == "GET" and path == "/agent/run-download":
        try:
            data = send_run((q.get("run") or [""])[0], (q.get("sub") or [""])[0], runs_dir_for(email))
        except ValueError as e:
            return h._json({"error": str(e)}, 400)
        return raw(200, data, "application/gzip")
    if method == "POST" and path == "/agent/events":
        b = body()
        ok = HUB.events(email, str(b.get("job") or ""), b.get("events") or [], b.get("bid"))
        return h._json({"ok": ok}, 200 if ok else 404)
    if method == "POST" and path == "/agent/run-upload":
        try:
            b = body()
            data = assemble(email, b)
            if data is None:
                return h._json({"ok": True, "partial": True})
            n = receive_run(str(b.get("run") or ""), data, runs_dir_for(email))
        except (ValueError, tarfile.TarError) as e:
            return h._json({"error": str(e)[:200]}, 400)
        return h._json({"ok": True, "files": n})
    if path in ("/agent/app-upload", "/agent/app-download"):
        b = body() if method == "POST" else {}
        name = str(b.get("name") or "") if method == "POST" else (q.get("name") or [""])[0]
        if APPS_FOR is None or not APP_NAME.match(name or ""):
            return h._json({"error": "no such app"}, 404)
        d = Path(APPS_FOR(email))
        f = d / f"{name}.tgz"
        if method == "GET":
            return raw(200, f.read_bytes(), "application/gzip") if f.is_file() else h._json({"error": "no saved copy"}, 404)
        try:
            data = assemble(email, b)
            if data is None:
                return h._json({"ok": True, "partial": True})
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                list(_safe_members(tf, UPLOAD_MAX))           # refuse unsafe or oversized archives
        except (ValueError, tarfile.TarError) as e:
            return h._json({"error": str(e)[:200]}, 400)
        d.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(f)
        return h._json({"ok": True, "bytes": len(data)})
    if method == "POST" and path == "/agent/rec-upload":
        b = body()
        r = HUB.records.get(str(b.get("job") or ""))
        if not r or r.email != email.lower():
            return h._json({"error": "no such recording"}, 404)
        try:
            data = assemble(email, b)
            if data is None:
                return h._json({"ok": True, "partial": True})
            n = r.receive(data)
        except (ValueError, tarfile.TarError) as e:
            return h._json({"error": str(e)[:200]}, 400)
        return h._json({"ok": True, "files": n})
    return h._json({"error": "not found"}, 404)
