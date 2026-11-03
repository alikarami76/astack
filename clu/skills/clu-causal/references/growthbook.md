# Reading GrowthBook, and what clu must add

GrowthBook is a feature-flag and A/B-testing platform that stores no event data: it reads a SQL warehouse and computes user-level test statistics (https://docs.growthbook.io/warehouses). clu reads it through a small, read-only script and adds what GrowthBook does not do. Sources: `docs/research/2026-09-29-growthbook.md` and the GrowthBook documentation URLs below. The research read the code and docs but did not run the stats engine, so treat feature-gating details as indicative.

## Reading an experiment

Use the script, not MCP. Commands, all with `python3 "$CLU/scripts/growthbook.py"`:

- `growthbook.py experiments [--status <status>, e.g. running, stopped or draft] [--q text]` lists experiments.
- `growthbook.py experiment <id> [--cache DIR] [--record]` reads the definition: hash attribute, variations, weights, phases, metrics. `--record` upserts the experiment's facts into the clu KB.
- `growthbook.py results <id> [--phase N] [--cache DIR]` reads the latest computed results.
- `growthbook.py metrics [--q text]` looks up metric definitions.

The script is read-only, refuses redirects and never uses MCP. The API allows 60 requests per minute (https://docs.growthbook.io/api). If the call fails, say so and ask the user for the facts or an export.

## What to check in its results

1. **SRM p-value.** GrowthBook flags SRM at p below 0.001 (docs). If flagged, stop reading and diagnose (`cstat.py srm`).
2. **Phase dates.** Confirm the phase read matches the intended window; a changed traffic split mid-test breaks a pooled read.
3. **Is CUPED on? Is sequential testing on?** CUPED (regression adjustment) and sequential testing are gated by licence: Pro and above, and self-hosted with no licence loses them. Say which was used, because a sequential interval and a fixed-horizon interval answer different questions (https://docs.growthbook.io/statistics/cuped).
4. **Multiple-testing correction.** Holm and Benjamini-Hochberg are documented for the frequentist engine only, are computed in the app, and adjusted p-values are not in the API (https://docs.growthbook.io/statistics/multiple-corrections). Apply `cstat.py multitest --p ... --method bh` yourself across goal metrics, variations and dimension values. Whether correction covers secondary and guardrail metrics is unclear in the research; assume it does not.
5. **Dimension breakdowns.** Each dimension value is analysed separately with no interaction test. Treat them as exploratory.
6. **Unit.** Check the hash attribute matches the unit the story needs (buyer, seller, cluster).

## Recompute

Never trust a platform number on its own. Build per-arm aggregates in SQL: sample count, sum, sum of squares, and for ratio metrics the numerator, denominator and the sum of their products. Then run `cstat.py gbcheck --arms <file> --metric <mean|proportion|ratio> --reported <file>`, which recomputes the statistic and compares it with what GrowthBook reported. A mismatch means the metric definition, the window, the exposure logic or the identifier join differs; find out which before reporting. When `--reported` has `ci_low` and `ci_high`, gbcheck also compares the CI widths and warns if they differ by more than 25%.

gbcheck does not support CUPED: it recomputes the unadjusted statistic. Compare it against GrowthBook's unadjusted (non-CUPED) numbers; if the experiment used CUPED and only adjusted numbers are available, expect a mismatch, usually a narrower GrowthBook CI, and say so rather than treating it as an error.

## What GrowthBook does not do

GrowthBook is a user-level A/B analysis tool. Its power calculator and decision framework assume independent units.

- **Interference.** No interference-aware estimators, no spillover diagnostics. In a marketplace the naive split usually overstates a full rollout. Use the designs in `experiment-design.md` and report the naive number as an upper bound.
- **Cluster and switchback analysis.** Only the event-quantile statistic accepts a cluster count. Aggregate to one row per cluster or time block and use `cstat.py abtest` or `cstat.py permute --cluster`.
- **Cluster-adjusted power.** Its MDE ignores the design effect. Use `cstat.py power --cluster-size --icc`.
- **Quasi-experiments.** No DiD, synthetic control, discontinuity or interrupted time series. Use `cstat.py did`, `synth`, `rdd`, `its`.
- **Heterogeneity, novelty and long-term effects.** No CATE modelling or novelty detection beyond time-series charts.

## Your instance

Write down what you learn about your own GrowthBook here (keep the real URLs and keys out of git): the app URL, where the REST API lives (the app's `/api/init` names the API host if it moves), which hash attributes your experiments use, and whether your metrics are fact metrics (`/fact-metrics`) or legacy metrics (`/metrics`; a firewall or an older version may block one of them). `growthbook.py metrics` reads fact metrics first. Results carry `chanceToBeatControl` (shown as chanceToWin) and each arm's `numerator` and `denominator`, which are the inputs `cstat.py gbcheck` needs. A 400 on `results` usually means the experiment has no analysis yet.

## Access

Use a read-only key only. The research recommends the official MCP server only with a read-only key; clu does not use it and reads through `growthbook.py`. The hosted MCP and GrowthBook Cloud are US-hosted SaaS, which can raise data-residency or sanctions questions depending on where you work (research recommendation). Check whether your licence and region allow paid features; do not assume Pro features exist.
