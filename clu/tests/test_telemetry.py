import json
import subprocess
import sys
from pathlib import Path

import telemetry

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "telemetry.py"


def test_family_filter(tmp_path):
    out = tmp_path / "e.jsonl"
    assert telemetry.handle({"hook_event_name": "PostToolUse", "agent_type": "clu", "tool_name": "Bash", "session_id": "s"}, out)
    assert telemetry.handle({"hook_event_name": "SubagentStop", "agent_type": "clu-verifier", "session_id": "s"}, out)
    assert not telemetry.handle({"hook_event_name": "PostToolUse", "agent_type": "Explore", "tool_name": "Read"}, out)
    assert not telemetry.handle({"hook_event_name": "PostToolUse", "tool_name": "Read"}, out)
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert [l["event"] for l in lines] == ["PostToolUse", "SubagentStop"]
    assert lines[0]["schema_version"] == 1


def test_redaction_and_truncation(tmp_path):
    out = tmp_path / "e.jsonl"
    telemetry.handle({"hook_event_name": "PostToolUse", "agent_type": "clu", "tool_name": "Bash",
                      "tool_input": {"command": "tool --password hunter2 query 'select 1' " + "x" * 500},
                      "tool_response": {"stdout": "ok"}}, out)
    telemetry.handle({"hook_event_name": "UserPromptSubmit", "agent_type": "clu", "prompt": "token: abc123 " + "y" * 3000}, out)
    a, b = [json.loads(l) for l in out.read_text().splitlines()]
    assert "hunter2" not in json.dumps(a) and len(a["input_preview"]) <= 300 and a["input_hash"]
    assert "abc123" not in b["prompt_preview"] and len(b["prompt_preview"]) <= 2000


def test_failure_event_records_error(tmp_path):
    out = tmp_path / "e.jsonl"
    telemetry.handle({"hook_event_name": "PostToolUseFailure", "agent_type": "clu", "tool_name": "Bash", "error": "timeout after 300s"}, out)
    assert json.loads(out.read_text())["error"] == "timeout after 300s"


def test_cli_never_fails(tmp_path):
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True,
                       env={"CLU_TELEMETRY_FILE": str(tmp_path / "e.jsonl"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0


def test_bearer_token_redaction(tmp_path):
    out = tmp_path / "e.jsonl"
    telemetry.handle({"hook_event_name": "UserPromptSubmit", "agent_type": "clu", "prompt": "Authorization: Bearer sk-live-abc123"}, out)
    rec = json.loads(out.read_text())
    assert "sk-live-abc123" not in rec["prompt_preview"]
    assert "Bearer ***" in rec["prompt_preview"]


def test_redaction_forms(tmp_path):
    out = tmp_path / "e.jsonl"
    cases = [
        ('{"password": "hunter2"}', "hunter2"),
        ("mysql -phunter2 -u root", "hunter2"),
        ("MYSQL_PWD=hunter2 mysqldump", "hunter2"),
        ("DB_PASS=hunter2 ./run.sh", "hunter2"),
        ("connect to mysql://root:hunter2@10.0.0.1/db", "hunter2"),
    ]
    for i, (text, secret) in enumerate(cases):
        telemetry.handle({"hook_event_name": "PostToolUseFailure", "agent_type": "clu", "tool_name": "Bash", "error": text}, out)
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    for rec, (text, secret) in zip(recs, cases):
        assert secret not in rec["error"], f"{secret!r} leaked in {rec['error']!r} (from {text!r})"


def test_error_field_redaction_and_truncation(tmp_path):
    out = tmp_path / "e.jsonl"
    long_error = "--password hunter2 " + "x" * 5000
    telemetry.handle({"hook_event_name": "PostToolUseFailure", "agent_type": "clu", "tool_name": "Bash", "error": long_error}, out)
    rec = json.loads(out.read_text())
    assert "hunter2" not in rec["error"]
    assert len(rec["error"]) <= 2000
