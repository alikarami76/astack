# Tool catalog

| Tool | Command | Purpose |
|---|---|---|
| KB | `python3 kb/kb.py init\|add\|add-many\|search\|recall\|embed\|show\|verify\|supersede\|link\|export\|rebuild\|stats` | knowledge base; errors are one-line messages with exit 1 |
| Runs | `python3 scripts/run_record.py new --tier\|set\|append\|log\|ingest\|validate [--for-close]\|close` | per-task records; accepts a run id or path; `ingest` validates helper output and computes the verification verdict; `close` checks the protocol and commits |
| Analysis record | `python3 scripts/run_report.py scaffold\|build\|check [--cached] <run>` | `methods.md` (steps, data, queries, aggregations) and `reproduce.ipynb` (standalone Jupyter notebook, explanation cells plus code, executed and saved with outputs), checked against the reported values |
| Helper outputs | `scripts/helper_output.py` (library) | JSON schemas for helper outputs and the tolerance rule |
| Role guard | `hooks/guard.py` (PreToolUse hook) | enforces verifier blindness, read-only challenger and editor, curator and lead self-edit limits |
| Proposals | `python3 scripts/proposals.py new\|list\|show\|apply\|reject` | gated self-improvement |
| Patterns | `python3 scripts/patterns.py --since N` | cross-run learning signals |
| Telemetry | `hooks/telemetry.py` (hook, 4 events) | event log |
| Hooks install | `python3 scripts/install_hooks.py [--yes]` | merge hooks into ~/.claude/settings.json |
| Replay | `python3 scripts/replay.py [--case ID]` | contract-quality regression tests |
| Install | `./install.sh` | link agents and skills, hooks, alias, KB setup |
| BI exports | `python3 scripts/bi_exports.py index\|list [--q TEXT]` | index dashboard exports the user drops into clu-snapshots/bi-exports |
| Recall eval | `python3 kb/eval_recall.py [--mode lexical\|hybrid\|both] [--show-misses] [--save-baseline] [--check]` | scores search on `kb/eval/recall_set.jsonl` (recall@5/@10, hit@5, MRR); baseline in `kb/eval/baseline.json`; `--check` fails on a drop of more than 0.03 |
| Causal toolkit | `"$CLU/.venv-causal/bin/python" scripts/causal/cstat.py power\|srm\|abtest\|multitest\|permute\|evalue\|did\|rdd\|its\|synth\|gbcheck` | tested estimators with diagnostics; setup: scripts/causal/setup.sh |
| GrowthBook | `python3 scripts/growthbook.py experiments\|experiment\|results\|metrics` | read-only; key in ~/.claude/secrets/growthbook.env |
| OpenMetadata | `python3 scripts/om.py pull\|search\|status` | read-only catalog client; URL and token from the `openmetadata` MCP entry in ~/.claude.json (or OM_URL, OM_TOKEN) |
| OM search lane | `python3 kb/kb.py search "<text>" --lane om` | OpenMetadata metrics and glossary terms imported into the KB; conflicts with clu rows are kept in the snapshot's state.json |
| OM drafts | `python3 scripts/om_drafts.py conventions\|new\|validate\|render\|status\|payload` | staged entries for OpenMetadata in `CLU_OM_DRAFTS`, validated against the vocabularies of the latest snapshot |
| OM recall eval | `python3 kb/eval_recall.py --lane om [--check]` | scores the om lane on `kb/eval/om_recall_set.jsonl`; baseline `kb/eval/om_baseline.json` |
