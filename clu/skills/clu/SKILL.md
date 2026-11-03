---
name: clu
description: Bring in clu, the data-analyst teammate, from any session - "/clu <question>". Spawns the clu agent as a subagent, relays its alignment questions to the user (or answers them from project context), resumes it, and returns its verified result. Use for any data analysis, metric, sizing, reconciliation or dashboard question.
---

# /clu — work with clu as a teammate

You (the current session) are the caller. clu does the analysis; you relay.

1. **Spawn** clu with the Agent tool, `subagent_type: "clu"`. Give it a full brief, because it does not see this conversation:
   - the user's question, verbatim;
   - relevant context from this session: project folder, files, numbers already discussed, decisions made;
   - `mode: teammate`, `caller_cwd: <cwd>`;
   - the output format the user wants, if known.
2. **Relay alignment.** clu's first reply is usually a `NEEDS_ALIGNMENT` block (see `$HOME/agent-stack/clu/skills/clu-core/references/teammate-protocol.md`):
   - Answer from this session's context only when the answer is certain and already stated by the user.
   - Otherwise ask the user with AskUserQuestion. Use clu's questions and recommended answers, with the recommended option first, at most 4 per call. Also show the user the contract, 15 lines or fewer.
   - Resume the same clu agent with SendMessage (its agent id): `ALIGNED` plus the numbered answers.
3. **Wait** for the `RESULT` (or `BLOCKED`). Do not run the analysis yourself in parallel.
4. **Deliver.** Show the user the answer and headline numbers, open the deliverables, and keep clu's verified/unverified labels. If the user corrects anything, send the correction to clu with SendMessage so it can fix the deliverable and record the lesson. Do not fix it yourself.

If the question is a quick lookup, clu may answer directly with a `RESULT`.
