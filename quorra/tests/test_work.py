import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gitfix import _git, commit
import work

PY = sys.executable
FAKE_PW = ("import sys, pathlib; o = pathlib.Path(sys.argv[sys.argv.index('--output') + 1]); "
           "o.mkdir(parents=True, exist_ok=True); (o / 'shot.png').write_bytes(b'png')")
CHECKS = {
    "profiles": {
        "php": {
            "install": [PY, "-c", "print('installed')"],
            "checks": [
                {"name": "lint", "files": "\\.php$",
                 "cmd": [PY, "-c", "import sys; sys.exit(1 if 'BROKEN' in open(sys.argv[1]).read() else 0)", "{file}"]},
                {"name": "unit", "cmd": [PY, "-c", "print('unit ok')"], "needs": ["deps"]},
                {"name": "stan", "cmd": ["vendor/bin/phpstan", "{files}"], "files": "\\.php$", "requires": "vendor/bin/phpstan"},
                {"name": "integration", "cmd": [PY, "-c", "print(1)"], "needs": ["db"]},
            ],
        },
        "ts": {
            "install": [PY, "-c", "import sys; sys.exit(3)"],
            "checks": [{"name": "tsc", "cmd": [PY, "-c", "print(1)"], "needs": ["deps"]}],
            "panel": {"start": [PY, "-m", "http.server", "{port}", "--bind", "127.0.0.1"], "port": 0,
                      "ready_timeout": 20, "playwright": [PY, "-c", FAKE_PW]},
        },
    },
    "repos": {"api-app": {"profile": "php"}, "web-app": {"profile": "ts"}},
}


def _src(tmp_path, name, files):
    r = tmp_path / "src" / name
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "master")
    return r, commit(r, files, "init")


def _refs(repo):
    return _git(repo, "for-each-ref") + _git(repo, "status", "--porcelain")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    repos, srcs = {}, {}
    for name, lang, files in [("api-app", "php", {"src/A.php": "<?php\nclass A {}\n"}),
                              ("team", "php", {"src/Ref/Testing/bootstrap.php": "<?php\n"}),
                              ("web-app", "ts", {"src/a.ts": "export const a = 1;\n"})]:
        path, sha = _src(tmp_path, name, files)
        repos[name] = {"path": str(path), "gitlab": f"team/{name}", "ref": "master", "lang": lang}
        srcs[name] = (path, sha)
    (tmp_path / "repos.json").write_text(json.dumps(repos))
    (tmp_path / "checks.json").write_text(json.dumps(CHECKS))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    monkeypatch.setenv("QUORRA_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("QUORRA_CHECKS", str(tmp_path / "checks.json"))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "index"))
    return srcs


def test_prepare_refuses_framework_and_bad_slug(ws):
    with pytest.raises(work.WorkError, match="not a repo quorra may change"):
        work.prepare("team", "x")
    with pytest.raises(work.WorkError, match="slug"):
        work.prepare("api-app", "Bad Slug")


def test_prepare_clones_branches_and_leaves_source_untouched(ws):
    src, sha = ws["api-app"]
    before = _refs(src)
    r = work.prepare("api-app", "fix-a")
    clone = work.clone_path("api-app")
    assert r["branch"] == "quorra/fix-a" and r["base"] == sha
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "quorra/fix-a"
    assert _git(clone, "remote", "get-url", "origin") == "https://gitlab.example.com/team/api-app.git"
    assert _git(clone, "remote", "get-url", "local") == str(src)
    hook = clone / ".git" / "hooks" / "pre-push"
    assert hook.exists() and os.stat(hook).st_mode & stat.S_IXUSR
    assert r["install"]["ok"] is True
    assert not work.clone_path("team").exists()  # the framework is not cloned: PHP tests cannot run locally
    assert _refs(src) == before


def test_prepare_resumes_existing_branch_and_honours_base(ws):
    src, sha = ws["api-app"]
    sha2 = commit(src, {"src/B.php": "<?php\n"}, "second")
    r = work.prepare("api-app", "fix-b", base=sha)
    assert r["base"] == sha
    commit(work.clone_path("api-app"), {"src/C.php": "<?php\n"}, "work")
    again = work.prepare("api-app", "fix-b", install=False)
    assert again["base"] == sha and again["branch"] == "quorra/fix-b"
    assert work.status("api-app")["commits"] == 1
    other = work.prepare("api-app", "fix-c")
    assert other["base"] == sha2


def test_install_failure_is_recorded_not_raised(ws):
    r = work.prepare("web-app", "ui")
    assert r["install"]["ok"] is False and "exit 3" in r["install"]["reason"]


def test_status_reports_branch_dirty_and_install(ws):
    work.prepare("api-app", "fix-d", install=False)
    clone = work.clone_path("api-app")
    (clone / "src/New.php").write_text("<?php\n")
    s = work.status("api-app")
    assert s["branch"] == "quorra/fix-d" and s["commits"] == 0
    assert s["dirty"] == ["src/New.php"] and s["install"]["ok"] is False


def test_status_handles_files_with_spaces(ws):
    """Files with spaces in names are returned unquoted and complete."""
    work.prepare("api-app", "fix-spaces", install=False)
    clone = work.clone_path("api-app")
    (clone / "src/a b.php").write_text("<?php\n")
    s = work.status("api-app")
    assert s["dirty"] == ["src/a b.php"]


def test_status_preserves_modified_tracked_files(ws):
    """Modified tracked files appear correctly without losing first character."""
    work.prepare("api-app", "fix-tracked", install=False)
    clone = work.clone_path("api-app")
    # src/A.php was committed during prepare
    (clone / "src/A.php").write_text("<?php\nmodified\n")
    s = work.status("api-app")
    assert s["dirty"] == ["src/A.php"]


def test_prepare_refuses_switching_with_uncommitted_changes(ws):
    """Cannot switch branches when clone has uncommitted changes."""
    work.prepare("api-app", "fix-e", install=False)
    clone = work.clone_path("api-app")
    (clone / "src/Uncommitted.php").write_text("<?php\n")
    with pytest.raises(work.WorkError, match="uncommitted changes"):
        work.prepare("api-app", "fix-f")


def test_prepare_allows_dirty_tree_on_same_branch(ws):
    """Re-preparing the same branch with dirty tree is allowed."""
    work.prepare("api-app", "fix-g", install=False)
    clone = work.clone_path("api-app")
    (clone / "src/Uncommitted.php").write_text("<?php\n")
    # Re-preparing the same branch should succeed
    r = work.prepare("api-app", "fix-g", install=False)
    assert r["branch"] == "quorra/fix-g"


def test_prepare_handles_corrupt_quorra_json(ws):
    """Corrupt quorra.json raises WorkError, not JSONDecodeError."""
    work.prepare("api-app", "fix-h", install=False)
    clone = work.clone_path("api-app")
    meta_file = clone / ".git" / "quorra.json"
    meta_file.write_text("{ invalid json")
    with pytest.raises(work.WorkError, match="corrupt quorra.json"):
        work.prepare("api-app", "fix-i")


def _by_name(res):
    return {r["name"]: r for r in res["results"]}


def test_check_reports_passed_failed_and_not_run(ws):
    work.prepare("api-app", "chk")
    clone = work.clone_path("api-app")
    commit(clone, {"src/Bad.php": "<?php BROKEN\n", "src/Good.php": "<?php\n"}, "w")
    res = work.check("api-app")
    r = _by_name(res)
    assert res["files"] == ["src/Bad.php", "src/Good.php"]
    assert r["lint"]["status"] == "failed" and "src/Bad.php" in r["lint"]["reason"]
    assert r["unit"]["status"] == "passed"
    assert r["stan"]["status"] == "not_run" and "vendor/bin/phpstan" in r["stan"]["reason"]
    assert r["integration"]["status"] == "not_run" and "database" in r["integration"]["reason"]
    saved = json.loads((clone / ".git" / "quorra-checks.json").read_text())
    assert saved["results"] == res["results"]


def test_check_without_install_and_without_files(ws):
    work.prepare("api-app", "chk2", install=False)
    r = _by_name(work.check("api-app"))
    assert r["unit"]["status"] == "not_run" and "dependencies not installed" in r["unit"]["reason"]
    assert r["lint"]["status"] == "not_run" and "no changed files" in r["lint"]["reason"]


def test_check_includes_untracked_files_and_only_filter(ws):
    work.prepare("api-app", "chk3")
    (work.clone_path("api-app") / "src/Untracked.php").write_text("<?php BROKEN\n")
    res = work.check("api-app", only=["lint"])
    assert [x["name"] for x in res["results"]] == ["lint"] and res["results"][0]["status"] == "failed"


def test_check_output_goes_through_scrub(ws, monkeypatch):
    monkeypatch.setattr(work, "scrub", lambda s: "SCRUBBED")
    work.prepare("api-app", "chk4")
    commit(work.clone_path("api-app"), {"src/Bad.php": "<?php BROKEN\n"}, "w")
    assert _by_name(work.check("api-app"))["lint"]["output"] == "SCRUBBED"


def test_real_checks_config_is_well_formed():
    cfg = json.loads((work.qpaths.REPO / "config" / "checks.example.json").read_text())
    assert set(cfg["repos"]) == set(work.WRITABLE)
    for name, entry in cfg["repos"].items():
        prof = cfg["profiles"][entry["profile"]]
        assert (prof["install"] or prof.get("install_note")) and prof["checks"], name
        for c in prof["checks"]:
            assert c["name"] and c["cmd"] and set(c.get("needs", [])) <= {"deps", "db", "docker"}, (name, c)
    assert cfg["repos"]["web-app"]["profile"] == "ts" and cfg["profiles"]["ts"]["panel"]["port"] == 3333


def test_check_placeholder_without_files_pattern(ws):
    """Check with {file} placeholder but no files pattern should return not_run."""
    work.prepare("api-app", "chk5", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/A.php": "<?php\n"}, "w")
    # Create a check with {file} but no files pattern in repo_checks
    checks_cfg = json.loads(work.qpaths.checks_path().read_text())
    checks_cfg["profiles"]["php"]["checks"].append({"name": "no-pattern", "cmd": ["echo", "{file}"]})
    work.qpaths.checks_path().write_text(json.dumps(checks_cfg))
    r = _by_name(work.check("api-app"))
    assert r["no-pattern"]["status"] == "not_run" and "placeholder without a files pattern" in r["no-pattern"]["reason"]


def test_check_nothing_to_run(ws):
    """Check with {files} but no matching files should return not_run."""
    work.prepare("api-app", "chk6", install=False)
    clone = work.clone_path("api-app")
    # No files committed, so no changed files
    checks_cfg = json.loads(work.qpaths.checks_path().read_text())
    checks_cfg["profiles"]["php"]["checks"].append({"name": "empty-run", "cmd": ["echo", "{files}"], "files": "\\.xyz$"})
    work.qpaths.checks_path().write_text(json.dumps(checks_cfg))
    r = _by_name(work.check("api-app"))
    assert r["empty-run"]["status"] == "not_run" and "no changed files of this kind" in r["empty-run"]["reason"]


def test_check_no_tests_ran(ws):
    """Check that exits 0 but output matches no_tests regex returns not_run."""
    work.prepare("api-app", "chk7", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/A.php": "<?php\n"}, "w")
    checks_cfg = json.loads(work.qpaths.checks_path().read_text())
    checks_cfg["profiles"]["php"]["checks"].append({
        "name": "notests", "cmd": [PY, "-c", "print('No tests executed'); exit(0)"],
        "no_tests": "No tests executed"
    })
    work.qpaths.checks_path().write_text(json.dumps(checks_cfg))
    r = _by_name(work.check("api-app"))
    assert r["notests"]["status"] == "not_run" and "no tests ran" in r["notests"]["reason"]


def test_check_oserror_on_command(ws):
    """Non-executable file used as command should return not_run with OSError reason."""
    work.prepare("api-app", "chk8", install=False)
    clone = work.clone_path("api-app")
    # Create a directory file and use it as a command - this will cause OSError when trying to execute
    badcmd = clone / "badcmd"
    badcmd.mkdir()
    commit(clone, {"src/A.php": "<?php\n"}, "w")
    checks_cfg = json.loads(work.qpaths.checks_path().read_text())
    checks_cfg["profiles"]["php"]["checks"].append({
        "name": "badcmd", "cmd": ["./badcmd"], "requires": "badcmd"
    })
    work.qpaths.checks_path().write_text(json.dumps(checks_cfg))
    r = _by_name(work.check("api-app"))
    assert r["badcmd"]["status"] == "not_run" and "cannot run" in r["badcmd"]["reason"]


def test_check_file_with_spaces_as_single_arg(ws):
    """File with spaces in name should be passed as a single argument to {file} check."""
    work.prepare("api-app", "chk9", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/File With Spaces.php": "<?php BROKEN\n"}, "w")
    res = work.check("api-app")
    r = _by_name(res)
    assert res["files"] == ["src/File With Spaces.php"]
    assert r["lint"]["status"] == "failed" and "src/File With Spaces.php" in r["lint"]["reason"]


def test_patch_writes_commits_and_refuses_dirty_tree(ws, tmp_path):
    work.prepare("api-app", "pat", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/P.php": "<?php\n// patched\n"}, "add P")
    out = tmp_path / "out" / "api-app.patch"
    r = work.patch("api-app", out)
    assert r["commits"] == 1 and "patched" in out.read_text() and "Subject: [PATCH] add P" in out.read_text()
    (clone / "src/P.php").write_text("<?php\n// dirty\n")
    with pytest.raises(work.WorkError, match="uncommitted"):
        work.patch("api-app", out)


def test_patch_refuses_secret_lines_without_printing_them(ws, tmp_path, monkeypatch):
    work.prepare("api-app", "sec", install=False)
    commit(work.clone_path("api-app"), {"src/S.php": "<?php\n$k = 'SECRETLINE';\n"}, "s")
    monkeypatch.setattr(work, "scrub", lambda s: s.replace("SECRETLINE", "<redacted>"))
    with pytest.raises(work.WorkError) as e:
        work.patch("api-app", tmp_path / "x.patch")
    assert "possible secret" in str(e.value) and "SECRETLINE" not in str(e.value)
    assert not (tmp_path / "x.patch").exists()


def test_diff_includes_uncommitted_changes(ws):
    work.prepare("api-app", "dif", install=False)
    (work.clone_path("api-app") / "src/A.php").write_text("<?php\nclass A { public $x; }\n")
    assert "public $x" in work.diff("api-app")


def test_no_push_path_exists(ws, tmp_path):
    with pytest.raises(SystemExit) as e:
        work.main(["push", "api-app"])
    assert e.value.code == 2
    work.prepare("api-app", "nopush", install=False)
    clone = work.clone_path("api-app")
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(clone, "remote", "set-url", "origin", str(bare))
    # the named remote has a push URL that cannot work
    p = subprocess.run(["git", "-C", str(clone), "push", "origin", "quorra/nopush"], capture_output=True, text=True)
    assert p.returncode != 0
    # a push straight to a path skips the push URL, and the pre-push hook refuses it
    p = subprocess.run(["git", "-C", str(clone), "push", str(bare), "quorra/nopush"], capture_output=True, text=True)
    assert p.returncode != 0 and "never push" in p.stderr
    assert _git(bare, "branch", "--list") == ""


def test_cli_exit_codes(ws):
    assert work.main(["prepare", "team", "x"]) == 2
    assert work.main(["prepare", "api-app", "cli"]) == 0
    commit(work.clone_path("api-app"), {"src/Bad.php": "<?php BROKEN\n"}, "w")
    assert work.main(["check", "api-app"]) == 1
    assert work.main(["status", "api-app"]) == 0


def test_main_scrubs_workerror_with_credentials(ws, monkeypatch, capsys):
    """WorkError text with credentials is scrubbed before printing to stderr."""
    work.prepare("api-app", "cred", install=False)
    monkeypatch.setattr(work, "status", lambda name:
        (_ for _ in ()).throw(work.WorkError("fetch failed for https://user:tok3n@git.example/x.git")))
    ret = work.main(["status", "api-app"])
    assert ret == 2
    captured = capsys.readouterr()
    assert "tok3n" not in captured.err
    assert "<redacted>" in captured.err


def test_diff_includes_untracked_files(ws):
    """diff includes untracked files as diff-style blocks."""
    work.prepare("api-app", "dif2", install=False)
    clone = work.clone_path("api-app")
    (clone / "src/Untracked.php").write_text("<?php\necho 'new';\n")
    d = work.diff("api-app")
    assert "Untracked.php" in d and "echo 'new'" in d


def test_patch_refuses_zero_commits(ws, tmp_path):
    """patch with zero commits raises WorkError."""
    work.prepare("api-app", "zero", install=False)
    with pytest.raises(work.WorkError, match="no commits to patch"):
        work.patch("api-app", tmp_path / "out.patch")


def test_patch_refuses_real_credentials_without_monkeypatch(ws, tmp_path):
    """patch detects and refuses real password credentials like $password = 'hunter2hunter2'."""
    work.prepare("api-app", "cred", install=False)
    commit(work.clone_path("api-app"), {"src/Config.php": "<?php\n$password = 'hunter2hunter2';\n"}, "config")
    with pytest.raises(work.WorkError, match="possible secret"):
        work.patch("api-app", tmp_path / "out.patch")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _set_port(tmp_path, port):
    cfg = json.loads((tmp_path / "checks.json").read_text())
    cfg["profiles"]["ts"]["panel"]["port"] = port
    (tmp_path / "checks.json").write_text(json.dumps(cfg))


def _open(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_panel_up_and_down(ws, tmp_path):
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "panel", install=False)
    r = work.panel_up()
    try:
        assert r["url"] == f"http://localhost:{port}/" and _open(port)
        assert work.panel_up()["pid"] == r["pid"]  # already running: reuse
    finally:
        assert work.panel_down()["stopped"] is True
    assert not _open(port)
    assert work.panel_down()["stopped"] is False


def test_panel_test_writes_config_and_collects_screenshots(ws, tmp_path):
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "pt", install=False)
    clone = work.clone_path("web-app")
    out = tmp_path / "shots"
    work.panel_up()
    try:
        r = work.panel_test("tests/scenarios/feature.spec.ts", out)
    finally:
        work.panel_down()
    assert r["status"] == "passed" and len(r["screenshots"]) == 1
    shot = Path(r["screenshots"][0])
    assert shot.name == "shot.png" and shot.parent.parent == out and shot.exists()
    cfg = (clone / ".quorra" / "playwright.config.ts").read_text()
    assert "baseURL: 'http://localhost:" in cfg and "screenshot: 'on'" in cfg
    assert ".quorra/" in (clone / ".git" / "info" / "exclude").read_text()
    assert "?? .quorra" not in _git(clone, "status", "--porcelain")


def test_panel_needs_a_prepared_front_end(ws):
    with pytest.raises(work.WorkError, match="prepare"):
        work.panel_up()


def test_panel_stale_pid_file(ws, tmp_path):
    """Stale pid file with wrong start time is ignored without signalling."""
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "stale", install=False)
    clone = work.clone_path("web-app")
    pf = clone / ".git" / "quorra-panel.pid"
    # Write a stale pid file with test process's own pid but wrong start time
    wrong_start = "Mon Jan  1 00:00:00 1970"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps({"pid": os.getpid(), "port": port, "start": wrong_start}))
    # panel_down should return stopped False without signalling
    r = work.panel_down()
    assert r["stopped"] is False and "stale" in r.get("reason", "").lower()
    # Test process should still be alive
    assert os.kill(os.getpid(), 0) is None
    # Pid file should be deleted
    assert not pf.exists()


def test_panel_child_ignores_sigterm(ws, tmp_path):
    """Child process that ignores SIGTERM is killed by SIGKILL."""
    port = _free_port()
    _set_port(tmp_path, port)
    # Create script that spawns a child ignoring SIGTERM and serves http.server
    IGNORE_SIGTERM = f"""import subprocess, sys, signal
subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)'])
import http.server, socketserver
port = int(sys.argv[1])
handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(('127.0.0.1', port), handler) as httpd:
    print('Serving', flush=True)
    httpd.serve_forever()
"""
    work.prepare("web-app", "sigterm", install=False)
    clone = work.clone_path("web-app")
    # Modify the ts panel config to use our script
    cfg = json.loads(work.qpaths.checks_path().read_text())
    cfg["profiles"]["ts"]["panel"]["start"] = [PY, "-c", IGNORE_SIGTERM, "{port}"]
    work.qpaths.checks_path().write_text(json.dumps(cfg))
    # Start the panel
    r = work.panel_up()
    pgid = r["pid"]
    try:
        assert _open(port), "Panel should be running"
        time.sleep(0.5)  # let the leader spawn its SIGTERM-ignoring child
        down = work.panel_down()
        assert down["stopped"] is True
        assert work._group_alive(pgid) == []  # nobody left in the group, including the child
        assert not _open(port)
    finally:
        for member in work._group_alive(pgid):
            os.kill(member, signal.SIGKILL)


def test_panel_port_closed_starts_fresh(ws, tmp_path):
    """Valid pid but closed port causes fresh start."""
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "portclosed", install=False)
    clone = work.clone_path("web-app")
    # Start the panel
    r1 = work.panel_up()
    pid1 = r1["pid"]
    try:
        # Kill the process but leave pid file intact (simulating crash)
        os.killpg(pid1, signal.SIGKILL)
        time.sleep(0.5)
        # Call panel_up again - should detect closed port and start fresh
        r2 = work.panel_up()
        assert r2["pid"] != pid1, "Should start a new process with different pid"
        assert _open(port), "New panel should be running"
        # Verify it reused the port
        assert r2["url"] == f"http://localhost:{port}/"
    finally:
        work.panel_down()


def test_push_is_locked_even_when_tooling_redirects_hooks(ws, tmp_path):
    work.prepare("api-app", "lock", install=False)
    clone = work.clone_path("api-app")
    _git(clone, "config", "core.hooksPath", ".husky")  # what husky install does
    work.prepare("api-app", "lock")  # an install run re-locks
    assert _git(clone, "config", "--get", "core.hooksPath") == str(clone / ".git" / "hooks")
    assert _git(clone, "config", "--get", "remote.origin.pushurl") == work.NO_PUSH_URL
    assert _git(clone, "config", "--get", "remote.local.pushurl") == work.NO_PUSH_URL
    p = subprocess.run(["git", "-C", str(clone), "push", "origin", "quorra/lock"], capture_output=True, text=True)
    assert p.returncode != 0


def test_docker_checks_and_repos_without_install(ws, tmp_path):
    cfg = json.loads((tmp_path / "checks.json").read_text())
    cfg["profiles"]["php"]["install"] = None
    cfg["profiles"]["php"]["install_note"] = "tests need the Docker test image"
    cfg["profiles"]["php"]["checks"].append({"name": "unit-docker", "cmd": [PY, "-c", "print(1)"], "needs": ["deps", "docker"]})
    (tmp_path / "checks.json").write_text(json.dumps(cfg))
    r = work.prepare("api-app", "dock")
    assert r["install"] == {"ok": False, "reason": "tests need the Docker test image"}
    res = {x["name"]: x for x in work.check("api-app")["results"]}
    assert res["unit-docker"]["status"] == "not_run" and "Docker" in res["unit-docker"]["reason"]


def test_check_refuses_implausible_untracked_files(ws, monkeypatch):
    work.prepare("api-app", "many", install=False)
    clone = work.clone_path("api-app")
    monkeypatch.setattr(work, "MAX_UNTRACKED", 3)
    for i in range(5):
        (clone / f"junk{i}.php").write_text("<?php\n")
    with pytest.raises(work.WorkError, match="untracked files"):
        work.check("api-app")


def _push(clone, *args):
    return subprocess.run(["git", "-C", str(clone), "push", "--no-verify", *args], capture_output=True, text=True,
                          timeout=60)


def test_push_is_impossible_even_with_no_verify_url_or_path(ws):
    src, _sha = ws["api-app"]
    work.prepare("api-app", "np", install=False)
    clone = work.clone_path("api-app")
    before = _refs(src)
    p = _push(clone, str(src), "HEAD:refs/heads/x")
    assert p.returncode != 0 and "no-push" in p.stderr
    assert "x" not in _git(src, "branch", "--list").split()
    assert _refs(src) == before
    p = _push(clone, "https://gitlab.example.com/x/y.git", "HEAD")
    assert p.returncode != 0 and "no-push" in p.stderr
    p = _push(clone, "git@gitlab.example.com:x/y.git", "HEAD")
    assert p.returncode != 0 and "no-push" in p.stderr


def test_read_commands_relock_hooks_path(ws):
    work.prepare("api-app", "relock", install=False)
    clone = work.clone_path("api-app")
    hooks = str(clone / ".git" / "hooks")
    for call in (lambda: work.status("api-app"), lambda: work.check("api-app"), lambda: work.diff("api-app")):
        _git(clone, "config", "core.hooksPath", ".husky")
        call()
        assert _git(clone, "config", "--get", "core.hooksPath") == hooks
    commit(clone, {"src/R.php": "<?php\n"}, "r")
    _git(clone, "config", "core.hooksPath", ".husky")
    work.patch("api-app", clone.parent / "r.patch")
    assert _git(clone, "config", "--get", "core.hooksPath") == hooks


def test_interrupted_prepare_writes_meta_and_relocks(ws, monkeypatch):
    def boom(clone, cmd):
        _git(clone, "config", "core.hooksPath", ".husky")
        raise KeyboardInterrupt
    real_install = work._install
    monkeypatch.setattr(work, "_install", boom)
    with pytest.raises(KeyboardInterrupt):
        work.prepare("api-app", "intr")
    clone = work.clone_path("api-app")
    assert _git(clone, "config", "--get", "core.hooksPath") == str(clone / ".git" / "hooks")
    meta = json.loads((clone / ".git" / "quorra.json").read_text())
    assert "quorra/intr" in meta["branches"]
    monkeypatch.setattr(work, "_install", real_install)
    assert work.status("api-app")["branch"] == "quorra/intr"


def test_prepare_recovers_branch_without_meta(ws):
    src, sha = ws["api-app"]
    work.prepare("api-app", "rec", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/W.php": "<?php\n"}, "work")
    (clone / ".git" / "quorra.json").unlink()
    _git(clone, "checkout", "--quiet", "--detach", "HEAD~1")
    r = work.prepare("api-app", "rec", install=False)
    assert r["recovered"] is True and r["base"] == sha and r["branch"] == "quorra/rec"
    assert work.status("api-app")["commits"] == 1
    assert "recovered" not in work.prepare("api-app", "rec", install=False)


def test_patch_scan_catches_url_userinfo(ws, tmp_path):
    work.prepare("api-app", "ui", install=False)
    commit(work.clone_path("api-app"), {"src/U.php": "<?php\n// https://deploytoken@git.example/x.git\n"}, "u")
    with pytest.raises(work.WorkError, match="possible secret"):
        work.patch("api-app", tmp_path / "u.patch")


def test_name_validated_against_writable(ws):
    for fn in (work.status, work.check, work.diff):
        with pytest.raises(work.WorkError, match="not a repo quorra may change"):
            fn("../api-app")
    with pytest.raises(work.WorkError, match="not a repo quorra may change"):
        work.patch("../api-app", Path("x.patch"))


def test_check_only_unknown_name(ws):
    work.prepare("api-app", "unk", install=False)
    with pytest.raises(work.WorkError, match="known checks: lint, unit, stan, integration"):
        work.check("api-app", only=["nope"])


def test_odd_file_names_in_changed_and_dirty(ws):
    work.prepare("api-app", "odd", install=False)
    clone = work.clone_path("api-app")
    names = ['src/a"b.php', "src/نام.php"]
    for n in names:
        (clone / n).write_text("<?php\n")
    assert sorted(work.status("api-app")["dirty"]) == sorted(names)
    assert work.check("api-app")["files"] == sorted(names)
    # a rename lists its old path as a separate NUL entry
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "add")
    _git(clone, "mv", "src/نام.php", "src/دیگر.php")
    assert work.status("api-app")["dirty"] == ["src/دیگر.php"]


def test_panel_down_keeps_pid_file_on_permission_error(ws, tmp_path, monkeypatch):
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "perm", install=False)
    pf = work.clone_path("web-app") / ".git" / "quorra-panel.pid"
    r = work.panel_up()
    try:
        real = os.killpg
        monkeypatch.setattr(work.os, "killpg", lambda *a: (_ for _ in ()).throw(PermissionError()))
        assert work.panel_down()["stopped"] is False and pf.exists()
        monkeypatch.setattr(work.os, "killpg", real)
    finally:
        assert work.panel_down()["stopped"] is True
    assert not pf.exists() and r["pid"]


def test_panel_log_is_private(ws, tmp_path):
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "log", install=False)
    log = work.clone_path("web-app") / ".git" / "quorra-panel.log"
    try:
        work.panel_up()
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
    finally:
        work.panel_down()


SLOW_SERVER = ("import runpy, sys, time; time.sleep(3); sys.argv = ['http.server', sys.argv[1], '--bind', '127.0.0.1']; "
               "runpy.run_module('http.server', run_name='__main__')")


def test_panel_up_waits_for_a_panel_that_is_still_starting(ws, tmp_path):
    import threading
    port = _free_port()
    _set_port(tmp_path, port)
    cfg = json.loads(work.qpaths.checks_path().read_text())
    cfg["profiles"]["ts"]["panel"]["start"] = [PY, "-c", SLOW_SERVER, "{port}"]
    work.qpaths.checks_path().write_text(json.dumps(cfg))
    work.prepare("web-app", "slow", install=False)
    pf = work.clone_path("web-app") / ".git" / "quorra-panel.pid"
    first = {}
    t = threading.Thread(target=lambda: first.update(work.panel_up()))
    t.start()
    try:
        deadline = time.time() + 10
        while not pf.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert pf.exists() and not _open(port)
        pid = json.loads(pf.read_text())["pid"]
        second = work.panel_up()  # during the delay
        assert second["pid"] == pid and _open(port)
        t.join()
        assert first["pid"] == pid and json.loads(pf.read_text())["pid"] == pid
        assert len(work._group_alive(pid)) == 1
    finally:
        t.join()
        work.panel_down()


def test_panel_up_stops_a_panel_that_never_opens_its_port(ws, tmp_path):
    port = _free_port()
    _set_port(tmp_path, port)
    cfg = json.loads(work.qpaths.checks_path().read_text())
    cfg["profiles"]["ts"]["panel"].update(start=[PY, "-c", "import time; time.sleep(600)", "{port}"], ready_timeout=2)
    work.qpaths.checks_path().write_text(json.dumps(cfg))
    work.prepare("web-app", "hang", install=False)
    pf = work.clone_path("web-app") / ".git" / "quorra-panel.pid"
    import threading
    err = {}

    def first():
        try:
            work.panel_up()
        except work.WorkError as e:
            err["e"] = e
    t = threading.Thread(target=first)
    t.start()
    try:
        deadline = time.time() + 10
        while not pf.exists() and time.time() < deadline:
            time.sleep(0.1)
        pid = json.loads(pf.read_text())["pid"]
        t.join()
        assert "did not answer" in str(err["e"])
        assert work._group_alive(pid) == [] and not pf.exists()
    finally:
        t.join()
        for m in work._group_alive(pid):
            os.kill(m, signal.SIGKILL)


def test_panel_test_never_deletes_existing_files(ws, tmp_path):
    port = _free_port()
    _set_port(tmp_path, port)
    work.prepare("web-app", "keep", install=False)
    out = tmp_path / "panel"
    out.mkdir()
    (out / "old.txt").write_text("keep me")
    work.panel_up()
    try:
        r = work.panel_test("x.spec.ts", out)
        assert (out / "old.txt").read_text() == "keep me"
        assert Path(r["screenshots"][0]).parent.parent == out
        run = tmp_path / "run"
        run.mkdir()
        (run / "run.json").write_text("{}")
        with pytest.raises(work.WorkError, match="run.json"):
            work.panel_test("x.spec.ts", run)
        assert (run / "run.json").exists()
    finally:
        work.panel_down()


def test_panel_test_needs_a_running_panel(ws, tmp_path):
    _set_port(tmp_path, _free_port())
    work.prepare("web-app", "norun", install=False)
    with pytest.raises(work.WorkError, match="panel is not running; run work.py panel up"):
        work.panel_test("x.spec.ts", tmp_path / "o")


def test_panel_config_has_traces_off():
    assert "trace: 'off'" in work.PANEL_CONFIG


def test_check_records_only_and_dirty(ws):
    work.prepare("api-app", "chk-flags", install=False)
    clone = work.clone_path("api-app")
    commit(clone, {"src/Good.php": "<?php\n"}, "w")
    res = work.check("api-app")
    assert res["only"] is None and res["dirty"] is False
    (clone / "src" / "Wip.php").write_text("<?php\n")
    res = work.check("api-app", only=["lint"])
    assert res["only"] == ["lint"] and res["dirty"] is True
    saved = json.loads((clone / ".git" / "quorra-checks.json").read_text())
    assert saved["only"] == ["lint"] and saved["dirty"] is True
