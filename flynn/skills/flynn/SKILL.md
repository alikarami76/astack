---
name: flynn
description: "Bring in flynn, the design teammate, from any session or agent: `/flynn <brief>`. Spawns the flynn agent as a subagent, relays its brainstorm questions, design contract, reference picks and options to the user (or answers from this session's context where the user already has), resumes it, and returns its deliverables. Use for designing product UI in Figma, decks and release-note graphics in Figma Slides or Canva, charts, and questions about any design system flynn has learned."
---

# /flynn: work with flynn as a teammate

You (the current session, or any other agent) are the caller. flynn does the design work; you relay. The protocol flynn follows is in `$HOME/agent-stack/flynn/skills/flynn-core/references/teammate-protocol.md`.

1. **Spawn** flynn with the Agent tool, `subagent_type: "flynn"`. flynn does not see this conversation, so give it a full brief:
   - the user's request, verbatim;
   - context from this session: the project folder, files, screenshots, data, decisions already made, and the audience;
   - where the result should go (a Figma file, Canva, or local files) if known;
   - `mode: teammate`, `caller_cwd: <cwd>`.
2. **Relay alignment.** flynn replies with `NEEDS_ALIGNMENT` blocks, usually several, one per `stage:` (brainstorm, contract, references, options, component).
   - Show the user every file listed under `show_user`. Open images with the Read tool, or give their paths.
   - Answer from this session's context only when the user has already said the answer. You may never approve a contract, reference, option or component on the user's behalf.
   - Otherwise ask the user with AskUserQuestion, using flynn's questions and recommended answers, with the recommended option first, at most 4 questions per call. Split more than 4 across several calls. For a contract or options, ask for approval or changes, and pass on the user's words.
   - Resume the same flynn agent with SendMessage (its agent id): `ALIGNED` plus the numbered answers, quoting the user.
3. **Wait** for `RESULT` or `BLOCKED`. Do not do the design work yourself in parallel. On `BLOCKED`, tell the user the reason and what is needed; if they can supply it, resume flynn with SendMessage.
4. **Deliver.** Show the user the deliverables (links and paths), the final audit verdict and the caveats. If the user corrects anything, send the correction to flynn with SendMessage so it records the lesson. Do not edit flynn's knowledge store yourself.

## When the caller is another agent
Other agents (clu, quorra, or any agent with the Agent tool) call flynn the same way. For example, clu might need a chart for a report, or quorra a mockup for a proposal. If the calling agent cannot ask the user either (it has no AskUserQuestion), it passes flynn's `NEEDS_ALIGNMENT` block up to its own caller unchanged, with its own `run:` line added, and relays the answers back down. Approvals still come only from the user.

Quick questions about the design system ("which table variants does this system have?") may come back directly as a `RESULT`.
