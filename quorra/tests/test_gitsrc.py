import subprocess

import gitsrc
from gitfix import commit, make_repo


def test_list_and_read(tmp_path):
    repo, sha = make_repo(tmp_path, {"src/a.php": "<?php\nclass A {}\n", "dir with space/b.ts": "export const B = 1;\n"})
    assert sorted(gitsrc.list_files(repo, sha)) == ["dir with space/b.ts", "src/a.php"]
    got = dict(gitsrc.read_files(repo, sha, ["src/a.php", "missing.php", "dir with space/b.ts"]))
    assert got["src/a.php"].startswith(b"<?php")
    assert got["missing.php"] is None
    assert got["dir with space/b.ts"] == b"export const B = 1;\n"


def test_changed_files_and_resolve(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1", "b.php": "1"})
    s2 = commit(repo, {"a.php": "2", "b.php": None, "c.php": "3"})
    assert sorted(gitsrc.changed_files(repo, s1, s2)) == [("A", "c.php"), ("D", "b.php"), ("M", "a.php")]
    assert gitsrc.resolve(repo, "master") == s2


def test_grep_at_sha_ignores_working_tree(tmp_path):
    repo, sha = make_repo(tmp_path, {"x.php": "function calculateFeaturedofferPrice() {}\n"})
    (repo / "x.php").write_text("nothing here\n")  # uncommitted edit must not matter
    hits = gitsrc.grep(repo, sha, "featuredofferprice")
    assert hits == [{"file": "x.php", "line": 1, "text": "function calculateFeaturedofferPrice() {}"}]


def test_grep_no_match_returns_empty(tmp_path):
    repo, sha = make_repo(tmp_path, {"x.php": "a\n"})
    assert gitsrc.grep(repo, sha, "zzz") == []


def test_recent_files(tmp_path):
    repo, sha = make_repo(tmp_path, {"a.php": "1"})
    assert gitsrc.recent_files(repo, sha) == {"a.php"}


def test_resolve_prefers_origin_over_local(tmp_path):
    # Create origin repo with initial commit
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "-C", str(origin), "init", "-q", "-b", "master"], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@t"], check=True)
    (origin / "a.txt").write_text("1")
    subprocess.run(["git", "-C", str(origin), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-q", "-m", "init"], check=True)
    origin_sha1 = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    # Clone to get a local copy
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)

    # Add new commit to origin
    (origin / "b.txt").write_text("2")
    subprocess.run(["git", "-C", str(origin), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-q", "-m", "new"], check=True)
    origin_sha2 = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    # Fetch in clone (don't pull, so local master is still at sha1)
    gitsrc.fetch(clone)

    # resolve should return origin's sha, not local master's sha
    resolved = gitsrc.resolve(clone, "master")
    assert resolved == origin_sha2
    assert resolved != origin_sha1


def test_resolve_raises_on_unknown_ref(tmp_path):
    repo, _ = make_repo(tmp_path, {"a.txt": "1"})
    try:
        gitsrc.resolve(repo, "nonexistent-ref-xyz")
        assert False, "should raise GitError"
    except gitsrc.GitError as e:
        assert "cannot resolve" in str(e)


def test_fetch_raises_on_no_origin(tmp_path):
    repo, _ = make_repo(tmp_path, {"a.txt": "1"})
    try:
        gitsrc.fetch(repo)
        assert False, "should raise GitError"
    except gitsrc.GitError as e:
        assert "fetch" in str(e).lower()


def test_read_files_raises_on_non_repo(tmp_path):
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    try:
        list(gitsrc.read_files(not_a_repo, "abc", ["x.txt"]))
        assert False, "should raise GitError"
    except gitsrc.GitError:
        pass


def test_read_files_returns_none_for_directory(tmp_path):
    repo, sha = make_repo(tmp_path, {"dir/a.txt": "content"})
    got = dict(gitsrc.read_files(repo, sha, ["dir", "dir/a.txt"]))
    assert got["dir"] is None
    assert got["dir/a.txt"] == b"content"


def test_read_files_returns_none_for_path_with_newline(tmp_path):
    repo, sha = make_repo(tmp_path, {"a.txt": "content"})
    got = dict(gitsrc.read_files(repo, sha, ["a.txt", "bad\npath"]))
    assert got["a.txt"] == b"content"
    assert got["bad\npath"] is None


def test_changed_files_handles_rename(tmp_path):
    repo, s1 = make_repo(tmp_path, {"old.php": "1"})
    # Use git mv to actually rename
    subprocess.run(["git", "-C", str(repo), "mv", "old.php", "new.php"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rename"], check=True)
    s2 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    changes = sorted(gitsrc.changed_files(repo, s1, s2))
    # Without --no-renames, git would report as rename. With --no-renames (used in our code), it's D + A
    assert ("A", "new.php") in changes
    assert ("D", "old.php") in changes


def test_recent_files_excludes_old_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "master"], check=True)
    # Create old commit
    old_sha = commit(repo, {"old.php": "1"}, "old", date="2020-01-01T00:00:00")
    # Create new commit
    new_sha = commit(repo, {"new.php": "2"}, "new")
    # recent_files with default 6.months should only see new.php
    recent = gitsrc.recent_files(repo, new_sha)
    assert "new.php" in recent
    assert "old.php" not in recent


def test_grep_handles_colon_in_filename(tmp_path):
    repo, sha = make_repo(tmp_path, {"file:with:colons.php": "function test() {}\n"})
    hits = gitsrc.grep(repo, sha, "function")
    assert len(hits) == 1
    assert hits[0]["file"] == "file:with:colons.php"


def test_grep_handles_form_feed_in_text(tmp_path):
    repo, sha = make_repo(tmp_path, {"x.php": "line1\fline2\n"})
    hits = gitsrc.grep(repo, sha, "line1")
    assert len(hits) == 1
    assert hits[0]["line"] == 1
