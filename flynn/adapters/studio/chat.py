"""Studio's AI chat (Module 1): conversations, turns, and their live connection.

A conversation is "home" or "run:<run id>". Its events are numbered (their index) and stored as
$FLYNN_STUDIO_CHATS/<key>/events.jsonl, with state.json (session, extra_allow, agent, effort), uploads/ and history/.
Turns run through flynn desk's engines (adapters/penpot/panel/engines.py), with Studio's own tools
(studio_mcp.py) and instructions. Lessons carried over from flynn desk: numbered events so a reconnect resumes,
repair() closes turns a restart cut off, one turn at a time per conversation, and the desk's permission rules."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import base64
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
PANEL = FLYNN / "adapters" / "penpot" / "panel"
sys.path.insert(0, str(PANEL))
import engines  # noqa: E402
import perms  # noqa: E402
import stream  # noqa: E402  (desk parser: aligned_message)
sys.path.insert(0, str(FLYNN / "scripts"))
sys.path.insert(0, str(HERE))
import fpaths  # noqa: E402
import team  # noqa: E402  (team server: who is signed in, each person's folders)

STUDIO_PORT = 8770
TOOL_TOKEN = secrets.token_urlsafe(24)
STOP_GRACE = 10.0
KEY = re.compile(r"home|run:[A-Za-z0-9][A-Za-z0-9._-]*|system:[a-z0-9][a-z0-9-]*|app:[a-z0-9][a-z0-9-]*")
AGENTS = ("flynn", "quorra")
ASTACK = FLYNN.parent
QUORRA = ASTACK / "quorra"
DK_CODE = Path(perms.PROTECTED_CODE)


def apps_root() -> Path:
    return Path(os.environ.get("FLYNN_APPS_DIR", str(Path.home() / "flynn-apps")))
HID = re.compile(r"\d{8}-\d{6}(?:-[0-9a-f]{4})?")

EFFORT = {
    "quick": {"label": "Quick", "model": "sonnet", "effort": "low", "cap": 1.0,
              "line": "Effort: Quick. Use flynn-checker on Haiku only; no critic. Audit with lint_page.py only. Keep replies short."},
    "balanced": {"label": "Balanced", "model": "opus", "effort": "medium", "cap": 3.0,
                 "line": "Effort: Balanced. Specialists on their usual models (Sonnet, Haiku). Audit: one flynn-critic round on Sonnet."},
    "thorough": {"label": "Thorough", "model": "opus", "effort": "high", "cap": 8.0,
                 "line": "Effort: Thorough. Usual specialists; the final flynn-critic on Opus. Audit: up to three rounds."},
    "max": {"label": "Max", "model": "opus", "effort": "max", "cap": None,
            "line": "Effort: Max. Composer and critic on Opus where it helps. Audit: up to three rounds."},
}
ORDER = ["quick", "balanced", "thorough", "max"]
MODULES = ("studio_chat", "studio_comments_ai", "studio_quick_edit", "studio_home_v2", "studio_systems",
           "studio_recorder", "studio_quorra", "studio_apps")


# ---------------------------------------------------------------- settings, switches, log
def flynn_home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


def chats_root() -> Path:
    return Path(os.environ.get("FLYNN_STUDIO_CHATS", str(flynn_home() / "studio" / "chats")))


def runs_root() -> Path:
    """FLYNN_RUNS_DIR, else flynn's runs/; on a team server, the folder of the person this thread works for."""
    return fpaths.runs_dir()


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    tmp.replace(p)


def settings() -> dict:
    s = {"engine": "sdk", "caps": {}, "effort": "balanced"}
    s.update(_read(flynn_home() / "studio.json"))
    return s


def save_settings(**kw) -> dict:
    s = settings()
    if "caps" in kw:
        caps = dict(s.get("caps", {}))
        for k, v in (kw.pop("caps") or {}).items():
            if k not in EFFORT:
                continue
            if v is None or v == "":
                caps[k] = None
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"the {k} cap must be a number of dollars") from None
            if v < 0:
                raise ValueError(f"the {k} cap can't be negative")
            caps[k] = v
        s["caps"] = caps
    s.update({k: v for k, v in kw.items() if k in ("engine", "effort")})
    if s.get("effort") not in EFFORT:
        raise ValueError("effort must be quick, balanced, thorough or max")
    if s["engine"] not in ("sdk", "cli", "api", "off"):
        raise ValueError("engine must be sdk, cli, api or off")
    _write(flynn_home() / "studio.json", s)
    return s


def cap(level: str) -> float | None:
    caps = settings().get("caps", {})
    return caps[level] if level in caps else EFFORT[level]["cap"]


def feature(name: str, default: bool = True) -> bool:
    v = _read(flynn_home() / "features.json").get(name)
    return default if v is None else v is not False


def set_feature(name: str, on: bool) -> None:
    p = flynn_home() / "features.json"
    d = _read(p)
    d[name] = bool(on)
    _write(p, d)


_LOG_DIR: Path | None = None


def log(msg: str) -> None:
    """studio_chat.log. The folder is fixed at the first line, so a turn thread that outlives its caller (a test whose
    temporary FLYNN_HOME is gone) can't write into the real log."""
    global _LOG_DIR
    try:
        if _LOG_DIR is None:
            _LOG_DIR = flynn_home() / "logs"
        d = _LOG_DIR
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "studio_chat.log", "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def claude_bin() -> str:
    return os.environ.get("FLYNN_CLAUDE_BIN", "claude")


def valid_key(key: str) -> bool:
    return isinstance(key, str) and bool(KEY.fullmatch(key)) and ".." not in key


def _run_ids() -> set[str]:
    root = runs_root()
    return {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()


# ---------------------------------------------------------------- instructions and turn profiles
_STUDIO_TOOLS = (
    "- Ask questions with the studio ask_user tool (a card with options), then end your turn; his answers arrive as your "
    "next message starting with ALIGNED. Do not write NEEDS_ALIGNMENT blocks.\n"
    "- Show finished work with show_result. studio_view tells you what he is looking at and the elements he selected "
    "(selector, option, text, an HTML snippet and key styles; \"this\" or \"these\" in his message means them).\n"
    "- Each message starts with a <studio_context> block and an effort line; follow the effort line for model mix and "
    "audit depth.\n- Write plainly and briefly; Persian text is fine and shows right-to-left.")


def instructions(kind: str = "run", agent: str = "flynn", name: str = "") -> str:
    text = _instructions(kind, agent)
    if team.on():
        who = (name or "the person").split()[0] if name else "the person"
        text = text.replace("You are working inside flynn Studio, the user's local web app for everything flynn makes "
                            f"(http://127.0.0.1:{STUDIO_PORT}).",
                            f"You are working inside flynn Studio on the team server ({team.public_url()}), running on "
                            f"{who}'s own computer through their flynn agent. Their run folders are under runs/ here and "
                            "go to the server when a tool needs them and when your turn ends.")
        text = text.replace("the user's", f"{who}'s").replace("the user ", f"{who} ").replace("the user.", f"{who}.").replace("his ", "their ")
    return text


def _instructions(kind: str = "run", agent: str = "flynn") -> str:
    head = (f"You are working inside flynn Studio, the user's local web app for everything flynn makes "
            f"(http://127.0.0.1:{STUDIO_PORT}). the user is talking to you in Studio's chat panel.\n")
    if kind == "app":
        return head + (
            "This conversation is the App builder: you build and change one web app (Vite + React + TypeScript) in the "
            "current folder, the way Lovable does. Work in small, complete steps; keep the app running (`npm run dev` is "
            "already managed by Studio; never start another dev server); install packages with npm only when needed; "
            "use the design system's tokens in src/theme.css when the app has one. Studio commits your changes as a "
            "version after each turn, so don't commit yourself and never push. Explain what changed in two or three "
            "sentences.\n"
            "- AI features: when the app has AI (<studio_context> says ai: on), use src/lib/ai.ts (ask, askJSON with a "
            "JSON schema, stream) for anything that needs a model: summaries, suggestions, classification, extraction, "
            "chat. It calls the app's own /api/ai endpoint (flynn-ai.ts, the Claude Agent SDK on this computer's Claude "
            "sign-in; text and JSON only, a cost cap per request). Never call a model API directly, never put keys in "
            "the app, and show a clear loading and error state for every AI action. Prefer model haiku for quick, "
            "frequent features and sonnet for harder ones.\n" + _STUDIO_TOOLS)
    if agent == "quorra":
        return head + (
            "You are quorra here, in a run's conversation. Follow your quorra protocol (quorra-build for building, "
            "quorra-mrs for MR checks). The usual job: turn the run's picked Admin Panel design into a "
            "web-app change: export it with studio_export kind tsx, map it to the real UIKit components, "
            "build it in your own work clone with your writer and reviewers, run the repo's checks, then write the "
            "publish request and run publish.py plan. Show the user the plan (branch, files, checks, MR title, fingerprint) "
            "with ask_user, options Approve / Change something. Push and open the MR only after he approves, through "
            "publish.py push --approve. Then call studio_link with the MR URL so it shows on the run.\n" + _STUDIO_TOOLS)
    if kind == "system":
        return head + (
            "This conversation maintains one design system (named in <studio_context>). Updating it from code or a live "
            "product, adding components or tokens, or adding a new system all go through flynn's gated changes "
            "(scripts/proposals.py) and the store's tiers; never mark anything verified yourself. For a live product, "
            "use the recorder (studio_view shows recent recordings) or capture.py. Say what is verified, what is "
            "inferred, and what still needs the user's review.\n" + _STUDIO_TOOLS)
    return head + (
        ("It is docked beside the run canvas. " if kind == "run" else "This is Home, where new work starts. ") +
        "Follow your normal flynn protocol.\n"
        "- Use studio_focus to open the run or option you just made on his canvas. studio_comments lists, replies to "
        "and resolves his pinned comments; studio_edits lists his direct edits waiting for you to apply to the source "
        "page; studio_snapshot freezes options as a version; studio_export runs exports; studio_link attaches a link "
        "(an MR, a Figma file) to the run.\n"
        "- Edits to page/index.html (or options/<x>/page/index.html) appear live on his canvas; re-render with "
        "adapters/html/render.py when shots or PDFs must change.\n" + _STUDIO_TOOLS)


# ---------------------------------------------------------------- the conversation's design system
DESIGN_KINDS = ("home", "run")          # the conversations that design (not system maintenance, not the app builder)
DESIGN_NONE_RULE = ("Use no design system in this conversation: no library components or document theme; build plain, "
                    "well-crafted HTML that follows your rubric.")


class DesignLocked(Exception):
    """The design system can't change: the conversation has messages, or its run names the system."""


def design_rule(conv: "Conversation") -> str:
    """What every turn tells the agent about the conversation's design system ("" while Flynn chooses)."""
    if conv.kind not in DESIGN_KINDS or conv.agent != "flynn":
        return ""
    c = conv.design_choice()
    if c == "auto":
        return ""
    if c == "none":
        return DESIGN_NONE_RULE
    return (f"Use only the {design_name(c)} design system ({c}) for everything you design in this conversation: its "
            "components, tokens, patterns and guide. Do not switch systems; if the request needs something the system "
            "lacks, say so and propose an addition.")


def design_name(sid: str) -> str:
    try:
        import systems
        return systems.display_name(sid)
    except Exception:  # noqa: BLE001
        return sid


def design_options() -> list[dict]:
    """The selector's options, in order: Flynn chooses, Product UI libraries, Documents themes, no design system."""
    import systems
    out = [{"id": "auto", "name": "Flynn chooses", "group": ""}]
    ch = systems.choices()
    out += [{"id": c["id"], "name": c["name"], "group": "Product UI"} for c in ch if c["kind"] == "library"]
    out += [{"id": c["id"], "name": c["name"], "group": "Documents"} for c in ch if c["kind"] == "theme"]
    out.append({"id": "none", "name": "No design system", "group": ""})
    return out


def turn_profile(conv: "Conversation") -> dict:
    """Working folder and permissions for a conversation's turns."""
    if conv.kind == "app":
        # On a team server the app is on the person's own Mac: their agent puts its folder in place of __APP_DIR__.
        app = "__APP_DIR__" if team.on() else apps_root() / conv.key[4:]
        return {"cwd": str(app), "agent": "",
                "allow": ["Read", "Glob", "Grep", "TodoWrite", "WebSearch", "WebFetch",
                          f"Write(/{app}/**)", f"Edit(/{app}/**)", "Bash(npm install:*)", "Bash(npm run build:*)",
                          "Bash(npm run lint:*)", "Bash(npx tsc:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"],
                "deny": [*perms.DENY, "Bash(git commit:*)", "Bash(npm run dev:*)"]}
    if conv.agent == "quorra":
        work = DK_CODE / "quorra-work"
        others = [p for p in (DK_CODE.iterdir() if DK_CODE.exists() else []) if p.is_dir() and p.name != "quorra-work"]
        deny = [r for p in others for r in (f"Write(/{p}/**)", f"Edit(/{p}/**)", f"NotebookEdit(/{p}/**)")]
        return {"cwd": str(QUORRA), "agent": "quorra",
                "allow": ["Read", "Glob", "Grep", "Skill", "Agent", "Task", "TodoWrite", "WebSearch", "WebFetch",
                          f"Bash(python3 {QUORRA}/*)", f"Bash(python3 \"{QUORRA}/*)", f"Bash(python3 {FLYNN}/*)",
                          f"Bash(python3 \"{FLYNN}/*)", f"Write(/{work}/**)", f"Edit(/{work}/**)",
                          f"Write(/{QUORRA}/runs/**)", f"Edit(/{QUORRA}/runs/**)"],
                "deny": [*deny, "Bash(git push:*)"]}
    return {"cwd": str(FLYNN), "agent": "flynn", "allow": list(perms.BASE_ALLOW), "deny": list(perms.DENY)}


# ---------------------------------------------------------------- slash commands and skills
STUDIO_UI_COMMANDS = [   # done by the browser or by Studio itself
    ("new", "New conversation", "Start fresh; this one stays in history", "Conversation"),
    ("history", "Conversations", "Open a past conversation", "Conversation"),
    ("attach", "Attach files", "Add images, PDFs, CSV or other files", "Conversation"),
    ("select", "Select elements", "Pick elements on the canvas to send with your message", "Conversation"),
    ("usage", "Usage", "What this conversation and today have cost", "Conversation"),
    ("stop", "Stop", "Stop the current turn", "Conversation"),
    ("settings", "Settings", "Engine, effort caps and modules", "Conversation"),
    ("systems", "Design systems", "Open the design systems and the recorder", "Design systems"),
    ("record", "Record a product", "Capture a live product's screens and design system", "Design systems"),
    ("help", "Commands", "Every command", "Conversation"),
]
DESK_PROMPTS = ("brainstorm", "ds", "library", "chart", "clu", "quorra", "skill")


def installed_skills() -> list[dict]:
    """flynn's skills (skills/<name>/SKILL.md), each runnable as /<name>."""
    out = []
    for p in sorted((FLYNN / "skills").glob("*/SKILL.md")):
        if p.parent.name in ("flynn-core",):
            continue
        desc = ""
        for line in p.read_text(encoding="utf-8").splitlines()[:12]:
            if line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"')[:160]
        out.append({"name": p.parent.name, "description": desc})
    return out


def commands_list(conv: "Conversation") -> list[dict]:
    import commands as desk
    out = [{"name": c.name, "label": c.label, "hint": c.hint, "kind": "prompt", "arg": c.arg, "group": c.group}
           for c in desk.COMMANDS if c.name in DESK_PROMPTS and (c.name != "chart" or conv.kind != "app")]
    out += [{"name": n, "label": l, "hint": h, "kind": "ui", "arg": "", "group": g} for n, l, h, g in STUDIO_UI_COMMANDS
            if n != "select" or conv.kind == "run"]
    out += [{"name": k["name"], "label": "Use " + k["name"], "hint": k["description"], "kind": "skill", "arg": "what for",
             "group": "Skills"} for k in installed_skills()]
    return out


def command_prompt(conv: "Conversation", name: str, arg: str) -> tuple[str, str]:
    if name in DESK_PROMPTS:
        import commands as desk
        text, shown = desk.prompt(name, arg, {})
        return text.replace("flynn desk", "flynn Studio").replace("canvas library", "library"), shown
    if any(k["name"] == name for k in installed_skills()):
        shown = f"/{name}" + (f" {arg}" if arg else "")
        return (f"Use the {name} skill (skills/{name}/SKILL.md) for this: " + (arg or "(ask me what with ask_user)"), shown)
    raise KeyError(name)


def clean_view(view) -> dict:
    """What the page says the user is looking at, reduced to known fields of known types (it comes from the browser)."""
    if not isinstance(view, dict):
        return {}
    out = {k: str(view[k])[:200] for k in ("run", "option", "version") if isinstance(view.get(k), (str, int))}
    if isinstance(view.get("zoom"), (int, float)):
        out["zoom"] = round(float(view["zoom"]), 3)
    p = view.get("pointed")
    items = p if isinstance(p, list) else [p] if isinstance(p, dict) else []
    sel = []
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        e = {k: str(it[k])[:400] for k in ("selector", "text", "page", "option", "version", "crop")
             if isinstance(it.get(k), (str, int)) and it.get(k) != ""}
        if isinstance(it.get("html"), str) and it["html"]:
            e["html"] = it["html"][:1200]
        if isinstance(it.get("style"), dict):
            e["style"] = {str(k)[:40]: str(v)[:80] for k, v in list(it["style"].items())[:12]}
        if e.get("selector"):
            sel.append(e)
    if sel:
        out["pointed"] = sel
    return out


def context_block(conv: "Conversation", view: dict, effort: str) -> str:
    v = {k: view.get(k) for k in ("run", "option", "version", "zoom") if view.get(k)}
    if conv.key.startswith("run:"):
        v.setdefault("run", conv.key[4:])
    if conv.kind == "system":
        v = {"system": conv.key[7:], **{k: x for k, x in v.items() if k != "run"}}
    if conv.kind == "app":
        folder = apps_root() / conv.key[4:]
        v = {"app": conv.key[4:], "folder": "the current folder" if team.on() else str(folder)}
        meta = _read(folder / "app.json")
        if team.on():
            try:
                import apps
                meta = apps.run_op(conv.me, "info", conv.key[4:], timeout=20)
            except Exception:  # noqa: BLE001 - context never blocks a message
                meta = {}
        if meta.get("system"):
            v["design_system"] = meta["system"]
        v["ai"] = "on: use src/lib/ai.ts" if meta.get("ai") else "off (the user can turn it on with Enable AI)"
        if team.on() and meta.get("system") or (folder / "src" / "theme.css").exists():
            v["theme"] = "src/theme.css holds the design system's tokens as CSS variables; use them for every colour and font"
    if conv.agent == "quorra":
        v["agent"] = "quorra"
    if view.get("pointed"):
        v["pointed"] = view["pointed"]
    run = v.get("run")
    if run:
        try:
            sys.path.insert(0, str(FLYNN / "adapters" / "review"))
            import review
            v["open_comments"] = sum(1 for c in review.load(runs_root() / run).get("comments", []) if c.get("status") == "open")
        except Exception:  # noqa: BLE001 - context never blocks a message
            pass
    return f"<studio_context>{json.dumps(v, ensure_ascii=False)}\n{EFFORT[effort]['line']}</studio_context>"


class StudioSdkEngine(engines.SdkEngine):
    """flynn desk's SDK engine, with a Stop that reaches a silent turn. The desk's stop() only sets a flag that is
    read when the next message arrives; here Stop also asks the client to interrupt through the turn's own event loop,
    and kill() disconnects it (ending the claude process) when the interrupt isn't enough."""

    def __init__(self):
        super().__init__()
        self._loop = None

    def run(self, t, emit) -> None:
        import asyncio
        from claude_agent_sdk import ClaudeSDKClient

        async def go():
            self._loop = asyncio.get_running_loop()
            got_end = False
            async with ClaudeSDKClient(options=self.options(t)) as client:
                self._client = client
                if self._stop.is_set():
                    return False
                await client.query(t.prompt)
                async for m in client.receive_response():
                    d = self.as_dict(m)
                    if d is None:
                        continue
                    for ev in stream.events_from_line(json.dumps(d, default=str)):
                        if ev["t"] == "turn_end":
                            got_end = True
                            engines._finish(ev, emit)
                        else:
                            emit(ev)
            return got_end
        try:
            got_end = asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            emit({"t": "error", "text": f"{type(e).__name__}: {e}"[:400]})
            got_end = False
        finally:
            self._loop = None
        if not got_end:
            emit({"t": "turn_end", "ok": False, "cost": None})

    def _call(self, name: str) -> None:
        import asyncio
        loop, client = self._loop, self._client
        if loop is not None and client is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(getattr(client, name)(), loop)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        super().stop()
        self._call("interrupt")

    def kill(self) -> None:
        self._call("disconnect")


_COST_CACHE: dict[str, tuple[float, str, float]] = {}


def today_cost(root: Path | None = None) -> float:
    """Spend since midnight across every Studio conversation, including archived ones (cached per file by mtime).
    root: one person's chats folder on a team server (default: this computer's)."""
    today = _dt.date.today().isoformat()
    total = 0.0
    root = root or chats_root()
    files = list(root.glob("*/events.jsonl")) + list(root.glob("*/history/*.jsonl")) if root.exists() else []
    for p in files:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        hit = _COST_CACHE.get(str(p))
        if hit and hit[0] == mt and hit[1] == today:
            total += hit[2]
            continue
        sub = 0.0
        if _dt.date.fromtimestamp(mt).isoformat() == today:
            for line in p.read_text(encoding="utf-8").splitlines():
                if '"turn_end"' in line and today in line:
                    try:
                        sub += json.loads(line).get("cost") or 0
                    except ValueError:
                        pass
        _COST_CACHE[str(p)] = (mt, today, sub)
        total += sub
    return total


# ---------------------------------------------------------------- conversations
class Conversation:
    def __init__(self, key: str, me: dict | None = None):
        self.key = key
        self.kind = key.split(":", 1)[0] if ":" in key else "home"
        self.me = dict(me or {})                 # team server: whose conversation this is
        self.base = team.chats_of(me) if me else chats_root()
        self.runs = team.runs_of(me) if me else None
        self.dir = self.base / key.replace(":", "__")
        (self.dir / "uploads").mkdir(parents=True, exist_ok=True)
        (self.dir / "history").mkdir(exist_ok=True)
        self.cond = threading.Condition()      # an RLock underneath: new_chat may run inside open_chat
        self.running = False
        self.engine = None
        self.gen = 0                           # turn generation: events from an abandoned turn are dropped
        self.stop_gen = -1                     # the generation Stop was pressed for
        self.asked = False
        self.view: dict = {}
        st = _read(self.dir / "state.json")
        self.session: str | None = st.get("session")
        self.extra_allow: list[str] = st.get("extra_allow", [])
        self.agent: str = st.get("agent") if st.get("agent") in AGENTS and self.kind == "run" else ("" if self.kind == "app" else "flynn")
        self.last_text = ""
        self.file_id = key                     # flynn desk's tools (propose_skill) name the conversation by this
        eff = st.get("effort", settings().get("effort", "balanced"))
        self.effort: str = eff if eff in EFFORT else "balanced"
        self.design: str = str(st.get("design_system") or "auto")   # "auto", "none" or a system id (see design_choice)
        self.primed: str | None = st.get("primed")   # the session that already got the long instructions
        self.epoch: int = int(st.get("epoch", 0) or 0)   # bumped when the event list is replaced (new or opened chat)
        self.events: list[dict] = self._load()
        self.repair()

    # storage
    def _load(self) -> list[dict]:
        p = self.dir / "events.jsonl"
        out = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def save_state(self) -> None:
        _write(self.dir / "state.json", {"session": self.session, "extra_allow": self.extra_allow, "agent": self.agent,
                                         "effort": self.effort, "primed": self.primed, "epoch": self.epoch,
                                         "design_system": self.design})

    def emit(self, ev: dict, gen: int | None = None) -> None:
        if ev.get("t") in ("turn_end", "user", "moved"):
            ev = {**ev, "at": _dt.datetime.now().isoformat(timespec="seconds")}
        with self.cond:
            if gen is not None and gen != self.gen:
                return
            self.events.append(ev)
            with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.cond.notify_all()

    def tool_emit(self, ev: dict) -> None:
        """A card from a tool call: only while a turn is running, so a stopped turn's late call can't reach a new chat."""
        if not self.running:
            raise RuntimeError("this turn has ended")
        self.emit(ev, self.gen)

    def repair(self) -> None:
        last_user = max((i for i, e in enumerate(self.events) if e.get("t") == "user"), default=-1)
        last_end = max((i for i, e in enumerate(self.events) if e.get("t") == "turn_end"), default=-1)
        if last_user > last_end and team.on() and self._reattach():
            return
        if last_user > last_end:
            self.emit({"t": "error", "text": "Studio restarted while flynn was working, so this turn was cut off. "
                                             "Send your message again, or say continue."})
            self.emit({"t": "turn_end", "ok": False, "cost": None, "interrupted": True})
            log(f"{self.key} closed a turn cut off by a restart")

    MOVED_KEEP = 120.0

    def clear_moved(self) -> bool:
        """Home after its thread moved into a run holds only the "continues in <run>" note, there so the open tab
        follows the thread. Once that's done (two minutes on), Home starts empty instead of showing it for good."""
        with self.cond:
            if self.running or not self.events or any(e.get("t") != "moved" for e in self.events):
                return False
            try:
                at = _dt.datetime.fromisoformat(self.events[-1].get("at") or "2000-01-01T00:00:00").timestamp()
            except ValueError:
                at = 0
            if time.time() - at < self.MOVED_KEEP:
                return False
            self.events = []
            (self.dir / "events.jsonl").write_text("")
            self.epoch += 1
            self.save_state()
            self.cond.notify_all()
            return True

    def _reattach(self) -> bool:
        """Team server, after a restart: this conversation's turn may still be running on the person's computer.
        Wait for their agent and take the rest of the turn (agent_hub.Reattach) instead of closing it."""
        try:
            import agent_hub
        except ImportError:
            return False
        uid = (self.me or {}).get("uid", "")
        jid = next((j for j, r in agent_hub.open_turns().items() if r.get("conv") == self.key and r.get("uid") == uid), None)
        if not jid:
            return False
        rec = agent_hub.open_turns()[jid]
        with self.cond:
            self.running = True
            self.gen += 1
            gen = self.gen
        self.emit({"t": "activity", "agent": "Studio", "tool": "restart",
                   "text": "Studio restarted while Flynn was working; picking the turn up from your computer…"}, gen)
        eng = agent_hub.Reattach(jid, rec.get("email", ""), lambda ev: self._reattach_emit(ev, gen))
        self.engine = eng
        log(f"{self.key} reattached to remote turn {jid[:8]} after a restart")

        def go():
            try:
                eng.run()
            finally:
                with self.cond:
                    if gen == self.gen:
                        self.running, self.engine = False, None
                    self.cond.notify_all()
        threading.Thread(target=go, daemon=True).start()
        return True

    def _reattach_emit(self, ev: dict, gen: int) -> None:
        if ev.get("t") == "session":
            if ev.get("id"):
                self.session = ev["id"]
                self.save_state()
            return
        self.emit(ev, gen)

    def proposed_rules(self) -> set[str]:
        """Permission rules flynn's refused tool calls asked for in this conversation (the only ones Allow accepts)."""
        return {t.get("rule") for e in self.events if e.get("t") == "denied" for t in e.get("tools") or [] if t.get("rule")}

    # design system
    def design_applies(self) -> bool:
        return self.kind in DESIGN_KINDS and self.agent == "flynn"

    def _run_design(self) -> str | None:
        """A run's conversation follows the system its run.json names."""
        if self.kind != "run":
            return None
        import systems
        return systems.run_choice((self.runs or runs_root()) / self.key[4:])

    def design_choice(self) -> str:
        if self.kind not in DESIGN_KINDS:
            return "auto"
        return self._run_design() or self.design

    def design_locked(self) -> str:
        """Why the choice can't change ("" while it can): the run names it, or the conversation has messages."""
        if self.kind not in DESIGN_KINDS:
            return "This conversation doesn't use a design system."
        if self._run_design():
            return "This run's design system is part of the run, so the chat can't swap it."
        if any(e.get("t") == "user" for e in self.events):
            return "Set for this conversation. Start a new conversation to use another design system."
        return ""

    def design_info(self) -> dict:
        c = self.design_choice()
        name = {"auto": "Flynn chooses", "none": "No design system"}.get(c) or design_name(c)
        return {"choice": c, "name": name, "applies": self.kind in DESIGN_KINDS, "locked": bool(self.design_locked()),
                "reason": self.design_locked()}

    def set_design_system(self, value) -> None:
        v = str(value if value is not None else "auto").strip() or "auto"
        with self.cond:
            if v == self.design_choice():
                return
            why = self.design_locked()
            if why:
                raise DesignLocked(why)
            if v not in ("auto", "none") and v not in {o["id"] for o in design_options()}:
                raise ValueError(f"unknown design system {v!r}")
            self.design = v
            self.save_state()

    def _record_design(self, folder: Path) -> None:
        """A run made by this conversation carries its design system in run.json."""
        c = self.design_choice()
        if c != "auto" and self.design_applies():
            import systems
            systems.record_choice(folder, c)

    # turns
    def start_turn(self, text: str, files: list[dict] | None = None, view=None,
                   effort: str | None = None, shown: str | None = None) -> bool:
        if view is not None:
            self.view = clean_view(view)
        self.last_text = text
        if effort in EFFORT and effort != self.effort:
            self.effort = effort
        prompt = text
        if files:
            prompt += "\n\nAli attached these files (read them with Read, which shows images and PDFs):\n" + \
                "\n".join(f"- {f['path']} ({f.get('type') or 'file'})" for f in files)
        try:
            prompt = context_block(self, self.view, self.effort) + "\n\n" + prompt
        except Exception:  # noqa: BLE001 - never block (or wedge) a message on its context
            pass
        with self.cond:
            if self.running:
                return False
            self.running = True
            self.gen += 1
            gen = self.gen
        try:
            self.save_state()
            self.emit({"t": "user", "text": shown if shown is not None else text, "effort": self.effort,
                       **({"files": [f["name"] for f in files]} if files else {})})
            threading.Thread(target=self._run, args=(prompt, gen), daemon=True).start()
        except Exception:
            with self.cond:
                self.running = False
                self.cond.notify_all()
            raise
        return True

    def _turn(self, prompt: str) -> engines.Turn:
        e = EFFORT[self.effort]
        env = ({"STUDIO_URL": team.public_url(), "STUDIO_CONV": self.key} if team.on() else
               {"STUDIO_URL": f"http://127.0.0.1:{STUDIO_PORT}", "STUDIO_CONV": self.key, "STUDIO_TOKEN": TOOL_TOKEN})
        mcp = {"studio": {"type": "stdio", "command": sys.executable, "args": [str(HERE / "studio_mcp.py")], "env": env}}
        first = not self.session or self.primed != self.session
        system = (instructions(self.kind, self.agent, self.me.get("name", "")) if first
                  else "You are in flynn Studio's chat (instructions given earlier in this session).")
        rule = design_rule(self)                  # every turn, in every engine: they all send Turn.system
        if rule:
            system += "\n\n" + rule
        prof = turn_profile(self)
        t = engines.Turn(prompt=prompt, session=self.session, system=system,
                         allow=[*prof["allow"], *self.extra_allow], deny=prof["deny"], file_id=self.key,
                         cwd=prof["cwd"], claude=claude_bin(), user=(self.me.get("email") or "ali"), agent=prof["agent"],
                         mcp=mcp, model=e["model"], effort=e["effort"], budget_usd=cap(self.effort))
        t.sync_runs = [self.key[4:]] if self.kind == "run" else []    # the agent fetches the server's copy first
        t.app = self.key[4:] if self.kind == "app" else None           # the agent runs it in the app's folder
        t.conv_key, t.conv_uid = self.key, (self.me or {}).get("uid", "")  # a restarted server finds it again
        return t

    def _engine(self):
        if self.kind != "app" and self.uses_api():     # a model provider's API, through aisuite (api_engine.py)
            import api_engine
            return api_engine.ApiEngine(self)
        if team.on():                                  # the AI runs on the person's own computer (their flynn agent)
            import agent_hub
            return agent_hub.RemoteEngine()
        name = settings().get("engine", "sdk")
        eng = engines.choose({"sdk": "local", "cli": "cli"}.get(name, "local"))
        return StudioSdkEngine() if type(eng) is engines.SdkEngine else eng

    def _run(self, prompt: str, gen: int) -> None:
        with fpaths.use_runs(self.runs):
            self._run_in(prompt, gen)

    def uses_api(self) -> bool:
        if team.on():
            import api_keys
            return api_keys.choice(self.me or None).get("engine") == "api"
        return settings().get("engine") == "api"

    def _run_in(self, prompt: str, gen: int) -> None:
        t0 = time.time()
        cost = None
        eng = None
        before: set[str] = set()
        try:
            before = _run_ids()
            eng = self._engine()
            self.engine = eng
            self.asked = False
            turn = self._turn(prompt)
            if (eng.name == "api") != str(turn.session or "").startswith("api-"):
                turn.session = None                    # a session from the other kind of engine can't be resumed here
            log(f"{self.key} turn start engine={eng.name} model={turn.model} effort={turn.effort} session={self.session}")

            def emit(ev):
                nonlocal cost
                if ev["t"] == "session":
                    with self.cond:
                        if gen != self.gen:
                            return
                        if ev.get("id") and ev["id"] != self.session:
                            self.session = ev["id"]
                        if self.primed != self.session and turn.system.startswith("You are working inside flynn Studio"):
                            self.primed = self.session
                        self.save_state()
                    return
                if ev["t"] == "card" and ev["card"].get("kind") == "needs_alignment" and self.asked:
                    return
                if ev["t"] == "error" and self.stop_gen == gen:     # the user pressed Stop: not a failure
                    ev = {"t": "stopped"}
                if ev["t"] == "turn_end":
                    cost = ev.get("cost")
                    if self.stop_gen == gen:
                        ev = {**ev, "interrupted": True}
                    c = cap(self.effort)
                    if not ev.get("ok") and c and cost is not None and cost >= c * 0.95:
                        nxt = ORDER[min(ORDER.index(self.effort) + 1, len(ORDER) - 1)]
                        self.emit({"t": "card", "card": {"kind": "effort_cap", "level": self.effort, "spent": cost,
                                                         "next": nxt}}, gen)
                self.emit(ev, gen)
            if self.stop_gen == gen:                       # Stop arrived before the engine started
                self.emit({"t": "turn_end", "ok": False, "cost": None, "interrupted": True}, gen)
            else:
                eng.run(turn, emit)
        except Exception as e:  # noqa: BLE001
            self.emit({"t": "error", "text": f"{type(e).__name__}: {e}"[:400]}, gen)
            self.emit({"t": "turn_end", "ok": False, "cost": None}, gen)
        finally:
            log(f"{self.key} turn end engine={getattr(eng, 'name', '?')} cost={cost} secs={time.time() - t0:.0f}")
            try:
                if gen == self.gen and self.kind in ("home", "system"):
                    self._announce_runs(before, t0)
                if gen == self.gen and self.kind == "app":
                    self._commit_app()
            except Exception as e:  # noqa: BLE001
                log(f"{self.key} announce failed: {e}")
            finally:
                with self.cond:
                    if gen == self.gen:
                        self.running = False
                        self.engine = None
                    self.cond.notify_all()

    def _commit_app(self) -> None:
        """App builder: every turn's changes become a version (a git commit) named after the user's message."""
        try:
            import apps
        except ImportError:
            return
        msg = (self.last_text.strip().splitlines() or ["Change"])[0][:72]
        r = apps.run_op(self.me or None, "commit", self.key[4:], {"message": msg}).get("commit")
        if r:
            self.emit({"t": "version", "app": self.key[4:], **({k: r[k] for k in ("hash", "message") if k in r})})

    def set_agent(self, agent: str) -> None:
        if self.kind != "run" or agent not in AGENTS:
            raise ValueError("only a run's conversation can switch between flynn and quorra")
        with self.cond:
            if self.running:
                raise RuntimeError("flynn is still working; stop the turn first")
            if agent == self.agent:
                return
            self.agent, self.session, self.primed, self.extra_allow = agent, None, None, []
            self.save_state()
        self.emit({"t": "agent", "agent": agent})

    def _announce_runs(self, before: set[str], since: float = 0.0) -> None:
        """A run folder made during this turn (new, and created after the turn began) gets this conversation: on Home
        the whole thread moves into the run's chat (events and session), Home files it in history and starts fresh, and
        the browser follows it to the run's canvas."""
        for rid in sorted(_run_ids() - before):
            folder = runs_root() / rid
            if not (folder / "page" / "index.html").exists() and not (folder / "run.json").exists():
                continue
            try:
                born = getattr(folder.stat(), "st_birthtime", folder.stat().st_ctime)
            except OSError:
                continue
            if since and born < since - 2:
                continue
            self._record_design(folder)
            if self.kind == "home":
                if self.move_to_run(rid):
                    return
            self.emit({"t": "run_created", "run": rid})

    def move_to_run(self, rid: str) -> bool:
        """Move this (Home) conversation into run:<rid>. The run's chat gets every event first, then its own (if it
        already had any), and this conversation's session; Home archives the thread with a link to the run."""
        other = get(f"run:{rid}", self.me or None)
        with self.cond:
            events = list(self.events)
            session, primed, effort, design = self.session, self.primed, self.effort, self.design_choice()
        if not events:
            return False
        with other.cond:
            if other.running:
                return False
            own = [e for e in other.events if e.get("t") != "linked"]
            merged = events + [{"t": "linked", "from": "home", "text": "Moved here from Home."}] + own
            other.events = merged
            with open(other.dir / "events.jsonl", "w", encoding="utf-8") as f:
                for e in merged:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            if not own or not other.session:
                other.session, other.primed = session, primed
            other.effort = effort
            if design != "auto" and not other._run_design():
                other.design = design
            other.epoch += 1
            other.save_state()
            other.cond.notify_all()
        hid = self.new_chat(meta={"run": rid}, ending_turn=True)
        self.emit({"t": "moved", "run": rid, "history": hid})
        log(f"home moved to run:{rid} ({len(events)} events)")
        return True

    def stop(self) -> None:
        with self.cond:
            if not self.running:
                return
            gen = self.gen
            self.stop_gen = gen
            eng = self.engine
        if eng is not None:
            try:
                eng.stop()
            except Exception:  # noqa: BLE001
                pass

        def force():
            with self.cond:
                if not (self.running and self.gen == gen):
                    return
                self.gen += 1                          # the old turn's late events are dropped from here on
                cur = self.engine or eng
            log(f"{self.key} forced stop after {STOP_GRACE}s")
            try:
                if hasattr(cur, "kill"):
                    cur.kill()
                p = getattr(cur, "proc", None)
                if p is not None and p.poll() is None:
                    p.kill()
            except Exception:  # noqa: BLE001
                pass
            self.emit({"t": "stopped"})
            self.emit({"t": "turn_end", "ok": False, "cost": None, "interrupted": True})
            with self.cond:
                self.running = False
                self.engine = None
                self.cond.notify_all()
        t = threading.Timer(STOP_GRACE, force)
        t.daemon = True
        t.start()

    # history
    def new_chat(self, meta: dict | None = None, ending_turn: bool = False) -> str:
        with self.cond:
            if self.running and not ending_turn:      # the turn's own end (moving Home to a run) may archive
                raise RuntimeError("flynn is still working; stop the turn first")
            if not self.events:
                return ""
            hid = _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            ev = self.dir / "events.jsonl"
            if ev.exists():
                shutil.move(str(ev), self.dir / "history" / f"{hid}.jsonl")
            title = next((e.get("text", "") for e in self.events if e.get("t") == "user"), "")[:80]
            _write(self.dir / "history" / f"{hid}.json", {"session": self.session, "title": title,
                                                          "effort": self.effort, "at": hid, "design_system": self.design,
                                                          **(meta or {})})
            self.events, self.session, self.primed, self.extra_allow = [], None, None, []
            self.design = "auto"
            self.epoch += 1
            self.save_state()
            self.cond.notify_all()
        return hid

    def history(self) -> list[dict]:
        out = []
        for p in sorted((self.dir / "history").glob("*.json"), reverse=True):
            if (p.with_suffix(".jsonl")).exists():
                out.append({"id": p.stem, **_read(p)})
        return out

    def open_chat(self, hid: str) -> None:
        if not isinstance(hid, str) or not HID.fullmatch(hid):
            raise KeyError("no such chat")
        src = self.dir / "history" / f"{hid}.jsonl"
        if not src.exists():
            raise KeyError("no such chat")
        meta = _read(self.dir / "history" / f"{hid}.json")
        with self.cond:
            if self.running:
                raise RuntimeError("flynn is still working; stop the turn first")
            self.new_chat()
            shutil.copy(src, self.dir / "events.jsonl")
            self.events = self._load()
            self.session = meta.get("session")
            self.primed = self.session
            self.design = str(meta.get("design_system") or "auto")
            self.effort = meta.get("effort", self.effort) if meta.get("effort") in EFFORT else self.effort
            self.epoch += 1
            self.save_state()
            self.cond.notify_all()

    def cost_summary(self) -> dict:
        conv = sum(e.get("cost") or 0 for e in self.events if e.get("t") == "turn_end")
        return {"conversation": round(conv, 4), "today": round(today_cost(self.base), 4)}

    def state(self) -> dict:
        return {"key": self.key, "running": self.running, "effort": self.effort, "agent": self.agent, "epoch": self.epoch,
                "session": bool(self.session), "events": len(self.events), "cost": self.cost_summary(),
                "design": self.design_info(),
                "efforts": [{"id": k, "label": EFFORT[k]["label"], "cap": cap(k)} for k in ORDER]}


_REG: dict[tuple, Conversation] = {}
_REG_LOCK = threading.Lock()


def get(key: str, me: dict | None = None) -> Conversation:
    """A conversation. On a team server each person has their own (me: the signed-in person); never anyone else's."""
    if not valid_key(key):
        raise KeyError(f"bad conversation {key!r}")
    if team.on() and not (me and me.get("uid")):
        raise KeyError("sign in first")
    rk = ((me or {}).get("uid", ""), key)
    with _REG_LOCK:
        if rk not in _REG:
            _REG[rk] = Conversation(key, me)
        return _REG[rk]


def repair_all() -> None:
    owners = [(chats_root(), None)] if not team.on() else [(team.chats_of(p), p) for p in team.people()]
    for root, me in owners:
        if root and root.exists():
            for d in root.iterdir():
                key = d.name.replace("__", ":", 1)
                if valid_key(key):
                    try:
                        get(key, me)
                    except KeyError:
                        pass


def running_turns(me: dict | None = None) -> list[str]:
    uid = (me or {}).get("uid", "")
    return [k for (u, k), c in _REG.items() if c.running and (not team.on() or u == uid)]


def all_running() -> list[str]:
    return [f"{u}/{k}" if u else k for (u, k), c in _REG.items() if c.running]


def shutdown() -> None:
    for c in list(_REG.values()):
        if c.running and c.engine is not None and hasattr(c.engine, "detach"):
            c.engine.detach()              # a turn on the person's computer keeps running; the restarted server resumes it
            continue
        if c.running and c.engine is not None:
            try:
                c.engine.stop()
            except Exception:  # noqa: BLE001
                pass
            if hasattr(c.engine, "kill"):
                c.engine.kill()
            p = getattr(c.engine, "proc", None)
            if p is not None and p.poll() is None:
                p.kill()


# ---------------------------------------------------------------- HTTP (called from studio.py's handler)

UPLOAD_MAX = 25 * 1024 * 1024


def host_ok(h) -> bool:
    """Reads are refused when the Host isn't Studio itself (a DNS-rebinding page can't read chats). On a team server
    every request is signed in, so the Host check isn't needed (the platform's proxy sets it)."""
    if team.on():
        return True
    port = h.server.server_address[1]
    return (h.headers.get("Host") or "") in (f"127.0.0.1:{port}", f"localhost:{port}")


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def origin_ok(h) -> bool:
    o = h.headers.get("Origin")
    if not o:
        return True
    if team.on():
        return o == team.public_origin()
    port = h.server.server_address[1]
    return o in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


class Srv:
    """What Studio's tools may do to runs (studio.py passes its own functions in)."""

    def __init__(self, root: Path, snapshot, export, summary=None, link=None, list_runs=None, pick=None):
        self.root, self._snapshot, self._export, self._summary, self._link = root.resolve(), snapshot, export, summary, link
        self._list, self._pick = list_runs, pick

    def list_runs(self):
        return self._list() if self._list else []

    def pick(self, run, option, note=""):
        if self._pick is None:
            raise KeyError("picking isn't available")
        return self._pick(run, option, note)

    def link(self, run, url, label="", kind="link"):
        if self._link is None:
            raise KeyError("links aren't available")
        return self._link(run, url, label, kind)

    def summary(self, run):
        if self._summary is None:
            raise KeyError("no summary")
        return self._summary(run)

    def run_path(self, rid: str) -> Path:
        root = fpaths.runs_dir().resolve() if team.on() else self.root     # a team server: the caller's own runs
        p = (root / str(rid)).resolve()
        if root not in p.parents or not p.is_dir():
            raise KeyError(f"no run {rid}")
        return p

    def snapshot(self, run, option, label):
        return self._snapshot(run, option, label)

    def export(self, run, kind, option):
        return self._export(run, kind, option)


SRV: Srv | None = None


def _q(query: dict, k: str, default=None):
    return (query.get(k) or [default])[0]


def health() -> list[dict]:
    out = []

    def add(i, label, status, detail, fix=""):
        out.append({"id": i, "label": label, "status": status, "detail": detail, "fix": fix})
    try:
        import claude_agent_sdk
        add("sdk", "Claude Agent SDK", "ok", f"version {getattr(claude_agent_sdk, '__version__', '?')}")
    except ImportError:
        add("sdk", "Claude Agent SDK", "warn", "not installed; Studio uses the claude CLI instead",
            'python3 -m pip install --user "claude-agent-sdk>=0.1"')
    cb = shutil.which(claude_bin()) or (claude_bin() if os.path.exists(claude_bin()) else None)
    add("claude", "claude command", "ok" if cb else "fail", cb or "not found", "Install Claude Code, or the SDK (it bundles claude)")
    if cb:
        try:
            r = subprocess.run([cb, "auth", "status"], capture_output=True, text=True, timeout=15)
            signed = r.returncode == 0
            add("auth", "Signed in to Claude", "ok" if signed else "fail",
                "signed in" if signed else "not signed in (status only; Studio never reads the token)", "claude auth login")
        except (OSError, subprocess.TimeoutExpired):
            add("auth", "Signed in to Claude", "warn", "could not check", "claude auth status")
    else:
        add("auth", "Signed in to Claude", "fail", "claude not found", "claude auth login")
    agent = Path.home() / ".claude" / "agents" / "flynn.md"
    add("agent", "flynn agent installed", "ok" if agent.exists() else "fail", str(agent),
        'bash "$HOME/agent-stack/install.sh"')
    rr = runs_root()
    add("runs", "Runs folder writable", "ok" if rr.exists() and os.access(rr, os.W_OK) else "fail", str(rr))
    eng = settings().get("engine", "sdk")
    add("engine", "Engine", "warn" if eng == "cli" else ("fail" if eng == "off" else "ok"),
        {"sdk": "Agent SDK (effort caps apply)", "cli": "claude CLI (effort caps can't stop a turn early)",
         "api": "Model API (aisuite)", "off": "chat is off"}.get(eng, eng))
    if eng == "api":
        import api_engine, api_keys
        sdk, ch = api_engine._sdk_ok(), api_keys.choice(None)
        p = api_engine.PROVIDERS.get(ch.get("provider") or "")
        add("api", "Model API", "ok" if sdk["ok"] and p and ch.get("model") and (not p["key"] or api_keys.get(None, ch["provider"])) else "fail",
            f"{p['label']} · {ch.get('model')}" if p and ch.get("model") else "choose a provider, a model and a key below",
            sdk.get("fix", ""))
    add("studio_chat", "Chat module", "ok" if feature("studio_chat") else "warn", "on" if feature("studio_chat") else "off")
    for mod in ("systems", "apps"):
        try:
            out += __import__(mod).health()
        except Exception as e:  # noqa: BLE001 - a module's check never breaks the page
            add(mod, f"{mod} checks", "warn", f"could not run: {e}"[:120])
    return out


TEAM_OFF = ("studio_recorder", "studio_quorra")   # need the person's own Mac; off on a team server


def team_health(me: dict | None) -> list[dict]:
    """The team server's checks for this person: their computer (the flynn agent), sign-in, storage, design systems."""
    out = []

    def add(i, label, status, detail, fix=""):
        out.append({"id": i, "label": label, "status": status, "detail": detail, "fix": fix})
    import agent_hub
    st = agent_hub.HUB.status((me or {}).get("email", "")) if me else {}
    off = st.get("offline") or {}
    if st.get("connected"):
        detail = f"flynn agent on {st.get('host') or 'your computer'}"
        if off.get("until"):
            detail += f"; last offline {off.get('since', '')[11:16]}-{off['until'][11:16]}: {off.get('text') or off.get('reason')}"
        if st.get("outbox"):
            detail += "; still delivering a finished turn"
    elif st.get("last_seen"):
        mins = int((time.time() - float(st["last_seen"])) / 60)
        detail = (f"not heard from your computer for {mins} min. Check it is awake, online and on the VPN; "
                  "the agent reconnects by itself and sends anything it saved")
    else:
        detail = "flynn's AI runs on your own computer with your own Claude account; connect it once"
    add("agent", "Your computer is connected", "ok" if st.get("connected") else "fail", detail,
        "" if st.get("last_seen") else f"curl -fsSL {team.public_url()}/flynn/agent/install.sh | bash")
    ok, why = team.configured()
    add("signin", "Karvansara sign-in", "ok" if ok else "fail", why)
    add("storage", "Your files", "ok", "kept on the server (Postgres), visible only to you")
    sys.path.insert(0, str(FLYNN / "scripts"))
    import systems_policy
    add("systems", "Design systems", "ok", ", ".join(systems_policy.shipped()) + " (maintained by the admin)")
    return out


def settings_payload(me: dict | None = None) -> dict:
    feats = {k: v for k, v in _read(flynn_home() / "features.json").items() if k.startswith("studio_")}
    for k in MODULES:
        feats.setdefault(k, True)
    if team.on():
        for k in TEAM_OFF:
            feats[k] = False
    sys.path.insert(0, str(FLYNN / "scripts"))
    import systems_policy
    editable, why = systems_policy.can_change()
    return {"settings": settings(), "features": feats, "systems": {"editable": editable, "why": why,
            "shipped": systems_policy.shipped()},
            "efforts": [{"id": k, **{x: EFFORT[k][x] for x in ("label", "model", "effort")}, "cap": cap(k)} for k in ORDER],
            "team": team_payload(me)}


def team_payload(me: dict | None) -> dict | None:
    """On a team server: who is signed in, and their computer's connection (the flynn agent)."""
    if not team.on():
        return None
    import agent_hub
    st = agent_hub.HUB.status(me["email"]) if me and me.get("email") else {}
    return {"on": True, "url": team.public_url(), "email": (me or {}).get("email"), "name": (me or {}).get("name"),
            "admin": bool((me or {}).get("admin")), "owner": bool((me or {}).get("owner")), "agent": st,
            "install": f"curl -fsSL {team.public_url()}/flynn/agent/install.sh | bash"}


def _me(h) -> dict | None:
    return getattr(h, "me", None) if team.on() else None


def _models_get(h, parts, query) -> bool:
    if parts[:2] != ["api", "models"]:
        return False
    import api_engine
    me = _me(h)
    try:
        if parts[2:] == ["providers"]:
            h._json(api_engine.providers_payload(me))
        elif parts[2:] == ["list"]:
            h._json({"models": api_engine.list_models(me, _q(query, "provider", ""), _q(query, "base_url", "") or "")})
        else:
            return False
    except Exception as e:  # noqa: BLE001
        h._json({"error": api_engine._clean(f"{e}", me, _q(query, "provider", ""))[:300]}, 400)
    return True


def _models_post(h, parts, body) -> bool:
    if parts[:2] != ["api", "models"]:
        return False
    if not origin_ok(h):
        h._json({"error": "origin not allowed"}, 403)
        return True
    import api_engine, api_keys
    me = _me(h)
    prov = str(body.get("provider") or "")
    try:
        if parts[2:] == ["choice"]:
            eng = body.get("engine")
            if eng is not None and eng not in (("agent", "api") if team.on() else ("sdk", "cli", "api", "off")):
                raise ValueError("unknown engine")
            if prov and prov not in api_engine.PROVIDERS:
                raise ValueError("unknown provider")
            if not team.on() and eng:
                save_settings(engine=eng)
            api_keys.save_choice(me, engine=eng if team.on() else None, provider=prov or None,
                                 model=(str(body["model"]).strip() if body.get("model") is not None else None),
                                 base_url=(str(body["base_url"]).strip() if body.get("base_url") is not None else None),
                                 prices=body.get("prices") if isinstance(body.get("prices"), dict) else None)
            h._json(api_engine.providers_payload(me))
        elif parts[2:] == ["key"]:
            if prov not in api_engine.PROVIDERS:
                raise ValueError("unknown provider")
            api_keys.set_key(me, prov, str(body.get("key") or ""))
            log(f"api key {'saved' if body.get('key') else 'removed'} for {prov}")
            h._json(api_engine.providers_payload(me))
        elif parts[2:] == ["test"]:
            h._json(api_engine.test(me, prov, str(body.get("model") or ""), str(body.get("base_url") or "")))
        else:
            return False
    except Exception as e:  # noqa: BLE001 - never echo a key back
        h._json({"error": api_engine._clean(f"{type(e).__name__}: {e}", me, prov)[:400]}, 400)
    return True


def handle_get(h, parts: list[str], query: dict) -> bool:
    if _models_get(h, parts, query):
        return True
    if parts == ["settings"]:
        h._send((HERE / "static" / "settings.html").read_bytes(), "text/html; charset=utf-8")
        return True
    if parts == ["api", "settings"]:
        h._json(settings_payload(_me(h)))
        return True
    if parts == ["api", "health"]:
        h._json({"checks": team_health(_me(h)) if team.on() else health(), "running": running_turns(_me(h))})
        return True
    if parts[:2] != ["api", "chat"] or len(parts) != 3:
        return False
    if not host_ok(h):
        h._json({"error": "host not allowed"}, 403)
        return True
    try:
        conv = get(_q(query, "c", "home"), _me(h))
    except KeyError as e:
        h._json({"error": str(e)}, 404)
        return True
    conv.clear_moved()
    since = _int(_q(query, "since", None) or h.headers.get("Last-Event-ID"), 0)
    epoch = _q(query, "e", None)
    if epoch is not None and _int(epoch, -1) != conv.epoch:
        since = 0                                   # the list was replaced since this tab last saw it
    what = parts[2]
    if what == "state":
        h._json(conv.state())
    elif what == "history":
        h._json(conv.history())
    elif what == "commands":
        h._json({"commands": commands_list(conv)})
    elif what == "design_system":
        h._json({**conv.design_info(), "options": design_options() if conv.kind in DESIGN_KINDS else []})
    elif what == "events":
        _sse(h, conv, since)
    elif what == "ws":
        if not origin_ok(h):
            h._json({"error": "origin not allowed"}, 403)
        else:
            _ws(h, conv, since)
    else:
        return False
    return True


def _design_post(h, conv: Conversation, value) -> bool:
    """Set the conversation's design system; True when the request was refused (the reply is already sent)."""
    try:
        conv.set_design_system(value)
    except DesignLocked as e:
        h._json({"error": str(e), "design": conv.design_info()}, 409)
        return True
    except ValueError as e:
        h._json({"error": str(e)}, 400)
        return True
    return False


def handle_post(h, parts: list[str], body: dict) -> bool:
    if _models_post(h, parts, body):
        return True
    if parts == ["api", "settings"]:
        if not origin_ok(h):
            h._json({"error": "origin not allowed"}, 403)
            return True
        if team.on() and not (_me(h) or {}).get("admin"):
            h._json({"error": "Only the admin changes the team server's settings."}, 403)
            return True
        kw = {k: body[k] for k in ("engine", "caps", "effort") if k in body}
        if kw:
            save_settings(**kw)
        for k, v in (body.get("features") or {}).items():
            if k.startswith("studio_"):
                set_feature(k, bool(v))
        h._json(settings_payload(_me(h)))
        return True
    if parts[:2] != ["api", "chat"] or len(parts) != 3:
        return False
    what = parts[2]
    if team.on():
        me = _me(h) or {}
        if (what == "tool") != (me.get("via") == "agent"):     # tools only from the person's own agent, and only tools
            h._json({"error": "not allowed"}, 403)
            return True
    elif what == "tool":
        if not secrets.compare_digest(h.headers.get("X-Studio-Token") or "", TOOL_TOKEN):
            h._json({"error": "tool calls come only from Studio's own turns"}, 403)
            return True
    elif not origin_ok(h):
        h._json({"error": "origin not allowed"}, 403)
        return True
    try:
        conv = get(body.get("c") or "home", _me(h))
    except KeyError as e:
        h._json({"error": str(e)}, 404)
        return True
    if what == "tool":
        try:
            import studio_tools
            h._json({"result": studio_tools.call(body.get("name", ""), conv, body.get("args") or {}, SRV)})
        except Exception as e:  # noqa: BLE001 - the tool's error goes back to flynn
            h._json({"error": f"{type(e).__name__}: {e}"[:400]})
        return True
    if what in ("send", "answer", "allow", "command") and conv.kind == "system":
        sys.path.insert(0, str(FLYNN / "scripts"))
        import systems_policy
        ok, why = systems_policy.can_change()
        if not ok:
            h._json({"error": why}, 403)
            return True
    if what in ("send", "answer", "allow"):
        if not feature("studio_chat") or settings().get("engine") == "off":
            h._json({"error": "Studio's chat is off. Turn it on in Settings."}, 503)
            return True
    if what in ("send", "command") and "design_system" in body:    # the first message carries the choice
        if _design_post(h, conv, body.get("design_system")):
            return True
    if what == "design_system":
        if not _design_post(h, conv, body.get("system")):
            h._json({**conv.design_info(), "options": design_options() if conv.kind in DESIGN_KINDS else []})
        return True
    if what == "send":
        files = []
        for fid in body.get("files") or []:
            p = (conv.dir / "uploads" / Path(str(fid)).name)
            if p.exists():
                files.append({"name": p.name.split("-", 1)[-1], "path": str(p), "type": ""})
        ok = conv.start_turn(str(body.get("text") or ""), files=files, view=body.get("view"), effort=body.get("effort"))
        h._json({"ok": True} if ok else {"error": "flynn is still working on your last message."}, 200 if ok else 409)
    elif what == "view":
        conv.view = clean_view(body.get("view"))
        h._json({"ok": True})
    elif what == "command":
        name, arg = str(body.get("name") or ""), str(body.get("arg") or "")
        try:
            text, shown = command_prompt(conv, name, arg)
        except KeyError:
            h._json({"error": f"There is no /{name} command."}, 400)
            return True
        ok = conv.start_turn(text, shown=shown, view=body.get("view"), effort=body.get("effort"))
        h._json({"ok": True} if ok else {"error": "flynn is still working."}, 200 if ok else 409)
    elif what == "skill":
        try:
            import desk_tools
            sid = str(body.get("id") or "")
            m = desk_tools.approve_skill(sid) if body.get("action") == "approve" else desk_tools.reject_skill(sid)
            log(f"{conv.key} skill {m['name']} {m['status']}")
            conv.emit({"t": "skill-result", "id": m["id"], "name": m["name"], "state": m["status"]})
            h._json(m)
        except (ValueError, OSError, KeyError) as e:
            h._json({"error": str(e)}, 400)
    elif what == "agent":
        try:
            conv.set_agent(str(body.get("agent") or ""))
            h._json(conv.state())
        except ValueError as e:
            h._json({"error": str(e)}, 400)
        except RuntimeError as e:
            h._json({"error": str(e)}, 409)
    elif what == "stop":
        conv.stop()
        h._json({"ok": True})
    elif what == "new":
        try:
            h._json({"id": conv.new_chat()})
        except RuntimeError as e:
            h._json({"error": str(e)}, 409)
    elif what == "open":
        try:
            conv.open_chat(str(body.get("id") or ""))
            h._json({"ok": True})
        except (KeyError, RuntimeError) as e:
            h._json({"error": str(e)}, 400)
    elif what == "answer":
        msg = stream.aligned_message(body.get("answers") or [], body.get("note") or "")
        ok = conv.start_turn(msg, shown="Answered:\n" + "\n".join(f"{a['n']}. {a['answer']}" for a in body.get("answers") or []))
        h._json({"ok": True} if ok else {"error": "flynn is still working."}, 200 if ok else 409)
    elif what == "allow":
        rules = [str(r) for r in body.get("rules") or []]
        if not rules or any("/code/" in r for r in rules):
            h._json({"error": "Studio never allows writes to protected code."}, 400)
            return True
        unknown = [r for r in rules if r not in conv.proposed_rules()]
        if unknown:
            h._json({"error": "Studio only allows rules flynn asked for in this conversation."}, 400)
            return True
        conv.extra_allow = sorted(set(conv.extra_allow) | set(rules))
        conv.save_state()
        ok = conv.start_turn("Allowed. Continue where you stopped.", shown="Allowed: " + ", ".join(rules))
        h._json({"ok": ok}, 200 if ok else 409)
    elif what == "upload":
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except ValueError:
            h._json({"error": "bad file data"}, 400)
            return True
        if len(data) > UPLOAD_MAX:
            h._json({"error": "files are limited to 25 MB"}, 400)
            return True
        name = re.sub(r"[^A-Za-z0-9._؀-ۿ-]+", "_", Path(str(body.get("name") or "file")).name).strip("._") or "file"
        fid = f"{secrets.token_hex(4)}-{name}"
        p = conv.dir / "uploads" / fid
        p.write_bytes(data)
        h._json({"id": fid, "name": name, "path": str(p), "type": body.get("type") or "", "size": len(data)})
    else:
        return False
    return True


def _sse(h, conv: Conversation, since: int) -> None:
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    i, epoch = max(0, since), conv.epoch
    try:
        h.wfile.write(f"event: hello\ndata: {json.dumps({'epoch': epoch})}\n\n".encode())
        while True:
            with conv.cond:
                if i >= len(conv.events) and conv.epoch == epoch:
                    conv.cond.wait(timeout=15)
                reset = conv.epoch != epoch
                if reset:
                    i, epoch = 0, conv.epoch
                batch = conv.events[i:]
                running = conv.running
            if reset:
                h.wfile.write(f"event: reset\ndata: {json.dumps({'epoch': epoch})}\n\n".encode())
            if batch:
                for ev in batch:
                    h.wfile.write(f"id: {i}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    i += 1
            elif not reset:
                h.wfile.write(f"event: ping\ndata: {json.dumps({'running': bool(running), 'epoch': epoch})}\n\n".encode())
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        return


def _ws(h, conv: Conversation, since: int) -> None:
    import urllib.request
    import wsproto
    ws = wsproto.accept(h)
    if ws is None:
        h._json({"error": "websocket upgrade expected"}, 400)
        return
    state = {"closed": False}
    port = h.server.server_address[1]
    origin = f"http://127.0.0.1:{port}"

    def pump():
        i, epoch = max(0, since), conv.epoch
        try:
            ws.send_json({"type": "hello", "epoch": epoch})
        except wsproto.Closed:
            return
        while not state["closed"]:
            with conv.cond:
                if i >= len(conv.events) and conv.epoch == epoch:
                    conv.cond.wait(timeout=15)
                reset = conv.epoch != epoch
                if reset:
                    i, epoch = 0, conv.epoch
                batch = conv.events[i:]
                running = conv.running
            try:
                if reset:
                    ws.send_json({"type": "reset", "epoch": epoch})
                if batch:
                    for ev in batch:
                        ws.send_json({"type": "event", "id": i, "ev": ev})
                        i += 1
                else:
                    ws.send_json({"type": "ping", "running": bool(running), "epoch": epoch})
            except wsproto.Closed:
                return

    def call(m: dict):
        path = str(m.get("path") or "")
        method = "GET" if m.get("method") == "GET" else "POST"
        if not path.startswith("/api/") or ".." in path or path.startswith(("/api/chat/ws", "/api/chat/events", "/api/chat/tool")):
            status, body = 400, {"error": "not an allowed /api route"}
        else:
            data = None if method == "GET" else json.dumps(m.get("body") or {}).encode()
            hdrs = {"Content-Type": "application/json", "Origin": origin}
            if team.on():                         # the same person, through Studio's own address
                hdrs.update({"Origin": team.public_origin(), "Cookie": h.headers.get("Cookie") or ""})
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            except OSError as e:
                status, raw = 502, json.dumps({"error": str(e)[:200]}).encode()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {"error": "bad reply"}
        try:
            ws.send_json({"type": "reply", "id": m.get("id"), "status": status, "body": body})
        except wsproto.Closed:
            pass

    threading.Thread(target=pump, daemon=True).start()
    try:
        ws.settimeout(120)
        while True:
            m = ws.recv_json()
            kind = m.get("type") if isinstance(m, dict) else None
            if kind == "view":
                conv.view = clean_view(m.get("view"))
            elif kind == "call":
                threading.Thread(target=call, args=(m,), daemon=True).start()
    except (wsproto.Closed, OSError, ValueError):
        pass
    finally:
        state["closed"] = True
        ws.close()
        with conv.cond:
            conv.cond.notify_all()
