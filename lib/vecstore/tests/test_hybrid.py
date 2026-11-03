import json

import pytest

from vecstore import Unavailable, hybrid
from vecstore.vecfile import VecFile

DOCS = {"l1": ("lesson", "Keep ClickHouse queries at max_threads one"),
        "m1": ("metric", "flash-sale share of NMV"),
        "m2": ("metric", "Seller churn rate attrition"),
        "f1": ("fact", "Old churn definition superseded")}


class Toy(hybrid.BaseAdapter):
    name, instruct = "toy", "Instruct: toy\nQuery: "

    def __init__(self, tmp):
        self.vec_path = tmp / "toy.vectors.sqlite"
        self.eval_dir = tmp / "eval"

    def entries(self):
        return [(e, t, x) for e, (t, x) in DOCS.items()]

    def lexical(self, query, etype, limit):
        q = set(query.lower().split())
        hits = [e for e, (t, x) in DOCS.items() if q & set(x.lower().split()) and (not etype or t == etype)]
        return [{"id": e, "type": DOCS[e][0], "title": DOCS[e][1]} for e in hits][:limit]

    def hydrate(self, eid):
        return {"id": eid, "type": DOCS[eid][0], "title": DOCS[eid][1]}

    def keep(self, eid):
        return eid != "f1"


@pytest.fixture
def toy(tmp_path, fake_embed):
    a = Toy(tmp_path)
    VecFile(a.vec_path).sync(a.entries(), fake_embed)
    return a


def test_hybrid_merges_and_hydrates_semantic_only_hits(toy, fake_embed):
    res = hybrid.hybrid(toy, "clickhouse threads", embed_fn=fake_embed)
    assert res[0]["id"] == "l1"
    assert {r["id"] for r in res} >= {"m1", "m2"}  # hydrated from the vector ranking
    assert "f1" not in {r["id"] for r in res}      # keep() filter


def test_hybrid_respects_type(toy, fake_embed):
    assert {r["type"] for r in hybrid.hybrid(toy, "share", etype="metric", embed_fn=fake_embed)} == {"metric"}


def test_hybrid_falls_back_to_keywords(toy, capsys):
    def boom(texts):
        raise Unavailable("no daemon")
    res = hybrid.hybrid(toy, "churn", embed_fn=boom)
    assert [r["id"] for r in res] == ["m2", "f1"]
    assert "[vecstore] semantic search unavailable (no daemon); keyword results only" in capsys.readouterr().err


def test_any_exception_falls_back(toy, capsys):
    def boom(texts):
        raise OSError("disk")
    assert hybrid.hybrid(toy, "churn", embed_fn=boom)
    assert "(OSError); keyword results only" in capsys.readouterr().err


def test_query_gets_instruction_and_prepare_query(toy, monkeypatch):
    from vecstore import config
    monkeypatch.setattr(config, "profile", lambda m=None: config.PROFILES["qwen"])
    seen = []

    def spy(texts):
        seen.extend(texts)
        raise Unavailable("stop here")
    toy.prepare_query = lambda q: q + " expanded"
    hybrid.hybrid(toy, "churn", embed_fn=spy)
    assert seen == ["Instruct: toy\nQuery: churn expanded"]


def test_gemma_query_gets_the_model_task_prompt(toy, monkeypatch):
    from vecstore import config
    monkeypatch.setattr(config, "profile", lambda m=None: config.PROFILES["gemma"])
    seen = []

    def spy(texts):
        seen.extend(texts)
        raise Unavailable("stop here")
    hybrid.hybrid(toy, "churn", embed_fn=spy)
    assert seen == ["task: search result | query: churn"]


def test_resolve_mode_needs_vectors_and_a_passing_baseline(toy, monkeypatch):
    assert hybrid.resolve_mode(toy, "lexical") == "lexical"
    assert hybrid.resolve_mode(toy, "hybrid") == "hybrid"
    assert hybrid.resolve_mode(toy) == "lexical"  # no baseline yet
    toy.eval_dir.mkdir()
    (toy.eval_dir / "baseline.json").write_text(json.dumps(
        {"modes": {"lexical": {"recall@5": 0.5}, "hybrid": {"recall@5": 0.6}}}))
    assert hybrid.resolve_mode(toy) == "hybrid"
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    assert hybrid.resolve_mode(toy) == "lexical"


def test_corrupt_vector_file_means_keywords(toy, capsys):
    toy.vec_path.write_bytes(b"not a sqlite file")
    assert hybrid.resolve_mode(toy) == "lexical"
    assert [r["id"] for r in hybrid.hybrid(toy, "churn")] == ["m2", "f1"]
    assert "keyword results only" in capsys.readouterr().err


def test_search_uses_lexical_when_mode_resolves_lexical(toy):
    def never(texts):
        raise AssertionError("should not embed")
    assert [r["id"] for r in hybrid.search(toy, "churn", mode="lexical", embed_fn=never)] == ["m2", "f1"]
