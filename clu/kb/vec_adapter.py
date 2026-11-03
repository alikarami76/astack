"""clu KB adapter for vecstore (agent-stack/lib/vecstore). It reproduces exactly the texts the pre-library vectors.py
embedded, so every existing vector in kb.vectors.sqlite stays valid."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KB = Path(__file__).resolve().parent
for p in (str(KB), str(KB.parents[1] / "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)
import store  # noqa: E402
from vecstore.hybrid import BaseAdapter  # noqa: E402

INSTRUCT = ("Instruct: Given a data analyst's question about business data, retrieve knowledge base entries "
            "(metric definitions, table notes, lessons, recipes, benchmarks, past runs) that help answer it\nQuery: ")


def vec_path() -> Path:
    return Path(os.environ.get("CLU_KB_VECTORS", str(store.db_path()).replace(".sqlite", ".vectors.sqlite")))


def doc_text(title: str, body: str) -> str:
    return f"{title}\n{body}".strip()[:2000]


def header_on() -> bool:
    """CLU_KB_HEADER=1 puts the one-line context header on the vector text. Off by default: on the 2026-10-20 eval
    it did not help (recall@5 -0.03, MRR -0.005 on the curated set), see kb/eval/upgrade-2026-10-20.md."""
    return os.environ.get("CLU_KB_HEADER", "0") == "1"


def context_header(conn, etype: str, eid: str, title: str) -> str:
    """"<type> · <topic or tags> · <name>": one line that tells the embedding model what kind of row this is and
    what it is about, which the row's own text often leaves implicit. Topic is the fact's topic, the lesson's
    applies_to or the run's topics; tags are the row's tags."""
    spec = store.TYPES.get(etype)
    row = conn.execute(f"SELECT * FROM {spec['table']} WHERE id = ?", (eid,)).fetchone() if spec else None
    topic = ""
    tags: list[str] = []
    if row is not None:
        r = dict(row)
        topic = str(r.get("topic") or r.get("applies_to") or r.get("topics") or "").strip()
        try:
            tags = [t for t in json.loads(r.get("tags") or "[]") if t and t != "om"]
        except ValueError:
            tags = []
    about = ", ".join(x for x in dict.fromkeys([topic, *tags[:5]]) if x)
    return " · ".join(x for x in (etype, about, " ".join((title or "").split())[:120]) if x)


def vector_text(conn, etype: str, eid: str, title: str, body: str) -> str:
    text = doc_text(title or "", body or "")
    return f"{context_header(conn, etype, eid, title)}\n{text}" if header_on() else text


def generated_ids(conn) -> set[str]:
    """Rows bulk-generated from schema caches. They stay keyword-searchable but are not embedded: in the recall eval
    they crowded curated rows out of the semantic ranking (docs/decisions.md, 2026-09-29)."""
    out: set[str] = set()
    for spec in store.TYPES.values():
        out |= {r[0] for r in conn.execute(
            f"SELECT id FROM {spec['table']} WHERE origin LIKE ?", (store.NO_EXPORT_ORIGINS[0] + "%",))}
    # OpenMetadata rows: metrics and glossary terms have the om lane, tables and columns the schema lane.
    out |= store.om_ids(conn)
    return out


class CluAdapter(BaseAdapter):
    name = "clu"
    instruct = INSTRUCT
    eval_dir = KB / "eval"

    def __init__(self, conn):
        self.conn = conn
        self.vec_path = vec_path()

    def entries(self):
        skip = generated_ids(self.conn)
        for r in self.conn.execute("SELECT etype, eid, title, body FROM fts"):
            if r["eid"] not in skip:
                yield r["eid"], r["etype"], vector_text(self.conn, r["etype"], r["eid"], r["title"], r["body"])

    def rerank_text(self, eid):
        r = self.conn.execute("SELECT etype, title, body FROM fts WHERE eid = ?", (eid,)).fetchone()
        return vector_text(self.conn, r["etype"], eid, r["title"], r["body"]) if r else None

    def lexical(self, query, etype, limit):
        return store._lexical(self.conn, query, etype, limit)

    def prepare_query(self, query):
        return store.expand_query(self.conn, query)

    def keep(self, eid):
        if not eid.startswith("fact_"):
            return True
        sup = self.conn.execute("SELECT superseded_by FROM facts WHERE id = ?", (eid,)).fetchone()
        return not (sup and sup[0])

    def hydrate(self, eid):
        r = self.conn.execute("SELECT etype, title, substr(body, 1, 120) AS snip FROM fts WHERE eid = ?",
                              (eid,)).fetchone()
        return {"type": r["etype"], "id": eid, "title": r["title"], "snippet": r["snip"]} if r else None


def make(conn=None) -> CluAdapter:
    return CluAdapter(conn or store.connect())
