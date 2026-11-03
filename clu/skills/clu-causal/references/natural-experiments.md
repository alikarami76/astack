# Finding natural experiments in a marketplace

A natural experiment is a change in exposure that something other than the outcome decided. Use this list when nobody randomised. Walk through it with the user, then check the data: plot exposure by date and unit, look for mass at thresholds, look for calendar irregularities. Sources: `docs/research/2026-09-29-causal-inference.md` (section 2) and the published examples at the end. The examples are prompts to ask about, not findings; confirm each against the code and the data.

## Eligibility thresholds and tier boundaries

Examples to probe: a free-shipping minimum, a seller score for a badge, the minimum discount to join a campaign, commission tiers by GMV, a rating cutoff for a ranking boost.
**Why it can be as good as random:** units just above and just below a threshold are near-identical, so crossing it acts like a coin flip.
**What breaks it:** sellers who see the score and move themselves across the line; two rules switching at the same threshold; a rule applied by hand, not by machine.
**Design and command:** regression discontinuity with a density test (`cstat.py rdd`); if agents pile up at the line, bunching (no toolkit command). See `method-selection.md`.

## Staggered category or cohort rollouts

Examples: a feature enabled category by category, seller cohorts onboarded in waves.
**Why it can be as good as random:** the order is set by an operational schedule, not by expected outcomes.
**What breaks it:** the order followed readiness, traffic or a promising trend; a big campaign landed in one wave only. Ask who chose the order and why, and get the dated rollout log from feature flags or deployment history.
**Design and command:** staggered DiD with Callaway-Sant'Anna or Sun-Abraham, never plain TWFE (`cstat.py did --method cs --control notyet`).

## Alternating-day and rotation rules

Example: a daily deal that is chosen automatically on odd days and by hand on even days, or a ranking rule that swaps every other week. An alternating rule can act like a randomised assignment.
**Why it can be as good as random:** the rule is mechanical and set in advance, so what a seller or a buyer did does not choose the day type.
**What breaks it:** carry-over. Buyers may wait for the favourable day type, and stock sold on one day is gone the next; both break the no-interference assumption that `abtest` and `permute` rely on. Check lead and lag days and compare outcomes over day pairs. Also the alternation may line up with weekdays or sale days, and month boundaries and the swap logic can break the pattern; ask whether sellers can move offers onto favoured days; deterministic rules can correlate with listing age. Check balance on pre-period metrics and the weekday mix before any effect is read.
**Design and command:** treat as a quasi-randomised comparison, analysed at day level with `cstat.py abtest` or, with pre-period data, `cstat.py did`; cluster by day block, and use `cstat.py permute` for randomisation inference because the effective sample is the number of days.

## Outages and bugs

Examples: a discount not applied to a subset of offers, a ranking bug, a payment gateway outage.
**Why it can be as good as random:** the cause is a fault, not a decision, so exposure changes for reasons unrelated to demand.
**What breaks it:** the fault hit units unevenly (large sellers, one region); people reacted to the outage itself. Ask for the incident log and the exact affected IDs and times.
**Design and command:** outages are usually shocks for interrupted time series or DiD (`cstat.py its`, `cstat.py did`). Most outages hit outcomes directly (a payment outage cuts orders), so they are an instrument only if the fault changed the treatment alone, such as a discount silently not applied, and the exclusion restriction can be argued (see `method-selection.md`).

## Capacity limits

Examples: delivery-slot caps, warehouse limits, campaign slot lotteries.
**Why it can be as good as random:** when a cap binds, who gets in near the limit depends on arrival order, which is close to arbitrary.
**What breaks it:** customers who arrive early differ from those who arrive late; a `capacity = 0` row can mean an invisible slot, so check how the slot table encodes full versus hidden.
**Design and command:** regression discontinuity on arrival rank, or an instrument. Check the KB for what the slot table records before using it.

## Price rounding and left-digit effects

Prices just below and just above a round number are almost equal in value but look different to buyers (https://doi.org/10.1287/mnsc.2022.4410, https://www.aeaweb.org/articles?id=10.1257/aer.102.5.2206).
**Why it can be as good as random:** a price of 999 versus 1,000 currency units changes perception, not real value.
**What breaks it:** sellers choose round or non-round prices on purpose, so the two groups differ in kind. The rounding itself is bunching.
**Design and command:** regression discontinuity around the round number, with a bunching check. Compare money in a stable currency, not a depreciating one, when crossing dates.

## Campaign-calendar shocks

Sale events (seasonal sales, Black Friday-style events) and currency moves hit all units, but with different intensity by category.
**Why it can be as good as random:** exposure intensity differs by category for reasons the category did not choose.
**What breaks it:** the events overlap most launches and hit treated and control categories unequally. Lunar-calendar holidays such as Ramadan shift by about 11 days a year; others are fixed on their own calendar.
**Design and command:** DiD with exposure as a dose, event-by-category controls, or exclude event windows. Note that stronger assumptions apply to dose-response designs (https://arxiv.org/abs/2107.02637).

## Published examples

Keep only the ones the research marked as verified.

- eBay brand-search ads: a geographic holdout across DMAs found brand search ads had little return; correlational ROI overstates the effect (https://doi.org/10.3982/ECTA12423).
- Uber surge pricing: regression discontinuity around multiplier thresholds gave demand elasticity (https://www.nber.org/papers/w22627). Algorithmic price steps create discontinuities for free.
- Booking.com: experimentation at scale and its pitfalls, useful for guardrails and false-positive control (https://arxiv.org/abs/1710.08217).

The research could not find a well-documented public study with a stable link for Amazon, Instacart or Alibaba, so do not cite them from memory.
