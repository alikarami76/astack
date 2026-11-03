import install_hooks

EVENTS = ["PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop"]
CMD = 'python3 "/home/user/agent-stack/clu/hooks/telemetry.py"'
GUARD = 'python3 "/home/user/agent-stack/clu/hooks/guard.py"'


def test_merge_adds_and_preserves():
    s = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}]}, "model": "opus"}
    out = install_hooks.merge(s, CMD)
    assert out["hooks"]["PreToolUse"] == s["hooks"]["PreToolUse"] and out["model"] == "opus"
    for e in EVENTS:
        cmds = [h["command"] for g in out["hooks"][e] for h in g["hooks"]]
        assert cmds.count(CMD) == 1


def test_merge_is_idempotent():
    once = install_hooks.merge({}, CMD)
    twice = install_hooks.merge(once, CMD)
    assert once == twice


def test_merge_drops_unread_events_and_adds_guard_once():
    old = {"hooks": {e: [{"hooks": [{"type": "command", "command": CMD}]}]
                     for e in ["SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "SessionEnd"]}}
    old["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "other-tool"}]})
    out = install_hooks.merge(old, CMD, GUARD)
    assert "SessionStart" not in out["hooks"] and "PreCompact" not in out["hooks"]
    assert out["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "other-tool"}]}]
    assert set(EVENTS) <= set(out["hooks"])
    again = install_hooks.merge(out, CMD, GUARD)
    guards = [h for g in again["hooks"]["PreToolUse"] for h in g["hooks"] if "guard.py" in h["command"]]
    assert len(guards) == 1 and again == out
