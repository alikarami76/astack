#!/usr/bin/env python3
"""Build or refresh one repo's code index. Examples:
  index_repo.py api-app --fetch
  index_repo.py web-app --full
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitsrc  # noqa: E402
import qpaths  # noqa: E402
from qindex import build  # noqa: E402


def _unlink_db(p: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)


def _full_build(name: str, cfg: dict, sha: str, dbp: Path) -> dict:
    """Build from scratch into a temp file next to the index, then swap it in with os.replace. The old index (and its
    -wal/-shm) stays untouched until the new one is complete, so a failed --full build never leaves no index."""
    dbp.parent.mkdir(parents=True, exist_ok=True)
    tmp = dbp.with_name(f"{dbp.name}.tmp-{os.getpid()}")
    _unlink_db(tmp)
    try:
        res = build.build_index(name, cfg["path"], cfg["lang"], sha, tmp)
        conn = sqlite3.connect(tmp)
        try:  # fold the WAL into the main file so the single file we move is complete
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        for suffix in ("-wal", "-shm"):  # the old index's WAL must never be replayed onto the new file
            Path(str(dbp) + suffix).unlink(missing_ok=True)
        os.replace(tmp, dbp)
    finally:
        _unlink_db(tmp)
    return res


def refresh(name: str, fetch: bool = False, full: bool = False) -> dict:
    cfg = qpaths.repo_cfg(name)
    warning = None
    if fetch:
        try:
            gitsrc.fetch(cfg["path"])
        except gitsrc.GitError as e:
            warning = f"fetch failed, using last fetched state: {e}"
    sha = gitsrc.resolve(cfg["path"], cfg["ref"])
    dbp = qpaths.index_path(name)
    if full:
        res = _full_build(name, cfg, sha, Path(dbp))
    else:
        res = build.build_index(name, cfg["path"], cfg["lang"], sha, dbp)
    if warning:
        res["warning"] = warning
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="index_repo.py")
    ap.add_argument("name")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(refresh(a.name, a.fetch, a.full)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
