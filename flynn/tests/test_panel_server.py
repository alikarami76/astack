import importlib.util
import json
import os
import pathlib
import stat
import threading
import time
import urllib.error
import urllib.request

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
open(os.environ["FAKE_ARGS_OUT"], "a").write(json.dumps(args) + "\n")
prompt = args[args.index("-p") + 1]
sid = args[args.index("--resume") + 1] if "--resume" in args else "sess-1"
def out(d): print(json.dumps(d), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid})
out({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "پاسخ: "}}})
if prompt.startswith("ALIGNED"):
    text = "RESULT\nrun: /r\nanswer: Done.\n"
else:
    text = "NEEDS_ALIGNMENT\nrun: /r\nstage: brainstorm\nbrief: x\nquestions:\n  1. Who? — recommended: sellers — because empty list\n"
out({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
out({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_stop"}})
denials = [{"tool_name": "Bash", "tool_input": {"command": "rm x"}}] if "deny" in prompt else []
out({"type": "result", "subtype": "success", "session_id": sid, "result": "پاسخ: " + text, "total_cost_usd": 0.01, "permission_denials": denials})
'''


@pytest.fixture
def server(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FLYNN_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("FAKE_ARGS_OUT", str(tmp_path / "args.jsonl"))
    monkeypatch.setenv("FLYNN_PANEL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_PANEL_CONTEXT", "0")
    monkeypatch.setenv("FLYNN_ENGINE", "cli")
    spec = importlib.util.spec_from_file_location("panel_server", FLYNN / "adapters/penpot/panel/panel_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = mod.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{srv.server_port}", tmp_path
    srv.shutdown()


def post(base, path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def history(base, file_id):
    with urllib.request.urlopen(f"{base}/api/history?file={file_id}", timeout=10) as r:
        return json.loads(r.read())


def wait_idle(base, file_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        h = history(base, file_id)
        if not h["running"] and any(e["t"] == "turn_end" for e in h["events"]):
            return h
        time.sleep(0.1)
    raise AssertionError("turn did not finish")


def test_send_streams_and_parses_card(server):
    mod, base, tmp = server
    assert post(base, "/api/send", {"file": "f1", "text": "یک empty state بساز", "file_name": "M0 trial"})["ok"]
    h = wait_idle(base, "f1")
    kinds = [e["t"] for e in h["events"]]
    assert kinds[0] == "user" and "delta" in kinds and "card" in kinds and kinds[-1] == "turn_end"
    card = next(e for e in h["events"] if e["t"] == "card")["card"]
    assert card["kind"] == "needs_alignment" and card["questions"][0]["recommended"] == "sellers"
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[0])
    assert args[args.index("--agent") + 1] == "flynn"
    assert "--resume" not in args
    assert "--output-format" in args and "stream-json" in args
    sp = args[args.index("--append-system-prompt") + 1]
    assert "M0 trial" in sp and "NEEDS_ALIGNMENT" in sp
    assert any("/code/" in a for a in args[args.index("--disallowedTools") + 1:])


def test_second_turn_resumes_session_and_answer_builds_aligned(server):
    mod, base, tmp = server
    post(base, "/api/send", {"file": "f2", "text": "hi"})
    wait_idle(base, "f2")
    post(base, "/api/answer", {"file": "f2", "answers": [{"n": 1, "answer": "new sellers"}], "note": ""})
    end = time.time() + 10
    while time.time() < end and sum(e["t"] == "turn_end" for e in history(base, "f2")["events"]) < 2:
        time.sleep(0.1)
    lines = (tmp / "args.jsonl").read_text().splitlines()
    second = json.loads(lines[1])
    assert second[second.index("--resume") + 1] == "sess-1"
    assert second[second.index("-p") + 1].startswith("ALIGNED\n1. new sellers")


def test_denials_and_allow(server):
    mod, base, tmp = server
    post(base, "/api/send", {"file": "f3", "text": "please deny"})
    h = wait_idle(base, "f3")
    denied = next(e for e in h["events"] if e["t"] == "denied")
    assert denied["tools"][0]["rule"] == "Bash(rm x)"
    post(base, "/api/allow", {"file": "f3", "rules": ["Bash(rm x)"]})
    end = time.time() + 10
    while time.time() < end and len((tmp / "args.jsonl").read_text().splitlines()) < 2:
        time.sleep(0.1)
    second = json.loads((tmp / "args.jsonl").read_text().splitlines()[1])
    allowed = second[second.index("--allowedTools") + 1: second.index("--disallowedTools")]
    assert "Bash(rm x)" in allowed


def test_history_survives_restart(server):
    mod, base, tmp = server
    post(base, "/api/send", {"file": "f4", "text": "hello"})
    wait_idle(base, "f4")
    mod.CHANNELS.clear()
    h = history(base, "f4")
    assert {k: v for k, v in h["events"][0].items() if k != "at"} == {"t": "user", "text": "hello"}
    assert h["session"] == "sess-1"


def test_feature_switch_off(server, tmp_path):
    mod, base, tmp = server
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    (home / "features.json").write_text(json.dumps({"panel_chat": False}))
    req = urllib.request.Request(base + "/api/send", json.dumps({"file": "f5", "text": "x"}).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 503


def test_bad_file_id_refused(server):
    mod, base, tmp = server
    req = urllib.request.Request(base + "/api/send", json.dumps({"file": "../x", "text": "x"}).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def _wait_for(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _fake_run(tmp, name="2026-10-04-test-run"):
    run = tmp / "runs" / name
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text("<html></html>")
    return run


def test_auto_push_after_turn_in_penpot_mode(server, monkeypatch):
    mod, base, tmp = server
    run = _fake_run(tmp)
    monkeypatch.setattr(mod, "RUNS_DIR", tmp / "runs")
    calls = []
    monkeypatch.setattr(mod, "push_run_fn", lambda r, file_id, team=None, progress=None: calls.append((r.name, file_id)) or
                        {"pageName": "flynn · " + r.name, "link": "http://x/#/ws", "boards": {"A · t": {"built": 3, "total": 3}}, "errors": [], "kept_edited": {}})
    post(base, "/api/send", {"file": "p1", "text": f"make it in runs/{run.name} please"})
    assert _wait_for(lambda: any(e["t"] == "penpot" and e["state"] == "done" for e in history(base, "p1")["events"]))
    assert calls == [(run.name, "p1")]
    ev = [e for e in history(base, "p1")["events"] if e["t"] in ("run", "penpot")]
    assert ev[0] == {"t": "run", "run": run.name, "studio": f"http://127.0.0.1:8770/run/{run.name}"}
    # Nothing changed: the next turn does not push again.
    post(base, "/api/send", {"file": "p1", "text": "thanks"})
    _wait_for(lambda: sum(e["t"] == "turn_end" for e in history(base, "p1")["events"]) >= 2)
    time.sleep(0.5)
    assert len(calls) == 1


def test_studio_mode_skips_auto_push_but_manual_push_works(server, monkeypatch):
    mod, base, tmp = server
    run = _fake_run(tmp, "2026-10-04-studio-run")
    monkeypatch.setattr(mod, "RUNS_DIR", tmp / "runs")
    calls = []
    monkeypatch.setattr(mod, "push_run_fn", lambda r, file_id, team=None, progress=None: calls.append(r.name) or
                        {"pageName": "p", "link": None, "boards": {}, "errors": [], "kept_edited": {}})
    assert post(base, "/api/mode", {"file": "p2", "mode": "studio"})["mode"] == "studio"
    post(base, "/api/send", {"file": "p2", "text": f"see runs/{run.name}"})
    wait_idle(base, "p2")
    time.sleep(0.5)
    assert calls == []
    assert history(base, "p2")["mode"] == "studio"
    post(base, "/api/push", {"file": "p2"})
    assert _wait_for(lambda: calls == [run.name])


def test_push_error_is_shown(server, monkeypatch):
    mod, base, tmp = server
    run = _fake_run(tmp, "2026-10-04-err-run")
    monkeypatch.setattr(mod, "RUNS_DIR", tmp / "runs")

    def boom(r, file_id, team=None, progress=None):
        raise mod.PenpotError("open that file")
    monkeypatch.setattr(mod, "push_run_fn", boom)
    post(base, "/api/send", {"file": "p3", "text": f"runs/{run.name}"})
    assert _wait_for(lambda: any(e["t"] == "penpot" and e["state"] == "error" and "open that file" in e["text"]
                                 for e in history(base, "p3")["events"]))


def test_penpot_context_added_to_prompt_but_not_shown(server, monkeypatch):
    mod, base, tmp = server
    monkeypatch.setattr(mod, "context_fn", lambda ch: {"page": "flynn · 2026-10-04-x", "selection": [{"name": "Button", "type": "rect"}],
                                                       "selection_png": "/tmp/sel.png"})
    post(base, "/api/send", {"file": "c1", "text": "make this bigger"})
    h = wait_idle(base, "c1")
    assert {k: v for k, v in h["events"][0].items() if k != "at"} == {"t": "user", "text": "make this bigger"}
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    prompt = args[args.index("-p") + 1]
    assert prompt.startswith("make this bigger") and "<penpot_context>" in prompt and "Button" in prompt and "/tmp/sel.png" in prompt
    ctx = next(e for e in h["events"] if e["t"] == "context")
    assert ctx["page"] == "flynn · 2026-10-04-x" and ctx["selected"] == 1


def test_context_failure_does_not_block(server, monkeypatch):
    mod, base, tmp = server
    def boom(ch):
        raise RuntimeError("no tab")
    monkeypatch.setattr(mod, "context_fn", boom)
    post(base, "/api/send", {"file": "c2", "text": "hello"})
    wait_idle(base, "c2")
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    assert args[args.index("-p") + 1] == "hello"


def test_figma_import_validates_the_link_and_briefs_flynn(server):
    mod, base, tmp = server
    req = urllib.request.Request(base + "/api/figma/import", json.dumps({"file": "fg1", "url": "https://example.com/x"}).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400
    url = "https://www.figma.com/design/AbC123/Shop?node-id=0-1&t=z"
    assert post(base, "/api/figma/import", {"file": "fg1", "url": url, "page_name": "Checkout"})["ok"]
    h = wait_idle(base, "fg1")
    assert h["events"][0]["text"].startswith("Import from Figma: " + url)
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    prompt = args[args.index("-p") + 1]
    assert "file key AbC123, node 0:1" in prompt and "CANVAS" in prompt and "import_page.py" in prompt and '"Checkout"' in prompt
    allowed = args[args.index("--allowedTools") + 1: args.index("--disallowedTools")]
    assert "mcp__plugin_figma_figma__get_metadata" in allowed and not any("use_figma" in a for a in allowed)


def test_letter_export_brief_and_formats(server, monkeypatch):
    mod, base, tmp = server
    meta = {"file": "lt1", "type": "deck", "title": "Q3 review", "run": "/r/runs/2026-10-06-q3", "theme": "editorial", "built": True}

    class FakeLetters:
        TYPES = {"deck": {"label": "Deck", "template": "deck", "hint": "16:9 slides"}}
        @staticmethod
        def get(fid): return meta if fid == "lt1" else None
        @staticmethod
        def themes(): return [{"system": "editorial", "label": "Editorial", "kind": "theme"}]
        @staticmethod
        def save(m): pass
    monkeypatch.setattr(mod, "_letters", lambda: FakeLetters)
    j = json.loads(urllib.request.urlopen(f"{base}/api/systems?file=lt1", timeout=5).read())
    assert j["letter"]["type"] == "deck" and j["exports"] == ["pdf", "pptx", "png", "canva"]
    assert [s["system"] for s in j["systems"]] == ["editorial"]
    req = urllib.request.Request(base + "/api/letter/export", json.dumps({"file": "lt1", "format": "email"}).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)                       # email is not a deck format
    assert e.value.code == 400
    assert post(base, "/api/letter/export", {"file": "lt1", "format": "pptx"})["ok"]
    wait_idle(base, "lt1")
    args = json.loads((tmp / "args.jsonl").read_text().splitlines()[-1])
    prompt, sp = args[args.index("-p") + 1], args[args.index("--append-system-prompt") + 1]
    assert "push.py" in prompt and "pull" in prompt and "pptx_export.py" in prompt and "exports/" in prompt
    assert "LETTER" in sp and "Q3 review" in sp


def test_conversation_history_archive_reopen_delete(server):
    mod, base, tmp = server
    post(base, "/api/send", {"file": "f9", "text": "first conversation"})
    wait_idle(base, "f9")
    conv = lambda: json.loads(urllib.request.urlopen(f"{base}/api/conversations?file=f9", timeout=10).read())["conversations"]
    assert conv()[0]["current"] and conv()[0]["title"] == "first conversation"
    post(base, "/api/reset", {"file": "f9"})
    h = history(base, "f9")
    assert h["events"] == [] and h["session"] is None
    past = conv()
    assert len(past) == 1 and past[0]["title"] == "first conversation" and past[0]["resumable"]
    post(base, "/api/send", {"file": "f9", "text": "second one"})
    wait_idle(base, "f9")
    assert post(base, "/api/conversations/open", {"file": "f9", "id": past[0]["id"]})["ok"]
    h = history(base, "f9")
    assert h["events"][0]["text"] == "first conversation" and h["session"] == "sess-1"
    titles = [c["title"] for c in conv()]
    assert titles == ["first conversation", "second one"]          # the current one first, the other archived
    other = conv()[1]["id"]
    post(base, "/api/conversations/delete", {"file": "f9", "id": other})
    assert [c["title"] for c in conv()] == ["first conversation"]
    with pytest.raises(urllib.error.HTTPError):
        post(base, "/api/conversations/open", {"file": "f9", "id": "../x"})


def test_turns_keep_specialists_in_the_foreground():
    # A turn's process ends with flynn's reply; background specialists would die with it (2026-10-06).
    import sys as _sys
    _sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
    import engines
    t = engines.Turn(prompt="x", session=None, system="", allow=[], deny=[], file_id="f")
    assert t.env.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1"


def test_a_turn_cut_off_by_a_restart_is_closed(server):
    mod, base, tmp = server
    mod.fstore.append("panel/cut1", {"t": "user", "text": "hi"})          # files or Postgres, as the server stores it
    mod.fstore.append("panel/cut1", {"t": "penpot", "state": "progress", "run": "r", "build": "r@1"})
    ev = history(base, "cut1")["events"]
    assert ev[-1]["t"] == "turn_end" and ev[-1]["interrupted"] is True
    assert any(e["t"] == "penpot" and e["state"] == "error" and e["build"] == "r@1" for e in ev)
    assert not history(base, "cut1").get("running")
