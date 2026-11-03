import subprocess
from pathlib import Path

import pytest

import kb_git
from gitfix import _git, commit

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def kb(tmp_path):
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    commit(repo, {"features/demo/01.md": "# Demo\n\n## Part A\nold\n\n## Part B\nkeep\n", "README.md": "kb\n"}, "init")
    return repo


def test_branch_creates_and_reuses(kb):
    assert kb_git.branch(kb, "demo", "2026-09-30") == "quorra/demo-2026-09-30"
    assert _git(kb, "branch", "--show-current") == "quorra/demo-2026-09-30"
    _git(kb, "switch", "-q", "main")
    assert kb_git.branch(kb, "demo", "2026-09-30") == "quorra/demo-2026-09-30"


def test_branch_refuses_unrelated_tracked_changes_but_ignores_untracked(kb):
    (kb / "notes.txt").write_text("untracked is fine\n")
    (kb / "features/demo/01.md").write_text("# Demo\nedited\n")
    assert kb_git.branch(kb, "demo", "2026-09-30")
    _git(kb, "switch", "-q", "main")
    (kb / "README.md").write_text("changed\n")
    with pytest.raises(kb_git.KbGitError, match="README.md"):
        kb_git.branch(kb, "other", "2026-09-30")


def test_commit_only_the_feature_with_trailer(kb):
    kb_git.branch(kb, "demo", "2026-09-30")
    (kb / "features/demo/01.md").write_text("# Demo\n\n## Part A\nnew text\n\n## Part B\nkeep\n")
    (kb / "features/demo/02.md").write_text("# Two\n")
    (kb / "README.md").write_text("not mine\n")
    sha = kb_git.commit(kb, "demo", "demo: refresh")
    assert _git(kb, "show", "--name-only", "--format=", sha).split() == ["features/demo/01.md", "features/demo/02.md"]
    assert _git(kb, "log", "-1", "--format=%B", sha).strip().endswith("Co-Authored-By: quorra <noreply@example.com>")
    s = kb_git.summary(kb)
    assert s["branch"] == "quorra/demo-2026-09-30" and s["base"] == "main"
    f = {x["file"]: x for x in s["files"]}
    assert f["features/demo/01.md"]["headings"] == ["## Part A"] and f["features/demo/02.md"]["added"] == 1


def test_commit_refuses_on_main_and_on_secrets(kb):
    (kb / "features/demo/01.md").write_text("x\n")
    with pytest.raises(kb_git.KbGitError, match="quorra/"):
        kb_git.commit(kb, "demo", "m")
    kb_git.branch(kb, "demo", "2026-09-30")
    (kb / "features/demo/01.md").write_text("config: $dbPassword = 'hunter2hunter2';\n")
    with pytest.raises(kb_git.KbGitError, match=r"features/demo/01.md:1"):
        kb_git.commit(kb, "demo", "m")
    assert _git(kb, "diff", "--cached", "--name-only") == ""
    assert kb_git.commit(kb, "demo", "m", accept=["features/demo/01.md:1"])


def test_no_push_path_exists():
    src = (ROOT / "scripts" / "kb_git.py").read_text(encoding="utf-8")
    assert '"push"' not in src and "'push'" not in src
    with pytest.raises(SystemExit) as e:
        kb_git.main(["push"])
    assert e.value.code == 2


def test_parser_has_only_branch_commit_summary(monkeypatch):
    import argparse
    seen = {}
    orig = argparse.ArgumentParser.add_subparsers

    def spy(self, *a, **k):
        act = orig(self, *a, **k)
        seen["act"] = act
        return act
    monkeypatch.setattr(argparse.ArgumentParser, "add_subparsers", spy)
    with pytest.raises(SystemExit):
        kb_git.main(["--help"])
    assert set(seen["act"].choices) == {"branch", "commit", "summary"}


def test_commit_returns_the_kb_to_its_starting_branch(kb):
    kb_git.branch(kb, "demo", "2026-09-30")
    (kb / "features/demo/01.md").write_text("# Demo\nnew\n")
    sha, info = kb_git.commit_and_restore(kb, "demo", "demo: x")
    assert _git(kb, "branch", "--show-current") == "main"
    assert info["on_branch"] == "main" and info["commit_branch"] == "quorra/demo-2026-09-30"
    assert _git(kb, "branch", "--contains", sha).replace("*", "").split() == ["quorra/demo-2026-09-30"]
    assert kb_git.summary(kb)["branch"] == "quorra/demo-2026-09-30"


def test_commit_stays_when_it_cannot_switch_back(kb):
    kb_git.branch(kb, "demo", "2026-09-30")
    kb_git._return_file(kb).write_text("no-such-branch\n", encoding="utf-8")
    (kb / "features/demo/01.md").write_text("# Demo\nnew\n")
    _sha, info = kb_git.commit_and_restore(kb, "demo", "m")
    assert info["on_branch"] == "quorra/demo-2026-09-30" and "no-such-branch" in info["stayed"]


def test_commit_refuses_another_features_branch(kb):
    kb_git.branch(kb, "demo", "2026-09-30")
    (kb / "features/demo/01.md").write_text("x\n")
    with pytest.raises(kb_git.KbGitError, match="quorra/other-"):
        kb_git.commit(kb, "other", "m")


def test_commit_refuses_longer_named_features_branch(kb):
    (kb / "features/stock").mkdir(parents=True, exist_ok=True)
    kb_git.branch(kb, "stock-limit", "2026-09-30")
    (kb / "features/stock/01.md").write_text("x\n")
    with pytest.raises(kb_git.KbGitError, match="quorra/stock-"):
        kb_git.commit(kb, "stock", "m")
