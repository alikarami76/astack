# GrowthBook research for marketplace experimentation

Date: 2026-09-29. Sources: docs.growthbook.io, github.com/growthbook/growthbook (main, cloned sparse; commit dated 2026-09-29), PyPI, growthbook-mcp repo. Facts marked (source) were read from code; (docs) from documentation pages; (unverified) not confirmed.

## 1. What GrowthBook is, licensing, self-hosting

GrowthBook is a feature-flag and A/B-testing platform with a "warehouse-native" design: it stores no event data. It connects read-only to your SQL warehouse, runs SELECT queries, and sends only aggregated statistics to its app for analysis (https://docs.growthbook.io/warehouses). The repo claims 24 SDKs, 11 data sources, CUPED/Bayesian/sequential stats, a REST API and product analytics (https://github.com/growthbook/growthbook).

Licensing is open core. The LICENSE file says everything is MIT except three directories under the GrowthBook Enterprise License: `packages/back-end/src/enterprise`, `packages/front-end/enterprise`, `packages/shared/src/enterprise` (source: LICENSE). The important nuance is that many statistical features are MIT code but gated at runtime by a license key. `packages/shared/src/enterprise/license-consts.ts` defines plans `oss | starter | pro | pro_sso | enterprise`, and the `oss` and `starter` plans get an empty feature set (source). Feature gating by plan:

- Pro (paid): regression-adjustment (CUPED), sequential-testing, quantile-metrics, retention/funnel-metrics, multi-armed-bandits, historical-power, decision-framework, safe-rollout, sticky-bucketing, prerequisites, redirects, precomputed-dimensions.
- Enterprise only: post-stratification, holdouts, multi-metric-queries, pipeline-mode, metric-slices, incremental-refresh, contextual-bandits, custom roles, audit logging, SCIM, metric-effects, dashboards.
- Free/OSS with no license: basic Bayesian and frequentist t-tests, SRM check, feature flags, SDKs, fact tables, dimensions, namespaces, REST API, MCP.

Multiple-testing correction is documented as a settings-level feature but I did not find its plan in the gating list; treat as unverified.

Self-hosting: one Docker image bundles a NextJS front end, an ExpressJS API and a Python stats engine; the only other dependency is MongoDB (or a compatible store such as DocumentDB). `docker compose up -d` gives a working instance on port 3000; a Helm chart exists (https://docs.growthbook.io/self-host). Runs as non-root uid 1000. Licence keys are checked against a license server (`LICENSE_SERVER_URL`), and air-gapped keys exist (source: licenseUtil.ts), so paid features on self-host in a sanctioned or air-gapped environment would need either an offline key arranged with GrowthBook or none at all. Sanctions/billing eligibility with GrowthBook Inc. (US) for a paid key is an open question to ask them; I did not find a policy statement. GrowthBook Cloud (and the hosted MCP at mcp.growthbook.io) is a US SaaS and may be a poor fit depending on where you operate.

## 2. Stats engine (gbstats)

Location: `packages/stats` in the repo, https://github.com/growthbook/growthbook/tree/main/packages/stats.

Important packaging finding: PyPI's `gbstats` latest release is 0.8.0 from 2024-06-20, tagged "Pre-Alpha", MIT, requires pandas<2, numpy<2 (https://pypi.org/project/gbstats/). The repo's pyproject still says 0.8.0 but main is far ahead (bandits, post-stratification, quantile, mid-experiment power, pandas ^2.1). So `pip install gbstats` gives a stale engine; use `pip install "git+https://github.com/growthbook/growthbook.git#subdirectory=packages/stats"` pinned to a commit. My install test of main on Python 3.14 stalled building numpy; use Python 3.10-3.12. I did not execute gbstats, so code below is read from source, not run.

Capabilities (all in source):
- Bayesian engine: `gbstats/bayesian/tests.py` (`BayesianABTest`, `EffectBayesianABTest`, Gaussian priors, relative/absolute). Outputs chance to win, expected loss (risk), credible interval.
- Frequentist: `gbstats/frequentist/tests.py`: two-sided and one-sided Welch t-tests, `SequentialTwoSidedTTest` and one-sided variants (always-valid intervals, tuning parameter default 5000).
- CUPED: `RegressionAdjustedStatistic` and `RegressionAdjustedRatioStatistic` in `models/statistics.py`; theta computed from pre-period covariate. Post-stratification in `models/tests.py` (`PostStratificationSummary`, strata results).
- SRM: `utils.check_srm(users, weights)` is a chi-squared test returning a p-value; the app flags at p<0.001 (docs).
- Ratio metrics: `RatioStatistic`, delta method via `utils.variance_of_ratios`.
- Quantile metrics: `QuantileStatistic` and `QuantileClusteredStatistic` (uses `n_clusters`, i.e. unit-level clustering for event quantiles). Docs: not compatible with CUPED or sequential, not supported on MySQL/Mixpanel (https://docs.growthbook.io/statistics/quantile).
- Multiple-testing correction: Holm-Bonferroni (FWER) and Benjamini-Hochberg (FDR); frequentist engine only; applied across goal metrics, variations and dimension values; computed in the app, not exposed via API (https://docs.growthbook.io/statistics/multiple-corrections). `p_value_corrected` exists in analysis settings; the correction itself is done outside gbstats' t-test classes, so standalone users must apply their own (statsmodels `multipletests` works).
- Dimension breakdowns: `AnalysisSettingsForStatsEngine.dimension`, `max_dimensions=20`; each dimension value is analysed separately with no built-in interaction test.
- Bandits: `bayesian/bandits.py`, Thompson sampling with a 1% minimum weight (https://docs.growthbook.io/bandits/overview); Bayesian only, single decision metric.
- Power/MDE: `power/midexperimentpower.py` (mid-experiment power and additional sample needed). Pre-experiment power calculator is in the app (up to 5 metrics, population from fact table/segment/past experiment; 80% power, alpha 0.05) (https://docs.growthbook.io/statistics/power).
- Decision framework (ship / rollback / review / days left) is app logic driven by target MDE and decision criteria, not in gbstats (https://docs.growthbook.io/app/experiment-decisions).

Standalone use. Two levels:

1. Low level, recommended for clu. Build sufficient statistics from your own query output and call the test classes:
   - Statistics classes take sums, not raw rows: `SampleMeanStatistic(n, sum, sum_squares)`, `ProportionStatistic(n, sum)`, `RatioStatistic(n, m_statistic, d_statistic, m_d_sum_of_products)`, `RegressionAdjustedStatistic(...)` with pre-period sums and covariance terms.
   - Pass a list of `(baseline_stat, variation_stat)` tuples to `TwoSidedTTest(stats, FrequentistConfig(difference_type="relative"|"absolute"|"scaled", alpha=0.05))`, `SequentialTwoSidedTTest(stats, SequentialConfig(...))` or `EffectBayesianABTest(stats, EffectBayesianConfig(...))`, then `.compute_result()`. It returns expected effect, CI, p-value or chance-to-win/risk.
   - Constructor signatures should be checked in `models/statistics.py` before use; I read class names, not every field.
   - Cluster-robust variance is not general: only the event-quantile statistic accepts a cluster count. For clustered designs, aggregate to the cluster level first (one row per cluster) and treat cluster as the unit.
2. High level: `gbstats.gbstats.process_experiment_results(data)` takes the same JSON the app sends: `{metrics: {id: MetricSettings}, analyses: [AnalysisSettings], query_results: [{rows, metrics}], bandit_settings?}`. Rows are one per (dimension, variation) with columns such as `dimension`, `variation`, `users`, `count`, `main_sum`, `main_sum_squares`, plus `*_denominator_*`/`*_covariate_*` columns for ratio and CUPED, in GrowthBook's SQL output shape. This is convenient only if you also generate the SQL in that shape, which means reproducing GrowthBook's SQL templates (`packages/back-end/src/integrations/SqlIntegration.ts`, 120 KB).

Effort: level 1 with plain per-arm SQL aggregates is small (days). Level 2 is a larger port.

## 3. Data sources and how queries work

Supported: Athena, BigQuery, ClickHouse, Databricks, Mixpanel, MsSQL, MySQL/MariaDB, Postgres, Vertica, Presto/Trino, Redshift, Snowflake (https://docs.growthbook.io/warehouses). `ClickHouse.ts` and `Mysql.ts` exist as first-class integrations in `packages/back-end/src/integrations` (source). ClickHouse also serves as GrowthBook's own managed warehouse for event ingestion. Caveats: quantile metrics unsupported on MySQL; incremental refresh and pipeline mode are Enterprise and warehouse-specific.

Query model (https://docs.growthbook.io/app/datasources):
- Identifier types (user_id, anonymous_id, device_id...), with optional identifier join tables when assignment and metric IDs differ.
- Experiment assignment query: rows of identifier, timestamp, experiment_id, variation_id (+ optional dimension columns). Duplicates are handled. Custom assignment works for designs assigned outside the SDK, which matters for us.
- Fact tables + fact metrics (mean, proportion, ratio, quantile, retention, funnel) with SQL templated in Handlebars. Legacy metrics also exist.
- GrowthBook generates SQL per experiment: assignment CTE, metric CTEs joined on identifier and time window (conversion windows, delays), then aggregates to per-variation sums fed to gbstats. Only aggregates leave the warehouse.

For a company with a ClickHouse clickstream: it is a direct fit for an assignment table plus fact tables; MySQL is supported but weaker (no quantiles) and heavy queries on OLTP replicas are risky.

## 4. SDKs and assignment

Assignment is deterministic hashing done client-side or server-side in SDKs: hash(seed + attributeValue) with FNV-1a 32-bit, v2 = `(fnv32a(fnv32a(seed + value) + "") % 10000) / 10000` (source: `packages/sdk-js/src/util.ts`). The bucket is compared with variation ranges. `hashAttribute` picks the unit (default `id`), `hashVersion: 2` recommended, `seed` defaults to the experiment key. Namespaces give mutual exclusion: `["ns-name", start, end]` hashes `"__" + namespace` on the same attribute and includes a user only inside the range; overlapping ranges break independence (https://docs.growthbook.io/lib/build-your-own). Sticky bucketing (Pro) persists assignments.

Implications:
- Buyers vs sessions: choose `hashAttribute` = user_id, anonymous/device id or a session id. Sessions give a different id each time so users cross arms; avoid for pricing.
- Sellers: possible by setting `hashAttribute` = seller_id, but the SDK must be evaluated where the seller_id is known (server-side). The assignment lands in the warehouse only if you log an exposure event (the `trackingCallback`).
- Cluster randomization: works naturally if the hash attribute is a cluster id (category, city, seller cohort). The hash is by attribute value, so all members of a cluster share an arm. But the stats then treat rows as independent users unless you analyse at cluster level, so an analyst must aggregate to cluster units.
- Switchback: no native support (no time-block randomization). Doable by defining an attribute such as `time_block_id` (or market x block) as hashAttribute, logging it, and analysing at that unit. This is a workaround, not a feature; the GrowthBook marketplace article does not claim native switchback support (https://www.growthbook.io/insights/marketplace-experimentation-doordash-upwork-expedia).
- Holdouts: Enterprise only. 5% default, one per feature/experiment, excludes conversion-window metrics, needs a 2-4 week analysis period (https://docs.growthbook.io/app/holdouts). Without a license, emulate with a namespace slice plus prerequisites.
- Because assignment is stateless hashing of an id and seed, an external system (pricing engine) could compute the same buckets in Python/SQL and log its own assignments, using GrowthBook only for analysis.

## 5. REST API and MCP

REST API: full API, versioned `/v1` and `/v2`, base `https://api.growthbook.io/api` on Cloud or your `API_HOST` self-hosted; Bearer or Basic with a secret key or personal access token; 60 requests/minute (https://docs.growthbook.io/api). From the repo's router (source): experiments (list, get, create, update, start, stop, comment), results (`getExperimentResults`, bulk results, `postExperimentSnapshot` to trigger an analysis run), plus fact tables, fact metrics, metrics, data sources, dimensions, segments, saved groups, namespaces, features, SDK connections, reports, queries, snapshots, holdout/release related endpoints. Adjusted (multiple-testing) p-values are not exposed through the API.

MCP: official, MIT, https://github.com/growthbook/growthbook-mcp and https://docs.growthbook.io/integrations/mcp. The server is a thin wrapper with four tools: `growthbook_list_skills`, `growthbook_read_skill`, `growthbook_api_read` (authenticated GET passthrough) and `growthbook_api_write` (POST/PUT/PATCH/DELETE passthrough). So it can read anything the REST API can (experiments, results, metrics, fact tables) and write anything a key permits (create experiments/flags, start/stop). Cloud uses OAuth at mcp.growthbook.io; self-hosted uses stdio (`npx @growthbook/mcp`, `GB_API_KEY`, `GB_API_URL`) or the Docker image `ghcr.io/growthbook/growthbook-mcp` with OAuth 2.1. Note that marketing pages mention "14 tools" for an earlier version; the current docs list four. For clu a read-only key (or restricting to `api_read`) is the safe setup.

## 6. Limits vs big-tech platforms, and where an agent must supplement

GrowthBook is a user-level A/B analysis tool. Gaps relative to Airbnb/DoorDash/Uber-class platforms:
- Interference: no interference-aware estimators, no exposure mapping, no cluster-robust or randomization-inference standard errors (except the quantile cluster variance), no spillover or bias diagnostics. Pricing experiments in a marketplace (buyers compete for the same buybox, sellers' prices interact) are exactly where naive user splits are biased; Airbnb's cluster-randomization pricing study found materially different estimates (https://pubsonline.informs.org/doi/10.1287/mnsc.2020.01157).
- Designs: no switchback, geo/market-level, synthetic control, difference-in-differences, matched-market or two-sided (buyer x seller) randomization support; no budget-split or bipartite designs.
- Quasi-experiments and observational causal inference (DiD, CausalImpact, regression discontinuity): absent.
- Metrics: no built-in heterogeneous treatment effect or CATE modelling, no long-term surrogate modelling, no novelty-effect detection beyond time-series charts, no ratio-of-sums across clusters except via ratio metrics with the delta method at user level.
- Decision framework and power tools assume iid units; MDE from them will be optimistic for clustered/switchback designs (design effect ignored).
- Multiple-testing corrections only frequentist, not on secondary/guardrail metrics.

What clu must supplement: choosing the randomization unit and the interference story before launch; computing design-effect-adjusted power; aggregating to cluster/time-block units before calling gbstats; running DiD/synthetic control for non-randomisable pricing changes; checking spillover (e.g., compare treated sellers' competitors); reading results with guardrails on GMV, margin, and seller-side metrics; validating SRM at the correct unit; documenting decisions.

## 7. Recommendation

A worked example: a company with no experimentation platform yet, a ClickHouse warehouse, and pricing experiments that are marketplace experiments with interference. A sensible sequence:

1. Start with gbstats-only (own repo pin), driven by clu, on ClickHouse aggregates. Reasons: no licensing exposure, no new service to operate, the MIT engine already covers Welch t, Bayesian, sequential, CUPED, delta-method ratios, SRM. Effort is small, and it forces the design questions (unit, cluster, window) that a UI would hide. Apply multiple-testing correction, cluster aggregation and power design yourselves.
2. Pilot GrowthBook self-hosted (Docker + MongoDB, ClickHouse data source, no license) when non-technical teams need assignment, a results UI, a catalogue of experiments and standard fact-table metrics. Expect to lose CUPED, sequential, quantile, bandits, decision framework and holdouts in the UI unless you obtain a license (air-gapped keys exist, eligibility unknown). Use its SDK only where the unit is a user; for sellers/clusters/switchback use custom assignment (log your own assignment table, point GrowthBook's assignment query at it).
3. Check GrowthBook Cloud and its hosted MCP against your data-residency and compliance rules before adopting them (US SaaS).

What the AI analyst does in each case:
- gbstats-only: clu writes per-arm SQL for sums, sum of squares, ratio components and pre-period covariates; runs SRM first; calls gbstats at the right unit; applies Holm or BH; reports CI, effect, power and guardrails; flags interference risk and suggests cluster/switchback or DiD when the design demands.
- GrowthBook self-hosted: clu uses the MCP (self-hosted stdio or Docker, read-only key) or REST to list experiments and fetch results and snapshots, cross-checks numbers against its own gbstats recompute on ClickHouse, explains decisions, and drafts experiment definitions (writes gated by human approval). It also does the design work GrowthBook lacks.
- Neither: clu falls back to its current ad hoc t-tests and DiD; not recommended.

Open questions to resolve: license eligibility and offline keys for your region; a tested gbstats pin on Python 3.11; whether your BI tool can render results (store gbstats output in a warehouse table and chart it there); exact constructor fields of the statistics classes (test before building).

## Key URLs
- https://github.com/growthbook/growthbook (LICENSE, packages/stats, packages/back-end/src/integrations, packages/shared/src/enterprise/license-consts.ts)
- https://pypi.org/project/gbstats/
- https://docs.growthbook.io/self-host, /warehouses, /app/datasources, /statistics/multiple-corrections, /statistics/cuped, /statistics/quantile, /statistics/power, /app/experiment-decisions, /app/holdouts, /bandits/overview, /api, /integrations/mcp
- https://github.com/growthbook/growthbook-mcp
- https://www.growthbook.io/insights/marketplace-experimentation-doordash-upwork-expedia
