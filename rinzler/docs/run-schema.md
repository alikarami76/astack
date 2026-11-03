# Run record schema (`run.json`)

Every QA run leaves one folder `runs/<YYYY-MM-DD>-<slug>/` (a `-2`, `-3` suffix on a name collision) with `run.json`, created and edited only through `scripts/run_record.py` (`new`, `append`, `set`, `close`, `show`). Only `run.json` and the small reports are tracked in git (see `.gitignore`); screenshots, traces and raw outputs stay local.

| Field | Type | Written by |
|---|---|---|
| `run_id` | folder name | `new` |
| `date` | `YYYY-MM-DD` | `new` |
| `type` | plan, explore, scripted, eval, ai-eval, data-sim, reconcile, perf, bug, build, regression | `new --type` |
| `request` | the owner's words | `new --request` |
| `feature` | feature name | `new --feature` |
| `environment` | `{name, url, commit, ...}`; name is offline, local, demo or stage | `new --env`, then `set` |
| `mode`, `caller_cwd` | direct or teammate; the caller's folder in teammate mode | `new` |
| `questions` | `{q, answer}` | Rinzler |
| `subagents` | `{name, model, purpose}` | each spawner |
| `assets` | files made (plan, suites, scenarios) | Rinzler |
| `results` | `{kind, path, summary}` per eval, simulation or perf run | Rinzler |
| `bugs` | `{draft, status, verdict, jira_key}` | Rinzler |
| `verification` | verifier summaries | Rinzler |
| `corrections` | `{quote, lesson_id}` | Rinzler, as each correction arrives |
| `notes` | free text, approvals (`{approval: "<owner's words>", for: "..."}`), proposals for the owner | anyone |
| `outcome`, `closed_at` | set by `close`, which also writes the KB `run` row | `close` |

`append` accepts only the list fields and raises KeyError otherwise; `set` cannot change `run_id` or `date`.
