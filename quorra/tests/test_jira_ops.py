import json

import pytest

import jira_ops as j


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "create":
            return {"created": {"key": f"PROJ-{100 + len(self.calls)}"}, "url": "u"}
        return {"ok": True}


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    return d


def test_non_cpp_refused_before_any_call(run):
    fr = FakeRunner()
    with pytest.raises(j.JiraOpsError, match="PROJ"):
        j.track(run, "OTHER-1")
    with pytest.raises(j.JiraOpsError, match="PROJ"):
        j.comment(run, "MP-5", "x", runner=fr)
    assert fr.calls == []


def test_untracked_cpp_ticket_refused(run):
    fr = FakeRunner()
    with pytest.raises(j.JiraOpsError, match="not a ticket of this run"):
        j.move(run, "PROJ-7", "review", runner=fr)
    assert fr.calls == []


def test_track_then_move_and_comment(run):
    fr = FakeRunner()
    j.track(run, "proj-97")
    assert j.allowed(run, "PROJ-97")
    j.move(run, "PROJ-97", "review", runner=fr)
    j.comment(run, "PROJ-97", "MR: https://x/-/merge_requests/1", runner=fr)
    assert fr.calls == [["transition", "PROJ-97", "--transition", "In Review"],
                        ["comment", "PROJ-97", "--body", "MR: https://x/-/merge_requests/1"]]


def test_unknown_step_refused(run):
    j.track(run, "PROJ-1")
    with pytest.raises(j.JiraOpsError, match="step"):
        j.move(run, "PROJ-1", "done", runner=FakeRunner())


def test_propose_and_create_need_matching_fingerprint(run):
    (run / "jira-proposal.json").write_text(json.dumps({"tickets": [
        {"summary": "Backend flag", "description": "AC: ...", "issue_type": "Task"},
        {"summary": "UI column", "description": "AC: ...", "issue_type": "Sub-task", "parent": "PROJ-50"}]}))
    p = j.propose(run)
    assert len(p["fingerprint"]) == 12 and "Backend flag" in p["markdown"]
    assert (run / "jira-proposal.md").read_text() == p["markdown"]
    fr = FakeRunner()
    with pytest.raises(j.JiraOpsError, match="fingerprint"):
        j.create(run, "000000000000", runner=fr)
    assert fr.calls == []
    keys = j.create(run, p["fingerprint"], runner=fr)
    assert keys == ["PROJ-101", "PROJ-102"]
    assert fr.calls[1][:1] == ["create"] and "--field" in fr.calls[1]
    assert json.loads(fr.calls[1][fr.calls[1].index("--field") + 1].split("=", 1)[1]) == {"key": "PROJ-50"}
    assert all(j.allowed(run, k) for k in keys)


def test_create_refuses_attribution_and_secrets(run):
    (run / "jira-proposal.json").write_text(json.dumps({"tickets": [
        {"summary": "x", "description": "Co-Authored-By: someone", "issue_type": "Task"}]}))
    with pytest.raises(j.JiraOpsError, match="attribution"):
        j.propose(run)
    (run / "jira-proposal.json").write_text(json.dumps({"tickets": [
        {"summary": "x", "description": "password=hunter2hunter2", "issue_type": "Task"}]}))
    with pytest.raises(j.JiraOpsError, match="secret"):
        j.propose(run)


def test_parent_outside_cpp_refused(run):
    (run / "jira-proposal.json").write_text(json.dumps({"tickets": [
        {"summary": "x", "description": "y", "issue_type": "Sub-task", "parent": "MP-1"}]}))
    with pytest.raises(j.JiraOpsError, match="PROJ"):
        j.propose(run)


def test_create_rerun_does_not_duplicate(run):
    (run / "jira-proposal.json").write_text(json.dumps({"tickets": [
        {"summary": "A", "description": "d", "issue_type": "Task"},
        {"summary": "B", "description": "d", "issue_type": "Task"}]}))
    fp = j.propose(run)["fingerprint"]
    calls = []

    def bad(args):
        calls.append(args)
        if len(calls) == 2:
            raise j.JiraOpsError("boom")
        return {"created": {"key": "PROJ-201"}}

    with pytest.raises(j.JiraOpsError, match="boom"):
        j.create(run, fp, runner=bad)
    good = FakeRunner()
    keys = j.create(run, fp, runner=good)
    assert len(good.calls) == 1 and good.calls[0][good.calls[0].index("--summary") + 1] == "B"
    assert keys == ["PROJ-201", "PROJ-101"]


def test_default_runner_timeout(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 120)

    monkeypatch.setattr(j.subprocess, "run", boom)
    with pytest.raises(j.JiraOpsError, match="timed out after 120s"):
        j.default_runner(["comment", "PROJ-1"])


def test_comment_cli_needs_the_approved_fingerprint(run, capsys, monkeypatch):
    j.track(run, "PROJ-97")
    fr = FakeRunner()
    monkeypatch.setattr(j, "default_runner", fr)
    assert j.main(["comment", str(run), "PROJ-97", "--body", "hello", "--plan"]) == 0
    fp = json.loads(capsys.readouterr().out)["fingerprint"]
    assert fp == j._fp({"key": "PROJ-97", "body": "hello"})
    assert j.main(["comment", str(run), "PROJ-97", "--body", "hello", "--approve", "000000000000"]) == 2
    assert fr.calls == []
    assert j.main(["comment", str(run), "PROJ-97", "--body", "changed", "--approve", fp]) == 2
    assert fr.calls == []
    assert j.main(["comment", str(run), "PROJ-97", "--body", "hello", "--approve", fp]) == 0
    assert fr.calls == [["comment", "PROJ-97", "--body", "hello"]]
    with pytest.raises(SystemExit):
        j.main(["comment", str(run), "PROJ-97", "--body", "hello"])
