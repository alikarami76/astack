---
name: rinzler-plan
description: Rinzler's planning protocol - from a feature in the product map or a PRD to numbered atomic requirements, likelihood x impact risk with reasons, a risk-based test plan, exploratory charters, open questions, a requirement-to-case-to-result traceability table, and a regression selection from changed files. Load when asked for a test strategy, test plan, test cases, charters or "what should we retest".
---

# Planning

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted).

## Inputs
- A feature from the product map (`$RINZLER_PRODUCT_MAP/*.json`): `python3 "$RINZLER/scripts/plan.py" features --grep <word>` lists them with their rule counts.
- A PRD (markdown, bullets or numbered lines): `plan.py extract --prd <file> --feature "<name>"`.
- Business context from your product knowledge base and quorra's knowledge (`/quorra` for "how does X work in code"). Read-only.

## Steps
1. `python3 "$RINZLER/scripts/plan.py" extract --feature "<name>" --out <run>/requirements.yaml`.
2. Spawn rinzler-planner with the run folder, or do it yourself for a small feature: atomic requirements with sources, re-scored risk with a reason each, `field` names on input rules, open questions with recommended answers.
3. `plan.py plan <run>/requirements.yaml --out <run>/test-plan.md --charters <run>/charters.md`, then edit: approach per risk, environments, entry and exit criteria.
4. Ask the owner the open questions (direct mode) or return them in `NEEDS_ALIGNMENT` (teammate mode). Record the answers in requirements.yaml as decisions.
5. Cases: rinzler-evals turns requirements into suites. Every case carries `requirement_ids`.
6. After execution: `python3 "$RINZLER/scripts/evals.py" trace <suite.yaml>... --requirements <run>/requirements.yaml --results <results.json>... --out <run>/traceability.md`. It lists requirements without a case and cases pointing at unknown requirements; both must be empty or explained.

## Risk scoring
Impact 1-5: 5 money or eligibility or data others rely on; 4 a blocked core step; 3 a wrong state or result shown; 2 wording and display. Likelihood 1-5: complex conditions, rounding, time windows, many states, external services, recent change, past bugs on the feature (KB). High risk (15 and above) gets a simulation or unit-level check, an API check, one E2E path and a charter; medium gets API or E2E; low gets a checklist item.

## Regression selection
`git -C ~/rinzler-work/<repo> diff --name-only <old>..<new> > <run>/changed.txt`, then `plan.py regress --changed <run>/changed.txt --suites <suites>`. It maps changed files to features through the product map's evidence paths and selects their cases plus every case tagged `smoke`. Add features the change can reach indirectly (shared components, API transformers) with the reason. Flaky tests in quarantine (`kb.py list flaky --status quarantined`) run separately and do not gate.
