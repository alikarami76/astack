# Teammate mode protocol

When quorra runs as a subagent (spawned by `/quorra` or by another agent), it cannot ask the user anything. Every question goes through its caller, using the blocks below. quorra never writes into the caller's project unless the brief explicitly asks. Open the run with `run_record.py new ... --mode teammate --caller-cwd <cwd from the brief>`.

## First reply: NEEDS_ALIGNMENT (unless the request is a one-line lookup)

```
NEEDS_ALIGNMENT
run: <run folder path>
question: <the request restated, 10 lines max>
questions:
  1. <question> — recommended: <answer> — because <one line>
  2. ...
assumptions_if_unanswered:
  - <what quorra will assume>
```

The caller answers from its own context or asks the user, then resumes quorra with SendMessage: `ALIGNED` plus the numbered answers and any changes.

## Final reply: RESULT

```
RESULT
run: <run folder path>
answer: <two sentences, answer first>
findings:
  - <repo:file:line> — <what it is> — verified|observed
reuse_verdict: <reuse | extend | nothing-found, with the phrasings searched>   (when relevant)
deliverables:
  - <path or URL>
caveats:
  - <max three>
```

`verified` means quorra read the code at a known commit. `observed` means a lookup hit that quorra did not open.

## BLOCKED

```
BLOCKED
run: <run folder path>
reason: <why quorra cannot continue>
needed: <what is required to continue>
```

The caller tells the user the reason and what is needed. If the user can supply it, the caller resumes quorra with SendMessage; the caller does not do the work itself.
