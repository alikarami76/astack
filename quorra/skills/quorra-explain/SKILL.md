---
name: quorra-explain
description: quorra's explain and KB-upkeep protocol. Explain how a product feature works into the feature knowledge base (features/<feature>/, the feature-explainer standard), refresh a feature's docs when code changed, or correct the docs after quorra's own work found different behaviour. Uses quorra's index and concept map for discovery, re-verifies only stale citations, and hands every change to the user as a local branch commit.
---

# quorra explain and KB upkeep

`$QUORRA` = `$HOME/agent-stack/quorra`; `$KB` = `${QUORRA_FEATURE_KB:-$HOME/feature-knowledge-base}`, the git repo that holds your feature docs. Substitute the literal paths and quote them. You are the controller: agents do the reading, you do the judgment and the writing. Every agent writes to `$RUN/kb/<feature>/` and returns at most 15 lines.

## The standard
The feature-explainer standard holds: exhaustive, not representative; every number traced to declared, computed or hardcoded; gaps labelled, never filled; corrections stated in public; navigable by an AI. If you have a feature-explainer skill installed, read it once per session for depth and tone. The discovery, tracing, schema, QC and consistency agents named below come from that skill set; if you do not have them, do the same work yourself with read-only git commands. Code references are `repo:path:line` at the pinned sha. Keep verified / likely / unverified labels.

## 0. Open the run
`RUN=$(python3 "$QUORRA/scripts/run_record.py" new <explain|refresh>-<feature> --type <explain|refresh> --request "<the user's words>" --repos <r1,r2>)`. Run `python3 "$QUORRA/scripts/index_repo.py" <repo> --fetch` for each repo the feature touches. Note each repo's indexed sha: these are the pinned shas for the whole run.

## 1. Which mode
- **explain** (no `features/<feature>/` yet): steps 2-6.
- **refresh** ("refresh X", or you touched X during other work): step 2R, then 4-6 for what it flags.
- **update-after-work** (your investigation or build found behaviour the docs state differently): edit only those sections, then 5-6.

## 2. Discovery (explain)
1. `python3 "$QUORRA/scripts/lookup.py" "<name>" --json` with the English name, the Persian name, any codename, and a plain description; add `--kind` for tables, routes, console commands and queues. `python3 "$QUORRA/kb/kb.py" search "<name>" --type concept` for known implementations.
2. Label each hit's repo: owns the core logic / consumer / admin-reporting surface / customer-facing surface.
3. Spawn `discovery-scout` (haiku) only for an axis that returned nothing, or for a repo quorra does not index. Say in the brief which axes the index already covered.
4. Plan the components (scoring factor, config mechanism, outcome storage, audit trail, API surface) and spawn one `component-tracer` (sonnet) per component, in parallel, with the pinned shas and the clone paths from `$QUORRA/config/repos.json`. Tracers use only git show / grep / ls-tree / log at the sha.
5. Schema: for a table whose entity the index has, transcribe it yourself from `git show <sha>:<entity file>`. Spawn `schema-scraper` (haiku) only for tables the index cannot place.

## 2R. What went stale (refresh)
`python3 "$QUORRA/scripts/kb_cite.py" stale <feature> --out "$RUN/kb/<feature>/stale.json"`. `stale` exits 1 whenever anything is not `unchanged` (including `unresolved` and `no_baseline`); that is a finding, not a crash (exit 2 is an error, such as a missing feature folder). A doc's own footer is its baseline. A doc without a footer has no baseline: `--sha <repo>=<sha>` fills it in, but only for repos the doc's footer does not name (a footer always wins). Find the commit the doc was written against per doc: the date is `git -C "$KB" log -1 --format=%cI -- <doc>`, then `git -C "<clone>" rev-list -1 --before=<date> <indexed sha>` with the repo's indexed sha as the explicit ref. Or treat all its citations as `changed`. Each item carries `resolved_by` (`path`, `basename` or `class`); a `basename` or `class` resolution is a guess at which file was meant, so verify the file before you change a line number on its say-so. A footer sha that cannot be found gives `unresolved` with reason `baseline sha not found` for that doc. Then:
- `unchanged`: leave the claim alone.
- `moved`: update the line number only.
- `changed`: spawn `component-tracer` on the component the claim belongs to, with the old and new line.
- `gone`: the class, method or file was removed (the result names the commit). Re-trace the whole component it served, starting from that commit's diff.
- `unresolved`: read the citation yourself; fix the citation form or mark it unverified.
Group the flagged citations by component so each tracer gets one component, not one line.

## 3. Write (explain)
Write `features/<feature>/` in the numbered structure of the existing features: `00-README.md` (summary, reading order, open questions, defect list), `01-Overview.md`, the flow docs, `0A-Database/` (one file per table group plus `README.md`), `0B-Who-Sees-What.md`, `0C-Code-Architecture-Map.md`, `0Z-Configuration-and-Constants.md`. Look at a finished feature in the KB for the shape. For a large feature, spawn editors (sonnet) per doc group with a written brief that makes the tracer files the only source of new claims.

## 4. Correct (refresh and update-after-work)
Edit in place. Every corrected claim is stated as a correction, in this form: "**Corrected <date>:** this used to be X; since commit <sha> (<date>) it is Y (`repo:path:line`)." Keep the old text of a known defect and mark it "fixed on <date> by <commit>" or "still present at <sha>". Never write a secret value, and never write the name or value of anything that works as a credential (keys, tokens, bypass headers); cite it by `repo:path:line` only.

## 5. QC
- One `qc-verifier` (sonnet) per edited doc, or per small group of related docs, in parallel. Its brief names the pinned shas, the read-only commands, and says to focus on every correction and every new section.
- One `consistency-checker` (haiku) over the whole `features/<feature>/` folder.
- Send each editor the QC files for its docs (one fix round), then one scoped re-check per half of the docs. Fix the small residue yourself. A verifier that cannot write its file returns the findings in its reply: save them to `$RUN/kb/<feature>/qc-<doc>.md` yourself.
- Stamp every edited doc: `python3 "$QUORRA/scripts/kb_cite.py" stamp "$KB/features/<feature>/<doc>.md" --repo <repo>=<sha> ...` with the pinned shas.

## 6. Hand off
1. `python3 "$QUORRA/scripts/kb_git.py" branch <feature>` (it refuses if the KB has unrelated tracked changes; untracked files are fine).
2. `python3 "$QUORRA/scripts/kb_git.py" commit <feature> --message "<feature>: <what changed and why>"`. It refuses unless you are on `quorra/<feature>-...`. After a successful commit the KB is back on the branch it was on before `branch` (the result says so; if it says `stayed`, tell the user the KB is still on the quorra branch). If it reports possible secrets, remove them. Use `--accept <path:line>` only for a line you read and confirmed is not a secret.
3. `python3 "$QUORRA/scripts/kb_git.py" summary --branch <the branch printed in step 1>` (without `--branch` it picks the newest `quorra/` branch), and show the user the files, the headings touched and the findings that matter (new defects first).
4. Ask the user whether to push, and push only after the user says yes, in this conversation, every time: a yes covers that one push of that one branch. The branch must be `quorra/<feature>-<YYYY-MM-DD>`. Push with `git -C "$KB" push -u origin <branch>`. Never push to main; never merge.
   In teammate mode (you were spawned by another agent or session) you never push: return the branch, the summary and the exact push command in your RESULT for the caller to show the user, because a relayed yes is not the user's. Writing to the KB repo under `features/<feature>/` is the one exception to the teammate rule "never write into the caller's project", and only when the brief asks for explain or refresh.
5. Learn: new names (en, fa, codename) become concept rows (quorra-core §4; Persian names confirmed with the user). The user's corrections become lessons. Close the run with the counts: citations checked, stale by status, agents and tokens.
