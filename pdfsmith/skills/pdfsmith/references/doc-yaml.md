# doc.yaml reference

Every build folder holds a `doc.yaml`. Unknown keys are rejected, and every error names the key at fault. Paths are relative to the build folder. The output lands in `<dir>/out/<name>.pdf`, where `name` defaults to the folder name.

| Key | Default | Meaning |
|---|---|---|
| `template` | `report` | `report` (Markdown with cover and table of contents), `custom` (your own HTML) or `latex` (a .tex file compiled with tectonic) |
| `theme` | `neutral` for en, `neutral-fa` for fa | a file name in `themes/` |
| `name` | folder name | output file name, without `.pdf` |
| `lang` | `en` | `en`, `fa` or `mixed` |
| `dir` | from lang | `ltr`, `rtl` or `auto`, to override |
| `page.size` | `A4` | `A4`, `A5`, `A3`, `Letter`, `Legal`, `16:9` and `4:3` (slide sizes), or any CSS size such as `280mm 158mm` |
| `page.orientation` | `portrait` | `portrait` or `landscape`; ignored when the size has two values |
| `page.margins` | `"20mm"` | a CSS margin **string**, one to four values. `0` fails; write `"0mm"` |
| `digits` | Persian for fa | `fa`, `latin`, or `fa-body-latin-data` (Persian in text, Latin in tables and code) |
| `calendar` | Jalali for fa | `jalali` or `gregorian`, to override |
| `halfspace` | `off` | `auto` inserts missing Persian half-spaces (ZWNJ); `off` keeps the text as written |
| `meta.title`, `subtitle`, `kicker`, `author`, `subject` | — | written to the cover and the PDF metadata |
| `meta.keywords` | `[]` | a list, written to the metadata |
| `meta.date` | today | `YYYY-MM-DD`, shown in Jalali for fa |
| `cover`, `toc` | `false` | report route only |
| `header`, `footer` | `""`, `"{page} / {pages}"` | margin text; tokens `{title}` `{section}` `{page}` `{pages}` |
| `content` | — | a Markdown file, or a list of them in order (report route) |
| `html` | — | HTML file (custom route); it may also be used in the report route in place of `content` |
| `tex` | `main.tex` | the LaTeX source (latex route only; `content`, `html` and `data` are refused there) |
| `network` | `false` | allow `http(s)` fetches (fonts, images); off by default so builds are reproducible |
| `timeout` | `120` | render timeout in seconds |
| `fonts` | theme's | role → family list, e.g. `{body: [Vazirmatn, Inter], mono: [JetBrains Mono]}`. Roles: `body`, `heading`, `display`, `mono`. Before you name a system font, run `pdfsmith fonts list --filter <family>` and confirm a Regular (400) face exists; only installed weights exist. |
| `features.attachments` | `[]` | files to embed in the PDF |
| `checks.pages` | template's | `[min, max]` expected page count |
| `checks.allow_overflow` | `[]` | CSS selectors allowed to overflow their box |
| `audience` | `internal` | `internal`, `executive` or `external`; recorded, no effect yet |

Not available yet (a clear "not implemented yet" error): `watermark`, `confidential`, `page.mirror`, `features.encrypt`, `features.merge`, `features.pdfa`, `data`.

## LaTeX route

```yaml
template: latex
name: my-paper
tex: main.tex            # default; any .tex inside the folder
meta: {title: …, author: …, subject: …, keywords: [a, b]}   # optional; override or fill the PDF info
features: {attachments: [data.csv]}
checks: {pages: [1, 8]}
timeout: 300             # first run downloads TeX packages
```

Only `lang: en` works on this route (`lang: fa` fails with "not implemented yet"). `theme`, `header`, `footer`, `page`, `cover` and `toc` have no effect: set those in the .tex file. `pdfsmith new latex <dir>` writes a working article (`doc.yaml`, `main.tex`, `references.bib`).

## Report route, Persian

```yaml
template: report
lang: fa
meta:
  title: بازبینی فصلی قیمت‌گذاری
  author: تیم قیمت‌گذاری و پروموشن
  date: 2026-09-30
cover: true
toc: true
header: "{title}"
content: content.md
features:
  attachments: [results.csv]
```

`content.md` is ordinary Markdown: headings become the outline and the table of contents, and tables, code, quotes and images all work. Raw HTML blocks are allowed where Markdown is not enough.

## Custom route, a slide deck

```yaml
template: custom
meta: {title: Q3 Pricing Review, author: Pricing team, date: 2026-09-30}
page: {size: 280mm 158mm, margins: "0mm"}   # or size: "16:9"
header: ""
footer: ""
html: slides.html
```

```html
<style>
.slide { box-sizing: border-box; height: 158mm; padding: 18mm 22mm; break-after: page;
         display: flex; flex-direction: column; justify-content: center; }
.slide:last-child { break-after: auto; }
.title { background: var(--color-accent); color: var(--color-accent-contrast); }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8mm; }
.kpi { background: var(--color-subtle); border-radius: var(--radius); padding: 6mm; }
</style>
<section class="slide title"><h1>Q3 Pricing Review</h1><p>What moved, and what we do next</p></section>
<section class="slide"><h2>Headline numbers</h2><div class="kpis">…</div></section>
```

The same pattern covers newsletters (a masthead plus a two-column CSS grid), visual guides (numbered step cards with images), one-pagers and release notes (a header band, then sections of cards). Put images next to the HTML and reference them relatively.
