"""quorra KB adapter for vecstore (agent-stack/lib/vecstore): the texts it embeds, its keyword ranker and its filters.
Embedding, fusion and the cross-encoder rerank are the shared library's (the same daemon as clu and flynn)."""
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

INSTRUCT = ("Instruct: Given a developer's question about your company code, retrieve knowledge base entries "
            "(coding conventions, business concepts and where they are implemented, lessons, past runs) that help answer it\nQuery: ")


def vec_path() -> Path:
    return Path(os.environ.get("QUORRA_KB_VECTORS", str(store.db_path()).replace(".sqlite", ".vectors.sqlite")))


def doc_text(title: str, body: str) -> str:
    return f"{title}\n{body}".strip()[:2000]


def generated_ids(conn) -> set[str]:
    """Rows bulk-generated from schema caches stay keyword-searchable but are not embedded: in the recall eval they
    crowded curated rows out of the semantic ranking (docs/decisions.md, 2026-09-29)."""
    out: set[str] = set()
    for spec in store.TYPES.values():
        out |= {r[0] for r in conn.execute(
            f"SELECT id FROM {spec['table']} WHERE origin LIKE ?", (store.NO_EXPORT_ORIGINS[0] + "%",))}
    return out


class QuorraAdapter(BaseAdapter):
    name = "quorra"
    instruct = INSTRUCT
    eval_dir = KB / "eval"

    def __init__(self, conn, vec: Path | None = None):
        self.conn = conn
        self.vec_path = vec or vec_path()

    def entries(self):
        skip = generated_ids(self.conn)
        for r in self.conn.execute("SELECT etype, eid, title, body FROM fts"):
            if r["eid"] not in skip:
                yield r["eid"], r["etype"], doc_text(r["title"] or "", r["body"] or "")

    def lexical(self, query, etype, limit):
        return store._lexical(self.conn, query, etype, limit)

    def prepare_query(self, query):
        return store.expand_query(self.conn, query)

    def keep(self, eid):
        if not eid.startswith("conv_"):
            return True
        sup = self.conn.execute("SELECT superseded_by FROM conventions WHERE id = ?", (eid,)).fetchone()
        return not (sup and sup[0])

    def hydrate(self, eid):
        r = self.conn.execute("SELECT etype, title, substr(body, 1, 120) AS snip FROM fts WHERE eid = ?",
                              (eid,)).fetchone()
        return {"type": r["etype"], "id": eid, "title": r["title"], "snippet": r["snip"]} if r else None

    def rerank_text(self, eid):
        r = self.conn.execute("SELECT title, body FROM fts WHERE eid = ?", (eid,)).fetchone()
        return doc_text(r["title"] or "", r["body"] or "") if r else None


def make(conn=None) -> QuorraAdapter:
    return QuorraAdapter(conn or store.connect())


def build_eval(workdir: Path, norm: str) -> QuorraAdapter:
    """A private copy of the KB rebuilt from the committed export under the current VECSTORE_NORMALIZE, for
    `vecstore eval quorra --matrix`."""
    conn = store.rebuild(str(Path(workdir) / f"quorra-n{norm}.sqlite"), str(store.REPO / "kb" / "export"))
    return QuorraAdapter(conn, Path(workdir) / f"quorra-n{norm}.vectors.sqlite")
