import os
import subprocess
from pathlib import Path


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, files: dict, msg: str = "c", date: str | None = None) -> str:
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            _git(repo, "rm", "-q", rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _git(repo, "add", rel)
    env = os.environ.copy()
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg],
                       check=True, capture_output=True, text=True, env=env)
    return _git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path, files: dict) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    return repo, commit(repo, files, "init")
