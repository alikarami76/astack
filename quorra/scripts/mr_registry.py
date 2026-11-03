"""The MRs quorra opened or registered: state/mrs.json (local, untracked). Ref format '<repo>!<iid>'."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402

MR_URL = re.compile(r"^https://[^/]+/(.+?)/-/merge_requests/(\d+)/?$")


def _file() -> Path:
    return qpaths.state_dir() / "mrs.json"


def ref(repo: str, iid: int) -> str:
    return f"{repo}!{int(iid)}"


def parse_mr_url(url: str) -> tuple[str, int]:
    m = MR_URL.match(url.strip())
    if not m:
        raise ValueError(f"not a GitLab merge request URL: {url}")
    return m.group(1), int(m.group(2))


def repo_for_project(path: str) -> str:
    for name, cfg in qpaths.load_repos().items():
        if cfg["gitlab"] == path:
            return name
    raise KeyError(f"no repo in config/repos.json has GitLab project {path!r}")


def load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mrs": {}}


def save(reg: dict) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")


def add(entry: dict) -> str:
    reg = load()
    r = ref(entry["repo"], entry["iid"])
    if r in reg["mrs"]:
        raise ValueError(f"{r} is already registered")
    reg["mrs"][r] = {"state": "opened", "last_checked": None, "last_note_id": 0, **entry}
    save(reg)
    return r


def get(r: str) -> dict:
    reg = load()
    if r not in reg["mrs"]:
        raise KeyError(f"{r} is not a quorra MR (register it with mrs.py register)")
    return reg["mrs"][r]


def update(r: str, **fields) -> None:
    reg = load()
    get(r)
    reg["mrs"][r].update(fields)
    save(reg)


def open_refs() -> list[str]:
    return [r for r, e in load()["mrs"].items() if e.get("state") == "opened"]
