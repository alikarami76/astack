import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "scripts", ROOT / "scripts" / "perf", ROOT / "kb"):
    sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own KB, runs folder, work folder and a fake code folder."""
    monkeypatch.setenv("RINZLER_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("RINZLER_KB_EXPORT", str(tmp_path / "export"))
    monkeypatch.setenv("RINZLER_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("RINZLER_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("RINZLER_CODE_DIR", str(tmp_path / "code"))
    (tmp_path / "code").mkdir()
