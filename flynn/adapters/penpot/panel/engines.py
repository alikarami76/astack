"""AI engines for flynn desk: what runs one turn of flynn for a conversation.

Every engine takes the same inputs (the prompt, the session to resume, the system context, the allowed and denied
tools, and the flynn desk tools from desk_tools.py) and produces the same panel events through `emit` (see
stream.py for the event list). The conversation code never knows which engine ran.

  local   the user's own Claude through the Claude Agent SDK (`claude_agent_sdk`), with flynn's agent, skills and
          files, and the desk tools as an MCP server. Falls back to `cli` when the SDK is not installed.
  cli     the same, by running `claude -p --output-format stream-json` directly (the original path).
  remote  the team server (FLYNN_ENGINE=remote): the turn goes to the signed-in person's flynn agent, which runs
          it on their own computer with their own Claude (agent_hub.py, adapters/agent/flynn_agent.py).
  api     the default for other people later: the Messages API with tool use, flynn's prompt and the desk tools,
          in a loop run here. Not available yet; choosing it says so.

The choice is one setting for the whole app: ~/.flynn/desk.json {"engine": "local" | "cli" | "api"}.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[2]
sys.path.insert(0, str(HERE))
import stream  # noqa: E402

DESK_TOOLS = "mcp__desk"          # the prefix Claude gives the desk tools; allowing it allows all of them


@dataclass
class Turn:
    prompt: str
    session: str | None
    system: str                     # appended to the agent's system prompt
    allow: list[str]
    deny: list[str]
    file_id: str
    cwd: str = str(FLYNN)
    claude: str = "claude"
    user: str = ""                  # who sent it (the signed-in email); the remote engine routes the turn by it
    # Specialists (the Agent tool) run in the foreground: a turn's process ends with flynn's reply, and a background
    # specialist would be killed with it, its work lost (2026-10-06: every composer and clu hand-off died this way).
    env: dict = field(default_factory=lambda: {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
    agent: str = "flynn"            # the Claude Code agent the turn runs as ("" = no named agent; Studio can pick quorra)
    mcp: dict | None = None         # MCP servers by name; None keeps the desk tools ({"desk": desk_server(file_id)})
    model: str | None = None        # None = the CLI default
    effort: str | None = None       # low | medium | high | xhigh | max; None = default
    budget_usd: float | None = None # SDK only: the turn stops when it has spent this much


def settings_path() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn"))) / "desk.json"


def _fstore():
    sys.path.insert(0, str(FLYNN / "scripts"))
    import fstore
    return fstore


def settings() -> dict:
    """fstore settings "desk": ~/.flynn/desk.json on files (settings_path), or Postgres with FLYNN_DB_URL."""
    st = _fstore().get("settings", "desk", None, file=settings_path())
    return st if isinstance(st, dict) else {}


def save_settings(**kw) -> dict:
    st = {**settings(), **kw}
    _fstore().put("settings", "desk", st, file=settings_path())
    return st


def sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except ImportError:
        return False


def desk_server(file_id: str) -> dict:
    """The desk tools as a stdio MCP server for this conversation. On a flynn agent (adapters/agent) the tools call
    the team server with the agent's token, and send run folders up before a tool that names a run."""
    env = {"FLYNN_DESK_FILE": file_id, "FLYNN_DESK_PANEL": os.environ.get("FLYNN_DESK_PANEL", "http://127.0.0.1:8772")}
    for k in ("FLYNN_DESK_TOKEN", "FLYNN_DESK_RUNS"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return {"type": "stdio", "command": sys.executable, "args": [str(HERE / "desk_mcp.py")], "env": env}


def mcp_servers(t: "Turn") -> dict:
    return t.mcp if t.mcp is not None else {"desk": desk_server(t.file_id)}


def mcp_allow(t: "Turn") -> list[str]:
    return [f"mcp__{name}" for name in mcp_servers(t)]


class Engine:
    name = "base"
    label = ""

    def run(self, turn: Turn, emit: Callable[[dict], None]) -> None:
        """Run one turn; emit panel events (session, delta, tool, denied, card, turn_end...). Blocks until done."""
        raise NotImplementedError

    def stop(self) -> None:
        pass


def _finish(ev: dict, emit) -> None:
    """turn_end carries flynn's final text: text blocks (the old NEEDS_ALIGNMENT/RESULT form) still become cards."""
    for card in stream.parse_blocks(ev.get("text", "")):
        emit({"t": "card", "card": card})
    emit({"t": "turn_end", "ok": ev.get("ok"), "cost": ev.get("cost")})


class CliEngine(Engine):
    name, label = "cli", "Local Claude (CLI)"

    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def cmd(self, t: Turn) -> list[str]:
        cmd = [t.claude, "-p", t.prompt, *(["--agent", t.agent] if t.agent else []),
               "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--permission-mode", "auto", "--permission-prompts", "none",
               "--append-system-prompt", t.system,
               "--mcp-config", json.dumps({"mcpServers": mcp_servers(t)})]
        if t.model:
            cmd += ["--model", t.model]
        if t.effort:
            cmd += ["--effort", t.effort]
        if t.session:
            cmd += ["--resume", t.session]
        cmd += ["--allowedTools", *t.allow, *mcp_allow(t), "--disallowedTools", *t.deny]
        return cmd

    def run(self, t: Turn, emit) -> None:
        got_end = False
        try:
            self.proc = subprocess.Popen(self.cmd(t), cwd=t.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1, env={**os.environ, **t.env})
        except FileNotFoundError:
            emit({"t": "error", "text": f"claude not found ({t.claude})"})
            emit({"t": "turn_end", "ok": False, "cost": None})
            return
        for line in self.proc.stdout:
            for ev in stream.events_from_line(line):
                if ev["t"] == "turn_end":
                    got_end = True
                    _finish(ev, emit)
                else:
                    emit(ev)
        self.proc.wait()
        if not got_end:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            emit({"t": "error", "text": (err.strip().splitlines() or ["flynn stopped without an answer"])[-1][:400]})
            emit({"t": "turn_end", "ok": False, "cost": None})

    def stop(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            p.terminate()


class SdkEngine(Engine):
    """The Claude Agent SDK. Its messages are turned into the same dicts the CLI streams, so stream.py parses both."""
    name, label = "local", "Local Claude (Agent SDK)"

    def __init__(self):
        self._stop = threading.Event()
        self._client = None

    def options(self, t: Turn):
        from claude_agent_sdk import ClaudeAgentOptions
        extra = {}
        if t.model:
            extra["model"] = t.model
        if t.effort:
            extra["effort"] = t.effort
        if t.budget_usd:
            extra["max_budget_usd"] = t.budget_usd
        return ClaudeAgentOptions(
            cwd=t.cwd, resume=t.session, cli_path=t.claude if os.path.sep in t.claude else (shutil.which(t.claude) or None),
            system_prompt={"type": "preset", "preset": "claude_code", "append": t.system},
            setting_sources=["user", "project", "local"],
            allowed_tools=[*t.allow, *mcp_allow(t)], disallowed_tools=list(t.deny),
            permission_mode="auto", mcp_servers=mcp_servers(t),
            include_partial_messages=True, env=dict(t.env),
            # One message from Claude Code can be a big tool result (a screenshot, a long page): the SDK's 1 MB default
            # ended turns with "JSON message exceeded maximum buffer size" (2026-10-21).
            max_buffer_size=64 * 1024 * 1024,
            extra_args={**({"agent": t.agent} if t.agent else {}), "permission-prompts": "none"}, **extra)

    @staticmethod
    def as_dict(m) -> dict | None:
        """An SDK message as the CLI's stream-json line."""
        import dataclasses
        kind = type(m).__name__
        if kind == "SystemMessage":
            return {"type": "system", "subtype": getattr(m, "subtype", ""), **(getattr(m, "data", None) or {})}
        if kind == "StreamEvent":
            return {"type": "stream_event", "event": m.event, "parent_tool_use_id": getattr(m, "parent_tool_use_id", None),
                    "session_id": getattr(m, "session_id", None)}
        if kind == "AssistantMessage":
            blocks = []
            for b in m.content:
                bk = type(b).__name__
                if bk == "ToolUseBlock":
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                elif bk == "TextBlock":
                    blocks.append({"type": "text", "text": b.text})
            return {"type": "assistant", "message": {"content": blocks}, "parent_tool_use_id": getattr(m, "parent_tool_use_id", None)}
        if kind == "ResultMessage":
            d = dataclasses.asdict(m) if dataclasses.is_dataclass(m) else dict(vars(m))
            return {"type": "result", **d}
        return None

    def run(self, t: Turn, emit) -> None:
        import asyncio
        from claude_agent_sdk import ClaudeSDKClient

        async def go():
            got_end = False
            async with ClaudeSDKClient(options=self.options(t)) as client:
                self._client = client
                await client.query(t.prompt)
                async for m in client.receive_response():
                    if self._stop.is_set():
                        await client.interrupt()
                        break
                    d = self.as_dict(m)
                    if d is None:
                        continue
                    for ev in stream.events_from_line(json.dumps(d, default=str)):
                        if ev["t"] == "turn_end":
                            got_end = True
                            _finish(ev, emit)
                        else:
                            emit(ev)
            return got_end
        try:
            got_end = asyncio.run(go())
        except Exception as e:  # noqa: BLE001 - shown in the tab, like a CLI failure
            emit({"t": "error", "text": f"{type(e).__name__}: {e}"[:400]})
            got_end = False
        if not got_end:
            emit({"t": "turn_end", "ok": False, "cost": None})

    def stop(self) -> None:
        self._stop.set()


class ApiEngine(Engine):
    """Later: the Messages API with tool use. The loop will be: send flynn's prompt and the conversation with
    tools = desk_tools.schemas("api") (plus file and shell tools of its own), run each tool_use through
    desk_tools.call() in-process, send the results back, and repeat until the model ends its turn; the API's
    streaming events map to delta / tool / turn_end like the other engines."""
    name, label = "api", "Claude API"

    def run(self, t: Turn, emit) -> None:
        emit({"t": "error", "text": "The API engine is not available yet. Choose Local Claude in /settings."})
        emit({"t": "turn_end", "ok": False, "cost": None})


def _remote():
    import agent_hub
    return agent_hub.RemoteEngine()


_remote.label = "Your computer (flynn agent)"
ENGINES = {"local": SdkEngine, "cli": CliEngine, "api": ApiEngine, "remote": _remote}


def choose(name: str | None = None) -> Engine:
    name = name or os.environ.get("FLYNN_ENGINE") or settings().get("engine") or "local"
    if name == "local" and not sdk_available():
        name = "cli"
    return ENGINES.get(name, CliEngine)()


def describe() -> dict:
    cur = os.environ.get("FLYNN_ENGINE") or settings().get("engine") or "local"
    return {"engine": cur, "running": choose(cur).name, "sdk": sdk_available(),
            "options": [{"id": k, "label": v.label, "available": k != "api" and (k != "local" or sdk_available())}
                        for k, v in ENGINES.items() if k != "remote" or cur == "remote"]}
