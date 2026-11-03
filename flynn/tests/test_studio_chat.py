import importlib
import json
import stat
import sys
import time
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
open(os.environ["FAKE_ARGS_OUT"], "a").write(json.dumps(args) + "\n")
prompt = args[args.index("-p") + 1]
sid = args[args.index("--resume") + 1] if "--resume" in args else "sess-1"
def out(d): print(json.dumps(d), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid})
if "SLOW" in prompt:
    time.sleep(30)
if "MKRUN" in prompt:
    os.makedirs(os.path.join(os.environ["FLYNN_RUNS_DIR"], "2026-10-20-new-run", "page"), exist_ok=True)
    open(os.path.join(os.environ["FLYNN_RUNS_DIR"], "2026-10-20-new-run", "run.json"), "w").write('{"run_id": "2026-10-20-new-run", "request": "x", "system": ""}')
    open(os.path.join(os.environ["FLYNN_RUNS_DIR"], "2026-10-20-new-run", "page", "index.html"), "w").write("<html></html>")
out({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "سلام "}}})
cost = 3.0 if "BUDGET" in prompt else 0.02
ok = "BUDGET" not in prompt
out({"type": "result", "subtype": "success" if ok else "error_max_budget_usd", "is_error": not ok, "session_id": sid,
     "result": "سلام", "total_cost_usd": cost, "permission_denials": []})
'''


@pytest.fixture
def chat(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FLYNN_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("FAKE_ARGS_OUT", str(tmp_path / "args.jsonl"))
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_STUDIO_CHATS", str(tmp_path / "chats"))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "runs").mkdir()
    import chat as mod
    mod = importlib.reload(mod)
    mod.save_settings(engine="cli")
    yield mod
    mod.shutdown()


def wait_idle(conv, timeout=10):
    t = time.time()
    while conv.running and time.time() - t < timeout:
        time.sleep(0.05)
    assert not conv.running


def args_of(tmp_path):
    return [json.loads(l) for l in (tmp_path / "args.jsonl").read_text().splitlines()]


def test_keys_are_validated(chat):
    assert chat.valid_key("home") and chat.valid_key("run:2026-10-20-x")
    for bad in ("", "run:", "run:../x", "other", "run:a/b"):
        assert not chat.valid_key(bad)
    with pytest.raises(KeyError):
        chat.get("run:../x")


def test_turn_streams_numbers_and_resumes(chat, tmp_path):
    c = chat.get("home")
    assert c.start_turn("hello", effort="quick")
    wait_idle(c)
    kinds = [e["t"] for e in c.events]
    assert kinds[0] == "user" and "delta" in kinds and kinds[-1] == "turn_end"
    assert c.session == "sess-1"
    lines = (Path(chat.chats_root()) / "home" / "events.jsonl").read_text().splitlines()
    assert len(lines) == len(c.events)
    c.start_turn("again")
    wait_idle(c)
    a = args_of(tmp_path)
    assert "--resume" not in a[0] and a[1][a[1].index("--resume") + 1] == "sess-1"
    assert a[0][a[0].index("--model") + 1] == "sonnet" and a[0][a[0].index("--effort") + 1] == "low"
    assert "mcp__studio" in a[0]
    # the long instructions go only with the first turn of a session
    sys0 = a[0][a[0].index("--append-system-prompt") + 1]
    sys1 = a[1][a[1].index("--append-system-prompt") + 1]
    assert "flynn Studio" in sys0 and len(sys1) < len(sys0)
    assert "<studio_context>" in a[1][a[1].index("-p") + 1]
    log = (Path(chat.flynn_home()) / "logs" / "studio_chat.log").read_text()
    assert "home turn start engine=cli" in log and "home turn end" in log


def test_second_send_while_running_is_refused(chat):
    c = chat.get("home")
    assert c.start_turn("SLOW")
    time.sleep(0.3)
    assert c.start_turn("x") is False
    c.stop()


def test_stop_marks_turn_interrupted(chat):
    c = chat.get("home")
    c.start_turn("SLOW")
    time.sleep(0.3)
    c.stop()
    wait_idle(c, timeout=5)
    assert c.events[-1]["t"] == "turn_end" and c.events[-1].get("interrupted")
    assert not any(e["t"] == "error" for e in c.events)          # a stop the user asked for is not shown as a failure


def test_forced_stop_after_grace(chat, monkeypatch):
    monkeypatch.setattr(chat, "STOP_GRACE", 0.5)
    c = chat.get("home")
    c.start_turn("SLOW")
    time.sleep(0.3)
    monkeypatch.setattr(c.engine, "stop", lambda: None)   # an engine that ignores Stop (silent tool call)
    c.stop()
    wait_idle(c, timeout=5)
    assert c.events[-1]["t"] == "turn_end" and c.events[-1].get("interrupted")


def test_repair_closes_cut_turn(chat):
    d = Path(chat.chats_root()) / "home"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps({"t": "user", "text": "x"}) + "\n")
    c = chat.get("home")
    assert c.events[-1] == {**c.events[-1], "t": "turn_end", "interrupted": True}


def test_budget_hit_shows_effort_card(chat):
    c = chat.get("home")
    c.start_turn("BUDGET", effort="balanced")
    wait_idle(c)
    cards = [e["card"] for e in c.events if e["t"] == "card"]
    assert any(k["kind"] == "effort_cap" and k["next"] == "thorough" for k in cards)


def test_home_conversation_moves_into_the_new_run(chat):
    c = chat.get("home")
    c.start_turn("MKRUN make a release note")
    wait_idle(c)
    r = chat.get("run:2026-10-20-new-run")
    kinds = [e["t"] for e in r.events]
    assert kinds[0] == "user" and "delta" in kinds and "linked" in kinds      # the whole thread is in the run's chat
    assert r.session == "sess-1"
    assert [e["t"] for e in c.events] == ["moved"] and c.events[0]["run"] == "2026-10-20-new-run"
    h = c.history()[0]
    assert h["run"] == "2026-10-20-new-run" and h["title"].startswith("MKRUN")   # Home's history links to the run
    assert c.session is None


def test_old_runs_are_never_claimed(chat, tmp_path):
    import os
    old = Path(os.environ["FLYNN_RUNS_DIR"]) / "2026-09-30-old"
    (old / "page").mkdir(parents=True)
    (old / "page" / "index.html").write_text("<html></html>")
    c = chat.get("home")
    c._announce_runs(set(), since=time.time() + 60)       # the folder is older than the turn
    assert not any(e["t"] in ("moved", "run_created") for e in c.events)


def test_new_chat_archives_and_reopens(chat):
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    hid = c.new_chat()
    assert c.events == [] and c.session is None
    assert c.history()[0]["id"] == hid
    c.open_chat(hid)
    assert c.session == "sess-1" and c.events[0]["t"] == "user"


def test_cost_summary(chat):
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    s = c.cost_summary()
    assert s["conversation"] == pytest.approx(0.02) and s["today"] >= 0.02


def test_feature_switch_and_settings(chat):
    assert chat.feature("studio_chat") is True
    chat.set_feature("studio_chat", False)
    assert chat.feature("studio_chat") is False
    s = chat.save_settings(caps={"quick": 0.5})
    assert s["caps"]["quick"] == 0.5 and chat.cap("quick") == 0.5 and chat.cap("max") is None


import threading as _th
import urllib.error
import urllib.request

ROOT = FLYNN


@pytest.fixture
def server(chat, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("studio", ROOT / "adapters" / "studio" / "studio.py")
    studio = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(studio)
    run = tmp_path / "runs" / "2026-10-20-demo"
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text("<html><body><h1>x</h1></body></html>")
    srv = studio.serve(port=0, open_browser=False, root=tmp_path / "runs")
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    chat.STUDIO_PORT = port
    yield f"http://127.0.0.1:{port}", chat
    srv.shutdown()


def req(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data, {"Content-Type": "application/json", **(headers or {})},
                               method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


import importlib.util  # noqa: E402


def test_send_state_and_409(server):
    base, chat = server
    assert req(base, "/api/chat/send", {"c": "run:2026-10-20-demo", "text": "SLOW"})[0] == 200
    time.sleep(0.3)
    code, j = req(base, "/api/chat/send", {"c": "run:2026-10-20-demo", "text": "x"})
    assert code == 409 and "still working" in j["error"]
    assert req(base, "/api/chat/state?c=run:2026-10-20-demo")[1]["running"] is True
    req(base, "/api/chat/stop", {"c": "run:2026-10-20-demo"})


def test_origin_and_token_are_enforced(server):
    base, chat = server
    assert req(base, "/api/chat/send", {"c": "home", "text": "x"}, {"Origin": "https://evil.example"})[0] == 403
    assert req(base, "/api/chat/tool", {"c": "home", "name": "studio_view", "args": {}})[0] == 403
    code, j = req(base, "/api/chat/tool", {"c": "home", "name": "studio_view", "args": {}}, {"X-Studio-Token": chat.TOOL_TOKEN})
    assert code == 200 and "result" in j
    assert req(base, "/api/chat/state?c=run:../etc")[0] == 404


def test_sse_resumes_from_since(server):
    base, chat = server
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    n = len(c.events)
    with urllib.request.urlopen(base + f"/api/chat/events?c=home&since={n - 1}", timeout=5) as f:
        assert f.readline().decode() == "event: hello\n"
        f.readline(); f.readline()
        first = f.readline().decode()
        data = f.readline().decode()
    assert first == f"id: {n - 1}\n" and '"turn_end"' in data


def test_upload_and_allow_rules(server, tmp_path):
    import base64
    base, chat = server
    code, up = req(base, "/api/chat/upload", {"c": "home", "name": "../../a.png", "type": "image/png",
                                               "data": base64.b64encode(b"\x89PNG").decode()})
    assert code == 200 and Path(up["path"]).parent == chat.get("home").dir / "uploads" and ".." not in up["name"]
    assert req(base, "/api/chat/allow", {"c": "home", "rules": ["Write(/Users/x/Documents/code/**)"]})[0] == 400
    assert req(base, "/api/chat/allow", {"c": "home", "rules": ["Bash(*)"]})[0] == 400      # flynn never asked for it
    chat.get("home").emit({"t": "denied", "tools": [{"name": "Bash", "input": {}, "rule": "Bash(ls:*)"}]})
    assert req(base, "/api/chat/allow", {"c": "home", "rules": ["Bash(ls:*)"]})[0] == 200


def test_settings_and_health(server):
    base, chat = server
    code, s = req(base, "/api/settings", {"engine": "cli", "caps": {"quick": 0.5}, "features": {"studio_chat": True}})
    assert code == 200 and s["settings"]["caps"]["quick"] == 0.5
    h = req(base, "/api/health")[1]
    ids = {c["id"] for c in h["checks"]}
    assert {"sdk", "claude", "auth", "agent", "runs", "engine"} <= ids
    assert all(c["status"] in ("ok", "warn", "fail") for c in h["checks"])
    assert "token" not in json.dumps(h).lower() or "never" in json.dumps(h).lower()


def test_chat_off_returns_503(server):
    base, chat = server
    chat.set_feature("studio_chat", False)
    assert req(base, "/api/chat/send", {"c": "home", "text": "x"})[0] == 503


def test_websocket_streams_events(server):
    base, chat = server
    sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
    import wsproto
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    ws = wsproto.connect(base.replace("http", "ws") + "/api/chat/ws?c=home&since=0",
                         headers={"Origin": base})
    assert ws.recv_json() == {"type": "hello", "epoch": c.epoch}
    m = ws.recv_json()
    assert m == {"type": "event", "id": 0, "ev": c.events[0]}
    ws.close()


def test_bad_view_never_wedges_a_conversation(server):
    base, chat = server
    for bad in ("x", {"pointed": "x"}, {"run": {"a": 1}}, [1]):
        assert req(base, "/api/chat/view", {"c": "home", "view": bad})[0] == 200
        code, _ = req(base, "/api/chat/send", {"c": "home", "text": "hello", "view": bad})
        assert code == 200
        wait_idle(chat.get("home"))
    assert chat.get("home").running is False


def test_caps_and_effort_are_validated(chat):
    with pytest.raises(ValueError):
        chat.save_settings(caps={"quick": "abc"})
    with pytest.raises(ValueError):
        chat.save_settings(caps={"quick": -1})
    assert chat.save_settings(caps={"thorough": "5"})["caps"]["thorough"] == 5.0
    with pytest.raises(ValueError):
        chat.save_settings(effort="huge")


def test_new_chat_on_empty_and_epoch(chat):
    c = chat.get("home")
    assert c.new_chat() == "" and c.history() == []
    c.start_turn("hello")
    wait_idle(c)
    e0 = c.epoch
    hid = c.new_chat()
    assert c.epoch == e0 + 1 and len(hid.split("-")) == 3
    c.open_chat(hid)
    assert c.epoch == e0 + 2 and c.events and c.session == "sess-1"


def test_tool_cards_need_a_running_turn(chat):
    c = chat.get("home")
    with pytest.raises(RuntimeError):
        c.tool_emit({"t": "card", "card": {"kind": "result", "summary": "late"}})


def test_reads_need_studios_own_host(server):
    base, chat = server
    assert req(base, "/api/chat/state?c=home", headers={"Host": "evil.example:8770"})[0] == 403
    assert req(base, "/api/chat/state?c=home&since=abc")[0] == 200


def test_sse_resets_after_new_chat(server):
    base, chat = server
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    with urllib.request.urlopen(base + f"/api/chat/events?c=home&since=0&e={c.epoch + 5}", timeout=5) as f:
        assert f.readline().decode() == "event: hello\n"
        f.readline(); f.readline()
        assert f.readline().decode() == "id: 0\n"           # a stale epoch starts the tab over from the first event


def test_sdk_stop_and_kill_reach_the_client(chat):
    import asyncio
    e = chat.StudioSdkEngine()
    calls = []

    class Client:
        async def interrupt(self):
            calls.append("interrupt")

        async def disconnect(self):
            calls.append("disconnect")
    loop = asyncio.new_event_loop()
    _th.Thread(target=loop.run_forever, daemon=True).start()
    try:
        e._loop, e._client = loop, Client()
        e.stop()
        e.kill()
        time.sleep(0.2)
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert calls == ["interrupt", "disconnect"] and e._stop.is_set()


def test_selected_elements_reach_the_context(chat):
    v = chat.clean_view({"run": "r", "pointed": [
        {"selector": "main > h1", "text": "عنوان", "page": "page/index.html", "option": "main",
         "html": "<h1>عنوان</h1>", "style": {"font-size": "32px", "color": "rgb(0, 0, 0)"}},
        {"selector": "table", "page": "options/b/page/index.html"}, "junk", {"text": "no selector"}] + [{"selector": f"p{i}"} for i in range(10)]})
    assert [p["selector"] for p in v["pointed"]][:2] == ["main > h1", "table"] and len(v["pointed"]) <= 8
    assert v["pointed"][0]["style"]["font-size"] == "32px" and v["pointed"][0]["html"] == "<h1>عنوان</h1>"
    c = chat.get("run:r")
    assert '"main > h1"' in chat.context_block(c, v, "quick")
    assert chat.clean_view({"pointed": {"selector": "h2"}})["pointed"] == [{"selector": "h2"}]


# ---------------------------------------------------------------- the conversation's design system
RUN = "run:2026-10-20-demo"
RULE_ACME = ("Use only the Acme UI design system (acme-ui) for everything you design in this conversation: its "
             "components, tokens, patterns and guide. Do not switch systems; if the request needs something the "
             "system lacks, say so and propose an addition.")
RULE_NONE = ("Use no design system in this conversation: no library components or document theme; build plain, "
             "well-crafted HTML that follows your rubric.")


@pytest.fixture
def known(monkeypatch):
    """Two neutral sample systems: a product UI library and a document theme."""
    import systems
    monkeypatch.setattr(systems, "_library_ids", lambda: ["acme-ui"])
    monkeypatch.setattr(systems, "_theme_ids", lambda: ["acme-paper"])
    monkeypatch.setattr(systems, "_rows", lambda: {})


def appended(tmp_path, n=-1):
    a = args_of(tmp_path)[n]
    return a[a.index("--append-system-prompt") + 1]


def design_state(base, key="home"):
    return req(base, f"/api/chat/design_system?c={key}")[1]


def test_design_default_is_flynn_chooses(server, known, tmp_path):
    base, chat = server
    d = design_state(base)
    assert d["choice"] == "auto" and d["locked"] is False and d["applies"] is True
    groups = [(o["group"], o["id"]) for o in d["options"]]
    assert groups == [("", "auto"), ("Product UI", "acme-ui"), ("Documents", "acme-paper"), ("", "none")]
    assert [o["name"] for o in d["options"]] == ["Flynn chooses", "Acme UI", "Acme Paper", "No design system"]
    assert req(base, "/api/chat/state?c=home")[1]["design"]["choice"] == "auto"
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    assert "design system" not in appended(tmp_path)          # nothing extra today
    assert "Use no design system" not in appended(tmp_path)


def test_design_set_before_first_message_and_told_every_turn(server, known, tmp_path):
    base, chat = server
    code, d = req(base, "/api/chat/design_system", {"c": "home", "system": "acme-ui"})
    assert code == 200 and d["choice"] == "acme-ui" and d["locked"] is False
    assert design_state(base)["choice"] == "acme-ui"
    assert json.loads((chat.get("home").dir / "state.json").read_text())["design_system"] == "acme-ui"
    assert req(base, "/api/chat/design_system", {"c": "home", "system": "nope"})[0] == 400
    assert req(base, "/api/chat/design_system", {"c": "home", "system": "auto"})[1]["choice"] == "auto"
    # the first message's request carries the choice
    assert req(base, "/api/chat/send", {"c": "home", "text": "hi", "design_system": "acme-paper"})[0] == 200
    c = chat.get("home")
    wait_idle(c)
    assert c.design_choice() == "acme-paper" and c.design_info()["locked"] is True
    c.start_turn("again")
    wait_idle(c)
    first, second = appended(tmp_path, 0), appended(tmp_path, 1)
    paper = RULE_ACME.replace("Acme UI design system (acme-ui)", "Acme Paper design system (acme-paper)")
    assert first.endswith(paper) and "flynn Studio" in first
    assert second.endswith(paper) and len(second) < len(first)      # a resumed session still hears the rule
    # it survives a reload (a new Conversation object reads the state file)
    assert chat.Conversation("home").design_choice() == "acme-paper"


def test_design_refused_after_the_first_message(server, known, tmp_path):
    base, chat = server
    req(base, "/api/chat/design_system", {"c": "home", "system": "acme-ui"})
    assert req(base, "/api/chat/send", {"c": "home", "text": "hi"})[0] == 200
    c = chat.get("home")
    wait_idle(c)
    code, j = req(base, "/api/chat/design_system", {"c": "home", "system": "none"})
    assert code == 409 and "Start a new conversation" in j["error"]
    n = len(c.events)
    code, _ = req(base, "/api/chat/send", {"c": "home", "text": "again", "design_system": "acme-paper"})
    assert code == 409 and len(c.events) == n                       # no turn started either
    assert req(base, "/api/chat/send", {"c": "home", "text": "ok", "design_system": "acme-ui"})[0] == 200   # same value
    wait_idle(c)
    assert c.design_choice() == "acme-ui"
    assert json.loads((c.dir / "state.json").read_text())["design_system"] == "acme-ui"
    d = design_state(base)
    assert d["locked"] is True and "Start a new conversation" in d["reason"]
    # a new conversation is free again
    c.new_chat()
    assert design_state(base)["choice"] == "auto" and design_state(base)["locked"] is False


def test_run_conversation_follows_and_locks_the_runs_system(server, known, tmp_path):
    base, chat = server
    run = tmp_path / "runs" / "2026-10-20-demo"
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "system": "acme-ui"}))
    d = design_state(base, RUN)
    assert d["choice"] == "acme-ui" and d["locked"] is True and "run" in d["reason"]
    code, _ = req(base, "/api/chat/design_system", {"c": RUN, "system": "none"})
    assert code == 409 and chat.get(RUN).design_choice() == "acme-ui"
    assert req(base, "/api/chat/send", {"c": RUN, "text": "hi", "design_system": "none"})[0] == 409
    c = chat.get(RUN)
    c.start_turn("hello")
    wait_idle(c)
    assert appended(tmp_path).endswith(RULE_ACME)
    # a run that names none, and a run that names only a theme
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "design_system": "none", "system": ""}))
    assert design_state(base, RUN)["choice"] == "none"
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "theme": "acme-paper"}))
    assert design_state(base, RUN)["choice"] == "acme-paper"
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "system": ""}))
    assert design_state(base, RUN)["choice"] == "auto"            # it names nothing: the run doesn't pin it


def test_none_rule_and_untouched_conversations(server, known, tmp_path):
    base, chat = server
    req(base, "/api/chat/design_system", {"c": "home", "system": "none"})
    c = chat.get("home")
    c.start_turn("hello")
    wait_idle(c)
    assert appended(tmp_path).endswith(RULE_NONE)
    # a system-maintenance conversation is not a design conversation: no selector, no rule
    s = chat.get("system:acme-ui")
    assert s.design_info()["applies"] is False and chat.design_rule(s) == ""
    assert req(base, "/api/chat/design_system", {"c": "system:acme-ui", "system": "none"})[0] == 409
    assert design_state(base, "system:acme-ui")["options"] == []


@pytest.mark.parametrize("choice,expect", [("acme-ui", {"system": "acme-ui"}),
                                           ("acme-paper", {"system": "acme-paper", "theme": "acme-paper"}),
                                           ("none", {"system": "", "design_system": "none"})])
def test_new_run_records_the_chosen_system(server, known, choice, expect):
    base, chat = server
    req(base, "/api/chat/design_system", {"c": "home", "system": choice})
    c = chat.get("home")
    c.start_turn("MKRUN make a page")
    wait_idle(c)
    rec = json.loads((Path(chat.runs_root()) / "2026-10-20-new-run" / "run.json").read_text())
    assert {k: rec.get(k) for k in expect} == expect
    r = chat.get("run:2026-10-20-new-run")                          # the thread moved; its choice is locked in
    assert r.design_choice() == choice and r.design_info()["locked"] is True


def test_flynn_chooses_leaves_run_json_alone(server, known):
    base, chat = server
    c = chat.get("home")
    c.start_turn("MKRUN make a page")
    wait_idle(c)
    rec = json.loads((Path(chat.runs_root()) / "2026-10-20-new-run" / "run.json").read_text())
    assert rec["system"] == "" and "theme" not in rec and "design_system" not in rec
    assert chat.get("run:2026-10-20-new-run").design_choice() == "auto"


def test_history_keeps_the_choice(chat, known):
    c = chat.get("home")
    c.set_design_system("acme-ui")
    c.start_turn("hello")
    wait_idle(c)
    hid = c.new_chat()
    assert c.design_choice() == "auto"
    c.open_chat(hid)
    assert c.design_choice() == "acme-ui" and c.design_info()["locked"] is True


def test_design_rule_reaches_every_engine(chat, known, tmp_path, monkeypatch):
    """The rule rides on Turn.system: the SDK appends it to its prompt, the remote engine sends it to the person's
    agent, and the model-API engine builds its prompt from it."""
    import api_engine
    c = chat.get("home")
    c.set_design_system("acme-ui")
    t = c._turn("hi")
    assert t.system.endswith(RULE_ACME)
    # model API: its own prompt, with the rule at the end
    assert api_engine._system_prompt(t).endswith(RULE_ACME) and "flynn_read" in api_engine._system_prompt(t)
    c.design = "none"
    assert api_engine._system_prompt(c._turn("hi")).endswith(RULE_NONE)
    c.design = "auto"
    plain = api_engine._system_prompt(c._turn("hi"))
    assert "Use only the" not in plain and "Use no design system" not in plain
    # Agent SDK: the options it starts the client with
    sdk = pytest.importorskip("claude_agent_sdk")
    c.design = "acme-ui"
    opts = chat.StudioSdkEngine().options(c._turn("hi"))
    assert opts.system_prompt["append"].endswith(RULE_ACME)
    # remote engine: the job sent to the person's computer
    agent_hub = importlib.import_module("agent_hub") if "agent_hub" in sys.modules else None
    if agent_hub is None:
        sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
        agent_hub = importlib.import_module("agent_hub")
    sent = []

    class Hub:
        turns, info, seen = {}, {}, {}
        def connected(self, email): return True
        def put(self, email, job):
            sent.append(job)
            self.turns[job["id"]]["done"].set()
    monkeypatch.setattr(agent_hub, "note_turn", lambda *a, **k: None)
    monkeypatch.setattr(agent_hub, "server_files", lambda p: {})
    tt = c._turn("hi")
    tt.user = "a@example.com"
    agent_hub.RemoteEngine(Hub()).run(tt, lambda ev: None)
    assert sent and sent[0]["turn"]["system"].endswith(RULE_ACME)


def test_mcp_create_run_takes_a_system(chat, known, tmp_path, monkeypatch):
    import design_tools
    ctx = type("C", (), {"me": None, "base_url": "", "public": ""})()
    monkeypatch.setattr(design_tools, "_url", lambda c, r: "u")
    out = design_tools.create_run(ctx, {"title": "Pick ui", "type": "ui", "system": "acme-ui"})
    rec = json.loads((Path(chat.runs_root()) / out["run"] / "run.json").read_text())
    assert rec["system"] == "acme-ui"
    out = design_tools.create_run(ctx, {"title": "Pick paper", "type": "ui", "system": "acme-paper"})
    rec = json.loads((Path(chat.runs_root()) / out["run"] / "run.json").read_text())
    assert rec["system"] == "acme-paper" and rec["theme"] == "acme-paper"
    out = design_tools.create_run(ctx, {"title": "Pick none", "type": "ui", "system": "none"})
    rec = json.loads((Path(chat.runs_root()) / out["run"] / "run.json").read_text())
    assert rec["system"] == "" and rec["design_system"] == "none"
    out = design_tools.create_run(ctx, {"title": "Pick nothing", "type": "ui"})
    rec = json.loads((Path(chat.runs_root()) / out["run"] / "run.json").read_text())
    assert rec["system"] == "" and "design_system" not in rec
