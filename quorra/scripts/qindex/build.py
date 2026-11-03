# scripts/qindex/build.py
"""Full and incremental index builds from a fixed commit."""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

import gitsrc
from qindex import db
from qindex.java import extract_java, extract_java_config, extract_java_migration, is_migration
from qindex.names import words_text
from qindex.php import Symbol, extract_cron, extract_php, is_cron_file
from qindex.redact import scrub_obj
from qindex.ts import extract_ts, resolve_api_urls

EXCLUDE = re.compile(r"(^|/)(vendor|node_modules|dist|build|public|coverage|tests?|tests-legacy|__tests__|mocks?|"
                     r"playwright-report|test-results|\.storybook)/")
EXCLUDE_TS = re.compile(r"\.(test|spec|stories)\.tsx?$|\.d\.ts$|\.min\.js$")
EXCLUDE_JAVA = re.compile(r"(^|/)(src/test|build|\.gradle|graphify-out)/")
# Bump whenever an extractor changes what it writes (new kinds, redaction, signature shape). An index built by another
# version is rebuilt in full, even at the same sha: incremental builds only re-read changed files.
EXTRACTOR_VERSION = "5"
JAVA_RESOURCE = re.compile(r"(^|/)src/main/resources/(?:(?:[^/]+/)*application[\w.\-]*\.(?:ya?ml|properties)$|(?:[^/]+/)*(?:db/migration|changelog)/[^/]+$)")


def should_index(path: str, lang: str) -> bool:
    if lang == "java":
        if EXCLUDE_JAVA.search(path):
            return False
        if path.endswith(".java"):
            return re.search(r"(^|/)src/main/", path) is not None
        return JAVA_RESOURCE.search(path) is not None
    if EXCLUDE.search(path):
        return False
    if is_cron_file(path):
        return True
    if lang == "php":
        return path.endswith(".php")
    return path.endswith((".ts", ".tsx")) and not EXCLUDE_TS.search(path)


def _extract(path: str, text: str, lang: str) -> list[Symbol]:
    if lang == "java":
        if path.endswith(".java"):
            return extract_java(path, text)
        return extract_java_migration(path, text) if is_migration(path) else extract_java_config(path, text)
    if is_cron_file(path):
        return extract_cron(path, text)
    return extract_php(path, text) if lang == "php" else extract_ts(path, text)


def _insert(conn, repo_name: str, path: str, symbols: list[Symbol]) -> None:
    for s in symbols:
        cur = conn.execute("INSERT INTO symbols (repo, kind, name, fqname, file, line, signature, extra) VALUES (?,?,?,?,?,?,?,?)",
                           (repo_name, s.kind, s.name, s.fqname, path, s.line, s.signature, json.dumps(s.extra, ensure_ascii=False)))
        conn.execute("INSERT INTO sym_fts (rowid, words, fqname, signature, file) VALUES (?,?,?,?,?)",
                     (cur.lastrowid, f"{words_text(s.name)} {s.name} {s.kind}", s.fqname, s.signature, words_text(path)))


def _delete_files(conn, paths: list[str]) -> None:
    for p in paths:
        ids = [r[0] for r in conn.execute("SELECT id FROM symbols WHERE file = ?", (p,))]
        conn.executemany("DELETE FROM sym_fts WHERE rowid = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM symbols WHERE file = ?", (p,))
        conn.execute("DELETE FROM failures WHERE file = ?", (p,))


def _resolve_ts_urls(conn) -> None:
    """Re-resolve every API url from stored rows, so a changed endpoint re-resolves unchanged clients elsewhere."""
    rows = conn.execute("SELECT id, kind, name, extra FROM symbols "
                        "WHERE kind IN ('endpoint', 'api_client', 'constant', 'ui_route')").fetchall()
    syms = [Symbol(r["kind"], r["name"], r["name"], 0, "", json.loads(r["extra"])) for r in rows]
    resolve_api_urls(syms)
    for s in syms:  # urls joined across files are new strings: scrub them like any extracted value
        s.extra = scrub_obj(s.extra)
    conn.executemany("UPDATE symbols SET extra = ? WHERE id = ?",
                     [(json.dumps(s.extra, ensure_ascii=False), r["id"]) for s, r in zip(syms, rows) if r["kind"] in ("api_client", "endpoint")])


def _resolve_kafka_topics(conn) -> None:
    """Resolve ${key} consumer topics from the repo's own application*.properties/yml config rows (default profile first).
    Rebuilt from stored rows on every build, so a changed config file re-resolves unchanged listeners."""
    cfg: dict[str, list] = {}
    for r in conn.execute("SELECT name, file, extra FROM symbols WHERE kind = 'config' AND file LIKE '%application%' ORDER BY file"):
        x = json.loads(r["extra"])
        if "value" in x:
            cfg.setdefault(r["name"], []).append(x)
    for r in conn.execute("SELECT id, name, extra FROM symbols WHERE kind = 'consumer'").fetchall():
        x = json.loads(r["extra"])
        key = x.get("placeholder")
        if not key:
            continue
        topic = None
        for c in sorted(cfg.get(key, []), key=lambda c: bool(c.get("profile"))):
            v = c["value"].strip()
            m = re.fullmatch(r"\$\{[^}:]+:([^}]+)\}", v)
            v = m.group(1) if m else v
            if v and "${" not in v and v != "<redacted>":
                topic = v
                break
        x.pop("topic", None)
        sig = f"consumer {r['name']}"
        if topic:
            x["topic"] = topic
            sig += f" -> {topic}"
        conn.execute("UPDATE symbols SET extra = ?, signature = ? WHERE id = ?", (json.dumps(x, ensure_ascii=False), sig, r["id"]))
        conn.execute("UPDATE sym_fts SET signature = ? WHERE rowid = ?", (sig, r["id"]))


def _counts(conn) -> tuple[int, int]:
    return (conn.execute("SELECT count(*) FROM symbols").fetchone()[0],
            conn.execute("SELECT count(*) FROM failures").fetchone()[0])


def _record_failure(conn, path: str, error: str) -> None:
    conn.execute("INSERT OR REPLACE INTO failures VALUES (?, ?)", (path, error[:300]))


def build_index(name: str, repo: Path, lang: str, sha: str, db_path) -> dict:
    """Build or refresh the index at `sha`. All changes are one transaction: any error rolls the index back to its
    previous state (old sha included), so a failed build never leaves a broken base for the next incremental run."""
    conn = db.connect(db_path)
    try:
        old = db.get_meta(conn, "sha")
        same_extractor = db.get_meta(conn, "extractor_version") == EXTRACTOR_VERSION
        if old == sha and same_extractor:
            symbols, failures = _counts(conn)
            return {"mode": "noop", "sha": sha, "files": 0, "symbols": symbols, "failures": failures}
        conn.execute("BEGIN")
        try:
            changes, mode = None, "full"
            if old and same_extractor:
                try:
                    changes = gitsrc.changed_files(repo, old, sha)
                    mode = "incremental"
                except gitsrc.GitError:
                    changes, mode = None, "full"
            if mode == "full":
                conn.execute("DELETE FROM symbols")
                conn.execute("DELETE FROM sym_fts")
                conn.execute("DELETE FROM failures")
                todo = [p for p in gitsrc.list_files(repo, sha) if should_index(p, lang)]
            else:
                touched = [p for _, p in changes if should_index(p, lang)]
                _delete_files(conn, touched)
                todo = [p for st, p in changes if st != "D" and should_index(p, lang)]
            for path, data in gitsrc.read_files(repo, sha, todo):
                if data is None:
                    _record_failure(conn, path, "unreadable blob")
                    continue
                try:
                    symbols = _extract(path, data.decode("utf-8", "replace"), lang)  # fully extracted before any insert
                    conn.execute("SAVEPOINT f")
                    try:
                        _insert(conn, name, path, symbols)
                    except Exception:
                        conn.execute("ROLLBACK TO f")
                        raise
                    finally:
                        conn.execute("RELEASE f")
                except Exception as e:  # noqa: BLE001 - every failure is recorded and counted, never silent
                    _record_failure(conn, path, f"{type(e).__name__}: {e}")
            if lang == "ts":
                _resolve_ts_urls(conn)
            if lang == "java":
                _resolve_kafka_topics(conn)
            db.set_meta(conn, "repo", name)
            db.set_meta(conn, "sha", sha)
            db.set_meta(conn, "extractor_version", EXTRACTOR_VERSION)
            db.set_meta(conn, "built_at", _dt.datetime.now().isoformat(timespec="seconds"))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        symbols, failures = _counts(conn)
        return {"mode": mode, "sha": sha, "files": len(todo), "symbols": symbols, "failures": failures}
    finally:
        conn.close()
