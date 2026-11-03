"""quorra knowledge base core. Stdlib only. Adapted from clu/kb/store.py."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = Path(__file__).with_name("schema.sql")
NO_EXPORT_ORIGINS = ("generated",)

# Shared Farsi/Arabic normalisation (agent-stack lib/vecstore/normalize.py, pure Python): Arabic and Persian spellings of
# a word meet in the keyword index, the concept expansion and the vectors. Without the library next to quorra, text
# is used as written. VECSTORE_NORMALIZE=0 turns it off (rebuild the index after switching).
_LIB = REPO.parent / "lib"
try:
    if (_LIB / "vecstore").is_dir() and str(_LIB) not in sys.path:
        sys.path.append(str(_LIB))
    from vecstore import config as _vcfg
    from vecstore.normalize import fts_text as _NORMALIZE
except ImportError:
    _vcfg, _NORMALIZE = None, None


def nz(text) -> str:
    """Keyword-index form of text: normalised when the shared library is present and normalisation is on."""
    s = "" if text is None else str(text)
    return _NORMALIZE(s) if _NORMALIZE is not None and _vcfg.normalizing() else s

TIERS = ("enforced", "dominant", "transitional", "stated_only")
SOURCES = ("code", "tooling", "review", "guide")

TYPES: dict[str, dict] = {
    "convention": dict(table="conventions", prefix="conv", key=["repo", "scope", "area", "rule"], optional_key=["scope"],
                       cols=["repo", "scope", "area", "rule", "tier", "rationale", "examples", "counter_examples", "source",
                             "checked_at_sha", "verified", "superseded_by", "run_id"],
                       text=["repo", "area", "rule", "rationale"]),
    "concept": dict(table="concepts", prefix="cpt", key=["name_en"],
                    cols=["name_en", "names", "description", "implementations", "kb_docs", "verified_at_sha"],
                    text=["name_en", "names", "description"]),
    "lesson": dict(table="lessons", prefix="les", key=["rule"],
                   cols=["rule", "why", "trigger_quote", "run_id", "applies_to"],
                   text=["rule", "why", "trigger_quote", "applies_to"]),
    "exception": dict(table="exceptions", prefix="exc", key=["repo", "rule_id", "situation"],
                      cols=["repo", "rule_id", "situation", "decision", "why", "run_id"],
                      text=["repo", "situation", "decision", "why"]),
    "run": dict(table="runs_index", prefix="run", key=["run_id"],
                cols=["run_id", "date", "type", "request", "repos", "outcome", "corrections_count", "verify_result"],
                text=["run_id", "type", "request", "repos", "outcome"]),
}
PREFIX_TO_TYPE = {v["prefix"]: k for k, v in TYPES.items()}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def make_id(etype: str, row: dict) -> str:
    spec = TYPES[etype]
    # an empty optional key field (scope "") is left out, so unscoped rules keep their (repo, area, rule) id
    natural = "|".join(_norm(row.get(k)) for k in spec["key"] if _norm(row.get(k)) or k not in spec.get("optional_key", ()))
    return f"{spec['prefix']}_{hashlib.sha1(natural.encode()).hexdigest()[:12]}"


def _has_empty_natural_key(etype: str, row: dict) -> bool:
    spec = TYPES[etype]
    return any(not _norm(row.get(k)) for k in spec["key"] if k not in spec.get("optional_key", ()))  # every key field must be present


def db_path() -> Path:
    return Path(os.environ.get("QUORRA_KB_DB", REPO / "kb" / "kb.sqlite"))


def export_dir() -> Path:
    return Path(os.environ.get("QUORRA_KB_EXPORT", REPO / "kb" / "export"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    if "scope" not in [r[1] for r in conn.execute("PRAGMA table_info(conventions)")]:  # database created before scope joined the key
        conn.execute("ALTER TABLE conventions ADD COLUMN scope TEXT DEFAULT ''")
        conn.commit()
    return conn


def _title(etype: str, row: dict) -> str:
    spec = TYPES[etype]
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
    conn.execute(
        f"INSERT OR REPLACE INTO {spec['table']} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [("" if c == "scope" and merged.get(c) is None else merged.get(c)) for c in cols],
    )


def _as_json(v):
    """Stored rows keep JSON columns as text; accept both forms."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _validate(etype: str, row: dict) -> None:
    if etype not in TYPES:
        raise ValueError(f"unknown type {etype}; expected one of {sorted(TYPES)}")
    if not isinstance(row, dict):
        raise ValueError(f"row must be an object, got {type(row).__name__}")
    if etype == "convention":
        if row.get("tier") not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {row.get('tier')!r}")
        if row.get("source") not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {row.get('source')!r}")
    elif etype == "concept":
        names = row.get("names")
        if names not in (None, ""):
            names = _as_json(names)
            if not isinstance(names, dict) or not all(
                    isinstance(v, list) and all(isinstance(x, str) for x in v) for v in names.values()):
                raise ValueError("names must be an object mapping en/fa/codename to lists of strings")
        impls = row.get("implementations")
        if impls not in (None, ""):
            impls = _as_json(impls)
            if not isinstance(impls, list) or not all(
                    isinstance(i, dict) and i.get("repo") and i.get("file") for i in impls):
                raise ValueError("implementations must be a list of objects with repo and file")
    if _has_empty_natural_key(etype, row):
        raise ValueError(f"empty natural key for {etype}")


def upsert(conn, etype: str, row: dict, origin: str = "", tags: list[str] | None = None) -> str:
    _validate(etype, row)
    eid, merged = _merge(conn, etype, row, origin, tags)
    _write(conn, etype, merged)
    _fts_put(conn, etype, eid, merged)
    conn.commit()
    return eid


def bulk_upsert(conn, etype: str, rows: list[dict], origin: str, fts: bool = True) -> int:
    n = 0
    for row in rows:
        _validate(etype, row)
        eid, merged = _merge(conn, etype, row, origin, row.pop("_tags", None))
        _write(conn, etype, merged)
        if fts:
            _fts_put(conn, etype, eid, merged)
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


def _concept_names(conn) -> list[list[str]]:
    groups = []
    for (names,) in conn.execute("SELECT names FROM concepts WHERE names IS NOT NULL AND names != ''"):
        try:
            d = json.loads(names)
        except (TypeError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        group = []
        for k in ("en", "fa", "codename"):
            v = d.get(k)
            if isinstance(v, list):
                group += [n for n in v if isinstance(n, str) and n]
        if group:
            groups.append(group)
    return groups


def _expansions(conn) -> list[tuple[str, str]]:
    """(phrase, expansion) pairs: each concept name expands to every other name of the same concept."""
    pairs = []
    for group in _concept_names(conn):
        for a in group:
            pairs += [(nz(a).lower(), b) for b in group if b != a]
    return [(a, b) for a, b in pairs if len(a) >= 2]


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word match, Unicode-aware, so 'tax' is not found inside 'syntax'."""
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def concept_terms(conn, query: str) -> list[str]:
    ql = query.strip().lower()
    if len(ql) < 3:
        return []
    out: list[str] = []
    for group in _concept_names(conn):
        if any(_has_phrase(ql, n.lower()) or _has_phrase(n.lower(), ql) for n in group):
            out += group
    return list(dict.fromkeys(out))


def expand_query(conn, q: str) -> str:
    ql = nz(q).lower()
    extra = [b for a, b in _expansions(conn) if a in ql]
    return " ".join([q, *extra])


def _terms(q: str) -> list[str]:
    return [t for t in re.findall(r"\w+", nz(q).lower(), flags=re.UNICODE) if t not in STOPWORDS]


def _fts_query(q: str) -> str:
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in dict.fromkeys(_terms(q)))


def _lexical(conn, query: str, etype: str | None, limit: int) -> list[dict]:
    fq = _fts_query(expand_query(conn, query))
    if not fq:
        return []
    sql = ("SELECT fts.etype AS etype, fts.eid AS eid, fts.title AS title, snippet(fts, 3, '[', ']', '…', 12) AS snip "
           "FROM fts LEFT JOIN conventions c ON c.id = fts.eid AND fts.etype = 'convention' "
           "WHERE fts MATCH ?" + (" AND fts.etype = ?" if etype else "")
           + " AND (c.superseded_by IS NULL OR c.superseded_by = '') ORDER BY bm25(fts) LIMIT ?")
    args: list = [fq, *([etype] if etype else []), limit]
    return [{"type": r["etype"], "id": r["eid"], "title": r["title"], "snippet": r["snip"]} for r in conn.execute(sql, args)]


def vectors_ready() -> bool:
    """True when the optional vector index exists (see kb/vectors.py)."""
    p = os.environ.get("QUORRA_KB_VECTORS", str(db_path()).replace(".sqlite", ".vectors.sqlite"))
    return Path(p).exists() and Path(p).stat().st_size > 0


def search(conn, query: str, etype: str | None = None, k: int = 10, mode: str = "lexical",
           rerank: bool = False) -> list[dict]:
    """mode: lexical (FTS5 BM25 with concept-name expansion) or hybrid (lexical + local embeddings through
    lib/vecstore, fused with RRF). rerank=True reorders the fused top 30 with the cross-encoder (the fused order
    stands when it is unavailable)."""
    if mode == "hybrid":
        try:
            import vectors  # optional dependency: numpy and lib/vecstore
            return vectors.hybrid(conn, query, etype, k, rerank=rerank)
        except ImportError as e:
            print(f"[kb] semantic search unavailable ({type(e).__name__}); keyword results only", file=sys.stderr)
    return _lexical(conn, query, etype, k)


class ExportRefused(RuntimeError):
    """export() would drop rows that exist only on disk."""


def _db_file(conn) -> str:
    return next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "") or ""


def _export_ids(p: Path) -> tuple[set[str], list[str]]:
    ids, bad = set(), []
    if not p.exists():
        return ids, bad
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad.append(f"{p.name}:{n}: not JSON")
            continue
        if isinstance(r, dict) and r.get("id"):
            ids.add(r["id"])
        else:
            bad.append(f"{p.name}:{n}: no id")
    return ids, bad


def export(conn, export_dir_: str | None = None, etype: str | None = None, force: bool = False) -> list[str]:
    """Rewrite <type>.jsonl from the database. Refuses (ExportRefused) when that would silently drop rows: after a
    rebuild of this database skipped invalid rows, or when an export file holds ids or lines the database lacks.
    force=True overwrites anyway."""
    d = Path(export_dir_) if export_dir_ else export_dir()
    d.mkdir(parents=True, exist_ok=True)
    types = [etype] if etype else list(TYPES)
    if not force:
        if LAST_REBUILD_SKIPPED and _LAST_REBUILD_DB and _LAST_REBUILD_DB == _db_file(conn):
            raise ExportRefused(f"the last rebuild skipped {len(LAST_REBUILD_SKIPPED)} invalid row(s) that exist only in the "
                                f"export files ({'; '.join(LAST_REBUILD_SKIPPED[:3])}); fix them and rebuild, or export with force")
        problems = []
        for t in types:
            ids, bad = _export_ids(d / f"{t}.jsonl")
            have = {r[0] for r in conn.execute(f"SELECT id FROM {TYPES[t]['table']}")}
            lost = sorted(ids - have)
            if lost:
                problems.append(f"{t}.jsonl holds {len(lost)} id(s) not in the database (e.g. {', '.join(lost[:3])})")
            problems += bad
        if problems:
            raise ExportRefused("export would drop rows: " + "; ".join(problems) + ". Run kb.py rebuild first, or export with force")
    written = []
    for t in types:
        rows = conn.execute(f"SELECT * FROM {TYPES[t]['table']} ORDER BY id").fetchall()
        rows = [dict(r) for r in rows if not str(r["origin"] or "").startswith(NO_EXPORT_ORIGINS)]
        p = d / f"{t}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
        written.append(str(p))
    return written


LAST_REBUILD_SKIPPED: list[str] = []
_LAST_REBUILD_DB = ""  # the database file LAST_REBUILD_SKIPPED belongs to


def rebuild(db_path_: str | None = None, export_dir_: str | None = None) -> sqlite3.Connection:
    """Rebuild the database from the JSONL export. Invalid rows are skipped, reported on stderr and listed in
    LAST_REBUILD_SKIPPED. Raises FileNotFoundError, leaving the database untouched, if there is nothing to load."""
    p = Path(db_path_) if db_path_ else db_path()
    d = Path(export_dir_) if export_dir_ else export_dir()
    if not d.is_dir() or not any(d.glob("*.jsonl")):
        raise FileNotFoundError(f"no *.jsonl export files in {d}; refusing to rebuild an empty database")
    LAST_REBUILD_SKIPPED.clear()
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    conn = connect(str(p))
    for t in TYPES:
        f = d / f"{t}.jsonl"
        if not f.exists():
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                _validate(t, r)
                if not r.get("id"):
                    raise ValueError("missing id")
                _write(conn, t, r)
                _fts_put(conn, t, r["id"], r)
            except (ValueError, TypeError) as e:
                LAST_REBUILD_SKIPPED.append(f"{f.name}:{n}: {e}")
    conn.commit()
    global _LAST_REBUILD_DB
    _LAST_REBUILD_DB = _db_file(conn)
    if LAST_REBUILD_SKIPPED:
        print(f"[kb] rebuild skipped {len(LAST_REBUILD_SKIPPED)} invalid row(s):\n  " + "\n  ".join(LAST_REBUILD_SKIPPED),
              file=sys.stderr)
    return conn


def stats(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {s['table']}").fetchone()[0] for t, s in TYPES.items()}
