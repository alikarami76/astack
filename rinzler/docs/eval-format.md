# Eval format

Two kinds of suites, both YAML, both carrying requirement ids so results trace back to requirements.

## Functional suite (`scripts/evals.py`)

```yaml
suite: flash-discount-functional        # unique name
feature: Flash discount form   # as in the product map or PRD
requirements: ../requirements.yaml      # optional pointer, used by `evals.py trace`
cases:
- id: CA-R6-002                         # unique in the suite
  title: Typing 5,500 on a 10,000 rial variant shows 45 percent
  requirement_ids: [R6, R5]             # at least one
  kind: edge                            # acceptance | functional | edge | negative | regression | exploratory
  tags: [boundary, money, rounding]     # free; "smoke" cases are always selected for regression
  status: ready                         # draft | ready | retired (retired cases are skipped)
  expected: percent 45                  # the expected behaviour in plain words (always required)
  target: {adapter: node, source: ../datasim/discount_form.js, function: percentFromPrice}
  input: {args: [10000, 5500]}
  checks: [{type: equals, value: 45}]   # code checks; required for python, node and http
```

Adapters:

| adapter | fields | what it does |
|---|---|---|
| `python` | `ref: module:function` | imports the module (next to the suite, or in scripts/) and calls it with `input` as keyword arguments |
| `node` | `source`, `function` | runs a CommonJS file with node and calls the exported function with `input.args` |
| `http` | `url`, `method`, `headers`, `approval` | calls the endpoint after `safety.check_target`; needs `--allow-network`; production writes are refused |
| `playwright` | `grep` | reads the result of the Playwright test with that exact title from `--playwright-results` |
| `manual` | none | reads `--manual-results` (`{case_id: {result, notes}}`) written after exploration |

The observation a check sees is `{"output": ..., "status": ..., "latency_ms": ..., "error": ...}`; `on` picks a dotted path (default `output`). Check types (`scripts/checks.py`): exists, equals, not_equals, in, range, approx, regex, not_regex, contains, not_contains, contains_all, contains_any, type, length, max_value, digits (persian or latin), refusal.

Results (`evals.py run`): one record per case with `status` pass, fail, error, not_run or skipped, each check's verdict and detail, the observation, and a summary. The pass rate is pass / (pass + fail + error); not-run cases are counted and listed, never hidden. `evals.py diff` compares two results by case id: regressed, fixed, still failing, added, removed.

## AI-only suite (`scripts/ai_eval.py`)

Tests the model's behaviour for an AI feature, not the screen around it.

```yaml
suite: seller-assistant-onboarding-ai
feature: AI assistant for new sellers
system_under_test: {description: ..., prompt_version: ..., model: ...}
k: 3                                    # tries per case: pass@k and pass^k
thresholds: {pass_hat_k: 0.9, safety: 1.0, refusal_accuracy: 1.0, p95_latency_ms: 6000, cost_per_case_usd: 0.01,
             judge_min_kappa: 0.6, judge_min_labels: 20}
judge:
  calibration: calibration.yaml         # human-labelled outputs; decides which criteria count
  criteria:
  - {id: grounded, question: "...", pass_if: "..."}
cases:
- id: AI-NAME-001
  input: سلام، چند وقت یک بار می‌تونم اسم فروشگاهم رو عوض کنم؟
  requirement_ids: [AI-R1]
  tags: [golden]                        # golden, paraphrase, safety, injection, privacy, cross-seller, refusal,
                                        # over-refusal, hallucination, ambiguous
  expected_behavior: answer             # answer | refuse | clarify
  paraphrase_of: null                   # id of the case this rephrases (robustness groups)
  golden: {must_include: ["30|۳۰"], must_not_include: ["۶۰ روز"], source: "..."}
  checks: [{type: digits, script: persian}]
  rubric: [grounded]
```

Grading of each try, most trusted first: expected behaviour (answer, refusal or clarifying question); golden facts (every `must_include`, where `a|b` accepts either, with Persian and Latin digits, Arabic and Persian ye and kaf, and ZWNJ normalised); known false claims (`must_not_include`, the hallucination guard); code checks; then rubric criteria from the judge. A criterion's verdicts count only if the calibration gives kappa at or above `judge_min_kappa` on at least `judge_min_labels` labels; otherwise they are shown as advisory.

Outputs file (JSONL, one line per try): `case_id`, `trial`, `output`, `latency_ms`, and optionally `input_tokens`, `output_tokens`, `cost_usd`. Judge labels file (JSONL): `case_id`, `trial`, `criterion`, `pass`, `reason`. Calibration file: `criteria` plus `items` with `criterion`, `input`, `output`, `human` (pass or fail) and, when already graded, `judge`.

Summary: trial pass rate, pass@k, pass^k, safety pass^k, refusal accuracy, over-refusal rate, paraphrase consistency, pass^k by tag, latency p50 and p95, cost total and per case, untrusted criteria, gates and a verdict. `ai_eval.py diff` flags any newly failing safety case and drops of more than 5 points in pass^k, refusal accuracy or paraphrase consistency.

## Data simulation scenario (`scripts/datasim.py`)

```yaml
scenario: lightening-percent-from-price-round
requirement_ids: [R6, R5]
oracle: {ref: "product_rules:percent_from_price", args: [base, price]}      # from the requirement, exact arithmetic
impl: {adapter: node, source: discount_form.js, function: percentFromPrice, args: [base, price]}  # the real code
source_check: {file: $RINZLER_WORK/web-app/src/..., lines: 5-9, sha256: 76535f2451e23f9a}
generate:
  seed: 1405
  count: 0                              # random rows after the boundary combinations
  fields:
    base: {type: choice, values: [], boundaries: [10000, 20000]}
    pct: {type: int, min: 1, max: 99, boundaries: [44, 45]}
    price: {type: expr, expr: "base * (100 - pct) // 100 // 100 * 100"}
  include: [{base: 10000, pct: 45, price: 5500}]
```
