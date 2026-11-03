import pytest
import json
import subprocess
from pathlib import Path

import proposals
import patterns
import store


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    (path / "a.txt").write_text("old\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=path, check=True)
    return path


DIFF = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
"""


def test_new_list_apply(tmp_path):
    repo = git_repo(tmp_path / "r")
    pdir = tmp_path / "proposals"
    p = proposals.new("fix-a", "Change a", "clu", "run x: user said y", "none", "cat a.txt", DIFF, pdir=pdir)
    assert p.exists() and proposals.list_pending(pdir)[0]["id"] == p.stem
    sha = proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "changelog.md")
    assert (repo / "a.txt").read_text() == "new\n" and len(sha) >= 7
    assert (pdir / "applied" / p.name).exists() and proposals.list_pending(pdir) == []
    assert "Change a" in (tmp_path / "changelog.md").read_text()


def test_diff_touching_fenced_markdown_applies(tmp_path):
    repo = git_repo(tmp_path / "r")
    old_text = "# Notes\n\n" + "```python\nold_code()\n```\n"
    new_text = "# Notes\n\n" + "```python\nnew_code()\n```\n"
    (repo / "notes.md").write_text(old_text)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add notes"], cwd=repo, check=True)

    # Compute the real unified diff (touching a fenced .md block) instead of
    # hand-typing one, so the embedded fence characters are exactly what git produces.
    (repo / "notes.md").write_text(new_text)
    diff_proc = subprocess.run(["git", "diff", "--no-color", "--", "notes.md"], cwd=repo,
                                capture_output=True, text=True, check=True)
    # keep from the "--- a/notes.md" line onward
    out_lines = diff_proc.stdout.splitlines(keepends=True)
    idx = next(i for i, l in enumerate(out_lines) if l.startswith("--- "))
    md_diff = "".join(out_lines[idx:])
    (repo / "notes.md").write_text(old_text)  # restore; the proposal's diff is what applies the change

    pdir = tmp_path / "proposals"
    p = proposals.new("md-fix", "Fix notes", "clu", "m", "r", "t", md_diff, pdir=pdir)
    # the proposal body must use the 4-backtick fence so the embedded ``` doesn't truncate it
    assert "````diff" in p.read_text()
    sha = proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "changelog.md")
    assert "new_code()" in (repo / "notes.md").read_text() and len(sha) >= 7


def test_new_appends_suffix_instead_of_overwriting(tmp_path):
    pdir = tmp_path / "proposals"
    p1 = proposals.new("dup", "First", "clu", "m", "r", "t", DIFF, pdir=pdir)
    p2 = proposals.new("dup", "Second", "clu", "m", "r", "t", DIFF, pdir=pdir)
    p3 = proposals.new("dup", "Third", "clu", "m", "r", "t", DIFF, pdir=pdir)
    assert p1 != p2 != p3
    assert p2.stem == p1.stem + "-2"
    assert p3.stem == p1.stem + "-3"
    assert "First" in p1.read_text()
    assert "Second" in p2.read_text()
    assert "Third" in p3.read_text()


def test_reject(tmp_path):
    pdir = tmp_path / "proposals"
    p = proposals.new("nope", "Bad idea", "clu", "m", "r", "t", DIFF, pdir=pdir)
    proposals.reject(p.stem, "not needed", pdir=pdir)
    moved = pdir / "rejected" / p.name
    assert moved.exists() and "not needed" in moved.read_text()


def test_bad_diff_is_refused(tmp_path):
    repo = git_repo(tmp_path / "r")
    pdir = tmp_path / "proposals"
    p = proposals.new("bad", "Bad", "clu", "m", "r", "t", DIFF.replace("-old", "-missing"), pdir=pdir)
    try:
        proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "c.md")
        assert False, "should raise"
    except RuntimeError:
        pass
    assert (repo / "a.txt").read_text() == "old\n"


def test_patterns_report(kbenv, tmp_path):
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "units on money", "applies_to": "step:report"})
    store.upsert(c, "lesson", {"rule": "data first", "applies_to": "step:report"})
    store.upsert(c, "run", {"run_id": "r1", "date": "2026-09-23", "type": "analysis", "question": "q", "corrections_count": 3, "verify_result": "refuted"})
    ev = tmp_path / "events.jsonl"
    ev.write_text("\n".join(json.dumps({"event": "PostToolUseFailure", "tool_name": "Bash", "error": "timeout after 300s", "ts": "2026-09-23T10:00:00+03:30"}) for _ in range(3)) + "\n")
    rep = patterns.report(since_days=3650, events=ev, conn=c)
    assert "step:report: 2" in rep and "r1" in rep and "Bash" in rep and "timeout" in rep


def test_patterns_fires_on_two_runs_touching_same_source(kbenv, tmp_path):
    c = store.connect()
    # The source's origin sticks to the first curator write (schema-cache does
    # not export, but a real run: origin still only records the first run),
    # so the same-source signal must come from facts.run_id, not sources.origin.
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"},
                        origin="run:2026-09-20-first-run")
    store.upsert(c, "fact", {"statement": "orders fact from run 1", "source_id": sid, "run_id": "2026-09-20-first-run"},
                 origin="run:2026-09-20-first-run")
    store.upsert(c, "fact", {"statement": "orders fact from run 2", "source_id": sid, "run_id": "2026-09-26-second-run"},
                 origin="run:2026-09-20-first-run")  # origin unchanged: it stuck to the first run
    rep = patterns.report(since_days=3650, conn=c)
    assert "orders: 2 runs" in rep


def test_delete_file_diff(tmp_path):
    repo = git_repo(tmp_path / "r")
    (repo / "b.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add b"], cwd=repo, check=True)

    delete_diff = """--- a/b.txt
+++ /dev/null
@@ -1 +0,0 @@
-content
"""
    pdir = tmp_path / "proposals"
    p = proposals.new("del-b", "Delete b", "clu", "cleanup", "none", "test", delete_diff, pdir=pdir)
    sha = proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "changelog.md")

    # Verify b.txt is deleted
    assert not (repo / "b.txt").exists()
    # Verify it's not in git
    ls = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True)
    assert "b.txt" not in ls.stdout
    # Verify proposal was applied
    assert (pdir / "applied" / p.name).exists()


def test_dirty_target_file_is_refused(tmp_path):
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("old\nlocal edit\n")  # dirty, uncommitted
    pdir = tmp_path / "proposals"
    p = proposals.new("dirty", "Change a", "clu", "m", "r", "t", DIFF, pdir=pdir)
    try:
        proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "c.md")
        assert False, "should raise"
    except RuntimeError as e:
        assert "refusing to apply" in str(e)
    assert (repo / "a.txt").read_text() == "old\nlocal edit\n"
    ls = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True)
    assert "M a.txt" in ls.stdout


def test_prestaged_unrelated_file_is_left_alone(tmp_path):
    # The repo is shared by several projects: another project's staged work must not block a proposal,
    # and must not be swept into its commit.
    repo = git_repo(tmp_path / "r")
    (repo / "other.txt").write_text("unrelated\n")
    subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
    pdir = tmp_path / "proposals"
    p = proposals.new("staged", "Change a", "clu", "m", "r", "t", DIFF, pdir=pdir)
    proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "c.md")
    assert (repo / "a.txt").read_text() == "new\n"
    shown = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    assert shown.stdout.split() == ["a.txt"]
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True, check=True)
    assert staged.stdout.strip() == "other.txt"


def test_staged_touched_file_is_refused(tmp_path):
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("staged edit\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    pdir = tmp_path / "proposals"
    p = proposals.new("staged2", "Change a", "clu", "m", "r", "t", DIFF, pdir=pdir)
    with pytest.raises(RuntimeError, match="refusing to apply"):
        proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "c.md")


def test_revert_on_test_failure(tmp_path):
    repo = git_repo(tmp_path / "r")
    # Add a test file that expects the old content
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_ok():\n    assert open('a.txt').read() == 'old\\n'\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add test"], cwd=repo, check=True)

    pdir = tmp_path / "proposals"
    p = proposals.new("fail", "Will fail", "clu", "m", "r", "t", DIFF, pdir=pdir)

    # Get the initial HEAD before apply
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    # Try to apply - should fail and revert
    try:
        proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=True, changelog=tmp_path / "c.md")
        assert False, "should raise"
    except RuntimeError as e:
        assert "tests failed, reverted" in str(e)

    # Verify file was reverted
    assert (repo / "a.txt").read_text() == "old\n"

    # Verify no new commit was created
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    assert head_before == head_after

    # Verify proposal is still pending
    assert p.exists()
    assert proposals.list_pending(pdir)[0]["id"] == p.stem


def test_diff_u_headers_with_timestamps_apply(tmp_path):
    repo = git_repo(tmp_path / "r")
    pdir = tmp_path / "proposals"
    d = DIFF.replace("--- a/a.txt", "--- a/a.txt\t2026-09-29 17:35:09").replace("+++ b/a.txt", "+++ b/a.txt\t2026-09-29 17:35:10")
    p = proposals.new("fix-ts", "Timestamped", "clu", "m", "none", "t", d, pdir=pdir)
    proposals.apply(p.stem, pdir=pdir, repos={"clu": repo}, run_tests=False, changelog=tmp_path / "c.md")
    assert (repo / "a.txt").read_text() == "new\n"


def test_patterns_reports_helper_use(kbenv, tmp_path):
    c = store.connect()
    ev = tmp_path / "events.jsonl"
    rows = [{"event": "SubagentStart", "agent_type": "clu-verifier", "agent_id": a, "ts": "2026-09-23T10:00:00"} for a in ("a1", "a1", "a2")]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rep = patterns.report(since_days=3650, events=ev, conn=c)
    assert "- clu-verifier: 2" in rep and "- clu-methodologist: 0  <- never used" in rep
