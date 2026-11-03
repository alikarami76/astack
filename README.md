# astack

**astack is a small team of AI assistants for Claude Code, each with one job.** You talk to them in plain language, they ask what they need to know, do the work, check it, and learn from your corrections. They run on your own computer, under your own Claude Code login.

If you have never used Claude Code: it is Anthropic's coding assistant that runs in a terminal. An *agent* here is a ready-made set of instructions, tools and safety rules that turns it into a specialist. Nothing here changes how Claude itself works; it is the working habits of six specialists, written down and tested.

## The six agents

| Agent | What it does, in one line |
|---|---|
| **flynn** | A designer. It designs screens, reports, decks and charts, asks questions first, and reviews its own work. Includes flynn desk, a local design app built on Penpot. |
| **clu** | A data analyst. It agrees on exactly what a number means, queries your data sources, has a second agent re-derive the number, and reports with the evidence. |
| **quorra** | A builder and explainer. It learns how your code is written, answers "does this already exist?", explains features, and builds small changes in its own copy of your repos. |
| **Rinzler** | A QA engineer. It plans tests by risk, writes and runs evals, tests your app on its own local build, and drafts bugs that a second agent tries to disprove before you see them. |
| **ram** | A usage analyst for Claude Code itself. It reads your local transcripts, shows where tokens are wasted, and proposes cheaper setups for you to approve one by one. |

It also includes **pdfsmith**, a tool that builds checked PDFs from Markdown or HTML with first-class Persian and right-to-left support, and `lib/vecstore`, the local search the agents share.

## Install

You need a Mac or Linux machine, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) signed in, Python 3.11 or newer and Git. flynn desk also needs Docker and Chrome; pdfsmith needs [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/alikarami76/astack.git ~/agent-stack
cd ~/agent-stack
bash install.sh
```

The installer links every agent and skill into `~/.claude`, builds each agent's local knowledge store and adds shell aliases. It is safe to run again. The agents expect the repo at `~/agent-stack`; if you clone it elsewhere, the installer links that path to your clone.

Then open a new terminal and start any agent:

```bash
claude --agent flynn      # design
claude --agent clu        # data analysis
claude --agent quorra     # code conventions, explain, build
claude --agent rinzler    # QA
claude --agent ram        # Claude Code usage
```

From any Claude Code session you can also call them as teammates: `/flynn <brief>`, `/clu <question>`, `/quorra <question>`, `/rinzler <request>`, `/ram weekly`. For PDFs, `pdfsmith/README.md` has the commands.

## Make it yours

Nothing about any company ships here. Each agent starts empty and you teach it your world:

- **flynn:** record your product with the recorder, or ingest a design system from code.
- **clu:** connect the data-source skills or tools you use; `clu/skills/clu-core/references/source-routing.md` explains how.
- **quorra:** list your repos and issue tracker in `quorra/config/`.
- **Rinzler:** put your product rules in `rinzler/scripts/product_rules.py` and point `rinzler/scripts/local/app.sh` at your app.

## What is deliberately left out

The agents in the private version of this project carry knowledge bases built from real work: past runs, saved analyses, memories, screenshots, exported data and samples. None of that is published here, and the cleaning step that produced this repo checks every file against a list of terms that must not appear. What you get is the machinery: the protocols, scripts, tests and templates. Each knowledge base starts empty on your machine and fills as you work.

## Each part, in more detail

- `flynn/README.md`, `flynn/docs/architecture.md`
- `clu/README.md`, `clu/docs/protocol.md`
- `quorra/README.md`, `quorra/docs/architecture.md`
- `rinzler/README.md`, `rinzler/docs/architecture.md`
- `ram/skills/ram-core/SKILL.md`
- `pdfsmith/README.md`

flynn desk adds to Penpot without rebuilding it: it mounts an overlay on the stock frontend image (`flynn/adapters/penpot/stack/`). Penpot is © Kaleidos and licensed under MPL-2.0.
