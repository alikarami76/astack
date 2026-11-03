---
name: rinzler-evals
description: Rinzler's eval protocol - functional eval suites generated from requirements and reviewed (acceptance, boundary, negative, Persian digits) and run through python, node, http, playwright or manual adapters, with pass rate by requirement and diffs against a baseline; and AI-only evals for AI features (golden answers, paraphrase robustness, refusal, safety and prompt injection, hallucination, rubric LLM judge counted only after calibration, pass@k and pass^k, latency and cost). Load when asked for evals, acceptance tests from a PRD, or to evaluate an AI feature.
---

# Evals

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted). The format is in `$RINZLER/docs/eval-format.md`.

## Functional evals (from requirements)
1. Draft: `python3 "$RINZLER/scripts/eval_gen.py" <requirements.yaml> --out <run>/evals/functional.generated.yaml`. It reads limits ("at least", "up to", "between", "under", "N to M percent", "ends with 00", "whole number") and value lists ("Statuses: ...") and writes acceptance, boundary, negative and Persian-digit cases, all `status: draft`, `adapter: manual`.
2. Review with rinzler-eval-author: the generator guesses; a reviewer fixes the guesses, adds cases, binds adapters and writes review-notes.md.
3. Validate: `python3 "$RINZLER/scripts/evals.py" validate <suite.yaml>`.
4. Run: `python3 "$RINZLER/scripts/evals.py" run <suite.yaml> --out <run>/evals/<name>.json`, adding `--playwright-results <run>/e2e/results.json` after a Playwright run, `--manual-results <file.yaml>` for explored cases (`{case_id: {result: pass|fail, notes: ...}}`), and `--allow-network` for http cases (local, demo, stage only; safety.py decides).
5. Report: `evals.py report <results.json> --out <run>/evals/<name>-report.md` (pass rate overall, by requirement and tag, failures, not run).
6. Compare: `evals.py diff <baseline.json> <results.json>` exits 1 when a case that passed now fails. Keep the accepted baseline's path in the KB: `kb.py add asset --json '{"kind": "eval", "name": "<suite> baseline", "path": "...", "feature": "..."}'`.

## AI-only evals (the model's behaviour, not the screen)
1. Suite: rinzler-eval-author writes cases with `input`, `expected_behavior` (answer, refuse, clarify), `golden` (`must_include`, `must_not_include`, source), code `checks`, `rubric` criteria, `paraphrase_of`, and tags (golden, paraphrase, safety, injection, privacy, cross-seller, refusal, over-refusal, hallucination, ambiguous).
2. Outputs: record the system's answers k times per case (default k = 3) into JSONL (`case_id`, `trial`, `output`, `latency_ms`, `input_tokens`, `output_tokens`, `cost_usd`). The owner logs in to demo by hand if the assistant needs a session; or use `--sut-cmd "<command>" --allow-command` for a local or demo endpoint.
3. Judge: code checks first. For rubric criteria spawn rinzler-judge (Sonnet; never the same model family as the system under test when avoidable) to write judge-labels.jsonl, or pass `--judge-cmd "claude -p --model haiku"`.
4. Calibrate: `python3 "$RINZLER/scripts/ai_eval.py" calibrate <calibration.yaml> --out <run>/ai-eval/calibration.json`. A criterion counts only with kappa at or above 0.6 on at least 20 human labels; otherwise its verdicts are advisory. The labels come from one product domain expert, never from Rinzler.
5. Run and report: `ai_eval.py run <suite> --outputs <jsonl> --judge-labels <jsonl> --out <run>/ai-eval/results.json`, then `ai_eval.py report <results.json> --out <run>/ai-eval/report.md`. Gates: pass^k, safety (100%), refusal accuracy (100%), p95 latency, cost per case.
6. Regression: `ai_eval.py diff <baseline.json> <results.json>` fails on any newly failing safety case or a drop in pass^k beyond 5 points. Run it after every prompt or model change.
