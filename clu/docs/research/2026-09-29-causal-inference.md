# Causal inference for an AI data-analyst agent in a marketplace: research notes

Date: 2026-09-29. Package versions and Python support were checked on PyPI on this date. Method claims come from primary papers and textbooks; URLs are given per claim. Items marked (unverified) come from my own knowledge and were not re-fetched.

Free textbooks the agent can point users to:
- Cunningham, Causal Inference: The Mixtape: https://mixtape.scunning.com/
- Huntington-Klein, The Effect: https://theeffectbook.net/
- Facure, Causal Inference for the Brave and True (Python): https://matheusfacure.github.io/python-causality-handbook/
- Roth, Sant'Anna, Bilinski, Poe, "What's trending in difference-in-differences" (modern DiD survey): https://arxiv.org/abs/2201.01194
- de Chaisemartin & D'Haultfoeuille, TWFE/DiD survey: https://arxiv.org/abs/2112.04565

---
## 0. Ground rules for the agent (short version)

1. Ask first how treatment was assigned. The assignment mechanism decides the method, not the data shape.
2. Prefer designs where the identifying assumption can be partly tested (pre-trends, density at cutoff, placebo units) over designs where it cannot (selection-on-observables).
3. Every estimate ships with: an estimand in plain words, the assumption, at least one falsification test, a confidence interval, and an MDE-style statement of what effect size could have been detected.
4. Say "we cannot tell" when diagnostics fail. A failed pre-trend test or an overlapping campaign is a finding, not an inconvenience.

---
## 1. Method-selection guide

### 1.1 Decision tree (what the agent should ask)

- Was exposure randomised (including hash/holdout, odd/even day)? Use RCT analysis (1.2).
- Is there a sharp rule: score/threshold/date that decides who is treated? Use RD / RKD / bunching (1.8-1.10) or regression discontinuity in time.
- Are there treated and never-/not-yet-treated units observed before and after? Use DiD/event study (1.3-1.4); with one or a few treated units and many donors, synthetic control/SDiD (1.5).
- Only one treated unit (whole site) and a long pre-period, no control units? Interrupted time series / BSTS with control series (1.7). Weakest design; say so.
- Is there a factor that nudges treatment but does not affect the outcome directly (random encouragement, judge/agent assignment, outage)? IV (1.6).
- Nothing but observed covariates? Matching/IPW/DR/DML (1.11-1.12), with mandatory sensitivity analysis (section 4).
- Treatment intensity varies (discount depth, days exposed)? Continuous/dose-response DiD, RKD; warn about stronger assumptions (Callaway, Goodman-Bacon, Sant'Anna: https://arxiv.org/abs/2107.02637).

### 1.2 RCT / A/B test
- Assumption: random assignment, SUTVA (no interference), no differential attrition. In a marketplace, interference is real: treated sellers' price cuts pull demand from control sellers. Use cluster randomisation, switchbacks, or geo/category-level tests. DoorDash on switchbacks: https://careersatdoordash.com/blog/switchback-tests-and-randomized-experimentation-under-network-effects-at-doordash/ (unverified path).
- Diagnostics: sample-ratio mismatch (SRM) test, pre-period balance (A/A), CUPED variance reduction (Deng et al.: https://exp-platform.com/Documents/2013-02-CUPED-ImprovingSensitivityOfControlledExperiments.pdf).
- Failure modes: peeking, novelty effects, interference, ratio-metric variance done wrong (see 3.9).
- Reference: Kohavi, Tang, Xu, Trustworthy Online Controlled Experiments: https://experimentguide.com/ ; Booking.com's experience: https://arxiv.org/abs/1710.08217 (Kaufman et al.).
- Python: scipy, statsmodels (ttest, proportions, power), CausalPy for Bayesian A/B.

### 1.3 Difference-in-differences (DiD), single adoption date
- Assumption: parallel trends in untreated potential outcomes (treated group would have moved like control absent treatment), no anticipation, no spillover to controls, stable composition.
- Data: unit x period panel (or repeated cross-sections), multiple pre-periods (3+ preferred).
- Diagnostics: event-study plot with leads near zero; placebo treatment date; placebo outcome unaffected by the feature; compare covariate trends. Caveat: passing a pre-trend test does not prove parallel trends, and pretesting distorts inference (Roth 2022: https://www.aeaweb.org/articles?id=10.1257/aeri.20210236). Prefer sensitivity analysis: HonestDiD (Rambachan & Roth: https://arxiv.org/abs/2007.01616) shows how large a trend violation would overturn the result.
- Failure modes: treated units chosen because of recent trends (Ashenfelter dip; regression to the mean), concurrent campaigns hitting only the treated group, outcomes on different scales (parallel in levels vs logs is functional-form dependent), standard errors not clustered (Bertrand-Duflo-Mullainathan: https://academic.oup.com/qje/article/119/1/249/1876068).
- With few treated clusters (e.g. 3 categories), conventional cluster SEs are badly wrong; use wild cluster bootstrap (Cameron, Gelbach, Miller: https://doi.org/10.1162/rest.90.3.414) or permutation/randomisation inference.
- Python: pyfixest (feols with unit/time FE and clustered SEs), statsmodels, diff-diff, differences.

### 1.4 Staggered adoption DiD and event studies
- Problem: with staggered rollout (by category/seller cohort) plain two-way fixed effects (TWFE) regression uses already-treated units as controls and can produce wrong-signed estimates under effect heterogeneity or dynamics. Goodman-Bacon decomposition: https://doi.org/10.1016/j.jeconom.2021.03.014 ; de Chaisemartin & D'Haultfoeuille (negative weights): https://www.aeaweb.org/articles?id=10.1257/aer.20181169 ; Sun & Abraham on event-study contamination: https://doi.org/10.1016/j.jeconom.2020.09.006.
- Modern estimators (all build cohort-by-time effects and use clean controls):
  - Callaway & Sant'Anna group-time ATT(g,t) with never-/not-yet-treated controls, optional covariates via doubly robust: https://psantanna.com/files/Callaway_SantAnna_2020.pdf
  - Sun & Abraham interaction-weighted (saturated event study): link above.
  - Borusyak, Jaravel, Spiess imputation: https://arxiv.org/abs/2108.12419 ; Gardner two-stage (did2s): https://arxiv.org/abs/2207.05943
  - de Chaisemartin & D'Haultfoeuille DID_M / DID_l (handles treatment switching on/off): link above.
  - Stacked DiD (Wing et al.) and local-projections DiD (Dube et al.) are simple robust alternatives.
- Assumption: parallel trends between cohorts and clean controls, no anticipation. Rollouts that were assigned because a category "was ready" or "had high traffic" violate it; ask how the order was chosen.
- Diagnostics: check for never-treated or not-yet-treated units; event-study leads by cohort; report cohort-specific effects, not only one average; HonestDiD.
- Common failure: treating a rollout that ended with everyone treated as "no controls". Not-yet-treated controls still work, but only for the periods before the last cohort adopts.
- Python: pyfixest (did2s, lpdid, saturated event_study; v0.60, supports 3.14; docs https://pyfixest.org/tutorials/difference-in-differences.html), diff-diff (CS, SA, imputation, stacked, SDiD, HonestDiD; v3.12, 3.14 ok; https://diff-diff.readthedocs.io/), differences (ATTgt CS; https://github.com/bernardodionisi/differences), csdid (port of R did; unmaintained-looking classifiers, unverified 3.14), moderndid (Python port of R DiD suite; declares 3.11-3.13, https://pypi.org/project/moderndid/).

### 1.5 Synthetic control and synthetic DiD
- Use when one or few units are treated (a category, a region, a seller tier) and many potential donors exist.
- Assumption: a weighted mix of donors reproduces the treated unit's pre-period path (and would have continued to), donors not affected by treatment, no big shocks specific to the treated unit after launch. Original: Abadie, Diamond, Hainmueller: https://doi.org/10.1198/jasa.2009.ap08746 ; practitioner guide: Abadie 2021, https://www.aeaweb.org/articles?id=10.1257/jel.20191450.
- Diagnostics: pre-period RMSPE (fit must be good; do not proceed if not), leave-one-out donor robustness, in-space placebos (apply to every donor and compare post/pre RMSPE ratio: permutation p-value), in-time placebos, weight concentration.
- Failure modes: poor pre-fit, extrapolation, donors contaminated by spillover (cannibalisation between categories), too-short pre-period, overfitting with many predictors.
- Synthetic DiD (Arkhangelsky et al.: https://arxiv.org/abs/1812.09970) reweights units and time, works with multiple treated units and is more robust than either SC or DiD alone. Augmented SC (Ben-Michael, Feller, Rothstein: https://arxiv.org/abs/1811.04170) corrects imperfect fit. Conformal inference for SC: https://arxiv.org/abs/1712.09089.
- Python: pysyncon (v1.7; classic, augmented, robust, penalised, placebo tests; https://sdfordham.github.io/pysyncon/), CausalPy (Bayesian SC; https://github.com/pymc-labs/CausalPy), diff-diff (SDiD), SyntheticControlMethods (older, unverified maintenance). Meta's GeoLift is R only: https://facebookincubator.github.io/GeoLift/

### 1.6 Instrumental variables (incl. judge and encouragement designs)
- Estimand: LATE for compliers (Imbens & Angrist 1994; Angrist-Imbens-Rubin 1996: https://www.jstor.org/stable/2291629). Not the effect on everyone.
- Assumptions: relevance (strong first stage), independence/exclusion (instrument affects outcome only through treatment), monotonicity (no defiers).
- Examples: random encouragement (email/banner nudge to adopt a feature), random assignment of tickets to reviewers/agents who differ in strictness (judge design; Frandsen, Lefgren, Leslie on testing exclusion and monotonicity: https://www.aeaweb.org/articles?id=10.1257/aer.20201860), outage or bug that randomly changes exposure.
- Diagnostics: first-stage F. The old "F>10" rule is not enough for valid inference; use effective F and the tF adjustment (Lee, McCrary, Moreira, Porter: https://www.aeaweb.org/articles?id=10.1257/aer.20211063 ); reduced-form plot; balance of pre-treatment covariates across instrument values; overidentification tests when several instruments exist; Anderson-Rubin confidence sets (robust to weak instruments).
- Failure modes: exclusion violation (nudge changes behaviour directly), weak first stage, many instruments, interpreting LATE as ATE.
- Python: linearmodels IV2SLS/IVGMM (v7.0; classifiers up to 3.13), pyfixest feols with IV syntax, statsmodels IV2SLS, DoubleML IV, EconML IV learners.

### 1.7 Interrupted time series, CausalImpact and BSTS
- Assumption: without launch, the pre-period pattern (trend, seasonality, plus covariate relationships) continues; controls series are unaffected by launch; no concurrent shocks. Every concurrent campaign is a threat.
- CausalImpact (Brodersen et al.): https://research.google/pubs/inferring-causal-impact-using-bayesian-structural-time-series-models/ . Uses control time series to build a counterfactual with credible intervals. Segmented-regression ITS primer: Bernal, Cummins, Gasparrini: https://academic.oup.com/ije/article/46/1/348/2622842. Regression discontinuity in time: Hausman & Rapson: https://www.annualreviews.org/doi/10.1146/annurev-resource-121517-033306
- Diagnostics: in-time placebo (pretend the launch was earlier and check no "effect"), holdout validation of pre-period forecast, residual autocorrelation (Durbin-Watson, Ljung-Box), control-series stability, sensitivity to control set.
- Failure modes: ignoring autocorrelation (SEs too small by a large factor), seasonality not modelled (lunar-calendar holidays such as Ramadan shift roughly 11 days a year), controls that respond to treatment, long post-periods where forecast intervals explode.
- Python: tfcausalimpact (v0.0.19; requires Python <3.14), pycausalimpact (abandoned, 2020), statsmodels UnobservedComponents / SARIMAX / Newey-West OLS (recommended fallback), CausalPy ITS and pre-post designs.

### 1.8 Regression discontinuity (sharp, fuzzy)
- Use when eligibility depends on a running variable crossing a cutoff (seller score, order value for free shipping, min stock, launch date by SKU-ID).
- Assumption: potential outcomes continuous at cutoff; units cannot precisely manipulate the running variable. Guide: Cattaneo, Idrobo, Titiunik, A Practical Introduction to RD: https://arxiv.org/abs/1911.09511. Fuzzy RD = RD in outcome divided by RD in treatment take-up (IV at cutoff; requires strong jump in take-up).
- Estimation: local linear with data-driven bandwidth and robust bias-corrected CIs (Calonico, Cattaneo, Titiunik: https://doi.org/10.3982/ECTA11757). Do not use high-order global polynomials (Gelman & Imbens: https://www.tandfonline.com/doi/full/10.1080/07350015.2017.1366909).
- Diagnostics: density test for manipulation (McCrary: https://doi.org/10.1016/j.jeconom.2007.05.005 ; rddensity local polynomial version, Cattaneo, Jansson, Ma: https://doi.org/10.1080/01621459.2019.1635480); covariate balance at cutoff (rdrobust with covariates as outcomes); placebo cutoffs; bandwidth sensitivity; donut RD dropping units near the cutoff; check that other rules do not also switch at the same cutoff.
- Failure modes: thresholds that people or sellers know about and game (round-number prices, "spend 50 dollars for free shipping"); multiple treatments at same cutoff; too few points near cutoff; effect only local (LATE at cutoff).
- Python: rdrobust (v2.1, updated 2026-09-27), rddensity (v3.0, 3.14 ok), rdpower, rdmulti (all https://rdpackages.github.io/), CausalPy RD.

### 1.9 Bunching estimators
- Use when a kink or notch in incentives makes agents pile up at a point (discount depth caps, price ending .999, commission tier boundaries, free-shipping thresholds). Bunching mass relative to a counterfactual density recovers behavioural elasticity. Review: Kleven: https://www.annualreviews.org/doi/10.1146/annurev-economics-080315-015234 ; Chetty et al.: https://doi.org/10.1093/qje/qjr041
- Assumptions: smooth counterfactual density, agents can adjust (optimisation frictions matter), no other bunching reasons.
- Diagnostics: excluded window sensitivity, polynomial order, bootstrap SE, placebo thresholds, pre-policy density.
- Failure modes: round-number bunching that exists without the policy (sellers like round prices), counterfactual mis-specified. Python: no mature package; implement with numpy/statsmodels and bootstrap (agent should write it and disclose that it is bespoke).

### 1.10 Regression kink design (RKD)
- Assumption: a policy changes slope (not level) of treatment vs running variable at a kink; smooth outcome slope absent policy. Card, Lee, Pei, Weber: https://www.econometricsociety.org/publications/econometrica/2015/11/01/inference-regression-kink-design
- Diagnostics: kink in treatment is visible; no kink in covariates; placebo kinks. Data hungry, noisy: need large n near kink. Python: rdrobust with deriv=1 option.

### 1.11 Matching, propensity scores, IPW, doubly robust
- Assumption: conditional ignorability (all confounders observed) and overlap. This is the weakest design and cannot be tested from data.
- Diagnostics: covariate balance (standardised mean differences <0.1 after weighting), overlap plots of propensity, effective sample size after weights, trimming (Crump et al.: https://doi.org/10.1093/biomet/asn055), placebo outcomes, negative controls, sensitivity analysis (section 4).
- Prefer doubly robust (AIPW) estimators. Do not match on the propensity score itself (King & Nielsen: https://gking.harvard.edu/files/gking/files/psnot.pdf) ; use weighting or covariate matching. Stuart review: https://doi.org/10.1214/09-STS313.
- Never adjust for post-treatment variables or colliders (section 4.1).
- For DiD with covariates use the doubly robust DiD of Sant'Anna & Zhao: https://doi.org/10.1016/j.jeconom.2020.06.003.
- Python: DoWhy (v0.14; requires <3.14), EconML, DoubleML, statsmodels (logit, GLM), causalml (unverified), zepid (stale).

### 1.12 Double/debiased ML (DML) and heterogeneous effects
- Use for high-dimensional confounding under ignorability, or to estimate CATE (which segment benefits). Chernozhukov et al.: https://arxiv.org/abs/1608.00060. Assumptions are the same as 1.11; ML only handles functional form. It does not fix unobserved confounding.
- Diagnostics: cross-fitting, learner sensitivity, overlap, comparison with simple regression, placebo treatment. For heterogeneity use out-of-sample calibration and multiple-testing control; subgroup mining without pre-specification is forking paths (section 3.7).
- Python: DoubleML (v0.11; https://docs.doubleml.org/), EconML (v0.17, 3.14 ok; https://www.pywhy.org/EconML/).

### 1.13 Panel fixed effects
- Removes time-invariant confounders (seller, product); assumes no time-varying confounders and strict exogeneity (past outcomes must not drive treatment, e.g. sellers cut price after sales drop). Failure: feedback (reverse causality), TWFE bias with staggered timing (1.4), Nickell bias with lagged DV. Imai & Kim on what TWFE assumes: https://doi.org/10.1111/ajps.12417. Cluster at treatment-assignment level (Abadie, Athey, Imbens, Wooldridge: https://doi.org/10.1093/qje/qjac038).
- Python: pyfixest (fast, multiple high-dimensional FE, wild bootstrap, HC/CRV SEs), linearmodels PanelOLS.

### 1.14 Quick reference table

| Design | Key assumption | First falsification test | Package |
|---|---|---|---|
| RCT | random assignment, no interference | SRM, A/A balance | scipy, statsmodels |
| DiD | parallel trends | event-study leads, HonestDiD | pyfixest, diff-diff |
| Staggered DiD | parallel trends w/ clean controls | cohort-level leads, Bacon decomposition | pyfixest, diff-diff, differences |
| Synthetic control | pre-period fit | RMSPE, placebo-in-space | pysyncon, CausalPy |
| RD | continuity, no sorting | rddensity, covariate balance | rdrobust, rddensity |
| IV | exclusion, monotonicity | first-stage effective F | linearmodels, pyfixest |
| ITS/BSTS | no concurrent shock | placebo date, holdout forecast | statsmodels, CausalPy |
| Matching/IPW/DR | no unmeasured confounding | balance, negative controls, E-value | DoWhy, EconML |
| DML | same as DR | learner sensitivity, overlap | DoubleML, EconML |
| Panel FE | no time-varying confounders | leads/feedback tests | pyfixest, linearmodels |
| RKD | slope smoothness | placebo kinks | rdrobust (deriv=1) |
| Bunching | smooth counterfactual density | placebo thresholds | bespoke |

---
## 2. Natural experiments in e-commerce and marketplaces

### 2.1 Catalogue (what the agent should probe the user about)
1. Thresholds and eligibility cutoffs: free-shipping minimum, seller score for a badge, minimum discount to join a campaign, GMV tiers for commission, product rating cutoff for ranking boost. Use RD (1.8) and bunching (1.9). Ask: is the score visible to sellers (manipulation)? Is the rule applied mechanically?
2. Staggered rollouts by category/seller cohort/region: staggered DiD (1.4). Ask who picked the order and why; ask for a rollout log with dates per unit (from feature flags, config tables or deployment history).
3. Outages, bugs, misconfigurations: sudden as-if random exposure changes (a discount not applied to a subset, a search ranking bug, a payment gateway outage). Often the cleanest source of exogenous variation; treat with DiD/ITS/IV. Ask the on-call/incident log.
4. Policy changes and external shocks: commission changes, exchange-rate jumps, holiday calendar moves. Usually affect all units: use time-varying exposure intensity (some categories were more exposed) as DiD "dose".
5. Capacity limits and rationing: warehouse or delivery slot caps; ordering by arrival time gives quasi-random allocation near capacity.
6. As-if random assignment: odd/even days or IDs, hash-based bucket assignment, alphabetical batches, lotteries for campaign slots, queue positions. Test balance carefully; deterministic ID rules can correlate with age of listing.
7. Price rounding and left-digit effects: prices just below/above round numbers are close to identical in value but differ in perceived price. Strulov-Shlain (left-digit bias in scanner data): https://doi.org/10.1287/mnsc.2022.4410 ; Lacetera, Pope, Sydnor (odometer thresholds, RD): https://www.aeaweb.org/articles?id=10.1257/aer.102.5.2206
8. Geographic/time-block rollouts: geo experiments, DMA holdouts, switchbacks.
9. Algorithmic cutoffs: rank position boundaries (e.g. page 1 vs page 2 on search) exploited with RD on rank score.
10. Encouragement: emails/push notifications randomly sent (IV).

### 2.2 Published examples
- eBay, search-ad effectiveness: Blake, Nosko, Tadelis ran a geographic holdout (turn off brand-keyword ads in a random set of DMAs) and found brand search ads had little return: https://doi.org/10.3982/ECTA12423 . Lesson: correlational ROI overstates effect because of selection; geo holdouts are the fix.
- eBay, seller-side experiments: Einav et al., learning from seller experiments (posted price/auction choices): https://www.nber.org/papers/w19538 (unverified id). eBay seller reputation and market design surveyed in Tadelis, "Reputation and feedback systems in online platform markets": https://www.annualreviews.org/doi/10.1146/annurev-economics-080315-015325
- Uber, surge pricing: Cohen, Hahn, Hall, Levitt, Metcalfe used regression discontinuity around surge multiplier thresholds to estimate demand elasticity and consumer surplus: https://www.nber.org/papers/w22627 . Lesson: algorithmic price step rules create RD for free.
- Airbnb, artificial counterfactuals: "ACE" uses ML-based counterfactuals to measure launches that cannot be A/B tested: https://medium.com/airbnb-engineering/artificial-counterfactual-estimation-ace-machine-learning-based-causal-inference-at-airbnb-ee32ee4d0512 (unverified path). Airbnb also documented when to use quasi-experiments and difference-in-differences over holdouts in its experimentation series (search "Airbnb engineering quasi experiment").
- Netflix: quasi-experimentation series on DiD, synthetic control and RD for non-testable changes: https://netflixtechblog.com/quasi-experimentation-at-netflix-566b57d2e362 (unverified path)
- Booking.com: experimentation at scale and pitfalls, useful for guardrails and false-positive control: https://arxiv.org/abs/1710.08217
- Amazon, Instacart, Alibaba: I did not find a well-documented public quasi-experimental study with a stable primary-source URL in this session. The agent should not cite them from memory. Meta's GeoLift and Google's causal-impact tooling show the same geo-holdout pattern.
- DoorDash: switchback tests for marketplace interference (URL in 1.2).

### 2.3 How to spot natural experiments during a conversation
Ask the user in this order: (a) Was there a rule that decided who got it? (b) Was rollout staggered, and who chose the order? (c) Were any units eligible but not treated, and why? (d) Was there a bug, outage or config error that changed exposure? (e) What else changed at the same time (campaigns, price moves, ranking releases)? (f) Can units opt in (selection)? Then check data: plot exposure by date and unit, look for mass at thresholds, look for calendar irregularities.

### 2.4 Marketplace cautions (domain notes, not from sources)
- Concurrent campaigns and site-wide sale events (e.g. seasonal sales, Black Friday-style events) overlap most launches. Include event-by-category controls or exclude event windows; a DiD comparing categories is only clean if events hit both equally.
- Lunar and solar calendars: Ramadan and Muharram shift about 11 days per Gregorian year; other holidays follow a local calendar such as the Jalali one. Build week-of-month (Jalali), holiday and local-weekend features; do not use Gregorian month-end assumptions. pandas has no native Jalali support; use jdatetime or persiantools for date parts.
- Currency depreciation and price-inflation regimes make level comparisons across time unreliable; prefer within-period comparisons, log outcomes, or real-price deflating.
- Cannibalisation/substitution across sellers and categories violates SUTVA; measure at the level where substitution happens (category or query), not the SKU.

---
## 3. Statistical-meaningfulness checklist

### 3.1 Power and MDE
- Before analysis, compute the minimum detectable effect given historical variance and sample size, and tell the user "an effect smaller than X% would not be visible." Post-hoc "observed power" is misleading; report MDE and CI instead. Guide: Kohavi et al. (link in 1.2); Bloom MDE: https://doi.org/10.1177/0193841X9501900504 (unverified). Python: statsmodels.stats.power (TTestIndPower, NormalIndPower).
- Quasi-experiments have lower power than RCTs; DiD variance is dominated by the number of treated clusters, not rows.
- Type-M / Type-S errors: significant results from underpowered studies exaggerate effect size and can get the sign wrong (Gelman & Carlin: https://doi.org/10.1177/1745691614551642).

### 3.2 Practical vs statistical significance
Convert to business units (currency, orders, %). Report point estimate with 95% CI and compare the CI to the smallest effect that would matter (equivalence/non-inferiority via TOST if the goal is "no harm"). A tiny p-value on millions of rows can be irrelevant; a wide CI that includes both zero and a large effect is "inconclusive", not "no effect".

### 3.3 Standard errors
- Cluster at the level of treatment assignment (category, seller, day-block), not the observation (Abadie et al., link in 1.13; Cameron & Miller practitioner guide: https://jhr.uwpress.org/content/50/2/317). Few clusters (<30-40): wild cluster bootstrap or randomisation inference (pyfixest supports wildboottest).
- Time series: autocorrelation makes naive SEs far too small. Use HAC (Newey-West), block bootstrap, or clustering by unit in panels; collapse pre/post to two periods as a check (Bertrand-Duflo-Mullainathan, link in 1.3).

### 3.4 Multiple comparisons
Testing many metrics, segments, or time windows inflates false positives. Pre-specify one primary metric; control FDR (Benjamini-Hochberg: https://doi.org/10.1111/j.2517-6161.1995.tb02031.x) or Holm for the family; label the rest exploratory. statsmodels.stats.multitest.multipletests.

### 3.5 Forking paths and specification curves
Analyst choices (window, outcome transform, controls, outlier rule) multiply. Gelman & Loken: http://www.stat.columbia.edu/~gelman/research/unpublished/forking.pdf . Show a specification curve (Simonsohn, Simmons, Nelson: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2694998): run all defensible specifications and plot estimates. The agent should log every specification tried.

### 3.6 Regression to the mean, Simpson's paradox, survivorship, selection on outcome
- Regression to the mean: units chosen for treatment because of extreme recent performance (worst sellers, lowest-conversion categories) bounce back without treatment. Fix: choose controls with the same selection rule; use pre-periods that span the selection window; use RD or randomisation.
- Simpson's paradox: aggregate and subgroup trends differ due to mix shift. Always compare like with like (stratify or model composition); check whether treated mix changed (e.g. a discount attracting low-basket customers).
- Survivorship/attrition: analysing only sellers/SKUs that still exist or still list. If treatment changes who survives, effects are biased; use intent-to-treat on the original cohort, or Lee bounds: https://doi.org/10.1111/j.1467-937X.2009.00536.x
- Conditioning on outcome or a post-treatment variable (buyers only, converted sessions only) creates collider bias (4.1). Analyse at the level exposed (all users shown the feature), not those who reacted.

### 3.7 Heterogeneity
Only pre-specified subgroups are confirmatory. Data-driven CATE needs cross-fitting and honest validation (EconML/DoubleML). Multiple-testing correction applies across subgroups.

### 3.8 Resampling
Bootstrap: resample at cluster level, not row level. Permutation/randomisation inference: reassign treatment labels within the same assignment scheme (placebo permutations for SC and DiD with few treated units). scipy.stats.bootstrap and scipy.stats.permutation_test are available in scipy 1.17.

### 3.9 Heavy-tailed revenue and ratio metrics
- Revenue per user is heavy-tailed; a few whales dominate variance. Options: winsorise at a pre-declared percentile (report both raw and capped), trim, use quantile effects, or model conversion x (order value | order). Predeclare the choice; choosing after seeing results is forking paths. Kohavi book (1.2) covers outlier handling.
- log(y) with zeros: the "log+1" and arcsinh transformations give effects that depend on the unit of measure; use Poisson PPML (pyfixest fepois) or an extensive/intensive margin split. Chen & Roth: https://doi.org/10.1093/qje/qjad054
- Ratio metrics (revenue/order, conversion = orders/sessions) with unit of analysis different from unit of randomisation: use the delta method or linearisation (Deng, Knoblich, Lu: https://alexdeng.github.io/public/files/kdd2018-dm.pdf ), or bootstrap by cluster. Naive per-row t-tests understate variance.
- Variance reduction: CUPED / regression adjustment with pre-period covariate (Deng et al., link in 1.2).

### 3.10 Other pitfalls
- Peeking/sequential looks at accumulating data: use sequential methods or fixed horizon.
- Spillovers: check whether control units' outcomes moved after launch.
- Composition changes and data definition changes at the same time as launch (logging changes).
- Outcome and treatment measured from the same noisy source (mechanical correlation).
- Document all data exclusions and why.

---
## 4. Causal-thinking tools for brainstorming

### 4.1 DAGs
- Draw the DAG with the user before any modelling: treatment, outcome, confounders (cause both), mediators (on the causal path), colliders (caused by both treatment and outcome or their causes).
- Backdoor criterion: adjust for a set that blocks all backdoor paths and contains no descendants of treatment. Control for confounders; do not control for mediators when you want the total effect; never control for colliders (opens a spurious path). "Crash course in good and bad controls": Cinelli, Forney, Pearl: https://ftp.cs.ucla.edu/pub/stat_ser/r493.pdf (unverified host; also DOI 10.1177/00491241221099552). Tools: dagitty (web, https://www.dagitty.net/), DoWhy graph API, networkx.
- Marketplace examples: price cut -> demand <- competitor price; campaign membership <- seller quality -> sales (confounder); "buyer placed order" as a filter after treatment (collider/selection).
- Baseline text: Hernán & Robins, Causal Inference: What If (free): https://miguelhernan.org/whatifbook

### 4.2 Potential outcomes
State each estimand as ATE, ATT (effect on those treated), LATE (compliers) or CATE. Different designs identify different ones: DiD identifies ATT; RD the effect at the cutoff; IV the effect on compliers. Rubin/Imbens & Rubin textbook: https://doi.org/10.1017/CBO9781139025751. Make the agent state the estimand in the user's words ("effect on sellers who joined, not on all sellers").

### 4.3 Target trial framing
Write the ideal randomised experiment first: eligibility, treatment strategies, assignment, time zero, follow-up, outcome, contrast. Then say how the observational data emulate it and where they cannot (time zero misalignment, immortal-time bias). Hernán & Robins: https://academic.oup.com/aje/article/183/8/758/1739860 (and the book above).

### 4.4 Pre-registration and analysis plan
Before touching outcome data, write: hypothesis, estimand, unit, treatment definition and dates, primary metric and transformation, sample and exclusions, method and controls, SE clustering, planned diagnostics and what results would falsify the design, MDE, subgroups, and multiple-testing plan. Save it (timestamped) with the run. Deviations are logged, not hidden. Template ideas: Huntington-Klein book ch. on research design (link in intro), OSF preregistration: https://osf.io/prereg/.

### 4.5 Sensitivity analysis
- E-value: minimum strength of an unmeasured confounder (risk ratio scale) that would explain away the effect: VanderWeele & Ding: https://doi.org/10.7326/M16-2607 ; calculator https://www.evalue-calculator.com/.
- Oster's delta and R-max bounds: how much selection on unobservables, relative to observables, would drive the coefficient to zero: https://doi.org/10.1080/07350015.2016.1227711 . Rule of thumb Rmax = 1.3 * R-squared of the controlled model.
- Cinelli & Hazlett robustness value / sensemakr for regression: https://doi.org/10.1111/rssb.12348 ; Python port sensemakr (exists on PyPI; version and 3.14 support not verified).
- Rosenbaum bounds for matched designs (how large a hidden bias Gamma changes the p-value): Rosenbaum, Observational Studies, https://doi.org/10.1007/978-1-4757-3692-2 
- DiD: HonestDiD (1.3). Synthetic control: placebo distribution and leave-one-out. RD: bandwidth and donut. IV: Anderson-Rubin sets and plausibly-exogenous bounds (Conley et al.: https://doi.org/10.1162/REST_a_00139).
- DoWhy refutation tests (placebo treatment, random common cause, data subset): https://www.pywhy.org/dowhy/

---
## 5. Package and environment notes (checked on PyPI 2026-09-29)

| Package | Version | Python support | Note |
|---|---|---|---|
| pyfixest | 0.60.0 | 3.10+, tags 3.12-3.14 | FE, IV, did2s, lpdid, saturated event study, wild bootstrap |
| diff-diff | 3.12.0 | 3.9 to <3.15 | CS, SA, imputation, stacked, SDiD, HonestDiD; young project, validate against pyfixest |
| differences | 0.3.0 | 3.10+, tags to 3.14 | Callaway-Sant'Anna ATTgt |
| moderndid | 0.2.0 | 3.11-3.13 tags | port of R DiD ecosystem; early |
| csdid | 0.4.2 | no metadata | older port; skip unless needed |
| linearmodels | 7.0 | tags to 3.13 | IV, panel; pure Python + C extensions, 3.14 wheels unverified |
| rdrobust | 2.1.0 | >=3.9 | official Cattaneo et al. |
| rddensity | 3.0 | tags to 3.14 | manipulation test |
| rdpower / rdmulti | see PyPI | | power for RD, multi-cutoff |
| pysyncon | 1.7.0 | >=3.8, no tags | pure Python; 3.14 probably fine |
| causalpy | 0.9.0 | tags to 3.14 | Bayesian; heavy PyMC dependency |
| econml | 0.17.0 | tags to 3.14 | CATE, DML, IV |
| doubleml | 0.11.4 | tags to 3.13 | DML |
| dowhy | 0.14 | <3.14 | refuters, graph; no 3.14 |
| tfcausalimpact | 0.0.19 | <3.14 | pulls TensorFlow Probability; avoid on 3.14 |
| pycausalimpact | 0.1.1 | abandoned (2020) | do not use |
| statsmodels | 0.15.0 | tags to 3.14 | UnobservedComponents, power, multitest, IV2SLS |

Recommended install set for Python 3.14: pyfixest, diff-diff (or differences), rdrobust, rddensity, rdpower, pysyncon, econml, doubleml if ignorability designs are needed, causalpy if Bayesian ITS/SC wanted. On 3.14, replace tfcausalimpact by statsmodels UnobservedComponents or CausalPy; replace DoWhy by hand-written DAG checks (networkx) unless a 3.13 environment is available.
