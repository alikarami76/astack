import importlib.util
import json
import pathlib

FLYNN = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("panel_stream", FLYNN / "adapters/penpot/panel/stream.py")
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)


def ev(line):
    return stream.events_from_line(json.dumps(line))


def test_session_id_from_init():
    out = ev({"type": "system", "subtype": "init", "session_id": "s1", "cwd": "/x"})
    assert out == [{"t": "session", "id": "s1"}]


def test_text_delta_main_agent_only():
    main = {"type": "stream_event", "parent_tool_use_id": None,
            "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "سلام"}}}
    sub = dict(main, parent_tool_use_id="toolu_1")
    assert ev(main) == [{"t": "delta", "text": "سلام"}]
    assert ev(sub) == []


def test_progress_note_from_thinking_updates():
    line = {"type": "stream_event", "parent_tool_use_id": None,
            "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Reading the guide"}}}
    assert ev(line) == [{"t": "note", "text": "Reading the guide"}]
    empty = {"type": "stream_event", "parent_tool_use_id": None,
             "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": ""}}}
    assert ev(empty) == []


def test_message_stop_ends_bubble():
    line = {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_stop"}}
    assert ev(line) == [{"t": "assistant_end"}]


def test_tool_use_summary():
    line = {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "text", "text": "x"},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python3 kb/kb.py search button", "description": "Search the store"}},
        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/a/b/GUIDE.md"}},
        {"type": "tool_use", "id": "t3", "name": "Agent", "input": {"subagent_type": "flynn-librarian", "description": "Recall"}},
    ]}}
    out = ev(line)
    assert out == [
        {"t": "tool", "name": "Bash", "summary": "Search the store"},
        {"t": "tool", "name": "Read", "summary": "GUIDE.md"},
        {"t": "tool", "name": "Agent", "summary": "flynn-librarian: Recall"},
    ]


def test_result_with_denials_and_cost():
    line = {"type": "result", "subtype": "success", "session_id": "s1", "result": "done",
            "total_cost_usd": 0.12, "is_error": False,
            "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t9", "tool_input": {"command": "rm -rf x"}}]}
    out = ev(line)
    assert {"t": "session", "id": "s1"} in out
    assert {"t": "denied", "tools": [{"name": "Bash", "input": {"command": "rm -rf x"}, "rule": "Bash(rm -rf x)"}]} in out
    assert out[-1] == {"t": "turn_end", "ok": True, "cost": 0.12, "text": "done"}


def test_bad_line_is_ignored():
    assert stream.events_from_line("not json") == []


NEEDS = """Here is where we are.

```
NEEDS_ALIGNMENT
run: /runs/2026-10-04-empty-state
stage: brainstorm
brief: An empty state for the vouchers list.
questions:
  1. Who sees it? — recommended: new sellers — because the list is empty only before the first voucher
  2. Primary action? — recommended: create a voucher — because it is the only way out of empty
show_user:
  - /runs/2026-10-04-empty-state/contract.md
assumptions_if_unanswered:
  - desktop 1440px
```
"""


def test_parse_needs_alignment():
    cards = stream.parse_blocks(NEEDS)
    assert len(cards) == 1
    c = cards[0]
    assert c["kind"] == "needs_alignment"
    assert c["stage"] == "brainstorm"
    assert c["run"] == "/runs/2026-10-04-empty-state"
    assert c["questions"] == [
        {"n": 1, "q": "Who sees it?", "recommended": "new sellers", "because": "the list is empty only before the first voucher", "options": [], "multi": False},
        {"n": 2, "q": "Primary action?", "recommended": "create a voucher", "because": "it is the only way out of empty", "options": [], "multi": False},
    ]
    assert c["show_user"] == ["/runs/2026-10-04-empty-state/contract.md"]
    assert c["assumptions_if_unanswered"] == ["desktop 1440px"]


def test_parse_result_and_blocked():
    text = "RESULT\nrun: /r\nanswer: Done.\ndeliverables:\n  - /r/page/index.html — the page\ncaveats:\n  - none\n"
    c = stream.parse_blocks(text)[0]
    assert c["kind"] == "result" and c["answer"] == "Done." and c["deliverables"] == ["/r/page/index.html — the page"]
    b = stream.parse_blocks("BLOCKED\nrun: /r\nreason: no library\nneeded: the Figma link\n")[0]
    assert b == {"kind": "blocked", "run": "/r", "reason": "no library", "needed": "the Figma link"}


def test_no_block():
    assert stream.parse_blocks("Just a normal answer.") == []


def test_aligned_message():
    msg = stream.aligned_message([{"n": 1, "answer": "new sellers"}, {"n": 2, "answer": "create a voucher"}], note="keep it short")
    assert msg.startswith("ALIGNED\n1. new sellers\n2. create a voucher")
    assert "keep it short" in msg


MULTI = """NEEDS_ALIGNMENT
run: /r
stage: brainstorm
brief: x
questions:
  1. Primary action? — recommended: create a voucher — because it is the way out
     options: create a voucher | import vouchers | read the guide
  2. Which states? — recommended: empty; no results — because the guide needs both
     options: empty | no results | loading | error
     multi: yes
  3. Anything else? — recommended: no
"""


def test_parse_options_and_multi():
    c = stream.parse_blocks(MULTI)[0]
    q1, q2, q3 = c["questions"]
    assert q1["options"] == ["create a voucher", "import vouchers", "read the guide"] and q1["multi"] is False
    assert q2["options"] == ["empty", "no results", "loading", "error"] and q2["multi"] is True
    assert q2["recommended"] == "empty; no results"
    assert q3["options"] == [] and q3["multi"] is False


def test_specialist_tool_calls_become_named_activity():
    import json as _j
    start = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_9", "name": "Agent",
             "input": {"subagent_type": "flynn-composer", "description": "two options"}}]}}
    assert stream.events_from_line(_j.dumps(start))[0]["t"] == "tool"
    inner = {"type": "assistant", "parent_tool_use_id": "tu_9", "message": {"content": [
        {"type": "tool_use", "id": "x", "name": "Bash", "input": {"description": "Validate option A"}}]}}
    assert stream.events_from_line(_j.dumps(inner)) == [{"t": "activity", "agent": "flynn-composer", "tool": "Bash", "text": "Validate option A"}]
    text = {"type": "assistant", "parent_tool_use_id": "tu_9", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert stream.events_from_line(_j.dumps(text)) == []
