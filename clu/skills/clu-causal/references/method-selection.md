# Choosing a causal method

Every estimate ships with its estimand in plain words, its assumption, a falsification test, a confidence interval and the smallest effect the data could detect. When a diagnostic fails, say "we cannot tell".

Sources: the causal-inference research notes in `docs/research/2026-09-29-causal-inference.md`. Free textbooks to point the user to: https://mixtape.scunning.com/, https://theeffectbook.net/, https://miguelhernan.org/whatifbook.

## The assignment interview

Ask these six questions in this order. Stop when the answers point to one design; carry on when they do not.

1. Was there a rule that decided who got the treatment (a score, a threshold, a date, an ID rule)?
2. Was the rollout staggered, and who chose the order and why?
3. Were any units eligible but left untreated, and why?
4. Was there a bug, outage or configuration error that changed exposure for some units?
5. What else changed at the same time (campaigns, price moves, ranking releases, sale events)?
6. Could units opt in, or were they placed in the treatment?

Then check the data: plot exposure by date and unit, look for mass at thresholds, and look for calendar irregularities. Source: https://arxiv.org/abs/2201.01194 (modern DiD survey) and the interview list in the research notes.

## Decision tree

- Exposure was randomised (hash bucket, holdout, odd/even day)
  - Analyse as an RCT. Check interference first (see `experiment-design.md`).
- A sharp rule decided treatment
  - Score or threshold: regression discontinuity (sharp, or fuzzy if take-up is imperfect).
  - A date only: interrupted time series, or regression discontinuity in time.
  - Agents pile up at the threshold: bunching. Toolkit support is deferred.
- Units were treated at different dates
  - Staggered DiD with a modern estimator. Never plain TWFE.
- One treated unit or a few, many possible donors
  - Synthetic control or synthetic DiD.
- Treated and comparable units observed before and after
  - DiD with an event study.
- Only one treated unit and no comparison series
  - Interrupted time series. This is the weakest design; say so.
- Something nudged treatment and plausibly affects the outcome only through it (a random push message, reviewer assignment; an outage only if it changed the treatment alone)
  - Instrument, with a complier estimand.
- Only observed covariates
  - Matching, IPW or doubly robust, with a mandatory sensitivity analysis (`cstat.py evalue`).

Treatment intensity that varies (discount depth, days exposed) needs dose-response DiD; its assumptions are stronger, so warn the user (https://arxiv.org/abs/2107.02637).

## RCT (randomised experiment)

**Assumption:** random assignment, no interference between units, no differential attrition. In a marketplace interference is real: treated sellers' price cuts pull demand from control sellers.
**Data needed:** an assignment table with unit, arm and timestamp; outcomes at the randomisation unit.
**Diagnostics:** SRM, balance on pre-period metrics, an A/A run. See `stat-checks.md` and `experiment-design.md`. Source: https://experimentguide.com/.
**Fails when:** peeking, novelty effects, interference, ratio metrics analysed at the wrong level.
**Estimand:** the average effect of the change on the randomised units; under interference, not the effect of a full rollout.
**Command:** `"$CLU/.venv-causal/bin/python" "$CLU/scripts/causal/cstat.py" abtest --data <file> --arm <col> --control <label> --y <col>`, after `cstat.py srm`.

## Difference-in-differences and event study (single adoption date)

**Assumption:** parallel trends. Absent treatment, treated units would have moved like the control units. Also no anticipation and no spillover to controls.
**Data needed:** a unit-by-period panel with three or more pre-periods.
**Diagnostics:** event-study leads near zero (the toolkit reports each lead in `pre_trend_leads` and a joint Wald test of all leads in `pre_trend_joint`, which fails the run when p < 0.05), a placebo date (`--placebo-shift`), a placebo outcome, and HonestDiD (`--honest-m`) to show how large a trend violation would overturn the result. A passed pre-trend test does not prove parallel trends, and pre-testing distorts inference (https://www.aeaweb.org/articles?id=10.1257/aeri.20210236, https://arxiv.org/abs/2007.01616).
**Fails when:** treated units were picked because of recent trends (Ashenfelter dip, regression to the mean); a concurrent campaign hits only the treated group; levels versus logs give different answers; standard errors are not clustered (https://academic.oup.com/qje/article/119/1/249/1876068). With few treated clusters, such as three categories, cluster-robust CIs are too narrow; use a bootstrap or randomisation inference (https://doi.org/10.1162/rest.90.3.414). The toolkit's `treated_clusters` diagnostic warns below 20 treated clusters. Two routes are in the toolkit: add `--n-bootstrap 999` to `cstat.py did` (a multiplier bootstrap on the diff-diff estimators, not a wild cluster bootstrap), or build one row per cluster with its post-period mean minus its pre-period mean and run `cstat.py permute --data <file> --cluster <col> --arm <treated 0/1 col> --y <change col>`, which re-assigns treatment across clusters and gives an exact or simulated p-value.
**Estimand:** the effect on the treated units (ATT).
**Command:** `cstat.py did --data <file> --y <col> --unit <col> --time <col> --first-treat <col> --cluster <assignment-level column> --placebo-shift 1 --honest-m 1` (with the same prefix as above). Always cluster at the level where treatment was assigned; without `--cluster` the toolkit clusters on `--unit`, which is wrong when assignment is coarser than the unit.

## Staggered adoption DiD

Use this when categories, sellers or regions got the change on different dates. Plain two-way fixed effects uses already-treated units as controls and can return a wrong-signed estimate when effects differ across cohorts or grow over time. That is why the rule is never plain TWFE (Goodman-Bacon https://doi.org/10.1016/j.jeconom.2021.03.014; negative weights https://www.aeaweb.org/articles?id=10.1257/aer.20181169).

The estimators to use build cohort-by-time effects and use clean controls: Callaway and Sant'Anna (https://psantanna.com/files/Callaway_SantAnna_2020.pdf), Sun-Abraham (https://doi.org/10.1016/j.jeconom.2020.09.006), and the imputation estimator (https://arxiv.org/abs/2108.12419).

**Assumption:** parallel trends between cohorts and their clean controls, no anticipation. A rollout ordered by "the category was ready" or "had high traffic" violates it, so ask how the order was chosen.
**Data needed:** a panel with the first treatment date per unit, and never-treated or not-yet-treated units. A rollout that ended with everyone treated still works with not-yet-treated controls, but only for the periods before the last cohort adopts.
**Diagnostics:** event-study leads by cohort, cohort-specific effects (not only one average; `extra.cohort_effects` for `--method cs`), the joint pre-trend test, HonestDiD, and `treated_clusters`.
**Fails when:** no clean controls remain; cohorts are tiny; treatment switches on and off.
**Estimand:** the average effect on treated cohorts, by event time.
**Command:** `cstat.py did --data <file> --y <col> --unit <col> --time <col> --first-treat <col> --method cs --control notyet --cluster <col>`. The toolkit uses Callaway-Sant'Anna with a universal base and redirects TWFE requests. `--method sa` and `--method imputation` are the cross-checks.

## Synthetic control and synthetic DiD

Use when one or a few units are treated (a category, a region, a seller tier) and many donors exist.

**Assumption:** a weighted mix of donors reproduces the treated unit's pre-period path and would have kept doing so. Donors are not affected by the treatment, and no shock hits only the treated unit after launch (https://doi.org/10.1198/jasa.2009.ap08746, https://www.aeaweb.org/articles?id=10.1257/jel.20191450).
**Data needed:** a long pre-period, a donor pool, one outcome per unit-period.
**Diagnostics:** pre-period fit (do not proceed if it is poor; `pre_period_fit` for both methods), placebo runs on every donor (`sc`), weight concentration (`donor_weight_concentration` for `sdid`, `top_donor_weights` for both), and leave-one-out donors (manual step, not in the toolkit: re-run with `--donors` minus the top-weighted donor).
**Fails when:** poor fit, extrapolation, donors contaminated by cannibalisation between categories, a short pre-period.
**Estimand:** the effect on the treated unit or units after launch.
**Command:** `cstat.py synth --data <file> --unit <col> --time <col> --y <col> --treated-unit <id> --start <period> --method sdid`. The `sc` method gives a placebo p-value and no interval, so its verdict stays inconclusive; use `sdid` for a CI (https://arxiv.org/abs/1812.09970).

## Regression discontinuity (sharp and fuzzy)

Use when eligibility depends on a running variable crossing a cutoff: seller score, order value for free shipping, a minimum discount to join a campaign.

**Assumption:** potential outcomes are continuous at the cutoff, and units cannot precisely manipulate the running variable (https://arxiv.org/abs/1911.09511).
**Data needed:** many units near the cutoff. Use local linear fits with a data-driven bandwidth and robust bias-corrected intervals (rdrobust, https://doi.org/10.3982/ECTA11757). Avoid high-order global polynomials.
**Diagnostics:** the rddensity test for manipulation (https://doi.org/10.1080/01621459.2019.1635480), covariate balance at the cutoff, placebo cutoffs (`--placebo-cutoffs`), bandwidth sensitivity, and a donut that drops units near the cutoff (manual step, not in the toolkit: filter the data and re-run `cstat.py rdd`). Check that no other rule switches at the same cutoff.
**Fails when:** the threshold is known and gamed (round-number prices, free-shipping minimums); several treatments share one cutoff; too few points near the cutoff.
**Estimand:** the effect at the cutoff only, not for units far from it. For a fuzzy design it is the effect for units moved by crossing the cutoff.
**Command:** `cstat.py rdd --data <file> --x <col> --y <col> --cutoff <value> [--fuzzy <col>] --placebo-cutoffs ...`. Fuzzy runs report the first-stage strength; a weak jump in take-up means the design fails.

## Bunching

Use when a kink or notch makes agents pile up at a point (discount caps, commission tier boundaries, free-shipping thresholds). The excess mass against a counterfactual density recovers a behavioural elasticity (https://www.annualreviews.org/doi/10.1146/annurev-economics-080315-015234).

**Assumption:** a smooth counterfactual density and agents who can adjust.
**Data needed:** the running variable (price, discount depth, order value) for many units, before and after the policy.
**Diagnostics:** excluded-window and polynomial-order sensitivity, bootstrap errors, placebo thresholds, the density before the policy.
**Fails when:** sellers like round prices anyway.
**Estimand:** a behavioural elasticity among the units that respond.
**Command:** none. Toolkit support is deferred; there is no mature package, so any bunching estimate is bespoke and must be disclosed as such.

## Instrument and encouragement designs

**Assumption:** relevance (a strong first stage), exclusion (the instrument affects the outcome only through treatment), and monotonicity (no defiers). Examples: a randomly sent nudge to adopt a feature, random assignment to reviewers of differing strictness, a fault that changed the treatment alone, such as a discount silently not applied (https://www.jstor.org/stable/2291629). Most outages hit outcomes directly (a payment outage cuts orders), which breaks exclusion; treat those as a shock for interrupted time series or DiD, not as an instrument. Argue the exclusion restriction explicitly.
**Data needed:** the instrument, the treatment actually received, the outcome.
**Diagnostics:** the effective first-stage F rather than the old F above 10 rule (https://www.aeaweb.org/articles?id=10.1257/aer.20211063), balance of pre-treatment covariates across instrument values, Anderson-Rubin confidence sets, and a reduced-form plot.
**Fails when:** the nudge changes behaviour directly, or the first stage is weak.
**Estimand:** the effect for compliers, meaning the units whose treatment changed because of the instrument. It is not the effect on everyone; say "the effect on sellers who adopted because of the nudge".
**Command:** no toolkit command; use pyfixest (IV syntax) in `.venv-causal`, and report the first stage next to the estimate.

## Interrupted time series

**Assumption:** without the launch, the pre-period pattern (trend, seasonality, relationships with control series) would have continued, control series are unaffected, and nothing else changed at that date. Every concurrent campaign threatens it.
**Data needed:** a long regular series with many points before and after the start, ideally with control series.
**Diagnostics:** an in-time placebo (pretend the launch came earlier), residual autocorrelation, stability of the controls, sensitivity to the control set. Errors must allow for autocorrelation (HAC). Seasonality matters: holidays that follow a lunar or other non-Gregorian calendar move against the Gregorian calendar (Ramadan by about 11 days a year), while others are fixed on their own calendar. Sources: https://academic.oup.com/ije/article/46/1/348/2622842, https://research.google/pubs/inferring-causal-impact-using-bayesian-structural-time-series-models/.
**Fails when:** a concurrent shock, a long post-period where forecast intervals explode, or controls that respond to the treatment.
**Estimand:** the change in level or slope against the projected path, for the whole treated series.
**Command:** `cstat.py its --data <file> --time <col> --y <col> --start <date> [--period 7] [--controls ...]`.

## Matching, IPW and doubly robust

**Assumption:** all confounders are observed (conditional ignorability) and treated and untreated units overlap. This cannot be tested from the data, so it is the last resort.
**Data needed:** treatment status, outcome, and pre-treatment covariates that drive both.
**Diagnostics:** standardised mean differences below 0.1 after weighting, overlap plots, effective sample size, placebo outcomes, negative controls, and sensitivity via `cstat.py evalue --rr <x> --ci-low <x> --ci-high <x>` (https://doi.org/10.7326/M16-2607). Prefer doubly robust estimators, do not match on the propensity score itself (https://gking.harvard.edu/files/gking/files/psnot.pdf), and never adjust for a post-treatment variable or a collider.
**Fails when:** an unmeasured confounder exists, such as seller quality driving both campaign entry and sales.
**Estimand:** the effect on the treated (or on everyone, if stated) among the overlap population.
**Command:** no toolkit command beyond `evalue`; fit the weights and outcome models with statsmodels in `.venv-causal` and disclose the choice. DoubleML is not installed.

## Panel fixed effects

**Assumption:** no time-varying confounders and strict exogeneity. Past outcomes must not drive treatment, which fails when sellers cut prices after sales drop (https://doi.org/10.1111/ajps.12417).
**Data needed:** a unit-by-period panel where treatment varies within units over time.
**Diagnostics:** lead terms, a feedback check, clustering at the assignment level (https://doi.org/10.1093/qje/qjac038).
**Fails when:** treatment timing is staggered (use the staggered DiD estimators), or a lagged outcome sits in the model.
**Estimand:** a weighted average of within-unit effects, which is hard to interpret; prefer DiD when treatment has a date.
**Command:** no toolkit command; use pyfixest `feols` in `.venv-causal`.
