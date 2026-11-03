PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, kind TEXT, name TEXT, feature TEXT, path TEXT, requirement_ids TEXT, description TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS bugs (
  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, feature TEXT, component TEXT, severity TEXT, priority TEXT, status TEXT,
  summary TEXT, requirement_id TEXT, jira_key TEXT, draft_path TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS flaky (
  id TEXT PRIMARY KEY, test_id TEXT, symptom TEXT, cause TEXT, status TEXT, first_seen TEXT, last_seen TEXT, fail_rate TEXT,
  run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, rule TEXT, why TEXT, trigger_quote TEXT, applies_to TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS baselines (
  id TEXT PRIMARY KEY, metric TEXT, target TEXT, environment TEXT, value REAL, unit TEXT, commit_sha TEXT, source_path TEXT,
  run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs_index (
  id TEXT PRIMARY KEY, run_id TEXT, date TEXT, type TEXT, request TEXT, feature TEXT, outcome TEXT, corrections_count INTEGER,
  bugs_found INTEGER, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  etype UNINDEXED, eid UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2');
