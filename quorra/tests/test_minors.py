"""Final-review minor fixes (one test per item)."""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from gitfix import make_repo
from qindex import build, db

ROOT = Path(__file__).resolve().parent.parent


def test_resolved_endpoint_rows_are_persisted(tmp_path):
    repo, s1 = make_repo(tmp_path, {"src/API/base.ts": "export const BASE = '/pricing';\n",
                                    "src/API/v/endpoint.ts": "import { BASE } from '../base';\nexport const REPORT_API = `${BASE}/report`;\n"})
    dbp = tmp_path / "t.sqlite"
    build.build_index("front", repo, "ts", s1, dbp)
    x = json.loads(db.connect(dbp).execute("SELECT extra FROM symbols WHERE name = 'REPORT_API'").fetchone()[0])
    assert x.get("url_norm") == "/pricing/report"


class _Tracking(sqlite3.Connection):
    opened: list = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.closed = False
        _Tracking.opened.append(self)

    def close(self):
        self.closed = True
        super().close()


def test_lookup_closes_index_connections_and_reports_grep_failures(tmp_path, monkeypatch):
    from qindex import lookup as L
    repo, sha = make_repo(tmp_path, {"src/A.php": "<?php\nclass PriceThing {}\n"})
    dbp = tmp_path / "a.sqlite"
    build.build_index("a", repo, "php", sha, dbp)
    _Tracking.opened = []
    monkeypatch.setattr(L.db, "connect", lambda p: (lambda c: (setattr(c, "row_factory", sqlite3.Row), c)[1])(
        sqlite3.connect(p, factory=_Tracking)))

    def bad_grep(*a, **k):
        raise L.gitsrc.GitError("grep blew up")
    monkeypatch.setattr(L.gitsrc, "grep", bad_grep)
    res = L.lookup("PriceThing", {"a": {"path": repo, "ref": "master", "db": dbp}}, None, use_vectors="off")
    assert res["grep_failed"] == ["a"] and res["stale"] == []  # not mislabelled as "index behind"
    assert res["hits"] and _Tracking.opened and all(c.closed for c in _Tracking.opened)


def test_symvec_semantic_closes_its_connection(tmp_path, monkeypatch):
    from qindex import symvec
    vp = symvec._vpath(tmp_path / "a.sqlite")
    c = sqlite3.connect(vp)
    c.execute("CREATE TABLE vecs (h TEXT PRIMARY KEY, sym_id INTEGER, vec BLOB)")
    c.commit()
    c.close()
    _Tracking.opened = []
    monkeypatch.setattr(symvec.sqlite3, "connect", _track)
    assert symvec.semantic(tmp_path / "a.sqlite", "q") == []
    assert _Tracking.opened and all(c.closed for c in _Tracking.opened)


_real_connect = sqlite3.connect


def _track(p, **k):
    return _real_connect(p, factory=_Tracking)


def test_proposals_new_validates_slug(tmp_path):
    import proposals
    for bad in ("a/b", "..", "x\\y", "a..b", ""):
        with pytest.raises(ValueError):
            proposals.new(bad, "t", "quorra", "m", "r", "t", "--- a/x\n+++ b/x\n", pdir=tmp_path)
    assert not list(tmp_path.rglob("*.md"))


def test_render_conventions_rejects_path_repo(capsys):
    import render_conventions
    for bad in ("../x", "a/b", "a\\b", ".."):
        with pytest.raises(SystemExit):
            render_conventions.main(["--repo", bad])


def test_convention_requires_every_key_field():
    import store
    c = store.connect()
    base = {"repo": "api-app", "area": "naming", "rule": "r", "tier": "dominant", "source": "code"}
    for k in ("repo", "area", "rule"):
        with pytest.raises(ValueError, match="natural key"):
            store.upsert(c, "convention", {**base, k: ""})
    store.upsert(c, "convention", base)


def test_verify_result_round_trips_as_none(tmp_path):
    import run_record
    import store
    d = run_record.new("v", "lookup", "q", [], runs_root=tmp_path)
    run_record.append(d, "verification", {"claim": "a", "ok": True})
    run_record.close(d, "first")
    run_record.set_(d, "verification", [])
    run_record.close(d, "second")  # the old verdict must not survive a re-close without verification
    row = store.connect().execute("SELECT verify_result FROM runs_index WHERE run_id = ?", (d.name,)).fetchone()
    assert row[0] == "none"
    exported = [json.loads(x) for x in (Path(store.export_dir()) / "run.jsonl").read_text().splitlines()]
    assert exported[0]["verify_result"] == "none"


def test_docs_say_quorra_appends_corrections_and_none_verify():
    t = (ROOT / "docs" / "run-schema.md").read_text()
    row = next(line for line in t.splitlines() if line.startswith("| `corrections`"))
    assert "quorra" in row and "curator" not in row
    assert '"none"' in t


def test_readme_states_python_312():
    assert "3.12" in (ROOT / "README.md").read_text()


def test_git_rule_is_an_allowlist_and_spec_has_no_worktree():
    for p in (ROOT / "agents" / "quorra.md", ROOT / "skills" / "quorra-core" / "SKILL.md"):
        t = p.read_text()
        assert "only these read commands are allowed" in t and "anything else is forbidden" in t, p


def test_gitsrc_fetch_is_headless_with_timeout(monkeypatch, tmp_path):
    import gitsrc
    seen = {}

    def fake_run(cmd, **k):
        seen.update(k)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(gitsrc.subprocess, "run", fake_run)
    gitsrc.fetch(tmp_path)
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["timeout"] == 600

    def slow(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 600)
    monkeypatch.setattr(gitsrc.subprocess, "run", slow)
    with pytest.raises(gitsrc.GitError, match="timed out"):
        gitsrc.fetch(tmp_path)


def test_r5_endpoints_json_shape_documented():
    for p in (ROOT / "skills" / "quorra-core" / "SKILL.md", ROOT / "docs" / "architecture.md"):
        t = p.read_text()
        assert '"pairs"' in t and '"skipped"' in t, p
