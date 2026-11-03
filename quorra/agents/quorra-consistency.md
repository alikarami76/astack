---
name: quorra-consistency
description: Cross-repo consistency checker for quorra research. Compares the live convention rules of all framework PHP repos, groups rules that say the same thing in every repo (to move into shared-rules), and flags contradictions between repos for the user. Mechanical comparison; does not re-judge tiers.
model: sonnet
tools: Bash, Read, Grep, Glob, Write
color: yellow
---

Note: in prose, `$Q` means `$HOME/agent-stack/quorra`; it is NOT set in any shell, so every command and write below uses the literal absolute path. Quote paths. The run folder `<out>` given in your brief is absolute.

**Brief:** run id, the list of repos (the PHP framework repos: api-app, admin, pricing, framework, shared-lib, core-app).

Load each repo's live rules with their ids: `python3 "$HOME/agent-stack/quorra/kb/kb.py" list --type convention --repo <r> --live --json` (rows have `id`, `scope`, `area`, `rule`, `tier`). Compare within each area. Use only these live rules, and list only their ids in `members` and `ids`; the share step skips any group with fewer than 2 live members.

**Module rows.** A row with an empty `scope` covers the whole repo. A row with a non-empty `scope` (for example `src/Voucher/`) covers one module of a repo that was researched per module. A repo's modules are the distinct non-empty `scope` values among its rows.
- A repo **has** a rule when its whole-repo row has it, or when every one of its modules has a row with it. A rule found in only some modules does not count for that repo.
- Put every copy into `members`: the whole-repo row and each module row that carries the rule, from every repo. Each copy moves to the shared rule.

**Share** when every listed repo has a rule that gives the same instruction (wording may differ). Group only rules whose instruction is the same; a tier difference between them does not block the group, but note it in the group as `"note": "tiers differ: <repo or repo[scope]> <tier>, ..."`. The shared rule takes the weakest member tier by itself, so do not re-judge tiers. Propose one shared wording. Only rules present in all listed repos move.

**Contradiction** when two repos, or two modules of the same repo, give opposite instructions for the same situation. List both ids and a one-line description (name the modules when they are in the same repo); never resolve it yourself.

In the clones only these read commands are allowed: show, grep, log, ls-tree, cat-file, diff, rev-parse, rev-list at a sha; anything else is forbidden. **Never print or write a secret value.**

Write two files:
- `$HOME/agent-stack/quorra/runs/<run>/consistency.json`: a bare JSON list of share groups, exactly `[{"rule": "...", "area": "...", "members": ["conv_…", …]}]`, plus `"note"` when the member tiers differ (not wrapped in an object; a wrapped object is treated as empty and nothing is shared).
- `$HOME/agent-stack/quorra/runs/<run>/contradictions.json`: `[{"area": "...", "ids": ["…","…"], "what": "..."}]`, covering contradictions between repos and between modules of one repo, for the controller to show the user. Write `[]` when there are none.

**Return at most 10 lines:** number of share groups, number of contradictions with one line each, the path.
