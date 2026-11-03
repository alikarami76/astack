# Test design heuristics

## Charter template (Elisabeth Hendrickson, "Explore It!")
Explore **<target>** with **<resources>** to discover **<information>**. Time box 30 to 60 minutes. Debrief: what was covered, what was found, questions, what is left. One charter per high-risk area from the plan.

## SFDIPOT: where to look (James Bach, Heuristic Test Strategy Model)
- **Structure**: steps, screens, components, what the wizard is made of.
- **Function**: calculations, validations, auto actions (auto raise), state changes.
- **Data**: prices, percents, quantities, Persian and Latin digits, empty, huge, Excel input.
- **Interfaces**: APIs, wallet and credit services, payment gateway, Excel import and export.
- **Platform**: desktop and mobile layouts, browsers, module flags.
- **Operations**: how sellers really use it: round prices, bulk "all variants", many slots, coming back later.
- **Time**: deadlines (48 h, 24 h), slots, periods, time zone Asia/Tehran, Jalali dates, refresh mid-flow.

## HICCUPPS: how to know it is wrong (Michael Bolton)
Consistent with: **H**istory (earlier versions), **I**mage (your company's reputation), **C**omparable products (other promotion flows), **C**laims (PRD, help text, product map), **U**ser desires, **P**roduct (the same rule elsewhere in the panel), **P**urpose, **S**tatutes (tax, consumer law). A finding names its oracle.

## Boundary values and equivalence classes
For each limit test just below, on, and just above (minimum and maximum). For each input, one value per class: valid, invalid type (fraction, letters), empty, Persian digits, very large. Money: multiples of 100 rials and one value that is not. Percent: 0, 1, the minimum, 99, 100.

## Pairwise (combinatorial) testing
When several options combine (payment method x slot count x variant mode x module flags), cover every pair of values instead of every combination; NIST's work shows most interaction bugs need only two factors. List the factors and values in the plan and generate the pairs by hand or with a small script.

## Oracles for calculations
Write the oracle from the requirement with exact arithmetic (integers or fractions), never by copying the code. Compare it with the real code over seeded random inputs and the values sellers actually use (round prices, common percents). Shrink a mismatch to the smallest example.
