# clu — data analyst

A truth-seeking data analyst agent family for Claude Code. It works with whatever data-source skills or tools you configure.

- Start it: `clu` (alias for `claude --agent clu`), or `/clu <question>` inside any Claude Code session.
- Review its self-improvement proposals: `/clu-review`.
- Install: `./install.sh`.
- Tests: `python3 -m pytest -q`.

Read `docs/architecture.md` first.

## Prerequisites and current limits

- **Prerequisites:** for causal work, uv (the causal toolkit env is built by scripts/causal/setup.sh, which install.sh runs); for GrowthBook, a read-only API key in ~/.claude/secrets/growthbook.env (GROWTHBOOK_API_URL, GROWTHBOOK_API_KEY); PyYAML; for semantic recall, numpy + torch + transformers and the local model `Qwen/Qwen3-Embedding-0.6B` (about 1.2 GB, downloaded once to the Hugging Face cache; then `python3 kb/kb.py embed`). Without them, search falls back to keywords only; one or more read-only data-source skills or tools of your own (a SQL client, a warehouse or BI tool skill), listed in a sources file (see `skills/clu-core/references/source-routing.md`); `~/.claude/skills` must be a git repo (`install.sh` appends the linked skill names to its `.gitignore`, which needs a repo to sit in).
- **OpenMetadata** (optional): an `openmetadata` MCP server with a personal access token in its Authorization header (`claude mcp add --transport http --scope user openmetadata <OpenMetadata url>/mcp --header "Authorization: Bearer <token>"`); then `python3 scripts/om.py pull && python3 kb/kb.py import-legacy --only om && python3 kb/kb.py embed --lane om`. Without it, clu works as before.
- **`$CLU` is hardcoded** to `$HOME/agent-stack/clu` throughout the agents, skills and scripts, so the repo must live at that exact path until the paths are made relative.
- **After `git pull`**, run `python3 kb/kb.py rebuild` (it refreshes the vector index when one exists) **before** any `kb.py add` — `add` re-derives and overwrites that entity type's export from the local KB, so a stale or missing local DB will wipe out what `git pull` just brought in.
- **Plugin mode may namespace agent names** (e.g. `clu:clu` instead of `clu`). The telemetry family filter (`hooks/telemetry.py`) and the `/clu` skill do not handle that namespacing yet — under a plugin install, telemetry may under-capture and `/clu` may need the exact namespaced agent name.
