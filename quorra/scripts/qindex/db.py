# scripts/qindex/db.py
"""Per-repo code index: one SQLite file with symbols, an FTS5 table over split words, parse failures and meta."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS symbols (
  id INTEGER PRIMARY KEY, repo TEXT, kind TEXT, name TEXT, fqname TEXT, file TEXT, line INTEGER,
  signature TEXT, extra TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS sym_name ON symbols(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS sym_file ON symbols(file);
CREATE INDEX IF NOT EXISTS sym_kind ON symbols(kind);
CREATE TABLE IF NOT EXISTS failures (file TEXT PRIMARY KEY, error TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS sym_fts USING fts5(words, fqname, signature, file, tokenize='unicode61 remove_diacritics 2');
"""


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))
