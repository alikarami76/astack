---
name: rinzler-perf
description: Rinzler's performance protocol - front-end lab metrics with Lighthouse on the local build (LCP, CLS, TBT as the lab stand-in for INP, median of 3 runs), API load tests with k6 or Locust (smoke, load, stress, soak, spike) with p50/p95/p99 and error-rate thresholds, baselines stored in the KB, and regression checks against them. Never against production, and never a load test on a shared environment, without the owner's explicit approval. Load for performance, speed, load or Web Vitals questions.
---

# Performance

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted). Targets pass through `safety.py`: local is free; demo and stage need the owner's approval for load; production load needs his explicit approval for that run and is never the default.

## Front end (Lighthouse on the local build)
1. Build health: `bash "$RINZLER/scripts/local/app.sh" health`. Dev-server numbers are slower than production builds; compare like with like and say which mode was measured.
2. `python3 "$RINZLER/scripts/perf/perf.py" lighthouse http://localhost:3333/<route> --out <run>/perf/<page> --runs 3` (pinned Lighthouse through npx; median of the runs; the spread is reported). Pages behind login need the owner's session: he logs in by hand, or the page is measured with stubbed APIs in Playwright instead.
3. Budgets (web.dev "good"): LCP 2,500 ms or less, CLS 0.1 or less, TBT 200 ms or less (lab stand-in for INP, which needs real interactions). Field INP and LCP come from real-user monitoring, not from here.

## API load (k6 or Locust)
1. Start with `smoke` (1 user, 1 minute) on local. Pick endpoints from the plan; GET only unless the owner approved writes on demo.
2. `python3 "$RINZLER/scripts/perf/perf.py" k6 "$RINZLER/scripts/perf/k6_template.js" --base-url <url> --scenario smoke|load|stress|soak|spike --out <run>/perf [--approval "<owner's words>"]`, with `ENDPOINTS=/a,/b` in the environment. k6 is not installed by default: ask the owner before `brew install k6`. Locust: `$RINZLER/scripts/perf/locustfile.py`.
3. If an endpoint needs a session, the owner exports `SESSION_COOKIE` in his own terminal; Rinzler never reads or prints it.
4. `perf.py parse-k6 <summary.json> --out <run>/perf/metrics.json` gives p50, p90, p95, p99, error rate and requests per second.

## Baselines and regressions
- First run on a target becomes a candidate baseline; the owner accepts it, then `perf.py baseline <metrics.json> --target <page or endpoint> --env local --commit <sha>` stores it in the KB.
- Each later run: `perf.py compare <metrics.json> --baseline <baseline metrics.json> [--budget budget.yaml]`. Defaults: p95 may not get more than 20% worse, p99 25%, error rate under 1%, Lighthouse performance score may not drop more than 0.05, plus the absolute budgets above. Repeat a failing run once before calling it a regression; lab noise is real.
- Report percentiles, never only averages, with the environment, build and run count.
