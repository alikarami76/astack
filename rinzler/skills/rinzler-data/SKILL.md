---
name: rinzler-data
description: Rinzler's data validation protocol - simulate your product rules (discounts, price rounding, quantities, campaign and slot eligibility, commissions) with seeded synthetic inputs and boundary values, compare the real implementation with an oracle written from the requirement, shrink mismatches to the smallest example, generate synthetic datasets with expected outputs, and reconcile against real data through the owner's db skills, SELECT-only. Load for "is this calculation right", rule checks, synthetic data, or data reconciliation.
---

# Data validation and simulation

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted).

## The oracle comes from the requirement
Write the expected rule as a function in `$RINZLER/scripts/product_rules.py` (shared product rules) or a module next to the scenario, from the requirement text, the PRD or the product KB, with exact integers or fractions and a docstring naming the source. Never copy the code under test into the oracle; a copied oracle repeats the code's mistakes. If the rule is ambiguous (rounding direction, inclusive limits), ask the owner and record the answer.

## The implementation is the real code
- JavaScript (web-app): copy the function verbatim from Rinzler's work copy into a CommonJS file next to the scenario, remove only type annotations, and add a `source_check` (file, lines, sha256 of those lines). `datasim.py source-check <scenario>` fails when the repo's lines change, so the copy cannot go stale silently.
- Python port: only when node cannot run it; Python floats are IEEE 754 doubles like JavaScript numbers.
- PHP (backend): run on Rinzler's local backend stack (rinzler-local) or test through the API on demo with http eval cases.

## Run
`python3 "$RINZLER/scripts/datasim.py" run <scenario.yaml> --out <run>/datasim/<name>.json --md <run>/datasim/<name>.md`. Inputs: every combination of the declared boundary values, then seeded random rows (`count`), then explicit `include` rows; `type: expr` derives a field from earlier ones (for example the exact price for a target percent). Use the values sellers really use (round prices, common percents) as boundaries: floating-point errors hide in round numbers, not in random ones.

## Synthetic datasets
`datasim.py generate <scenario.yaml> --out <run>/datasim/<name>.jsonl` writes inputs with the oracle's expected output, to feed http or Playwright cases. Synthetic values only; never real seller data.

## Reconciliation against real data (read-only)
1. Write the SELECT; check it: `python3 "$RINZLER/scripts/datasim.py" sqlcheck <query.sql>`.
2. Run it through the owner's skill: `your-db-skill` when the check ties code to data (most product reconciliations), `your-db-analyst` for pure counts. Demo databases first; production data only read, only with a reason, aggregated where possible, and never copied into the KB or a bug.
3. Export the expected values (from the oracle) and the actual values (from the query) as CSV with a shared key, then `datasim.py reconcile <expected.csv> <actual.csv> --key <id> --cols <a,b> [--tol 0]`.
