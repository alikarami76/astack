# pptx adapter

This adapter turns a deck built from `adapters/html/templates/deck.html` into an editable PowerPoint file. Headings, paragraphs, list items, labels, step numbers, the footer and the slide number all become real, editable text boxes. Cards, pills and rules become shapes; charts and screenshots become pictures.

**When flynn uses it:** when someone has to edit the slides themselves in PowerPoint, Keynote or Google Slides. For a deck that is only presented or sent, the PDF from `render.py --slides` is better, because it looks exactly like the design.

## Commands

| Command | What it does |
|---|---|
| `pptx_export.py <run>/page/index.html --out <run>/deck.pptx` | Measures every slide in a real browser (Playwright) and writes the pptx. Prints how many text boxes, shapes and pictures each slide got. |
| `... --preview` | macOS only. Also renders each slide through Quick Look into `<run>/deck-preview/NN.png`. This shows how a viewer other than PowerPoint draws the file. Fonts that aren't installed are substituted, exactly as they would be for the reader. |
| `... --font "<name>"` | Uses another font name, for example one the audience has installed. |

## How it maps the page

- **Text:** every block that holds text becomes a text box at the same position and width. Paragraphs are right-to-left (`rtl="1"`) and right-aligned unless the page centres them. Each keeps its size, weight, colour and line height, and `<b>` runs stay bold. Persian gets the `fa-IR` language tag and the theme font in the complex-script slot. Each paragraph is wrapped in Right-to-Left Marks, because Keynote, Quick Look and Google Slides otherwise flip the brackets and punctuation at its ends. PowerPoint ignores the marks.
- **Pseudo-elements:** list bullets and CSS counters (the slide number, numbered steps) become text. A counter with a background, such as the step circles, also gets its shape.
- **Shapes:** boxes with a fill or a border become rectangles, with rounded corners when the radius is at least 8px. A border on one side only becomes a thin bar on that side.
- **Pictures:** `<img>`, inline `<svg>` charts and `<canvas>` become pictures cut from a 2x screenshot of the element.
- **Stacking:** shapes go at the bottom, pictures above them, and text on top.

## Limits, stated plainly

- **Font:** the file uses the theme font (Vazirmatn by default) but can't embed it. On a computer without the font, PowerPoint and Keynote substitute another one, and lines may wrap differently. To avoid that, install the font from `adapters/html/themes/editorial/fonts/`, or pass `--font` with a font the reader has.
- **Charts:** they are pictures, not editable PowerPoint charts. To change a chart, change the data in `charts.py` and export again.
- **Effects:** gradients, shadows, masks and CSS transforms are dropped.
- **Text boxes:** each one is 4% wider than on the page, on the side the text grows towards, because PowerPoint's text shaping differs slightly from the browser's. Check long headings in the preview.
- **LibreOffice:** `--preview` uses Quick Look on macOS rather than LibreOffice. Quick Look draws only the first slide of a file, so the preview writes one single-slide file per slide.
