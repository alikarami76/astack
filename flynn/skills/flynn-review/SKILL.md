---
name: flynn-review
description: Review flynn's pending proposals (changes to the design system, or to flynn's own agents, skills, scripts or rubric) and apply or reject each one. Use when the user types /flynn-review or asks to see what flynn wants to change.
---

`$FLYNN` = `$HOME/agent-stack/flynn`. Expand `$HOME` to your home folder when a tool needs a full path (the Read, Write and Edit tools do).

1. `python3 "$FLYNN/scripts/proposals.py" list`. If it is empty, say so and stop.
2. For each id, run `python3 "$FLYNN/scripts/proposals.py" show <id>`. Summarise it for the user in plain words: what changes, why (which runs it came from), the risk, and how it is tested. Show the diff.
3. Ask the user for each proposal: apply, reject (and ask for the reason), or skip.
4. Apply: `python3 "$FLYNN/scripts/proposals.py" apply <id>`. This runs the tests, reverts on failure, commits, and updates `docs/changelog.md`. Report the commit sha, or the failure verbatim.
5. Reject: `python3 "$FLYNN/scripts/proposals.py" reject <id> --reason "<the user's reason>"`, then record the reason as a lesson: `python3 "$FLYNN/kb/kb.py" add lesson --json '{"rule": "<what not to propose>", "why": "<reason>", "applies_to": "proposals"}'`.
