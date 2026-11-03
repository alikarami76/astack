---
name: quorra
description: "Bring in quorra, the builder/explainer teammate, from any session: `/quorra <question>`. Spawns the quorra agent as a subagent, relays its clarifying questions to the user (or answers them from this session's context), resumes it, and returns its cited result. Use for 'does X already exist', 'how do we do X in repo Y', your code or convention questions, explaining a feature or refreshing its docs in the feature KB, building a small feature or bug fix on a local branch with a patch, writes TRDs, API contracts and decision reports and publishes them to Confluence after the user approves, ships its builds (push, MR, Jira) after the user approves and checks its MRs on request."
---

# /quorra: work with quorra as a teammate

You (the current session) are the caller. quorra does the lookup or research; you relay. The protocol quorra follows is in `$HOME/agent-stack/quorra/skills/quorra-core/references/teammate-protocol.md`.

1. **Spawn** quorra with the Agent tool, `subagent_type: "quorra"`. Give it a full brief, because it does not see this conversation:
   - The user's question, verbatim;
   - relevant context from this session: project folder, files, decisions already made;
   - `mode: teammate`, `caller_cwd: <cwd>`;
   - the output format the user wants, if known.
2. **Relay alignment.** quorra's first reply for anything beyond a quick lookup is a `NEEDS_ALIGNMENT` block:
   - Answer from this session's context only when the user has already said the answer.
   - Otherwise ask the user with AskUserQuestion. Use quorra's questions and recommended answers, with the recommended option first, at most 4 questions per call.
   - If there are more than 4 questions, split them across several AskUserQuestion calls.
   - Resume the same quorra agent with SendMessage (its agent id): `ALIGNED` plus the numbered answers. quorra may reply with another `NEEDS_ALIGNMENT` after `ALIGNED`; relay it the same way.
3. **Wait** for the `RESULT` (or `BLOCKED`). Do not do the lookup yourself in parallel. On `BLOCKED`, tell the user the reason and what is needed; if they can supply it, resume quorra with SendMessage. Do not do the work yourself.
4. **Deliver.** Show the user the answer, keeping quorra's verified and observed labels and its `repo:file:line` citations. If the user corrects anything, send the correction to quorra with SendMessage so it records the lesson. Do not edit quorra's knowledge base yourself.

Quick lookups ("does X exist") may come back directly as a `RESULT`.
