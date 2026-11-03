---
name: pdfsmith
description: "Use for EVERY task that creates, builds, exports, renders or saves a PDF: reports, decks, visual guides, one-pagers, newsletters, release notes, investigation write-ups, analysis deliverables, invoices, forms, academic papers and maths-heavy documents written in LaTeX, or any custom HTML/CSS design that must end up as a PDF, in English or Persian/Farsi (RTL, Jalali dates, Persian digits, any font). Also use to turn Markdown or HTML into a PDF, to attach files to a PDF, and to check a finished PDF or render its pages to PNG for review. Prefer it over make-pdf, reportlab, weasyprint, wkhtmltopdf, pandoc or raw Chrome print. Only for editing an existing third-party PDF (splitting, filling someone else's form, extracting text) use anthropic-skills:pdf instead."
---

# pdfsmith

pdfsmith builds a PDF from Markdown or from your own HTML and CSS, prints it with Chrome and Paged.js, fixes up the PDF (metadata, outline, language, right-to-left reading order, Persian page labels, attachments), and then runs 13 mechanical checks on it. A third route compiles real LaTeX with tectonic. A build passes only if every check passes, so a passing build is a PDF you can hand over.

Use it for every PDF you create. Do not hand-roll Chrome `--print-to-pdf`, reportlab or WeasyPrint: they get Persian shaping, bidi, fonts and page numbers wrong, and nothing checks the result.

```bash
PDF="$HOME/agent-stack/pdfsmith/bin/pdfsmith"
"$PDF" doctor          # once per machine: Chrome, fonts, vendored files
```

## The workflow

1. **Recall.** Read `~/.pdfsmith/lessons.md` if it exists. It holds what earlier runs got wrong. Follow every lesson that matches your template, language or design.
2. **Pick a route.**
   - **LaTeX route**: pick it for academic papers, maths, and anything where real LaTeX typesetting is wanted. See "LaTeX route" below.
   - **Report route** (Markdown content: reports, write-ups, release notes, analyses). Run `"$PDF" new report <dir> --lang en|fa`, then edit `<dir>/doc.yaml` and `<dir>/content.md`. It gives you a cover, a table of contents, headings, tables, code and quotes in the theme. The scaffold carries sample values, so set `name`, `meta.title` and `meta.subtitle` (or delete the subtitle) before you build.
   - **Custom route** (any design of your own: decks, visual guides, newsletters, one-pagers, dashboards on paper, branded layouts). Write `<dir>/doc.yaml` with `template: custom` and `html: page.html`, and put your whole design in `page.html` with a `<style>` block. Use the theme variables (below) so colours and fonts stay consistent. One HTML file can hold many pages: use `break-before: page` between them. For slides, set `page: {size: A4, orientation: landscape}` or a custom size such as `size: 280mm 158mm`, and make each slide a `<section>` with `break-after: page`.
3. **Build.** Run `"$PDF" build <dir>`. stdout prints only the PDF path, and stderr prints `PASSED` or the failing checks. Exit codes: 0 passed, 1 bad input, 2 render error, 3 checks failed, 4 environment missing.
4. **Fix until it passes.** The stderr summary names each failure; open `<dir>/build/report.json` only if that isn't enough. Fix the content or the CSS, not the check. Common fixes are in `references/pitfalls.md`. **Known false positives, so don't rebuild for them:** the `persian` check flags words that combine lam-alef or hamza with a half-space (e.g. فروشگاه, اختلاف‌نظرها), and the `outline` check flags headings that are long mixed Persian/Latin text or contain a half-space. Avoid the outline case up front by keeping headings short. For the persian case, look at the one page where it occurs and report it as a known limitation.
5. **Look at it, in proportion.** Render only the pages at risk (`"$PDF" pages <dir>/out/<name>.pdf --dpi 60 --out <dir>/build/png`) and view one or two yourself: the first content page and any page a check flagged. Use an inspector subagent only for long (more than about 15 pages) or design-heavy deliverables such as decks and newsletters, never for plain text reports or forms. Its brief: "View every PNG in <folder>. Report clipped or overlapping text, mirrored or reversed phrases, wrong digits, empty pages, orphaned headings and broken layout, each with the page number. Do not edit anything." Aim for one build, at most two.
6. **Learn.** If any check failed, the inspector found a defect, or the user corrected the PDF, append one lesson to `~/.pdfsmith/lessons.md` (create the folder if needed), in this form:
   `- YYYY-MM-DD [template/lang/feature] Symptom: … Cause: … Do: …`
   Keep it to one line and make it general enough to help the next run. If the same lesson is already there, change nothing. If a lesson shows pdfsmith itself is wrong (a bug, a missing feature, a check that fires falsely), do not edit pdfsmith's code. Tell the user and propose the change.

## doc.yaml

The full key list and a worked example for both routes are in `references/doc-yaml.md`. The keys used most are:

```yaml
template: report            # or custom, or latex
theme: neutral              # neutral (English) or neutral-fa (Persian); default follows lang
lang: fa                    # en or fa; fa turns on RTL, Persian digits, Jalali dates, ی/ک fixes
meta: {title: …, subtitle: …, author: …, date: 2026-09-30, keywords: [a, b]}
cover: true                 # report route
toc: true                   # report route
header: "{title}"           # tokens: {title} {section} {page} {pages}
footer: "{page} / {pages}"
page: {size: A4, orientation: portrait, margins: 20mm}
content: content.md         # report route
html: page.html             # custom route
fonts: {body: [Vazirmatn, Inter]}   # any installed or project font, by family name
features:
  attachments: [data.csv, query.sql]     # embedded in the PDF (PDF/A-3 style /AF)
checks: {pages: [3, 6]}     # optional expected page range
```

Font files (`.ttf`, `.otf`, `.ttc`) placed in `<dir>/fonts/` are picked up by the build automatically and take priority over bundled and system fonts. Before naming a system font, run `pdfsmith fonts list --filter <family>` and confirm a Regular (400) face exists.

**Not available yet**, and each fails loudly with "not implemented yet" if used: `watermark`, `confidential`, `page.mirror`, `features.encrypt`, `features.merge`, `features.pdfa`, and `data` (JSON input). If you need a draft mark, put it in your HTML. If you need to merge PDFs or add a password, do it afterwards with anthropic-skills:pdf.

## LaTeX route

Use `template: latex` for academic papers, equations, theorems, BibTeX references and any document where you want straight LaTeX. pdfsmith compiles it with tectonic (XeTeX), so fontspec and Unicode work, and then runs the same PDF fix-up and checks.

```bash
"$PDF" new latex <dir>      # doc.yaml + main.tex (A4 article) + references.bib; builds and passes as-is
"$PDF" build <dir>          # compiles, writes <dir>/out/<name>.pdf
```

- **doc.yaml keys:** `template: latex`, `tex: main.tex` (default; the file to compile, run from `<dir>` so `\input` and `\includegraphics` paths are relative to it), `name`, `meta.title|author|subject|keywords` (override what hyperref wrote; if left out, hyperref's values stay), `features.attachments`, `checks.pages`, `timeout`. `content`, `html` and `data` are refused. Only `lang: en`; `new latex --lang fa` and `lang: fa` fail with "not implemented yet".
- **Fonts:** Latin Modern is the default. Put font files in `<dir>/fonts/` and load them by file name with a path: `\setmainfont{Inter.ttf}[Path=fonts/]`. Naming a font family without `Path=` only finds fonts installed on the system. Variable fonts load at their default weight only; use static files for bold and italic.
- **Bibliography:** put `references.bib` next to the .tex and use `\bibliographystyle{...}` and `\bibliography{references}` (the scaffold uses natbib). tectonic runs bibtex and the extra passes for you. An undefined citation or reference shows up as a `resources` warning.
- **Checks:** `persian`, `digits` and `empty-sections` are recorded as "skipped: n/a for latex" in `report.json`. `overflow` reads the tectonic log: an `Overfull \hbox` wider than `checks: {overflow_pt: 2}` (default 2pt) in doc.yaml fails the build, listing up to 5 as `file:lines: Npt too wide`; smaller hboxes and overfull vboxes are warnings. `outline` passes when the PDF has bookmarks (load hyperref) and fails when the source has `\section` but no bookmarks. `fonts`, `links`, `metadata`, `leftovers`, `pages`, `features` and `resources` run as usual.
- **Errors:** a LaTeX error exits 2 and prints the first errors as `file.tex:LINE: message`; the full log is `<dir>/build/main.log`. Fix the .tex and rebuild. A missing tectonic exits 4 (`brew install tectonic`).
- **First run is slow:** tectonic downloads TeX packages and fonts from its bundle on first use (needs network; about 25 s the first time, 2 to 7 s afterwards). `timeout` (default 120 s) covers it.
- `preview` does not apply to this route.

## Theme variables for custom HTML

- Colours: `--color-text`, `--color-muted`, `--color-accent`, `--color-accent-contrast`, `--color-surface`, `--color-subtle`, `--color-border`, `--color-info`, `--color-warn`, `--color-risk`, `--color-success`.
- Fonts: `--font-body`, `--font-heading`, `--font-display`, `--font-mono`.
- Type and space: `--text-base`, `--scale`, `--leading`, `--leading-tight`, `--space`, `--radius`.

Use logical CSS (`margin-inline-start`, `padding-inline`, `text-align: start`, `inset-inline-end`) and never `left` or `right`. The same HTML then works in both English and Persian.

## Persian

With `lang: fa`, pdfsmith does the following for you, so do not do it by hand:
- It sets RTL, and normalises Arabic ي and ك to Persian ی and ک while keeping half-spaces (ZWNJ).
- It converts digits to Persian (`۱۲٬۳۴۵٫۶`, `٪`) and writes Jalali dates.
- It wraps every Latin phrase, URL, email, path, `#tag` and `--flag` in a left-to-right isolate so it is not mirrored.

Write the text naturally. Numbers that must stay Latin (codes, IDs, SKUs) go in `<code>` or inside a Latin phrase, and pdfsmith leaves them alone. The `persian` and `digits` checks confirm that the text in the PDF reads exactly as written.

## Other commands

- `"$PDF" check <file.pdf>` runs the checks on any PDF and prints JSON.
- `"$PDF" preview <dir>` opens the composed HTML in a browser, for design work.
- `"$PDF" fonts list --filter <name>` shows which fonts are available. `"$PDF" fonts scan --project <dir>` registers the fonts in a project folder.
