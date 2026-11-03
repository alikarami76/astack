# Measurement contract

- **Question (user's words):**
- **Decision this serves:** what will be done differently depending on the answer
- **Metric:** name, and the formula written as numerator / denominator
- **OpenMetadata metric:** the certified catalog metric this follows (its name) and any difference from it, or "n/a"
- **Unit of analysis (grain):** order line | order | cart | cart shipment | seller | variant | day …
- **Units to report:** which of count (products / variants / sellers), value (revenue) and per-channel splits the answer needs. For coverage and share questions, ask up front and default to all of them side by side, labelled "% of products" and "% of revenue"; if thresholds matter (e.g. number of offers), agree the ladder of levels now
- **Population and filters:** who and what is included and excluded (business-to-business orders, cancelled or test records, one product family only?, experiment groups, …)
- **Window and time boundaries:** dates, time zone, cycle boundaries (e.g. a business day that runs noon to noon), the working week
- **Money:** currency (and scale, such as units vs thousands) and time unit (per day / month / total over window)
- **Baseline / comparison:** vs what (pre-period, control group, colleague's number, dashboard)
- **Sources:** table.column for each input, with known pitfalls from the KB
- **Expected magnitude:** from benchmarks or past runs, so a result can be sanity-checked
- **Method:** plain description; for causal claims, the identification strategy and main confounders
- **Output:** format, audience, language, charts (for each: the finding it must prove, its unit and base, the one item to highlight)
- **Assumptions:** each tagged verified / observed / assumed
- **Open questions for the user:**
