import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "scripts", ROOT / "kb"):
    sys.path.insert(0, str(p))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("FLYNN_KB_EXPORT", str(tmp_path / "export"))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(tmp_path / "runs"))
    if os.environ.get("FLYNN_DB_URL"):
        # Running the suite on Postgres (fstore): each test starts from empty tables, as it would from an empty
        # tmp folder. reset_for_tests refuses any database but flynn_d1 or *_test.
        import fstore
        fstore.reset_for_tests()
