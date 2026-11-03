"""Studio's model-API engine end to end against a fake OpenAI-compatible server (provider "custom"): aisuite streams a
tool call in fragments, the engine runs the design tool, sends the result back, then streams the final answer."""
import importlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("aisuite")
FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))


def sse(h, chunks):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    for c in chunks:
        h.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        h.wfile.flush()
    h.wfile.write(b"data: [DONE]\n\n")


def sse_head(h):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    h.wfile.write(f"data: {json.dumps(chunk({'content': 'Starting…'}))}\n\n".encode())
    h.wfile.flush()


def chunk(delta, finish=None):
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


SCRIPT = {"steps": None}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    seen = []
    SCRIPT["steps"] = None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            assert self.headers["Authorization"] == "Bearer sk-test-1234"
            if SCRIPT["steps"] is not None:
                step = SCRIPT["steps"][min(len(seen), len(SCRIPT["steps"])) - 1]
                if step == "429":
                    self.send_response(429); self.send_header("Retry-After", "0.1"); self.send_header("Content-Type", "application/json")
                    self.end_headers(); self.wfile.write(b'{"error": {"message": "rate limit"}}'); return
                if step == "stall":
                    sse_head(self); time.sleep(30); return
                return sse(self, step)
            if len(seen) == 1:          # step 1: think, then call studio_create_run in two argument fragments
                sse(self, [chunk({"role": "assistant", "reasoning_content": "Planning a page."}),
                           chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                  "function": {"name": "studio_create_run", "arguments": '{"title": "Tip'}}]}),
                           chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' split", "type": "ui", "html": "<html></html>"}'}}]}),
                           chunk({}, "tool_calls")])
            else:                        # step 2: answer
                sse(self, [chunk({"content": "Made the "}), chunk({"content": "run."}), chunk({}, "stop")])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("STUDIO_TEAM", raising=False)
    (tmp_path / "runs").mkdir()
    import team, chat, api_keys, design_tools, api_engine, studio
    for m in (team, chat, api_keys, design_tools, api_engine, studio):
        importlib.reload(m)
    chat.SRV = chat.Srv(tmp_path / "runs", studio.snapshot, studio.export, studio.run_summary, studio.add_link,
                        studio.list_runs, studio.pick)
    api_keys.set_key(None, "custom", "sk-test-1234")
    api_keys.save_choice(None, provider="custom", model="fake-model", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1")
    chat.save_settings(engine="api")
    yield chat, seen, tmp_path
    srv.shutdown()


def test_api_turn_streams_runs_a_tool_and_answers(fake):
    chat, seen, tmp = fake
    conv = chat.get("home")
    assert conv.start_turn("make a tip split page", effort="quick")
    import time
    for _ in range(200):
        if not conv.running:
            break
        time.sleep(0.05)
    assert conv.events[-1]["t"] == "moved"                 # a new run: Home's conversation moved into it
    conv = chat.get(f"run:{conv.events[-1]['run']}")
    kinds = [e["t"] for e in conv.events]
    assert "note" in kinds and "tool" in kinds and "turn_end" in kinds, kinds
    tool = next(e for e in conv.events if e["t"] == "tool")
    assert tool["name"] == "studio_create_run" and tool["summary"] == "Tip split"
    assert "".join(e["text"] for e in conv.events if e["t"] == "delta") == "Made the run."
    assert [e for e in conv.events if e["t"] == "turn_end"][-1]["ok"] is True
    runs = [p.name for p in (tmp / "runs").iterdir()]
    assert any(r.endswith("tip-split") for r in runs)
    # step 2 got the tool's result back, linked to the call
    tool_msg = [m for m in seen[1]["messages"] if m.get("role") == "tool"]
    assert tool_msg and tool_msg[0]["tool_call_id"] == "call_1" and "tip-split" in tool_msg[0]["content"]
    assert conv.session and conv.session.startswith("api-")
    hist = json.loads((conv.base / "_api" / f"{conv.session}.json").read_text())
    assert [m["role"] for m in hist] == ["user", "assistant", "tool", "assistant"]
    assert "sk-test-1234" not in json.dumps(conv.events)


def _wait(conv, secs=20):
    for _ in range(int(secs / 0.05)):
        if not conv.running:
            return
        time.sleep(0.05)
    raise AssertionError("turn did not end")


def test_rate_limit_retries_then_answers(fake):
    chat, seen, _ = fake
    SCRIPT["steps"] = ["429", [chunk({"content": "Hello."}), chunk({}, "stop")]]
    conv = chat.get("home")
    conv.start_turn("hi", effort="quick"); _wait(conv)
    assert "".join(e["text"] for e in conv.events if e["t"] == "delta") == "Hello."
    assert len(seen) >= 2            # retried (by the provider's own SDK, or by the engine when the SDK doesn't)


def test_mangled_tool_call_is_explained_not_run(fake):
    chat, seen, tmp = fake
    SCRIPT["steps"] = [[chunk({"tool_calls": [{"index": 0, "id": "c9", "type": "function",
                                              "function": {"name": "studio_create_run", "arguments": '{"title": "cut'}}]}), chunk({}, "length")],
                       [chunk({"content": "Sorry."}), chunk({}, "stop")]]
    conv = chat.get("home")
    conv.start_turn("make it", effort="quick"); _wait(conv)
    assert not list((tmp / "runs").iterdir())                        # nothing was created
    tool_msg = [m for m in seen[1]["messages"] if m.get("role") == "tool"][0]
    assert "cut off by the output limit" in tool_msg["content"]


def test_length_cut_reply_is_continued(fake):
    chat, seen, _ = fake
    SCRIPT["steps"] = [[chunk({"content": "Part one, "}), chunk({}, "length")], [chunk({"content": "part two."}), chunk({}, "stop")]]
    conv = chat.get("home")
    conv.start_turn("long answer please", effort="quick"); _wait(conv)
    assert "".join(e["text"] for e in conv.events if e["t"] == "delta") == "Part one, part two."
    assert seen[1]["messages"][-1]["content"].startswith("Your reply was cut off")


def test_stop_ends_a_stalled_stream(fake):
    chat, _, _ = fake
    SCRIPT["steps"] = ["stall"]
    conv = chat.get("home")
    conv.start_turn("hi", effort="quick")
    time.sleep(1.0)
    t0 = time.time(); conv.stop(); _wait(conv, 8)
    assert time.time() - t0 < 5 and any(e["t"] == "stopped" for e in conv.events)
