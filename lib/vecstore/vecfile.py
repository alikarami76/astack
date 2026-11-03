"""One store's vector file: vectors keyed by entry id and a hash of the embedded text (clu's format, unchanged).
An entry is embedded again only when its text changes; entries that disappear are deleted on sync. The file records
the model it was built with (meta key "model"); syncing with another model clears and re-embeds the whole file."""
from __future__ import annotations

import datetime as _dt
import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from . import config
from .normalize import normalize

EmbedFn = Callable[[list[str]], np.ndarray]
_CACHE: dict[str, tuple] = {}  # path -> (stamp, ids, types, matrix)


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


class VecFile:
    def __init__(self, path, model: str | None = None):
        self.path = Path(path)
        self.model = model or config.model()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE IF NOT EXISTS vecs (eid TEXT PRIMARY KEY, etype TEXT, h TEXT, model TEXT, vec BLOB)")
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        return c

    def plan(self, entries: Iterable[tuple[str, str, str]]):
        if self.path.exists():
            c = self._conn()
            have = dict(c.execute("SELECT eid, h FROM vecs WHERE model = ?", (self.model,)))
            every = [r[0] for r in c.execute("SELECT eid FROM vecs")]
            c.close()
        else:
            have, every = {}, []
        todo, live = [], set()
        norm = config.normalizing()
        for eid, etype, text in entries:
            if norm:
                text = normalize(text)  # the hash covers it: switching normalisation re-embeds
            h = text_hash(text)
            live.add(eid)
            if have.get(eid) != h:
                todo.append((eid, etype, h, text))
        return todo, [e for e in every if e not in live], len(live)

    def sync(self, entries, embed_fn: EmbedFn, batch: int = 16, progress: bool = False,
             max_embed: int | None = None) -> dict:
        built_with = self.file_model()
        if built_with and built_with != self.model:
            self.clear()  # never keep two models' vectors in one file; everything is embedded again below
        todo, stale, total = self.plan(entries)
        if max_embed is not None and len(todo) > max_embed:
            return {"embedded": 0, "removed": 0, "total": total, "pending": len(todo), "model": self.model,
                    "skipped": True}
        todo.sort(key=lambda t: len(t[3]))  # similar lengths per batch means less padding
        c = self._conn()
        before = c.execute("SELECT count(*) FROM vecs").fetchone()[0]
        step = batch * 8
        for i in range(0, len(todo), step):
            part = todo[i:i + step]
            vecs = embed_fn([config.doc_prefix() + t[3] for t in part])
            c.executemany("INSERT OR REPLACE INTO vecs VALUES (?,?,?,?,?)",
                          [(e, t, h, self.model, np.asarray(v, dtype=np.float16).tobytes())
                           for (e, t, h, _), v in zip(part, vecs)])
            c.commit()
            if progress:
                print(f"embedded {min(i + step, len(todo))}/{len(todo)}", file=sys.stderr)
        c.executemany("DELETE FROM vecs WHERE eid = ?", [(e,) for e in stale])
        c.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (self.model,))
        c.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)",
                  (_dt.datetime.now().isoformat(timespec="seconds"),))
        c.commit()
        if before and len(stale) > 0.2 * before:
            c.execute("VACUUM")
        c.close()
        return {"embedded": len(todo), "removed": len(stale), "total": total, "model": self.model}

    def matrix(self, etype: str | None = None):
        if not self.path.exists():
            return [], None
        st = self.path.stat()
        stamp = (st.st_mtime_ns, st.st_size, self.model)
        hit = _CACHE.get(str(self.path))
        if not hit or hit[0] != stamp:
            c = self._conn()
            rows = c.execute("SELECT eid, etype, vec FROM vecs WHERE model = ?", (self.model,)).fetchall()
            c.close()
            mat = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float16).reshape(len(rows), -1)
                   .astype(np.float32) if rows else None)
            hit = (stamp, [r[0] for r in rows], np.array([r[1] for r in rows]), mat)
            _CACHE[str(self.path)] = hit
        _, ids, types, mat = hit
        if mat is None or not etype:
            return ids, mat
        keep = np.flatnonzero(types == etype)
        return [ids[i] for i in keep], mat[keep]

    def count(self) -> int:
        if not self.path.exists():
            return 0
        c = self._conn()
        n = c.execute("SELECT count(*) FROM vecs WHERE model = ?", (self.model,)).fetchone()[0]
        c.close()
        return n

    def clear(self) -> None:
        c = self._conn()
        c.execute("DELETE FROM vecs")
        c.commit()
        c.execute("VACUUM")
        c.close()

    def file_model(self) -> str | None:
        """The model this file was built with: its meta record, or (files from before the record) its vectors."""
        if not self.path.exists():
            return None
        c = self._conn()
        r = c.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
        if not r:
            r = c.execute("SELECT model FROM vecs LIMIT 1").fetchone()
        c.close()
        return r[0] if r else None

    def meta(self, key: str) -> str | None:
        if not self.path.exists():
            return None
        c = self._conn()
        r = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        c.close()
        return r[0] if r else None
