import subprocess

import proposals


def test_new_list_reject(tmp_path):
    p = proposals.new("add-area", "Add a caching area", "quorra", "scouts miss caching", "low", "pytest",
                      "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", pdir=tmp_path)
    assert [fm["id"] for fm in proposals.list_pending(pdir=tmp_path)] == [p.stem]
    proposals.reject(p.stem, "not now", pdir=tmp_path)
    assert proposals.list_pending(pdir=tmp_path) == []
    assert (tmp_path / "rejected" / p.name).exists()


def test_apply_commits(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "x").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    p = proposals.new("fix", "Change x", "quorra", "m", "r", "t", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", pdir=tmp_path / "p")
    sha = proposals.apply(p.stem, pdir=tmp_path / "p", repos={"quorra": repo}, run_tests=False, changelog=tmp_path / "cl.md")
    assert (repo / "x").read_text() == "b\n" and sha
    assert "Change x" in (tmp_path / "cl.md").read_text()


def _repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "x").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    return repo


def _count(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_diff_without_file_headers_refused(tmp_path):
    import pytest
    repo = _repo(tmp_path)
    (repo / "stray").write_text("untracked\n")
    p = proposals.new("nohdr", "No headers", "quorra", "m", "r", "t", "similarity index 100%\nrename from x\nrename to y\n", pdir=tmp_path / "p")
    with pytest.raises(RuntimeError, match="which files"):
        proposals.apply(p.stem, pdir=tmp_path / "p", repos={"quorra": repo}, run_tests=False, changelog=tmp_path / "cl.md")
    assert _count(repo) == "1"


def test_rename_only_diff_commits_only_renamed_paths(tmp_path):
    repo = _repo(tmp_path)
    (repo / "stray").write_text("untracked\n")
    diff = "diff --git a/x b/y\nsimilarity index 100%\nrename from x\nrename to y\n"
    p = proposals.new("ren", "Rename", "quorra", "m", "r", "t", diff, pdir=tmp_path / "p")
    proposals.apply(p.stem, pdir=tmp_path / "p", repos={"quorra": repo}, run_tests=False, changelog=tmp_path / "cl.md")
    names = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert names == ["y"] and not (repo / "x").exists()
    assert (repo / "stray").exists()


def test_untracked_file_never_committed_and_changelog_in_commit(tmp_path):
    repo = _repo(tmp_path)
    (repo / "stray").write_text("untracked\n")
    p = proposals.new("fix", "Change x", "quorra", "m", "r", "t", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", pdir=tmp_path / "p")
    proposals.apply(p.stem, pdir=tmp_path / "p", repos={"quorra": repo}, run_tests=False, changelog=repo / "docs" / "changelog.md")
    names = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert sorted(names) == ["docs/changelog.md", "x"]


def test_bad_pid_rejected(tmp_path):
    import pytest
    for fn in (lambda: proposals.reject("../x", "r", pdir=tmp_path), lambda: proposals.apply("a/b", pdir=tmp_path)):
        with pytest.raises(ValueError):
            fn()
