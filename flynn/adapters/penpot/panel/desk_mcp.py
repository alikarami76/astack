#!/usr/bin/env python3
"""The flynn desk tools as a stdio MCP server, for local Claude (the Agent SDK or the claude CLI).

The panel server starts flynn with this server configured as `desk` and FLYNN_DESK_FILE set to the conversation's
file id. Tool definitions come from desk_tools.py; every call goes to the panel server (POST /api/tool), which
runs it next to the conversation. Speaks newline-delimited JSON-RPC 2.0 (MCP over stdio).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desk_tools  # noqa: E402

PANEL = os.environ.get("FLYNN_DESK_PANEL", "http://127.0.0.1:8772")
FILE = os.environ.get("FLYNN_DESK_FILE", "")
TOKEN = os.environ.get("FLYNN_DESK_TOKEN", "")      # on a flynn agent: its token for the team server
RUNS = os.environ.get("FLYNN_DESK_RUNS", "")        # on a flynn agent: its local runs folder
PROTOCOL = "2025-06-18"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def sync_run(name: str, args: dict, when: str) -> None:
    """On a flynn agent, run folders live on this computer: send the run up before a tool that names it, and bring
    the files canvas_pull and canvas_check make on the server back after, so flynn can Read them here."""
    run = Path(str(args.get("run") or "")).name
    if not (RUNS and run and TOKEN):
        return
    import base64
    import io
    import tarfile
    local = Path(RUNS) / run
    agent = PANEL.removesuffix("/flynn") + "/flynn/agent"
    if when == "before" and local.is_dir():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for f in sorted(local.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    tf.add(f, arcname=str(f.relative_to(local)), recursive=False)
        body = json.dumps({"run": run, "tgz": base64.b64encode(buf.getvalue()).decode()}).encode()
        urllib.request.urlopen(urllib.request.Request(agent + "/run-upload", body, _headers(), method="POST"), timeout=600).read()
    back = {"canvas_pull": "penpot-pull", "canvas_check": "canvas-check"}.get(name)   # files the tool made there
    if when == "after" and back:
        req = urllib.request.Request(f"{agent}/run-download?run={run}&sub={back}", headers=_headers())
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
        dest = local / back
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.isfile() and not m.name.startswith("/") and ".." not in Path(m.name).parts:
                    tf.extract(m, dest)


def forward(name: str, args: dict) -> tuple[str, bool]:
    try:
        sync_run(name, args, "before")
    except OSError as e:
        return f"Could not send the run folder to the flynn desk server ({e}).", True
    body = json.dumps({"file": FILE, "name": name, "args": args}).encode()
    req = urllib.request.Request(PANEL + "/api/tool", body, _headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read())
        except ValueError:
            j = {"error": f"HTTP {e.code}"}
    except OSError as e:
        return f"The flynn desk panel server is not reachable ({e}).", True
    if j.get("error"):
        return str(j["error"]), True
    try:
        sync_run(name, args, "after")
    except OSError as e:
        return f"The tool ran, but its files could not be brought back from the server ({e}).", True
    return json.dumps(j.get("result"), ensure_ascii=False), False


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:                                   # a notification
        return None
    if method == "initialize":
        res = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
               "capabilities": {"tools": {}}, "serverInfo": {"name": "flynn-desk", "version": "1"}}
    elif method == "tools/list":
        res = {"tools": desk_tools.schemas("mcp")}
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
        except Exception as e:  # noqa: BLE001 - never kill the session over one bad message
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)[:300]}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
