#!/usr/bin/env python3
"""clu toolkit-change proposals: new / list / show / apply / reject."""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PDIR = REPO / "proposals"
REPOS = {"clu": REPO, "skills": Path.home() / ".claude/skills"}
CHANGELOG = REPO / "docs/changelog.md"
TRAILER = "Co-Authored-By: <attribution line>"


def _read(p: Path) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", p.read_text(), re.S)
    return yaml.safe_load(m.group(1)), m.group(2)


def _write(p: Path, fm: dict, body: str) -> None:
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body)


def _diff_of(body: str) -> str:
    # Prefer the 4-backtick fence (safe for diffs that touch a fenced .md file).
    m = re.search(r"````diff\n(.*?)\n````", body, re.S)
    if m:
        return m.group(1) + "\n"
    # Fall back to the 3-backtick fence, anchored on the LAST ``` in the body
    # (a non-greedy match would stop at the first ``` inside the diff itself).
    m = re.search(r"```diff\n(.*)```", body, re.S)
    if not m:
        raise RuntimeError("proposal has no ```diff block")
    return m.group(1)


def new(slug, title, repo, motivation, risk, test, diff_text, pdir=None) -> Path:
    d = Path(pdir or PDIR)
    d.mkdir(parents=True, exist_ok=True)
    base_pid = f"{_dt.date.today().isoformat()}-{slug}"
    pid, p, i = base_pid, d / f"{base_pid}.md", 2
    while p.exists():
        pid = f"{base_pid}-{i}"
        p, i = d / f"{pid}.md", i + 1
    fm = {"id": pid, "title": title, "repo": repo, "status": "pending", "created": _dt.datetime.now().isoformat(timespec="seconds")}
    # 4-backtick fence: a diff may itself contain a ```-fenced block (e.g. when it
    # edits a .md file), and a 3-backtick fence would close early on that.
    body = (f"\n## Motivation\n{motivation}\n\n## Risk\n{risk}\n\n## Test\n{test}\n\n## Diff\n````diff\n"
            f"{diff_text if diff_text.endswith(chr(10)) else diff_text + chr(10)}````\n")
    _write(p, fm, body)
    return p


def list_pending(pdir=None) -> list[dict]:
    d = Path(pdir or PDIR)
    return [_read(p)[0] for p in sorted(d.glob("*.md")) if _read(p)[0].get("status") == "pending"]


def _git(repo: Path, *args, input_text=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], input=input_text, text=True, capture_output=True)


def apply(pid, pdir=None, repos=None, run_tests=True, changelog=None) -> str:
    d = Path(pdir or PDIR)
    p = d / f"{pid}.md"
    fm, body = _read(p)
    repo = Path((repos or REPOS)[fm["repo"]])
    diff = _diff_of(body)
    # `diff -u` appends "\t<timestamp>" to header paths; git diff does not
    files_added = set(re.findall(r"^\+\+\+ b/([^\t\n]+)", diff, re.M))
    files_deleted = set(re.findall(r"^--- a/([^\t\n]+)", diff, re.M))
    files = (files_added | files_deleted) - {"/dev/null"}

    # Refuse to sweep up unrelated work: nothing staged and no local edits on the files this proposal
    # touches. (The repo may be shared with other projects, so their staged work is left alone;
    # the commit below names only these files.)
    if files and _git(repo, "diff", "--cached", "--quiet", "--", *files).returncode != 0:
        raise RuntimeError(f"refusing to apply: staged changes on touched file(s): {sorted(files)}")
    if files and _git(repo, "diff", "--quiet", "HEAD", "--", *files).returncode != 0:
        raise RuntimeError(f"refusing to apply: local edits on touched file(s): {sorted(files)}")

    chk = _git(repo, "apply", "--check", "-", input_text=diff)
    if chk.returncode != 0:
        raise RuntimeError(f"diff does not apply: {chk.stderr.strip()}")
    app = _git(repo, "apply", "-", input_text=diff)
    if app.returncode != 0:
        raise RuntimeError(f"diff apply failed: {app.stderr.strip()}")
    if run_tests and fm["repo"] == "clu":
        t = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo, capture_output=True, text=True)
        if t.returncode != 0:
            _git(repo, "apply", "-R", "-", input_text=diff)
            raise RuntimeError(f"tests failed, reverted:\n{t.stdout[-1500:]}")
    ad = _git(repo, "add", "-A", "--", *files)
    if ad.returncode != 0:
        _git(repo, "apply", "-R", "-", input_text=diff)
        raise RuntimeError(f"git add failed: {ad.stderr.strip()}")
    cm = _git(repo, "-c", "user.name=clu", "-c", "user.email=clu@local", "commit", "-m",
              f"clu proposal {pid}: {fm['title']}\n\n{TRAILER}", "--", *files)
    if cm.returncode != 0:
        _git(repo, "reset", "HEAD", "--", *files)
        _git(repo, "apply", "-R", "-", input_text=diff)
        raise RuntimeError(f"commit failed: {cm.stderr.strip()}")
    sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    cl = Path(changelog or CHANGELOG)
    cl.parent.mkdir(parents=True, exist_ok=True)
    with cl.open("a") as f:
        f.write(f"- {_dt.date.today().isoformat()} — {fm['title']} ({fm['repo']} {sha}, proposal {pid})\n")
    fm.update(status="applied", applied_commit=sha)
    (d / "applied").mkdir(exist_ok=True)
    _write(d / "applied" / p.name, fm, body)
    p.unlink()
    return sha


def reject(pid, reason, pdir=None) -> None:
    d = Path(pdir or PDIR)
    p = d / f"{pid}.md"
    fm, body = _read(p)
    fm.update(status="rejected", reason=reason)
    (d / "rejected").mkdir(exist_ok=True)
    _write(d / "rejected" / p.name, fm, body)
    p.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="proposals.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    for a in ("--slug", "--title", "--motivation", "--risk", "--test", "--diff-file"):
        n.add_argument(a, required=True)
    n.add_argument("--repo", choices=sorted(REPOS), default="clu")
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("id")
    a = sub.add_parser("apply"); a.add_argument("id")
    r = sub.add_parser("reject"); r.add_argument("id"); r.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "new":
        print(new(args.slug, args.title, args.repo, args.motivation, args.risk, args.test, Path(args.diff_file).read_text()))
    elif args.cmd == "list":
        for fm in list_pending():
            print(f"{fm['id']}  [{fm['repo']}] {fm['title']}")
    elif args.cmd == "show":
        print((PDIR / f"{args.id}.md").read_text())
    elif args.cmd == "apply":
        print(apply(args.id))
    elif args.cmd == "reject":
        reject(args.id, args.reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
