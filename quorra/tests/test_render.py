import pytest

import render_conventions
import store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    return store.connect()


def test_render_groups_by_area_and_orders_tiers(conn):
    base = {"repo": "api-app", "source": "code", "checked_at_sha": "abc1234", "verified": 1, "rationale": "why"}
    store.upsert(conn, "convention", {**base, "area": "naming", "rule": "Services end with Service", "tier": "dominant",
                                      "examples": [{"file": "src/Service/A.php", "line": 3, "snippet": "class AService"}]})
    store.upsert(conn, "convention", {**base, "area": "naming", "rule": "CI blocks PSR-12 violations", "tier": "enforced",
                                      "source": "tooling", "examples": []})
    store.upsert(conn, "convention", {**base, "area": "apis", "rule": "Old views return arrays; new ones return DTOs",
                                      "tier": "transitional", "examples": [], "verified": 0})
    md = render_conventions.render(conn, "api-app")
    assert md.startswith("# api-app conventions")
    assert md.index("## apis") < md.index("## naming")
    assert md.index("CI blocks PSR-12") < md.index("Services end with Service")
    assert "`src/Service/A.php:3`" in md
    assert "unverified" in md
    assert "checked at `abc1234`" in md


def test_render_survives_bad_rows(conn):
    base = {"repo": "r", "source": "code", "tier": "dominant"}
    store.upsert(conn, "convention", {**base, "area": "a", "rule": "x"})
    conn.execute("UPDATE conventions SET area = NULL, examples = 'not json', counter_examples = '{\"a\": 1}'")
    store.upsert(conn, "convention", {**base, "area": "b", "rule": "multi\nline `tick`", "rationale": "l1\nl2",
                                      "examples": [{"line": 3}, {"file": "f.py"}, "str", {"file": "g`.py", "line": 2, "snippet": "a\nb"}]})
    conn.execute("UPDATE conventions SET rule = NULL WHERE area = 'b'")
    conn.commit()
    md = render_conventions.render(conn, "r")
    assert "## general" in md and "(unnamed rule)" in md
    assert "`f.py`" in md and "`g'.py:2` — `a b`" in md
    assert "l1 l2" in md
