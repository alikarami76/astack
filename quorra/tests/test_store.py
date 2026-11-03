import json

import pytest

import store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    return store.connect()


RULE = {"repo": "api-app", "area": "data-storage", "rule": "Declare tables in getTableSchemaDefinition with TableSchema",
        "tier": "dominant", "rationale": "all entities do", "source": "code",
        "examples": [{"file": "src/Entity/A.php", "line": 12, "snippet": "new TableSchema("}],
        "counter_examples": [], "checked_at_sha": "abc", "verified": 1}


def test_convention_roundtrip_and_search(conn):
    eid = store.upsert(conn, "convention", RULE, origin="test")
    got = store.get(conn, eid)
    assert got["tier"] == "dominant" and json.loads(got["examples"])[0]["line"] == 12
    assert store.search(conn, "table schema", etype="convention")[0]["id"] == eid


@pytest.mark.parametrize("field,value", [("tier", "mostly"), ("source", "vibes")])
def test_convention_validation(conn, field, value):
    with pytest.raises(ValueError, match=field):
        store.upsert(conn, "convention", {**RULE, field: value})


def test_concept_persian_search_and_terms(conn):
    store.upsert(conn, "concept", {"name_en": "featuredoffer winner",
                                   "names": {"en": ["featuredoffer winner"], "fa": ["برنده بای باکس"], "codename": ["smart_featured_offer"]},
                                   "description": "the offer shown on the product page",
                                   "implementations": [{"repo": "pricing", "file": "src/Service/FeaturedofferService.php",
                                                        "symbol": "FeaturedofferService", "kind": "service"}]})
    assert store.search(conn, "برنده بای باکس", etype="concept")
    assert set(store.concept_terms(conn, "برنده بای باکس")) == {"featuredoffer winner", "برنده بای باکس", "smart_featured_offer"}
    assert store.concept_terms(conn, "unrelated zzz") == []


def test_export_rebuild(conn, tmp_path):
    store.upsert(conn, "convention", RULE)
    store.upsert(conn, "lesson", {"rule": "api-app moved to UseCase classes", "why": "the user corrected", "applies_to": "api-app"})
    store.export(conn)
    c2 = store.rebuild()
    assert store.stats(c2)["convention"] == 1 and store.stats(c2)["lesson"] == 1


# ---- fix round 1 ----
import sys

CONCEPT = {"name_en": "featuredoffer winner",
           "names": {"en": ["featuredoffer winner"], "fa": ["برنده بای باکس"], "codename": ["smart_featured_offer"]},
           "description": "the offer shown"}


def test_bulk_upsert_validates(conn):
    with pytest.raises(ValueError, match="tier"):
        store.bulk_upsert(conn, "convention", [{**RULE, "tier": "mostly"}], origin="t")


def test_rebuild_skips_and_reports_invalid_rows(conn, tmp_path, capsys):
    store.upsert(conn, "convention", RULE)
    store.upsert(conn, "convention", {**RULE, "rule": "second rule"})
    store.export(conn)
    f = tmp_path / "export" / "convention.jsonl"
    lines = f.read_text().splitlines()
    bad = json.loads(lines[0])
    bad["tier"] = "vibes"
    f.write_text(json.dumps(bad) + "\n" + lines[1] + "\nnot json\n")
    c2 = store.rebuild()
    assert store.stats(c2)["convention"] == 1
    assert len(store.LAST_REBUILD_SKIPPED) == 2
    assert "skipped" in capsys.readouterr().err


def test_rebuild_missing_export_keeps_db(conn, tmp_path):
    store.upsert(conn, "lesson", {"rule": "r", "why": "w"})
    conn.commit()
    with pytest.raises(FileNotFoundError):
        store.rebuild()  # export dir does not exist yet
    assert store.stats(store.connect())["lesson"] == 1


@pytest.mark.parametrize("names", ["x", ["a"], {"en": "notalist"}, {"en": [1]}])
def test_concept_names_validated(conn, names):
    with pytest.raises(ValueError, match="names"):
        store.upsert(conn, "concept", {**CONCEPT, "names": names})


def test_concept_implementations_validated(conn):
    with pytest.raises(ValueError, match="implementations"):
        store.upsert(conn, "concept", {**CONCEPT, "implementations": [{"repo": "x"}]})


def test_search_survives_corrupt_concept_row(conn):
    store.upsert(conn, "concept", CONCEPT)
    conn.execute("INSERT INTO concepts (id, name_en, names) VALUES ('cpt_bad1','bad','\"x\"')")
    conn.execute("INSERT INTO concepts (id, name_en, names) VALUES ('cpt_bad2','bad2','[1]')")
    conn.execute("INSERT INTO concepts (id, name_en, names) VALUES ('cpt_bad3','bad3','{oops')")
    conn.commit()
    assert store.search(conn, "featuredoffer")
    assert store.concept_terms(conn, "featuredoffer winner")


def test_hybrid_falls_back_without_numpy(conn, monkeypatch, capsys):
    store.upsert(conn, "convention", RULE)
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.delitem(sys.modules, "vectors", raising=False)
    res = store.search(conn, "table schema", etype="convention", mode="hybrid")
    assert res and res[0]["type"] == "convention"
    assert "keyword" in capsys.readouterr().err


def test_concept_terms_short_and_word_boundary(conn):
    store.upsert(conn, "concept", {"name_en": "tax", "names": {"en": ["tax"], "codename": ["vat_x"]}})
    store.upsert(conn, "concept", {"name_en": "syntax", "names": {"en": ["syntax"]}})
    assert store.concept_terms(conn, "ta") == []
    assert store.concept_terms(conn, " a ") == []
    assert set(store.concept_terms(conn, "check syntax rules")) == {"syntax"}
    assert set(store.concept_terms(conn, "how is tax computed")) == {"tax", "vat_x"}


# ---- final review I2: export never silently drops rows ----

def test_export_refuses_after_rebuild_skipped_rows(conn, tmp_path):
    store.upsert(conn, "convention", RULE)
    store.upsert(conn, "convention", {**RULE, "rule": "second rule"})
    store.export(conn)
    f = tmp_path / "export" / "convention.jsonl"
    lines = f.read_text().splitlines()
    bad = json.loads(lines[0])
    bad["tier"] = "vibes"
    f.write_text(json.dumps(bad) + "\n" + lines[1] + "\n")
    c2 = store.rebuild()
    with pytest.raises(store.ExportRefused, match="skipped"):
        store.export(c2, etype="convention")
    assert f.read_text().count("\n") == 2  # the hand-fixable bad row is still on disk


def test_export_refuses_when_file_holds_ids_the_db_lacks(conn, tmp_path):
    store.upsert(conn, "lesson", {"rule": "r1", "why": "w"})
    store.export(conn)
    other = store.connect(str(tmp_path / "other.sqlite"))  # a DB that never saw r1
    store.upsert(other, "lesson", {"rule": "r2", "why": "w"})
    with pytest.raises(store.ExportRefused, match="not in the database"):
        store.export(other, etype="lesson")
    f = tmp_path / "export" / "lesson.jsonl"
    assert "r1" in f.read_text() and "r2" not in f.read_text()
    store.export(other, etype="lesson", force=True)
    assert "r1" not in f.read_text() and "r2" in f.read_text()


def test_export_refuses_unreadable_export_lines(conn, tmp_path):
    store.upsert(conn, "lesson", {"rule": "r1", "why": "w"})
    store.export(conn)
    f = tmp_path / "export" / "lesson.jsonl"
    f.write_text(f.read_text() + "not json\n")
    with pytest.raises(store.ExportRefused):
        store.export(conn, etype="lesson")


def test_kb_cli_add_reports_refused_export(conn, tmp_path, capsys):
    import kb
    store.upsert(conn, "lesson", {"rule": "r1", "why": "w"})
    store.export(conn)
    conn.execute("DELETE FROM lessons")
    conn.commit()
    assert kb.main(["add", "lesson", "--json", '{"rule": "r2", "why": "w"}']) == 1
    assert "export refused" in capsys.readouterr().err
    assert kb.main(["export", "--force"]) == 0
