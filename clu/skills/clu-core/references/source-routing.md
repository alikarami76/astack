# Where data lives and which skill to use

clu does not ship with data connections. It works with whatever data-source skills or tools you configure. Fill in the table below once (copy the example rows, then replace them with your own), and keep the machine-readable copy in `~/.claude/clu-sources.json` so the reproduce notebooks can run your queries too.


## Your sources (fill this in)

| Need | Use | Known limits |
|---|---|---|
| Transactional truth: orders, payments, customers | _example:_ `warehouse-db-analyst` skill (SQL engine `warehouse`) | SELECT only; at most 1,000 rows per call (aggregate in SQL or chunk by id); 300 s statement timeout; no cross-engine joins (join in Python) |
| Big scans, funnels, daily summary tables | _example:_ `bi-analyst` skill over your BI tool's SQL API | 1,000 rows by default, raise with `--max-limit`; at most 4 JOINs; no trailing `;`; summary tables keep about 60 days of detail |
| Website or app events | _example:_ `events-analyst` skill (columnar event store) | about 30 days of retention; set `max_threads = 1` on each query |
| Finance history | _example:_ `finance-db-analyst` skill | none recorded yet; record what you learn |
| Official BI dashboards | `python3 "$CLU/scripts/bi_exports.py" list --q "<report>"` over files you export into `~/agent-stack/clu-snapshots/bi-exports/<date>/`; read them with pandas | only what you have exported, as of the export date; if a report or visual is missing, export it (most BI tools have Export data or Export to CSV on the visual menu) |
| What code does with a table or column | your code-reading skill or tool (a read-only Git host client works well) | read-only |
| Build or edit dashboards | your dashboard-builder skill, if any | usually limited to what it created |
| Experiment design method | the references of `clu-causal` | |
| Deliverables | a PDF skill, `anthropic-skills:xlsx`, `anthropic-skills:pptx`, your documentation tool's writer skill; charts through flynn's chart handoff (below), with `dataviz` as the fallback | open the file after creating it |
| Experiments and their results (A/B tests) | `python3 "$CLU/scripts/growthbook.py" experiments --q "<name>"`, then `experiment`/`results <id> --cache <run>/gb` | read-only; 60 requests/min; adjusted p-values are not in the API (use `cstat.py multitest`); recompute with `cstat.py gbcheck` |
| Certified warehouse definitions: what a curated table or column means, how a metric is defined (SQL, unit, aggregation, base table), the business glossary with its synonyms | OpenMetadata, already in the KB: `kb.py recall` prints an **OpenMetadata definitions** section, `kb.py search "<text>" --lane om` for more; live, the `openmetadata` MCP tools (`search_metadata`, then `get_entity_details` with the exact FQN and entity type it returned) or `python3 "$CLU/scripts/om.py" search "<text>"` | read-only; some OpenMetadata servers have semantic search or metric detail lookups disabled — use `search_metadata` and the KB copy |

Always check `kb.py search "<table>"` first: the KB records access, speed and pitfalls per table.

**OpenMetadata and clu disagree: which wins.** The catalog is the authority for the warehouse tables it curates and their metrics: use its definition, cite the metric name, and record clu's differing row as a pitfall. clu's verified rows win for operational tables. Recall prints each clash as `conflict: <name> — clu <id> vs OM <id>`; say in the contract which one you used and why.

Record per-source traps that are not obvious from the schema, such as which owner column is current and which is a point-in-time snapshot. If two reasonable choices shift results by several percentage points, write that down in the KB with the numbers.

## The sources file

`~/.claude/clu-sources.json` (or the path in `CLU_SOURCES_FILE`) maps each query kind that `run_sql` in a reproduce notebook understands to the command-line tool that runs it:

```json
{
  "mysql":      {"tool": "~/.claude/skills/warehouse-db-analyst/scripts/query_tool.py", "flag": "--engine"},
  "metabase":   {"tool": "~/.claude/skills/bi-analyst/scripts/bi_query_tool.py", "flag": "--database"},
  "clickhouse": {"tool": "~/.claude/skills/events-analyst/scripts/events_tool.py", "flag": "--engine"}
}
```

The tool must accept `--quiet [--max-limit N] [--database D] query --file <sql> --output <json>` after its flag (see `run_sql` in `scripts/run_report.py` for the exact command it builds), and write rows as JSON. A kind with no entry fails with a clear message. The kind names (`mysql`, `metabase`, `clickhouse`, `ods`) are only labels: use `mysql` for any SQL database you reach through a tool of that shape.

## Direct CLI lines (use these instead of loading a whole skill)

Load the source skill only if you need something these lines do not cover (schema discovery, memory search, safety rules you are unsure of). Replace the example paths with your own.

- SQL database: `python3 ~/.claude/skills/warehouse-db-analyst/scripts/query_tool.py --engine <engine> --quiet query --sql "<SELECT …>"` (or `--file <q.sql> --output <out.json>`). Global flags (`--max-limit N`, `--timeout S`, `--quiet`) go before `query`. Every query needs a LIMIT or a WHERE on an id or date column.
- BI tool SQL API: `python3 ~/.claude/skills/bi-analyst/scripts/bi_query_tool.py --quiet query --database <db id or name> --sql "<SELECT …>"`. Check the row cap, the JOIN limit and whether `;` is accepted. Long inline id lists are faster with `IN (...)` than with an array function. Some engines truncate `GROUP_CONCAT`-style aggregates at about 1,024 characters: chunk by id instead.
- Event store: `python3 ~/.claude/skills/events-analyst/scripts/events_tool.py --engine <engine> --quiet query --sql "<SELECT …>"`.
- Table notes: your database skill's memory search, for example `python3 ~/.claude/skills/warehouse-db-analyst/scripts/memory_tool.py search "<table>" --brief`.

## Cross-source coverage and share questions

When a question joins one source's product set to another's sales or catalogue (coverage, share of revenue, share of variants), do it in ONE engine and aggregate there; do not pull per-product rows from both sides and join in Python. Per-product pulls hit the row caps and turn into dozens of chunked calls per plan.
- If one engine mirrors the other (for example a BI tool's synced copy of a transactional database), check that the row counts match, compute the flags per product in a subquery on the mirror, and join to the sales table there. Return group-by-level aggregates (products, variants per channel, revenue per channel) in one call per denominator. Use the original database only for columns missing from the mirror and for point lookups; full scans there often time out.
- For a sellable-catalogue snapshot, use the daily availability summary table joined to the product table for the seller, sliced by `product_id % 100`, and compare with a change-log replay if one exists (a few percent gap is normal).
- Ask for every unit the answer may need (products, variants, revenue) in the same query plan, so a follow-up does not need a new runner.

## Charts: hand them to flynn

For every chart in a deliverable, write a chart spec to `<run>/deliverable/charts/<name>.json` (format: `$HOME/agent-stack/flynn/docs/clu-handoff.md`). `message` is the finding sentence from your answer, `unit` includes the currency and time unit, `window` names the population and period, and `source` names this run and whether the number is verified. Also fill the exhibit fields: `exhibit_no` (`"auto"` unless the report numbers its charts itself), `subtitle` with `measure`, `cuts`, `unit` and `base`, `footnotes` for what a series includes, `notes` for rounding and method, `source` as a list that starts with this run's id and its verified or unverified status, and `highlight` for the one thing the finding names. Do not choose colours, sizes or chart styles; flynn does. Do not put the finding in `notes`. When the finding is a comparison with a total (growth, shares of a total), use a `bridge` or `stacked_columns` spec; for size-and-rate questions use `marimekko`; for before-and-after across many items use `dot_range` with `change`. Then run:

```
python3 "$HOME/agent-stack/flynn/adapters/html/chartspec.py" validate "<spec>" && python3 "$HOME/agent-stack/flynn/adapters/html/chartspec.py" render "<spec>" --out "<run>/deliverable/charts/<name>"
```

Use `<run>/deliverable/charts/<name>/chart.png` in the PDF, xlsx or deck, and log it as a deliverable. If `validate` fails, fix the spec (usually the message is not a finding yet). A warning about the base, the source or rounding is a real defect; fix the spec rather than waiting for the editor. For a whole report (many charts, diagrams, page design), call flynn as a teammate (Agent subagent_type "flynn") with a brief, a data file holding every number, the audience and the headline numbers; flynn renders the PDF and writes it where you tell it. If flynn is not installed or chartspec.py fails, fall back to `dataviz`.
