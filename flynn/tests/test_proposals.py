import subprocess

import proposals
import pytest

DIFF = """diff --git a/note.md b/note.md
--- a/note.md
+++ b/note.md
@@ -1 +1 @@
-old
+new
"""


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "note.md").write_text("old\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True)
    return r


def test_new_apply_reject(tmp_path):
    pdir, repo = tmp_path / "proposals", _repo(tmp_path)
    p = proposals.new("tweak", "Tweak note", "flynn", "because", "low", "pytest", DIFF, pdir=pdir)
    assert "````diff" in p.read_text()
    assert [x["id"] for x in proposals.list_pending(pdir)] == [p.stem]
    sha = proposals.apply(p.stem, pdir=pdir, repos={"flynn": repo}, run_tests=False, changelog=repo / "docs/changelog.md")
    assert sha and (repo / "note.md").read_text() == "new\n" and (pdir / "applied" / p.name).exists()
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
    assert log.startswith("flynn proposal")
    q = proposals.new("other", "Other", "flynn", "m", "r", "t", DIFF, pdir=pdir)
    proposals.reject(q.stem, "not needed", pdir=pdir)
    assert (pdir / "rejected" / q.name).exists() and proposals.list_pending(pdir) == []


def test_apply_refuses_local_edits(tmp_path):
    pdir, repo = tmp_path / "proposals", _repo(tmp_path)
    (repo / "note.md").write_text("dirty\n")
    p = proposals.new("tweak", "Tweak", "flynn", "m", "r", "t", DIFF, pdir=pdir)
    with pytest.raises(RuntimeError, match="local edits"):
        proposals.apply(p.stem, pdir=pdir, repos={"flynn": repo}, run_tests=False, changelog=repo / "c.md")


def test_bad_id_rejected():
    with pytest.raises(ValueError):
        proposals.new("../evil", "t", "flynn", "m", "r", "t", DIFF)


def test_apply_works_when_flynn_is_a_subfolder_of_the_git_repo(tmp_path):
    # flynn lives in agent-stack/flynn; git apply run from a subfolder silently skips root-relative paths
    top = tmp_path / "astack"
    sub = top / "flynn"
    sub.mkdir(parents=True)
    (sub / "note.md").write_text("old\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(top), *cmd], check=True)
    pdir = tmp_path / "proposals"
    p = proposals.new("tweak", "Tweak note", "flynn", "m", "r", "t", DIFF, pdir=pdir)
    sha = proposals.apply(p.stem, pdir=pdir, repos={"flynn": sub}, run_tests=False, changelog=sub / "docs/changelog.md")
    assert sha and (sub / "note.md").read_text() == "new\n"
    changed = subprocess.run(["git", "-C", str(top), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert "flynn/note.md" in changed
