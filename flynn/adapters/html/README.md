# HTML adapter

This adapter turns a page written in a flynn theme into the files the user shares:

- a single-page PDF;
- one PNG per section, ready to paste into an email;
- a full-page PNG for review;
- the HTML itself.

It is flynn's default route for anything people read rather than edit themselves: release notes, reports, newsletters and decks. Canva is used only when someone has to keep editing the file in Canva. Figma is used for product UI.

The reason is practical. Canva's Free plan can't load custom brand fonts, the API can't set fonts or page heights, and exports can be blocked, so a Canva design can't always be reviewed at full size. The HTML route has none of these limits.

## Requirements

- Python 3 with `playwright` (`pip install --user playwright`, then `python3 -m playwright install chromium`).
- `Pillow`, for the crops.

## Files

| File | What it does |
|---|---|
| `new_page.py <run> [--template release-page] [--theme editorial]` | Creates `<run>/page/` with `index.html`, `style.css`, `fonts/` and an empty `img/`. A theme's own `templates/<name>.html` wins over a shared template of the same name. |
| `crops.py <spec.json> --img <run>/page/img` | Crops screenshots, places the highlight boxes, prints the `.shot` markup and warns when a crop would be too small to read. |
| `crops.py --grid <img> <out> [x0,y0,x1,y1]` | Draws a percentage grid over an image so you can choose crop and box coordinates. |
| `render.py <run>/page/index.html --pdf <name>.pdf` | Renders `<run>/shots/full.png`, `<run>/shots/sec-*.png` and the PDF, and runs the checks. |
| `themes/editorial/` | The neutral default theme: `style.css` and the Vazirmatn variable font. |
| `templates/release-page.html` | Skeleton of the approved release page. |
| `templates/report.html`, `themes/editorial/report.css` | Report: question, answer, key numbers, findings with charts, method, recommendations. |
| `charts.py` | Inline-SVG charts in the theme, 11 types (see `components.md`, "Charts"), and `fa()` for Persian digits. `charts.py demo <out.html>` shows every type. |
| `chartspec.py validate\|choose\|render <spec.json> [--out <dir>]` | The chart handoff: turns a JSON chart spec into `chart.png` (2x), `chart.svg` and `figure.html`. `auto` picks the chart type; validation rejects titles that only name the metric, missing or mixed units, more than 7 highlights, and negative bars. Spec format and the clu flow: `docs/clu-handoff.md`. |
| `templates/deck.html`, `themes/editorial/deck.css` | 16:9 deck: title, divider, statement, screenshot, compare, chart, numbers and next-steps slides. |
| `render.py <page> --slides --pdf <name>.pdf` | Deck mode: `<run>/shots/slides/NN.png` at 2560x1440, one exact image per `.slide`, and a PDF with one 1280x720 page per slide. It also runs the `slideOverflow` check. |
| `templates/newsletter.html`, `themes/editorial/newsletter.css` | 640px newsletter: header, lead story, story cards, numbers strip, footer. |
| `render.py <page> --pad 0` | Section PNGs without the 24px padding. Required for newsletters, whose blocks are stacked edge to edge in the email. |
| `email_pack.py <run> --subject "..." [--to ...] [--link-base URL]` | Builds `<run>/newsletter.eml`: an Outlook-safe 640px table of the section PNGs, embedded as cid images, with alt text from each block, a link per block (`data-link`) and a plain-text alternative. It is marked `X-Unsent: 1`, so Outlook opens it as a draft. It never sends mail. |
| `../review/review.py serve <run>` | The review UI: the live page with a comment layer. See `components.md`, "Review UI". |
| `halftone.py <src> -o out.svg --cell 8 --color "#4FD1FF" [--crop x,y,w,h] [--width N]` | Turns an image into a halftone SVG: one circle per cell, radius scaled by the cell's brightness, cells below `--threshold` (0.08) left empty. The viewBox is the cropped image size. Used for dot illustrations on black backgrounds. Python: `halftone_svg(src, cell, color, threshold, crop, width)`. |
| `components.md` | Tokens, the component catalogue and the Persian text rules. |

## Checks (in `render.py`)

A failed check makes the exit code 1. The renderer reports the failure and does not hand the page on.

- **`font`:** the theme font loaded.
- **`badImages`:** every image loaded.
- **`overflow`:** nothing is wider than the page.
- **`smallShots`:** every `.shot` image is shown at 0.8 or more of its 2x capture size. Below that, the product text is too small to read.
- **`slideOverflow`** (slides mode only): content that spills out of a 1280x720 slide.

## Workflow

1. Run `new_page.py` to create the page.
2. Save the screenshots in `page/img/`.
3. Use `crops.py --grid` to choose the regions, then write `crops-spec.json` and run `crops.py`.
4. Write the content into `index.html`, using the component catalogue.
5. Run `render.py`.
6. Look at the section PNGs at full size.
7. Send the renders to `flynn-critic` on Opus, apply its blockers and render again.
8. Open the review UI for the user (`review.py serve <run>`, see Review below) and give them the PDF path. Work through their comments until the user picks the final version.

## Templates

- **Release page:** built.
- **Report:** built.
- **Deck:** built. `render.py --slides` produces a PDF and slide PNGs. For an editable file, run `../pptx/pptx_export.py <page> --out <run>/deck.pptx --preview`. See `adapters/pptx/README.md`: the theme font must be installed on the reader's machine, and charts are pictures.
- **Canva:** `../canva/canva_plan.py` maps any page onto a Canva template, or writes an outline for Canva generation. See `adapters/canva/README.md` for the verified limits.
- **Newsletter:** built. Render with `render.py --width 720 --pad 0`, then run `email_pack.py`. The user opens the .eml in their mail client and sends it themselves.

## Review

After the critic's blockers are fixed, run `python3 "$FLYNN/adapters/review/review.py" serve <run>` in the background. It opens the page for the user with the comment layer. Read their comments with `review.py list <run>`, fix each one, re-render (the page reloads itself), then `review.py resolve <run> <n> --note "what changed"`. Use `reply` when a comment needs an answer rather than a change.
