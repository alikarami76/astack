# UX thinking guide

flynn designs inside strict design systems (a component kit, a document theme, a company design system). The system already decides how things look. This guide is about the decisions it leaves open: what job the design does, how the information is organised, where the user comes from and goes next, which action is primary, which states exist, how errors are prevented and fixed, how dense the screen is, what the words say, and which existing component fits the job.

Read it before the brainstorm (step 2), use it when you write the contract (step 3) and brief composers (step 5), and the critic and fresh-eyes reviewer judge against it (step 6).

## What this guide does not change

The look belongs to the system. Nothing here is a reason to add a colour, a typeface, a type size, a radius, a shadow, an icon style, custom CSS or a component outside the library. When a principle seems to need one, the answer is a different existing component, a different arrangement, or different words. If none of those works, it is a component proposal (flynn-core step 5), never an improvised part. The system-fidelity rules (rubric section 5, the system's guide, the theme READMEs) always win over this guide.

## How the principles are tiered

- **Floor.** Never ship a design that breaks one. The critic reports a Floor break as a Blocker.
- **Reflex.** The default. You may choose otherwise only with a reason written in the contract's "Decisions the system leaves open" section; an unexplained break is a High finding.
- **Polish.** Makes a good design better. Never blocks; the critic reports it as Medium or Low.

Each principle has the rule in one line, how to apply it inside a strict system, and how a reviewer checks it.

## Floor

**F1. A destructive or hard-to-reverse action has a confirm step or an undo window.**
Apply: deleting a voucher, ending a promotion early, removing products from a campaign, or changing a price in bulk asks first with the system's confirm-dialog pattern. The question names the object and the consequence, and the button repeats the verb: "Delete the code "Spring sale"? Customers can no longer use it." with "Delete code" and "Cancel". When the action can be reversed safely, an undo in the snackbar is better than a confirm.
Check: list every action that deletes, ends, publishes or changes money. Each has a confirm or an undo, and the confirm button is not "Yes" or "OK".

**F2. Every error says what went wrong and how to fix it, and keeps what the user typed.**
Apply: form errors sit under the field (the system's form pattern), say the rule and the fix ("The minimum price is 100,000; enter a higher price."), and never clear the form. A failed load says what failed and offers "Try again". Never show a raw code as the message.
Check: every error state on screen has a cause in plain words and a next action. "Something went wrong" alone is a Blocker.

**F3. Every state the design can reach is designed.**
Apply: the contract's state map lists them all: loading (a skeleton shaped like the final layout), empty on first use, empty after filtering, error, partial data, busy while saving, success, no permission, and the module switched off. First use and no results are different screens: "You haven't created a discount code yet" with "Create discount code", against "No results match these filters" with "Clear filters". Use the system's loading and empty-state patterns; do not invent an empty-state layout.
Check: every row of the state map has a frame, and none of them is blank or shows only a spinner for content.

**F4. Nothing is invented and nothing pretends.**
Apply: numbers come from the brief, recall or clu; example data is plausible and labelled as an example. A success state appears only after something really happened: a form never says "Saved" before the request returns. Never add a testimonial, a metric or a count that nobody gave you.
Check: every figure on the page has a source in the contract or the brief; no success screen follows an action that could still fail.

**F5. The user can always get out.**
Apply: every dialog has "Cancel" and closes with the close icon; every step of a multi-step flow has a way back that keeps what was entered; every filtered list has "Clear filters"; leaving a form with unsaved changes asks first.
Check: from every screen and state, find the way back to safety. A dead end is a Blocker.

**F6. The primary job can be done from what is on the screen.**
Apply: the primary action from the contract is visible without hunting, labelled with its verb, and enabled until the request starts (then busy). If it needs a choice first, the choice is on the same screen.
Check: walk the job story start to finish on the screenshots. If you cannot finish it, it is a Blocker.

## Reflex

**R1. Start from the job, then the content, then the layout.**
Apply: write the job story first ("When…, I want to…, so I can…") and put the content the user came for at the top. The shell (top bar, title row) is fixed by the system; the first card is where your design starts.
Check: the first thing the eye lands on in the content area serves the job story.

**R2. Use the product's existing pattern for this kind of job.**
Apply: users expect a screen to work like the screens they already know. Choose the pattern from the table in the system's guide (a list to act on is a filterable data table, a task in steps is a multi-step flow, one record's tool is a modal tool). Depart only with a written reason.
Check: name the pattern each region uses. A region that matches no pattern and has no reason in the contract is a High finding.

**R3. Organise by the user's task and words, not by the database.**
Apply: group fields the way a user thinks about the job (price and discount together, stock and shipping together), and name things as users say them ("Stock", not "inventory_count"; "Selling price", not a column name). Order follows the job, not the API.
Check: read the labels aloud as a new user would. Any internal term or backend grouping is a finding.

**R4. One thing is primary; everything else steps back.**
Apply: one primary button per screen or region; other actions use the secondary and text variants, and rare ones go in a menu. Spend emphasis in one place. If everything looks equally important, nothing is.
Check: squint at the screenshot. One action and one piece of content should stand out, and they should be the contract's primary action and purpose.

**R5. Prevent the error before you design its message.**
Apply: constrain inputs (the end date picker gets a minimum date equal to the start date; a percentage field cannot go over 100), offer good defaults, validate as the user leaves a field, and disable an impossible choice with a short reason beside it rather than letting it fail on submit.
Check: for each error message in the design, ask whether a constraint or default could have stopped it. If yes and it is missing, that is a High finding.

**R6. Every action gets visible feedback.**
Apply: the button shows busy while saving, a snackbar or the result confirms the outcome in the same verb ("Discount saved"), a long job shows progress or "we will notify you", and a changed row looks changed.
Check: for every action, name what the user sees right after it. No visible response is a High finding.

**R7. Show what the user needs to decide where they decide it.**
Apply: the current price sits next to the new-price field; the selected products stay visible on the last step of a flow; a filter chip shows the active filter. The user should never need to remember something from an earlier screen.
Check: for each decision, is the information it needs on the same screen? A memory bridge between screens is a finding.

**R8. Few choices at a time; reveal the rest when needed.**
Apply: at a decision point show about four options or fewer; one primary, one or two secondary, the rest in a menu. Rare settings fold into an `Accordion` or an "advanced" step. Defaults cover the common case.
Check: count the visible options at each decision point. More than seven with no grouping is a finding.

**R9. Density matches how often and how expertly people use it.**
Apply: an operational tool is used daily: dense tables, aligned numbers, short labels, no padded dashboards. A first-week user gets guidance through the system's existing onboarding, hint and guide patterns, not through a looser layout. A report for an executive is sparse: one conclusion per block.
Check: compare the density with the persona. Big empty cards on an operational screen, or a dense table in a slide, are findings.

**R10. After success, the next step is visible.**
Apply: the flow continues: the success dialog after a wizard says what happened and offers the next useful action ("View discounts" or "Add another product"); a saved form returns to the list with the new row visible. For a report, end with the decision or the action you want.
Check: on the success state, can the user tell what to do next? A dead-end success is a finding.

**R11. Words are specific, consistent and useful.**
Apply: buttons are a verb and an object ("Create discount code", not "Continue" or "Confirm"); the same action keeps the same word through the flow (the button "Save discount" leads to "Discount saved"); helper text answers a question rather than repeating the label; empty states say what will be here and how to start. Plain words, in the voice the system's guide sets.
Check: list every button label and the message that follows it. Generic labels or a word that changes mid-flow are findings.

**R12. Structure carries meaning, never decoration.**
Apply: one layer of containment (a card is never inside a card); numbered markers only for a real sequence; no icon placed above every heading; no small label above a heading that only repeats it; differences in treatment only where importance really differs.
Check: for each border, card, number, icon and label, ask what it tells the user. If nothing, it is a "looks machine-made" finding (rubric section 4).

**R13. Design for the worst content, not the demo content.**
Apply: test the layout with the longest real product title, a shop name in Latin letters, 500 rows and zero rows, missing optional fields, prices in the hundreds of millions, and mixed local-script and Latin text and digits in SKUs. Use the contract's worst-case content table.
Check: the worst-case frames exist, nothing overflows or truncates without a tooltip, and the table still reads at 500 rows (pagination, page size).

## Polish

**P1. Remove one element.** Before you call a design done, remove one thing (a line of helper text, a divider, a second button, a badge) and see if anything is lost. If not, leave it out.

**P2. End well.** People remember the hardest moment and the end of a task. Make the success state calm and clear, and make the hardest step (usually choosing products or entering prices) as simple as the system allows.

**P3. Help sits next to the hard part.** A hint beside a confusing term beats a link to a help page. Explain a domain term once, where it is used.

**P4. State survives refresh and sharing.** Filters, tabs, page and sort live in the URL so back, refresh and a shared link keep them. In a component-kit screen this is a note for developers in the contract, not something to draw.

**P5. Numbers are easy to compare.** Units go in the column header instead of every cell, figures align, and the number that matters most is the one emphasised.

## Process techniques

**Job story.** One sentence before any layout: "When [situation], I want to [job], so I can [outcome]." It names the moment, not the persona. Example: "When an item is about to sell out, I want to stop its discount early, so I don't run out of stock at a low price."

**Flow map.** A few lines: where the user arrives from (menu, notification, a link in another page), what they do on this screen, what happens on success, what happens on failure, and where they go next. A screen designed without its neighbours breaks the flow.

**State map.** A table: state, what triggers it, what the user sees, what they can do. Fill it before layout so no state is an afterthought.

**Worst-case content table.** Real extremes for each field: longest name, most rows, zero rows, missing values, largest number, mixed digits. Build at least one frame from it.

**Structural divergence.** Options differ in how the information is organised, not in styling. Pick an axis per option: task-centred (start from what to do) against object-centred (start from the list of things); one page with inline editing against a step-by-step flow; or a different primary element. Name the axis for each option.

**Plan, genericness check, build.** Before building, write a five-line plan and ask: could this plan fit any similar product unchanged? If yes, find what is specific to this product and this user (their real data, their real deadline, the real rule) and put it in the plan.

**Remove-one-element pass.** After building, remove one element and record what was removed or why nothing could go.

**Squint and five-second test.** Blur the screenshot or look for five seconds. Can you say what the page is for and what to click first? The fresh-eyes reviewer does this with no context.

## Personas for critique

Walk the primary job on the screenshots as two or three of these, and report what breaks for each one, specifically.

- **The experienced high-volume user.** Runs a high-volume shop, uses the product daily on desktop, works fast, acts on many rows at once, ignores guidance, and hates extra clicks and confirmations on safe actions. Breaks on: missing bulk actions, slow paths to the primary action, padding that hides rows.
- **The new user.** In their first week, unsure of terms such as "commission" and of campaign rules, afraid of breaking prices, often on a laptop. Breaks on: jargon, missing explanations at the hard step, empty states with no next action, irreversible actions without warning.
- **The PM or category manager.** Skims between meetings, wants the status and the exception in seconds, often on a laptop or phone. Breaks on: no summary, the important number buried, the exception not standing out.
- **The executive (reports and decks).** Reads for ten seconds per page or slide. Breaks on: headlines that name a topic instead of stating the conclusion, more than one idea per block, a chart without its takeaway.

## Sources

Our own words, built from these. Aesthetic and theme advice in them was left out on purpose.
- Impeccable, Paul Bakaus, `skill/reference/critique.md`, `harden.md`, `clarify.md`, `onboard.md` — https://github.com/pbakaus/impeccable (Apache 2.0): the critique pass, the cognitive-load checklist, personas, error and empty-state copy, hardening edge cases.
- Web Interface Guidelines, Vercel — https://github.com/vercel-labs/web-interface-guidelines (MIT): destructive actions need a confirm or undo, errors with the fix, specific button labels, empty states, long content, URL state.
- frontend-design skill, Anthropic — https://github.com/anthropics/skills/tree/main/skills/frontend-design (Apache 2.0): plan then genericness check, structure as information, remove one element, copy rules.
- Hallmark, Together AI — https://github.com/Nutlope/hallmark (MIT): Floor, Reflex and Finish tiers with recorded reasons for overrides, no invented facts, no fake success, the fresh-context reviewer, no card inside a card.
- Designer Skills, Owl-Listener — https://github.com/Owl-Listener/designer-skills (MIT): `map-states`, `explore-options` (concepts that differ in behaviour, a decision record), `critique-ux`.
- Nielsen Norman Group, "10 Usability Heuristics for User Interface Design", Jakob Nielsen — https://www.nngroup.com/articles/ten-usability-heuristics/ (cited and paraphrased, not copied).
- Laws of UX, Jon Yablonski — https://lawsofux.com/ (cited and paraphrased): Jakob's law, Hick's law, chunking, proximity and common region, Von Restorff, peak-end, goal-gradient, Doherty threshold.
