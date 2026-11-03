#!/usr/bin/env python3
"""quorra's build workspace: its own clones under quorra-work/, one quorra/<slug> branch per task, the repo's checks,
patches, and the local app panel. the user's clones are only cloned and fetched from, never written. There is deliberately
no push command, and every work clone gets a pre-push hook that refuses.
  work.py prepare <repo> <slug> [--base SHA] [--no-install]
  work.py check <repo> [--only NAME ...]
  work.py status <repo>
  work.py diff <repo>
  work.py patch <repo> --out FILE
  work.py panel up | down | test <spec> --out DIR"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402
from qindex.redact import scrub  # noqa: E402

WRITABLE = ("api-app", "pricing", "shared-lib", "admin", "web-app", "notifier")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
GITLAB = os.environ.get("QUORRA_GITLAB_URL", "https://gitlab.example.com").rstrip("/")
_GITLAB_HOST = GITLAB.split("://", 1)[-1]
NO_PUSH_URL = "no-push://quorra-work-clones-never-push"
NO_PUSH_PREFIX = "no-push://blocked/"
GITLAB_PREFIXES = (GITLAB + "/", f"ssh://git@{_GITLAB_HOST}/", f"git@{_GITLAB_HOST}:")
HOOK = "#!/bin/sh\necho 'quorra work clones never push; pushing comes with sub-project 4 and needs the user.' >&2\nexit 1\n"
INSTALL_TIMEOUT = 1800
TAIL = 3000


class WorkError(RuntimeError):
    pass


def _run(cwd, *cmd, timeout=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def _git(cwd, *a: str) -> str:
    p = _run(cwd, "git", "-c", "core.quotepath=off", *a)
    if p.returncode != 0:
        raise WorkError(f"git {' '.join(a[:2])} failed: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def _gitz(cwd, *a: str) -> list[str]:
    """Run git and split NUL-separated output without stripping (names may hold quotes, spaces, any script)."""
    p = _run(cwd, "git", "-c", "core.quotepath=off", *a)
    if p.returncode != 0:
        raise WorkError(f"git {' '.join(a[:2])} failed: {p.stderr.strip()[:300]}")
    return [x for x in p.stdout.split("\0") if x]


def _dirty(clone: Path) -> list[str]:
    """Paths with uncommitted changes, from `git status --porcelain -z` (a rename lists the old path as its own entry,
    which is dropped here: the new path is the one that exists)."""
    out, entries, i = [], _gitz(clone, "status", "--porcelain", "-z", "--untracked-files=all"), 0
    while i < len(entries):
        e = entries[i]
        out.append(e[3:])
        i += 2 if ("R" in e[:2] or "C" in e[:2]) else 1
    return out


def _tail(text: str) -> str:
    return _safe(text[-TAIL:])


def _safe(text: str) -> str:
    """Scrub credentials and redact URL userinfo (which scrub may miss in some contexts)."""
    text = scrub(text)
    return re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", text)


def clone_path(name: str) -> Path:
    return qpaths.work_dir() / name


def load_checks() -> dict:
    return json.loads(qpaths.checks_path().read_text(encoding="utf-8"))


def repo_checks(name: str) -> dict:
    cfg = load_checks()
    entry = cfg["repos"].get(name)
    if entry is None:
        raise WorkError(f"no checks configured for {name} in {qpaths.checks_path()}")
    return {**cfg["profiles"][entry["profile"]], **{k: v for k, v in entry.items() if k != "profile"}}


def _meta_file(clone: Path) -> Path:
    return clone / ".git" / "quorra.json"


def _read_meta(clone: Path) -> dict:
    try:
        return json.loads(_meta_file(clone).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkError(f"{clone} is not a prepared work clone; run work.py prepare first") from None


def _write_meta(clone: Path, meta: dict) -> None:
    _meta_file(clone).write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _branch(clone: Path) -> str:
    return _git(clone, "rev-parse", "--abbrev-ref", "HEAD")


def _base(clone: Path) -> str:
    meta = _read_meta(clone)
    br = _branch(clone)
    if br not in meta.get("branches", {}):
        raise WorkError(f"{clone} is on {br!r}, which work.py did not create")
    return meta["branches"][br]["base"]


def indexed_sha(name: str) -> str | None:
    p = qpaths.index_path(name)
    if not p.exists():
        return None
    con = sqlite3.connect(p)
    try:
        row = con.execute("select value from meta where key='sha'").fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _ensure_clone(name: str) -> Path:
    cfg = qpaths.repo_cfg(name)
    dest = clone_path(name)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        p = _run(dest.parent, "git", "clone", "--quiet", "--no-checkout", str(cfg["path"]), str(dest))
        if p.returncode != 0:
            raise WorkError(f"clone of {name} failed: {p.stderr.strip()[:300]}")
        _git(dest, "remote", "rename", "origin", "local")
        _git(dest, "remote", "add", "origin", f"{GITLAB}/{cfg['gitlab']}.git")
    _lock_push(dest)
    _git(dest, "fetch", "--quiet", "local", "+refs/remotes/origin/*:refs/remotes/local-origin/*",
         "+refs/heads/*:refs/remotes/local/*")
    return dest


def _lock_push(clone: Path) -> None:
    """Make pushing impossible however it is asked for: with the pre-push hook, with --no-verify, with a remote name, with
    an explicit URL, or with the path of the user's clone. Husky's install points core.hooksPath at .husky/ (or a global
    hooksPath may exist), so the hooks path is pinned to this clone's own hooks folder. Every remote gets a push URL that
    cannot work, and pushInsteadOf rewrites GitLab URLs and the user's clone paths to a scheme git cannot speak."""
    hooks = (clone / ".git" / "hooks").absolute()
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(HOOK)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _git(clone, "config", "--local", "core.hooksPath", str(hooks))
    for remote in _git(clone, "remote").split():
        _git(clone, "config", f"remote.{remote}.pushurl", NO_PUSH_URL)
    key = f"url.{NO_PUSH_PREFIX}.pushInsteadOf"
    prefixes = list(GITLAB_PREFIXES)
    for cfg in qpaths.load_repos().values():
        path = Path(cfg["path"])
        prefixes += [str(path.parent).rstrip("/") + "/", str(path)]
    _run(clone, "git", "config", "--unset-all", key)
    for prefix in dict.fromkeys(prefixes):
        _git(clone, "config", "--add", key, prefix)


def _open(name: str) -> tuple[Path, dict]:
    """The prepared work clone of a writable repo and its metadata, with the push lock re-applied."""
    if name not in WRITABLE:
        raise WorkError(f"{name} is not a repo quorra may change (allowed: {', '.join(WRITABLE)})")
    clone = clone_path(name)
    meta = _read_meta(clone)
    _lock_push(clone)
    return clone, meta


def _resolve_base(clone: Path, name: str, base: str | None) -> str:
    ref = qpaths.repo_cfg(name)["ref"]
    for cand in [base] if base else [indexed_sha(name), f"local-origin/{ref}", f"local/{ref}"]:
        if not cand:
            continue
        p = _run(clone, "git", "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}")
        if p.returncode == 0:
            return p.stdout.strip()
        if base:
            raise WorkError(f"base {base!r} is not a commit in {name}")
    raise WorkError(f"cannot find a base commit for {name}")


def _install(clone: Path, cmd: list[str]) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "COMPOSER_NO_INTERACTION": "1", "CI": "1"}
    try:
        p = _run(clone, *cmd, timeout=INSTALL_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "cmd": cmd, "reason": f"timed out after {INSTALL_TIMEOUT}s"}
    except FileNotFoundError as e:
        return {"ok": False, "cmd": cmd, "reason": f"command not found: {e.filename}"}
    if p.returncode != 0:
        return {"ok": False, "cmd": cmd, "reason": f"exit {p.returncode}", "tail": _tail(p.stdout + p.stderr)}
    return {"ok": True, "cmd": cmd}


def prepare(name: str, slug: str, base: str | None = None, install: bool = True) -> dict:
    if name not in WRITABLE:
        raise WorkError(f"{name} is not a repo quorra may change (allowed: {', '.join(WRITABLE)})")
    if not SLUG_RE.match(slug):
        raise WorkError("slug must be lowercase letters, digits and hyphens (at most 61 characters)")
    clone = _ensure_clone(name)
    try:
        meta = json.loads(_meta_file(clone).read_text(encoding="utf-8")) if _meta_file(clone).exists() else {}
    except json.JSONDecodeError as e:
        raise WorkError(f"corrupt quorra.json in {clone}: {e}") from None
    branches = meta.setdefault("branches", {})
    branch = f"quorra/{slug}"
    current_branch = _branch(clone)

    # Refuse to leave one quorra branch for another while it holds uncommitted work.
    if current_branch != branch and current_branch.startswith("quorra/"):
        dirty = _dirty(clone)
        if dirty:
            raise WorkError(f"{name} work clone has uncommitted changes ({', '.join(dirty[:2])}); "
                            "commit or discard them before switching tasks")

    exists = _run(clone, "git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    recovered = False
    if exists:
        _git(clone, "checkout", "--quiet", branch)
        if branch in branches:
            sha = branches[branch]["base"]
        else:  # an earlier prepare was interrupted before it wrote the metadata
            sha = _git(clone, "merge-base", branch, _resolve_base(clone, name, base))
            branches[branch] = {"base": sha}
            recovered = True
            _write_meta(clone, meta)
    else:
        sha = _resolve_base(clone, name, base)
        _git(clone, "checkout", "--quiet", "-b", branch, sha)
        branches[branch] = {"base": sha}
        _write_meta(clone, meta)  # before the install, which can be interrupted

    try:
        install_cmd = repo_checks(name).get("install")
        if install and not install_cmd:
            meta["install"] = {"ok": False,
                               "reason": repo_checks(name).get("install_note", "no local install for this repo")}
        elif install:
            meta["install"] = _install(clone, install_cmd)
        else:
            meta.setdefault("install", {"ok": False, "reason": "not installed (--no-install)"})
    finally:
        _lock_push(clone)  # the install may have run git hooks setup (husky), even when it was interrupted
    _write_meta(clone, meta)
    res = {"repo": name, "clone": str(clone), "branch": branch, "base": sha, "install": meta["install"]}
    if recovered:
        res["recovered"] = True
    return res


def _changed(clone: Path, base: str) -> list[str]:
    tracked = _gitz(clone, "diff", "--name-only", "-z", "--diff-filter=ACMR", base)
    new = _gitz(clone, "ls-files", "-z", "--others", "--exclude-standard")
    if len(new) > MAX_UNTRACKED:
        raise WorkError(f"{len(new)} untracked files in {clone.name} (first: {new[0]}); an install probably wrote files "
                        "git does not ignore. Remove them or commit the real changes before running checks.")
    return sorted({f for f in tracked + new if f and (clone / f).is_file()})


def status(name: str) -> dict:
    clone, meta = _open(name)
    base = _base(clone)
    return {"repo": name, "branch": _branch(clone), "base": base, "head": _git(clone, "rev-parse", "HEAD"),
            "commits": int(_git(clone, "rev-list", "--count", f"{base}..HEAD")), "dirty": sorted(_dirty(clone)),
            "install": meta.get("install", {})}


CHECK_TIMEOUT = 900
MAX_UNTRACKED = 500


def _run_check(clone: Path, c: dict, files: list[str], installed: bool) -> dict:
    name = c["name"]
    if "db" in c.get("needs", []):
        return {"name": name, "status": "not_run", "reason": "needs a database; left to CI"}
    if "docker" in c.get("needs", []):
        return {"name": name, "status": "not_run", "reason": "needs the framework's test image (Docker); left to CI"}
    if "deps" in c.get("needs", []) and not installed:
        return {"name": name, "status": "not_run", "reason": "dependencies not installed"}
    if c.get("requires") and not (clone / c["requires"]).exists():
        return {"name": name, "status": "not_run", "reason": f"{c['requires']} not present"}
    targets = [f for f in files if re.search(c["files"], f)] if c.get("files") else []
    if c.get("files") and not targets:
        return {"name": name, "status": "not_run", "reason": "no changed files of this kind"}
    if "{file}" in c["cmd"]:
        if not c.get("files"):
            return {"name": name, "status": "not_run", "reason": "placeholder without a files pattern"}
        targets_prefixed = [f"./{f}" if f.startswith("-") else f for f in targets]
        runs = [[targets_one if part == "{file}" else part for part in c["cmd"]] for targets_one in targets_prefixed]
    elif "{files}" in c["cmd"]:
        if not c.get("files"):
            return {"name": name, "status": "not_run", "reason": "placeholder without a files pattern"}
        targets_prefixed = [f"./{f}" if f.startswith("-") else f for f in targets]
        cmd = []
        for part in c["cmd"]:
            cmd.extend(targets_prefixed if part == "{files}" else [part])
        runs = [cmd]
    else:
        cmd = []
        for part in c["cmd"]:
            cmd.extend(targets if part == "{files}" else [part])
        runs = [cmd]
    if not runs:
        return {"name": name, "status": "not_run", "reason": "nothing to run"}
    failed, out = [], []
    for cmd in runs:
        try:
            p = _run(clone, *cmd, timeout=c.get("timeout", CHECK_TIMEOUT))
        except subprocess.TimeoutExpired:
            return {"name": name, "status": "failed", "reason": f"timed out after {c.get('timeout', CHECK_TIMEOUT)}s"}
        except (FileNotFoundError, OSError) as e:
            reason = f"command not found: {e.filename}" if isinstance(e, FileNotFoundError) else f"cannot run {cmd[0]}: {e}"
            return {"name": name, "status": "not_run", "reason": reason}
        out.append(p.stdout + p.stderr)
        if p.returncode == 0 and c.get("no_tests") and re.search(c["no_tests"], p.stdout + p.stderr):
            return {"name": name, "status": "not_run", "reason": "no tests ran", "output": _tail("".join(out))}
        if p.returncode != 0:
            if "{file}" in c["cmd"]:
                failed.append(cmd[[i for i, part in enumerate(c["cmd"]) if part == "{file}"][0]])
            else:
                failed.append(f"exit {p.returncode}")
    res = {"name": name, "status": "failed" if failed else "passed", "output": _tail("".join(out))}
    if failed:
        res["reason"] = "failed: " + ", ".join(failed)
    return res


def check(name: str, only: list[str] | None = None) -> dict:
    clone, meta = _open(name)
    checks = repo_checks(name)["checks"]
    unknown = [n for n in (only or []) if n not in {c["name"] for c in checks}]
    if unknown:
        raise WorkError(f"unknown check {', '.join(unknown)}; known checks: {', '.join(c['name'] for c in checks)}")
    files = _changed(clone, _base(clone))
    dirty = bool(_dirty(clone))
    installed = bool(meta.get("install", {}).get("ok"))
    results = [_run_check(clone, c, files, installed) for c in checks if not only or c["name"] in only]
    res = {"repo": name, "branch": _branch(clone), "head": _git(clone, "rev-parse", "HEAD"), "files": files,
           "only": only or None, "dirty": dirty, "results": results}
    (clone / ".git" / "quorra-checks.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def diff(name: str) -> str:
    clone, _meta = _open(name)
    text = _git(clone, "diff", _base(clone))
    # Append diff-style blocks for untracked files
    untracked = _gitz(clone, "ls-files", "-z", "--others", "--exclude-standard")
    for file in untracked:
        file_path = clone / file
        if file_path.is_file():
            p = _run(clone, "git", "diff", "--no-index", "/dev/null", file)
            if p.stdout:
                text += p.stdout
    return _safe(text)


def patch(name: str, out: Path) -> dict:
    clone, _meta = _open(name)
    base = _base(clone)
    if _dirty(clone):
        raise WorkError(f"{name} has uncommitted changes; commit them before writing a patch")
    commit_count = int(_git(clone, "rev-list", "--count", f"{base}..HEAD"))
    if commit_count == 0:
        raise WorkError("no commits to patch")
    text = _git(clone, "format-patch", "--stdout", f"{base}..HEAD") + "\n"
    flagged = [str(i) for i, line in enumerate(text.splitlines(), 1) if _safe(line) != line]
    if flagged:
        raise WorkError(f"possible secret on patch line(s) {', '.join(flagged[:20])}; remove it, then re-run")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return {"patch": str(out), "commits": commit_count}


PANEL_REPO = os.environ.get("QUORRA_PANEL_REPO", "web-app")
PANEL_CONFIG = """import {{ defineConfig, devices }} from '@playwright/test';

// Written by quorra's work.py for local feature checks; excluded from git, never committed.
export default defineConfig({{
    testDir: '..',
    timeout: 60000,
    retries: 0,
    workers: 1,
    reporter: [['list']],
    use: {{
        baseURL: 'http://localhost:{port}/',
        screenshot: 'on',
        trace: 'off',
        testIdAttribute: 'data-testid',
    }},
    // The installed Google Chrome: no browser download, whatever Playwright version the repo pins.
    projects: [{{ name: 'chrome', use: {{ ...devices['Desktop Chrome'], channel: 'chrome' }} }}],
}});
"""


def _panel() -> tuple[Path, dict]:
    clone = clone_path(PANEL_REPO)
    if not (clone / ".git").exists():
        raise WorkError(f"the local panel needs a prepared {PANEL_REPO} work clone; run work.py prepare first")
    _read_meta(clone)
    _lock_push(clone)
    return clone, repo_checks(PANEL_REPO)["panel"]


def _pid_file(clone: Path) -> Path:
    return clone / ".git" / "quorra-panel.pid"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proc_start(pid: int) -> str | None:
    """Get process start time via ps; return None if process not found or ps fails."""
    try:
        p = _run(Path.cwd(), "ps", "-o", "lstart=", "-p", str(pid))
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    return None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _panel_proc(clone: Path) -> dict | None:
    """Read and validate the panel pid file; return info dict or None if stale/missing/invalid."""
    pf = _pid_file(clone)
    if not pf.exists():
        return None
    try:
        info = json.loads(pf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    pid = info.get("pid")
    if not isinstance(pid, int) or not _alive(pid):
        return None
    try:
        if os.getpgid(pid) != pid:
            return None
    except (ProcessLookupError, PermissionError):
        return None
    if _proc_start(pid) != info.get("start"):
        return None
    return info


def _leader_running(pid: int) -> bool:
    """True while the process exists and is not a zombie."""
    p = _run(Path.cwd(), "ps", "-o", "stat=", "-p", str(pid))
    stat_ = p.stdout.strip()
    return p.returncode == 0 and bool(stat_) and not stat_.startswith("Z")


def _wait_ready(port: int, pid: int, deadline: float, proc: subprocess.Popen | None = None) -> str:
    """Wait for the port: 'ready', 'died' (the process exited first) or 'timeout'."""
    while True:
        if _port_open(port):
            return "ready"
        try:
            os.waitpid(pid, os.WNOHANG)  # reap our own child so it does not linger as a zombie
        except ChildProcessError:
            pass
        if (proc is not None and proc.poll() is not None) or not _leader_running(pid):
            return "died"
        if time.time() >= deadline:
            return "timeout"
        time.sleep(0.5)


def panel_up() -> dict:
    clone, cfg = _panel()
    port = int(cfg["port"])
    ready_timeout = float(cfg.get("ready_timeout", 300))
    url = f"http://localhost:{port}/"
    pf = _pid_file(clone)
    log = clone / ".git" / "quorra-panel.log"
    info = _panel_proc(clone)
    if info and _port_open(port):
        return {"url": url, "pid": info["pid"], "log": str(log)}
    if info:
        # A panel that is still starting: wait for it instead of orphaning it with a second one.
        deadline = float(info.get("started", time.time())) + ready_timeout
        state = _wait_ready(port, info["pid"], deadline)
        if state == "ready":
            return {"url": url, "pid": info["pid"], "log": str(log)}
        if state == "timeout":
            panel_down()
            raise WorkError(f"the panel did not answer on port {port} within {cfg.get('ready_timeout', 300)}s")
    pf.unlink(missing_ok=True)
    if _port_open(port):
        raise WorkError(f"port {port} is already in use by another process; stop it first")
    cmd = [str(port) if part == "{port}" else part for part in cfg["start"]]
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=str(clone), stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                                env={**os.environ, "BROWSER": "none", "PORT": str(port)})
    started = time.time()
    pf.write_text(json.dumps({"pid": proc.pid, "port": port, "start": _proc_start(proc.pid), "started": started},
                             separators=(",", ":")), encoding="utf-8")
    state = _wait_ready(port, proc.pid, started + ready_timeout, proc)
    if state == "ready":
        return {"url": url, "pid": proc.pid, "log": str(log)}
    if state == "died":
        pf.unlink(missing_ok=True)
        raise WorkError(f"the panel exited with code {proc.returncode}: {_tail(log.read_text(errors='replace'))}")
    panel_down()
    raise WorkError(f"the panel did not answer on port {port} within {cfg.get('ready_timeout', 300)}s")


def _group_alive(pgid: int) -> list[int]:
    """Live (non-zombie) processes in the group. killpg(pgid, 0) is not usable here: on macOS it fails with EPERM
    once only zombies are left."""
    p = _run(Path.cwd(), "ps", "-A", "-o", "pid=,pgid=,stat=")
    alive = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == str(pgid) and not parts[2].startswith("Z"):
            alive.append(int(parts[0]))
    return alive


def _wait_group_gone(pgid: int, seconds: float) -> bool:
    """Wait until no live process is left in the group. The leader exiting is not enough: its children (rsbuild's
    node workers) can outlive it and keep the port."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            os.waitpid(pgid, os.WNOHANG)  # reap our own child so it does not linger as a zombie
        except ChildProcessError:
            pass
        if not _group_alive(pgid):
            return True
        time.sleep(0.25)
    return False


def panel_down() -> dict:
    clone, _cfg = _panel()
    info = _panel_proc(clone)
    pf = _pid_file(clone)
    if info is None:
        pf.unlink(missing_ok=True)
        return {"stopped": False, "reason": "no running panel (stale or missing pid file)"}
    pid = info["pid"]
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError as e:
        pf.unlink(missing_ok=True)
        return {"stopped": False, "reason": f"cannot signal process: {type(e).__name__}"}
    except PermissionError as e:  # still running and not ours: keep the pid file
        return {"stopped": False, "reason": f"cannot signal process: {type(e).__name__}"}
    if _wait_group_gone(pid, 10):
        pf.unlink(missing_ok=True)
        return {"stopped": True}
    for member in _group_alive(pid):
        try:
            os.kill(member, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            return {"stopped": False, "reason": f"cannot signal process: {type(e).__name__}"}
    if _wait_group_gone(pid, 2):
        pf.unlink(missing_ok=True)
        return {"stopped": True}
    return {"stopped": False, "reason": "process group still alive after SIGKILL"}


def panel_test(spec: str, out: Path) -> dict:
    clone, cfg = _panel()
    out = Path(out).absolute()
    if (out / "run.json").exists():
        raise WorkError(f"{out} is a run folder (it holds run.json); give --out a subfolder such as {out / 'panel'}")
    if not _port_open(int(cfg["port"])):
        raise WorkError("the panel is not running; run work.py panel up")
    qdir = clone / ".quorra"
    qdir.mkdir(exist_ok=True)
    (qdir / "playwright.config.ts").write_text(PANEL_CONFIG.format(port=int(cfg["port"])), encoding="utf-8")
    exclude = clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text().splitlines() if exclude.exists() else []
    if ".quorra/" not in lines:
        exclude.write_text("\n".join(lines + [".quorra/"]) + "\n")
    # Playwright empties its output folder, so it always gets a fresh one and nothing already in --out is touched.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out / stamp
    n = 1
    while out.exists():
        n += 1
        out = out.parent / f"{stamp}-{n}"
    out.mkdir(parents=True)
    cmd = [*cfg["playwright"], spec, "--config", ".quorra/playwright.config.ts", "--output", str(out)]
    try:
        p = _run(clone, *cmd, timeout=900)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "screenshots": [], "output": "timed out after 900s"}
    shots = sorted(str(x) for x in out.rglob("*.png"))
    return {"status": "passed" if p.returncode == 0 else "failed", "screenshots": shots,
            "output": _tail(p.stdout + p.stderr)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="work.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("repo"); p.add_argument("slug")
    p.add_argument("--base"); p.add_argument("--no-install", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("repo"); c.add_argument("--only", nargs="*")
    for n in ("status", "diff"):
        sub.add_parser(n).add_argument("repo")
    pa = sub.add_parser("patch")
    pa.add_argument("repo"); pa.add_argument("--out", required=True)
    pn = sub.add_parser("panel")
    pn.add_argument("action", choices=["up", "down", "test"])
    pn.add_argument("spec", nargs="?")
    pn.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            res = prepare(a.repo, a.slug, a.base, install=not a.no_install)
        elif a.cmd == "check":
            res = check(a.repo, a.only)
        elif a.cmd == "status":
            res = status(a.repo)
        elif a.cmd == "diff":
            print(diff(a.repo))
            return 0
        elif a.cmd == "patch":
            res = patch(a.repo, Path(a.out))
        elif a.action == "up":
            res = panel_up()
        elif a.action == "down":
            res = panel_down()
        else:
            if not a.spec or not a.out:
                raise WorkError("panel test needs a spec path and --out DIR")
            res = panel_test(a.spec, Path(a.out))
    except WorkError as e:
        print(json.dumps({"error": _safe(str(e))}), file=sys.stderr)
        return 2
    except Exception as e:
        print(json.dumps({"error": _safe(f"{type(e).__name__}: {e}")}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.cmd == "check" and any(r["status"] == "failed" for r in res["results"]):
        return 1
    if a.cmd == "panel" and a.action == "test" and res.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
