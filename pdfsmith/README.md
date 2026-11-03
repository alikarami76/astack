# pdfsmith

Builds checked PDFs from Markdown or HTML, with themes and first-class Persian support.

    bin/pdfsmith new report ./my-report --lang fa
    bin/pdfsmith build ./my-report

Templates: `report` (Markdown), `custom` (your own HTML) and `latex` (a .tex file compiled with tectonic, for academic papers and maths: `bin/pdfsmith new latex ./paper`). The latex route needs `brew install tectonic`; `bin/pdfsmith doctor` reports it. On the latex route `overflow` reads the TeX log: an overfull hbox wider than `checks: {overflow_pt: 2}` (default 2pt) fails the build; `persian`, `digits` and `empty-sections` are skipped. Persian is not available on the latex route yet.

The agent-facing instructions are in `skills/pdfsmith/SKILL.md`. Spec: `docs/superpowers/specs/2026-09-30-pdfsmith-design.md`. What shipped and what was cut is recorded in `docs/superpowers/plans/2026-09-30-pdfsmith-00-roadmap.md`. Run the tests with `uv run pytest`.
