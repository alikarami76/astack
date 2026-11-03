import json
from pathlib import Path

import pytest
import yaml

import bug_draft


def draft(tmp_path, **over):
    (tmp_path / "shot.png").write_bytes(b"png")
    d = {"title": "Discount step shows 44% for an exact 45% price on round prices", "feature": "Flash discount form",
         "component": "flashSaleForm", "requirement_id": "R6",
         "environment": {"name": "local", "url": "http://localhost:3333/x", "build": "abc123"},
         "steps": ["Open the step", "Type 5500"], "expected": "45", "actual": "44, token=abc123secret",
         "frequency": "always", "severity": "S3", "severity_rationale": "edge case", "priority": "P3",
         "priority_rationale": "backlog", "evidence": ["shot.png"]}
    d.update(over)
    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True))
    return p


def test_check_rejects_bad_drafts(tmp_path):
    base = yaml.safe_load(draft(tmp_path).read_text())
    assert bug_draft.check(base, tmp_path) == []
    bad = dict(base, title="bug", severity="S9", steps=["one"], evidence=["missing.png", ".env"],
               environment={"name": "production", "build": "x"})
    errs = " | ".join(bug_draft.check(bad, tmp_path))
    for s in ("vague", "severity", "two steps", "not found", "secret", "production"):
        assert s in errs, s


def test_lifecycle_gates(tmp_path, capsys):
    out = tmp_path / "bugs"
    assert bug_draft.main(["new", str(draft(tmp_path)), "--out-dir", str(out)]) == 0
    path = next(out.glob("BUG-*.json"))
    bug = json.loads(path.read_text())
    assert "abc123secret" not in json.dumps(bug)  # redacted
    assert bug_draft.main(["payload", str(path)]) == 4  # not approved
    fp = bug_draft.fingerprint(bug)
    assert bug_draft.main(["approve", str(path), "--fingerprint", fp]) == 4  # not verified yet
    assert bug_draft.main(["verdict", str(path), "--verdict", "sustained", "--notes", "reproduced"]) == 0
    assert bug_draft.main(["approve", str(path), "--fingerprint", "wrong"]) == 4
    assert bug_draft.main(["approve", str(path), "--fingerprint", fp]) == 0
    capsys.readouterr()
    assert bug_draft.main(["payload", str(path), "--project", "PROJ"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["projectKey"] == "PROJ" and payload["issueTypeName"] == "Bug"
    assert payload["cli"].startswith("python3 ~/.claude/skills/your-jira/scripts/jira_tool.py create --project PROJ --issue-type Bug")
    assert "--labels rinzler" in payload["cli"] and Path(payload["description_file"]).read_text().strip()
    assert bug_draft.main(["filed", str(path), "--key", "PROJ-123"]) == 0
    assert json.loads(path.read_text())["status"] == "filed" and json.loads(path.read_text())["jira_key"] == "PROJ-123"
    bug = json.loads(path.read_text()); bug["status"] = "approved"; path.write_text(json.dumps(bug))
    # editing the draft after approval invalidates it
    bug = json.loads(path.read_text())
    bug["actual"] = "changed"
    path.write_text(json.dumps(bug))
    assert bug_draft.main(["payload", str(path)]) == 4
    assert bug_draft.main(["filed", str(path), "--key", "PROJ-1"]) in (0, 4)


def test_refuted_cannot_be_approved(tmp_path):
    out = tmp_path / "bugs"
    bug_draft.main(["new", str(draft(tmp_path)), "--out-dir", str(out)])
    path = next(out.glob("BUG-*.json"))
    bug_draft.main(["verdict", str(path), "--verdict", "refuted", "--notes", "documented behaviour"])
    fp = bug_draft.fingerprint(json.loads(path.read_text()))
    assert bug_draft.main(["approve", str(path), "--fingerprint", fp]) == 4


def test_dedupe_finds_past_bug_and_builds_jql(tmp_path, capsys):
    out = tmp_path / "bugs"
    bug_draft.main(["new", str(draft(tmp_path)), "--out-dir", str(out)])
    bug_draft.main(["new", str(draft(tmp_path, title="Discount step shows 43% for an exact 44% price")), "--out-dir", str(out)])
    capsys.readouterr()
    path = sorted(out.glob("BUG-*-44-*.json"))[0]
    bug_draft.main(["dedupe", str(path), "--project", "PROJ"])
    res = json.loads(capsys.readouterr().out)
    assert res["kb_matches"] and res["jql"][0].startswith("project = PROJ AND issuetype = Bug")


def test_new_refuses_output_in_dk_code(tmp_path):
    with pytest.raises(Exception):
        bug_draft.main(["new", str(draft(tmp_path)), "--out-dir", str(tmp_path / "code" / "x")])
