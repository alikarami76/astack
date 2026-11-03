#!/usr/bin/env python3
"""Hand KB edits to the user: a quorra/<feature>-<date> branch, a scrub-checked commit of features/<feature>/ only, and a
summary. There is deliberately no push command: quorra asks the user and runs `git push -u origin <branch>` itself, and only
after an explicit yes in that conversation, every time.
  kb_git.py branch <feature> [--date YYYY-MM-DD]
  kb_git.py commit <feature> --message "..." [--accept path:line ...]
  kb_git.py summary [--branch NAME]
`branch` remembers the branch the KB was on; `commit` switches back to it after a successful commit."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402
from qindex.redact import scrub  # noqa: E402

TRAILER = "Co-Authored-By: quorra <noreply@example.com>"
FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class KbGitError(RuntimeError):
    pass


def _run(kb: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(kb), "-c", "core.quotepath=off", *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git(kb: Path, *a: str) -> str:
    p = _run(kb, *a)
    if p.returncode != 0:
        raise KbGitError(f"git {' '.join(a[:2])} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def _feature(feature: str) -> str:
    if not FEATURE_RE.match(feature):
        raise KbGitError(f"bad feature slug {feature!r}")
    return f"features/{feature}/"


def main_branch(kb: Path) -> str:
    for b in ("main", "master"):
        if subprocess.run(["git", "-C", str(kb), "rev-parse", "--verify", "--quiet", f"refs/heads/{b}"],
                          capture_output=True).returncode == 0:
            return b
    raise KbGitError("the KB repo has no main or master branch")


def current(kb: Path) -> str:
    return _git(kb, "branch", "--show-current").strip()


def branch(kb: Path, feature: str, date: str | None = None) -> str:
    prefix = _feature(feature)
    name = f"quorra/{feature}-{date or _dt.date.today().isoformat()}"
    dirty = [ln[3:] for ln in _git(kb, "status", "--porcelain", "--untracked-files=no").splitlines() if ln[3:]]
    unrelated = [p for p in dirty if not p.startswith(prefix)]
    if unrelated:
        raise KbGitError(f"unrelated changes in the KB working tree, commit or stash them first: {unrelated}")
    exists = subprocess.run(["git", "-C", str(kb), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                            capture_output=True).returncode == 0
    start = current(kb)
    if start and not start.startswith("quorra/"):
        _return_file(kb).write_text(start + "\n", encoding="utf-8")
    _git(kb, "switch", "-q", name) if exists else _git(kb, "switch", "-q", "-c", name, main_branch(kb))
    return name


def _return_file(kb: Path) -> Path:
    p = Path(_git(kb, "rev-parse", "--git-path", "quorra-kb-return").strip())
    return p if p.is_absolute() else kb / p


def _flagged(kb: Path, paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        text = _run(kb, "show", f":{p}").stdout
        for n, line in enumerate(text.split("\n"), 1):
            if scrub(line) != line:
                out.append(f"{p}:{n}")
    return out


def commit(kb: Path, feature: str, message: str, accept: list[str] | None = None) -> str:
    return commit_and_restore(kb, feature, message, accept)[0]


def commit_and_restore(kb: Path, feature: str, message: str, accept: list[str] | None = None) -> tuple[str, dict]:
    prefix = _feature(feature)
    if not re.fullmatch(rf"quorra/{re.escape(feature)}-\d{{4}}-\d{{2}}-\d{{2}}", current(kb)):
        raise KbGitError(f"not on a quorra/{feature}- branch (on {current(kb)!r}); run kb_git.py branch {feature} first")
    _git(kb, "add", "--", prefix)
    staged = [p for p in _git(kb, "diff", "--cached", "--name-only").splitlines() if p]
    outside = [p for p in staged if not p.startswith(prefix)]
    if outside:
        _git(kb, "reset", "-q", "--", prefix)
        raise KbGitError(f"other files are staged, unstage them first: {outside}")
    if not staged:
        raise KbGitError("nothing to commit under " + prefix)
    flagged = [f for f in _flagged(kb, staged) if f not in set(accept or [])]
    if flagged:
        _git(kb, "reset", "-q", "--", prefix)
        raise KbGitError("possible secrets (scrub would change these lines); remove them, or pass --accept for a "
                         f"confirmed false positive: {flagged}")
    _git(kb, "commit", "-q", "-m", message.rstrip() + "\n\n" + TRAILER)
    sha = _git(kb, "rev-parse", "HEAD").strip()
    work = current(kb)
    info: dict = {"on_branch": work}
    rf = _return_file(kb)
    back = rf.read_text(encoding="utf-8").strip() if rf.exists() else ""
    if back and back != work:
        p = _run(kb, "switch", "-q", back)
        if p.returncode == 0:
            info = {"on_branch": back, "commit_branch": work}
            rf.unlink()
        else:
            info["stayed"] = f"could not switch back to {back}: {p.stderr.strip()[:200]}"
    return sha, info


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _quorra_branch(kb: Path, branch_name: str | None) -> str:
    if branch_name:
        return branch_name
    cur = current(kb)
    if cur.startswith("quorra/"):
        return cur
    refs = _git(kb, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/quorra/").split()
    if not refs:
        raise KbGitError("no quorra/ branch in the KB repo")
    return refs[0]


def summary(kb: Path, branch_name: str | None = None) -> dict:
    base = main_branch(kb)
    tip = _quorra_branch(kb, branch_name)
    files = []
    for row in _git(kb, "diff", "--numstat", f"{base}...{tip}", "--", "features/").splitlines():
        added, removed, path = row.split("\t", 2)
        lines = _run(kb, "show", f"{tip}:{path}").stdout.split("\n")
        heads = []
        for h in _git(kb, "diff", "-U0", f"{base}...{tip}", "--", path).splitlines():
            m = HUNK.match(h)
            if not m:
                continue
            start = max(int(m.group(1)), 1)
            head = next((lines[i] for i in range(min(start, len(lines)) - 1, -1, -1) if lines[i].startswith("#")), None)
            if head and head not in heads:
                heads.append(head)
        files.append({"file": path, "added": int(added) if added.isdigit() else 0,
                      "removed": int(removed) if removed.isdigit() else 0, "headings": heads})
    return {"branch": tip, "base": base, "files": files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("branch"); b.add_argument("feature"); b.add_argument("--date", default=None)
    c = sub.add_parser("commit"); c.add_argument("feature"); c.add_argument("--message", required=True)
    c.add_argument("--accept", action="append")
    sm = sub.add_parser("summary"); sm.add_argument("--branch", default=None)
    a = ap.parse_args(argv)
    kb = Path(a.kb) if a.kb else qpaths.feature_kb_path()
    try:
        if a.cmd == "branch":
            out = {"branch": branch(kb, a.feature, a.date)}
        elif a.cmd == "commit":
            sha, info = commit_and_restore(kb, a.feature, a.message, a.accept)
            out = {"commit": sha, **info}
        else:
            out = summary(kb, a.branch)
    except KbGitError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
