"""flynn design knowledge store. Stdlib only. Adapted from quorra/kb/store.py (no concept expansion, no vectors)."""
from __future__ import annotations

import contextlib
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
# a word meet in the keyword index. The flynn desk deploy copies only flynn/, so without the library next to it text
# is indexed and searched as written. VECSTORE_NORMALIZE=0 turns it off (rebuild the index after switching).
_LIB = REPO.parent / "lib"
try:
    if (_LIB / "vecstore").is_dir() and str(_LIB) not in sys.path:
        sys.path.append(str(_LIB))
    from vecstore import config as _vcfg
    from vecstore.normalize import fts_text as _NORMALIZE
except ImportError:  # the deploy tree
    _vcfg, _NORMALIZE = None, None


def nz(text) -> str:
    """Keyword-index form of text: normalised when the shared library is present and normalisation is on."""
    s = "" if text is None else str(text)
    return _NORMALIZE(s) if _NORMALIZE is not None and _vcfg.normalizing() else s

TIERS = ("verified", "figma-only", "code-only", "drift", "inferred", "proposed")
TOKEN_KINDS = ("color", "space", "radius", "type", "elevation", "motion", "size", "other")
TOKEN_LEVELS = ("raw", "semantic")
REFERENCE_STATUSES = ("candidate", "approved", "rejected")
DESIGN_KINDS = ("screen", "slide", "graphic", "chart")
JSON_COLS = {"anatomy", "variants", "props", "states", "usage", "code_props", "components", "reference_ids", "screenshots",
             # product knowledge (screen, feature, flow): what each page and functionality is for, and how people move
             "key_actions", "data_shown", "apis", "modules", "evidence", "business_rules", "screens", "entities", "steps",
             "entry_points", "edge_cases"}
ENUMS = {"token": {"kind": TOKEN_KINDS, "level": TOKEN_LEVELS, "tier": TIERS}, "component": {"tier": TIERS},
         "screen": {"tier": TIERS}, "feature": {"tier": TIERS}, "flow": {"tier": TIERS},
         "reference": {"status": REFERENCE_STATUSES}, "design": {"kind": DESIGN_KINDS}}

TYPES: dict[str, dict] = {
    "token": dict(table="tokens", prefix="tok", key=["system", "name"], title="name",
                  cols=["system", "name", "kind", "level", "value", "refers_to", "tier", "source", "notes", "run_id"],
                  text=["system", "name", "kind", "notes"]),
    "component": dict(table="components", prefix="cmp", key=["system", "name"], title="name",
                      cols=["system", "name", "description", "anatomy", "variants", "props", "states", "usage", "rtl_notes",
                            "figma_key", "code_path", "code_props", "canva_ref", "tier", "drift_notes", "run_id"],
                      text=["system", "name", "description", "usage", "rtl_notes"]),
    "pattern": dict(table="patterns", prefix="pat", key=["system", "name"], title="name",
                    cols=["system", "name", "description", "components", "rules", "reference_ids", "run_id"],
                    text=["system", "name", "description", "rules"]),
    # Product knowledge of a design system's product: its pages (screens) with their place in the navigation, the
    # functionalities (features) with their business rules, and the UX flows across pages. Flynn reads them so it knows
    # what each thing it designs is for, not only how it looks.
    "screen": dict(table="screens", prefix="scr", key=["system", "route"], title="name",
                   cols=["system", "route", "name", "name_fa", "parent", "nav_group", "nav_order", "purpose", "audience",
                         "key_actions", "data_shown", "states", "components", "apis", "modules", "code_path", "screenshots",
                         "tier", "evidence", "run_id"],
                   text=["system", "route", "name", "name_fa", "nav_group", "purpose", "audience"]),
    "feature": dict(table="features", prefix="fea", key=["system", "name"], title="name",
                    cols=["system", "name", "name_fa", "description", "business_rules", "screens", "entities", "apis", "tier",
                          "evidence", "run_id"],
                    text=["system", "name", "name_fa", "description", "business_rules"]),
    "flow": dict(table="flows", prefix="flo", key=["system", "name"], title="name",
                 cols=["system", "name", "name_fa", "goal", "steps", "entry_points", "edge_cases", "screens", "tier", "evidence",
                       "run_id"],
                 text=["system", "name", "name_fa", "goal", "steps", "edge_cases"]),
    "reference": dict(table="refs", prefix="ref", key=["source_url"], title="title",
                      cols=["source_url", "title", "pattern", "image_path", "why_it_works", "status", "run_id"],
                      text=["title", "pattern", "why_it_works"]),
    "design": dict(table="designs", prefix="dsn", key=["run_id", "name"], title="name",
                   cols=["run_id", "name", "kind", "system", "doc_path", "screenshots", "brief", "contract_path", "verdict",
                         "figma_url", "canva_url"],
                   text=["name", "kind", "system", "brief", "verdict"]),
    "lesson": dict(table="lessons", prefix="les", key=["rule"], title="rule",
                   cols=["rule", "why", "trigger_quote", "run_id", "applies_to"],
                   text=["rule", "why", "trigger_quote", "applies_to"]),
    "taste": dict(table="taste", prefix="tst", key=["statement"], title="statement",
                  cols=["statement", "context", "evidence", "strength", "run_id"],
                  text=["statement", "context", "evidence"]),
    "run": dict(table="runs_index", prefix="run", key=["run_id"], title="run_id",
                cols=["run_id", "date", "type", "request", "system", "outcome", "corrections_count", "verdict"],
                text=["run_id", "type", "request", "system", "outcome"]),
}
PREFIX_TO_TYPE = {v["prefix"]: k for k, v in TYPES.items()}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def make_id(etype: str, row: dict) -> str:
    spec = TYPES[etype]
    natural = "|".join(_norm(row.get(k)) for k in spec["key"])
    return f"{spec['prefix']}_{hashlib.sha1(natural.encode()).hexdigest()[:12]}"


def db_path() -> Path:
    return Path(os.environ.get("FLYNN_KB_DB", REPO / "kb" / "kb.sqlite"))


def export_dir() -> Path:
    return Path(os.environ.get("FLYNN_KB_EXPORT", REPO / "kb" / "export"))


PG_SCHEMA = Path(__file__).with_name("schema_pg.sql")
PG_INT_COLS = {"strength", "corrections_count"}


def pg_url() -> str:
    """The Postgres URL when the Postgres backend is switched on (env FLYNN_DB_URL), else ''."""
    return os.environ.get("FLYNN_DB_URL", "").strip()


class _Row(dict):
    """A Postgres row that reads like sqlite3.Row: by column name and by position."""
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _row_factory(cur):
    names = [d.name for d in (cur.description or [])]
    return lambda vals: _Row(zip(names, vals))


class _PgConn:
    """Thin wrapper over a psycopg connection so the store's SQL (with ? placeholders) runs unchanged."""
    is_pg = True

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, args=()):
        return self._raw.execute(sql.replace("?", "%s"), list(args))

    def commit(self):
        if not self._raw.autocommit:
            self._raw.commit()

    def transaction(self):
        return self._raw.transaction()

    def close(self):
        self._raw.close()


def _is_pg(conn) -> bool:
    return bool(getattr(conn, "is_pg", False))


def _ord(conn) -> str:
    return ' COLLATE "C"' if _is_pg(conn) else ""


def connect_pg(url: str | None = None) -> _PgConn:
    """Connect to the flynn Postgres database; tables live in schema `kb`, created on first use."""
    try:
        import psycopg
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("FLYNN_DB_URL is set but psycopg is not installed (pip install 'psycopg[binary]')") from e
    # autocommit: a reader never sits idle inside a transaction (that would block a rebuild's TRUNCATE elsewhere);
    # writers that need several statements to land together open a transaction block explicitly.
    raw = psycopg.connect(url or pg_url(), row_factory=_row_factory, autocommit=True)
    raw.execute("SELECT set_config('search_path', 'kb,public', false)")
    if raw.execute("SELECT to_regclass('kb.fts') IS NULL AS missing").fetchone()["missing"]:
        with raw.transaction():
            raw.execute("SELECT pg_advisory_xact_lock(724001)")
            raw.execute("CREATE SCHEMA IF NOT EXISTS kb")
            raw.execute(PG_SCHEMA.read_text())
    return _PgConn(raw)


def connect(path: str | None = None):
    """The one entry point. Postgres when FLYNN_DB_URL is set (and no explicit sqlite path), else SQLite as before."""
    if not path and pg_url():
        return connect_pg()
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    return conn


def _as_json(v):
    """Stored rows keep JSON columns as text; accept both forms."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _fts_put(conn, etype: str, eid: str, row: dict) -> None:
    spec = TYPES[etype]
    body = " ".join(str(row.get(c) or "") for c in spec["text"])
    body += " " + " ".join(json.loads(row.get("tags") or "[]"))
    title, body = nz(row.get(spec["title"]) or ""), nz(body)
    if _is_pg(conn):
        conn.execute("INSERT INTO fts(etype, eid, title, body) VALUES (?,?,?,?) ON CONFLICT (eid) DO UPDATE SET "
                     "etype = EXCLUDED.etype, title = EXCLUDED.title, body = EXCLUDED.body",
                     (etype, eid, _pg_text(title), _pg_text(body)))
        return
    conn.execute("DELETE FROM fts WHERE eid = ?", (eid,))
    conn.execute("INSERT INTO fts(etype, eid, title, body) VALUES (?,?,?,?)", (etype, eid, title, body))


def _merge(conn, etype: str, row: dict, origin: str, tags: list[str] | None) -> tuple[str, dict]:
    spec = TYPES[etype]
    eid = make_id(etype, row)
    cur = conn.execute(f"SELECT * FROM {spec['table']} WHERE id = ?", (eid,)).fetchone()
    merged = dict(cur) if cur else {"id": eid, "created_at": _now(), "tags": "[]", "origin": origin}
    for c in spec["cols"]:
        v = row.get(c)
        if v is not None and v != "":
            merged[c] = v if isinstance(v, (int, float)) else (
                json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v).strip())
    merged["tags"] = json.dumps(sorted(set(json.loads(merged.get("tags") or "[]")) | set(tags or [])), ensure_ascii=False)
    cur_origin = str(merged.get("origin") or "")
    if origin and not str(origin).startswith(NO_EXPORT_ORIGINS) and (not cur_origin or cur_origin.startswith(NO_EXPORT_ORIGINS)):
        merged["origin"] = origin
    elif origin and not merged.get("origin"):
        merged["origin"] = origin
    merged["updated_at"] = _now()
    return eid, merged


def _pg_text(v):
    """Postgres rejects NUL characters and has no loose typing: lists and objects become JSON text."""
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False)
    return v.replace("\x00", "") if isinstance(v, str) else v


def _pg_val(col: str, v):
    if col in PG_INT_COLS:
        try:
            return None if v in (None, "") else int(v)
        except (TypeError, ValueError):
            return None
    return _pg_text(v)


def _write(conn, etype: str, merged: dict) -> None:
    spec = TYPES[etype]
    cols = ["id", *spec["cols"], "tags", "origin", "created_at", "updated_at"]
    if _is_pg(conn):
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
        conn.execute(f"INSERT INTO {spec['table']} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                     f"ON CONFLICT (id) DO UPDATE SET {sets}", [_pg_val(c, merged.get(c)) for c in cols])
        return
    conn.execute(f"INSERT OR REPLACE INTO {spec['table']} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [merged.get(c) for c in cols])


def _validate(etype: str, row: dict) -> None:
    if etype not in TYPES:
        raise ValueError(f"unknown type {etype}; expected one of {sorted(TYPES)}")
    if not isinstance(row, dict):
        raise ValueError(f"row must be an object, got {type(row).__name__}")
    for col, allowed in ENUMS.get(etype, {}).items():
        if row.get(col) not in allowed:
            raise ValueError(f"{col} must be one of {allowed}, got {row.get(col)!r}")
    if etype == "component":
        variants = _as_json(row.get("variants")) if row.get("variants") not in (None, "") else {}
        if not isinstance(variants, dict) or not all(
                isinstance(v, list) and all(isinstance(x, str) for x in v) for v in variants.values()):
            raise ValueError("variants must be an object mapping each axis to a list of values")
        props = _as_json(row.get("props")) if row.get("props") not in (None, "") else {}
        if not isinstance(props, dict) or not all(isinstance(v, dict) for v in props.values()):
            raise ValueError("props must be an object mapping each prop name to {type, required?, values?}")
        states = _as_json(row.get("states")) if row.get("states") not in (None, "") else []
        if not isinstance(states, list):
            raise ValueError("states must be a list")
    if any(not _norm(row.get(k)) for k in TYPES[etype]["key"]):
        raise ValueError(f"empty natural key for {etype} (needs {TYPES[etype]['key']})")


def upsert(conn, etype: str, row: dict, origin: str = "", tags: list[str] | None = None) -> str:
    _validate(etype, row)
    eid, merged = _merge(conn, etype, row, origin, tags)
    with conn.transaction() if _is_pg(conn) else contextlib.nullcontext():
        _write(conn, etype, merged)
        _fts_put(conn, etype, eid, merged)
    conn.commit()
    return eid


def get(conn, eid: str) -> dict | None:
    etype = PREFIX_TO_TYPE.get(eid.split("_", 1)[0])
    if not etype:
        return None
    r = conn.execute(f"SELECT * FROM {TYPES[etype]['table']} WHERE id = ?", (eid,)).fetchone()
    return dict(r) | {"type": etype} if r else None


def _parsed(row: sqlite3.Row) -> dict:
    return {k: (_as_json(v) if k in JSON_COLS else v) for k, v in dict(row).items()}


def token_names(conn, system: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM tokens WHERE system = ?", (system,))}


def components(conn, system: str) -> dict[str, dict]:
    return {r["name"]: _parsed(r) for r in conn.execute("SELECT * FROM components WHERE system = ?", (system,))}


def list_rows(conn, etype: str, system: str | None = None) -> list[dict]:
    spec = TYPES[etype]
    q, args = f"SELECT * FROM {spec['table']}", []
    if system is not None and "system" in spec["cols"]:
        q, args = q + " WHERE system = ?", [system]
    return [_parsed(r) for r in conn.execute(q + " ORDER BY id", args)]


STOPWORDS = set("""a an the of to in on for by with and or is are was were be been do does did how what which who why
when where many much there their its it this that these those my our your from at as vs per can should into than
از به در که را با این آن و چه چند برای های ها یا تا هر است هست بود""".split())


def _fts_query(q: str) -> str:
    terms = [t for t in re.findall(r"\w+", nz(q).lower(), flags=re.UNICODE) if t not in STOPWORDS]
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in dict.fromkeys(terms))


def search(conn, query: str, etype: str | None = None, k: int = 10) -> list[dict]:
    """Lexical search (FTS5 BM25) over every entity's title and text columns."""
    fq = _fts_query(query)
    if not fq:
        return []
    if _is_pg(conn):
        tq = " | ".join(f"'{t}':*" if len(t) >= 3 else f"'{t}'" for t in re.findall(r'"([^"]+)"', fq))
        sql = ("SELECT etype, eid, title, ts_headline('simple', body, q, 'StartSel=[, StopSel=], MaxWords=12, MinWords=5, "
               "ShortWord=0') AS snip FROM (SELECT etype, eid, title, body, tsv, to_tsquery('simple', %s) AS q FROM fts) f "
               "WHERE tsv @@ q" + (" AND etype = %s" if etype else "") +
               " ORDER BY ts_rank_cd(tsv, q) DESC, eid COLLATE \"C\" LIMIT %s")
        args = [tq, *([etype] if etype else []), k]
        return [{"type": r["etype"], "id": r["eid"], "title": r["title"], "snippet": r["snip"]}
                for r in conn._raw.execute(sql, args)]
    sql = ("SELECT etype, eid, title, snippet(fts, 3, '[', ']', '…', 12) AS snip FROM fts WHERE fts MATCH ?"
           + (" AND etype = ?" if etype else "") + " ORDER BY bm25(fts) LIMIT ?")
    args: list = [fq, *([etype] if etype else []), k]
    return [{"type": r["etype"], "id": r["eid"], "title": r["title"], "snippet": r["snip"]} for r in conn.execute(sql, args)]


class ExportRefused(RuntimeError):
    """export() would drop rows that exist only on disk."""


def _db_file(conn) -> str:
    if _is_pg(conn):
        return "pg:" + conn.execute("SELECT current_database() AS d").fetchone()["d"] + ":kb"
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


LAST_REBUILD_SKIPPED: list[str] = []
_LAST_REBUILD_DB = ""  # the database file LAST_REBUILD_SKIPPED belongs to


def export(conn, export_dir_: str | None = None, etype: str | None = None, force: bool = False) -> list[str]:
    """Rewrite <type>.jsonl from the database. Refuses (ExportRefused) when that would silently drop rows: after a
    rebuild of this database skipped invalid rows, or when an export file holds ids or lines the database lacks."""
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
        rows = conn.execute(f"SELECT * FROM {TYPES[t]['table']} ORDER BY id{_ord(conn)}").fetchall()
        keep = _ship_generated()
        rows = [dict(r) for r in rows if not str(r["origin"] or "").startswith(NO_EXPORT_ORIGINS)
                or (dict(r).get("system") or "") in keep]
        if not rows and not (d / f"{t}.jsonl").exists():
            continue
        p = d / f"{t}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
        written.append(str(p))
    return written


def _ship_generated() -> set[str]:
    """Systems whose machine-measured rows ship with flynn (systems/systems.json ship_generated)."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import systems_policy
        return systems_policy.ship_generated()
    except ImportError:
        return set()


SYSTEM_TYPES = ("component", "token", "pattern", "screen", "feature", "flow")


def sync_systems(conn, systems: list[str], export_dir_: str | None = None) -> dict:
    """Make the store's design-system rows (components, tokens, patterns) of `systems` match the export exactly:
    upsert every exported row and delete rows of those systems that the export no longer has. Other rows (lessons,
    runs, references, other systems) are untouched. The team server runs this on every start."""
    d = Path(export_dir_) if export_dir_ else export_dir()
    want = set(systems)
    out = {"upserted": 0, "deleted": 0, "skipped": []}
    for t in SYSTEM_TYPES:
        f = d / f"{t}.jsonl"
        keep_ids: dict[str, set] = {s: set() for s in want}
        if f.exists():
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    out["skipped"].append(f"{f.name}:{n}")
                    continue
                if r.get("system") not in want or not r.get("id"):
                    continue
                try:
                    _validate(t, r)
                    _write(conn, t, r)
                    _fts_put(conn, t, r["id"], r)
                    keep_ids[r["system"]].add(r["id"])
                    out["upserted"] += 1
                except (ValueError, TypeError) as e:
                    out["skipped"].append(f"{f.name}:{n}: {e}")
        table = TYPES[t]["table"]
        for s in want:
            for (rid,) in conn.execute(f"SELECT id FROM {table} WHERE system = ?", (s,)).fetchall():
                if rid not in keep_ids[s]:
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
                    conn.execute("DELETE FROM fts WHERE eid = ?", (rid,))
                    out["deleted"] += 1
    conn.commit()
    return out


def rebuild(db_path_: str | None = None, export_dir_: str | None = None):
    """Rebuild the database from the JSONL export. Invalid rows are skipped, reported on stderr and listed in
    LAST_REBUILD_SKIPPED. Raises FileNotFoundError, leaving the database untouched, if there is nothing to load."""
    use_pg = not db_path_ and bool(pg_url())
    p = Path(db_path_) if db_path_ else db_path()
    d = Path(export_dir_) if export_dir_ else export_dir()
    if not d.is_dir() or not any(d.glob("*.jsonl")):
        raise FileNotFoundError(f"no *.jsonl export files in {d}; refusing to rebuild an empty database")
    LAST_REBUILD_SKIPPED.clear()
    if use_pg:
        conn = connect_pg()
        tx = conn.transaction()
        tx.__enter__()
        conn.execute("TRUNCATE " + ", ".join([s["table"] for s in TYPES.values()] + ["fts"]))
    else:
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
    if use_pg:
        tx.__exit__(None, None, None)
    global _LAST_REBUILD_DB
    _LAST_REBUILD_DB = _db_file(conn)
    if LAST_REBUILD_SKIPPED:
        print(f"[kb] rebuild skipped {len(LAST_REBUILD_SKIPPED)} invalid row(s):\n  " + "\n  ".join(LAST_REBUILD_SKIPPED),
              file=sys.stderr)
    return conn


def stats(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {s['table']}").fetchone()[0] for t, s in TYPES.items()}
