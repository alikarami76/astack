import numpy as np

from vecstore import dedupe


def _unit(*rows):
    m = np.array(rows, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_clusters_group_near_duplicates_and_skip_singletons():
    ids = ["a", "b", "c", "d", "e"]
    mat = _unit([1, 0, 0], [0.99, 0.05, 0], [0, 1, 0], [0.01, 0.999, 0], [0, 0, 1])
    out = dedupe.clusters(ids, mat, 0.95)
    assert sorted(c["ids"] for c in out) == [["a", "b"], ["c", "d"]]
    assert all(0.95 <= c["min_sim"] <= c["max_sim"] <= 1.0001 for c in out)


def test_threshold_is_inclusive_and_chains_are_flagged():
    ids = ["a", "b", "c"]
    # a~b and b~c above 0.9, a~c below: one single-link cluster, but flagged as a chain
    mat = _unit([1, 0, 0], [0.8, 0.6, 0], [0.28, 0.96, 0])
    out = dedupe.clusters(ids, mat, 0.79)
    assert len(out) == 1 and out[0]["ids"] == ["a", "b", "c"] and out[0]["chain"] is True
    mat2 = _unit([1, 0, 0], [0.99, 0.05, 0])
    assert dedupe.clusters(["a", "b"], mat2, 0.9)[0]["chain"] is False


def test_empty_and_single_inputs():
    assert dedupe.clusters([], None, 0.9) == []
    assert dedupe.clusters(["a"], _unit([1, 0]), 0.9) == []


def test_large_input_matches_bruteforce():
    rng = np.random.default_rng(0)
    base = rng.normal(size=(40, 16))
    mat = np.vstack([base, base[:10] + rng.normal(scale=0.01, size=(10, 16))]).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    ids = [f"i{n}" for n in range(len(mat))]
    out = dedupe.clusters(ids, mat, 0.98, block=7)
    assert sorted(len(c["ids"]) for c in out) == [2] * 10


def test_report_hydrates_members_and_skips_dropped(tmp_path, fake_embed):
    from vecstore import dedupe
    from vecstore.vecfile import VecFile

    class A:
        vec_path = tmp_path / "d.vectors.sqlite"

        def keep(self, eid):
            return eid != "c"

        def hydrate(self, eid):
            return {"type": "doc", "title": f"title {eid}"}

    VecFile(A.vec_path).sync([("a", "doc", "buybox winner rule"), ("b", "doc", "buybox winner rule"),
                              ("c", "doc", "buybox winner rule"), ("d", "doc", "flash sale deals share")], fake_embed)
    rep = dedupe.report(A(), 0.99)
    assert rep["rows"] == 3 and len(rep["clusters"]) == 1
    assert [m["id"] for m in rep["clusters"][0]["members"]] == ["a", "b"]
    assert rep["clusters"][0]["members"][0]["title"] == "title a"
    assert "1 cluster(s)" in dedupe.render(rep)
    A.vec_path = tmp_path / "none.sqlite"
    assert dedupe.report(A())["rows"] == 0
