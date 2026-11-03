---
name: rinzler
description: "Bring in Rinzler, the QA teammate for your product, from any session: `/rinzler <request>`. Spawns the rinzler agent, relays its alignment questions to the user (or answers them from this session's context), resumes it, and returns its result. Use for a test plan from a feature or PRD, functional or AI-only evals, exploratory or Playwright testing on the local web-app build, rule simulations and reconciliation, performance checks, and drafting, verifying and (after approval) filing bugs."
---

# /rinzler: work with Rinzler as a teammate

You (the current session) are the caller. Rinzler does the QA work; you relay. The protocol Rinzler follows is in `$HOME/agent-stack/rinzler/skills/rinzler-core/references/teammate-protocol.md`.

1. **Spawn** Rinzler with the Agent tool, `subagent_type: "rinzler"`. Give it a full brief, because it does not see this conversation:
   - the user's request, verbatim;
   - the feature, PRD path, ticket or build it concerns, and anything already decided;
   - `mode: teammate`, `caller_cwd: <cwd>`;
   - the deliverable the user wants (plan, eval results, bug drafts, perf numbers), if known.
2. **Relay alignment.** Rinzler's first reply for anything beyond a quick answer is a `NEEDS_ALIGNMENT` block. Answer from this session's context only what the user has already said; otherwise ask the user with AskUserQuestion, using Rinzler's questions and recommended answers (recommended first, at most 4 per call). Resume the same agent with SendMessage: `ALIGNED` plus the numbered answers.
3. **Approvals are the user's, never yours.** When Rinzler returns `NEEDS_APPROVAL` (a bug draft to file in Jira, a load test on a shared environment, any production read), show the user the exact draft or plan and its fingerprint, and pass on only the user's own explicit answer. Do not approve on the user's behalf, and do not treat an earlier general "go ahead" as approval of a specific draft.
4. **Wait** for the `RESULT` (or `BLOCKED`). Do not do the testing yourself in parallel. On `BLOCKED` (for example the user must log in by hand, or set the your-jira credentials), tell the user exactly what is needed and resume Rinzler when it is done.
5. **Deliver.** Show the verdict, the evidence paths and what was not run. If the user corrects anything, send the correction to Rinzler with SendMessage so it records the lesson.
