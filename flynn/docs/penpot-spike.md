# Penpot trial: findings and decision

Run: `runs/2026-10-04-penpot-spike/`. Penpot 2.18.1, self-hosted in Docker on 127.0.0.1. Done by flynn at the user's request: the account, fonts, MCP key, Persian typing test and the gate itself.

## Decision: go

Persian right-to-left text, the plugin API, components, variants, tokens, comments and versions all work well enough for flynn to build on Penpot 2.18.1. The only RTL failure is a known bug (#11523, fix scheduled for 2.19) that does not affect anything flynn generates, because flynn's output always sits inside auto-layout boards. The fork can start as a **mounted overlay on the stock frontend image** (no rebuild), which is cheaper and safer than the source patch the plan assumed; the source build stays as the fallback for anything the overlay cannot do.

Pin Penpot 2.18.1 now. Re-run the right-to-left test matrix and the Persian typing test when 2.19 ships, then move the pin.

## Setup

- Compose file from the 2.18.1 tag in `adapters/penpot/stack/`, ports bound to 127.0.0.1, the secret key and flags from the environment; the secret lives in the keychain (`flynn-penpot` / `secret-key`).
- Docker needed Docker Desktop's tool folder on PATH for the credential helper (`/Applications/Docker.app/Contents/Resources/bin`). Image download was slow (about 20 minutes for 2.5 GB). Two parallel `compose up` runs collide; run one at a time.
- Account `owner@flynn.local`, team `flynn`, an access token and the MCP key were created through the RPC API (`setup_account.py`, `mcp_enable.py`); the password, token and MCP key are in the keychain. Fonts go through the 2.18 chunked upload: `create-upload-session`, `upload-chunk` (multipart fields must be kebab-case: `session-id`), then `create-font-variant` with `uploads`. All eight Vazirmatn weights are in the team.
- **The MCP connector is built into Penpot 2.18.** Once the profile has `mcpEnabled` and an MCP-type access token exists, the bundled "Penpot MCP Plugin" starts by itself in every open file (it runs in the background, no manual loading). The server is reached through Penpot's own nginx at `http://127.0.0.1:9001/mcp/stream?userToken=<key>`; no extra ports. Tools: `execute_code`, `high_level_overview`, `penpot_api_info`, `export_shape`.
- flynn's Chrome (`capture.py` profile) logs in and keeps the file open; the editor shows "● MCP" when connected.

## Plugin API

Names are in `runs/.../api-names.md` (from `@penpot/plugin-types` 1.5.0; 1.4.2 has no tokens). Probe results:

| Capability | Result |
|---|---|
| Boards, text, flex (incl. `row-reverse`), grid, per-corner radius, shadows | ok |
| Fonts (`fonts.findByName`, `applyToText` with a weight variant) | ok |
| Images (`uploadMediaData`, `createShapeFromSvgWithImages`) | ok (with a valid PNG) |
| Components (`library.local.createComponent`) | ok |
| Variants (`createVariantFromComponents`, `transformInVariant`, `addVariant`, `setVariantProperty`) | ok; `library.local.components` lists only one component per variant set, so reach variants through the variant container's children |
| Switch an instance's variant (`switchVariant`) | ok; the instance is replaced, so look it up again by name |
| Tokens (`library.local.tokens.addSet`, `addToken`, `shape.applyToken`) | ok; the token applies a moment later (asynchronous) |
| Comments (`addCommentThread` at a page position, `reply`, `resolved`) | ok; threads are anchored to positions, not to layers |
| Versions (`currentFile.saveVersion`) | ok |
| Code generation (`generateMarkup` html, `generateStyle` css) | ok; a starting point for board-to-HTML |
| Speed | 500 Persian text layers in 5.2 s; 500 auto-layout boards in 3.3 s, far inside the 120 s tool limit |

Corrections to the plan: text alignment is `align`, not `textAlign`; plan board-to-HTML on `generateMarkup`/`generateStyle`; comments need the layer name in the body to link them to layers.

## Right-to-left

See `runs/.../rtl/rtl.md`. All eight cases pass in both renderers, including mixed Persian and Latin text and typing Persian in the editor. Free-floating auto-width text grows the wrong way while typing (#11523), in both renderers.

## Fidelity baseline

Building the eight RTL cards by hand-written plugin code took about ten minutes of authoring and matched Chrome closely (`penpot-default-export.png` against `reference.png`). That is the baseline automatic capture must beat. `export_shape` returns 1x PNGs; use the plugin API's `shape.export({type: 'png', scale: 2})` for review images.

## The fork

Two routes were tested.

**Overlay on the stock image (works, recommended to start).** `docker-compose.flynn.yaml` mounts three things into the stock `penpotapp/frontend:2.18.1` container: an nginx location file in `overrides/server.d` (Penpot includes that folder), a copy of `index.html` with one extra `<script src="/flynn/inject.js">`, and flynn's static files under `/flynn/`. The injected script adds a **flynn tab next to Design, Prototype and Inspect** in the right sidebar, built from Penpot's own tab classes (`main_ui_ds_layout_tab_switcher__tab`, readable rather than hashed), so it has the same look, font (`worksans, vazirmatn`) and selected state. When selected, the flynn panel (same-origin iframe) covers the tab panel area exactly and copies Penpot's font and colour variables; clicking a native tab hands control back. A small style rule sizes the four tabs to their labels so none is cut. Screenshots: `tab-flynn.png`, `tab-design.png`.

Limits of the overlay: it depends on Penpot's class names and DOM structure, so each Penpot upgrade needs a quick check; it cannot change Penpot's ClojureScript behaviour.

**Source build (fallback).** Penpot 2.18.1 source is in `~/.flynn/penpot-src` with a one-line patch to `workspace.scss`. The build runs inside Penpot's 2 GB development image (`./manage.sh build-frontend-bundle`) and includes the Rust renderer and the MCP plugin. Result: see "Source build result" below.

## What changes in the modular plan

- The fork base starts from the overlay, not a source patch: the flynn tab, the `/flynn/*` route and the injected script. The source build is used only for changes the overlay cannot make.
- The host bridge can copy Penpot's own pattern: the bundled MCP plugin runs in the background in every file with a token and a server URL; flynn's bridge can do the same through the MCP connection that already exists, so it may be small.
- `bridge.py` uses `http://127.0.0.1:9001/mcp/stream?userToken=<key>`; no separate MCP ports.
- Text alignment is `align`; tokens need plugin-types 1.5.0 or later.

## Source build result

The patched frontend built in 12 minutes inside Penpot's development image (`./manage.sh build-frontend-bundle`, which also builds the Rust renderer and the MCP plugin), with no errors. The one-line patch (`inline-size: var(--flynn-workspace-inline-size, 100vw)`) is in the compiled `css/main.css`. The bundle is in `~/.flynn/penpot-src/bundles/frontend`. So the source route is practical as a fallback: about 12 minutes per build plus a 2 GB image, on top of the overlay.

## Added after the trial: the flynn tab talks to flynn

At the user's request, the chat part was built straight after the trial. The flynn tab now runs real conversations with flynn through `claude -p --agent flynn` on the user's Claude Code login, with streaming text, tool activity, question cards from `NEEDS_ALIGNMENT` blocks, result cards, and approval cards for refused tool calls. See `adapters/penpot/panel/README.md`. Tested end to end: a store question answered in 30 seconds, and a real brief produced flynn's brainstorm card in 84 seconds.
