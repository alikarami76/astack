# flynn architecture

flynn is the user's design agent and a sibling of clu and quorra. This file describes what is built.

## Entry points
- `claude --agent flynn` starts the lead agent. The shell alias `flynn`, added to `~/.zshrc` by `install.sh`, runs the same command.
- `/flynn <brief>` (skill `skills/flynn`) works from any Claude Code session. It spawns flynn as a subagent and relays its `NEEDS_ALIGNMENT` blocks to the user. Any other agent with the Agent tool (clu, quorra, and others) can call flynn the same way, with `subagent_type: "flynn"`. A calling agent that cannot ask the user passes the blocks up to its own caller. The block formats are in `skills/flynn-core/references/teammate-protocol.md`.
- `/flynn-review` reviews pending proposals.

flynn detects its mode itself: if it can call AskUserQuestion it is in direct mode; otherwise it is in teammate mode, and runs are recorded with `mode: teammate` and the caller's working directory.

## Agents
| Agent | Model | Job |
|---|---|---|
| `flynn` | opus | Lead: conversation, contract, decisions |
| `flynn-scout` | sonnet | Candidate references and images |
| `flynn-librarian` | sonnet | Recall from the store, and design system extraction |
| `flynn-composer` | sonnet (opus for new component specs) | Writes flynn documents |
| `flynn-renderer` | sonnet | Runs adapters, returns screenshots |
| `flynn-critic` | sonnet (opus for the final verdict) | Design review against contract, references and rubric |
| `flynn-checker` | haiku | Mechanical checks |
| `flynn-fresh-eyes` | sonnet | First-impression review from screenshots only, with no contract |
| `flynn-curator` | haiku | Lessons, taste notes, design records, proposals |

The lead preloads `flynn-core` (the protocol). Specialists write to the run folder and return at most 10 lines.

## Location
flynn lives in `agent-stack/flynn`, inside the agent-stack repo with clu, quorra and pdfsmith; `$FLYNN` points to it. `~/.claude/agents` and `~/.claude/skills` point at `agent-stack/agents` and `agent-stack/skills`, which link to flynn's files. `proposals.py apply` refuses while the agent-stack repo has staged changes anywhere.

## Learning a system from sample designs
`scripts/infer_system.py <system> <canva-json>... --ids <ids> --out <dir>` reads Canva `read-design` element data and writes `system.json` (colors, type styles, spacing, section outlines, variants) and `kb-rows.json` (raw and guessed semantic tokens, tier `inferred`). Roles, type and spacing come only from the dominant layout (the most common page width); other widths are summarised as variants. Naming components and fixing roles is flynn's judgment, written to the store as `inferred` rows with the Canva design ids as the source.

## Data stores
- Knowledge store: `kb/kb.sqlite`, gitignored and rebuilt from the committed `kb/export/*.jsonl` files by `kb.py rebuild` (install runs it). Types: `token`, `component`, `pattern`, `reference`, `design`, `lesson`, `taste`, `run`. Components and tokens carry a tier: `verified`, `figma-only`, `code-only`, `drift`, `inferred` or `proposed`.
- flynn documents: JSON files in each run's `docs/` folder; the format is in `docs/flynn-doc-format.md`, and `scripts/fdoc.py validate` checks them against the store.
- Runs: `runs/<date>-<slug>/`. Only `run.json` is tracked.
- Proposals: `proposals/`, applied through `/flynn-review`.

## Self-improvement
Free writes need no approval: lessons, taste notes, reference verdicts, design records and run records. Changes to the design system, or to flynn's agents, skills, scripts or rubric, are filed with `scripts/proposals.py new` and applied only after the user approves them. A component the user approves during a run enters the store as `proposed`.

## Stage
| # | Area | Status |
|---|---|---|
| 0 | Skeleton | built: the knowledge store, the flynn document format and validator, run records, proposals, the agents and teammate mode |
| 1 | Knowledge core (learning design systems) | built: recording a live product (`adapters/recorder`), inferring a system from sample designs (`scripts/infer_system.py`), and library files on the canvas (`adapters/library`) |
| 2 | UI in Figma (Figma adapter, full protocol) | Figma page import is built (`adapters/figma`); the full Figma UI protocol is not |
| 3 | Figma to code (TSX adapter, diff verification) | partly built: HTML to TSX is in Studio (`adapters/studio/html_to_tsx.py`); diff verification is not |
| 4 | Decks and graphics | built: `adapters/html` (render, crops, new_page), the neutral `editorial` theme, and the `release-page`, `report` (with `charts.py`), `deck` (`--slides`) and `newsletter` (`email_pack.py`) templates; editable PowerPoint (`adapters/pptx/pptx_export.py`) and the Canva plan adapter (`adapters/canva/canva_plan.py`) |
| 5 | Data visualization | built: 11 chart types (`adapters/html/charts.py`), chart specs and the clu handoff (`chartspec.py`, `docs/clu-handoff.md`), rules and critic checklist (`skills/flynn-core/references/dataviz.md`) |
| 6 | Studio and flynn desk | built: the review UI (`adapters/review`: comment pins on the live page, replies, resolve, auto-reload); Studio (`adapters/studio`: runs home, options and versions on a canvas, picks, exports); flynn desk, the Penpot-based app with a flynn tab, Letters and organizations (`adapters/penpot`, `adapters/letters`, `adapters/orgs`) |
| 7 | Evals and learning | built: `scripts/lint_page.py`, whose rules each come from a lesson the user taught |

Until an adapter exists, flynn can do clearly labelled interim designs with the Figma skills or Canva directly; these are tagged `interim` in the store.

Live product references: `adapters/browser/capture.py` drives flynn's own Chrome (Selenium 4, own profile at ~/.flynn/chrome-profile, driver fetched by Selenium Manager). The user browses and logs in by hand; snaps (viewport and full-page PNG, scrubbed live HTML, optional MHTML) go to `refs/snaps/` (git-ignored, local only) and into the store as `candidate` references.

Output route: anything people read (release notes, reports, newsletters, decks) goes through the HTML adapter; Canva only when someone must keep editing in Canva; Figma for product UI. Canva was ruled out as the default because its Free plan cannot load custom brand fonts, its API cannot set fonts or page height, and exports can be blocked.

## Smoke tests
- Direct mode: `claude --agent flynn -p "Who are you and what can you do at this stage?"` answered as flynn. It listed what is built and what is missing, offered an interim design, and noted that the Canva and Mobbin connectors were not authorised.
- Teammate mode: `claude -p '/flynn Design an empty state for an orders list...'`, run from an unrelated project folder, loaded the skill and spawned the flynn subagent. flynn followed the teammate protocol, but every read and script call under the flynn folder needed approval that a headless session cannot give. So it returned a correct `BLOCKED` block naming the refused calls and the allow rules needed, with its first brainstorm question already drafted. This matches quorra's headless behaviour. Interactive sessions work by approving the prompts. For headless or prompt-free use, allow Read on `$HOME/agent-stack/flynn/**`, and Bash for `python3 "$HOME/agent-stack/flynn/kb/kb.py" *` and `python3 "$HOME/agent-stack/flynn/scripts/*.py" *`.
