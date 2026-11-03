# Teammate mode protocol

When clu runs as a subagent (spawned by `/clu` or by another agent), it cannot ask the user anything. It talks through its caller using two blocks.

## First reply: NEEDS_ALIGNMENT (unless the request is a lookup)

```
NEEDS_ALIGNMENT
run: <run folder path>
contract:
  <the filled contract, 15 lines max>
questions:
  1. <question> — recommended: <answer> — because <one line>
  2. ...
challenger_objections:
  - <unresolved objection, if any>
```

The caller answers from its own project context or asks the user. It then resumes clu with SendMessage: `ALIGNED` plus the numbered answers and any changes. clu updates the contract, sets contract_confirmed_at and continues.

## Final reply: RESULT

```
RESULT
run: <run folder path>
answer: <two sentences, data first>
headline_numbers:
  - <name>: <value> <unit> <time unit> — verified|unverified
deliverables:
  - <path or URL>
caveats:
  - <max three>
```

If blocked (access denied, source down), reply `BLOCKED` with the reason and what is needed.
