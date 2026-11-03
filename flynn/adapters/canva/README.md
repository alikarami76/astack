# Canva adapter

This adapter turns a page built with the editorial templates into a **plan** that flynn carries out through the Canva connector. It never calls Canva itself.

**When flynn uses it:** only when someone must keep editing the result in Canva. Anything people only read goes through the HTML adapter instead, and slides someone edits go through the pptx adapter.

## Commands

| Command | What it does |
|---|---|
| `canva_plan.py <page.html> --design <id> --elements <dump.json> [--out <folder>]` | Maps the page's text onto an existing Canva design. It writes `canva-ops.json`, which holds the `page_index` and `operations` for `edit-design` (text replacements only). It also writes `canva-plan.md`: what maps where, what doesn't fit, page text with no slot, template text left unchanged, and what the user must do by hand. `--elements` takes a saved `read-design` response, a single page or a bare page. |
| `canva_plan.py outline <page.html> [--topic ...] [--audience ...]` | Writes `canva-outline.json`, the arguments for `generate-design-structured`, with one outline entry per `data-shot` section. Use it when there's no template to fill. Canva requires the outline to go through `request-outline-review` and get the user's approval first. |

## How flynn runs a plan

1. Call `read-design` on the design to open an editing transaction.
2. Call `edit-design` with the `transaction_id`, `finalize: "keep_open"` and the operations from `canva-ops.json`.
3. Show the user the preview it returns.
4. Only after the user approves, call `edit-design` again with `finalize: "commit"`. If they don't approve, call it with `"cancel"`.

## How matching works

Page blocks and Canva text elements each get a role: title, heading, label or body.

- **Page roles** come from the markup. `h1` is the title and `h2` is a section heading. `h3` to `h6` are item titles and count as body. Small labels (`.k`, `.kicker`, `.sec-label`, `.tag`, `.num` and similar) are labels. The wordmark is skipped.
- **Canva roles** come from font size and weight across the design. The largest size is the title. Larger, bold and short text is a heading. Numerals and small bold text are labels. Everything else is body. The wordmark is left alone.
- **Pairing:** within each role, page blocks in document order are paired with Canva elements in reading order (top to bottom, then right to left).
- **Fit:** a replacement is flagged as not fitting when it's clearly longer than the element was designed for. Canva can't grow the page.

## Limits

- **Fonts:** the Canva Free plan can't use custom fonts, so brand fonts aren't available, and the API can't set fonts at all. The design keeps its template font.
- **Page height:** the API can't change it. Longer copy means the user extends the page in Canva and moves the lower sections down.
- **Images:** uploads and imports need a public URL, so local screenshots can't be sent. The user drags them in from the run's `page/img/` folder.
- **Export:** exports can be blocked for a design, so the user downloads from the Canva editor.
- **Shape:** a template shaped differently from the page maps only partly. `canva-plan.md` shows exactly what's left over, and never hides it.
