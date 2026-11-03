---
name: rinzler-bugs
description: Rinzler's bug protocol - find bugs by exploration and scripted Playwright runs on the local web-app build, capture evidence (screenshots, console and network logs, traces), draft the bug with steps, expected vs actual, severity and priority rationale, environment and linked requirement, have rinzler-verifier try to refute it, search the KB and Jira for duplicates, show the owner the exact draft, and file it through the your-jira tool (jira_tool.py) only after his explicit approval (or hand him a ready-to-paste issue). Load when testing for bugs or when a bug must be written up or filed.
---

# Bugs: find, evidence, draft, verify, approve, file

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted).

## Find
- Scripted: Playwright specs in Rinzler's suites, run on the local build: `RINZLER_SUITES=<folder> RINZLER_E2E_OUT=<run>/e2e bash "$RINZLER/scripts/local/app.sh" e2e --grep "<pattern>"`. Specs import `test`, `expect`, `stubApi`, `checkRtl`, `checkA11y` from `../evidence` (`$RINZLER/playwright/evidence.ts`): auth is stubbed, so no login is needed. Prefer user-facing locators (role, label, the Persian text) and web-first assertions; no fixed sleeps.
- Exploratory: rinzler-explorer runs the plan's charters and writes session notes.
- Simulation: rinzler-data finds calculation bugs before any screen is opened.

## Evidence (every bug)
Exact steps and input values, environment and build commit, the evidence fixture's console and network logs, screenshot, trace (`npx playwright show-trace`), or the script output. Redact with `python3 "$RINZLER/scripts/safety.py" redact <file>`. Never attach `.env*`, `.npmrc`, cookies or storage state.

## Draft
Write `<run>/bugs/<slug>.yaml` with: `title` (what is wrong, where, under which condition), `feature`, `feature_fa`, `component`, `requirement_id`, `environment` (`name`, `url`, `build`, `browser`), `preconditions`, `steps` (two or more), `expected` (with the requirement it comes from), `actual`, `frequency` (always, or n of m), `impact`, `workaround`, `severity` S1-S4 with `severity_rationale`, `priority` P1-P4 with `priority_rationale` (see `$RINZLER/skills/rinzler-core/references/severity.md`), `evidence` (paths), `notes`. Then:
`python3 "$RINZLER/scripts/bug_draft.py" new <run>/bugs/<slug>.yaml --out-dir <run>/bugs`. It refuses vague titles, missing fields, production environments and missing or secret evidence, redacts every field, and records a KB row (status draft).

## Verify (never skipped)
Spawn rinzler-verifier with the draft path, the requirements file and the environment, without your reasoning. It re-runs the repro by another route, checks the oracle (is this really required, or documented behaviour?), the evidence and the severity, and records `sustained` or `refuted` with `bug_draft.py verdict`. A refuted bug is not shown for filing; tell the owner what was refuted and why.

## Duplicates
`bug_draft.py dedupe <bug.json>` searches Rinzler's KB and prints JQL queries. Run each with `python3 ~/.claude/skills/your-jira/scripts/jira_tool.py search --jql "<JQL>" --limit 10` (the your-jira skill; it reads its own credentials, never read or print them). If the tool reports missing credentials, report BLOCKED: the owner must set `JIRA_USER` and `JIRA_TOKEN` (or `~/.claude/secrets/jira.env`, see `~/.claude/skills/your-jira/SKILL.md`), or you note "Jira not searched" in the draft. Read the top results; a likely duplicate means you propose a comment on the existing ticket instead of a new one.

## Approve (the owner, explicitly)
`bug_draft.py show <bug.json>` prints the draft with its fingerprint. Show it to the owner in full and ask: "File this in Jira as is?" Only his explicit yes to this draft counts; a general "go ahead" earlier does not. Then `bug_draft.py approve <bug.json> --fingerprint <the fingerprint he saw>`. If anything in the draft changes afterwards, the fingerprint changes and he must approve again.

## File
After the owner's explicit yes and `approve`, run `bug_draft.py payload <bug.json>`. It writes the Jira-formatted description to a file and prints the exact `jira_tool.py create` command in its `cli` field (project from `$RINZLER/config/jira.json`, issue type Bug, summary, description, labels). Run that command with `--dry-run` appended first and check the output, then run it without `--dry-run`. Do not retype or change the fields. Attach evidence by hand (the tool cannot). Then `bug_draft.py filed <bug.json> --key <KEY-123>`. If the tool reports missing credentials, report BLOCKED and tell the owner how to set them (as above), or give him `bug_draft.py paste <bug.json>` to paste himself. Never change an existing ticket (`jira_tool.py update`, `comment`, `transition`) without the same kind of explicit approval.
