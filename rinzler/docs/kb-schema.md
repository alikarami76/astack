# Knowledge store schema

Same pattern as quorra and clu: a SQLite database (`kb/kb.sqlite`, rebuildable, not tracked) for search, and the JSONL export in `kb/export/<type>.jsonl` as the tracked source of truth. Code: `kb/store.py`; tables: `kb/schema.sql`; CLI: `kb/kb.py`. Every row also has `id`, `tags` (JSON list), `origin`, `created_at`, `updated_at`. The id is `<prefix>_<first 12 hex of sha1>` of the natural key (lowercased, whitespace collapsed); upserting the same key updates the row, and empty values never overwrite stored ones. After `git pull`, run `kb.py rebuild` before adding rows; `export` refuses to drop rows that exist only in the export files.

| Type | Table, prefix | Natural key | Columns | Used for |
|---|---|---|---|---|
| asset | assets, `ast` | kind, name | kind (plan, cases, charter, eval, ai-eval, suite, dataset, scenario, perf-script, runbook), name, feature, path, requirement_ids, description, run_id | reuse before rewriting |
| bug | bugs, `bug` | fingerprint (feature + component + title words) | title, feature, component, severity S1-S4, priority P1-P4, status (draft, verified, approved, filed, rejected, duplicate, not-a-bug), summary, requirement_id, jira_key, draft_path, run_id | duplicate checks, likelihood in risk scoring |
| flaky | flaky, `flk` | test_id | symptom, cause, status (watch, quarantined, fixed), first_seen, last_seen, fail_rate, run_id | quarantine; never retried into green |
| lesson | lessons, `les` | rule | why, trigger_quote (the owner's exact words), applies_to, run_id | corrections outrank defaults |
| baseline | baselines, `bas` | metric, target, environment | value, unit, commit_sha, source_path, run_id | perf and eval regression checks |
| run | runs_index, `run` | run_id | date, type, request, feature, outcome, corrections_count, bugs_found | history; written by `run_record.py close` |

Search is FTS5 BM25 over the text columns and tags (`kb.py search "<words>" [--type bug]`). No vectors yet; the vecstore library in `agent-stack/lib` can be added the way quorra uses it when the store grows.
