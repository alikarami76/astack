# editorial components

This is the component catalogue for the `editorial` theme (`themes/editorial/style.css`). `templates/release-page.html` is the reference page: when in doubt, copy how it uses a component.

## Tokens

| Token | Value | Use |
|---|---|---|
| `--ink` | #0f1020 | headings, bold text, the emphasis border |
| `--text` | #2e3040 | body text |
| `--muted` | #6b6e7f | intros, captions, secondary text |
| `--faint` | #737584 | labels and table headers (4.6:1 on white) |
| `--line` | #ebebf0 | hairlines, image borders |
| `--soft` | #f6f6f9 | card, example and chip fills |
| `--accent` | #0f766e | the theme's teal, used only as an accent: section labels, numbers, markers, highlight boxes, one emphasised result |
| `--accent-soft` | #e6f4f2 | the note callout |
| `--accent-text` | #0b5c56 | accent for small text (under 18px) on the soft tints, where `--accent` could fall under 4.5:1 |
| `--green` | #0f9d58 | validation results only (QA stats) |
| `--col` / `--mid` / `--wide` | 680 / 860 / 980 px | reading column / tables / screenshots |
| `--r` | 16px | radius for cards and screenshots |

Type uses the Vazirmatn variable font throughout:

| Level | Size / weight | Notes |
|---|---|---|
| h1 | 56 / 900 | |
| h2 | 36 / 800 | |
| h3 | 22 / 700 | |
| h4 | 18 / 700 | Used for steps, cards, tour captions and the FAQ. |
| body | 17 / 400 | line-height 1.9 |
| lead | 20 | two sentences at most |

Vertical spacing uses one scale: 24, 40 and 64. Screenshots and tour items get 64. Examples and notes get 40.

## Layout containers

- `.col`: the reading column.
- `.mid`: wide tables, such as the team table.
- `.wide`: screenshots and the tour.
- `section`: adds 96px above each section.
- `data-shot="<name>"`: marks a block to be exported as its own PNG. Give every block that should go into an email its own `data-shot`.

## Components

| Class | What it is | Use when | Never |
|---|---|---|---|
| `.hero` with `.tags`, `h1`, `.lead`, `.values > .value` | Opening: tags, a two-line title, a two-sentence lead and three value cards (label, short title, one or two sentences). | Every launch page. | More than three value cards, or a lead longer than two sentences. |
| `.sec-label` | Small accent label above an h2. | Numbered projects, and the tour. | Repeating the heading's words (for example "چرا" above «چرا…»). |
| `.problems > .problem` | Numbered problem card: number, h4, evidence with figures. | The "why" section. | Problems without a figure or example. |
| `.steps` | Numbered vertical steps using Persian numerals, each with a bold title and one or two sentences. | Processes. | More than about 4 steps in one list. Split them under `.group` labels and continue the numbering with `style="counter-reset: s 4"`. |
| `.group` | Small muted label above a step group. | Splitting a long process. | |
| `.fields > span` | A row of field chips. | Listing fields or options that would otherwise be one long sentence. | |
| `.levels > .lvl` | A grid of concept tiles. | Showing a set of parallel options (up to 6). | |
| `.funnel > .fstep` | A left-pointing sequence of stages. | Ordered decision stages. | |
| `.example` (`.k`, `h4`) | Grey worked-example box. Label it «مثال · اعداد فرضی‌اند». | Every abstract mechanism needs one worked example. | Real customer data. |
| `.duel > .cand(.win)` with `.pill.ink` | Two candidates side by side; the winner gets an ink border and an ink pill. | Explaining a choice between rules or options. | Colouring the winner green. |
| `.calc` with `.res` | A one-line calculation with the result in the accent colour. | The final number of an example. | |
| `.bands > .band.b1/.b2/.b3` | A proportional bar made of neutral bands, with the accent only on the band the example lands in. | Ranges and thresholds. | Colours outside the tokens. |
| `.life > .ls(.gate/.live)` | A lifecycle stepper. `.gate` gets an ink underline, `.live` an accent underline. | Status flows. | Solid colour fills. |
| `.mock` | A hand-built concept preview. Label it «طرح مفهومی» in `.bar span` and add a `.concept` line under it. | Only when no real screen exists. | Presenting it as a real screenshot. |
| `.shot` with `img`, `.hl(.tl)`, `span` | An annotated screenshot. `.hl` is a highlight box with a numbered badge; `.tl` moves the badge to the top-left. Build these with `crops.py`. | Every product claim that can be shown. | A box covering neighbouring words, a crop through text, or a display ratio below 0.8. |
| `.shot.fade-l` | Fades the left edge of a screenshot. | Crops that cut a horizontal scroll strip. | |
| `figure.narrow`, `.tour-item.narrow` | Screenshots up to 640px wide. | Tall or small crops, and tooltips. | |
| `.mk` | An inline numbered marker in text that matches a `.hl` badge. | Glued to the word before it and followed by its punctuation, all inside `.nw` so a line break can never split them: `<span class="nw">می‌کند<span class="mk">۱</span>:</span>` | A space before it, or placing it after the punctuation. |
| `.tour-item` with `.cap` (`h4 .num`, `p`) | A screenshot with a numbered caption. | Screens that don't belong to a project's explanation. | Parking a screenshot here when a project section explains it: put it next to that explanation instead. |
| `.note` | Tinted callout starting with «نکته:». | At most once per section, and only when it says something new. | One in every section. |
| `.aside` | A muted one-line paragraph. | Open questions, and sources for a small list. | |
| `table.teams` | Team-impact table: teams as rows, projects as columns. | Once per page. | Per-section audience cards. |
| `table.num` | A number table with centred, non-wrapping value columns. | Results and examples. | |
| `.stats > .stat .big` | Large validation numbers. | QA results only. | Business-impact claims. |
| `.next > .card` | Next-step cards with a label, h4, text and `.owner`. | Product follow-ups. | Engineering chores. |
| `.links`, `.faq > .q`, `.footer` | Link list, FAQ and the sender footer. | Every launch page. | |

## Terminology

When the user prefers one Persian term over another (for example a team or role name), record it as a lesson in the store and use it everywhere, including in the checker's rules.

## Persian text rules

- Use "." as the decimal separator in Persian digits (۹۹.۸۸٪), the same as the product UI. Many fonts draw U+066B like an apostrophe.
- Wrap short quoted UI labels in `<span class="nw">` so they stay on one line, and Latin terms in `<span class="en">`.
- Give each English term a Persian gloss the first time it appears, for example «حالت سایه (shadow)».
- Write «حدود ۲٬۱۰۰», not «~۲٬۱۰۰».
- Never start a heading with Latin.
- Never turn on a font stylistic set (such as `ss01`) for body text if it swaps every «تومان» for a currency sign, which reads oddly in a sentence. Use `<span class="toman-sign">…تومان</span>` only for a displayed price.
- Write signed or multiplied numbers in words where bidi would flip the sign: «۴۵٪ رشد», «۲.۷ برابر», not «+۴۵٪» or «۲.۷×».

## Report (`templates/report.html`, `themes/editorial/report.css`)

A report answers one question. The head alone must give the answer in 30 seconds. Reference: `templates/report.html`.

| Class | What it is | Use when | Never |
|---|---|---|---|
| `.report-head` (`.kind`, `h1`, `.byline`, `.sample-flag`) | Report type, the question as the title, author, date and data window. | Every report. Add `.sample-flag` whenever the numbers are not real. | A title that names a topic instead of asking or answering. |
| `.answer` | The answer in one or two sentences, with the main number, behind an accent rule. | Always, straight under the byline. | Hedging with no number. |
| `.mid.kpi-row > .kpi(.hot)` (`.v`, `.l`, `.d`) | Four key numbers: value, what it is, and a comparison. `.hot` marks the one the answer is about. | Straight after the answer. | More than four, or numbers the text never explains. |
| `.finding` (`.n`, `h3`, `figure.chart`, `p`, `.caveat`) | One finding: the claim as the heading, one chart that proves it, two or three sentences, and the caveat. | Each finding. The first sits in a `section` under «یافته‌ها»; the rest use `.col.finding-next`. | A heading that only names the metric, or two charts for one claim. |
| `figure.chart` with `figcaption` | A chart frame; the caption gives the definition, unit and window. | Every chart. | A chart without a caption. |
| `.method dl` | Data source, window, definitions and comparison method. | Every report. | |
| `table.recs` | Numbered recommendations, each with its reason (citing a finding) and an owner. | Last section. | A recommendation that no finding supports. |

## Charts (`charts.py`, `chartspec.py`)

Inline SVG, so charts stay sharp in the PDF and PNGs and inherit the theme font. Types: `bar_h`, `line`, `columns`, `bridge`, `dot_range` (before/after dumbbell), `stacked_share` (100% bars), `small_multiples` (shared scale), `sparkline` (inline, for KPI cards and tables), `table_bars` (ranked table with an inline bar), `scatter` (labelled highlights, optional quadrants), and `heatmap` (single-hue ramp). `fa(n, decimals)` gives Persian digits, with «٬» for thousands and «.» for decimals.

Callers that have data but no code (clu, other agents) write a chart spec and run `chartspec.py render <spec.json> --out <dir>`. That writes `chart.png`, `chart.svg` and `figure.html`: a `figure.chart` block with the title and the caption. `type: "auto"` picks the chart type from the data shape; `chartspec.py choose` explains the choice.

The choice table, the rules and the critic's checklist are in `skills/flynn-core/references/dataviz.md`. The short version:
- the title is the finding as a sentence;
- label directly;
- the accent colour only on what the title is about;
- bars start at zero;
- the unit, definition, window and source go in the caption, never in the plot;
- time runs left to right, and categories sit on the right.

Run `charts.py demo <out.html>` to see every type.

## Review UI (`adapters/review/`)

`review.py serve <run>` opens the page at `http://127.0.0.1:8765` with flynn's comment layer. The user presses C (or Comment), clicks anything, types and saves. Each pin stays attached to the element it was dropped on.

flynn reads the comments with `review.py list <run>`, answers with `review.py reply <run> <n> "…"`, and closes them with `review.py resolve <run> <n> --note "what changed"`. Replies appear in the page within 2 seconds, and the page reloads itself whenever flynn re-renders it.

Comments are stored in `<run>/feedback.json`, with a Markdown copy in `<run>/feedback.md`.

## Deck (`templates/deck.html`, `themes/editorial/deck.css`)

Slides are 1280x720 CSS px, rendered at 2x. Render with `render.py <page> --slides --pdf <name>.pdf`. Every slide ends with `<div class="foot">فروشگاه نمونه</div>`; the page number is added automatically, in Persian numerals.

| Class | What it is | Use when | Never |
|---|---|---|---|
| `.slide.title` | Wordmark, accent kicker, 72px two-line title, one-line sub, presenter and date. | First slide. | More than one sentence under the title. |
| `.slide.divider` | Grey slide with a big accent section number and the section name. | Between sections of a long deck. | In decks under about 8 slides. |
| `.slide.statement` | One 60px sentence plus one or two supporting sentences. | The single message the audience must remember. | Bullet lists. |
| `.slide.shot-slide` with `.frame > .shot` and `.cap` | Screenshot (crop made with crops.py) beside two short captions. | Showing a real screen. | A full-page capture; it fails `smallShots`. Crop tight. |
| `.compare > .side(.after)` with `.label` | Two columns, today versus after; the "after" side gets an ink border and an ink label. | Before and after, or two options. | More than about 4 bullets per side. |
| `figure.chart > .plot` with `figcaption` | Chart slot that fills the slide; put an SVG from charts.py inside `.plot`. | Any data slide. The title states the finding, and the caption gives source, window and unit. | A title that only names the metric. |
| `.numbers > .n(.key)` with `.big` and `p` | Three big numbers over ink rules; the key one is in the accent colour. | Headline results. | More than three numbers. |
| `.next-list` | Numbered next steps with owner and date. | Last slide. | Engineering chores. |

Rules:
- One idea per slide.
- The accent colour only for the kicker, the key number and the section number.
- Body text at least 20px.
- `slideOverflow` must be empty, because a slide clips its content and anything that spills out disappears silently.

## Newsletter (`templates/newsletter.html`, `themes/editorial/newsletter.css`)

A 640px digest. Each `data-shot` block becomes one image in the email; `data-link` is where clicking that block goes.

| Class | What it is |
|---|---|
| `.nl-head` | Wordmark plus the newsletter name and issue. |
| `.nl-lead` | Kicker, 30px headline, screenshot, two or three sentences, «بیشتر بخوانید». |
| `.nl-story` | Kicker, 20px headline, one or two sentences, «بیشتر بخوانید». Use three or four. |
| `.nl-numbers` | Three numbers; the key one is in the accent colour. |
| `.nl-foot` | Sender and a reply line. |

Rules:
- Render with `--width 720 --pad 0` so the blocks stack edge to edge.
- Keep each block's text meaningful on its own, because it becomes the image's alt text for readers whose client blocks images.
