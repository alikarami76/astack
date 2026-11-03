---
name: quorra-review
description: Review quorra's pending self-improvement proposals (changes to its agents, skills, scripts or research areas) and apply or reject each one. Use when the user types /quorra-review or asks to see what quorra wants to change about itself.
---

`$QUORRA` = `$HOME/agent-stack/quorra`. Expand `$HOME` to your home folder when a tool needs a full path (the Read, Write and Edit tools do).

1. `python3 "$QUORRA/scripts/proposals.py" list`. If empty, say so and stop.
2. For each id: `python3 "$QUORRA/scripts/proposals.py" show <id>`. Summarise for the user in plain words: what changes, why, the risk, how it is tested. Show the diff.
3. Ask the user per proposal: apply, reject (ask for the reason), or skip.
4. Apply: `python3 "$QUORRA/scripts/proposals.py" apply <id>` (runs the tests, reverts on failure, commits, updates `docs/changelog.md`). Report the commit sha or the failure verbatim.
5. Reject: `python3 "$QUORRA/scripts/proposals.py" reject <id> --reason "<the user's reason>"`, then record the reason as a lesson: `python3 "$QUORRA/kb/kb.py" add lesson --json '{"rule": "<what not to propose>", "why": "<reason>", "applies_to": "proposals"}'`.
