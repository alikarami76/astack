---
name: clu-review
description: Review, approve or reject clu's pending self-improvement proposals (changes to its agents, skills, scripts or existing skills), and see its recent learning patterns. Use when the user types /clu-review or asks what clu wants to change about itself.
---

# /clu-review

`$CLU` = `$HOME/agent-stack/clu`

1. Run `python3 "$CLU/scripts/proposals.py" list`. If nothing is pending, say so, show the top of `python3 "$CLU/scripts/patterns.py" --since 30`, and stop.
2. For each pending proposal, read it with `proposals.py show <id>` and summarise it in 3 lines: what changes, why (the run and the user quote), and the risk. Show the diff if it is under 40 lines, otherwise the list of files.
3. Ask the user with AskUserQuestion, one question per proposal, at most 4 per call: Approve / Reject / Skip for now.
4. Approve → `proposals.py apply <id>`. Report the commit, or the test failure and the automatic revert. Reject → ask for a one-line reason, then `proposals.py reject <id> --reason "<reason>"`.
5. clu, its skills and `~/.claude/skills` all live in the agent-stack repo. After an approved proposal, tell the user it is committed locally in agent-stack and ask whether to push.
