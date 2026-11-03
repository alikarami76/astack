"""Read-only git plumbing at a fixed commit. Nothing here changes a working tree or a branch."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, Iterator


class GitError(RuntimeError):
    pass


def _run(repo: Path, *args: str, input_bytes: bytes | None = None, ok_codes=(0,)) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *args], input=input_bytes, capture_output=True)
    if p.returncode not in ok_codes:
        raise GitError(f"git {' '.join(args[:3])} failed in {repo}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


FETCH_TIMEOUT = 600


def fetch(repo: Path) -> None:
    """Headless fetch: never prompts for credentials and gives up after FETCH_TIMEOUT seconds."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        p = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin"], capture_output=True, env=env,
                           timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git fetch timed out after {FETCH_TIMEOUT}s in {repo}") from e
    if p.returncode != 0:
        raise GitError(f"git fetch failed in {repo}: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")


def resolve(repo: Path, ref: str) -> str:
    for cand in (f"origin/{ref}", ref):
        p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"],
                           capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.strip()
    raise GitError(f"cannot resolve {ref!r} in {repo}")


def commit_date(repo: Path, sha: str) -> str:
    """The commit's committer date in strict ISO-8601 (`git log -1 --format=%cI <sha>`)."""
    return _run(repo, "log", "-1", "--format=%cI", sha).decode().strip()


def list_files(repo: Path, sha: str) -> list[str]:
    out = _run(repo, "ls-tree", "-r", "-z", "--name-only", sha)
    return [f for f in out.decode().split("\0") if f]


def read_files(repo: Path, sha: str, paths: Iterable[str]) -> Iterator[tuple[str, bytes | None]]:
    paths = list(paths)
    if not paths:
        return
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for path in paths:
            if "\n" in path:
                yield path, None
                continue
            try:
                proc.stdin.write(f"{sha}:{path}\n".encode())
                proc.stdin.flush()
            except BrokenPipeError as e:
                raise GitError(f"cat-file failed: {e}")
            header = proc.stdout.readline().decode().rstrip("\n")
            if not header:
                stderr = proc.stderr.read().decode(errors="replace").strip()
                raise GitError(f"cat-file failed in {repo}: {stderr}")
            if header.endswith(" missing") or header.endswith(" ambiguous"):
                yield path, None
                continue
            parts = header.split()
            if len(parts) < 3:
                yield path, None
                continue
            obj_type = parts[1]
            size = int(parts[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # trailing newline
            if obj_type != "blob":
                yield path, None
            else:
                yield path, data
    finally:
        proc.stdin.close()
        proc.wait()


def changed_files(repo: Path, old: str, new: str) -> list[tuple[str, str]]:
    out = _run(repo, "diff", "--name-status", "-z", "--no-renames", old, new).decode().split("\0")
    items = [x for x in out if x]
    return [(items[i][0], items[i + 1]) for i in range(0, len(items) - 1, 2)]


def grep(repo: Path, sha: str, term: str, max_per_file: int = 3, limit: int = 50) -> list[dict]:
    out = _run(repo, "grep", "-z", "-n", "-I", "-i", "-F", "--no-color", f"--max-count={max_per_file}", "-e", term, sha,
               ok_codes=(0, 1))
    hits = []
    # format with -z: <sha>:<file>\0<line>\0<text>\n (records separated by \n, fields by \0)
    text_out = out.decode(errors="replace")
    for record in text_out.split("\n"):
        if not record:
            continue
        # Split on \0 after removing the sha prefix
        rest = record[len(sha) + 1:]  # skip sha and the ':'
        parts = rest.split("\0")
        if len(parts) < 3:
            continue
        file, line_str, text = parts[0], parts[1], parts[2]
        try:
            hits.append({"file": file, "line": int(line_str), "text": text.strip()})
        except (ValueError, IndexError):
            continue
        if len(hits) >= limit:
            break
    return hits


def recent_files(repo: Path, sha: str, since: str = "6.months") -> set[str]:
    out = _run(repo, "log", "-z", f"--since={since}", "--name-only", "--format=", sha)
    return {f for f in out.decode().split("\0") if f}


def recent_order(repo: Path, sha: str, since: str = "6.months") -> dict[str, int]:
    """Path -> recency rank (0 = most recently changed), from git log at sha. -z so paths match list_files."""
    out = _run(repo, "log", f"--since={since}", "--name-only", "-z", "--format=", sha)
    order: dict[str, int] = {}
    for p in out.decode(errors="replace").split("\0"):
        if p and p not in order:
            order[p] = len(order)
    return order


def count_commits(repo: Path, old: str, new: str) -> int:
    return int(_run(repo, "rev-list", "--count", f"{old}..{new}").decode().strip() or 0)


def has_commit(repo: Path, sha: str) -> bool:
    """True when `sha` names a commit that exists in the clone at `repo`."""
    p = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True)
    return p.returncode == 0


def blob_text(repo: Path, sha: str, path: str) -> str | None:
    """A file's text at a commit, or None when the path is not a file there."""
    for _p, data in read_files(repo, sha, [path]):
        return None if data is None else data.decode("utf-8", "replace")
    return None


def deleting_commit(repo: Path, old: str, new: str, path: str) -> str | None:
    """The 10-char sha of the commit in old..new that deleted `path`, or None."""
    out = _run(repo, "log", "--diff-filter=D", "-1", "--format=%h", "--abbrev=10", f"{old}..{new}", "--", path)
    return out.decode().strip() or None


def last_commit(repo: Path, old: str, new: str, path: str) -> str | None:
    """The 10-char sha of the newest commit in old..new that touched `path`, or None."""
    out = _run(repo, "log", "-1", "--format=%h", "--abbrev=10", f"{old}..{new}", "--", path)
    return out.decode().strip() or None
