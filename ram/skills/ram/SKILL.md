---
name: ram
description: Bring in ram, the Claude Code usage analyst, from any session - "/ram weekly", "/ram review", or "/ram <question>" such as "why was yesterday's KB session so expensive" or "should code-tracer run on Sonnet". Spawns the ram agent, relays its proposals to the user for approval, and returns its result.
---

# /ram — usage reviews and proposals

You (the current session) are the caller; ram does the work.

1. Spawn ram with the Agent tool, `subagent_type: "ram"`. Brief it with the mode and the user's words verbatim: `weekly`, `review`, or `ask <question>`, plus any session IDs or projects mentioned in this conversation.
2. `weekly` and `ask`: show the user ram's answer and open the PDF it returns (`open <path>`).
3. `review`: ram returns numbered pending proposals. Ask the user with AskUserQuestion, at most 4 proposals per call, options Approve / Reject (with a reason in notes). Resume ram with SendMessage: `DECISIONS` and one line per proposal (`<id> approve` or `<id> reject: <reason>`). Relay what ram applied and any diffs it wants the user to apply by hand, and open the review PDF.
4. If the user disagrees with a finding or a model call, send it to ram so it records the lesson. Do not fix ram's files yourself.
