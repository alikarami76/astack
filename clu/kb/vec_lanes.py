"""Two more vector lanes for the clu KB, each in its own vector file, so they never touch the curated ranking
(docs/decisions.md, 2026-10-07):

  schema  one document per table, for every source row (generated or curated): kb.schema.vectors.sqlite
  runs    heading chunks of each run's question, contract.md, answer.md and methods.md: kb.runs.vectors.sqlite

The curated lane (vec_adapter.CluAdapter, kb.vectors.sqlite) is unchanged. Both new lanes keep a keyword index of
their own documents in their vector file, so they still answer by keywords when the daemon is down.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

import vec_adapter  # noqa: F401  (puts store and vecstore on sys.path)
import store
from vecstore.chunk import chunk_file
from vecstore.hybrid import BaseAdapter
from vecstore.mdstore import MdStore

LANES = ("curated", "schema", "runs", "om")
DOC_CHARS = 1400  # about MAX_TOKENS (384) tokens of table and column names; the model truncates beyond that
COL_CHARS = 300  # one column's text at most, so a long meaning cannot crowd out the rest
FACT_CHARS = 350  # the facts line when the columns need the room
CODE_LINES = 8  # lines kept from each fenced block in run files (SQL is in queries/ and methods.md anyway)

SCHEMA_INSTRUCT = ("Instruct: Given a data analyst's question about business data, retrieve the database tables "
                   "that hold the data needed to answer it\nQuery: ")
RUNS_INSTRUCT = ("Instruct: Given a data analyst's question about business data, retrieve passages from past "
                 "analyses (questions, measurement contracts, answers, methods) that help answer it\nQuery: ")


def _lane_file(env: str, suffix: str) -> Path:
    return Path(os.environ.get(env) or str(store.db_path()).replace(".sqlite", suffix))


def schema_vec_path() -> Path:
    return _lane_file("CLU_KB_SCHEMA_VECTORS", ".schema.vectors.sqlite")


def om_vec_path() -> Path:
    return _lane_file("CLU_KB_OM_VECTORS", ".om.vectors.sqlite")


def runs_vec_path() -> Path:
    return _lane_file("CLU_KB_RUNS_VECTORS", ".runs.vectors.sqlite")


def runs_dir() -> Path:
    return Path(os.environ.get("CLU_RUNS_DIR") or store.REPO / "runs")


def _c(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:max(0, n - 1)].rstrip() + "…"


def table_name(src: dict) -> str:
    return ".".join(x for x in (_c(src.get("database")), _c(src.get("schema_name")), _c(src.get("table_name"))) if x)


def _column_text(col: dict) -> str:
    t = _c(col.get("name"))
    if _c(col.get("type")):
        t += f" ({_c(col.get('type'))})"
    meaning, pit = _c(col.get("meaning")), _c(col.get("pitfalls"))
    about = ". ".join(x for x in (meaning, f"Pitfall: {pit}" if pit else "") if x)
    return _cut(f"{t} — {about}" if about else t, COL_CHARS)


def schema_doc_text(src: dict, columns=(), facts=(), max_chars: int = DOC_CHARS) -> str:
    """One table as one document:
    {kind} {engine} {database}.{schema_name}.{table_name}
    Grain: {grain}
    Notes: {notes}
    Columns: {name} ({type}) — {meaning}; {name} ({type}); ...
    Empty fields and lines are left out. Columns with a meaning or pitfall come first; columns are cut to fit
    max_chars, and curated facts about the table are added in a last "Facts:" line when there is room."""
    lines = [" ".join(x for x in (_c(src.get("kind")), _c(src.get("engine")), table_name(src)) if x)]
    for label, key in (("Grain", "grain"), ("Notes", "notes")):
        if _c(src.get(key)):
            lines.append(f"{label}: {_c(src.get(key))}")
    base = _cut("\n".join(lines), max_chars)
    cols = sorted(columns, key=lambda c: not (_c(c.get("meaning")) or _c(c.get("pitfalls"))))  # stable
    col_texts = [_column_text(c) for c in cols if _c(c.get("name"))]
    fact_line = "Facts: " + "; ".join(_c(f) for f in facts if _c(f)) if any(_c(f) for f in facts) else ""
    full_cols = "Columns: " + "; ".join(col_texts) if col_texts else ""
    if len("\n".join(x for x in (base, full_cols, fact_line) if x)) <= max_chars:
        return "\n".join(x for x in (base, full_cols, fact_line) if x)
    if fact_line:
        fact_line = _cut(fact_line, min(FACT_CHARS, max(0, max_chars - len(base) - 1)))
        if len(fact_line) < len("Facts: ") + 20:
            fact_line = ""
    room = max_chars - len(base) - 1 - (len(fact_line) + 1 if fact_line else 0)
    parts, used = [], len("Columns: ")
    for i, t in enumerate(col_texts):
        if used + len(t) + 2 > room:
            more = f"… +{len(col_texts) - i} more"
            if used + len(more) <= room:
                parts.append(more)
            break
        parts.append(t)
        used += len(t) + 2
    col_line = "Columns: " + "; ".join(parts) if parts else ""
    return "\n".join(x for x in (base, col_line, fact_line) if x)[:max_chars]


_DOCS = "CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(eid UNINDEXED, etype UNINDEXED, body, " \
        "tokenize = 'unicode61 remove_diacritics 2')"


class SchemaAdapter(BaseAdapter):
    """Every table as one document. Keyword search runs over the same documents (columns included) once the lane
    has been built; before that it falls back to the KB's own keyword search over table rows."""
    name = "clu-schema"
    instruct = SCHEMA_INSTRUCT
    eval_dir = None

    def __init__(self, conn):
        self.conn = conn
        self.vec_path = schema_vec_path()
        self._docs: list[tuple[str, str, str]] | None = None

    def entries(self):
        if self._docs is None:
            c = self.conn
            cols: dict[str, list[dict]] = {}
            for r in c.execute("SELECT source_id, name, type, meaning, pitfalls FROM columns ORDER BY rowid"):
                cols.setdefault(r["source_id"], []).append(dict(r))
            facts: dict[str, list[str]] = {}
            for r in c.execute("SELECT source_id, statement FROM facts WHERE coalesce(source_id, '') != '' "
                               "AND coalesce(superseded_by, '') = '' ORDER BY id"):
                facts.setdefault(r["source_id"], []).append(r["statement"])
            self._docs = [(r["id"], r["kind"] or "", schema_doc_text(dict(r), cols.get(r["id"], ()),
                                                                     facts.get(r["id"], ())))
                          for r in c.execute("SELECT * FROM sources ORDER BY id")]
        return self._docs

    def _db(self) -> sqlite3.Connection:
        self.vec_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.vec_path)
        c.row_factory = sqlite3.Row
        c.execute(_DOCS)
        return c

    def rerank_text(self, eid):
        return next((t for e, _, t in self.entries() if e == eid), None)

    def refresh_index(self) -> int:
        """Rewrite the keyword index of the documents (a few seconds; run with every embed of this lane)."""
        docs = self.entries()
        c = self._db()
        c.execute("DELETE FROM docs")
        c.executemany("INSERT INTO docs VALUES (?,?,?)", [(e, k, store.nz(t)) for e, k, t in docs])
        c.commit()
        c.close()
        return len(docs)

    def _has_docs(self) -> bool:
        if not self.vec_path.exists():
            return False
        c = sqlite3.connect(self.vec_path)
        try:
            return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'docs'").fetchone()
                        and c.execute("SELECT 1 FROM docs LIMIT 1").fetchone())
        except sqlite3.Error:
            return False
        finally:
            c.close()

    def lexical(self, query, etype, limit):
        if not self._has_docs():
            return [self.hydrate(r["id"]) for r in store._lexical(self.conn, query, "source", limit)]
        fq = store._fts_query(store.expand_query(self.conn, query))
        if not fq:
            return []
        c = self._db()
        rows = c.execute("SELECT eid FROM docs WHERE docs MATCH ?" + (" AND etype = ?" if etype else "")
                         + " ORDER BY bm25(docs) LIMIT ?", [fq, *([etype] if etype else []), limit]).fetchall()
        c.close()
        return [h for h in (self.hydrate(r["eid"]) for r in rows) if h]

    def prepare_query(self, query):
        return store.expand_query(self.conn, query)

    def hydrate(self, eid):
        r = self.conn.execute("SELECT * FROM sources WHERE id = ?", (eid,)).fetchone()
        if not r:
            return None
        name, eng = table_name(dict(r)), _c(r["engine"])
        return {"type": r["kind"], "id": eid, "title": f"{eng}/{name}" if eng and eng != _c(r["database"]) else name,
                "snippet": _c(r["grain"] or r["notes"])[:120]}


def why_table(conn, eid: str, query: str) -> str:
    """One line on why a table may hold the answer: the columns whose names match the query, else its grain or
    notes, else its first columns."""
    terms = [t for t in store._terms(store.expand_query(conn, query)) if len(t) >= 3]
    names = [r[0] for r in conn.execute("SELECT name FROM columns WHERE source_id = ? ORDER BY rowid", (eid,)) if r[0]]
    hits = [n for n in names if any(t in n.lower() for t in terms)]
    if hits:
        return "columns " + ", ".join(hits[:6])
    r = conn.execute("SELECT grain, notes FROM sources WHERE id = ?", (eid,)).fetchone()
    about = _c((r["grain"] or r["notes"]) if r else "")
    if about:
        return _cut(about, 100)
    return ("columns " + ", ".join(names[:6])) if names else ""


def cap_code(text: str, keep: int = CODE_LINES) -> str:
    """Shorten every fenced block to its first `keep` lines, so long SQL does not fill the run chunks."""
    out, block, fence = [], [], None
    for line in text.splitlines():
        m = re.match(r"^\s*(```|~~~)", line)
        if fence is None:
            out.append(line)
            if m:
                fence, block = m.group(1), []
            continue
        if m and m.group(1) == fence:
            out += block[:keep]
            if len(block) > keep:
                out.append(f"… ({len(block) - keep} more lines)")
            out.append(line)
            fence = None
        else:
            block.append(line)
    if fence is not None:  # an unclosed fence
        out += block[:keep]
    return "\n".join(out)


class RunsStore(MdStore):
    """Heading chunks of each run's question (run.json), contract.md, answer.md and methods.md. A hit's path is
    <run_id>/<file>, relative to clu/runs."""
    FILES = ("run.json", "contract.md", "answer.md", "methods.md")

    def __init__(self, root: Path | None = None):
        super().__init__("clu-runs", root or runs_dir(), runs_vec_path(), RUNS_INSTRUCT,
                         include=tuple(f"*/{f}" for f in self.FILES))
        tpl = store.REPO / "skills/clu-core/references/contract-template.md"
        self._template = tpl.read_text().strip() if tpl.exists() else None

    def chunk(self, rel: str):
        p = self.root / rel
        run_id = rel.split("/", 1)[0]
        if p.name == "run.json":
            try:
                q = _c(json.loads(p.read_text()).get("question"))
            except (ValueError, AttributeError, OSError):
                q = ""
            return [(f"{rel}#question", "question", f"{run_id} › question\n{q}")] if q else []
        text = p.read_text(errors="replace")
        if self._template is not None and text.strip() == self._template:
            return []  # an unfilled contract template says nothing about the run
        return chunk_file(p, self.root, cap_code(text))


def run_id_of(hit: dict) -> str:
    return (hit.get("path") or hit.get("id") or "").split("/", 1)[0]


OM_INSTRUCT = ("Instruct: Given a data analyst's question about business data, retrieve the certified metric definitions "
               "and business glossary terms from the company data catalog that define it\nQuery: ")


class OmAdapter(vec_adapter.CluAdapter):
    """OpenMetadata metrics and glossary terms (kb/om_import.py), in their own vector file so their synonym-rich text
    never crowds the curated ranking. Keyword search answers when the file has no vectors yet."""
    name = "clu-om"
    instruct = OM_INSTRUCT
    eval_dir = None

    def __init__(self, conn):
        super().__init__(conn)
        self.vec_path = om_vec_path()

    def entries(self):
        keep = store.om_ids(self.conn, ("metric", "glossary"))
        for r in self.conn.execute("SELECT etype, eid, title, body FROM fts WHERE etype IN ('metric', 'glossary')"):
            if r["eid"] in keep:
                yield r["eid"], r["etype"], vec_adapter.doc_text(r["title"] or "", r["body"] or "")

    def lexical(self, query, etype, limit):
        return store._lexical(self.conn, query, etype, limit, om="only")

    def prepare_query(self, query):
        return store.expand_query(self.conn, query, om=True)


def make(conn, lane: str):
    if lane == "curated":
        return vec_adapter.CluAdapter(conn)
    if lane == "om":
        return OmAdapter(conn)
    if lane == "schema":
        return SchemaAdapter(conn)
    if lane == "runs":
        return RunsStore()
    raise ValueError(f"unknown lane {lane!r}; expected one of {LANES}")
