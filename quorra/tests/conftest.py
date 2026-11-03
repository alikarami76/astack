import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "scripts", ROOT / "kb"):
    sys.path.insert(0, str(p))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb-default.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export-default"))
    monkeypatch.setenv("QUORRA_KB_VECTORS", str(tmp_path / "kb-default.vectors.sqlite"))
