# Rinzler

Rinzler is a QA agent for Claude Code, a sibling of clu (analyst), quorra (builder) and flynn (designer). It turns a feature or a PRD into a risk-based test plan with traceable cases, writes and runs evals (functional ones, and AI-only ones for AI features), tests your web app on its own local build, checks your product rules by simulation, measures performance against baselines, and drafts bugs that a second agent tries to refute before you see them. Nothing reaches your issue tracker without your explicit yes to the exact draft.

## Install
```
./install.sh            # or the install.sh at the root of this repo, which includes Rinzler
```
Open a new terminal and run `rinzler` (or `claude --agent rinzler`). From any other session, `/rinzler <request>` brings it in as a teammate: it asks its alignment questions through that session and returns a result.

## What to ask it
| You say | Rinzler does |
|---|---|
| "Test plan for the checkout discount form" | numbered requirements from your product map or PRD, risk per requirement with a reason, plan, charters, open questions (rinzler-plan) |
| "Write acceptance evals for this PRD" | draft cases from the requirement text, reviewed and bound to runnable adapters; pass rate by requirement (rinzler-evals) |
| "Evaluate the shop assistant's answers" | AI-only suite: golden facts, paraphrases, refusals, prompt injection, hallucination bait, calibrated judge, pass^k, latency and cost (rinzler-evals) |
| "Is the discount math right?" | simulation of the real code against an exact oracle, smallest failing example (rinzler-data) |
| "Explore the discount step and file what you find" | charters and Playwright on `~/rinzler-work/web-app`, evidence, bug drafts, verifier, duplicate search, your approval, then the issue tracker (rinzler-bugs) |
| "Did this change slow the wizard down?" | Lighthouse and k6 with baselines and percentile thresholds (rinzler-perf) |
| "What should we retest for this diff?" | regression selection from the changed files (rinzler-plan) |
| "Bring up the app locally" | its own clone and dev server, after your go-ahead (rinzler-local) |

## Safety, in short
Rinzler never writes to your own code clones (`~/code` by default); it builds in `~/rinzler-work/`. It never prints or stores secrets and never handles your logins: you log in by hand. It writes only to local, demo or stage; production is never written to, and load tests on shared environments need your approval. Database checks are SELECT-only through your own database skills. Bugs are filed only after the verifier sustains them and you approve the exact draft (its fingerprint). The rules and how the scripts enforce them: `skills/rinzler-core/references/safety.md`.

## Layout
```
agents/      rinzler (Opus) + planner, explorer, eval-author, judge, verifier (Sonnet), curator (Haiku)
skills/      rinzler-core (protocol), rinzler (/rinzler caller), rinzler-plan, -evals, -bugs, -data, -perf, -local
scripts/     safety, plan, eval_gen, evals, checks, ai_eval, datasim, product_rules, bug_draft, run_record,
             perf/ (perf.py, k6 and Locust templates), local/ (app.sh: build and run your app in a work copy)
playwright/  config and evidence fixture copied into the work copy's tests/rinzler/
kb/          knowledge store: assets, bugs, flaky tests, lessons, baselines, runs
config/      jira.json: your issue-tracker project key
runs/        one folder per QA run (created as you work)
docs/        research.md (sources), architecture, eval-format, kb-schema, run-schema
tests/       pytest
```

## Make it yours
- Put your own pricing or business rules in `scripts/product_rules.py` (the shipped functions belong to an invented discount form and show the pattern: an exact oracle written from the requirement, never from the code under test).
- Set your issue-tracker project key in `config/jira.json`, and give Rinzler a Jira skill or tool that can search and create issues if you want it to file bugs.
- Point `scripts/local/app.sh` at your web app with the `RINZLER_APP_*` variables described at the top of that script.
- Point `RINZLER_PRODUCT_MAP` at a folder of JSON feature descriptions if you keep a product map; `plan.py features` reads it.

## Test
```
python3 -m pytest -q
```
Needs Python 3.10+, PyYAML and pytest; node for the node adapter tests (they skip without it).

## Prerequisites for real runs
- Playwright browsers in the work copy (`npx playwright install chromium`).
- For Jira: a Jira skill or tool of your own with credentials set in your environment (never in this repo); the project key goes in `config/jira.json`.
- Optional: k6 (`brew install k6`) or Locust for load tests; `@axe-core/playwright` in the work copy for accessibility scans.
