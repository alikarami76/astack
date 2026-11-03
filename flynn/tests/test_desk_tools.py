import json
import pathlib
import subprocess
import sys

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
sys.path.insert(0, str(FLYNN / "adapters/penpot"))
from test_panel_server import server, post, history  # noqa: E402,F401

import desk_tools  # noqa: E402
import prototype  # noqa: E402


def test_registry_formats():
    names = {t["name"] for t in desk_tools.schemas("mcp")}
    assert {"ask_user", "show_result", "prototype_outline", "prototype_propose", "propose_ds_change"} <= names
    api = desk_tools.schemas("api")
    assert all("input_schema" in t and t["input_schema"]["type"] == "object" for t in api)


def test_ask_user_tool_makes_a_card(server):
    mod, base, tmp = server
    r = post(base, "/api/tool", {"file": "t1", "name": "ask_user", "args": {"stage": "brainstorm", "questions": [
        {"q": "Who uses it?", "options": ["new sellers", "all sellers"], "recommended": "new sellers", "because": "the empty state"}]}})
    assert r["result"]["shown"]
    card = next(e for e in history(base, "t1")["events"] if e["t"] == "card")["card"]
    assert card["kind"] == "needs_alignment" and card["questions"][0]["options"] == ["new sellers", "all sellers"]
    assert card["questions"][0]["n"] == 1


def test_tool_errors_come_back_as_text(server):
    mod, base, tmp = server
    assert "needs questions" in post(base, "/api/tool", {"file": "t1", "name": "ask_user", "args": {}})["error"]
    assert "no flynn desk tool" in post(base, "/api/tool", {"file": "t1", "name": "nope", "args": {}})["error"]


def test_prototype_propose_and_reject(server, monkeypatch):
    mod, base, tmp = server
    monkeypatch.setattr(desk_tools, "PROTOS", tmp / "protos")
    bad = post(base, "/api/tool", {"file": "t2", "name": "prototype_propose", "args": {"page": "P", "links": [{"from": "a", "action": "navigate"}]}})
    assert "needs to" in bad["error"]
    ok = post(base, "/api/tool", {"file": "t2", "name": "prototype_propose", "args": {"page": "P", "flow": "Checkout", "start": "b1",
          "links": [{"from": "s1", "from_name": "Pay", "to": "b2", "to_name": "Checkout", "animation": "slide-left"}, {"from": "s2", "action": "back"}]}})
    pid = ok["result"]["proposed"]
    ev = next(e for e in history(base, "t2")["events"] if e["t"] == "prototype")
    assert ev["plan"]["links"] == ["Pay → Checkout · slide-left", "s2 goes back"]
    post(base, "/api/prototype/reject", {"file": "t2", "id": pid})
    assert json.loads((tmp / "protos" / f"{pid}.json").read_text())["status"] == "rejected"


def test_desk_mcp_speaks_mcp():
    p = subprocess.run([sys.executable, str(FLYNN / "adapters/penpot/panel/desk_mcp.py")], text=True, capture_output=True, timeout=20,
                       input="\n".join(json.dumps(m) for m in [
                           {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]) + "\n",
                       env={"FLYNN_DESK_PANEL": "http://127.0.0.1:1", "PATH": "/usr/bin:/bin"})
    out = [json.loads(l) for l in p.stdout.splitlines()]
    assert out[0]["result"]["serverInfo"]["name"] == "flynn-desk"
    assert any(t["name"] == "prototype_propose" for t in out[1]["result"]["tools"])


def test_cli_engine_passes_desk_tools(server):
    mod, base, tmp = server
    cmd = mod.channel("t3")._cmd("hi")
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])
    assert cfg["mcpServers"]["desk"]["env"]["FLYNN_DESK_FILE"] == "t3"
    assert "mcp__desk" in cmd[cmd.index("--allowedTools") + 1: cmd.index("--disallowedTools")]


def test_prototype_validate():
    assert prototype.validate({"page": "P", "flow": "F", "links": [{"from": "x", "to": "y"}]}) == ["a named flow needs start (the first board's id)"]
    assert prototype.validate({"page": "P", "links": [{"from": "x", "action": "url"}]}) == ["link 1: url needs the address"]


def test_commands_listing_and_prompt(server):
    import urllib.request
    mod, base, tmp = server
    names = [c["name"] for c in json.loads(urllib.request.urlopen(f"{base}/api/commands?file=c1").read())["commands"]]
    assert {"record", "figma", "prototype", "sync", "use", "library", "new", "context", "usage", "studio", "clu", "quorra", "attach"} <= set(names)
    assert "chart" not in names and "export" not in names          # letters only
    assert post(base, "/api/command", {"file": "c1", "name": "clu", "arg": "weekly discount share"})["ok"]
    from test_panel_server import wait_idle
    h = wait_idle(base, "c1")
    assert h["events"][0]["text"] == "/clu weekly discount share"
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    assert 'subagent_type "clu"' in args[args.index("-p") + 1]


def test_context_usage_settings(server, monkeypatch):
    mod, base, tmp = server
    post(base, "/api/command", {"file": "c2", "name": "usage"})
    post(base, "/api/command", {"file": "c2", "name": "context"})
    post(base, "/api/command", {"file": "c2", "name": "settings"})
    ev = history(base, "c2")["events"]
    assert [e["title"] for e in ev if e["t"] == "info"] == ["Usage", "Context"]
    st = next(e for e in ev if e["t"] == "settings")
    assert {o["id"] for o in st["options"]} == {"local", "cli", "api"}
    assert post(base, "/api/settings", {"engine": "cli"})["engine"] == "cli"


def test_upload_and_attach(server):
    import base64
    mod, base, tmp = server
    up = post(base, "/api/upload", {"file": "c3", "name": "brief.csv", "type": "text/csv", "data": "data:text/csv;base64," + base64.b64encode(b"a,b\n1,2\n").decode()})
    assert up["size"] == 8
    post(base, "/api/send", {"file": "c3", "text": "use this", "attachments": [up["id"], "../../etc/passwd"]})
    from test_panel_server import wait_idle
    ev = wait_idle(base, "c3")["events"]
    assert ev[0]["files"] == ["brief.csv"]
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    prompt = args[args.index("-p") + 1]
    assert "brief.csv" in prompt and "passwd" not in prompt


def test_propose_and_install_skill(server, monkeypatch):
    mod, base, tmp = server
    monkeypatch.setattr(desk_tools, "SKILLS", tmp / "skills")
    monkeypatch.setattr(desk_tools, "CLAUDE_SKILLS", tmp / "claude-skills")
    monkeypatch.setattr(desk_tools, "DRAFTS", tmp / "drafts")
    monkeypatch.setattr(desk_tools, "FLYNN", tmp)
    bad = post(base, "/api/tool", {"file": "s1", "name": "propose_skill", "args": {"name": "flynn-core", "description": "x", "body": "y"}})
    assert "core skill" in bad["error"]
    bad = post(base, "/api/tool", {"file": "s1", "name": "propose_skill", "args": {"name": "flynn-xy", "description": "x", "body": "y", "files": {"../evil": "z"}}})
    assert "bad file" in bad["error"]
    r = post(base, "/api/tool", {"file": "s1", "name": "propose_skill", "args": {
        "name": "flynn-price-tags", "description": "Review price tag designs. Use when a design shows prices.", "body": "1. Check the tags.",
        "files": {"references/rules.md": "rules"}}})
    sid = r["result"]["proposed"]
    assert not (tmp / "skills" / "flynn-price-tags").exists()          # nothing before approval
    post(base, "/api/skill/approve", {"file": "s1", "id": sid})
    md = (tmp / "skills" / "flynn-price-tags" / "SKILL.md").read_text()
    assert md.startswith("---\nname: flynn-price-tags\n") and "1. Check the tags." in md
    assert (tmp / "skills" / "flynn-price-tags" / "references/rules.md").exists()
    assert (tmp / "claude-skills" / "flynn-price-tags").is_symlink()
    assert any(e["t"] == "skill-result" and e["state"] == "installed" for e in history(base, "s1")["events"])
