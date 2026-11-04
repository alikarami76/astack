# flynn design rubric

The critic judges every option against every section. A `pass` means a senior product designer at a top US tech company would ship it. Each section lists what to look for; any hit is a finding.

## 1. Fit to the contract
- The purpose is obvious within five seconds.
- There is exactly one primary action, and it is the most prominent interactive element.
- The content is the contract's real content, at its real volume, including the extremes.
- Every required state is designed, and none shows a blank screen.

## 2. Hierarchy
- The eye path matches the priority order in the contract's Structure section.
- There are at most three levels of type emphasis on a screen. Size, weight and color are not all used at once for the same job.
- Secondary information steps back: quieter color, smaller size, or grouped out of the way.

## 3. Density and spacing
- The density suits the audience: operational tools (back-office and admin screens) are denser than consumer pages.
- Every gap and padding is a spacing token, and related things are closer together than unrelated things.
- No uniform, oversized padding everywhere. Whitespace is used to group, not to fill.

## 4. Looks machine-made (reject on sight)
- Purple or indigo defaults, or any color that is not in the system.
- Heavy or decorative gradients.
- Maximum corner rounding on everything (fully rounded cards and buttons), or radius that does not follow the system's scale.
- A generic hero section (big headline, subtitle, two buttons) where the content should lead.
- A stock card grid of identical cards where the information needs a table, a list or a purpose-built layout.
- Lorem ipsum, "Item 1", "Title", or any placeholder copy.
- Heavy, layered drop shadows.
- Icons used as decoration with no meaning; emoji used as icons.
- Everything centered; a symmetric layout the content does not need.
- Too many accent colors; color used as the only way to signal state.
- Structural tells (these are about arrangement, not style, so they apply inside a strict system too):
  - A nested card: a card or bordered box inside another card. One layer of containment.
  - An icon tile above every heading, or an icon on every item, carrying no meaning.
  - Identical treatment for everything: every section, card or row given the same weight, so nothing is more important than anything else.
  - Numbered steps (1, 2, 3 or 01 / 02 / 03) for things that are not a sequence.
  - A decorative kicker label: a small label above a heading that only announces or repeats it.
  - A fake success state: a confirmation ("Saved", "Done successfully") shown with nothing behind it, or before the action could have finished.

## 5. System fidelity
- Only library components and tokens; any override is justified in the contract.
- Components are used as their usage rules say, and not stretched into another component's job.
- `drift`, `inferred` and `proposed` components are flagged to the user.

## 6. Right-to-left and language
- Direction is right-to-left throughout: alignment, icons that imply direction (arrows, chevrons, progress), and the order of table columns and form fields.
- Persian copy is natural, concise and consistent with the product's voice; numbers, dates and currency follow the system's formatting.
- Mixed Persian and Latin text (SKUs, codes, English brand names) stays readable.

## 7. Accessibility
- Text contrast is at least 4.5:1 (3:1 for large text and for UI parts such as borders and icons).
- Touch targets are at least 44 by 44 px on mobile.
- Focus order follows the reading order, and every icon-only button has a label.
- State is never shown by color alone; there is always text or an icon too.

## 8. Decks (in addition to the above)
- One idea per slide, and each headline is a full-sentence conclusion.
- The story holds together when you read only the headlines.
- Text density is low enough to read in 10 seconds; detail goes in speaker notes or the appendix.
- Visual rhythm: layouts vary on purpose, while type, color and grid stay consistent.
- Images follow the theme's art direction, and each one's license is recorded.

## 9. Charts
- The chart type fits the question: comparison, change over time, part of a whole, distribution or relationship.
- The title states the takeaway; units and the time period are always shown.
- Direct labels are used instead of a legend where possible, and one series is highlighted.
- Axes are honest (bars start at zero), and there is no 3D, no dual axis without a clear reason, and no pie with more than five slices.

## 10. UX and flow
Judge these with `references/ux-thinking.md` (Floor, Reflex and Polish principles) and the contract's job story, flow and state map.
For a static page (report, release page, newsletter, deck), judge the items that apply to reading it (job fit, next step, labels, hierarchy, chunking) and mark the rest n/a.
- Job fit: the design does the job story's job for its persona; the primary action serves that job.
- Flow continuity: the entry points and the next step in the contract's Flow section are reachable, and the design fits the screens around it.
- Next step visible: after success the user can see what to do next; no dead-end success state.
- Error prevention: constraints, defaults and inline validation stop the errors they can (for example an end date before the start date cannot be picked).
- Error recovery: every error says what went wrong and the fix, and keeps what the user typed.
- Undo or confirm: every destructive or hard-to-reverse action has a confirm step that names the object and the consequence, or an undo window. A missing one is a Blocker.
- Feedback for every action: busy while working, then the result in the action's own verb.
- Recognition over recall: the information needed for each decision is on the same screen; the user never has to remember something from an earlier step.
- Progressive disclosure: rare options are folded away, and a decision point shows about four choices or fewer.
- Specific labels: buttons are a verb and an object ("Create discount code"), never "Continue", "Confirm" or "Submit" alone where the object is unclear; the same action keeps the same word through the flow.
- A way out: cancel, back and clear filters exist wherever the user can get stuck.

**Cognitive-load checklist.** Mark each item pass or fail per option. Four or more fails is a Blocker; two or three is a High finding.
1. Single focus: the primary job can be done without competing elements pulling attention.
2. Chunking: information comes in small groups (about four items or fewer per group).
3. Grouping: related items sit together and unrelated ones apart.
4. Visual hierarchy: what matters most is obvious at a glance.
5. One decision at a time: the user is not asked to decide several things at once.
6. Few choices: about four visible options or fewer at each decision point.
7. Working memory: nothing must be remembered from a previous screen to act on this one.
8. Progressive disclosure: complexity appears only when it is needed.
