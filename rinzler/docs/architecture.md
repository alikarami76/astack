# Rinzler architecture

Rinzler is a QA agent family for your product, a sibling of clu (analyst), quorra (builder) and flynn (designer), laid out the same way: agents, skills, scripts, a knowledge store, run records, tests and an installer. The research behind each choice is in `docs/research.md`.

## Flow of a run

```
request ─► recall (KB: lessons, bugs, flaky, assets, baselines)
        ─► align with the owner (scope, environment, deliverable)
        ─► plan by risk          rinzler-planner   plan.py        requirements.yaml, test-plan.md, charters.md
        ─► author cases          rinzler-eval-author eval_gen.py  functional and AI-only suites
        ─► execute, cheapest level first
             rules and money      datasim.py, evals.py (node/python adapters on the real code)
             API                  evals.py http adapter (local/demo/stage via safety.py)
             browser              rinzler-explorer, Playwright on ~/rinzler-work build (stubbed auth)
             AI features          ai_eval.py + rinzler-judge (calibrated)
             performance          perf.py (Lighthouse, k6, Locust)
        ─► bugs: draft (bug_draft.py) ─► rinzler-verifier ─► dedupe (KB + Jira JQL) ─► owner approves ─► file via jira_tool.py
        ─► report with traceability (evals.py trace)
        ─► learn                 rinzler-curator    kb.py, run_record.py close
```

## Agents and models

| Agent | Model | Job | Why it is separate |
|---|---|---|---|
| rinzler | Opus | lead: aligns, decides severity, keeps the final judgment, talks to the owner | the judgment calls |
| rinzler-planner | Sonnet | requirements, risk, plan, charters, regression selection | long reading of maps and PRDs, kept out of the lead's context |
| rinzler-explorer | Sonnet | charters and Playwright runs with evidence | long tool loops |
| rinzler-eval-author | Sonnet | functional and AI-only suites | separate author from runner, so the cases are not shaped by results |
| rinzler-judge | Sonnet | binary rubric grading of AI outputs | a grader separate from the system and the author; calibrated against humans |
| rinzler-verifier | Sonnet | tries to refute each bug before the owner sees it | adversarial and blind to the finder's reasoning, like clu's verifier |
| rinzler-curator | Haiku | KB batch and run close | mechanical |

Dropped from the first sketch: a separate bug writer (the draft is a structured file the lead fills and `bug_draft.py` checks) and a separate perf agent (perf is scripted; the lead or the explorer runs it). The research synthesis recommends a lean set: planner, executor, verifier and judge, with everything else as a skill or a script.

## Skills
`rinzler-core` (the protocol, preloaded), `rinzler` (the `/rinzler` teammate caller), and one per capability: `rinzler-plan`, `rinzler-evals`, `rinzler-bugs`, `rinzler-data`, `rinzler-perf`, `rinzler-local`.

## Scripts (stdlib plus PyYAML)
| Script | Job |
|---|---|
| `safety.py` | targets (local, demo, stage, test, production), writable paths, secret files, redaction, SELECT-only SQL |
| `plan.py` | product map or PRD to requirements, risk, plan, charters; regression selection |
| `eval_gen.py` | draft functional cases from requirement text |
| `evals.py` | validate, run (python, node, http, playwright, manual), report, diff, trace |
| `checks.py` | code checks shared by both runners |
| `ai_eval.py` | AI-only evals: behaviour, golden, hallucination, checks, calibrated judge, pass@k and pass^k, gates, diff |
| `datasim.py`, `product_rules.py` | rule simulation against oracles, synthetic datasets, reconciliation, SQL gate |
| `bug_draft.py` | draft, dedupe, verdict, fingerprinted approval, Jira payload, paste text |
| `perf/perf.py` | Lighthouse and k6 runs and parsing, baselines, regression compare |
| `local/app.sh` | Rinzler's own builds in `~/rinzler-work` |
| `run_record.py` | run folders |

## Safety
See `skills/rinzler-core/references/safety.md`. The rules live in the agents' instructions and are enforced in code where code can: work folders outside code, target classification, redaction, SELECT-only SQL, and an approval fingerprint that makes it impossible to file a draft the owner did not see.

## Data
- Knowledge store: `kb/` (docs/kb-schema.md).
- Runs: `runs/<date>-<slug>/` (docs/run-schema.md). Only `run.json` and small reports are tracked.

## Stage
Built: everything above, with tests. Not yet done on real systems: a Playwright run on the local build (needs `install` and a free port), a Jira duplicate search (needs your-jira credentials set), the backend stack (heavy; needs the owner's logins), perf runs (k6 not installed), a real calibration set for the AI judge (needs an expert's labels).
