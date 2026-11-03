PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, kind TEXT, engine TEXT, database TEXT, schema_name TEXT, table_name TEXT,
  grain TEXT, row_estimate INTEGER, freshness TEXT, retention_days INTEGER, speed_class TEXT,
  access TEXT, last_checked TEXT, notes TEXT, aliases TEXT, questions TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS columns (
  id TEXT PRIMARY KEY, source_id TEXT, name TEXT, type TEXT, meaning TEXT, unit TEXT, pitfalls TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS columns_source ON columns(source_id);
CREATE TABLE IF NOT EXISTS code_refs (
  id TEXT PRIMARY KEY, source_id TEXT, column_name TEXT, repo TEXT, path TEXT, line INTEGER,
  role TEXT, what_it_does TEXT, commit_or_date TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS metrics (
  id TEXT PRIMARY KEY, name TEXT, aliases TEXT, definition TEXT, numerator TEXT, denominator TEXT,
  grain TEXT, filters TEXT, unit TEXT, formula_sql TEXT, variants TEXT, pitfalls TEXT,
  agreed_in_run TEXT, confidence TEXT, questions TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY, statement TEXT, topic TEXT, source_id TEXT, confidence TEXT, evidence TEXT,
  run_id TEXT, last_verified TEXT, superseded_by TEXT, aliases TEXT, questions TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS recipes (
  id TEXT PRIMARY KEY, name TEXT, purpose TEXT, source_id TEXT, sql TEXT, params TEXT,
  runtime_s REAL, verified INTEGER, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS benchmarks (
  id TEXT PRIMARY KEY, name TEXT, value TEXT, unit TEXT, period TEXT, as_of TEXT, source TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS glossary (
  id TEXT PRIMARY KEY, term_fa TEXT, term_en TEXT, meaning TEXT, aliases TEXT, questions TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT, path TEXT, status TEXT, summary TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY, name TEXT, summary TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, rule TEXT, why TEXT, trigger_quote TEXT, run_id TEXT, applies_to TEXT, aliases TEXT, questions TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs_index (
  id TEXT PRIMARY KEY, run_id TEXT, date TEXT, type TEXT, question TEXT, topics TEXT,
  outcome TEXT, corrections_count INTEGER, verify_result TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, status TEXT, growthbook_id TEXT, unit TEXT, population TEXT,
  window TEXT, design TEXT, estimand TEXT, primary_metric TEXT, guardrails TEXT, result TEXT, ci TEXT,
  decision TEXT, diagnostics TEXT, lessons TEXT, run_id TEXT, confidence TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS entity_links (
  from_id TEXT, to_id TEXT, created_at TEXT, PRIMARY KEY (from_id, to_id));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  etype UNINDEXED, eid UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2');
