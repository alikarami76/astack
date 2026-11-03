# Is this statistically meaningful? The checklist

Run this before calling any result meaningful. Each item names the toolkit command that helps. Sources: `docs/research/2026-09-29-causal-inference.md` (section 3) and `docs/research/2026-09-29-experimentation-platforms.md`. Commands use the prefix `"$CLU/.venv-causal/bin/python" "$CLU/scripts/causal/cstat.py"`.

## Before the analysis

1. **Compute the MDE first.** Say "an effect smaller than X would not be visible" before reading the result. Report the MDE and the CI, not post-hoc observed power. Quasi-experiments have less power than RCTs; DiD variance is driven by the number of treated clusters, not rows. Use `cstat.py power`. Underpowered significant results exaggerate the effect and can flip its sign (https://doi.org/10.1177/1745691614551642).
2. **Write the plan first.** One primary metric, guardrails, unit, window, clustering, outlier rule and decision rule go into `analysis_plan.md` before any outcome query.
3. **Log every specification.** Analyst choices (window, transform, controls, outlier rule) multiply the paths. Append every model run, planned or not, to `<run>/spec_log.jsonl` and state the count in the report (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2694998 for specification curves). Choosing after seeing results is forking paths (http://www.stat.columbia.edu/~gelman/research/unpublished/forking.pdf).
4. **SRM for any experiment.** A sample ratio mismatch is a chi-square test of the observed split against the designed split; treat p below 0.001 as "results invalid, do not read" until the cause is found (`cstat.py srm`). Segmenting on anything the treatment affects causes SRM. Repeat it on triggered counts. Source: https://www.microsoft.com/en-us/research/group/experimentation-platform-exp/articles/diagnosing-sample-ratio-mismatch-in-a-b-testing/. The 0.001 threshold is standard practice from the research note's background knowledge.

## Inference

5. **Statistical versus practical significance.** Convert to business units (currency, orders, percent). Put the CI next to the smallest effect that would matter. A wide CI containing zero and a large effect is inconclusive, not "no effect". Claim "no effect larger than X" only when the CI excludes X and the test is powered (the toolkit's verdict does this).
6. **Cluster at the assignment level.** Cluster standard errors at the level of treatment assignment (category, seller, day block), not the row (https://jhr.uwpress.org/content/50/2/317). With fewer than 30 to 40 clusters, use randomisation inference (`cstat.py permute --cluster`) or a bootstrap. For DiD, `cstat.py did` warns in `treated_clusters` below 20 treated clusters; then either add `--n-bootstrap 999`, or build one row per cluster with its post-minus-pre change and run `cstat.py permute --cluster <col> --arm <treated col> --y <change col>`. A wild cluster bootstrap is not in the toolkit.
7. **Time series need HAC errors.** Autocorrelation shrinks naive errors sharply. Use HAC (Newey-West), a block bootstrap, or collapse to pre and post as a check (https://academic.oup.com/qje/article/119/1/249/1876068).
8. **Multiple comparisons.** Pre-specify one primary metric. For secondary metrics and subgroups control the false discovery rate with Benjamini-Hochberg, or Holm for a family (`cstat.py multitest --method bh|holm`). Label everything else exploratory. With several treatment arms the toolkit's headline CI is Bonferroni-adjusted.
9. **Resample at the cluster level.** A row-level bootstrap on clustered data understates variance. Permutation tests should reassign labels within the same assignment scheme.
10. **Peeking.** Repeated looks at a fixed-horizon p-value inflate false positives (https://arxiv.org/pdf/1512.04922). Fix the horizon or use a sequential method.

## Traps

11. **Regression to the mean.** Units chosen for extreme recent performance (the worst sellers, the lowest-converting categories) bounce back without treatment. Pick controls with the same rule, or use a design that breaks the link.
12. **Simpson's paradox and mix shift.** The aggregate and the subgroups can disagree when the mix changes, for example a discount that attracts smaller baskets. Compare like with like.
13. **Survivorship and selection on the outcome.** Analysing only sellers who still list, or only buyers who ordered, conditions on something the treatment can change. Use intent-to-treat on the original cohort (https://doi.org/10.1111/j.1467-937X.2009.00536.x for bounds).
14. **Heavy-tailed revenue.** A few very large sellers or buyers dominate variance. Fix a winsorising or trimming rule in advance, report raw and capped, and do not choose it after seeing the result. For outcomes with zeros, do not use log(y + 1) because the effect depends on the unit; use Poisson pseudo-maximum likelihood (PPML, pyfixest `fepois`) or split the extensive and intensive margins (https://doi.org/10.1093/qje/qjad054). `cstat.py abtest --winsorize` applies a cap.
15. **Ratio metrics.** When the denominator is not the randomisation unit (orders per session), row-level t-tests understate variance. Use the delta method at the randomisation unit, or a cluster bootstrap (https://arxiv.org/abs/1803.06336). `cstat.py abtest --den` does this.
16. **Variance reduction.** Adjust with a pre-period covariate (CUPED, `--pre`); the covariate must be pre-treatment (https://dl.acm.org/doi/10.1145/2433396.2433413).
17. **Twyman's law.** A figure that looks too good or too interesting is usually wrong; check instrumentation, SRM and outliers first (Kohavi, Tang, Xu: https://experimentguide.com/).
18. **Bad controls.** Never adjust for a mediator when you want the total effect, and never for a collider (https://ftp.cs.ucla.edu/pub/stat_ser/r493.pdf).
19. **Sensitivity.** Observational claims need `cstat.py evalue`; DiD needs HonestDiD; RD needs bandwidth and donut checks (the donut is a manual step, not in the toolkit); synthetic control needs placebo runs and leave-one-out donors (leave-one-out is a manual step, not in the toolkit: re-run with `--donors` minus the top donor).
20. **Heterogeneity.** Only pre-specified subgroups are confirmatory. Data-driven subgroup hunting is forking paths.

## Marketplace specifics

21. **Concurrent campaigns.** Sale events overlap most launches. Include event-by-category controls or drop event windows; a category DiD is clean only if events hit both sides equally.
22. **Calendar.** Holidays on a lunar calendar (Ramadan, Muharram) move about 11 days a year against the Gregorian calendar, and others follow a local calendar such as the Jalali one. Build week-of-month, holiday and weekend features for your market (`jdatetime` or `persiantools` for the Jalali calendar), and know which days are your market's weekend.
23. **Inflation and currency.** In a high-inflation or fast-moving currency, levels across dates measure inflation, not trade. Compare in a stable currency (local price divided by that day's exchange rate), or within one period.
24. **Substitution.** Cannibalisation across sellers and categories breaks the no-interference rule. Measure at the level where substitution happens (category or query), not the SKU, and report the total effect on the category next to the treated items.
25. **Document exclusions.** State every exclusion and why, and any data-definition or logging change near the launch date.
