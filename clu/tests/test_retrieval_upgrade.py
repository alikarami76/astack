import json

import numpy as np
import pytest

import store
import vec_adapter
from tests.test_search import _fake_embed, vec  # noqa: F401  (the fixture and the fake embedder)


def _rows(c):
    store.upsert(c, "metric", {"name": "Seller churn rate", "definition": "sellers who stop selling"}, tags=["sellers"])
    store.upsert(c, "fact", {"statement": "Churn counts a seller with no sales in 90 days", "topic": "churn"})
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1", "applies_to": "step:execute"})


def test_context_header_names_type_topic_and_title(kbenv, monkeypatch):
    monkeypatch.setenv("CLU_KB_HEADER", "1")
    c = store.connect()
    _rows(c)
    ents = {e: t for e, _, t in vec_adapter.make(c).entries()}
    mid = store.make_id("metric", {"name": "Seller churn rate"})
    fid = store.make_id("fact", {"statement": "Churn counts a seller with no sales in 90 days"})
    lid = store.make_id("lesson", {"rule": "Keep ClickHouse queries at max_threads=1"})
    assert ents[mid].startswith("metric · sellers · Seller churn rate\n")
    assert ents[fid].startswith("fact · churn · Churn counts a seller")
    assert ents[lid].startswith("lesson · step:execute · Keep ClickHouse")


def test_header_is_off_by_default(kbenv, monkeypatch):
    monkeypatch.delenv("CLU_KB_HEADER", raising=False)
    c = store.connect()
    _rows(c)
    assert all(not t.startswith(("metric ·", "fact ·", "lesson ·")) for _, _, t in vec_adapter.make(c).entries())


def test_aliases_and_questions_reach_the_vector_text(kbenv):
    c = store.connect()
    eid = store.upsert(c, "metric", {"name": "Rule 2", "definition": "d", "aliases": "inflated list price",
                                    "questions": "which sellers inflate prices?"})
    text = dict((e, t) for e, _, t in vec_adapter.make(c).entries())[eid]
    assert "inflated list price" in text and "which sellers inflate prices?" in text


def test_vector_text_is_normalised_before_embedding(vec):
    c = store.connect()
    store.upsert(c, "fact", {"statement": "نرخ كميسيون ۱۲"})
    seen = []
    orig = vec.embed

    def spy(texts, batch=16):
        seen.extend(texts)
        return orig(texts, batch)
    vec.embed = spy
    try:
        vec.index(c)
    finally:
        vec.embed = orig
    assert seen and all("ك" not in t and "۱" not in t for t in seen)


def test_search_with_rerank_uses_the_reranker_and_falls_back(vec, monkeypatch):
    from vecstore import client
    c = store.connect()
    _rows(c)
    vec.index(c)
    base = [r["id"] for r in store.search(c, "churn seller clickhouse", k=5, mode="hybrid")]
    assert len(base) == 3
    monkeypatch.setattr(client, "rerank", lambda q, docs: [1.0 if "ClickHouse" in d else 0.0 for d in docs])
    got = store.search(c, "churn seller clickhouse", k=5, mode="hybrid", rerank=True)
    assert got[0]["type"] == "lesson"

    def boom(q, docs):
        raise OSError("no reranker")
    monkeypatch.setattr(client, "rerank", boom)
    assert [r["id"] for r in store.search(c, "churn seller clickhouse", k=5, mode="hybrid", rerank=True)] == base


def test_lexical_mode_needs_no_daemon_and_no_numpy_path(kbenv, monkeypatch):
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    c = store.connect()
    _rows(c)
    assert store.search(c, "ClickHouse threads")[0]["type"] == "lesson"


def test_dupes_cli_reports_clusters_and_never_merges(vec, capsys):
    import kb as kbcli
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1"})
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1 always"})
    store.upsert(c, "metric", {"name": "Seller churn rate", "definition": "sellers who stop selling"})
    vec.index(c)
    before = store.stats(c)
    assert kbcli.main(["dupes", "--lane", "curated", "--threshold", "0.8"]) == 0
    out = capsys.readouterr().out
    assert "1 cluster" in out and "ClickHouse" in out and "Seller churn" not in out
    assert store.stats(c) == before
    assert kbcli.main(["dupes", "--threshold", "0.8", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["clusters"][0]["ids"] and data["threshold"] == 0.8


def test_dupes_without_vectors_explains(kbenv, capsys):
    import kb as kbcli
    store.connect()
    assert kbcli.main(["dupes"]) == 1
    assert "no vectors" in capsys.readouterr().err


def test_dupes_skips_superseded_facts(vec, capsys):
    import kb as kbcli
    c = store.connect()
    a = store.upsert(c, "fact", {"statement": "Churn is measured over ninety days of no sales"})
    b = store.upsert(c, "fact", {"statement": "Churn is measured over ninety days of no sales."})
    store.supersede(c, a, b)
    vec.index(c)
    kbcli.main(["dupes", "--threshold", "0.8"])
    assert "0 cluster" in capsys.readouterr().out


def test_eval_mode_names_and_matrix_table():
    import eval_recall
    assert eval_recall.split_mode("hybrid_rerank") == ("hybrid", True, "rrf")
    assert eval_recall.split_mode("hybrid_rerank_order") == ("hybrid", True, "order")
    assert eval_recall.split_mode("lexical") == ("lexical", False, None)
    s = {"recall@5": 0.5, "mrr": 0.25, "p50_ms": 12}
    table = eval_recall.render_matrix({"baseline": {"curated": {"lexical": s}, "schema": {"lexical": s}}})
    assert "| baseline | lexical | 0.500 | 0.250 | 0.500 | 0.250 | 12 |" in table


def test_eval_scores_rerank_mode(vec, monkeypatch, tmp_path):
    import eval_recall
    from vecstore import client
    c = store.connect()
    _rows(c)
    vec.index(c)
    sets = tmp_path / "set.jsonl"
    lid = store.make_id("lesson", {"rule": "Keep ClickHouse queries at max_threads=1"})
    sets.write_text(json.dumps({"query": "churn seller clickhouse", "expected": [lid]}) + "\n")
    monkeypatch.setattr(eval_recall, "SET", sets)
    monkeypatch.setattr(client, "rerank", lambda q, docs: [1.0 if "ClickHouse" in d else 0.0 for d in docs])
    s = eval_recall.score(c, "hybrid_rerank")
    assert s["mode"] == "hybrid_rerank" and s["mrr"] == 1.0 and "p50_ms" in s
