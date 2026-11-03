"""flynn KB adapter for vecstore (agent-stack/lib/vecstore): local embeddings, hybrid search (keyword + vectors fused with
RRF) and the cross-encoder rerank, all through the shared daemon. Optional: without lib/vecstore (the flynn desk
deploy) or without the daemon, kb.py search answers with keywords. SQLite only; the Postgres desk stays lexical."""
from __future__ import annotations

import os
import sys
from pathlib import Path

KB = Path(__file__).resolve().parent
for p in (str(KB), str(KB.parents[1] / "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)
import store  # noqa: E402
from vecstore.hybrid import BaseAdapter  # noqa: E402

INSTRUCT = ("Instruct: Given a designer's request, retrieve design knowledge entries (components, tokens, patterns, "
            "references, lessons, taste notes, past designs) that help with it\nQuery: ")


def vec_path() -> Path:
    return Path(os.environ.get("FLYNN_KB_VECTORS", str(store.db_path()).replace(".sqlite", ".vectors.sqlite")))


def doc_text(title: str, body: str) -> str:
    return f"{title}\n{body}".strip()[:2000]


class FlynnAdapter(BaseAdapter):
    name = "flynn"
    instruct = INSTRUCT
    eval_dir = KB / "eval"

    def __init__(self, conn, vec: Path | None = None):
        self.conn = conn
        self.vec_path = vec or vec_path()

    def entries(self):
        for r in self.conn.execute("SELECT etype, eid, title, body FROM fts"):
            yield r["eid"], r["etype"], doc_text(r["title"] or "", r["body"] or "")

    def lexical(self, query, etype, limit):
        return store.search(self.conn, query, etype, limit)

    def hydrate(self, eid):
        r = self.conn.execute("SELECT etype, title, substr(body, 1, 120) AS snip FROM fts WHERE eid = ?",
                              (eid,)).fetchone()
        return {"type": r["etype"], "id": eid, "title": r["title"], "snippet": r["snip"]} if r else None

    def rerank_text(self, eid):
        r = self.conn.execute("SELECT title, body FROM fts WHERE eid = ?", (eid,)).fetchone()
        return doc_text(r["title"] or "", r["body"] or "") if r else None


def make(conn=None) -> FlynnAdapter:
    return FlynnAdapter(conn or store.connect(str(store.db_path())))


def build_eval(workdir: Path, norm: str) -> FlynnAdapter:
    """A private copy of the KB rebuilt from the committed export under the current VECSTORE_NORMALIZE, for
    `vecstore eval flynn --matrix`."""
    db = Path(workdir) / f"flynn-n{norm}.sqlite"
    conn = store.rebuild(str(db), str(store.REPO / "kb" / "export"))
    return FlynnAdapter(conn, Path(workdir) / f"flynn-n{norm}.vectors.sqlite")
