# The flynn document format (version 1)

A flynn document is a JSON file that describes one screen, slide, graphic or chart as a tree of **library component instances**. Every tool reads the same file: the Figma, Canva, HTML, PowerPoint and TSX adapters, and Studio. `scripts/fdoc.py validate <file>` checks it against the knowledge store.

## Top level

| Field | Required | Values |
|---|---|---|
| `flynn_doc` | yes | `1` |
| `kind` | yes | `screen`, `slide`, `graphic`, `chart` |
| `system` | yes | a design system name in the store, for example `acme-app` |
| `title` | no | a human title, in the design's language |
| `dir` | yes | `rtl` or `ltr` |
| `lang` | yes | `fa` or `en` |
| `viewport` | yes | `{"w": <px>, "h": <px>}`, positive integers |
| `root` | yes | a node |

## Nodes

```json
{"id": "cta", "component": "Button",
 "variant": {"size": "md", "style": "primary"},
 "props": {"label": "ثبت سفارش"},
 "layout": {"direction": "row", "gap": "space.sm", "padding": ["space.md", "space.lg"], "align": "center", "width": "hug"},
 "style": {"fill": "color.action.primary"},
 "overrides": [],
 "children": []}
```

- `id`: required, unique in the document. Adapters use it as the layer name, and Studio uses it to address comments and edits.
- `component`: required. Either a **primitive** or a component from the document's system in the store.
- Primitives: `Frame` (a layout container), `Text` (needs `text`), `Image` (needs `src` and `alt`), `Icon` (needs `name`). Primitives take no `variant` or `props`. `Text`, `Image` and `Icon` cannot have children.
- `variant`: an object of axis to value. Both must exist in the component spec's `variants`.
- `props`: must exist in the spec's `props`. A prop with `"required": true` must be set, and a prop with `values` must use one of them.
- `layout`: `direction` (`row`, `column`, `stack`), `align` (`start`, `center`, `end`, `stretch`), `justify` (`start`, `center`, `end`, `space-between`), `width` and `height` (`fill`, `hug`), plus `gap` and `padding`, which are tokens. `padding` may be a list of one to four tokens, in CSS order.
- `style`: `fill`, `color`, `stroke`, `radius`, `elevation`, `typography`. Every value is a token name from the system, such as `color.surface`.
- `overrides`: style or layout keys on this node that are allowed to hold a non-token value. An override is a warning, not an error. Studio writes overrides when the user uses an off-system value, and the curator then asks whether it should become a proposed token.

## Issue codes

Errors (the document is rejected): `version`, `bad-field`, `no-root`, `bad-node`, `bad-id`, `duplicate-id`, `no-component`, `missing-field`, `primitive-has-props`, `unknown-component`, `unknown-variant-axis`, `unknown-variant-value`, `unknown-prop`, `bad-prop-value`, `missing-prop`, `unknown-style-key`, `bad-layout`, `off-system-value`, `leaf-children`.

Warnings (allowed, but they must be mentioned when presenting): `tier` (the component is `drift`, `inferred`, `proposed`, `figma-only` or `code-only`), `off-system-override`, `empty-system`.

## Not covered yet

- Responsive behaviour across breakpoints
- Slide-specific fields such as speaker notes and deck-level theme
- Chart data binding
