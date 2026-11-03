"""flynn desk's own state: documents and event logs, kept in files (today) or in Postgres.

  documents   get(ns, key, default) · put(ns, key, value) · list(ns) -> {key: value} · delete(ns, key)
  event logs  append(stream, event) -> index · read(stream, since=0) · move(stream, new_stream) · drop(stream)
              streams(prefix, active_since=None) · last_at(stream)

The switch is the env FLYNN_DB_URL. Unset, everything stays in the files each module has always used under
~/.flynn: a module tells the file backend where its namespace lives with files() or log_files(), so the user's local
data is read and written in place and never migrated. Set, the same calls go to two Postgres tables, created on
first use: flynn_docs(ns, key, value jsonb, updated_at) and flynn_events(stream, idx, event jsonb, at).

Values are anything json.dumps accepts. Event indexes count from 0 within a stream. Stdlib only on files;
psycopg 3 on Postgres (imported only then). Never print FLYNN_DB_URL: it holds a password.
"""
from __future__ import annotations

import builtins
import contextlib
import datetime as dt
import json
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


def flynn_home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


def on_postgres() -> bool:
    return bool(os.environ.get("FLYNN_DB_URL"))


# ---------- file layouts ----------

@dataclass
class _DocFiles:
    path: Callable[[str, str], Path]
    keys: Callable[[str], Iterable[str]]
    indent: int | None = None


@dataclass
class _LogFiles:
    path: Callable[[str], Path]
    names: Callable[[], Iterable[str]]


_DOCS: dict[str, _DocFiles] = {}
_LOGS: dict[str, _LogFiles] = {}


def _safe(part: str) -> str:
    if not part or part in (".", "..") or "\\" in part or "\x00" in part:
        raise ValueError(f"bad store name {part!r}")
    return part


def files(ns: str, folder: Callable[[], Path] | None = None, *, path: Callable[[str, str], Path] | None = None,
          keys: Callable[[str], Iterable[str]] | None = None, indent: int | None = None) -> None:
    """Where a documents namespace lives on the file backend. `folder` holds one <key>.json per key; or give
    `path(ns, key)` and `keys(ns)`. An ns ending in "/" covers every ns under it. Callables are evaluated on
    each call, so env changes and patched module paths are honoured."""
    if folder:
        path = path or (lambda n, k: folder() / f"{_safe(k)}.json")
        keys = keys or (lambda n: sorted(p.stem for p in folder().glob("*.json")))
    _DOCS[ns] = _DocFiles(path, keys or (lambda n: []), indent)


def log_files(prefix: str, path: Callable[[str], Path], names: Callable[[], Iterable[str]]) -> None:
    """Where event logs live on the file backend: `path(stream)` is its JSONL file, `names()` every stream there."""
    _LOGS[prefix] = _LogFiles(path, names)


def _match(reg: dict, name: str):
    if name in reg:
        return reg[name]
    best = max((p for p in reg if p.endswith("/") and name.startswith(p)), key=len, default=None)
    return reg[best] if best else None


def _store_root() -> Path:
    return flynn_home() / "store"


def _default_doc(ns: str) -> _DocFiles:
    d = lambda: _store_root().joinpath(*[_safe(p) for p in ns.split("/")])
    return _DocFiles(lambda n, k: d() / f"{_safe(k)}.json", lambda n: sorted(p.stem for p in d().glob("*.json")))


def _default_log_path(stream: str) -> Path:
    parts = [_safe(p) for p in stream.split("/")]
    return _store_root().joinpath("streams", *parts[:-1], parts[-1] + ".jsonl")


def _default_log_names() -> list[str]:
    root = _store_root() / "streams"
    return [str(p.relative_to(root).with_suffix("")) for p in root.rglob("*.jsonl")] if root.exists() else []


# Small JSON settings files straight in ~/.flynn (desk.json, penpot.json, spaces.json, ...).
files("settings", folder=flynn_home, indent=1)


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


class _Files:
    _append_lock = threading.Lock()      # one process owns the files; this keeps indexes exact across threads

    def _doc(self, ns, key, file):
        lay = _match(_DOCS, ns) or _default_doc(ns)
        return (Path(file) if file else lay.path(ns, key)), lay

    def get(self, ns, key, default, file):
        p, _ = self._doc(ns, key, file)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def put(self, ns, key, value, file):
        p, lay = self._doc(ns, key, file)
        _atomic_write(p, json.dumps(value, ensure_ascii=False, indent=lay.indent))

    def list(self, ns):
        lay = _match(_DOCS, ns) or _default_doc(ns)
        out = {}
        for k in lay.keys(ns):
            v = self.get(ns, k, None, None)
            if v is not None:
                out[k] = v
        return out

    def delete(self, ns, key, file):
        p, _ = self._doc(ns, key, file)
        p.unlink(missing_ok=True)

    def _log(self, stream) -> Path:
        lay = _match(_LOGS, stream)
        return lay.path(stream) if lay else _default_log_path(stream)

    @staticmethod
    def _lines(p: Path) -> list[str]:
        try:
            return [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        except OSError:
            return []

    def append(self, stream, event):
        p = self._log(stream)
        with self._append_lock:
            n = len(self._lines(p))
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return n

    def read(self, stream, since):
        out = []
        for x in self._lines(self._log(stream))[since:]:
            try:
                out.append(json.loads(x))
            except ValueError:               # a line cut short by a crash; keep the rest
                pass
        return out

    def move(self, stream, new):
        src, dst = self._log(stream), self._log(new)
        with self._append_lock:
            if not src.exists():
                return False
            if dst.exists():
                raise ValueError(f"{new} already exists")
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        return True

    def drop(self, stream):
        self._log(stream).unlink(missing_ok=True)

    def streams(self, prefix, active_since):
        names = set(_default_log_names())
        for lay in _LOGS.values():
            names.update(lay.names())
        out = []
        for n in sorted(n for n in names if n.startswith(prefix)):
            if active_since is not None:
                at = self.last_at(n)
                if at is None or at < active_since:
                    continue
            out.append(n)
        return out

    def last_at(self, stream):
        try:
            return dt.datetime.fromtimestamp(self._log(stream).stat().st_mtime)
        except OSError:
            return None


# ---------- Postgres ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS flynn_docs (
  ns text NOT NULL, key text NOT NULL, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ns, key));
CREATE TABLE IF NOT EXISTS flynn_events (
  stream text NOT NULL, idx integer NOT NULL, event jsonb NOT NULL, at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (stream, idx));
"""


class _Pg:
    def __init__(self, url: str, size: int = 8):
        import psycopg                                  # only needed when FLYNN_DB_URL is set
        from psycopg.types.json import Jsonb
        self._psycopg, self._Jsonb = psycopg, Jsonb
        self._url = url
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(size)  # the HTTP server is threaded; cap open connections
        self._ready = False
        self._ready_lock = threading.Lock()

    def __repr__(self):
        return "fstore Postgres backend"                # never the URL

    @contextlib.contextmanager
    def _conn(self):
        with self._slots:
            try:
                c = self._idle.get_nowait()
            except queue.Empty:
                c = None
            if c is None or c.closed or c.broken:
                c = self._psycopg.connect(self._url, autocommit=True)
            ok = False
            try:
                yield c
                ok = True
            finally:
                if ok and not c.closed and not c.broken:
                    self._idle.put(c)
                else:
                    c.close()

    def _run(self, fn):
        if not self._ready:
            self.migrate()
        try:
            with self._conn() as c:
                return fn(c)
        except self._psycopg.OperationalError:
            # A pooled connection can die with a database restart; try once on a fresh one.
            with self._conn() as c:
                return fn(c)

    def migrate(self) -> None:
        with self._ready_lock, self._conn() as c, c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('flynn_fstore_schema'))")   # two processes starting at once
            c.execute(SCHEMA)
            self._ready = True

    def get(self, ns, key, default, file):
        row = self._run(lambda c: c.execute("SELECT value FROM flynn_docs WHERE ns = %s AND key = %s", (ns, key)).fetchone())
        return row[0] if row else default

    def put(self, ns, key, value, file):
        self._run(lambda c: c.execute(
            "INSERT INTO flynn_docs (ns, key, value) VALUES (%s, %s, %s) "
            "ON CONFLICT (ns, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (ns, key, self._Jsonb(value))))

    def list(self, ns):
        rows = self._run(lambda c: c.execute("SELECT key, value FROM flynn_docs WHERE ns = %s", (ns,)).fetchall())
        return dict(sorted(rows))

    def delete(self, ns, key, file):
        self._run(lambda c: c.execute("DELETE FROM flynn_docs WHERE ns = %s AND key = %s", (ns, key)))

    @staticmethod
    def _lock(c, *streams):
        for s in sorted(streams):                       # same order everywhere, so two moves never deadlock
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("flynn_events:" + s,))

    def append(self, stream, event):
        def go(c):
            with c.transaction():
                self._lock(c, stream)
                n = c.execute("SELECT coalesce(max(idx) + 1, 0) FROM flynn_events WHERE stream = %s", (stream,)).fetchone()[0]
                c.execute("INSERT INTO flynn_events (stream, idx, event) VALUES (%s, %s, %s)", (stream, n, self._Jsonb(event)))
                return n
        return self._run(go)

    def read(self, stream, since):
        rows = self._run(lambda c: c.execute(
            "SELECT event FROM flynn_events WHERE stream = %s AND idx >= %s ORDER BY idx", (stream, since)).fetchall())
        return [r[0] for r in rows]

    def move(self, stream, new):
        def go(c):
            with c.transaction():
                self._lock(c, stream, new)
                if c.execute("SELECT 1 FROM flynn_events WHERE stream = %s LIMIT 1", (new,)).fetchone():
                    raise ValueError(f"{new} already exists")
                return c.execute("UPDATE flynn_events SET stream = %s WHERE stream = %s", (new, stream)).rowcount > 0
        return self._run(go)

    def drop(self, stream):
        self._run(lambda c: c.execute("DELETE FROM flynn_events WHERE stream = %s", (stream,)))

    def streams(self, prefix, active_since):
        since = active_since.astimezone() if active_since else None
        rows = self._run(lambda c: c.execute(
            "SELECT stream FROM flynn_events WHERE left(stream, length(%s)) = %s GROUP BY stream "
            "HAVING %s::timestamptz IS NULL OR max(at) >= %s", (prefix, prefix, since, since)).fetchall())
        return sorted(r[0] for r in rows)               # Python order, the same as on files (not the collation's)

    def last_at(self, stream):
        row = self._run(lambda c: c.execute("SELECT max(at) FROM flynn_events WHERE stream = %s", (stream,)).fetchone())
        return row[0].astimezone().replace(tzinfo=None) if row and row[0] else None

    def reset_for_tests(self) -> None:
        def go(c):
            db = c.execute("SELECT current_database()").fetchone()[0]
            if not (db == "flynn_d1" or db.endswith("_test")):
                raise RuntimeError(f"refusing to empty the flynn tables in database {db}")
            c.execute("TRUNCATE flynn_docs, flynn_events")
        self._run(go)


_FILES = _Files()
_PG: dict[str, _Pg] = {}
_pg_lock = threading.Lock()


def _backend():
    url = os.environ.get("FLYNN_DB_URL")
    if not url:
        return _FILES
    with _pg_lock:
        if url not in _PG:
            _PG[url] = _Pg(url)
        return _PG[url]


# ---------- the API ----------

def get(ns: str, key: str, default: Any = None, *, file: Path | None = None) -> Any:
    """`file` is a caller's own path for this one document; only the file backend uses it."""
    return _backend().get(ns, key, default, file)


def put(ns: str, key: str, value: Any, *, file: Path | None = None) -> None:
    _backend().put(ns, key, value, file)


def list(ns: str) -> dict[str, Any]:  # noqa: A001 - the API reads best as fstore.list
    return _backend().list(ns)


def delete(ns: str, key: str, *, file: Path | None = None) -> None:
    _backend().delete(ns, key, file)


def append(stream: str, event: Any) -> int:
    return _backend().append(stream, event)


def read(stream: str, since: int = 0) -> builtins.list:
    return _backend().read(stream, max(0, int(since)))


def move(stream: str, new_stream: str) -> bool:
    """Archive a log under a new name. False when there was nothing to move; ValueError if the name is taken."""
    return _backend().move(stream, new_stream)


def drop(stream: str) -> None:
    _backend().drop(stream)


def streams(prefix: str, active_since: dt.datetime | None = None) -> builtins.list[str]:
    """Every stream whose name starts with `prefix`, sorted; with `active_since` (local time), only those written since."""
    return _backend().streams(prefix, active_since)


def last_at(stream: str) -> dt.datetime | None:
    """When the stream was last written (local time), or None if it does not exist."""
    return _backend().last_at(stream)


def reset_for_tests() -> None:
    """Empty both tables. Refuses unless the database is flynn_d1 or ends in _test."""
    b = _backend()
    if isinstance(b, _Pg):
        b.reset_for_tests()
