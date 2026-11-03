from pathlib import Path
import numpy as np

from vecstore.vecfile import VecFile, text_hash

ENTRIES = [("a", "note", "keep clickhouse threads at one"), ("b", "note", "buybox winner share"),
           ("c", "metric", "flash-sale share of nmv")]


def _counting(fake_embed):
    calls = []

    def fn(texts):
        calls.append(list(texts))
        return fake_embed(texts)
    return fn, calls


def test_sync_embeds_once_then_only_changes(tmp_path, fake_embed, monkeypatch):
    monkeypatch.setenv("VECSTORE_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    vf = VecFile(tmp_path / "s.vectors.sqlite")
    fn, calls = _counting(fake_embed)
    assert vf.sync(ENTRIES, fn) == {"embedded": 3, "removed": 0, "total": 3, "model": vf.model}
    assert vf.sync(ENTRIES, fn)["embedded"] == 0
    changed = [ENTRIES[0], ("b", "note", "buybox winner share per seller")]
    r = vf.sync(changed, fn)
    assert (r["embedded"], r["removed"], r["total"]) == (1, 1, 2)
    assert calls[-1] == ["buybox winner share per seller"]
    assert vf.count() == 2
    assert vf.meta("last_sync")


def test_hash_is_sha1_prefix_like_clu():
    import hashlib
    assert text_hash("x") == hashlib.sha1(b"x").hexdigest()[:16]


def test_rows_of_another_model_are_ignored_and_reembedded(tmp_path, fake_embed):
    p = tmp_path / "s.vectors.sqlite"
    VecFile(p, model="old-model").sync(ENTRIES, fake_embed)
    vf = VecFile(p, model="new-model")
    assert vf.matrix()[1] is None
    assert vf.sync(ENTRIES, fake_embed)["embedded"] == 3
    assert vf.count() == 3


def test_max_embed_skips_large_backlogs(tmp_path, fake_embed):
    vf = VecFile(tmp_path / "s.vectors.sqlite")
    r = vf.sync(ENTRIES, fake_embed, max_embed=2)
    assert r["skipped"] and r["pending"] == 3 and vf.count() == 0


def test_matrix_filters_by_type_and_refreshes_after_writes(tmp_path, fake_embed):
    vf = VecFile(tmp_path / "s.vectors.sqlite")
    vf.sync(ENTRIES, fake_embed)
    ids, mat = vf.matrix("metric")
    assert ids == ["c"] and mat.shape == (1, 64) and mat.dtype == np.float32
    vf.sync(ENTRIES[:1], fake_embed)
    assert vf.matrix()[0] == ["a"]


def test_clear_and_missing_file(tmp_path, fake_embed):
    vf = VecFile(tmp_path / "none.vectors.sqlite")
    assert vf.matrix() == ([], None) and vf.count() == 0
    vf.sync(ENTRIES, fake_embed)
    vf.clear()
    assert vf.count() == 0


def test_plan_does_not_create_missing_file(tmp_path):
    vf = VecFile(tmp_path / "sub" / "missing.db")
    todo, stale, total = vf.plan([("a", "t", "text a")])
    assert [t[0] for t in todo] == ["a"] and stale == [] and total == 1
    assert not vf.path.exists()


def test_gemma_documents_get_the_document_prompt(tmp_path, fake_embed, monkeypatch):
    monkeypatch.setenv("VECSTORE_MODEL", "google/embeddinggemma-2")
    vf = VecFile(tmp_path / "g.vectors.sqlite")
    fn, calls = _counting(fake_embed)
    vf.sync(ENTRIES[:1], fn)
    assert calls[-1] == ["title: none | text: keep clickhouse threads at one"]


def test_only_one_daemon_is_kept(tmp_path, monkeypatch):
    import json
    import socket
    import threading
    from vecstore import client
    import tempfile
    short = Path(tempfile.mkdtemp(dir="/tmp"))  # AF_UNIX paths must stay under ~104 bytes
    monkeypatch.setenv("VECSTORE_HOME", str(short))
    other = short / "embed-other.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(other))
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        got.append(json.loads(b"".join(iter(lambda: conn.recv(1 << 12), b""))))
        conn.send(b'{"stopping": true}')
        conn.close()
    t = threading.Thread(target=serve)
    t.start()
    client._stop_other_daemons()
    t.join(3)
    srv.close()
    assert got == [{"op": "stop"}]
