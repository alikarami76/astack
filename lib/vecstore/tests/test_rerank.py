import json
import threading
import time

import pytest

from vecstore import Unavailable, client, config, daemon, evalkit, hybrid, rerank
from vecstore.vecfile import VecFile

from test_hybrid import DOCS, Toy


def test_blend_order_uses_scores_only():
    fused = ["a", "b", "c"]
    assert rerank.blend(fused, [0.1, 0.9, 0.5], "order") == ["b", "c", "a"]


def test_blend_rrf_mixes_rerank_and_fused_ranks():
    fused = ["a", "b", "c"]
    # b is first by rerank, a first by fused: 0.7 weight on rerank puts b on top, a second
    assert rerank.blend(fused, [0.1, 0.9, 0.0], "rrf") == ["b", "a", "c"]
    # rank-based: even a tiny rerank margin counts as a full rank step at weight 0.7 ...
    assert rerank.blend(fused, [0.5, 0.5001, 0.0], "rrf")[0] == "b"
    # ... while a low rerank weight lets the fused lead stand
    assert rerank.blend(fused, [0.5, 0.5001, 0.0], "rrf", w=0.3)[0] == "a"
    assert rerank.blend(fused, [0.5, 0.5001, 0.0], "rrf", w=0.0)[0] == "a"


def test_blend_ties_keep_fused_order():
    assert rerank.blend(["a", "b", "c"], [0.5, 0.5, 0.5], "order") == ["a", "b", "c"]


def test_blend_rejects_length_mismatch():
    with pytest.raises(ValueError):
        rerank.blend(["a", "b"], [0.1], "order")


def test_rerank_ids_returns_none_on_any_failure():
    def boom(q, docs):
        raise Unavailable("no daemon")
    assert rerank.rerank_ids("q", [("a", "x"), ("b", "y")], rerank_fn=boom) is None
    assert rerank.rerank_ids("q", [("a", "x")], rerank_fn=lambda q, d: [1, 2]) is None  # wrong count
    assert rerank.rerank_ids("q", [], rerank_fn=boom) == []


def test_rerank_ids_only_touches_top_n():
    items = [(str(i), f"doc {i}") for i in range(10)]
    seen = {}

    def fn(q, docs):
        seen["n"] = len(docs)
        return [float(i) for i in range(len(docs))]  # prefers the later ones
    out = rerank.rerank_ids("q", items, top=4, mode="order", rerank_fn=fn)
    assert seen["n"] == 4
    assert out == ["3", "2", "1", "0", "4", "5", "6", "7", "8", "9"]


def test_flag_parsing(monkeypatch):
    monkeypatch.delenv("VECSTORE_RERANK", raising=False)
    assert config.rerank_flag() == "auto"
    for raw, want in (("1", "on"), ("on", "on"), ("0", "off"), ("off", "off"), ("auto", "auto"), ("junk", "auto")):
        monkeypatch.setenv("VECSTORE_RERANK", raw)
        assert config.rerank_flag() == want
    monkeypatch.setenv("VECSTORE_RERANK_TOP", "12")
    assert config.rerank_top() == 12
    monkeypatch.setenv("VECSTORE_RERANK_TOP", "x")
    assert config.rerank_top() == 30
    monkeypatch.delenv("VECSTORE_RERANK_BLEND", raising=False)
    assert config.rerank_blend() == "rrf"


def test_gate_needs_rerank_at_least_as_good(tmp_path):
    p = tmp_path / "baseline.json"
    assert not evalkit.gate_rerank(p)
    p.write_text(json.dumps({"modes": {"hybrid": {"recall@5": 0.7, "mrr": 0.7}}}))
    assert not evalkit.gate_rerank(p)  # no reranked score yet
    p.write_text(json.dumps({"modes": {"hybrid": {"recall@5": 0.7, "mrr": 0.7},
                                       "hybrid_rerank": {"recall@5": 0.7, "mrr": 0.71}}}))
    assert evalkit.gate_rerank(p)
    p.write_text(json.dumps({"modes": {"hybrid": {"recall@5": 0.7, "mrr": 0.7},
                                       "hybrid_rerank": {"recall@5": 0.69, "mrr": 0.8}}}))
    assert not evalkit.gate_rerank(p)


@pytest.fixture
def toy(tmp_path, fake_embed):
    a = Toy(tmp_path)
    VecFile(a.vec_path).sync(a.entries(), fake_embed)
    return a


def _prefer(word):
    return lambda q, docs: [1.0 if word in d.lower() else 0.0 for d in docs]


def test_hybrid_with_rerank_reorders_and_falls_back(toy, fake_embed):
    base = [r["id"] for r in hybrid.hybrid(toy, "churn share", k=5, embed_fn=fake_embed)]
    toy.rerank_text = lambda eid: DOCS[eid][1]
    got = [r["id"] for r in hybrid.hybrid(toy, "churn share", k=5, embed_fn=fake_embed, rerank=True,
                                          rerank_fn=_prefer("clickhouse"))]
    assert got[0] == "l1" and set(got) == set(base)

    def boom(q, docs):
        raise Unavailable("no reranker")
    quiet = [r["id"] for r in hybrid.hybrid(toy, "churn share", k=5, embed_fn=fake_embed, rerank=True,
                                            rerank_fn=boom)]
    assert quiet == base  # silent fallback to the fused order


def test_resolve_rerank(toy, monkeypatch):
    toy.eval_dir.mkdir()
    monkeypatch.delenv("VECSTORE_RERANK", raising=False)
    assert hybrid.resolve_rerank(toy, "off") is False
    assert hybrid.resolve_rerank(toy, "on") is True
    assert hybrid.resolve_rerank(toy, "auto") is False  # no baseline
    (toy.eval_dir / "baseline.json").write_text(json.dumps({"modes": {
        "hybrid": {"recall@5": 0.5, "mrr": 0.5}, "hybrid_rerank": {"recall@5": 0.6, "mrr": 0.5}}}))
    assert hybrid.resolve_rerank(toy, "auto") is True
    monkeypatch.setenv("VECSTORE_RERANK", "off")
    assert hybrid.resolve_rerank(toy, "auto") is False
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    assert hybrid.resolve_rerank(toy, "on") is False


def _serve(idle_s=5.0):
    enc = daemon.FakeEncoder()
    config.home().mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=daemon.serve, args=(enc, config.sock_path(), idle_s), daemon=True)
    t.start()
    for _ in range(100):
        if client.ping():
            return t
        time.sleep(0.02)
    raise AssertionError("daemon did not come up")


def test_daemon_rerank_endpoint_with_fake_model(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    t = _serve()
    scores = client.rerank("buybox winner", ["the buybox winner rule", "unrelated churn text"])
    assert len(scores) == 2 and scores[0] > scores[1]
    assert client.stop()
    t.join(2)


def test_client_rerank_unavailable_when_disabled_or_old_daemon(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    with pytest.raises(Unavailable):
        client.rerank("q", ["a"])
    monkeypatch.delenv("VECSTORE_DISABLE")
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    real = daemon.handle
    monkeypatch.setattr(daemon, "handle", lambda enc, req: {"error": "unknown op 'rerank'"}
                        if req.get("op") == "rerank" else real(enc, req))
    t = _serve()
    with pytest.raises(Unavailable, match="unknown op"):
        client.rerank("q", ["a"])
    client.stop()


def test_client_that_hangs_up_does_not_kill_the_daemon(vhome, monkeypatch):
    import socket
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    real = daemon.handle

    def slow(enc, req):
        if req.get("op") == "rerank":
            time.sleep(0.4)
        return real(enc, req)
    monkeypatch.setattr(daemon, "handle", slow)
    t = _serve()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(config.sock_path()))
    s.sendall(json.dumps({"op": "rerank", "query": "a", "docs": ["a"]}).encode())
    s.shutdown(socket.SHUT_WR)
    s.close()  # hang up before the reply
    time.sleep(0.8)
    assert client.ping()
    client.stop()
    t.join(2)
