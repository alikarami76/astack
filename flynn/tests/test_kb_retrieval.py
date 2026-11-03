"""Phase B retrieval upgrade: shared Farsi/Arabic normalisation and the vecstore hybrid search (lib/vecstore).
Vectors use the fake hashing encoder; no model is loaded."""
import json
import os

import pytest
import store

import kb

ARABIC = {"system": "sc", "name": "Table", "description": "جدول كالا با ستون قيمت", "tier": "verified"}


def _lib_available():
    return store._NORMALIZE is not None


needs_lib = pytest.mark.skipif(not _lib_available(), reason="agent-stack lib/vecstore not next to flynn (deploy tree)")


@needs_lib
def test_persian_query_finds_arabic_spelling(monkeypatch):
    monkeypatch.delenv("VECSTORE_NORMALIZE", raising=False)
    c = store.connect()
    eid = store.upsert(c, "component", ARABIC, origin="test")
    assert [r["id"] for r in store.search(c, "جدول کالا قیمت")] == [eid]  # Persian yeh and keheh in the query
    assert [r["id"] for r in store.search(c, "قيمت")] == [eid]  # Arabic spelling in the query too


@needs_lib
def test_normalisation_switch_off_keeps_old_behaviour(monkeypatch):
    monkeypatch.setenv("VECSTORE_NORMALIZE", "0")
    c = store.connect()
    store.upsert(c, "component", ARABIC, origin="test")
    assert store.search(c, "قیمت") == []


def test_store_works_without_the_library(monkeypatch):
    """The flynn desk deploy copies only flynn/: without lib/vecstore search is plain keyword search."""
    monkeypatch.setattr(store, "_NORMALIZE", None)
    c = store.connect()
    eid = store.upsert(c, "component", {**ARABIC, "description": "plain filter chip"}, origin="test")
    assert [r["id"] for r in store.search(c, "filter chip")] == [eid]


@needs_lib
def test_hybrid_search_with_fake_vectors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    import vec_adapter
    from vecstore.daemon import FakeEncoder
    from vecstore import hybrid
    from vecstore.vecfile import VecFile
    c = store.connect()
    a_id = store.upsert(c, "component", {"system": "sc", "name": "Chip", "description": "filter chip", "tier": "proposed"}, origin="t")
    b_id = store.upsert(c, "lesson", {"rule": "never stretch a logo", "why": "brand"}, origin="t")
    ad = vec_adapter.make(c)
    assert ad.vec_path == tmp_path / "kb.vectors.sqlite"
    assert {e for e, _, _ in ad.entries()} == {a_id, b_id}
    VecFile(ad.vec_path, model="fake-hash-64").sync(ad.entries(), FakeEncoder().encode)
    monkeypatch.setattr("vecstore.config.model", lambda: "fake-hash-64")
    res = hybrid.hybrid(ad, "stretch logo", k=5, embed_fn=FakeEncoder().encode)
    assert res[0]["id"] == b_id and res[0]["type"] == "lesson"
    assert "never stretch" in ad.rerank_text(b_id)


def test_cli_search_hybrid_falls_back_to_keywords(capsys, monkeypatch):
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    assert kb.main(["add", "component", "--json", json.dumps({"system": "sc", "name": "Chip",
                                                               "description": "filter chip", "tier": "proposed"})]) == 0
    eid = capsys.readouterr().out.strip()
    assert kb.main(["search", "filter", "--mode", "hybrid", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == eid
