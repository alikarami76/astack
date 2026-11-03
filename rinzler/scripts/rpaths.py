"""Where Rinzler keeps things. Every path can be moved with an environment variable (the tests do)."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def work_dir() -> Path:
    """Rinzler's own build folder, outside every your company repo."""
    return Path(os.environ.get("RINZLER_WORK", home() / "rinzler-work"))


def runs_dir() -> Path:
    return Path(os.environ.get("RINZLER_RUNS", REPO / "runs"))


def dk_code() -> Path:
    """The owner's your company clones. Rinzler never writes here."""
    return Path(os.environ.get("RINZLER_CODE_DIR", home() / "code"))


def product_map() -> Path:
    return Path(os.environ.get(
        "RINZLER_PRODUCT_MAP",
        home() / "product-map"))


def config(name: str) -> Path:
    return REPO / "config" / name
