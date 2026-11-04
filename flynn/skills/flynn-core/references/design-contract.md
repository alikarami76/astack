# Design contract template

Copy this into `<run>/contract.md` and fill in every section. A section that does not apply says "n/a" and why. The user approves the contract before anything is built. `references/ux-thinking.md` explains the job story, flow map, state map and worst-case table, and the principles the open decisions are judged by.

**Which sections apply to what.** A screen or a multi-screen flow fills every section. A report, newsletter or release page fills the job story (the reader's job), a short flow (where the reader comes from and what they should do after reading), worst-case content (longest headline, biggest and smallest numbers, missing data) and the open decisions; its state map is usually "n/a: static page" unless it has a live or interactive part. A deck or a single slide or graphic fills the job story and the open decisions, and writes "n/a" with the reason for the flow, the state map and worst-case content unless they apply.

```markdown
# Design contract: <name>

Run: <run id> · System: <system> · Kind: screen | slide | deck | graphic | chart · Language: fa (rtl) | en (ltr)

## Purpose
<One sentence: the one job this does. For a deck, the single takeaway in one full sentence.>

## Job story
<When <situation>, I want to <motivation>, so I can <expected outcome>. It names the moment and the outcome, not the persona.>

## Audience and situation
<Who uses it, how often, on what device, and what they are trying to get done. Name the personas from ux-thinking.md the critic should walk it as.>

## Flow
- Arrives from: <every entry point: menu item, notification, a link or button on another page, a direct URL>
- Does here: <the steps on this screen, in order>
- On success: <what the user sees, and where they can go next>
- On failure: <what the user sees, what is kept, and how they recover>
- Goes next: <the screen or action that usually follows>

## Primary action
<The one thing the user should do next. For a deck: what the audience should decide or believe.>

## Content
<The real fields, copy and data, in the design's language, at their real volume.>

## Worst-case content
| Case | Real example for this design |
|---|---|
| Longest name or title | <for example the longest product title in the catalogue> |
| Most rows or items | <for example 500 products, and how pagination handles it> |
| Zero rows or items | <which empty state shows> |
| Missing or optional fields | <what shows when a value is missing> |
| Largest and smallest numbers | <for example 2,450,000,000 and 0> |
| Mixed scripts and digits | <SKUs, codes, Latin brand names next to local-script text> |
<Add rows for anything else this design must survive. At least one frame is built from this table.>

## Required states
| State | What triggers it | What the user sees | What they can do |
|---|---|---|---|
| Loading | <first load, a page change, a refresh> | <a skeleton shaped like the final layout, not a spinner, for content> | |
| Empty, first use | <the user has never created one> | <what will be here and why it matters> | <the action that starts it> |
| Empty, no results | <filters or search match nothing> | <a message that says so> | <clear filters> |
| Partial data | <some values missing or still computing> | | |
| Error | <what can fail> | <what failed, in plain words> | <how to recover; what the user typed is kept> |
| Busy, in progress | <saving, a long job running> | <a busy button or progress> | <whether they can leave> |
| Success | <the action completed> | <confirmation in the action's own verb> | <the next step> |
| Undo window | <a reversible destructive action> | <the undo offer and how long it lasts> | <undo> |
| No permission | <the user lacks access> | <what they cannot do and why> | <how to get access> |
<Add any other state this design has, such as disabled, read-only or the module switched off. Write "n/a" with a reason for a state that cannot occur, for example on a slide.>

## Structure
<For a deck: a story outline, one line per slide, each headline a full-sentence conclusion with the evidence it needs. For a screen: the regions in priority order.>

## Library
- Components: <names and tiers from recall.md; flag drift, inferred and proposed>
- Patterns: <names>
- Missing (needs a component proposal): <names, or none>

## Decisions the system leaves open
<What this design chooses where the system does not decide, each with a one-line reason.>
- Information architecture: <how content is grouped and ordered, and why: by task or by object; one page or steps>
- Primary action: <which one, and what is deliberately secondary>
- Density: <compact or relaxed, and for which persona>
- Components chosen for the job: <for example "Popup, not SheetsPopup: desktop-only task"; "a table, not cards: users compare rows">
- Copy: <the key labels and messages, and the verb that stays the same through the flow>
- Overrides of a Reflex principle in ux-thinking.md: <the principle and the reason, or none>

## References (approved by the user)
- <reference id> — <image path> — <the quality to learn from it: hierarchy, density, state handling>

## Viewports
<For example 1440x900 desktop and 360x800 mobile, or 1920x1080 for slides.>

## Reject
<Patterns and looks to avoid for this design, beyond the rubric: things the user has disliked, and any competitor look to stay clear of.>

## Destination
<Figma file or page, Canva, or files; and who edits it afterwards.>
```
