# clu → flynn: handing a finding over as a chart

clu measures, and flynn draws. When clu has a verified finding that needs a chart, it writes a **chart spec**, a small JSON file. flynn turns the spec into a chart in the editorial language (or the theme named in the spec): the theme's font, RTL, Persian digits, the accent colour only on the highlighted thing, and the exhibit anatomy: number, finding title, subtitle with unit and base, notes and source.

clu never styles charts itself, and flynn never changes clu's numbers.

## Where things live

- clu's run folder is `$CLU/runs/<date>-<slug>/`. clu already puts deliverables in `<run>/deliverable/`, including chart PNGs such as `c_category.png`.
- **Specs** go in `<run>/deliverable/charts/<name>.json`, one file per chart, named like clu's existing charts (for example `c_category.json`).
- **flynn's output** goes in `<run>/deliverable/charts/<name>/`, which receives:
  - `chart.png`: 2x, for PDFs, decks and email;
  - `chart.svg`: sharp at any size;
  - `figure.html`: a `figure.chart` block, ready to paste into a flynn HTML report.

`$FLYNN` = `$HOME/agent-stack/flynn`.

## The spec (version 1)

```json
{
  "version": 1,
  "message": "نرخ خرید مجدد در لوازم جانبی نصف بقیهٔ دسته‌ها بود",
  "type": "auto",
  "data": {"items": [["لوازم خانگی", 28], ["کتاب", 26], ["پوشاک", 24], ["سایر دسته‌ها", 22], ["لوازم جانبی", 12]]},
  "unit": "درصد مشتریانی که دوباره خرید کردند",
  "highlight": "لوازم جانبی",
  "definition": "مشتریانی که در ۳۰ روز پس از اولین خرید دوباره خرید کردند، تقسیم بر همهٔ مشتریان اولین خرید",
  "window": "اولین خریدها در مهر ۱۴۰۵",
  "source": "clu run 2026-10-20-repeat-purchase (verified); نمونهٔ ساختگی در این مثال",
  "notes": [],
  "decimals": 0
}
```

The numbers above are fictional.

A full exhibit, with every optional field filled:

```json
{
  "version": 1,
  "exhibit_no": 2,
  "message": "یک‌سوم رشد فروش فصل پاییز از کالاهایی بود که تخفیف نداشتند.",
  "type": "bridge",
  "data": {"steps": [["فروش پاییز ۱۴۰۴", 410, "total"], ["افزایش ترافیک", 62, "delta"], ["افزایش نرخ تبدیل", 48, "delta"], ["کالاهای بدون تخفیف", 71, "delta"], ["مرجوعی", -16, "delta"], ["فروش پاییز ۱۴۰۵", 575, "total"]]},
  "subtitle": {"measure": "تجزیهٔ رشد فروش خالص", "cuts": "پاییز ۱۴۰۴ تا ۱۴۰۵", "unit": "میلیارد تومان در فصل", "base": "همهٔ سفارش‌های تحویل‌شده"},
  "highlight": "کالاهای بدون تخفیف",
  "annotations": [{"at": "کالاهای بدون تخفیف", "text": "۳۶٪ از رشد"}],
  "footnotes": ["بدون سفارش‌های لغوشده."],
  "notes": ["مجموع ارقام به‌دلیل گرد کردن ممکن است با کل برابر نباشد."],
  "source": ["clu run 2026-10-20-autumn-sales (تأییدشده)", "انبار داده"],
  "connectors": "solid",
  "decimals": 0
}
```

The numbers above are fictional. A spec with only the fields of the first example still renders as before (title, chart, caption); the exhibit fields below are all optional.

| Field | Rule |
|---|---|
| `message` | The finding as a sentence with a verb. It becomes the title, so it must be the same sentence as the finding in clu's answer. Specs that only name the metric are rejected. |
| `type` | Use `"auto"` unless there is a reason not to; flynn picks the type from the data shape. The table is in `skills/flynn-core/references/dataviz.md`, section 1. |
| `data` | One of the shapes below. |
| `unit` | Required, and only one unit per chart. Money includes the currency, and the time unit where it applies (one of clu's report rules), for example «میلیون ریال در ماه». |
| `highlight` | The label (or a list of up to 7) the message is about. It becomes the accent colour. `validate` rejects a label that is not in the data. Honoured by bars, lines, `columns` (one column), `heatmap` (cells) and `bridge` (a step). |
| `definition`, `window`, `source` | Without exhibit fields these become the caption. `window` names the population and the period. `source` should name the clu run and whether the number is verified. |
| `exhibit_no` | Optional. An integer, a string such as `"۱۰ الف"` for panels, or `"auto"` (the page numbers charts in document order). Rendered as «نمودار ۲» above the title, in muted grey. Omit it for a spot chart. Requires `source`. |
| `subtitle` | Optional. An object `{"measure", "cuts", "unit", "base"}` or a string (the bold measure part; the unit and base are appended). When absent it is built from `definition`, `window` and `unit`. |
| `base` | Optional shortcut for `subtitle.base`, for example «n = ۳٬۷۶۳ نفر». `validate` warns when the unit is a share of a group («٪ از …») and no base is given. |
| `footnotes` | Optional list of strings, numbered in order. Write the mark in other text as `[1]`. |
| `notes` | Optional list of strings, rendered after the footnotes under «یادداشت:». Put method and rounding here, never the finding. `validate` warns when rounded shares do not add up to exactly 100 and no note mentions rounding. |
| `source` | A string or a list (joined with «؛»). In an exhibit it is rendered as «منبع:» after the notes: the clu run and its verified status first, then the data provider. |
| `zero_line` | Optional bool. Draws the zero line; it is on automatically when a value is negative. |
| `ref_lines` | Optional list of `[value, label]`: dashed reference lines (`bar_h`, `columns`, `line`, `marimekko`). |
| `annotations` | Optional list of at most 2 `{"at": label, "text": ...}`. Drawn by `bridge` (a callout above the step), `line` (a ring and note at that x) and `bar_h` (a note beside the row); other types get a warning. |
| `context` | Optional list of series names drawn as thin grey lines under the highlight (`line` only). |
| `connectors` | Optional, `"solid"` or `"dashed"` (default) for a bridge. |
| `theme` | Optional palette name (`charts.PALETTES` or `themes/<name>/palette.json`). Default is the editorial theme; never put colours in a spec. |
| `decimals` | Precision in the chart. The table in the report can carry more. |

### Data shapes

| Shape | `auto` picks |
|---|---|
| `{"items": [[label, value], ...]}` | `bar_h`, or `stacked_share` when the values add up to 100 and the message is about a share |
| `{"x": [periods], "series": {name: [values]}, "band": [i0, i1, label]}` (`band` is optional) | `line`; `small_multiples` when there are more than 4 series |
| `{"rows": [[label, before, after], ...], "labels": [before_name, after_name]}` | `columns` for 3 rows or fewer; `dot_range` for 4 or more |
| `{"rows": [[label, {part: value}]], "parts": [...]}` | `stacked_share` |
| `{"steps": [[label, value, "total", "subtotal" or "delta"], ...]}` | `bridge` (a subtotal's value is the running total there; `validate` warns when it is not) |
| `{"points": [[label, x, y]], "x_label": "...", "y_label": "...", "quadrants": [x, y]}` | `scatter` |
| `{"rows": [labels], "cols": [periods], "values": [[...]], "highlight_cells": [[row, col]], "bins": [10, 20, 30]?, "legend": true?, "totals": false?}` | `heatmap` (`bins` colours by range with a legend row; `totals` only for additive values) |
| `{"rows": [{"label": ..., key: ...}], "columns": [[key, header, decimals]], "bar": key}` | `table_bars` |
| `{"values": [...]}` | `sparkline` |
| `{"groups": [[label, {part: value}]], "parts": [...]}` | `stacked_columns` (totals on top; add `"change": {"label", "from", "to"}` for a bracket) |
| `{"columns": [[label, width, {part: share} or rate]], "parts": [...]?, "ref_line": [value, label]?}` | `marimekko` |
| `{"x": [periods], "series": {band: [values]}, "stacked": true}` | `stacked_area` (6 bands at most) |
| `{"rows": [{"label": ..., key: ...}], "columns": [[key, header, decimals]]}` (no `bar`) | `table_plain` |

## Two ways to hand it over

**1. One chart, no design decisions.** Most report charts are like this. clu runs the renderer directly:

```bash
python3 "$FLYNN/adapters/html/chartspec.py" validate "<run>/deliverable/charts/c_category.json" \
  && python3 "$FLYNN/adapters/html/chartspec.py" render "<run>/deliverable/charts/c_category.json" --out "<run>/deliverable/charts/c_category"
```

- `validate` exits 1 and lists the problems when:
  - the message is not a finding;
  - the unit is missing or mixed;
  - there are too many highlights, or a highlight is not in the data;
  - a stacked type (`stacked_share`, `stacked_columns`, `stacked_area`) or a table of bars has negative values (`bar_h` and `columns` may be negative);
  - `exhibit_no` is set without a `source`, there are more than 2 annotations, or the title has a colon.

  Fix the spec; don't work around the check. `validate` also prints `warning:` lines (no base on a share-of-group unit, rounded shares with no rounding note, bridge totals that do not add up, annotations a type cannot draw) and still exits 0. A warning is a real defect: fix the spec.
- `render` prints `{"type", "svg", "figure", "png", "font"}`. `font` must be `true`.
- `choose <spec>` explains which chart `auto` picked and why.

Record `chart.png` in clu's run as a deliverable, and use it in the PDF or xlsx the way clu uses its `c_*.png` files today.

**2. A whole report, deck or several related charts.** Call flynn as a teammate, with Agent `subagent_type: "flynn"`. The brief contains:
- the spec paths;
- the audience and format: an HTML report page, a deck or a PDF;
- clu's headline numbers, marked verified or unverified;
- where to write, which is clu's run folder, `<run>/deliverable/`.

flynn follows its teammate protocol. It may send one NEEDS_ALIGNMENT block, for example to confirm the audience or to flag a chart that doesn't prove its message; clu answers from its contract. It ends with a RESULT block:

```
RESULT
run: <flynn run folder>
answer: <what was made, in two sentences>
deliverables:
  - <run>/deliverable/charts/c_category/chart.png — chart, 2x PNG
  - <run>/deliverable/charts/c_category/figure.html — figure block for the HTML report
  - <run>/deliverable/report.pdf — the report page, when a page was asked for
audit: <critic verdict and model>
stored: [...]
caveats: [...]
```

## Who owns what

- **clu** owns the numbers, the finding, the verification label and the choice of what to show.
- **flynn** owns the chart type (within the table), the layout, the colour and the typography.
- If flynn thinks a chart doesn't prove its message, or the numbers in a spec contradict each other (the totals don't add up, or the highlight isn't in the data), it says so in NEEDS_ALIGNMENT or in the caveats. It never changes a number.

## Rendering requirements

Rendering needs Python 3 with `playwright` and Chromium, the same setup as flynn's HTML adapter. With `--no-png`, `render` writes only the SVG and `figure.html`, with no browser needed.
