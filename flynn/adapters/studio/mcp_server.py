"""Studio as an MCP server: any AI app (Claude Code, Claude Desktop, Cursor, Codex, ChatGPT connectors, ...) can create
and update designs in Studio through the tools in design_tools.py.

Transport: MCP Streamable HTTP at /mcp (JSON-RPC 2.0 over POST; each request gets a JSON answer, so no server-sent
stream is needed). Authentication: a bearer token made in Settings ("Connect an AI"), one per app, shown once; stored
hashed in $FLYNN_HOME/studio-mcp-tokens.json. On a team server a token belongs to the person who made it and works
only inside their own runs; it opens /mcp and nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

import design_tools

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
_lock = threading.Lock()
INSTRUCTIONS = (
    "Flynn Studio holds a person's designs (UI screens, release notes, reports, decks, newsletters), each a run with one "
    "or more options (HTML pages) that they compare on a canvas, comment on and export. Work like this: "
    "studio_list_runs or studio_create_run; read the design system's guide (studio_get_system) before designing; change "
    "pages with studio_write_file / studio_edit_file; look at the result with studio_render after every change and fix "
    "what the checks and studio_lint report; add options with studio_add_option to compare directions. Design systems "
    "are read-only (the admin maintains them). For a full design process (brief, contract, options, critique), hand the "
    "request to Flynn with flynn_ask.")


def _path() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn"))) / "studio-mcp-tokens.json"


def _h(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _tokens() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def issue(uid: str, name: str) -> str:
    tok = "fsm_" + secrets.token_urlsafe(32)
    with _lock:
        d = _tokens()
        d[_h(tok)] = {"uid": uid or "", "name": str(name or "AI app")[:60], "created": int(time.time()), "last": None,
                      "hint": tok[-4:]}
        _save(d)
    return tok


def tokens_of(uid: str) -> list[dict]:
    return [{"id": k[:12], "name": v["name"], "created": v["created"], "last": v.get("last"), "hint": v.get("hint")}
            for k, v in _tokens().items() if v.get("uid", "") == (uid or "")]


def revoke(uid: str, tid: str) -> bool:
    with _lock:
        d = _tokens()
        keys = [k for k, v in d.items() if k.startswith(tid) and v.get("uid", "") == (uid or "") and len(tid) >= 8]
        for k in keys:
            d.pop(k)
        _save(d)
    return bool(keys)


def who(tok: str) -> dict | None:
    """The token's owner {uid, name}, or None. Marks when it was last used (at most once a minute)."""
    if not tok.startswith("fsm_"):
        return None
    k = _h(tok)
    d = _tokens()
    v = d.get(k)
    if not v:
        return None
    if not v.get("last") or time.time() - v["last"] > 60:
        with _lock:
            d = _tokens()
            if k in d:
                d[k]["last"] = int(time.time())
                _save(d)
    return {"uid": v.get("uid", ""), "app": v.get("name")}


# ---------------------------------------------------------------- JSON-RPC
def _result(mid, res):
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def _error(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


def handle_message(msg: dict, ctx: design_tools.Ctx) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "not a JSON-RPC 2.0 message")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:                                 # a notification (initialized, cancelled): nothing to answer
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return _result(mid, {"protocolVersion": want if want in SUPPORTED else PROTOCOL,
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "flynn-studio", "title": "Flynn Studio", "version": "1"},
                             "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        tools = []
        for s in design_tools.schemas():
            t = dict(s)
            t["annotations"] = {"readOnlyHint": not design_tools._TOOLS[s["name"]]["writes"]}
            tools.append(t)
        return _result(mid, {"tools": tools})
    if method == "tools/call":
        name, args = params.get("name") or "", params.get("arguments") or {}
        try:
            out = design_tools.call(name, ctx, args)
        except KeyError as e:
            if name not in design_tools._TOOLS:
                return _error(mid, -32602, f"unknown tool {name}")
            return _result(mid, {"content": [{"type": "text", "text": str(e.args[0] if e.args else e)}], "isError": True})
        except Exception as e:  # noqa: BLE001 - the tool's error goes back to the AI
            return _result(mid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"[:1500]}], "isError": True})
        content = []
        img = out.pop("_image", None) if isinstance(out, dict) else None
        content.append({"type": "text", "text": json.dumps(out, ensure_ascii=False)[:200000]})
        if img:
            content.append({"type": "image", "mimeType": img["mime"], "data": img["data"]})
        return _result(mid, {"content": content, "isError": False, **({"structuredContent": out} if isinstance(out, dict) else {})})
    if method in ("resources/list", "prompts/list"):
        return _result(mid, {method.split("/")[0]: []})
    return _error(mid, -32601, f"unknown method {method}")


def handle(h, me: dict | None, base_url: str) -> None:
    """POST /mcp (one message or a batch); GET /mcp is refused (no server-initiated stream); DELETE ends nothing."""
    if h.command == "GET":
        h.send_response(405)
        h.send_header("Allow", "POST")
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    if h.command == "DELETE":
        h.send_response(200)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    n = int(h.headers.get("Content-Length") or 0)
    try:
        body = json.loads(h.rfile.read(min(n, 8 * 1024 * 1024)) or b"null")
    except ValueError:
        return h._json(_error(None, -32700, "parse error"), 400)
    ctx = design_tools.Ctx(me=me, base_url=base_url, srv=__import__("chat").SRV)
    if isinstance(body, list):
        out = [r for r in (handle_message(m, ctx) for m in body) if r is not None]
    else:
        out = handle_message(body, ctx)
    if out is None or out == []:
        h.send_response(202)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    data = json.dumps(out, ensure_ascii=False).encode()
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    if isinstance(body, dict) and body.get("method") == "initialize":
        h.send_header("Mcp-Session-Id", secrets.token_hex(16))
    h.end_headers()
    h.wfile.write(data)
