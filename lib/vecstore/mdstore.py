"""A ready-made adapter for a folder of markdown (or other text) files that has no search command of its own:
an FTS5 keyword index over heading sections plus the vector ranking, both kept in the store's vector file."""
from __future__ import annotations

import fnmatch
import re
import sqlite3
import sys
from pathlib import Path

from . import client, config
from .normalize import fts_text, normalize
from .chunk import chunk_file
from .hybrid import BaseAdapter, hybrid, report_unavailable, resolve_mode
from .vecfile import VecFile

_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(eid UNINDEXED, etype UNINDEXED, path UNINDEXED, heading,"
    " body, generated UNINDEXED)",
    "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime_ns INTEGER)",
)


def _fts_query(q: str) -> str:
    if config.normalizing():
        q = fts_text(q)
    terms = [t for t in dict.fromkeys(re.findall(r"\w+", q.lower(), flags=re.UNICODE)) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in terms)


class MdStore(BaseAdapter):
    def __init__(self, name, root, vec_path, instruct, include=("**/*.md",), exclude=(), generated=None,
                 eval_dir=None, max_inline=20):
        self.name, self.root, self.vec_path, self.instruct = name, Path(root), Path(vec_path), instruct
        self.include, self.exclude, self.generated = tuple(include), tuple(exclude), generated
        self.eval_dir = Path(eval_dir) if eval_dir else None
        self.max_inline = max_inline

    def _db(self) -> sqlite3.Connection:
        self.vec_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.vec_path)
        c.row_factory = sqlite3.Row
        for s in _SCHEMA:
            c.execute(s)
        return c

    def files(self) -> dict[str, Path]:
        out = {}
        for pat in self.include:
            for p in self.root.glob(pat):
                rel = p.relative_to(self.root).as_posix()
                if p.is_file() and not any(fnmatch.fnmatch(rel, x) for x in self.exclude):
                    out[rel] = p
        return dict(sorted(out.items()))

    def refresh_index(self) -> int:
        c = self._db()
        known = {r["path"]: r["mtime_ns"] for r in c.execute("SELECT path, mtime_ns FROM files")}
        now = {rel: p.stat().st_mtime_ns for rel, p in self.files().items()}
        changed = [r for r, m in now.items() if known.get(r) != m]
        gone = [r for r in known if r not in now]
        for rel in changed + gone:
            c.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        for rel in changed:
            etype = rel.split("/", 1)[0] if "/" in rel else "root"
            for eid, heading, text in self.chunk(rel):
                gen = bool(self.generated and self.generated(rel, text))
                c.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                          (eid, etype, rel, heading, normalize(text) if config.normalizing() else text, int(gen)))
            c.execute("INSERT OR REPLACE INTO files VALUES (?,?)", (rel, now[rel]))
        c.executemany("DELETE FROM files WHERE path = ?", [(r,) for r in gone])
        c.commit()
        c.close()
        return len(changed) + len(gone)

    def chunk(self, rel: str) -> list[tuple[str, str, str]]:
        """(eid, heading, text) for one file. Subclasses override it to clean or reshape a file before chunking."""
        return chunk_file(self.root / rel, self.root)

    def entries(self):
        c = self._db()
        rows = c.execute("SELECT eid, etype, body FROM chunks WHERE generated = 0").fetchall()
        c.close()
        return [(r["eid"], r["etype"], r["body"]) for r in rows]

    def lexical(self, query, etype, limit):
        fq = _fts_query(query)
        if not fq:
            return []
        sql = ("SELECT eid, etype, path, heading, snippet(chunks, 4, '[', ']', '…', 12) AS snip FROM chunks"
               " WHERE chunks MATCH ?" + (" AND etype = ?" if etype else "") + " ORDER BY bm25(chunks) LIMIT ?")
        c = self._db()
        rows = c.execute(sql, [fq, *([etype] if etype else []), limit]).fetchall()
        c.close()
        return [{"id": r["eid"], "type": r["etype"], "path": r["path"], "heading": r["heading"], "snippet": r["snip"]}
                for r in rows]

    def hydrate(self, eid):
        c = self._db()
        r = c.execute("SELECT etype, path, heading, substr(body, 1, 160) AS snip FROM chunks WHERE eid = ?",
                      (eid,)).fetchone()
        c.close()
        return {"id": eid, "type": r["etype"], "path": r["path"], "heading": r["heading"], "snippet": r["snip"]} \
            if r else None

    def rerank_text(self, eid):
        c = self._db()
        r = c.execute("SELECT heading, body FROM chunks WHERE eid = ?", (eid,)).fetchone()
        c.close()
        return f"{r['heading']}\n{r['body']}" if r else None

    def sync_vectors(self, embed_fn=None, max_embed=None, progress=False) -> dict:
        return VecFile(self.vec_path).sync(self.entries(), embed_fn or client.embed, max_embed=max_embed,
                                           progress=progress)

    def search(self, query, k=10, etype=None, mode="auto", embed_fn=None):
        self.refresh_index()
        if resolve_mode(self, mode) != "hybrid":
            return self.lexical(query, etype, k)
        try:
            r = self.sync_vectors(embed_fn, max_embed=self.max_inline)
        except Exception as e:  # noqa: BLE001 - report once, answer with keywords
            report_unavailable(e)
            return self.lexical(query, etype, k)
        if r.get("skipped"):
            print(f"[vecstore] {r['pending']} chunks stale; run: vecstore index {self.name}", file=sys.stderr)
        return hybrid(self, query, etype, k, embed_fn)

    def rebuild(self) -> None:
        c = self._db()
        c.execute("DELETE FROM chunks")
        c.execute("DELETE FROM files")
        c.commit()
        c.close()
        VecFile(self.vec_path).clear()
