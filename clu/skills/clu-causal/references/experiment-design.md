# Designing experiments in a two-sided marketplace

A marketplace is a two-sided market, so a change to prices, promotions or seller tools rarely touches one unit in isolation. This guide is about choosing the randomisation unit, the design and the run length before launch. Sources: `docs/research/2026-09-29-experimentation-platforms.md`. Where a claim came from a search snippet rather than a page read in full, the research note marks it; those claims are marked "(snippet)" here.

## First question: what do treated and control units compete for?

Standard A/B tests assume one unit's treatment does not change another unit's outcome. In a marketplace treated and control units share the same demand, stock or budget, so there is interference. Airbnb's simulation found that forcing treated hosts to cut prices raised their bookings and lowered control hosts' bookings, so the naive difference overstated the effect of a full rollout, by up to a factor of two in the simulated case (https://arxiv.org/html/2004.12162v1). A live pricing meta-experiment confirmed the bias exists (https://pubsonline.informs.org/doi/10.1287/mnsc.2020.01157).

Ask, in this order: what is scarce (buyer attention, stock, a campaign budget, a daily-deal slot)? Who competes for it (products in one category, sellers of the same product, buyers in one city)? Would a treated unit's gain come from a control unit's loss? If yes, a user-level split will overstate a full rollout. Report the naive estimate as a likely upper bound and say which design could tighten it.

## Choosing the unit

Randomise on the scarce side. Johari, Li, Liskovich and Weintraub show that in demand-constrained markets, randomising customers is close to unbiased and randomising listings is biased; in supply-constrained markets the reverse holds; in balanced markets both are biased (https://arxiv.org/abs/2002.05670). For a typical marketplace, a flash sale with limited stock is supply-constrained, while a seller-panel feature in a supply-rich category behaves differently.

- **Buyer:** for buyer-side features where sellers do not react and stock is not binding (a banner, a search layout).
- **Seller:** for seller tools whose effect stays inside the seller's own sales. Treated and control sellers still compete for the same buyers, so read the category total too.
- **Product group or category:** for price and promotion tests, because substitution happens here. Randomising buyers to see different prices also hides how sellers react.
- **Time block:** for platform-wide parameters (see switchback).

Two-sided randomisation (randomise buyers and sellers independently, measure at the pair level) gives direct and spillover effects, but needs many of both sides (https://arxiv.org/pdf/2112.13495). Example: for a seller-facing discount badge, randomise both sellers (badge or not) and buyers (shown badges or not), then compare the four seller-by-buyer cells.

## Designs for interference

- **Cluster randomisation.** Randomise whole clusters such as categories or cities. Airbnb's simulation cut bias by up to 62% but raised variance by 75%, and the authors advise it only when large interference is expected, since the higher MDE can make a test unusable (https://arxiv.org/html/2004.12162v1). The effective sample size is N / (1 + (m - 1) x ICC), which approaches the number of clusters as the ICC grows. Example: for a pricing rule, randomise by leaf category, because sellers in one category compete for the same buyers.
- **Switchback.** The whole market, or a region, alternates between treatment and control in randomised time windows. Randomise the window order (do not alternate deterministically), make windows longer than the carry-over, randomise regions independently, and use cluster-robust errors with the window as the cluster (https://careersatdoordash.com/blog/switchback-tests-and-randomized-experimentation-under-network-effects-at-doordash/ (snippet), https://careersatdoordash.com/blog/cluster-robust-standard-error-in-switchback-experiments/ (snippet)). Storms, big events and outages contaminate windows (https://eng.lyft.com/challenges-in-experimentation-be9ab98a7ef4 (snippet)). In practice this means dropping holiday and sale-event days. Switchbacks are a poor fit when effects carry over, such as price changes that shift future demand, or when sellers see and react across windows.
- **Budget-split.** For promotion pools with a fixed budget or inventory cap, split users into two groups and split each budget into two matching sub-budgets, one per group, so arms cannot eat each other's budget. LinkedIn found the naive test differed by 30 to 70% from the budget-split result (https://arxiv.org/abs/2012.08724, https://www.linkedin.com/blog/engineering/infrastructure/budget-split-testing). An analogue is a seller-funded discount pool or a daily-deal slot allocation split in proportion to arm size.
- **Geo randomisation.** Whole regions as units. This gives few units, so pair it with synthetic control or DiD. National sellers, delivery and campaigns leak across regions (research-note background knowledge, not a sourced finding).

## Power and duration

Run `cstat.py power --metric mean|proportion|ratio --baseline ... --sd ... --mde ... --alpha 0.05 --power 0.8`. Compute variance at the randomisation unit using history. For cluster designs add `--cluster-size` and `--icc`. The design effect is `1 + (m - 1) x ICC`, where m is the cluster size; it multiplies the sample size, so a plain user-level MDE is too optimistic for clustered or switchback tests. Add `--units-per-day` to get a duration.

Run whole weeks. Weekday and weekend behaviour differ, and sale events and calendar effects (see `stat-checks.md`) hit weeks unevenly. Variance reduction by CUPED can cut the required sample by roughly 10 to 20% (DoorDash, snippet: https://careersatdoordash.com/blog/improving-experimental-power-through-control-using-predictions-as-covariate-cupac/); the method is in https://dl.acm.org/doi/10.1145/2433396.2433413.

## Guardrails and power guardrails

Name one primary metric and two to four guardrails before launch. Guardrails are of two kinds: metrics you must not hurt (revenue, margin, cancellations, latency) and invariants that should not move by design (allocation ratio). Airbnb adds a power guardrail: check that the test has enough exposure that "no significant harm" is a meaningful statement (https://medium.com/airbnb-engineering/designing-experimentation-guardrails-ed6a976ec669). In a marketplace, guard GMV, margin and seller-side metrics. A null guardrail on an underpowered test is inconclusive, not safe.

## Time effects

- **Novelty and primacy.** An early lift fades or an early dip recovers. Plot the daily effect and split new from returning users. Run at least one or two full weekly cycles, longer for habits (https://arxiv.org/pdf/2102.05310).
- **Lead-day bias.** In pricing, outcomes for future dates arrive later, so a short test can point the opposite way from the long-run result (https://dl.acm.org/doi/10.1145/3583780.3615502; the research note summarised the paper without parsing all of it).
- **Pull-forward.** A promotion can move demand from the days after it into the promotion window. Measure the post-period and total category or seller outcome, not only the promoted items.

## Holdouts and long-term effects

Keep a small holdout after launch (1 to 5%) and re-read at 4 and 12 weeks. Meta uses asymmetric holdouts for this (snippet: https://medium.com/@AnalyticsAtMeta/the-power-of-asymmetric-experiments-meta-8a8030d68c31). Short-term surrogates can stand in for long-run outcomes, but fail when the treatment acts through paths the surrogates miss (https://arxiv.org/pdf/2311.11922).

## Setting it up in GrowthBook

- Set `hashAttribute` to the randomisation unit: user id for buyers, seller id for sellers (evaluated server-side, where the id is known). A session id changes each visit, so users cross arms; avoid it for pricing (https://docs.growthbook.io/lib/build-your-own).
- Use a namespace for mutual exclusion between experiments; overlapping namespace ranges break independence.
- Log exposure through the tracking callback, or the assignment never reaches the warehouse.
- For cluster designs, set the hash attribute to the cluster id. All members then share an arm, but GrowthBook's statistics treat rows as independent users, so clu must aggregate to cluster level and analyse it.
- Switchback has no native support. Use a time-block id as the attribute, log it, and analyse at the block level. Holdouts are an Enterprise feature; without a licence, emulate one with a namespace slice.

## Before launch checklist

1. Write `analysis_plan.md`: unit, primary metric, guardrails, MDE, duration, decision rule.
2. Run an A/A or offline simulation: split past data at random many times and check the false-positive rate is near 5% and p-values are uniform.
3. Plan the SRM check on assignment counts and again on triggered counts (`cstat.py srm`).
4. State which side is scarce and what the interference story is.
5. Exclude campaign-calendar days that will contaminate the window.
