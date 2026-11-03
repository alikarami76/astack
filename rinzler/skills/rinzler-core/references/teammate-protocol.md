# Teammate mode protocol

When Rinzler runs as a subagent (spawned by `/rinzler` or another agent), it cannot ask the owner anything. Every question goes through its caller, using the blocks below. Rinzler never writes into the caller's project unless the brief explicitly asks. Open the run with `run_record.py new ... --mode teammate --caller-cwd <cwd from the brief>`.

## First reply: NEEDS_ALIGNMENT (unless the request is a one-line answer)

```
NEEDS_ALIGNMENT
run: <run folder path>
request: <the request restated, 10 lines max>
questions:
  1. <question> — recommended: <answer> — because <one line>
  2. ...
assumptions_if_unanswered:
  - <what Rinzler will assume>
```

The caller answers from its own context or asks the owner, then resumes Rinzler with SendMessage: `ALIGNED` plus the numbered answers.

## NEEDS_APPROVAL (before any gated action)

```
NEEDS_APPROVAL
run: <run folder path>
action: <file a Jira bug | load test on demo | production read | start backend stack | ...>
exactly: <the bug draft path and its fingerprint, or the target URL and load profile>
risk: <what could go wrong, one line>
```

Only the owner's own explicit answer counts. The caller shows him the exact item and passes back his words; Rinzler records them in the run before acting.

## Final reply: RESULT

```
RESULT
run: <run folder path>
verdict: <one or two sentences, answer first>
coverage: <requirements covered / total; cases passed, failed, not run>
findings:
  - <bug or observation> — <severity> — verified|observed|inferred — <evidence path>
deliverables:
  - <path>
needs_owner:
  - <approvals, logins, answers still needed>
```

## BLOCKED

```
BLOCKED
run: <run folder path>
reason: <why Rinzler cannot continue>
needed: <what is required: a login done by the owner, Jira credentials for your-jira, a PRD, VPN, an approval>
```
