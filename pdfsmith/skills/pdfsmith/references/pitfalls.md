# Checks and pitfalls

What each check means when it fails, and the usual fix.

| Check | Fails when | Usual fix |
|---|---|---|
| `opens` | the PDF is unreadable | a render error; read stderr |
| `pages` | the page count is outside `checks.pages` | shorten or split the content, or correct the range |
| `fonts` | a glyph came from a system fallback font, not the theme's fonts | add a font that covers the script to `fonts:`, e.g. Vazirmatn for Persian |
| `overflow` | an element runs out of its box or off the page | wrap long tables (`font-size`, fewer columns, landscape), give images `max-width: 100%`, break long URLs; list deliberate cases in `checks.allow_overflow` |
| `leftovers` | `{{ }}`, `{% %}`, `TODO`, `TBD`, `lorem ipsum` or `[object Object]` is in the text | finish the content. Text in code blocks and inline code is exempt, so quoted code may contain `TODO` or `}}` |
| `links` | a link points at a missing anchor or is not `http(s)`/`mailto` | fix the `href` or the heading id |
| `outline` | headings are missing from the PDF bookmarks | use real `h1`–`h3` elements, not styled `div`s |
| `metadata` | the title or language is missing or differs from the source | set `meta.title` |
| `persian` | a Persian word in the PDF differs from the source | usually bidi: keep Latin inside Persian as whole phrases; do not hand-insert RLM/LRM marks |
| `digits` | Latin digits appear in Persian text | write numbers normally; put codes and IDs in `<code>` |
| `empty-sections` | a heading has nothing under it | add content or remove the heading |
| `resources` (warning) | a file or URL could not be loaded | fix the path; remote files need `network: true` |
| `features` | a requested attachment is missing from the PDF | check `features.attachments` paths |

## Known pitfalls

- Do not print with Chrome directly or add `@page :left`/`:right` rules inside the body HTML. Paged.js does not see them, and Chrome then shrinks every page to about 81%.
- Use logical CSS properties (`inline`/`block`, `start`/`end`). Physical `left`/`right` breaks the Persian layout.
- Variable fonts are converted to static instances automatically, because Chrome embeds variable fonts as bitmaps (Type 3). System fonts are used as installed, and only the installed weights exist (a family that ships only its black weight prints all body text black). Some system Persian fonts also break the `persian` check. The bundled Vazirmatn is the safe default; check a system font with `pdfsmith fonts list --filter <family>` first.
- Version numbers such as `2.0.1` inside Persian text become Persian decimals. Put them in `<code>`.
- In `10 GB` inside Persian text, only the unit is treated as Latin. Write `10 GB` in `<code>` if the order matters.
- `{section}` in a header updates only on `h1`.
- `page.margins` must be a string: `"0mm"`, not `0`.
- `pdfsmith pages` needs a PDF without a user password.
