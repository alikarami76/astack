"""Studio's tools as a stdio MCP server for one chat turn. Calls go to Studio (STUDIO_URL /api/chat/tool) with the
conversation key and the per-process token, so only turns Studio started can use them.

On a flynn agent (Studio on a team server) the calls carry the agent's token instead (STUDIO_BEARER), and a run folder
the call names (or the conversation's run) goes up to the server first when it changed, so the tool sees the same files."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_tools  # noqa: E402

URL = os.environ.get("STUDIO_URL", "http://127.0.0.1:8770")
CONV = os.environ.get("STUDIO_CONV", "home")
TOKEN = os.environ.get("STUDIO_TOKEN", "")
BEARER = os.environ.get("STUDIO_BEARER", "")
RUNS = os.environ.get("STUDIO_RUNS", "")


def _sync(run: str) -> None:
    """Upload runs/<run> from this computer when its files changed since the last upload (agent only)."""
    if not (BEARER and RUNS and run):
        return
    d = Path(RUNS) / run
    if not d.is_dir() or "/" in run or ".." in run:
        return
    newest = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()), default=0)
    mark = Path(RUNS) / f".{run}.uploaded"
    try:
        if mark.exists() and float(mark.read_text() or 0) >= newest:
            return
    except (OSError, ValueError):
        pass
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    import flynn_agent
    flynn_agent.upload_run(flynn_agent.Server(URL, BEARER), d)
    mark.write_text(str(newest))
PROTOCOL = "2024-11-05"


def forward(name: str, args: dict) -> tuple[str, bool]:
    body = json.dumps({"c": CONV, "name": name, "args": args}).encode()
    headers = {"Content-Type": "application/json"}
    ctx = None
    if BEARER:
        headers["Authorization"] = f"Bearer {BEARER}"
        try:
            for run in {str(args.get("run") or ""), CONV[4:] if CONV.startswith("run:") else ""} - {""}:
                _sync(run)
        except Exception as e:  # noqa: BLE001 - the tool still runs, on the server's copy
            sys.stderr.write(f"run upload failed: {e}\n")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
        import flynn_agent
        ctx = flynn_agent._ssl()
    else:
        headers["X-Studio-Token"] = TOKEN
    req = urllib.request.Request(URL + "/api/chat/tool", body, headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600, **({"context": ctx} if ctx else {})) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read())
        except ValueError:
            j = {"error": f"HTTP {e.code}"}
    except OSError as e:
        return f"Studio is not reachable ({e}).", True
    if j.get("error"):
        return str(j["error"]), True
    return json.dumps(j.get("result"), ensure_ascii=False), False


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    if method == "initialize":
        res = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
               "capabilities": {"tools": {}}, "serverInfo": {"name": "flynn-studio", "version": "1"}}
    elif method == "tools/list":
        res = {"tools": studio_tools.schemas()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        text, err = forward(p.get("name", ""), p.get("arguments") or {})
        res = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        res = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(json.loads(line))
        except Exception as e:  # noqa: BLE001
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)[:300]}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
