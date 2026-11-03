"""fstore: flynn desk's own state as documents and event logs, on files (today's layouts) or Postgres.

The Postgres half uses the server in ~/.flynn/test-db-url, database flynn_d1, and skips when the file is missing or the server is not reachable."""
import json
import threading
import time
import datetime as dt
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

import fstore

URL_FILE = Path.home() / ".flynn" / "test-db-url"


def _pg_url() -> str | None:
    if not URL_FILE.exists():
        return None
    return urlunsplit(urlsplit(URL_FILE.read_text().strip())._replace(path="/flynn_d1"))


@pytest.fixture(params=["files", "postgres"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    if request.param == "files":
        monkeypatch.delenv("FLYNN_DB_URL", raising=False)
        assert not fstore.on_postgres()
    else:
        url = _pg_url()
        if not url:
            pytest.skip("no ~/.flynn/test-db-url")
        psycopg = pytest.importorskip("psycopg")
        try:                                   # a stale url file with no server behind it is a skip, not a failure
            psycopg.connect(url, connect_timeout=3).close()
        except psycopg.Error:
            pytest.skip("the Postgres server in ~/.flynn/test-db-url is not reachable")
        monkeypatch.setenv("FLYNN_DB_URL", url)
        assert fstore.on_postgres()
        fstore.reset_for_tests()
    yield fstore


# ---------- documents ----------

def test_documents_roundtrip(store):
    assert store.get("t-docs", "a") is None
    assert store.get("t-docs", "a", {"x": 1}) == {"x": 1}
    store.put("t-docs", "a", {"title": "گزارش قیمت", "n": [1, 2]})
    store.put("t-docs", "b", [1, "two"])
    assert store.get("t-docs", "a") == {"title": "گزارش قیمت", "n": [1, 2]}
    store.put("t-docs", "a", {"title": "new"})
    assert store.list("t-docs") == {"a": {"title": "new"}, "b": [1, "two"]}
    assert store.list("t-other") == {}
    store.delete("t-docs", "a")
    store.delete("t-docs", "missing")
    assert store.list("t-docs") == {"b": [1, "two"]}


# ---------- event logs ----------

def test_event_log_append_read(store):
    assert store.read("t/s1") == []
    assert [store.append("t/s1", {"t": "user", "text": f"m{i}"}) for i in range(3)] == [0, 1, 2]
    assert [e["text"] for e in store.read("t/s1")] == ["m0", "m1", "m2"]
    assert [e["text"] for e in store.read("t/s1", since=2)] == ["m2"]
    assert store.last_at("t/s1") is not None and store.last_at("t/none") is None


def test_event_log_move_drop_and_streams(store):
    store.append("t/a", {"i": 1})
    store.append("t/a", {"i": 2})
    store.append("t/ab", {"i": 3})
    assert store.move("t/a", "t/a/history/1") is True
    assert store.read("t/a") == [] and [e["i"] for e in store.read("t/a/history/1")] == [1, 2]
    assert store.move("t/a", "t/a/history/2") is False             # nothing to move
    store.append("t/a", {"i": 4})
    with pytest.raises(ValueError):
        store.move("t/a", "t/a/history/1")                         # never merges two logs
    assert store.streams("t/a/") == ["t/a/history/1"]
    assert store.streams("t/") == ["t/a", "t/a/history/1", "t/ab"]
    future = dt.datetime.now() + dt.timedelta(hours=1)
    assert store.streams("t/", active_since=future) == []
    store.drop("t/a/history/1")
    assert store.streams("t/a/") == []


def test_appends_from_many_threads_keep_every_event(store):
    def work(n):
        for i in range(20):
            store.append("t/busy", {"w": n, "i": i})
    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    evs = store.read("t/busy")
    assert len(evs) == 120 and len({(e["w"], e["i"]) for e in evs}) == 120


# ---------- the file backend keeps today's files ----------

def test_file_layouts_write_todays_files(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYNN_DB_URL", raising=False)
    folder = tmp_path / "letters"
    fstore.files("t-letters", folder=lambda: folder, indent=1)
    fstore.put("t-letters", "file-1", {"title": "Q3"})
    assert json.loads((folder / "file-1.json").read_text()) == {"title": "Q3"}
    assert (folder / "file-1.json").read_text() == json.dumps({"title": "Q3"}, indent=1)
    (folder / "file-2.json").write_text(json.dumps({"title": "by hand"}))
    assert set(fstore.list("t-letters")) == {"file-1", "file-2"}

    root = tmp_path / "panel"
    fstore.log_files("t-panel/", path=lambda s: root / s.split("/", 1)[1] / "events.jsonl",
                     names=lambda: [f"t-panel/{p.parent.name}" for p in root.glob("*/events.jsonl")])
    (root / "f1").mkdir(parents=True)
    (root / "f1" / "events.jsonl").write_text(json.dumps({"t": "user"}) + "\n\n")
    assert fstore.append("t-panel/f1", {"t": "turn_end"}) == 1
    assert (root / "f1" / "events.jsonl").read_text().splitlines()[-1] == json.dumps({"t": "turn_end"})
    assert fstore.streams("t-panel/") == ["t-panel/f1"]

    one = tmp_path / "orgs.json"                                    # a caller's own path wins on files
    fstore.put("t-orgs", "orgs", {"orgs": []}, file=one)
    assert json.loads(one.read_text()) == {"orgs": []} and fstore.get("t-orgs", "orgs", file=one) == {"orgs": []}


def test_settings_live_in_flynn_home(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYNN_DB_URL", raising=False)
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path))
    fstore.put("settings", "desk", {"engine": "cli"})
    assert json.loads((tmp_path / "desk.json").read_text()) == {"engine": "cli"}


# ---------- Postgres ----------

def test_postgres_tables_and_idempotent_migration(store):
    if not store.on_postgres():
        pytest.skip("Postgres only")
    import psycopg
    store.put("t-docs", "a", {"k": 1})
    store._backend().migrate()                                      # safe to run again
    with psycopg.connect(_pg_url()) as c:
        cols = {t: [r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (t,))]
            for t in ("flynn_docs", "flynn_events")}
        kind = c.execute("SELECT jsonb_typeof(value) FROM flynn_docs WHERE ns = 't-docs' AND key = 'a'").fetchone()[0]
    assert cols == {"flynn_docs": ["ns", "key", "value", "updated_at"], "flynn_events": ["stream", "idx", "event", "at"]}
    assert kind == "object"
