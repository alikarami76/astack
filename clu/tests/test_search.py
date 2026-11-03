import hashlib

import numpy as np
import pytest

import store


def _seed(c):
    store.upsert(c, "glossary", {"term_fa": "حراج‌ویژه", "term_en": "Flash sale (flash_offer)", "meaning": "headline promotion"})
    store.upsert(c, "metric", {"name": "Flash sale share of GMV", "definition": "flash_offer GMV over site GMV"})
    store.upsert(c, "metric", {"name": "Seller churn rate", "aliases": "ریزش فروشنده; attrition", "definition": "sellers who stop selling"})
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1"})


def test_stopwords_do_not_match_everything(kbenv):
    c = store.connect()
    _seed(c)
    assert store.search(c, "how are the") == []


def test_farsi_query_reaches_english_rows_through_glossary_and_aliases(kbenv):
    c = store.connect()
    _seed(c)
    assert "Flash sale share of GMV" in [r["title"] for r in store.search(c, "حراج‌ویژه")]
    assert store.search(c, "ریزش فروشنده")[0]["title"] == "Seller churn rate"
    assert store.search(c, "seller attrition")[0]["title"] == "Seller churn rate"


def _fake_embed(texts, batch=16):
    """Bag-of-words hashing embedder: deterministic, no model needed."""
    out = np.zeros((len(texts), 64), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in store._terms(t.split("Query: ")[-1]):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
    n = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.where(n == 0, 1, n)


@pytest.fixture
def vec(kbenv, monkeypatch):
    import vectors
    monkeypatch.setenv("CLU_KB_VECTORS", str(kbenv / "kb.vectors.sqlite"))
    monkeypatch.setattr(vectors, "embed", _fake_embed)
    return vectors


def test_index_is_incremental_and_drops_deleted_rows(vec):
    c = store.connect()
    _seed(c)
    first = vec.index(c)
    assert first["embedded"] == first["total"] == 4
    assert vec.index(c)["embedded"] == 0
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1", "why": "shared cluster"})
    c.execute("DELETE FROM fts WHERE title = 'Seller churn rate'")
    again = vec.index(c)
    assert again["embedded"] == 1 and again["removed"] == 1
    assert store.vectors_ready()


def test_hybrid_fuses_both_rankers_and_respects_type(vec):
    c = store.connect()
    _seed(c)
    vec.index(c)
    res = store.search(c, "clickhouse threads", mode="hybrid")
    assert res[0]["type"] == "lesson"
    assert all(r["type"] == "metric" for r in store.search(c, "share", etype="metric", mode="hybrid"))


def test_hybrid_falls_back_to_keywords_when_model_is_missing(vec, monkeypatch, capsys):
    c = store.connect()
    _seed(c)
    vec.index(c)
    monkeypatch.setattr(vec, "embed", lambda *a, **k: (_ for _ in ()).throw(OSError("no model")))
    res = store.search(c, "clickhouse", mode="hybrid")
    assert res and res[0]["type"] == "lesson"
    assert "keyword results only" in capsys.readouterr().err


def test_recall_cli_groups_results(kbenv, capsys):
    import kb as kbcli
    c = store.connect()
    _seed(c)
    kbcli.main(["recall", "seller churn"])
    out = capsys.readouterr().out
    assert "## top" in out and "## metrics" in out and "Seller churn rate" in out


def test_recall_regression_check():
    import eval_recall
    base = {"modes": {"lexical": {"recall@5": 0.70, "recall@10": 0.80}}}
    assert eval_recall.regressions({"lexical": {"recall@5": 0.68, "recall@10": 0.80}}, base) == []
    assert eval_recall.regressions({"lexical": {"recall@5": 0.60, "recall@10": 0.80}}, base)
