"""Studio's MCP server: tokens from Settings, the JSON-RPC handshake, and the design tools working inside the caller's
own runs (Studio on this computer, and a team server where each token belongs to one person)."""
import http.client
import importlib
import json
import sys
import threading
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))


def _serve(tmp_path, monkeypatch, team=False):
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "runs").mkdir(exist_ok=True)
    if team:
        monkeypatch.setenv("STUDIO_TEAM", "1")
        monkeypatch.setenv("STUDIO_PUBLIC_URL", "https://studio.example.com")
    else:
        monkeypatch.delenv("STUDIO_TEAM", raising=False)
    import team as team_mod, chat, studio, mcp_server, design_tools
    for m in (team_mod, chat, design_tools, mcp_server, studio):
        importlib.reload(m)
    httpd = studio.serve(0, open_browser=False, root=tmp_path / "runs")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        return r.status, dict(r.getheaders()), (json.loads(data) if data and data[:1] in (b"{", b"[") else data)
    return httpd, req, port, team_mod, mcp_server


def rpc(req, tok, method, params=None, mid=1):
    st, h, body = req("POST", "/mcp", {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
                      {"Authorization": f"Bearer {tok}"})
    assert st == 200, body
    return body


def tool(req, tok, name, args):
    r = rpc(req, tok, "tools/call", {"name": name, "arguments": args})["result"]
    return r, (r["content"][0]["text"] if r.get("isError") else json.loads(r["content"][0]["text"]))


def test_local_mcp_needs_a_token_and_designs_end_to_end(tmp_path, monkeypatch):
    httpd, req, port, _, _ = _serve(tmp_path, monkeypatch)
    try:
        assert req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize"})[0] == 401
        st, _, made = req("POST", "/api/mcp/tokens", {"name": "Cursor"}, {"Origin": f"http://127.0.0.1:{port}"})
        assert st == 200 and made["token"].startswith("fsm_") and made["url"].endswith("/mcp")
        tok = made["token"]
        assert req("POST", "/api/mcp/tokens", {"name": "x"}, {"Origin": "https://evil.example"})[0] == 403
        init = rpc(req, tok, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        assert init["serverInfo"]["name"] == "flynn-studio" and init["capabilities"]["tools"]
        names = {t["name"] for t in rpc(req, tok, "tools/list")["result"]["tools"]}
        assert {"studio_create_run", "studio_write_file", "studio_render", "flynn_ask"} <= names
        _, run = tool(req, tok, "studio_create_run", {"title": "Bill split", "type": "ui", "html": "<html><body><h1>Hi</h1></body></html>"})
        rid = run["run"]
        r, w = tool(req, tok, "studio_edit_file", {"run": rid, "path": "page/index.html", "old": "Hi", "new": "Split the bill"})
        assert not r["isError"]
        _, f = tool(req, tok, "studio_read_file", {"run": rid, "path": "page/index.html"})
        assert "Split the bill" in f["content"]
        r, _ = tool(req, tok, "studio_write_file", {"run": rid, "path": "../../etc/x.html", "content": "x"})
        assert r["isError"]
        r, _ = tool(req, tok, "studio_write_file", {"run": rid, "path": "page/x.py", "content": "x"})
        assert r["isError"]
        _, opt = tool(req, tok, "studio_add_option", {"run": rid, "name": "b"})
        _, got = tool(req, tok, "studio_get_run", {"run": rid})
        assert got["options"] == ["main", "b"]
        _, runs = tool(req, tok, "studio_list_runs", {})
        assert [r["id"] for r in runs["runs"]] == [rid]
        assert req("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Authorization": f"Bearer {tok}"})[0] == 202
    finally:
        httpd.shutdown()


def test_team_tokens_belong_to_one_person(tmp_path, monkeypatch):
    httpd, req, port, team, mcp_server = _serve(tmp_path, monkeypatch, team=True)
    try:
        a = team.match({"sub": "a", "sso_sub": "sso-a", "email": "a@example.com"})
        b = team.match({"sub": "b", "sso_sub": "sso-b", "email": "b@example.com"})
        ta, tb = mcp_server.issue(a["uid"], "A's Claude"), mcp_server.issue(b["uid"], "B's Cursor")
        _, run = tool(req, ta, "studio_create_run", {"title": "A only", "type": "ui", "html": "<html></html>"})
        _, runs_b = tool(req, tb, "studio_list_runs", {})
        assert runs_b["runs"] == []
        r, _ = tool(req, tb, "studio_read_file", {"run": run["run"], "path": "page/index.html"})
        assert r["isError"]
        assert req("GET", "/api/runs", headers={"Authorization": f"Bearer {ta}"})[0] == 403     # /mcp only
        assert req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 401
        assert mcp_server.revoke(a["uid"], mcp_server.tokens_of(a["uid"])[0]["id"])
        assert req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Authorization": f"Bearer {ta}"})[0] == 401
    finally:
        httpd.shutdown()
