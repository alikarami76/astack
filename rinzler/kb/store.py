"""Rinzler knowledge store. Stdlib only. Same pattern as quorra/kb/store.py: SQLite for search, JSONL export in
kb/export/<type>.jsonl as the tracked source of truth, ids hashed from each type's natural key."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = Path(__file__).with_name("schema.sql")

BUG_STATUS = ("draft", "verified", "approved", "filed", "rejected", "duplicate", "not-a-bug")
FLAKY_STATUS = ("watch", "quarantined", "fixed")
SEVERITIES = ("S1", "S2", "S3", "S4")
PRIORITIES = ("P1", "P2", "P3", "P4")
ASSET_KINDS = ("plan", "cases", "charter", "eval", "ai-eval", "suite", "dataset", "scenario", "perf-script", "runbook")

TYPES: dict[str, dict] = {
    "asset": dict(table="assets", prefix="ast", key=["kind", "name"],
                  cols=["kind", "name", "feature", "path", "requirement_ids", "description", "run_id"],
                  text=["kind", "name", "feature", "description", "requirement_ids"]),
    "bug": dict(table="bugs", prefix="bug", key=["fingerprint"],
                cols=["fingerprint", "title", "feature", "component", "severity", "priority", "status", "summary",
                      "requirement_id", "jira_key", "draft_path", "run_id"],
                text=["title", "feature", "component", "summary", "jira_key"]),
    "flaky": dict(table="flaky", prefix="flk", key=["test_id"],
                  cols=["test_id", "symptom", "cause", "status", "first_seen", "last_seen", "fail_rate", "run_id"],
                  text=["test_id", "symptom", "cause"]),
    "lesson": dict(table="lessons", prefix="les", key=["rule"],
                   cols=["rule", "why", "trigger_quote", "applies_to", "run_id"],
                   text=["rule", "why", "trigger_quote", "applies_to"]),
    "baseline": dict(table="baselines", prefix="bas", key=["metric", "target", "environment"],
                     cols=["metric", "target", "environment", "value", "unit", "commit_sha", "source_path", "run_id"],
                     text=["metric", "target", "environment"]),
    "run": dict(table="runs_index", prefix="run", key=["run_id"],
                cols=["run_id", "date", "type", "request", "feature", "outcome", "corrections_count", "bugs_found"],
                text=["run_id", "type", "request", "feature", "outcome"]),
}
PREFIX_TO_TYPE = {v["prefix"]: k for k, v in TYPES.items()}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def bug_fingerprint(title: str, feature: str = "", component: str = "") -> str:
    """Stable key for a bug: lowercased words of feature, component and title, digits kept, order kept."""
    words = re.findall(r"\w+", f"{feature} {component} {title}".lower())
    return "-".join(words)[:200]


def make_id(etype: str, row: dict) -> str:
    spec = TYPES[etype]
    natural = "|".join(_norm(row.get(k)) for k in spec["key"])
    return f"{spec['prefix']}_{hashlib.sha1(natural.encode()).hexdigest()[:12]}"


def db_path() -> Path:
    return Path(os.environ.get("RINZLER_KB_DB", REPO / "kb" / "kb.sqlite"))


def export_dir() -> Path:
    return Path(os.environ.get("RINZLER_KB_EXPORT", REPO / "kb" / "export"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    return conn


def validate(etype: str, row: dict) -> None:
    if etype not in TYPES:
        raise ValueError(f"unknown type {etype}; expected one of {sorted(TYPES)}")
    if not isinstance(row, dict):
        raise ValueError("row must be an object")
    if etype == "bug":
        if not row.get("fingerprint") and row.get("title"):
            row["fingerprint"] = bug_fingerprint(row["title"], row.get("feature", ""), row.get("component", ""))
        for col, allowed in (("status", BUG_STATUS), ("severity", SEVERITIES), ("priority", PRIORITIES)):
            if row.get(col) not in (None, "") and row[col] not in allowed:
                raise ValueError(f"{col} must be one of {allowed}, got {row[col]!r}")
    if etype == "flaky" and row.get("status") not in (None, "") and row["status"] not in FLAKY_STATUS:
        raise ValueError(f"status must be one of {FLAKY_STATUS}")
    if etype == "asset" and row.get("kind") not in ASSET_KINDS:
        raise ValueError(f"kind must be one of {ASSET_KINDS}, got {row.get('kind')!r}")
    if etype == "baseline" and row.get("value") not in (None, ""):
        float(row["value"])
    if any(not _norm(row.get(k)) for k in TYPES[etype]["key"]):
        raise ValueError(f"empty natural key for {etype}: needs {TYPES[etype]['key']}")


def _fts_put(conn, etype: str, eid: str, row: dict) -> None:
    conn.execute("DELETE FROM fts WHERE eid = ?", (eid,))
    body = " ".join(str(row.get(c) or "") for c in TYPES[etype]["text"])
    body += " " + " ".join(json.loads(row.get("tags") or "[]"))
    conn.execute("INSERT INTO fts(etype, eid, title, body) VALUES (?,?,?,?)",
                 (etype, eid, str(row.get(TYPES[etype]["text"][0]) or ""), body))


def _write(conn, etype: str, merged: dict) -> None:
    spec = TYPES[etype]
    cols = ["id", *spec["cols"], "tags", "origin", "created_at", "updated_at"]
    conn.execute(f"INSERT OR REPLACE INTO {spec['table']} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [merged.get(c) for c in cols])


def upsert(conn, etype: str, row: dict, origin: str = "rinzler", tags: list[str] | None = None) -> str:
    row = dict(row)
    validate(etype, row)
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
    merged["updated_at"] = _now()
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


def rows(conn, etype: str, **where) -> list[dict]:
    q, params = f"SELECT * FROM {TYPES[etype]['table']} WHERE 1=1", []
    for k, v in where.items():
        if v is not None:
            q += f" AND {k} = ?"
            params.append(v)
    return [dict(r) for r in conn.execute(q + " ORDER BY updated_at DESC", params)]


STOPWORDS = set("""a an the of to in on for by with and or is are was were be it this that from at as when not no
از به در که را با این آن و چه برای های ها یا تا هر است نمی می""".split())


def _fts_query(q: str) -> str:
    terms = [t for t in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if t not in STOPWORDS]
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in dict.fromkeys(terms))


def search(conn, query: str, etype: str | None = None, k: int = 10) -> list[dict]:
    fq = _fts_query(query)
    if not fq:
        return []
    sql = ("SELECT etype, eid, title, snippet(fts, 3, '[', ']', '…', 12) AS snip, bm25(fts) AS score FROM fts "
           "WHERE fts MATCH ?" + (" AND etype = ?" if etype else "") + " ORDER BY bm25(fts) LIMIT ?")
    args = [fq, *([etype] if etype else []), k]
    return [{"type": r["etype"], "id": r["eid"], "title": r["title"], "snippet": r["snip"], "score": r["score"]}
            for r in conn.execute(sql, args)]


def export(conn, etype: str | None = None) -> list[str]:
    d = export_dir()
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for t in [etype] if etype else list(TYPES):
        data = [dict(r) for r in conn.execute(f"SELECT * FROM {TYPES[t]['table']} ORDER BY id")]
        p = d / f"{t}.jsonl"
        on_disk = _ids(p)
        lost = on_disk - {r["id"] for r in data}
        if lost:
            raise RuntimeError(f"export would drop {len(lost)} {t} row(s) that exist only in {p}; run `kb.py rebuild` first")
        p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in data), encoding="utf-8")
        written.append(str(p))
    return written


def _ids(p: Path) -> set[str]:
    if not p.exists():
        return set()
    return {json.loads(line)["id"] for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}


def rebuild() -> sqlite3.Connection:
    p = db_path()
    for suffix in ("", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)
    conn = connect()
    for t in TYPES:
        f = export_dir() / f"{t}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                _write(conn, t, r)
                _fts_put(conn, t, r["id"], r)
    conn.commit()
    return conn


def stats(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {s['table']}").fetchone()[0] for t, s in TYPES.items()}
