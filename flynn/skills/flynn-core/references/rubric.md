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
