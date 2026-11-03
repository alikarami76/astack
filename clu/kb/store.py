"""clu knowledge base core. Stdlib only."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
from vecstore import config as _vcfg  # noqa: E402  (pure Python: no numpy, no model)
from vecstore.normalize import fts_text as _fts_text, normalize as _normalize  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCHEMA = Path(__file__).with_name("schema.sql")
NO_EXPORT_ORIGINS = ("schema-cache",)
OM_ORIGIN = "om:"  # rows imported from OpenMetadata (kb/om_import.py)
OM_TYPES = ("source", "column", "metric", "glossary")

TYPES: dict[str, dict] = {
    "source": dict(table="sources", prefix="src", key=["kind", "database", "schema_name", "table_name"],
                   cols=["kind", "engine", "database", "schema_name", "table_name", "grain", "row_estimate", "freshness",
                         "retention_days", "speed_class", "access", "last_checked", "notes", "aliases", "questions"],
                   text=["database", "schema_name", "table_name", "grain", "notes", "aliases", "questions"]),
    "column": dict(table="columns", prefix="col", key=["source_id", "name"],
                   cols=["source_id", "name", "type", "meaning", "unit", "pitfalls"],
                   text=["name", "meaning", "pitfalls"]),
    "code_ref": dict(table="code_refs", prefix="code", key=["repo", "path", "line", "source_id", "column_name"],
                     cols=["source_id", "column_name", "repo", "path", "line", "role", "what_it_does", "commit_or_date"],
                     text=["repo", "path", "column_name", "what_it_does"]),
    "metric": dict(table="metrics", prefix="met", key=["name"],
                   cols=["name", "aliases", "definition", "numerator", "denominator", "grain", "filters", "unit",
                         "formula_sql", "variants", "pitfalls", "agreed_in_run", "confidence", "questions"],
                   text=["name", "aliases", "definition", "numerator", "denominator", "filters", "variants", "pitfalls",
                         "questions"]),
    "fact": dict(table="facts", prefix="fact", key=["statement"],
                 cols=["statement", "topic", "source_id", "confidence", "evidence", "run_id", "last_verified",
                       "superseded_by", "aliases", "questions"],
                 text=["statement", "topic", "evidence", "aliases", "questions"]),
    "recipe": dict(table="recipes", prefix="rec", key=["name"],
                   cols=["name", "purpose", "source_id", "sql", "params", "runtime_s", "verified", "run_id"],
                   text=["name", "purpose", "sql"]),
    "benchmark": dict(table="benchmarks", prefix="bm", key=["name", "period", "as_of"],
                      cols=["name", "value", "unit", "period", "as_of", "source"],
                      text=["name", "value", "unit", "period", "source"]),
    "glossary": dict(table="glossary", prefix="gl", key=["term_fa", "term_en"],
                     cols=["term_fa", "term_en", "meaning", "aliases", "questions"],
                     text=["term_fa", "term_en", "meaning", "aliases", "questions"]),
    "project": dict(table="projects", prefix="proj", key=["name"],
                    cols=["name", "path", "status", "summary"], text=["name", "path", "summary"]),
    "topic": dict(table="topics", prefix="top", key=["name"], cols=["name", "summary"], text=["name", "summary"]),
    "lesson": dict(table="lessons", prefix="les", key=["rule"],
                   cols=["rule", "why", "trigger_quote", "run_id", "applies_to", "aliases", "questions"],
                   text=["rule", "why", "trigger_quote", "applies_to", "aliases", "questions"]),
    "run": dict(table="runs_index", prefix="run", key=["run_id"],
                cols=["run_id", "date", "type", "question", "topics", "outcome", "corrections_count", "verify_result"],
                text=["run_id", "type", "question", "topics", "outcome"]),
    "experiment": dict(table="experiments", prefix="exp", key=["name"],
                       cols=["name", "kind", "status", "growthbook_id", "unit", "population", "window", "design",
                             "estimand", "primary_metric", "guardrails", "result", "ci", "decision", "diagnostics",
                             "lessons", "run_id", "confidence"],
                       text=["name", "kind", "design", "estimand", "primary_metric", "result", "decision", "lessons"]),
}
PREFIX_TO_TYPE = {v["prefix"]: k for k, v in TYPES.items()}
# Rows whose search text can carry curated enrichment (kb/enrich.py): other names a person would use, and the
# questions the row answers. Both are indexed in the keyword index and in the vector text.
ENRICHED = ("metric", "fact", "glossary", "lesson", "source")


def nz(text) -> str:
    """Farsi/Arabic normalisation of indexed text and queries (lib/vecstore/normalize.py); VECSTORE_NORMALIZE=0 off."""
    return _normalize(text) if _vcfg.normalizing() else ("" if text is None else str(text))


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def make_id(etype: str, row: dict) -> str:
    spec = TYPES[etype]
    natural = "|".join(_norm(row.get(k)) for k in spec["key"])
    return f"{spec['prefix']}_{hashlib.sha1(natural.encode()).hexdigest()[:12]}"


def _has_empty_natural_key(etype: str, row: dict) -> bool:
    spec = TYPES[etype]
    return all(not _norm(row.get(k)) for k in spec["key"])


def db_path() -> Path:
    return Path(os.environ.get("CLU_KB_DB", REPO / "kb" / "kb.sqlite"))


def export_dir() -> Path:
    return Path(os.environ.get("CLU_KB_EXPORT", REPO / "kb" / "export"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    _migrate(conn)
    return conn


def _migrate(conn) -> None:
    """Add the enrichment columns to a database created before they existed."""
    for etype in ENRICHED:
        table = TYPES[etype]["table"]
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in ("aliases", "questions"):
            if col in TYPES[etype]["cols"] and col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")


def _title(etype: str, row: dict) -> str:
    spec = TYPES[etype]
    if etype == "source":
        return ".".join(x for x in (row.get("database"), row.get("schema_name"), row.get("table_name")) if x)
    return str(row.get(spec["key"][0]) or "")


def _fts_put(conn, etype: str, eid: str, row: dict) -> None:
    conn.execute("DELETE FROM fts WHERE eid = ?", (eid,))
    body = " ".join(str(row.get(c) or "") for c in TYPES[etype]["text"])
    body += " " + " ".join(json.loads(row.get("tags") or "[]"))
    conn.execute("INSERT INTO fts(etype, eid, title, body) VALUES (?,?,?,?)",
                 (etype, eid, nz(_title(etype, row)), nz(body)))


def _merge(conn, etype: str, row: dict, origin: str, tags: list[str] | None) -> tuple[str, dict]:
    spec = TYPES[etype]
    eid = make_id(etype, row)
    cur = conn.execute(f"SELECT * FROM {spec['table']} WHERE id = ?", (eid,)).fetchone()
    merged = dict(cur) if cur else {"id": eid, "created_at": _now(), "tags": "[]", "origin": origin}
    for c in spec["cols"]:
        v = row.get(c)
        if v is not None and v != "":
            merged[c] = v if isinstance(v, (int, float)) else (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v).strip())
    merged["tags"] = json.dumps(sorted(set(json.loads(merged.get("tags") or "[]")) | set(tags or [])), ensure_ascii=False)
    cur_origin = str(merged.get("origin") or "")
    if origin and not str(origin).startswith(NO_EXPORT_ORIGINS) and (not cur_origin or cur_origin.startswith(NO_EXPORT_ORIGINS)):
        merged["origin"] = origin
    elif origin and not merged.get("origin"):
        merged["origin"] = origin
    merged["updated_at"] = _now()
    return eid, merged


def _write(conn, etype: str, merged: dict) -> None:
    spec = TYPES[etype]
    cols = ["id", *spec["cols"], "tags", "origin", "created_at", "updated_at"]
    quoted_cols = [f'"{c}"' for c in cols]
    conn.execute(
        f"INSERT OR REPLACE INTO {spec['table']} ({','.join(quoted_cols)}) VALUES ({','.join('?' * len(cols))})",
        [merged.get(c) for c in cols],
    )


def upsert(conn, etype: str, row: dict, origin: str = "", tags: list[str] | None = None) -> str:
    if etype not in TYPES:
        raise ValueError(f"unknown type {etype}; expected one of {sorted(TYPES)}")
    if _has_empty_natural_key(etype, row):
        raise ValueError(f"empty natural key for {etype}")
    eid, merged = _merge(conn, etype, row, origin, tags)
    _write(conn, etype, merged)
    _fts_put(conn, etype, eid, merged)
    conn.commit()
    return eid


def bulk_upsert(conn, etype: str, rows: list[dict], origin: str, fts: bool = True) -> int:
    n = 0
    for row in rows:
        if _has_empty_natural_key(etype, row):
            continue
        eid, merged = _merge(conn, etype, row, origin, row.pop("_tags", None) if isinstance(row, dict) else None)
        _write(conn, etype, merged)
        if fts:
            _fts_put(conn, etype, eid, merged)
        n += 1
    conn.commit()
    return n


def reindex(conn) -> int:
    """Rewrite the keyword index of every row from the tables (after a change of normalisation or of the indexed
    text). Rows and exports stay as they are."""
    n = 0
    for etype, spec in TYPES.items():
        for r in conn.execute(f"SELECT * FROM {spec['table']}").fetchall():
            _fts_put(conn, etype, r["id"], dict(r))
            n += 1
    conn.commit()
    return n


def get(conn, eid: str) -> dict | None:
    etype = PREFIX_TO_TYPE.get(eid.split("_", 1)[0])
    if not etype:
        return None
    r = conn.execute(f"SELECT * FROM {TYPES[etype]['table']} WHERE id = ?", (eid,)).fetchone()
    return dict(r) | {"type": etype} if r else None


STOPWORDS = set("""a an the of to in on for by with and or is are was were be been do does did how what which who why
when where many much there their its it this that these those my our your from at as vs per can should into than
از به در که را با این آن و چه چند برای های ها یا تا هر است هست بود""".split())


def _expansions(conn, om: bool = False) -> list[tuple[str, str]]:
    """(phrase, expansion) pairs from the glossary and metric aliases, both directions, lower-cased. OpenMetadata rows
    expand only the om lane (om=True): their hundreds of synonyms would otherwise rewrite every curated query."""
    pairs = []
    where = "" if om else f" WHERE coalesce(origin, '') NOT LIKE '{OM_ORIGIN}%'"
    for fa, en in conn.execute("SELECT term_fa, term_en FROM glossary" + where):
        if fa and en:
            pairs += [(nz(fa).lower(), en), (nz(re.sub(r"\s*\(.*?\)", "", en)).lower(), fa)]
    for fa, en, aliases in conn.execute("SELECT term_fa, term_en, aliases FROM glossary WHERE aliases IS NOT NULL "
                                        "AND aliases != ''" + where.replace("WHERE", "AND")):
        for al in re.split(r"[;,،؛\n]", aliases):
            if al.strip():
                pairs += [(nz(al).lower(), en or fa), (nz(al).lower(), fa or en)]
    for name, aliases in conn.execute("SELECT name, aliases FROM metrics WHERE aliases IS NOT NULL AND aliases != ''"
                                      + ("" if om else f" AND coalesce(origin, '') NOT LIKE '{OM_ORIGIN}%'")):
        for al in re.split(r"[;,]", aliases):
            if al.strip():
                pairs += [(nz(al).lower(), name), (nz(name).lower(), al.strip())]
    return [(a, b) for a, b in pairs if len(a) >= 2]


def expand_query(conn, q: str, om: bool = False) -> str:
    ql = nz(q).lower()
    extra = [b for a, b in _expansions(conn, om) if a in ql]
    return " ".join([q, *extra])


def _terms(q: str) -> list[str]:
    q = _fts_text(q) if _vcfg.normalizing() else q
    return [t for t in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if t not in STOPWORDS]


def _fts_query(q: str) -> str:
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in dict.fromkeys(_terms(q)))


def om_ids(conn, types=OM_TYPES) -> set[str]:
    """Rows imported from OpenMetadata (kb/om_import.py). They are searched in their own lane, so their long synonym
    lists never crowd clu's own rows out of the curated ranking (docs/decisions.md, 2026-10-10)."""
    return {r[0] for t in types for r in conn.execute(
        f"SELECT id FROM {TYPES[t]['table']} WHERE origin LIKE ?", (OM_ORIGIN + "%",))}


def _lexical(conn, query: str, etype: str | None, limit: int, om: str = "exclude") -> list[dict]:
    """om: exclude (curated ranking), only (the OpenMetadata lane: metrics and glossary terms) or all."""
    fq = _fts_query(expand_query(conn, query, om=(om == "only")))
    if not fq:
        return []
    omset = om_ids(conn) if om == "exclude" else om_ids(conn, ("metric", "glossary")) if om == "only" else set()
    sql = ("SELECT etype, eid, title, snippet(fts, 3, '[', ']', '…', 12) AS snip FROM fts WHERE fts MATCH ?"
           + (" AND etype = ?" if etype else "") + " ORDER BY bm25(fts) LIMIT ?")
    args: list = [fq, *([etype] if etype else []), limit + (len(omset) if om == "exclude" else 0)
                  if om != "only" else 100000]
    out = []
    for r in conn.execute(sql, args):
        if len(out) >= limit:
            break
        if (om == "exclude" and r["eid"] in omset) or (om == "only" and r["eid"] not in omset):
            continue
        if r["etype"] == "fact":
            sup = conn.execute("SELECT superseded_by FROM facts WHERE id = ?", (r["eid"],)).fetchone()
            if sup and sup[0]:
                continue
        out.append({"type": r["etype"], "id": r["eid"], "title": r["title"], "snippet": r["snip"]})
    return out


def vectors_ready() -> bool:
    """True when the optional vector index exists (see kb/vectors.py)."""
    p = os.environ.get("CLU_KB_VECTORS", str(db_path()).replace(".sqlite", ".vectors.sqlite"))
    return Path(p).exists() and Path(p).stat().st_size > 0


def search(conn, query: str, etype: str | None = None, k: int = 10, mode: str = "lexical",
           rerank: bool = False) -> list[dict]:
    """mode: lexical (FTS5 BM25 with glossary expansion) or hybrid (lexical + local embeddings, fused with RRF).
    rerank=True also reorders the fused top 30 with the cross-encoder (falls back to the fused order when the
    reranker is unavailable); callers decide it, usually from vectors.want_rerank."""
    if mode == "hybrid":
        import vectors  # optional dependency: numpy + transformers
        return vectors.hybrid(conn, query, etype, k, rerank=rerank)
    return _lexical(conn, query, etype, k)


def verify(conn, eid: str, evidence: str = "") -> None:
    row = get(conn, eid)
    if not row:
        raise KeyError(eid)
    upd = {"confidence": "verified"}
    if row["type"] == "fact":
        upd["last_verified"] = _dt.date.today().isoformat()
        if evidence:
            upd["evidence"] = evidence
    upsert(conn, row["type"], {**row, **upd})


def supersede(conn, eid: str, by_id: str) -> None:
    row = get(conn, eid)
    if not row or row["type"] != "fact":
        raise KeyError(f"{eid} is not a fact")
    conn.execute("UPDATE facts SET superseded_by = ?, updated_at = ? WHERE id = ?", (by_id, _now(), eid))
    conn.commit()


def link(conn, from_id: str, to_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO entity_links VALUES (?,?,?)", (from_id, to_id, _now()))
    conn.commit()


def export(conn, export_dir_: str | None = None, etype: str | None = None) -> list[str]:
    d = Path(export_dir_) if export_dir_ else export_dir()
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for t in ([etype] if etype else TYPES):
        rows = conn.execute(f"SELECT * FROM {TYPES[t]['table']} ORDER BY id").fetchall()
        rows = [dict(r) for r in rows if not str(r["origin"] or "").startswith(NO_EXPORT_ORIGINS)]
        p = d / f"{t}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
        written.append(str(p))
    links = conn.execute("SELECT * FROM entity_links ORDER BY from_id, to_id").fetchall()
    (d / "entity_links.jsonl").write_text("".join(json.dumps(dict(r), sort_keys=True) + "\n" for r in links))
    return written


def rebuild(db_path_: str | None = None, export_dir_: str | None = None) -> sqlite3.Connection:
    p = Path(db_path_) if db_path_ else db_path()
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    conn = connect(str(p))
    d = Path(export_dir_) if export_dir_ else export_dir()
    for t, spec in TYPES.items():
        f = d / f"{t}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                _write(conn, t, r)
                _fts_put(conn, t, r["id"], r)
    lf = d / "entity_links.jsonl"
    if lf.exists():
        for line in lf.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                conn.execute("INSERT OR IGNORE INTO entity_links VALUES (?,?,?)", (r["from_id"], r["to_id"], r["created_at"]))
    conn.commit()
    return conn


def stats(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {s['table']}").fetchone()[0] for t, s in TYPES.items()}
