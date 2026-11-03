# Run record schema (`run.json`)

Every quorra task leaves one folder `runs/<YYYY-MM-DD>-<slug>/` (a `-2`, `-3` suffix is added on a name collision) containing `run.json`. Records are tracked in git because they are the future test set for a standalone quorra. They are created and edited only through `scripts/run_record.py` (`new`, `append`, `set`, `close`).

| Field | Type | Written by |
|---|---|---|
| `run_id` | string, equal to the folder name | `run_record new` |
| `date` | string, `YYYY-MM-DD` | `run_record new` |
| `type` | string, one of `research`, `lookup`, `question`, `refresh`, `explain`, `build`, `ship`, `doc` | quorra, at `new` |
| `request` | string, the request as the user gave it | quorra, at `new` |
| `repos` | list of strings, the repos the task touched | quorra, at `new` |
| `mode` | string, `direct` (default, `claude --agent quorra`) or `teammate` (spawned by `/quorra`) | quorra, at `new` via `--mode` |
| `caller_cwd` | string or null; the calling session's working directory in teammate mode | quorra, at `new` via `--caller-cwd` |
| `questions` | list of `{q, answer}`, each question asked to the user and their answer | quorra |
| `subagents` | list of `{name, model, purpose, tokens}` | each spawner; `tokens` comes from the Agent tool's usage report |
| `reused` | list, KB rows, index entries or rulebooks reused instead of rebuilt | quorra |
| `created` | list, files and KB rows the run produced | quorra |
| `verification` | list, results of the verifier; the last entry is copied to the KB `verify_result` (the string `"none"` when the list is empty) | the verifier's summary, appended by quorra |
| `corrections` | list, corrections from the user and what changed because of them; its length becomes `corrections_count` | quorra, as each correction arrives |
| `notes` | list, free-form observations | anyone in the run |
| `publish` | list or null; `publish.py push` appends one `{fingerprint, items}` entry per push, with one result per repo in `items` (remote branch, MR link and iid, Jira actions, or the error); an older single-object value is kept as the first entry | `publish.py push` |
| `docs` | list or null; `doc.py publish` appends one `{doc_id, type, file, url, version}` entry per page it creates or updates | `doc.py publish` |
| `outcome` | string or null; null until the run is closed | quorra, at `close` |
| `closed_at` | ISO timestamp (seconds) or null | `run_record close` |

`append` accepts only the list fields (`questions`, `subagents`, `reused`, `created`, `verification`, `corrections`, `notes`) and raises `KeyError` for anything else. `set` replaces any field.

`close` also writes one row of type `run` to the KB (columns `run_id, date, type, request, repos` as a comma-joined string, `outcome, corrections_count, verify_result`) and re-exports `kb/export/run.jsonl`. `verify_result` is the last verification entry as JSON, or `"none"` when the run has no verification; it is never empty, so re-closing a run always overwrites the previous value.
