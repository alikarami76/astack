# Data visualization

These are the rules for every chart flynn makes or reviews.

- **The composer** uses them to choose and build charts, with `adapters/html/charts.py` or through a chart spec and `adapters/html/chartspec.py`.
- **The critic** runs the checklist at the end on every chart.
- **clu hands findings over** as chart specs; see `docs/clu-handoff.md`.

The quality bar is the graphics desks at the FT and NYT, and the data reports Stripe and Airbnb publish: one message per chart, readable in five seconds, with nothing a reader has to decode.

## 0. The exhibit: every chart in a document has the same anatomy

A chart in a report, brief, deck or release page is an exhibit. Its parts run top to bottom, aligned to one edge (the right edge in RTL), with no box or border:

1. **Number:** «نمودار ۲», counted per document, in muted grey (never the accent colour), 12px, bold. A chart that is only a spot illustration in running text may omit it; a chart a reader could cite may not. Panels of one claim take «نمودار ۱۰ الف»، «نمودار ۱۰ ب» and write the title once.
2. **Title:** the finding as a sentence with a verb, ending with a full stop, usually two lines at most (seven to twenty-four words). It names the comparison and its direction («نرخ خرید مجدد در لوازم جانبی نصف بقیهٔ دسته‌ها بود.»), not the metric, and carries the scope when the scope changes the claim. No colon, and never the word «نمودار». Projections take hedged verbs («می‌تواند»، «پیش‌بینی می‌شود»); measured facts take plain ones.
3. **Subtitle:** one line in two weights: the measure and its cuts in bold, then the unit and the base in regular: «**تغییر فروش به تفکیک دسته، ۱۴۰۴ تا ۱۴۰۵،** درصد». The unit comes last, is single, and is never repeated inside the plot. Name the base when it is a sample: «(n = ۳٬۷۶۳)».
4. **Plot.**
5. **Footnotes, then Note, then Source, in that order:** numbered footnotes say what a series includes or an estimate assumes (the mark sits after the noun it qualifies); «یادداشت:» gives method and rounding; «منبع:» lists providers separated by «؛», with the document's own credit last. This is the smallest text on the page (11px at least), grey, with no rule above it.

Spacing: number to title 6px, title to subtitle 12px, subtitle to plot 16px, plot to notes 10px.

A chart without exhibit fields (a spot chart) keeps the older form: title, plot, and a caption with the unit, definition, window and source. In a chart spec the exhibit fields are `exhibit_no`, `subtitle`, `base`, `footnotes`, `notes` and `source`; see `docs/clu-handoff.md`.

## 1. Choose the chart from the data and the message

| The data is | And the message is about | Use | Why |
|---|---|---|---|
| One measure per category | Which is biggest, or where one stands | `bar_h` (ranked) | It's the easiest to read and label in RTL, with the value at the bar's end. |
| A time series, up to 4 lines | A trend, or a change in a period | `line` (`band` shades the period) | Lines show change over time; the names sit at the line ends. |
| A time series, 5 lines or more | How each series moved | `small_multiples` (one scale shared by every cell) | More than 4 lines tangle. |
| Before and after, 3 categories or fewer | The change per category | `columns` | The plainest comparison. |
| Before and after, 4 categories or more | Which categories changed, and by how much | `dot_range` (dumbbell) | Each change is a single stroke, not a forest of columns. |
| Parts of a whole, per row | The mix, or how it differs across rows | `stacked_share` (100% bars) | It compares shares directly. |
| A start total, steps, an end total | How one became the other | `bridge` (subtotals allowed) | It shows each step's size and sign. |
| Parts of a total per period, absolute values | The total's growth and which part drives it | `stacked_columns` | The top total gives the headline; the segments show the driver. |
| Parts of a total over time, 6 parts at most | How the mix and the total moved together | `stacked_area` | Bands show both; more than 6 cannot be told apart, so use `small_multiples`. |
| Categories whose size and rate both matter | Which big categories grow or shrink | `marimekko` (width = size, height = rate or share) | It shows importance and performance together. |
| One measure per category, with positives and negatives | Who gained and who lost | `bar_h` or `columns` (a zero line is drawn) | One zero line, one reading. |
| Two measures per item | How they relate; outliers; quadrants | `scatter` (with `quadrants`) | One point per item, labelling only the ones that matter. |
| Category x period | Where and when values concentrate | `heatmap` (one hue) | It shows a grid pattern at a glance. |
| Several measures per ranked row | The ranking, plus the detail | `table_bars` | A table keeps the numbers, and the inline bar gives the ranking. |
| A plain set of numbers that must be exact | The reader needs the figures | `table_plain` with the exhibit header | The exhibit grammar without a drawing. |
| One short series inside a card or table | Direction only | `sparkline` | A word-sized trend. Never compare two sparklines, since each has its own scale. |

When the spec says `"type": "auto"`, `chartspec.py` applies this table, and `chartspec.py choose <spec>` says which row it picked. Override `auto` only with a reason you can state.

Out of scope, on purpose: donut and pie charts (above 3 slices), pyramids, gauges, and diverging stacked columns. Say the finding with a share bar, ranked bars or a `columns` chart with a zero line instead.

## 2. Words

- **Title:** the title is the finding, as a sentence with a verb: «نرخ خرید مجدد در لوازم جانبی نصف بقیهٔ دسته‌ها بود». It is never just the metric («نرخ خرید مجدد به تفکیک دسته»). `chartspec.py validate` rejects titles that only name a metric.
- **Caption (spot charts only):** a chart without exhibit fields carries a caption with the unit, the definition, the window and the source, in that order. In an exhibit the subtitle and the source line do this job. Either way the unit lives outside the plot, never inside it. The exception is a scatter plot's two axis titles, which name the measures.
- **Direct labels:**
  - a value sits inside its bar, centred (white on ink, ink on light grey); a bar too thin for it (under 14px) puts the value outside the end in bold. Never both in one chart. Totals above stacks are bold, segment values regular;
  - the series name at each line's last point, with a short hairline leader (time runs left to right, so that is the right-hand end); stacked segments are named in a column beside the last bar, with leaders;
  - the part name above the first share bar;
  - the before and after names on the first dumbbell row.

  Don't use a legend where a direct label fits. Grouped columns get one small inline key, because they have nowhere else to put it. A stack of 3 or more parts that cannot be labelled directly gets one legend row above the plot, never a box.
- **Annotations** mark the one or two moments that matter: a ring marker with a dotted drop line and an 11px note, or a bracket with the gap named in words and a number. They sit in white space inside the plot and are never a colour spot.
- **Reference lines** (an average, a target, a previous year) are dashed, labelled once with the value in bold italic.
- **Label only what matters:** label the highlighted points, or every point when there are 8 or fewer. Unlabelled grey points are context.
- **Language:** plain Persian. Explain jargon in half a sentence in the caption.

## 3. Colour

- **The accent colour (`--accent`)** marks only what the title is about. That's one item, or a small group, and never more than 7: one focus colour per figure, and never on a total. Figure and table labels (the exhibit number, title, subtitle, notes, table headers) are ink or muted grey, never the accent. A figure uses ink, up to three greys and the accent; a second hue is never added. Everything else is ink (`--ink`) or greys.
- **The palette comes from the theme.** Charts read ink, greys, the ink-to-light-grey ramp and the accent from the theme's palette (`palette=` on a chart function, `theme` in a chart spec), so a theme can restyle every chart without code changes. Square corners and a different accent are palette choices, not rule changes.
- **No highlight:** ink for the main series, and greys for the rest.
- **With a highlight, keep the other parts light:** share charts use light greys, so the accent carries the message instead of fighting a black block.
- **Green** is for validation results only (QA stats), not for "good" bars.
- **Heatmaps** use one hue, a ramp from soft grey to ink. No rainbow palettes and no red-to-green diverging scales.
- **Context goes underneath the highlight.** With many series or rows, draw the ones that are not the point as thin grey lines or light grey bars. Never delete the context; it is what makes the highlight mean something.
- **Unordered categories** (regions, sectors) get an ordered ramp from ink to light grey with direct labels, and the accent only on the segment the title names. They never get four hues.
- **A negative is not a different colour.** Negative bars sit below the zero line in the same ink or grey as positives. The accent goes on a negative only when the loss is what the title says; then it is the highlight.
- **Bridges:** totals and subtotals are ink, steps are mid-grey, and only the step the title names takes the accent, whether it is a gain or a loss. (Without a highlight, a bridge still draws negative steps in the accent, as before.) Connectors are solid hairlines.

## 4. Axes and scales

- **Zero baselines:** bars and columns start at zero, always, and may carry negative values: a 1px zero line is drawn whenever one is negative, and the bars cross it. A dumbbell, line or scatter may start above zero; its tick labels show where.
- **Ticks and grids:** hairline horizontal gridlines only (0.5px, light grey), none vertical. Ticks fall on nice steps (1, 2, 2.5, 5 × 10ⁿ), and no two tick labels are the same. A chart whose every bar carries a value label has no value axis and no gridlines. Dumbbell and dot rows use a light band behind each row instead.
- **Bar proportions:** a bar fills 50 to 65 percent of its slot (gap 35 to 50). Rows are at least 16px high (20px when a label sits inside). Stacks have 1px white seams. Corner radius is a theme choice (the editorial theme rounds 3 to 4px; a report theme may use none).
- **One unit per chart:** never use dual axes. If two measures matter, use a scatter, or two charts.
- **Small multiples** share one y-scale, so the cells can be compared. Say so in the caption.

## 5. Numbers in Persian

- **Digits:** Persian digits everywhere (`charts.fa()`), with «٬» for thousands and «.» for decimals, like the product UI. The font draws «٫» like an apostrophe.
- **Signed numbers** go in an LTR isolate (U+2066 … U+2069), or the sign jumps to the wrong end in RTL: «−۱۲۰». In running text, prefer words: «۴۵٪ رشد», «۲.۷ برابر».
- **The toman sign:** never turn on a font stylistic set (such as `ss01`) in body text or charts if it swaps every «تومان» for a currency sign. Use `.toman-sign` only on a displayed price.
- **Precision:** round to what the decision needs. Two significant figures are usually enough in a chart, and the table can hold the rest.
- **Rounding, one rule per chart:** whole numbers above 10, one decimal between 1 and 10 and for ratios, two only when the decision needs it. Never mix. When rounded shares do not add up to 100, say so in the note: «یادداشت: مجموع ارقام به‌دلیل گرد کردن ۱۰۰ نیست.»
- **True minus and explicit plus:** negatives use the true minus inside an LTR isolate («−۱۷»); change columns and bridge steps carry an explicit plus («+۴»).
- **Missing values are shown, not dropped:** «—» (or «ندارد») in the row. A dot with no partner means the comparison does not exist.

## 6. Direction

- The page is RTL. Category labels sit on the right. Bars grow from the right, and the first group of columns and the first cell of small multiples are on the right.
- Time runs left to right, as in most dashboards and Persian financial charts, with Persian month or day labels. The numeric x-axis of a scatter also grows to the right.

## 7. Anti-patterns (the critic rejects these on sight)

- Pie or donut charts with more than 3 slices. Use a share bar, or ranked bars.
- Dual axes.
- 3D, shadows on data, gradients, and pictograms that stand for quantities.
- Rainbow or categorical palettes where one highlight plus greys would do.
- Bar or column axes that don't start at zero.
- A legend when direct labels fit.
- A title that only names the metric.
- More than one message per chart, or two charts proving the same claim.
- Units repeated in the plot and the caption; or no unit at all.
- A document exhibit without a number, a unit, a source, or a title that is a sentence.
- More than one accent element in a figure, the accent on a total, or a figure label (title, header, note) set in the accent.
- Value labels both inside and outside the bars of one chart.
- A legend box. Use a direct label, or one inline key row.
- A title with a colon, the word «نمودار» in it, or no verb.
- Dropping a missing value silently.
- Donut, pyramid, gauge or diverging stacked columns; more than 6 bands in a stacked area.
- Labels colliding, clipped, or crossing a gridline without a background.
- Sparklines compared with each other as if they shared a scale.

## 8. The critic's checklist (every chart)

1. Does the title state a finding, and does the chart actually prove it?
2. Is the chart type the one section 1 gives for this data and message, or is there a stated reason not to use it?
3. Is the accent colour on the thing the title is about, and only on that?
4. Does every value the text quotes appear in the chart, with the same number?
5. Do bars and columns start at zero, and are the ticks on nice steps without duplicates?
6. Does the caption (or, in an exhibit, the subtitle and source line) give the unit, the definition, the window and the source?
7. Is the direction right: labels on the right, time left to right?
8. Are the digits Persian, with the right separators, signs isolated and no currency sign in text?
9. Is anything overlapping, clipped or too small? Check at full size: 11px minimum for chart text.
10. Could the chart be simpler, with fewer series, fewer labels or a plainer type, and say the same thing?
11. Is the anatomy complete: number, sentence title, subtitle with unit and base, notes, source?
12. Is the highlight the only accent, and is the context grey underneath it?
13. Do rounded values state that they may not sum?
14. Are negatives below a drawn zero line, and are missing values shown?
