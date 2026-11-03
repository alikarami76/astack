"""Postgres backend of the knowledge store. Uses the server in ~/.flynn/test-db-url, database flynn_d2 (skipped if absent or not reachable)."""
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import store

URL_FILE = Path.home() / ".flynn" / "test-db-url"
REAL_EXPORT = Path(__file__).resolve().parent.parent / "kb" / "export"

pytestmark = pytest.mark.skipif(not URL_FILE.exists(), reason="no ~/.flynn/test-db-url")
psycopg = pytest.importorskip("psycopg")

BUTTON = {"system": "admin-panel", "name": "Button", "description": "دکمه اصلی، primary action trigger",
          "usage": "برای ثبت قیمت پیشنهادی", "tier": "verified",
          "variants": {"size": ["sm", "md"]}, "props": {"label": {"type": "string", "required": True}}, "states": ["default"]}
TASTE = {"statement": "Prefer calm tables with generous spacing", "context": "پنل فروشنده", "strength": 3}


@pytest.fixture
def pg(monkeypatch):
    u = urlsplit(URL_FILE.read_text().strip())
    url = urlunsplit(u._replace(path="/flynn_d2"))
    try:                                   # a stale url file with no server behind it is a skip, not a failure
        raw = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.Error:
        pytest.skip("the Postgres server in ~/.flynn/test-db-url is not reachable")
    raw.execute("DROP SCHEMA IF EXISTS kb CASCADE")
    raw.close()
    monkeypatch.setenv("FLYNN_DB_URL", url)
    c = store.connect()
    assert getattr(c, "is_pg", False)
    yield c
    c.close()


def test_upsert_idempotent_merges_and_get(pg):
    a = store.upsert(pg, "component", BUTTON, origin="test")
    b = store.upsert(pg, "component", {**BUTTON, "drift_notes": "x"}, origin="test", tags=["a"])
    assert a == b
    row = store.get(pg, a)
    assert row["drift_notes"] == "x" and json.loads(row["tags"]) == ["a"] and row["type"] == "component"
    assert store.stats(pg)["component"] == 1
    assert store.components(pg, "admin-panel")["Button"]["variants"] == {"size": ["sm", "md"]}


def test_integer_column_and_listing(pg):
    eid = store.upsert(pg, "taste", TASTE)
    assert store.get(pg, eid)["strength"] == 3
    assert [r["id"] for r in store.list_rows(pg, "taste")] == [eid]


def test_search_persian_english_and_filter(pg):
    store.upsert(pg, "component", BUTTON)
    store.upsert(pg, "taste", TASTE)
    assert [r["title"] for r in store.search(pg, "دکمه")] == ["Button"]
    assert [r["title"] for r in store.search(pg, "قیمت")] == ["Button"]
    assert store.search(pg, "primary action", etype="component")[0]["type"] == "component"
    assert store.search(pg, "tabl")[0]["type"] == "taste"  # prefix match
    assert store.search(pg, "primary", etype="taste") == []
    assert store.search(pg, "فروشنده")[0]["type"] == "taste"
    assert store.search(pg, "the of") == []
    assert "[" in store.search(pg, "calm")[0]["snippet"]


def test_search_ranks_title_hits_first(pg):
    store.upsert(pg, "lesson", {"rule": "Spacing", "why": "other"})
    store.upsert(pg, "lesson", {"rule": "other", "why": "spacing spacing spacing is discussed here"})
    got = [r["title"] for r in store.search(pg, "spacing")]
    assert got[0] == "Spacing" and len(got) == 2


def test_export_and_rebuild_roundtrip(pg, tmp_path):
    eid = store.upsert(pg, "component", BUTTON)
    store.upsert(pg, "lesson", {"rule": "always show empty states", "why": "the user"})
    store.export(pg)
    assert (tmp_path / "export" / "component.jsonl").exists()
    c2 = store.rebuild()
    assert store.get(c2, eid)["name"] == "Button"
    assert [r["title"] for r in store.search(c2, "دکمه")] == ["Button"]
    assert store.stats(c2)["lesson"] == 1


def test_export_refuses_to_drop_rows(pg):
    store.upsert(pg, "lesson", {"rule": "always show empty states", "why": "the user"})
    store.export(pg)
    with (store.export_dir() / "lesson.jsonl").open("a") as f:
        f.write(json.dumps({"id": "les_ghost", "rule": "x"}) + "\n")
    with pytest.raises(store.ExportRefused):
        store.export(pg)


@pytest.mark.skipif(not REAL_EXPORT.is_dir(), reason="the store export is private and not in this copy")
def test_rebuild_from_the_real_export_files(pg, tmp_path, monkeypatch):
    d = tmp_path / "real"
    shutil.copytree(REAL_EXPORT, d)
    monkeypatch.setenv("FLYNN_KB_EXPORT", str(d))
    want = {p.stem: sum(1 for ln in p.read_text().splitlines() if ln.strip()) for p in d.glob("*.jsonl")}
    c = store.rebuild()
    assert store.LAST_REBUILD_SKIPPED == []
    got = store.stats(c)
    assert {t: got[t] for t in want} == want
    assert sum(want.values()) > 100
    # first lesson is findable by one of its own words, and a full export reproduces the rows
    first = json.loads((d / "lesson.jsonl").read_text().splitlines()[0])
    word = max((w for w in first["rule"].split() if w.isalpha()), key=len)
    assert any(r["id"] == first["id"] for r in store.search(c, word, k=50))
    out = tmp_path / "out"
    store.export(c, str(out))
    for t in want:
        a = [json.loads(x) for x in (d / f"{t}.jsonl").read_text().splitlines() if x.strip()]
        b = [json.loads(x) for x in (out / f"{t}.jsonl").read_text().splitlines() if x.strip()]
        assert a == b, t


def test_cli_against_postgres(pg, capsys):
    import kb
    row = {"system": "sc", "name": "Chip", "description": "فیلتر chip", "tier": "proposed"}
    assert kb.main(["add", "component", "--json", json.dumps(row)]) == 0
    eid = capsys.readouterr().out.strip()
    assert kb.main(["search", "فیلتر", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == eid
    assert kb.main(["rebuild"]) == 0
    assert json.loads(capsys.readouterr().out)["component"] == 1
