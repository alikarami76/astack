PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, system TEXT, name TEXT, kind TEXT, level TEXT, value TEXT, refers_to TEXT, tier TEXT, source TEXT,
  notes TEXT, run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS components (
  id TEXT PRIMARY KEY, system TEXT, name TEXT, description TEXT, anatomy TEXT, variants TEXT, props TEXT, states TEXT,
  usage TEXT, rtl_notes TEXT, figma_key TEXT, code_path TEXT, code_props TEXT, canva_ref TEXT, tier TEXT, drift_notes TEXT,
  run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS patterns (
  id TEXT PRIMARY KEY, system TEXT, name TEXT, description TEXT, components TEXT, rules TEXT, reference_ids TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS screens (
  id TEXT PRIMARY KEY, system TEXT, route TEXT, name TEXT, name_fa TEXT, parent TEXT, nav_group TEXT, nav_order TEXT, purpose TEXT, audience TEXT, key_actions TEXT, data_shown TEXT, states TEXT, components TEXT, apis TEXT, modules TEXT, code_path TEXT, screenshots TEXT, tier TEXT, evidence TEXT, run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS features (
  id TEXT PRIMARY KEY, system TEXT, name TEXT, name_fa TEXT, description TEXT, business_rules TEXT, screens TEXT, entities TEXT, apis TEXT, tier TEXT, evidence TEXT, run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS flows (
  id TEXT PRIMARY KEY, system TEXT, name TEXT, name_fa TEXT, goal TEXT, steps TEXT, entry_points TEXT, edge_cases TEXT, screens TEXT, tier TEXT, evidence TEXT, run_id TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS refs (
  id TEXT PRIMARY KEY, source_url TEXT, title TEXT, pattern TEXT, image_path TEXT, why_it_works TEXT, status TEXT, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS designs (
  id TEXT PRIMARY KEY, run_id TEXT, name TEXT, kind TEXT, system TEXT, doc_path TEXT, screenshots TEXT, brief TEXT,
  contract_path TEXT, verdict TEXT, figma_url TEXT, canva_url TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, rule TEXT, why TEXT, trigger_quote TEXT, run_id TEXT, applies_to TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS taste (
  id TEXT PRIMARY KEY, statement TEXT, context TEXT, evidence TEXT, strength INTEGER, run_id TEXT,
  tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs_index (
  id TEXT PRIMARY KEY, run_id TEXT, date TEXT, type TEXT, request TEXT, system TEXT, outcome TEXT, corrections_count INTEGER,
  verdict TEXT, tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  etype UNINDEXED, eid UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2');
