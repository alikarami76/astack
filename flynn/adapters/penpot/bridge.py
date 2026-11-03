#!/usr/bin/env python3
"""flynn -> Penpot bridge: runs Plugin API JavaScript in the open Penpot file through Penpot's built-in
MCP connector (http://127.0.0.1:9001/mcp/stream?userToken=<key>). The connector's plugin runs in whichever
browser tab has a Penpot file open (logged in as flynn's account).

  bridge.py ping                         which file and page are open
  bridge.py js '<code>' | @file.js       run code; prints its JSON result

The MCP key (account mcp-key) comes from scripts/fsecrets.py (env, secrets folder, or the macOS keychain, service
flynn-penpot) and is never printed.
Environment: FLYNN_PENPOT_MCP (default http://127.0.0.1:9001/mcp/stream)."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fsecrets  # noqa: E402


class PenpotError(RuntimeError):
    pass


def _keychain(account: str) -> str | None:
    """Kept under its old name: builder.py and others import it."""
    return fsecrets.get(account)


class Bridge:
    def __init__(self, url: str | None = None, key: str | None = None, timeout: float = 180):
        self.base = url or os.environ.get("FLYNN_PENPOT_MCP", "http://127.0.0.1:9001/mcp/stream")
        self._key = key if key is not None else _keychain("mcp-key")
        self.timeout = timeout
        self._session: str | None = None
        self._ids = itertools.count(1)

    def __repr__(self) -> str:
        return f"Bridge({self.base!r}, key={'set' if self._key else 'none'})"

    def _url(self) -> str:
        if not self._key:
            return self.base
        return self.base + ("&" if "?" in self.base else "?") + "userToken=" + urllib.parse.quote(self._key)

    def _post(self, payload: dict):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session:
            h["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(self._url(), json.dumps(payload).encode(), h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self._session = r.headers.get("Mcp-Session-Id") or self._session
                raw = r.read().decode()
                if not raw.strip():
                    return None
                if "event-stream" in r.headers.get("Content-Type", ""):
                    datas = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
                    raw = datas[-1] if datas else "{}"
                msg = json.loads(raw)
        except urllib.error.URLError as e:
            if getattr(e, "code", None) == 413:
                raise PenpotError("The request was too large for the canvas connector; send it in smaller parts.") from None
            raise PenpotError(f"The flynn canvas connector is not reachable ({e.reason}). Is the canvas running?") from None
        if "error" in msg:
            raise PenpotError(str(msg["error"].get("message", msg["error"])))
        return msg.get("result")

    def _ensure(self) -> None:
        if self._session:
            return
        self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "flynn", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict) -> dict:
        self._ensure()
        res = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                          "params": {"name": tool, "arguments": args}}) or {}
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise PenpotError(text[:800] or "tool error")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def run_js(self, code: str):
        out = self.call("execute_code", {"code": code})
        if isinstance(out, dict) and "result" in out:
            return out["result"]
        if isinstance(out, dict) and "text" in out and ("no plugin" in out["text"].lower() or "not connected" in out["text"].lower()):
            raise PenpotError("No canvas file is connected. Open the file in flynn and wait for '● MCP'.")
        return out

    def export_png(self, shape_id: str) -> bytes:
        """A PNG of one shape, through the connector's export tool."""
        import base64
        self._ensure()
        res = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                          "params": {"name": "export_shape", "arguments": {"shapeId": shape_id, "format": "png"}}}) or {}
        imgs = [c for c in res.get("content", []) if c.get("type") == "image"]
        if res.get("isError") or not imgs:
            raise PenpotError("export failed: " + "".join(c.get("text", "") for c in res.get("content", []))[:300])
        return base64.b64decode(imgs[0]["data"])

    def current(self) -> dict:
        return self.run_js("return { file: penpot.currentFile && penpot.currentFile.id, fileName: penpot.currentFile && penpot.currentFile.name, page: penpot.currentPage && penpot.currentPage.name };")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    j = sub.add_parser("js")
    j.add_argument("code")
    a = ap.parse_args(argv)
    b = Bridge()
    try:
        if a.cmd == "ping":
            out = b.current()
        else:
            code = open(a.code[1:], encoding="utf-8").read() if a.code.startswith("@") else a.code
            out = b.run_js(code)
    except PenpotError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
