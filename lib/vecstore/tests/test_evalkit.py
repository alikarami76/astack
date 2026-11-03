import json

import pytest

from vecstore import Unavailable, evalkit
from vecstore.vecfile import VecFile


def test_score_math():
    items = [{"query": "a", "expected": ["x"]}, {"query": "b", "expected": ["y", "z"]}]
    ranked = {"a": ["x", "q"], "b": ["q", "y"]}
    s = evalkit.score(lambda q: ranked[q], items)
    assert s["queries"] == 2 and s["recall@5"] == 0.75 and s["hit@5"] == 1.0 and s["mrr"] == 0.75


def test_regressions_and_gate(tmp_path):
    base = {"queries": 2, "modes": {"lexical": {"recall@5": 0.70, "recall@10": 0.80}}}
    ok = {"lexical": {"queries": 2, "recall@5": 0.68, "recall@10": 0.80}}
    bad = {"lexical": {"queries": 2, "recall@5": 0.60, "recall@10": 0.80}}
    assert evalkit.regressions(ok, base) == []
    assert evalkit.regressions(bad, base)
    p = tmp_path / "baseline.json"
    assert not evalkit.gate(p)
    evalkit.save_baseline(p, {"lexical": {"queries": 2, "recall@5": 0.5, "recall@10": 0.6, "hit@5": 1, "mrr": 0.5},
                              "hybrid": {"queries": 2, "recall@5": 0.5, "recall@10": 0.7, "hit@5": 1, "mrr": 0.6}})
    assert evalkit.gate(p)  # equal recall@5 passes
    saved = json.loads(p.read_text())
    assert saved["queries"] == 2 and set(saved["modes"]) == {"lexical", "hybrid"}


def test_load_set_skips_unlabelled(tmp_path):
    p = tmp_path / "recall_set.jsonl"
    p.write_text('{"query": "a", "expected": ["x"]}\n\n{"query": "b", "expected": []}\n')
    assert [i["query"] for i in evalkit.load_set(p)] == ["a"]


class EvalAdapter:
    name, instruct = "eval", "Instruct: eval\nQuery: "

    def __init__(self, tmp):
        self.vec_path = tmp / "eval.vectors.sqlite"
        self.eval_dir = tmp / "eval"

    def entries(self):
        return [("x", "doc", "test doc"), ("y", "doc", "another test")]

    def lexical(self, query, etype, limit):
        return [{"id": "x"}, {"id": "y"}]


@pytest.fixture
def eval_adapter(tmp_path, fake_embed):
    a = EvalAdapter(tmp_path)
    a.eval_dir.mkdir()
    a.eval_dir.joinpath("recall_set.jsonl").write_text('{"query": "test", "expected": ["x", "y"]}\n')
    VecFile(a.vec_path).sync(a.entries(), fake_embed)
    return a


def test_run_scores_both_modes_with_vectors(eval_adapter, fake_embed):
    result = evalkit.run(eval_adapter, embed_fn=fake_embed)
    assert set(result.keys()) == {"lexical", "hybrid"}
    assert result["lexical"]["queries"] == 1
    assert result["hybrid"]["queries"] == 1


def test_run_scores_lexical_only_when_vectors_unavailable(eval_adapter, capsys):
    def boom(texts):
        raise Unavailable("no daemon")
    result = evalkit.run(eval_adapter, embed_fn=boom)
    assert set(result.keys()) == {"lexical"}
    assert result["lexical"]["queries"] == 1
    assert "vecstore eval: vectors unavailable (no daemon); scoring lexical only" in capsys.readouterr().err


def test_run_scores_hybrid_rerank_when_asked(eval_adapter, fake_embed):
    eval_adapter.rerank_text = lambda eid: eid
    result = evalkit.run(eval_adapter, embed_fn=fake_embed, rerank_fn=lambda q, docs: [0.0] * len(docs))
    assert set(result.keys()) == {"lexical", "hybrid", "hybrid_rerank"}


def test_run_skips_rerank_when_the_reranker_is_down(eval_adapter, fake_embed, capsys):
    def boom(q, docs):
        raise Unavailable("no reranker")
    result = evalkit.run(eval_adapter, embed_fn=fake_embed, rerank_fn=boom)
    assert set(result.keys()) == {"lexical", "hybrid"}
    assert "reranker unavailable (no reranker)" in capsys.readouterr().err


def test_score_reports_median_latency():
    s = evalkit.score(lambda q: ["x"], [{"query": "a", "expected": ["x"]}] * 3)
    assert s["p50_ms"] >= 0 and s["mrr"] == 1.0


def test_matrix_builds_once_per_normalisation_and_scores_tags(tmp_path, fake_embed, monkeypatch):
    monkeypatch.delenv("VECSTORE_NORMALIZE", raising=False)
    seen = []

    def build(norm):
        import os
        seen.append((norm, os.environ.get("VECSTORE_NORMALIZE")))
        a = EvalAdapter(tmp_path / f"n{norm}")
        a.rerank_text = lambda eid: eid
        return a

    items = [{"query": "test", "expected": ["x"], "tag": "farsi"}, {"query": "another", "expected": ["y"]}]
    res = evalkit.matrix(build, items, rerank_fn=lambda q, docs: [1.0 if d == "y" else 0.0 for d in docs],
                         embed_fn=fake_embed, progress=False)
    assert seen == [("0", "0"), ("1", "1")]  # built once per setting, with the variable set
    assert list(res) == [s[0] for s in evalkit.STEPS]
    assert set(res["baseline"]) == {"lexical", "hybrid", "lexical/farsi", "hybrid/farsi"}
    assert res["baseline"]["lexical/farsi"]["queries"] == 1
    assert res["+ rerank (rrf blend)"]["hybrid_rerank"]["queries"] == 2
    import os
    assert "VECSTORE_NORMALIZE" not in os.environ  # restored
    table = evalkit.render_matrix(res, ("farsi",))
    assert table.count("\n") == 1 + 5 and "| + rerank (rrf blend) | hybrid_rerank |" in table
