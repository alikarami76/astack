import os
import sys
import pytest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture
def kbenv(tmp_path, monkeypatch):
    db = tmp_path / "kb.sqlite"
    exp = tmp_path / "export"
    monkeypatch.setenv("CLU_KB_DB", str(db))
    monkeypatch.setenv("CLU_KB_EXPORT", str(exp))
    monkeypatch.setenv("CLU_WRITETHROUGH", "0")
    monkeypatch.setenv("CLU_BACKGROUND_EMBED", "0")
    monkeypatch.setenv("CLU_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path
