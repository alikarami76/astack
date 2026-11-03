"""flynn paths. Stdlib only.

runs_dir() is FLYNN_RUNS_DIR, else flynn's own runs/ folder. On a team server each person has their own runs folder:
the code serving that person sets it for the current thread with `use_runs(path)` (a context manager), so everything
that reads runs_dir() on that thread works in their folder only."""
from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_local = threading.local()


def runs_dir() -> Path:
    own = getattr(_local, "runs", None)
    if own is not None:
        return Path(own)
    return Path(os.environ.get("FLYNN_RUNS_DIR") or REPO / "runs")


@contextlib.contextmanager
def use_runs(path):
    """Within this block (this thread only) runs_dir() is `path`; None leaves it as it is."""
    prev = getattr(_local, "runs", None)
    if path is not None:
        _local.runs = Path(path)
    try:
        yield
    finally:
        _local.runs = prev
