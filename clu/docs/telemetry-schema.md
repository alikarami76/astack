# Telemetry schema (schema_version 1)

Written by `hooks/telemetry.py` to `telemetry/events.jsonl` (gitignored), one JSON object per line. Only events whose `agent_type` is `clu` or starts with `clu-` are recorded.

Events captured: PostToolUse, PostToolUseFailure, SubagentStart, SubagentStop. These are the only events clu's scripts read (`patterns.py` failures and helper use, `run_tokens.py` transcripts). Until 2026-09-30 it also hooked SessionStart, UserPromptSubmit, PreCompact, Stop and SessionEnd in every session; nothing read them, so they were dropped.

| Field | Meaning |
|---|---|
| schema_version | 1 |
| ts | local ISO time with milliseconds |
| event | hook event name |
| session_id, prompt_id | Claude Code identifiers |
| agent_type, agent_id | clu or clu-* agent identity |
| tool_name, tool_use_id | for tool events |
| duration_ms | if Claude Code supplies it |
| input_chars, output_chars | sizes of tool input and response |
| error | failure text for PostToolUseFailure, redacted and capped at 2000 chars |
| input_hash | sha1 prefix of the tool input (groups repeated calls) |
| input_preview | first 300 characters, secrets redacted |
| prompt_preview | first 2,000 characters of a prompt when the event carries one, secrets redacted |
| transcript_path, cwd | where the full transcript lives |

Join to runs through session_id: run.json does not carry session_id yet, so join by time window and cwd, or add session_id when the run is opened (future improvement).
