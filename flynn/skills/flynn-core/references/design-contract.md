# Design contract template

Copy this into `<run>/contract.md` and fill in every section. A section that does not apply says "n/a" and why. The user approves the contract before anything is built.

```markdown
# Design contract: <name>

Run: <run id> · System: <system> · Kind: screen | slide | deck | graphic | chart · Language: fa (rtl) | en (ltr)

## Purpose
<One sentence: the one job this does. For a deck, the single takeaway in one full sentence.>

## Audience and situation
<Who uses it, how often, on what device, and what they are trying to get done.>

## Primary action
<The one thing the user should do next. For a deck: what the audience should decide or believe.>

## Content
<The real fields, copy and data, in the design's language. The volume and the extremes: longest name, most rows, zero rows.>

## Required states
- Loading: <what is shown while data loads (skeleton, not a spinner, for content)>
- Empty: <first use, and no results after filtering; each with a message and a next action>
- Error: <what failed, in plain words, and how to recover>
- Success: <confirmation, and what happens next>
- Permission: <what a user without access sees>
<Add any other state this design has, such as partial data, disabled or read-only. Write "n/a" with a reason for any state that cannot occur, for example on a slide.>

## Structure
<For a deck: a story outline, one line per slide, each headline a full-sentence conclusion with the evidence it needs. For a screen: the regions in priority order.>

## Library
- Components: <names and tiers from recall.md; flag drift, inferred and proposed>
- Patterns: <names>
- Missing (needs a component proposal): <names, or none>

## References (approved by the user)
- <reference id> — <image path> — <the quality to learn from it: hierarchy, density, state handling>

## Viewports
<For example 1440x900 desktop and 360x800 mobile, or 1920x1080 for slides.>

## Reject
<Patterns and looks to avoid for this design, beyond the rubric: things the user has disliked, and any competitor look to stay clear of.>

## Destination
<Figma file or page, Canva, or files; and who edits it afterwards.>
```
