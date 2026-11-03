"""quorra paths and repo registry. Stdlib only."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _config(name: str) -> Path:
    """config/<name>.json when the user wrote one, else the shipped config/<name>.example.json."""
    real = REPO / "config" / f"{name}.json"
    return real if real.exists() else REPO / "config" / f"{name}.example.json"


def load_repos(path: str | Path | None = None) -> dict[str, dict]:
    p = Path(path or os.environ.get("QUORRA_REPOS") or _config("repos"))
    raw = json.loads(p.read_text())
    return {name: {**cfg, "name": name, "path": Path(os.path.expanduser(cfg["path"]))} for name, cfg in raw.items()}


def repo_cfg(name: str) -> dict:
    repos = load_repos()
    if name not in repos:
        raise KeyError(f"unknown repo {name!r}; known: {', '.join(sorted(repos))}")
    return repos[name]


def index_path(name: str) -> Path:
    return Path(os.environ.get("QUORRA_INDEX_DIR") or REPO / "index") / f"{name}.sqlite"


def runs_dir() -> Path:
    return Path(os.environ.get("QUORRA_RUNS_DIR") or REPO / "runs")


def normalize_prefix(prefix: str | None) -> str | None:
    """'./src/Voucher', '/src/Voucher' and 'src/Voucher/' all become 'src/Voucher/'; empty means no scope (None).
    Shared by prep_repo.py (--path-prefix) and ingest_rules.py (the rules document's scope)."""
    if not prefix:
        return None
    p = prefix[2:] if prefix.startswith("./") else prefix
    p = p.strip("/")
    return p + "/" if p else None


def feature_kb_path() -> Path:
    """The feature knowledge base repo (the docs quorra explains into and keeps current)."""
    return Path(os.environ.get("QUORRA_FEATURE_KB") or Path.home() / "agent-stack-data/feature-knowledge-base")


def work_dir() -> Path:
    """quorra's own clones for building (never the user's clones)."""
    return Path(os.environ.get("QUORRA_WORK_DIR") or os.path.expanduser("~/code/quorra-work"))


def checks_path() -> Path:
    return Path(os.environ.get("QUORRA_CHECKS") or _config("checks"))


def state_dir() -> Path:
    """quorra's local, untracked state (the MR registry)."""
    return Path(os.environ.get("QUORRA_STATE_DIR") or REPO / "state")
