from vecstore import config, hybrid
from vecstore.chunk import chunk_file
from vecstore.mdstore import MdStore
from vecstore.vecfile import VecFile

ENTRIES = [("a", "note", "keep clickhouse threads at one"), ("b", "note", "buybox winner share")]


def test_model_is_selectable_and_gets_its_own_daemon(monkeypatch):
    monkeypatch.delenv("VECSTORE_MODEL", raising=False)
    assert config.model() == config.MODEL and config.sock_path().name == "embed.sock"
    monkeypatch.setenv("VECSTORE_MODEL", "BAAI/bge-m3")
    assert config.model() == "BAAI/bge-m3" and VecFile("x").model == "BAAI/bge-m3"
    assert config.sock_path().name.startswith("embed-") and config.lock_path() != config.home() / "daemon.lock"


def test_file_records_its_model_and_is_rebuilt_for_another(tmp_path, fake_embed, monkeypatch):
    p = tmp_path / "s.vectors.sqlite"
    VecFile(p).sync(ENTRIES, fake_embed)
    assert VecFile(p).file_model() == config.MODEL and VecFile(p).meta("model") == config.MODEL
    monkeypatch.setenv("VECSTORE_MODEL", "other/model")
    r = VecFile(p).sync(ENTRIES, fake_embed)
    assert r["embedded"] == 2 and r["model"] == "other/model"
    import sqlite3
    assert {m for (m,) in sqlite3.connect(p).execute("SELECT model FROM vecs")} == {"other/model"}


class _A(hybrid.BaseAdapter):
    instruct = ""

    def __init__(self, path):
        self.vec_path = path

    def lexical(self, query, etype, limit):
        return [{"id": "b"}]


def test_hybrid_refuses_a_file_of_another_model(tmp_path, fake_embed, monkeypatch, capsys):
    p = tmp_path / "s.vectors.sqlite"
    VecFile(p).sync(ENTRIES, fake_embed)
    monkeypatch.setenv("VECSTORE_MODEL", "other/model")
    assert hybrid.hybrid(_A(p), "clickhouse", embed_fn=fake_embed) == [{"id": "b"}]
    assert "built with" in capsys.readouterr().err


def test_chunk_file_text_override_and_mdstore_chunk_hook(tmp_path):
    (tmp_path / "a.md").write_text("# T\nfile text\n")
    assert "cleaned" in chunk_file(tmp_path / "a.md", tmp_path, "# T\ncleaned\n")[0][2]

    class Upper(MdStore):
        def chunk(self, rel):
            return [(f"{rel}#x", "x", "SHOUTED")]
    s = Upper("t", tmp_path, tmp_path / "t.vectors.sqlite", "")
    s.refresh_index()
    assert s.entries() == [("a.md#x", "root", "SHOUTED")]
