# KB schema

Generated from `kb/store.py` TYPES. Every table also has id, tags (JSON list), origin, created_at, updated_at.

## source (`sources`, id prefix `src_`)
- natural key: kind, database, schema_name, table_name
- columns: kind, engine, database, schema_name, table_name, grain, row_estimate, freshness, retention_days, speed_class, access, last_checked, notes
- searchable text: database, schema_name, table_name, grain, notes

## column (`columns`, id prefix `col_`)
- natural key: source_id, name
- columns: source_id, name, type, meaning, unit, pitfalls
- searchable text: name, meaning, pitfalls

## code_ref (`code_refs`, id prefix `code_`)
- natural key: repo, path, line, source_id, column_name
- columns: source_id, column_name, repo, path, line, role, what_it_does, commit_or_date
- searchable text: repo, path, column_name, what_it_does

## metric (`metrics`, id prefix `met_`)
- natural key: name
- columns: name, aliases, definition, numerator, denominator, grain, filters, unit, formula_sql, variants, pitfalls, agreed_in_run, confidence
- searchable text: name, aliases, definition, numerator, denominator, filters, variants, pitfalls

## fact (`facts`, id prefix `fact_`)
- natural key: statement
- columns: statement, topic, source_id, confidence, evidence, run_id, last_verified, superseded_by
- searchable text: statement, topic, evidence

## recipe (`recipes`, id prefix `rec_`)
- natural key: name
- columns: name, purpose, source_id, sql, params, runtime_s, verified, run_id
- searchable text: name, purpose, sql

## benchmark (`benchmarks`, id prefix `bm_`)
- natural key: name, period, as_of
- columns: name, value, unit, period, as_of, source
- searchable text: name, value, unit, period, source

## glossary (`glossary`, id prefix `gl_`)
- natural key: term_fa, term_en
- columns: term_fa, term_en, meaning
- searchable text: term_fa, term_en, meaning

## project (`projects`, id prefix `proj_`)
- natural key: name
- columns: name, path, status, summary
- searchable text: name, path, summary

## topic (`topics`, id prefix `top_`)
- natural key: name
- columns: name, summary
- searchable text: name, summary

## lesson (`lessons`, id prefix `les_`)
- natural key: rule
- columns: rule, why, trigger_quote, run_id, applies_to
- searchable text: rule, why, trigger_quote, applies_to

## run (`runs_index`, id prefix `run_`)
- natural key: run_id
- columns: run_id, date, type, question, topics, outcome, corrections_count, verify_result
- searchable text: run_id, type, question, topics, outcome

## experiment (`experiments`, id prefix `exp_`)
- natural key: name
- columns: name, kind, status, growthbook_id, unit, population, window, design, estimand, primary_metric, guardrails, result, ci, decision, diagnostics, lessons, run_id, confidence
- searchable text: name, kind, design, estimand, primary_metric, result, decision, lessons

## entity_links
- from_id, to_id, created_at

Confidence values: verified | observed | assumed.
Rows with origin `schema-cache` are rebuilt from the orchestrator schema cache and not exported.
Rows with origin `om:<fqn>` are imported from OpenMetadata (kb/om_import.py): exported, refreshed and removed by each import, searched in the `om` lane (metrics, glossary) and the schema lane (sources, columns), never in the curated ranking.
