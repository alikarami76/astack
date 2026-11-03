import shutil
import sys
import tempfile
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LIB))


@pytest.fixture(autouse=True)
def vhome(monkeypatch):
    # Unix socket paths must stay under ~104 bytes on macOS, so not pytest's long tmp_path.
    # Autouse: no test can ever reach the real ~/.cache/vecstore daemon.
    d = Path(tempfile.mkdtemp(prefix="vs", dir="/tmp"))
    monkeypatch.setenv("VECSTORE_HOME", str(d))
    monkeypatch.delenv("VECSTORE_DISABLE", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def fake_embed():
    from vecstore.daemon import FakeEncoder
    return FakeEncoder().encode
