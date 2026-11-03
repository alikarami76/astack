# clu architecture

clu is a family of Claude Code agents plus a small toolkit. The behaviour lives in markdown; the mechanics live in stdlib Python.

## Entry points
- `claude --agent clu` (alias `clu`): direct mode. clu is the main session and talks to the user.
- `/clu <question>` in any session: teammate mode. The caller spawns clu as a subagent and relays NEEDS_ALIGNMENT questions (see skills/clu-core/references/teammate-protocol.md).
- `/clu-review`: approve or reject self-improvement proposals.

## Agents
clu (opus, lead) → clu-challenger (sonnet), clu-data-runner (sonnet, deep tier only), clu-verifier (sonnet), clu-editor (haiku), clu-curator (sonnet), clu-methodologist (opus): designs identification for causal runs. They use whatever data-source skills or tools you configure (see skills/clu-core/references/source-routing.md), dashboard exports indexed by scripts/bi_exports.py, and any table-explorer, dashboard-builder or bug-investigator skills you add. Which helpers run depends on the tier (quick / standard / deep) in clu-core.

Helpers hand back one JSON file each (schemas: scripts/helper_output.py). The lead records it with `run_record.py ingest`, which validates it; for the verifier, it compares the recomputed numbers with the lead's `verify/claims.json` and applies the tolerance rule in code. The role rules are enforced by a PreToolUse hook (hooks/guard.py), not only stated in prompts: the verifier cannot read the runner's queries, results or helper outputs; the challenger and editor write only their output file; the curator and the lead cannot edit agents, skills, scripts, hooks or tests.

## Skills
clu-core: analyst protocol + references. clu-causal: causal protocol + references. Also clu, clu-review.

## Mechanics
Causal toolkit: scripts/causal/cstat.py in .venv-causal (Python 3.12, uv); GrowthBook reader: scripts/growthbook.py (read-only REST).

## Protocol
recall → contract (challenger + user confirmation) → probe → execute (parallel runners) → verify (independent verifier + challenger) → report (editor) → learn (curator). Full text: skills/clu-core/SKILL.md.

## Data stores
- Knowledge: kb/kb.sqlite (FTS5), plus an optional local vector index kb/kb.vectors.sqlite (Qwen3-Embedding-0.6B, curated rows only; untracked, rebuilt with `kb.py embed`). Search fuses BM25 and vector rankings with reciprocal rank fusion (kb/vectors.py). Rebuilt from tracked kb/export/*.jsonl. Written only via kb/kb.py, and optionally mirrored into the memories of your database and BI skills (see kb/writethrough.py). Schema: docs/kb-schema.md.
- OpenMetadata (optional): a company data catalog, read-only. `scripts/om.py pull` snapshots its tables, metrics, glossary, tags and domains to a local folder outside the repo; an import step writes them into the KB with origin `om:<fqn>`. OpenMetadata metrics and glossary terms live in their own vector lane (kb/kb.om.vectors.sqlite) and recall section, and their synonyms expand only that lane, so they never crowd the curated ranking; its tables and columns enrich the schema lane. Layer rule: the catalog wins for the warehouse tables it curates, clu's verified rows for operational ones.
- OpenMetadata drafts: tables, metrics and glossary terms clu learns in a domain the catalog lacks are staged in the catalog's own conventions in a drafts folder (`CLU_OM_DRAFTS`) by `scripts/om_drafts.py` (conventions, new, validate, render, status, payload); the catalog owners load them.
- Personal memory: ~/.claude/agent-memory/clu/MEMORY.md (preferences only).
- Runs: runs/<date>-<slug>/run.json. Schema: docs/run-schema.md.
- Telemetry: telemetry/events.jsonl. Schema: docs/telemetry-schema.md.
- Proposals: proposals/ (pending), proposals/applied/, proposals/rejected/, plus a changelog the apply step writes.
- Official dashboard exports: ~/agent-stack/clu-snapshots/bi-exports/<date>/ (files the user exports by hand; indexed by scripts/bi_exports.py, never committed).

## Self-improvement (tiered)
Free: KB rows (mirrored by writethrough.py into your data skills' memory files when those exist, which is an ungated write to those files), memory, run records. Gated: anything in agents/, skills/, scripts, hooks, tests, and existing skills' instructions; the guard hook refuses direct edits by clu and its curator. These go through proposals.py → /clu-review → apply (tests, commit, changelog).
