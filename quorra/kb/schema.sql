PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS conventions (
  id TEXT PRIMARY KEY, repo TEXT, scope TEXT DEFAULT '', area TEXT, rule TEXT, tier TEXT, rationale TEXT, examples TEXT, counter_examples TEXT,
  source TEXT, checked_at_sha TEXT, verified INTEGER, superseded_by TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS concepts (
  id TEXT PRIMARY KEY, name_en TEXT, names TEXT, description TEXT, implementations TEXT, kb_docs TEXT, verified_at_sha TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, rule TEXT, why TEXT, trigger_quote TEXT, run_id TEXT, applies_to TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS exceptions (
  id TEXT PRIMARY KEY, repo TEXT, rule_id TEXT, situation TEXT, decision TEXT, why TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs_index (
  id TEXT PRIMARY KEY, run_id TEXT, date TEXT, type TEXT, request TEXT, repos TEXT, outcome TEXT,
  corrections_count INTEGER, verify_result TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  etype UNINDEXED, eid UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2');
