---
name: clu-causal
description: clu's causal inference and experimentation protocol - the assignment interview, identification design with clu-methodologist, pre-analysis plan, estimation with the tested cstat.py toolkit, diagnostics, sensitivity and honest verdicts. Load for causal-impact questions, natural experiments, A/B design or reading (GrowthBook), and "is this statistically meaningful?" checks.
---

# clu causal protocol

`$CLU` = `$HOME/agent-stack/clu`. Expand `$HOME` to your home folder when a tool needs a full path (the Read, Write and Edit tools do). Toolkit: `"$CLU/.venv-causal/bin/python" "$CLU/scripts/causal/cstat.py" <command> ... --out "<run>/results/<name>.json"`. If `.venv-causal` is missing, run `"$CLU/scripts/causal/setup.sh"` yourself (say so in one line). Never hand-write an estimator for a headline number.

The question is always: what would have happened without the change, and how do we know? Start from how treatment was assigned, not from the data you happen to have.

## When this applies

- Impact of a project, launch, policy or campaign; "did X work?"; natural experiments; A/B test design or reading; "is this significant?".
- A single existing number with "is this significant?" can stay standard tier: use `references/stat-checks.md` only, then step 6.

## Steps

1. **Assignment interview.** Ask the fixed questions in `references/method-selection.md` (direct mode: AskUserQuestion, at most 4 at a time; teammate mode: a `NEEDS_ALIGNMENT` block). If a GrowthBook experiment exists, read it first (`python3 "$CLU/scripts/growthbook.py" experiment <id> --cache "<run>/gb" --record`) and ask only what it leaves open. If `growthbook.py` fails (no key, unreachable), say so in one line, ask the user for the experiment facts or an export, and continue with warehouse data if the assignment table is known. For natural-experiment hunting, walk `references/natural-experiments.md` with the user.
2. **Design.** Spawn `clu-methodologist` with the question, the user's words, recall results, the data map and the interview answers, and `ingest` its `helpers/methodologist.json`. Then spawn `clu-challenger` (mode=causal_plan) on its plan and `ingest` its output. Show the user: the causal diagram, the estimand in their words, the ranked designs with their honest weaknesses, and the MDE. Ask them to pick or add facts.
3. **Pre-analysis plan.** Write `<run>/analysis_plan.md` (the methodologist's draft, revised) BEFORE any outcome query: design, estimand, primary metric, guardrails, unit, window, clustering, outlier rule, diagnostics, decision rule, MDE. Log `{"set": {"plan_locked_at": "<now ISO>"}}` with `run_record.py log`. Changes after this point are allowed only if written into the plan with the reason.
4. **Probe.** Check the data supports the design: pre-period length, cohort sizes, never-treated units, donor pool, density near the cutoff, number of clusters, SRM for experiments (`cstat.py srm`). A failed probe returns to step 2. When SRM fails (p < 0.001), say in plain words that the result is invalid and cannot be trusted until the cause of the allocation imbalance is found and fixed; then investigate the cause. Never summarise a winner or a "chance to win" figure as a finding.
5. **Estimate.** Run the planned `cstat.py` command. Append every specification you run (planned or not) as one JSON line to `<run>/spec_log.jsonl`: `{"ts", "command", "args", "estimate", "ci", "status", "reported": true|false}`.
6. **Diagnostics and sensitivity.** Read every diagnostic. A `fail` is reported, never hidden, and blocks the word "effect" without a caveat. Run at least one sensitivity check: placebo date or cutoff, HonestDiD, bandwidth or donor sensitivity, or `cstat.py evalue` for observational designs.
7. **Verify.** Write the headline estimate to `<run>/verify/claims.json`, spawn `clu-verifier` with the run folder and contract only, and `ingest` its `verify/result.json`; it re-derives the number by a second route (another estimator, raw pre/post means as a bound, or `cstat.py gbcheck` for GrowthBook).
8. **Report** (data first, then meaning, per `clu-core/references/report-rules.md`): estimate and CI in business units; the estimand in plain words ("effect on sellers who adopted", "effect at the threshold", "effect on compliers"); the verdict exactly as the toolkit gives it (`effect detected`, `no effect larger than X` only when powered, or `inconclusive`); diagnostics in one line each; how many specifications were run (from `spec_log.jsonl`). Under interference, show the naive estimate as a likely upper bound.
8b. **Analysis record.** Follow clu-core's "Analysis record" (`methods.md` plus `reproduce.ipynb`). In the aggregation `code`, recompute each estimate with `cstat(...)`, using the same arguments as the planned command in `spec_log.jsonl`, and explain the design and its diagnostics in `explain`.
9. **Learn.** Upsert an `experiment` KB row (name, kind, unit, window, design, estimand, primary_metric, result, ci, decision, diagnostics, lessons, run_id) in the run's `add-many` batch, plus lessons from the user's corrections.

## References

- `references/method-selection.md` — assignment interview, decision tree, each method's assumption, data, diagnostics, failure modes, estimand, command.
- `references/experiment-design.md` — two-sided-market experiment design, power, guardrails, GrowthBook setup.
- `references/natural-experiments.md` — where to look for natural experiments in a marketplace and what breaks each design.
- `references/stat-checks.md` — the meaningfulness checklist.
- `references/growthbook.md` — reading GrowthBook, and what clu must add.
