"""Phase B retrieval upgrade: shared Farsi/Arabic normalisation and hybrid search through lib/vecstore.
Vectors use the fake hashing encoder; no model is loaded."""
import json

import pytest

import store

CONCEPT = {"name_en": "featuredoffer", "names": {"en": ["featuredoffer"], "fa": ["بای باکس", "برنده‌ی جعبه خريد"]},
           "description": "which offer wins the product page"}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    monkeypatch.delenv("VECSTORE_NORMALIZE", raising=False)
    return store.connect()


def test_persian_query_meets_arabic_spelling(conn):
    eid = store.upsert(conn, "concept", CONCEPT, origin="test")
    assert eid in [r["id"] for r in store.search(conn, "خرید")]  # Persian yeh in the query, Arabic in the row


def test_concept_expansion_uses_normalised_names(conn):
    store.upsert(conn, "concept", CONCEPT, origin="test")
    assert "featuredoffer" in store.expand_query(conn, "برنده‌ی جعبه خرید چطور انتخاب میشه")


def test_normalisation_off(conn, monkeypatch):
    monkeypatch.setenv("VECSTORE_NORMALIZE", "0")
    store.upsert(conn, "concept", CONCEPT, origin="test")
    assert store.search(conn, "خرید") == []


def test_vectors_is_a_shim_over_vecstore(conn, monkeypatch, tmp_path):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    monkeypatch.setattr("vecstore.config.model", lambda: "fake-hash-64")
    from vecstore.daemon import FakeEncoder
    import vectors
    monkeypatch.setattr(vectors, "embed", lambda texts, batch=16: FakeEncoder().encode(texts))
    store.upsert(conn, "concept", CONCEPT, origin="test")
    rule = store.upsert(conn, "convention", {"repo": "shop-api", "area": "data-storage", "tier": "dominant",
                                             "source": "code", "rule": "Declare tables with TableSchema"}, origin="t")
    out = vectors.index(conn)
    assert out["embedded"] == 2 and vectors.vec_path().exists()
    res = store.search(conn, "declare tables tableschema", mode="hybrid")
    assert res[0]["id"] == rule
    store.upsert(conn, "convention", {"repo": "shop-api", "area": "data-storage", "tier": "dominant", "source": "code",
                                      "rule": "Old rule about tables", "superseded_by": rule}, origin="t")
    vectors.index(conn)
    assert all(not r["title"].startswith("Old") for r in store.search(conn, "old rule tables", mode="hybrid"))


def test_cli_dupes_needs_vectors(conn, capsys):
    import kb
    assert kb.main(["dupes"]) == 1
    assert "embed" in capsys.readouterr().err
