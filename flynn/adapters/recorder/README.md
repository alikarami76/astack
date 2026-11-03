# Design system recorder

Records a live product in flynn's Chrome and turns it into real, editable screens and a design system library in flynn desk. Every page is captured twice in the same frozen state: as the page's real elements (`pnode.json`, the same converter that builds flynn's designs) and as a stitched screenshot, which stays underneath each screen as a hidden reference layer. It covers the recording bundle, tokens and components. Recording more states, asset handling and full scrubbing, and a packaged guide for each system, come later.

## Use it

Press the **Record** button (the ring, between Plugins and MCP) in the canvas toolbar. The popup asks for:

- the start address and the design system it belongs to (optional when you choose **Just the screens**);
- **Design system**: **Create or update the library** (the full flow below), or **Just the screens**: the snapped screens go on the page as editable layers, and nothing is mined, proposed or reviewed, so no library is created or changed;
- what you are recording, in your own words; flynn reads this to understand the screens and name what it finds;
- where the results go: the page you are on (pinned when you press Start), or a new page you name;
- how to record:
  - **I'll browse**: you click through in the window flynn opens. A floating **Snap** bar sits on every page there (drag it by its grip; it remembers where you put it). Nothing is recorded until you press **Snap** (or Alt+Shift+S, or **Snap** in the canvas toolbar), so open the menu, tab or form first. The bar is taken off the page before each capture, so it is never in a recording.
  - **flynn explores**: flynn follows same-site links, up to three pages. It never clicks, types or submits, and it skips sign-out, delete and download links.
- sizes (desktop 1440, mobile 390), whether flynn should review the results in chat, and a confirmation that you may record the product.

A recording holds at most **three snaps**: it ends by itself after the third (the bar says "3 of 3 · finished"). While recording, the toolbar shows a red dot, "N of 3 snaps", **Snap** and **Stop**. After you press Stop (or the third snap, or the crawl finishes), it shows "Finding components…" and then "Building on the canvas…", and finally a summary with **Show page**. Each step also appears in the flynn chat.

Pages that scroll: fixed elements appear once, in the first screen, and anything the page makes float only after you scroll (such as a table header copied to the top of the window) is hidden in every later screen, so it no longer lands on the rows.

## What it makes

- `refs/recordings/<system>/<session>/` (git-ignored, stays on this Mac):
  - `session.json`: your answers and the page list;
  - `pages/NNN-<title>/`: `page.html` (scrubbed) and, for each width in `w<width>/`, `viewport.png`, `full.png` and `dom.json`, which holds each element's box, computed styles, role, classes, own text and, for component-like elements, an HTML snippet. It also has the CSS variables, fonts and media queries;
  - `mine.json`, `css_vars.json`, and `components/<Name>/` with one crop per variant plus `snippet.html` (numbers, emails and links scrubbed).
- The flynn store, under system `rec:<system>`, tier `inferred`, origin `generated`:
  - one component row per candidate: variants by look and size, states seen, the pages it appeared on, the paths of its evidence;
  - one token row per colour, type style, spacing step, radius and shadow, named after a CSS variable when one has the same value.

  These rows never reach the git export. Because the system name differs, they cannot merge into a verified library: promoting them is a reviewed change.
- On the canvas, to the right of anything already on the page: a title block with your description, a **Components** board (cards with the crops of each variant), a **Tokens** board (swatches, type samples, spacing, radii, shadows) and one **Screens** board per page and width, built from the page's real elements (editable text, boxes, icons and images cut from the screenshot), with the screenshot as a hidden layer underneath.

## flynn's review and the library

With "Ask flynn to review" on (the default), flynn looks at the screens, crops and your description. It then:

1. decides which candidates are real components, merges duplicates (`Btn` and `Button`), drops wrappers and one-off layout, and names the variants;
2. writes `<session>/review.json` (`{"components": [{"name", "from": [...], "keep", "variants": {"1": "primary · large"}}]}`);
3. updates the store rows;
4. runs `library.py sync rec:<system>`.

That builds the system's library from the **real recorded elements**: each kept variant's example is cut out of the page's elements by its box. Fixed elements that merely overlap it, such as a floating button, are left out. Without a review, the library is built straight away with the mined names.

## How long pages are captured

Each page and width is captured in one consistent state, so every box in `dom.json` lands on its element in `full.png`:

1. **Unroll.** If the document itself does not scroll but a large inner panel does (an app shell with a scrolling main area), that panel and its parents get `height: auto` and `overflow: visible`. The whole content then becomes one long page with the same layout. A page that is taller than the window but blocks scrolling is opened up the same way.
2. **Load.** flynn scrolls through once, so content that loads on scroll appears, and waits up to 2 s for images.
3. **Freeze.** Animations, transitions, the text caret and scrollbars are switched off. Sticky elements become relative, so they keep their place in the flow without following the scroll. Fixed elements are listed.
4. **Measure, then stitch.** `dom.json` is measured at the top of the page. `full.png` is stitched from window-sized shots taken while scrolling down. Fixed elements show only in the first shot, where they sit at the top of the page, so a header or floating button appears once instead of in every screen. Window-height layouts (`100vh`) keep their real size, because the window is never stretched.
5. **Restore.** Every style change is undone, and the page and the panel scroll back to where they were. This matters when you are browsing the page yourself.

Pages longer than 16,000 px (endless feeds) are cut there and marked `truncated`. The code is in `stitch.js` and `Session._shoot` in `recorder.py`. The test page is `tests/fixtures/recorder/long.html`: an app shell with an inner scrolling panel, a sticky bar, a fixed button, a 60vh hero and lazy images.

## How components are found

- **Controls and roles:** buttons, fields, selects, checkboxes, radios, switches, tabs, tables, dialogs, menus and navigation.
- **Component-like class names:** card, chip, badge, banner and the like, with CSS-module hashes removed. `Name-hash` is the component; `Name__part` and `Name--modifier` are parts or variant hints. For example, `Button__filledPrimary` and `Button__large` become the variant evidence "filledPrimary" and "large".
- **Counting:** only the widest recorded size is counted, and other sizes are evidence. Variants are grouped by look (filled, outlined, text), colour, height and radius.

The names are candidates. flynn's review step, or you, give them their real names.

## Files

| File | Job |
|---|---|
| `record.js` | Runs in the recorded page and returns `dom.json`. |
| `recorder.py` | `Session`: opens the tab, watches or crawls, waits for pages to settle, shoots each width (emulated), and writes the session folder. CLI: `list`, `show`. |
| `mine.py` | Turns tokens and components into evidence and store rows. CLI: `mine.py <session> [--no-store]`. |
| `canvas.py` | Builds the boards through the MCP bridge. Images are sent in 60 KB parts because the connector caps each request at about 100 KB. CLI: `canvas.py <session> --new-page NAME \| --page-id ID`. |
| `../penpot/panel/record_api.py` | The panel server's `/api/record/start`, `capture`, `stop` and `state` routes, and the mine, build and review pipeline. |
| `../penpot/stack/flynn-overlay/www/recorder.js`, `recorder.css` | The toolbar control and the popup. |

## Debugging

- Logs: `~/.flynn/logs/recorder.log` (sessions) and `panel_chat.log` (the pipeline).
- `curl "http://127.0.0.1:8772/api/record/state?file=<file id>"` shows the current state.
- Switch it off with `~/.flynn/features.json` → `{"recorder": false}`.
- Tests: `tests/test_recorder.py` (class parsing, mining on a fixture, and the API with a fake session).

## Known limits

- **No mobile layout:** a page without a viewport tag and with a fixed minimum width records its phone size as the zoomed-out desktop page.
- **Page width:** content wider than the window (fixed-width pages at the mobile size) is cut at the window's width.
- **Heuristic names:** some candidates are wrappers rather than components, such as a "Menu" that wraps a whole navigation. The review step is there to sort these out.
