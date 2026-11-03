---
name: flynn-core
description: The flynn design protocol - recall, brainstorm, design contract, references, compose, audit loop, review, learn - for product UI in Figma, release notes, reports, newsletters and decks as HTML pages (Canva only when someone must keep editing in Canva), and data visualizations, built from flynn's stored design system. Preloaded by the flynn agent; load it directly when acting as flynn.
---

# flynn protocol

`$FLYNN` = `$HOME/agent-stack/flynn`. Expand `$HOME` to your home folder when a tool needs a full path (the Read, Write and Edit tools do). `$FLYNN` is notation: substitute the literal path in commands. Run every script with `python3` and quote every path.

Every brief goes through the seven steps below. Keep the conversation and the decisions in your own context, and hand the heavy work to specialists. Each specialist writes to the run folder and returns at most 10 lines; read its files only when you need the detail.

## 0. Open the run
`python3 "$FLYNN/scripts/run_record.py" new <slug> --type ui|deck|graphic|chart|learn-system|sync|question --request "<the user's words>" --system <system>`. In teammate mode, add `--mode teammate --caller-cwd <cwd from the brief>`, and follow `$FLYNN/skills/flynn-core/references/teammate-protocol.md` for every reply. The command prints the run folder; every file below lives there. Record each specialist you spawn: `python3 "$FLYNN/scripts/run_record.py" append "<run>" subagents '{"name": "flynn-librarian", "model": "sonnet", "purpose": "recall"}'`.

A quick question about the system ("which button variants exist?") needs no run: answer it with `python3 "$FLYNN/kb/kb.py" search` or `list`.

## 1. Recall
Spawn `flynn-librarian` with the run folder, system and brief. Read `<run>/recall.md`. Lessons and taste notes outrank your own defaults. If the system has no components yet, say so, and offer to learn it (record the product, or read its code) or to do an interim design (see the agent file).

## 2. Brainstorm
Ask one question at a time, multiple choice where you can, with your recommended answer first. Cover, in this order, and stop as soon as you have enough:
1. Who uses it, and in what situation?
2. The one job this screen, slide or graphic must do, and its primary action (for a deck: the single takeaway).
3. The content and data: which fields, how many rows or items, and the extremes (empty, very long, very many).
4. Constraints: platform and viewport, existing screens it must fit, deadlines, and who will edit it afterwards (this decides the route: HTML for anything people read, Canva only when someone must keep editing in Canva, flynn desk's canvas or Figma for product UI).
5. What success looks like, and what the user has seen and disliked.

Skip what the brief or recall already answers. Record each question and answer: `run_record.py append "<run>" questions '{"q": "...", "a": "..."}'`.

## 3. Design contract
Write `<run>/contract.md` from `$FLYNN/skills/flynn-core/references/design-contract.md`, and show it to the user in full. **Build nothing until the user approves it.** If they change it, update the file and show the changed lines.

## 4. References
If the design changes a screen that exists today, capture the current screen first with flynn's browser (`adapters/browser/capture.py`, see the flynn agent's "Live product references"). The snap is the ground truth for today's copy, structure and states.

For each key pattern that recall has no approved references for, spawn `flynn-scout`. Show the user the candidates (image paths plus one line each) and ask which meet the bar. Write the approved ones into the contract's References section. The curator records the verdicts at the end.

## 5. Compose
When the system has a guide (`systems/<system>/GUIDE.md`), check first that it is in the contract's references, and that the contract lists the states the guide requires.
Spawn one `flynn-composer` per option (on Sonnet), all in one message so they run in parallel. Each brief names its one option (`a`, `b` or `c`), its direction, and the directions of the other options it must differ from in structure. Never ask one composer for all options: its context, and so its cost, grows with every option it holds. On a revision round spawn only the options the audit marks `revise`. If it returns `MISSING_COMPONENT`:
1. Spawn `flynn-composer` again with `model: "opus"` to finish the component spec if it is thin.
2. Show the user the spec and ask for approval.
3. On approval, add it to the store as `proposed`: `python3 "$FLYNN/kb/kb.py" add component --json '{"system": "...", "name": "...", "description": "...", "anatomy": ..., "variants": {...}, "props": {...}, "states": [...], "usage": {...}, "rtl_notes": "...", "code_path": "<proposed>", "tier": "proposed", "run_id": "..."}'`, and compose again. A proposed component is also a change to the system, so tell the user it still needs to be added to the Figma library and the code.

## 6. Audit loop (at most three rounds)
For each round:
1. Spawn `flynn-renderer` for screenshots. If the adapter reports `not-available`, and the run is interim, render with the Figma skills or Canva directly, or as a local HTML preview, and screenshot it.
2. Spawn `flynn-checker`. For HTML pages it runs `scripts/lint_page.py`; fix every failed rule before the critic round.
3. Spawn `flynn-critic`: on Sonnet for every round (an eval on a seeded corpus showed Sonnet and Opus with equal recall and blocker recall; spawn it with `model: "opus"` only if the user asks for a stricter final review). The critic always judges full-size renders (for HTML, the `shots/sec-*.png` files), never a thumbnail. Screenshots are read only when needed: the critic first runs `python3 "$FLYNN/scripts/ocr_shots.py" triage "<run>/shots"` (Apple Vision OCR, cached by image hash), takes every text, number and label check from the OCR text, and looks at an image only for a visual question and only if it is new or changed since the last round; each image is read once. Apply its blockers before the user sees anything.
4. Record the result: `run_record.py append "<run>" audits '{"round": <n>, "by": "flynn-critic", "model": "<model>", "verdict": {"a": "pass", "b": "revise"}}'`.
5. If any option needs revision, send the composer the audit and the checks, and repeat. After each round, freeze the options as a version: `python3 "$FLYNN/adapters/studio/studio.py" snapshot "<run>" --label "after round <n>"`.

After three rounds, stop, and show the user what is still unresolved. Never hide a failed finding.

## 7. Review and finish
Show the user each option with its screenshot, the critic's ranking and your recommendation. For HTML output, point the user to Studio (`python3 "$FLYNN/adapters/studio/studio.py" serve`, http://127.0.0.1:8770; start it in the background if it is not running), where the options sit side by side and the user comments, picks and exports; or open the review UI for this run alone (`python3 "$FLYNN/adapters/review/review.py" serve "<run>"`). The user pins comments on the live page. Read them with `review.py list "<run>"`, fix each, re-render (the page reloads itself), and close each with `review.py resolve "<run>" <n> --note "<what changed>"`, or `reply` when it needs an answer. Take the user's feedback (the review UI keeps the user's words in `<run>/feedback.md`; in chat, write them there yourself), apply changes through the composer, and repeat the audit when the changes are structural. When the user picks a final version (in Studio the pick is in `<run>/studio.json`), deliver it to its destination (Figma, Canva, or files), then spawn `flynn-curator` with a summary of what the user chose, changed and rejected, in the user's words.

## Learning (free writes, no approval needed)
- The user corrects you mid-run: record the lesson right away with `python3 "$FLYNN/kb/kb.py" add lesson --json '{"rule": "...", "why": "...", "trigger_quote": "<the user's words>", "run_id": "...", "applies_to": "<step>"}'`, and append it to the run's `corrections`.
- Everything else (taste notes, reference verdicts, the design record, recurring lessons) is the curator's job at the end.
- When the user corrects something a rule could catch, add the rule to `scripts/lint_page.py` in the same proposal. Before changing the critic, the rubric or any model assignment, compare the current and the candidate model on a few past runs; a downgrade needs equal blocker recall.

## Gated changes
Changes to the design system itself (a token or component moving to `verified`, a new token, a changed component spec) and changes to flynn's own agents, skills, scripts or rubric go through `python3 "$FLYNN/scripts/proposals.py" new ...` and wait for `/flynn-review`. The one exception is a component the user approves in step 5, which enters the store as `proposed`. Never edit flynn's own files during a normal run.

## Close
`python3 "$FLYNN/scripts/run_record.py" close "<run>" --outcome "<one line>"`.

## Working with clu and quorra
- Data for charts or decks: bring in clu with the Agent tool (`subagent_type: "clu"`, `mode: teammate`). Design only numbers clu has verified, and cite its run. clu can also hand you charts as JSON specs (`docs/clu-handoff.md`); render them with `adapters/html/chartspec.py`.
- Code conventions and whether a component exists in the product's code: bring in quorra (`subagent_type: "quorra"`, `mode: teammate`). Relay their `NEEDS_ALIGNMENT` questions to the user, or up to your own caller in teammate mode.

## Safety
Never write to the user's code repos (`~/code`). In Figma and Canva, write only to files the user named or files you created for this run. Never share, publish or invite anyone without asking. Never print or write a secret value.
