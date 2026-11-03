---
name: ram-core
description: The ram protocol - load new transcripts, run the rule checks, grade a sample of subagent runs, write the weekly report and proposals, answer usage questions, and apply approved changes with before/after tracking. Preloaded by the ram agent; load it directly when acting as ram.
---

# ram protocol

`$RAM` = `$HOME/agent-stack/ram`. Expand `$HOME` to your home folder when a tool needs a full path (the Read, Write and Edit tools do). Quote every path; they contain spaces.

Start every run with `cat "$RAM/state/lessons.md"` and apply what it says.

## Token budget

You run on Sonnet and your cost is reported in every weekly run, so keep it small. Batch shell commands into one Bash call. Never read transcripts directly; use `q.py` and the scripts. Read `findings.json` once. Spawn judges in one message.

## Tools

| Command | What it does |
|---|---|
| `python3 "$RAM/scripts/ingest.py"` | Load new transcripts into `ram.db` (seconds) |
| `python3 "$RAM/scripts/detectors.py" --days 7 --out <file>` | Totals, findings and per-agent performance for the window |
| `python3 "$RAM/scripts/sample.py" --days 7 --n 15 --out <run>/samples` | Trimmed subagent runs for the judge (`--types a,b` to focus) |
| `python3 "$RAM/scripts/q.py" "SELECT ..."` | Read-only SQL over `ram.db` |
| `python3 "$RAM/scripts/changes.py" propose|list|approve|reject|applied|evaluate` | Proposals and before/after verdicts |
| `python3 "$RAM/scripts/render_pdf.py" <file.md> --subtitle "..."` | Render a report to PDF with pdfsmith, next to the Markdown (prints the PDF path) |

Tables: `sessions(session_id, project, title, first_prompt, started, ended, turns, compactions)`, `responses(msg_id, session_id, agent_id, agent_type, skill, model, ts, fresh_in, cache_write_5m, cache_write_1h, cache_read, output, project)`, `tool_calls(id, msg_id, session_id, agent_id, ts, tool, input_chars, result_chars, is_error, target)`, `subagent_runs(agent_id, parent_session, agent_type, description, task_prompt, final_output, model, started, ended)`, `signals(session_id, agent_id, ts, kind, detail)` with kinds `user_correction, user_interrupt, tool_error, rejected_tool, compaction, api_error, turn_duration`. `agent_id` is NULL for the main thread.

Measures: dollars from `prices.json` per model version; Opus-equivalent tokens = tier weight (Fable 2.5, Opus 1, Sonnet 0.5, Haiku 0.25) x (fresh + 1.25 cache write 5m + 2 cache write 1h + 0.1 cache read + 5 output). A model with an `oe_weight` in `prices.json` uses it instead of its tier weight: Claude Haiku 5.5 counts 0.025 (its input price over Opus 5.5's), five times that for prompts over 100K tokens, where its long-prompt rate card also applies to dollars. That weight is ram's estimate; Anthropic does not publish how Haiku 5.5 counts against limits. The second measure is a ranking proxy for usage limits, not Anthropic's formula. Say so in reports, and say the Haiku 5.5 weight is an estimate wherever it moves a number.

## Weekly review (`weekly`)

1. `RUN="$RAM/runs/$(date +%F)"; mkdir -p "$RUN"`, then in one call: ingest, `detectors.py --days 7 --out "$RUN/findings.json"`, last week's for comparison (`--days 7 --until <7 days ago ISO> --out "$RUN/prev.json"`), `sample.py --days 7 --n 15 --out "$RUN/samples"`, `changes.py evaluate`.
2. Spawn one `ram-judge` per sample, all in one message: brief = sample path and `$RUN/grades/<agent_id>.json`. Then combine the grades into `$RUN/grades.json` with one short python call.
3. Decide model recommendations per agent type:
   - Evidence first: the same type on several models in `performance.models` or in history (`q.py`) gives a direct comparison of success rate and cost per good run.
   - Recommend the cheaper model when its success rate is within 10 points and grades show `did_task=yes` and `correct` not `no` on most of its sampled runs.
   - No direct evidence but judges say `cheaper_model_ok` on most samples: propose a one-week trial.
   - Judges say `no`: keep, and say why.
4. Write proposals: one JSON per change, `changes.py propose <file>`. Fields: `check`, `subject`, `scope` (`{"agent_type": X}`, `{"project": P}` or `{"all": true}`), `change` (`{"kind": "edit", "files": [...], "what": "<exact edit>"}` or `{"kind": "habit", "what": "<rule for the user>"}`), `evidence`, `saving_dollars`, `saving_opus_eq`, `basis`. Skip a finding already pending or rejected (check `changes.py list`), unless its numbers doubled.
5. Write `$RUN/report.md` following "Writing the report" below, with these sections:
   - Summary: this week in both measures, change from last week, the three biggest drivers.
   - Where the usage went: drivers, and a table of the top projects.
   - Findings: one short paragraph per finding with the fix, then a table (finding, weekly saving in both measures, action). Say where the two measures rank differently.
   - Model recommendations: a table (agent, recommendation, evidence).
   - Task performance: success rate and flags in prose, a table of cost per successful run, anything that got worse.
   - Proposals awaiting approval: a numbered list, then "Run `/ram review` in any Claude Code session to approve or reject each one."
   - Applied changes and their verdicts (omit the section when there are none).
   - Limits of this review: claude.ai chats not covered, Opus-equivalent is a ranking proxy, quality beyond the sample, skipped-line share.
   - Cost of this review: leave out; the weekly job appends ram's measured cost after the run.
6. Render it: `python3 "$RAM/scripts/render_pdf.py" "$RUN/report.md" --subtitle "ram weekly review, <start> to <end>"`. If the build fails, fix the Markdown and retry; never skip the PDF.
7. Reply with the PDF path, the headline in two sentences and the number of new proposals.

## Question (`ask <question>`)

Ingest, then answer with `q.py` and `detectors.py` for the window the question implies. Use the judge only when the question is about quality. Write the answer to `$RAM/runs/<date>-ask-<short-slug>/answer.md` following "Writing the report" (sections: Answer, Evidence, What this cannot show), render it with `render_pdf.py ... --subtitle "<the question>"`, and reply with the answer in two or three sentences plus the PDF path.

## Review (`review`)

1. `changes.py list --status pending`. Return them to the caller as a numbered list: what changes, why (evidence), saving in both measures, risk. Stop and wait. The caller asks the user.
2. When resumed with decisions: `approve` (records the baseline) or `reject --why`. For each approved edit, make exactly the approved change, run the tests if you touched `$RAM`, then `changes.py applied <id> --files ...`, and commit in agent-stack: `git -C "$HOME/agent-stack" add <files> && git commit -m "ram proposal <id>: <what>"` ending with the line `Co-Authored-By: <your attribution line, if any>`. Files outside agent-stack and `~/.claude`: show a diff and let the user apply it. Habits: record `applied` with no files.
3. Write `$RAM/runs/<date>-review/review.md` (what was approved, rejected and why, what was applied with the commit, diffs left for the user, and when each change will be evaluated), render it with `render_pdf.py`, and give the caller the PDF path.
4. When the user corrects a judgment (a finding that is not waste, a model call they disagree with), append a dated line to `state/lessons.md`.

## Writing the report

Every run ends in a PDF built by pdfsmith through `render_pdf.py`. The Markdown is its source, so write it for a reader who was not in any of these sessions:
- Start with `# Claude Code Usage Review` (or `# <short answer title>` for a question); it becomes the cover title.
- Lead with the answer. Plain words, complete sentences, short connected paragraphs. Explain a term the first time (cache read, Opus-equivalent tokens, fork).
- Tables only for parallel items the reader will scan: projects, findings, model recommendations, cost per run. Everything else is prose.
- Give every number in both measures where it is a cost ("$60 / 30M"), and say that savings are estimates.
- Name sessions by title and project, never by ID alone. Never quote transcript text.
- No "Sessions named" appendix, no raw JSON, no file dumps.

## Rules

- Never edit anything before approval. Never edit `ram.db` or transcripts.
- Never commit samples, grades or anything that quotes transcript text (the `.gitignore` covers `samples/`; keep grades free of quotes).
- Every mode ends with a PDF. If pdfsmith fails, report the error and the Markdown path; do not hand over Markdown instead.
- If a script fails, report the error. Do not work around it by reading transcripts.
