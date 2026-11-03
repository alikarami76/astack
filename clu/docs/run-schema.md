# Run record schema (schema_version 1)

One folder per task: `runs/<YYYY-MM-DD>-<slug>/` containing `run.json`, `contract.md`, `queries/*.sql`, `results/*`, `helpers/*.json` (helper outputs, schemas in `scripts/helper_output.py`), `verify/` (the verifier's own SQL, `claims.json` written by the lead, `result.json`), `analysis_plan.md` for causal runs, and the deliverable or a link to it.

Git keeps only the run's record: `run.json`, `contract.md`, `answer.md`, `analysis_plan.md`, `verification.md`, `kb_batch.json`, `queries/*.sql|*.py`, `helpers/`, and `verify/*.sql|claims.json|result.json` (a whitelist in `.gitignore`). Raw data stays local. `run_record.py close` commits the run record and `kb/export` in one commit.

Analysis record (types analysis, causal, experiment, dashboard): `analysis_steps.json` (written by the lead), `methods.md` (built by `scripts/run_report.py build`: steps, data, every query with its SQL, aggregations, verification, corrections) and `reproduce.ipynb` (a standalone Jupyter notebook with explanation cells and code cells, generated from `analysis_steps.json`; `run_report.py check` executes it with nbclient, saves its outputs and stores `reproduction` in run.json).

Closing is refused unless: `tier` is set; a standard or deep run (other than a lookup) has `contract_confirmed_at` or a `contract_skipped` reason; and a standard or deep run has `verification.result` in sustained|partial|refuted|not_run (not_run needs `verification.reason`); and, for the analysis-record types, the three files exist and `reproduction.status` is `pass`, or `skipped` with a reason.

`run.json` fields:

| Field | Type | Meaning |
|---|---|---|
| schema_version | int | always 1 |
| run_id | str | folder name |
| created_at / ended_at | ISO time | start and close |
| mode | direct \| teammate | how clu was started |
| caller_cwd | str | the caller's working directory in teammate mode |
| type | lookup \| analysis \| dashboard \| bug \| experiment \| causal | classification |
| tier | quick \| standard \| deep | the protocol tier (required at close) |
| contract_skipped | str \| null | why the contract was not confirmed (the user's words) |
| plan_locked_at | ISO timestamp | causal runs: when analysis_plan.md was fixed |
| question | str | user's words |
| topics | list[str] | KB topics |
| contract_confirmed_at | ISO time \| null | when the user confirmed the contract |
| verification | {result: sustained\|partial\|refuted\|not_run\|null, discrepancies, numbers, failed_checks, file, reason} | set by `ingest` of verify/result.json (tolerance applied in code) |
| outcome | str \| null | one-line result |
| reproduction | {status: pass\|fail\|skipped, mode: live\|cached, checked_at, numbers, error, reason} | result of `run_report.py check` |
| sources | list[{at, ...}] | tables used |
| subagents | list[{at, name, purpose, output}] | helpers, recorded by `ingest` |
| challenges | list[{at, mode, file, blockers, majors}] | challenger rounds |
| reviews | list[{at, file, verdict, fixes}] | editor rounds |
| queries | list[{at, id, source, runtime_s, rows}] | executed queries |
| corrections | list[{at, quote, step}] | user corrections |
| deliverables | list[{at, path_or_url, format}] | outputs |
| kb_writes | list[{at, id}] | KB rows written |
| proposals | list[{at, id}] | proposals raised |
| steps | list[{at, step, note}] | protocol step log |

Closing a run upserts a `runs_index` row in the KB (see kb-schema.md).
