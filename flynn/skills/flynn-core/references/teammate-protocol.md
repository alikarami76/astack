# Teammate mode protocol

When flynn runs as a subagent (spawned by `/flynn`, or by any other agent with the Agent tool, such as clu or quorra), it cannot ask the user anything. Every question goes through its caller, using the blocks below. flynn never writes into the caller's project unless the brief explicitly asks. Open the run with `run_record.py new ... --mode teammate --caller-cwd <cwd from the brief>`.

Design needs more alignment than a lookup, so expect several rounds. Each `NEEDS_ALIGNMENT` block names its `stage:`
- `brainstorm`: questions about users, the job, content, constraints and success;
- `contract`: the full design contract, for approval;
- `references`: candidate references, for the user to mark which meet the bar;
- `options`: the finished options, for the user to choose from or give feedback on;
- `component`: a proposed new component spec, for approval.

## NEEDS_ALIGNMENT

```
NEEDS_ALIGNMENT
run: <run folder path>
stage: brainstorm|contract|references|options|component
brief: <the request restated, 10 lines max>
questions:
  1. <question> — recommended: <answer> — because <one line>
  2. ...
show_user:
  - <path to a file or image the user must see to answer, such as contract.md or a screenshot>
assumptions_if_unanswered:
  - <what flynn will assume>
```

The caller answers from its own context only when the user has already said the answer. Otherwise it asks the user and shows the `show_user` files. For the `contract`, `references`, `options` and `component` stages, the user must answer; the caller never approves on the user's behalf. The caller then resumes flynn with SendMessage: `ALIGNED` plus the numbered answers and any changes, quoting the user's words.

## RESULT

```
RESULT
run: <run folder path>
answer: <two sentences, the outcome first>
deliverables:
  - <Figma or Canva URL, or file path> — <what it is>
design_docs:
  - <path to the final flynn document(s)>
audit: <final critic verdict and model, e.g. "pass (opus, round 2)">
stored:
  - <design, lesson, taste and reference ids the curator wrote>
caveats:
  - <at most three, e.g. a drift component used, an interim design>
```

## BLOCKED

```
BLOCKED
run: <run folder path>
reason: <why flynn cannot continue>
needed: <what is required to continue, e.g. Figma library link, Canva sign-in>
```

The caller tells the user the reason and what is needed. If the user can supply it, the caller resumes flynn with SendMessage; the caller does not do the design work itself.
