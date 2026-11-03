# flynn panel (the flynn tab in flynn desk)

The flynn tab sits next to Design, Prototype and Inspect in Penpot's right sidebar. You talk to flynn there: it is the same flynn as `flynn` in the terminal (same agent, skills, store and run folders), run through `claude -p --agent flynn` on your own Claude Code login. No API key or extra token is needed.

## Run it

The whole app: `bin/flynn-desk` starts whatever is not running (the app's containers, this chat server, Studio and flynn's Chrome) and opens flynn desk. `bin/flynn-desk status` shows what's running, and `bin/flynn-desk stop` stops it (`stop --all` also stops the containers).

This server alone:

```bash
python3 "$FLYNN/adapters/penpot/panel/panel_server.py" serve          # http://127.0.0.1:8772
```

Penpot must run with the overlay (`adapters/penpot/stack/docker-compose.flynn.yaml`), which adds the tab, the `/flynn/` files and the `/flynn/api/` route to this server, and with the flynn branding (`docker-compose.brand.yaml`, made by `stack/brand.py`):

```bash
cd "$FLYNN/adapters/penpot/stack" && PENPOT_SECRET_KEY="$(security find-generic-password -s flynn-penpot -a secret-key -w)" \
  docker compose -p flynn-penpot -f docker-compose.yaml -f docker-compose.flynn.yaml -f docker-compose.brand.yaml up -d
```

## Branding

The app shows itself as **flynn** everywhere: name, logo (the disc), favicon, page titles and every translation. `stack/brand.py` copies Penpot's built files out of the image, renames only what people see (code names, links, the uppercase `PENPOT_*` keys and the MCP connector's name stay), swaps the logo symbols in both sprites and in `index.html`, and writes `docker-compose.brand.yaml`, which mounts the rewritten files. Run it again after every Penpot upgrade, then recreate `penpot-frontend`. flynn is told to call the app the canvas or flynn, never Penpot.

## Glass theme

flynn's look, always on in the dark theme: `www/tron.css` and `www/glass.js`.
- **Surfaces:** cool glass sidebars, a frosted toolbar and frosted menus.
- **Light:** outlines and selections are rim-lit in ice blue; lines never get thicker. Selected layer rows get one flat tint with a lit blue name, and hover gets a fainter tint with white text.
- **Motion, in the style of Liquid Glass:** a capsule glides between tabs, colours fade, buttons give a little when pressed, and menus fade in.
- **Speed:** only fixed paint effects are used, blur appears only on small floating surfaces, and there is no motion when the system's reduce-motion setting is on. `glass.js` watches each tab list on its own and measures at most once a frame.
- **Light theme:** untouched.

## Layout

- **Right sidebar width:** drag its left edge to resize it from 318px up to 768px, never more than 60% of the window. Double-click the edge to reset; the width is remembered. Penpot writes its own width inline, so the chosen width is applied through `--flynn-right-width` and an `!important` rule. The drag also ends cleanly when the mouse button comes up elsewhere, so the panel never stays in resize mode, which would block the chat.
- **No Enterprise prompts:** `www/declutter.css` hides the Enterprise banner and upgrade prompts, and `inject.js` closes any Enterprise popup with its own close button.

## How a turn works

1. You type in the tab. The server runs one turn of `claude -p "<message>" --agent flynn --output-format stream-json --include-partial-messages`, in the flynn folder, resuming the Claude session that belongs to this Penpot file (`--resume`).
2. The stream becomes events (`stream.py`): text as it is written, progress notes, one line per tool flynn uses, and the end of the turn.
3. flynn works in teammate mode here, because it cannot call AskUserQuestion. Its `NEEDS_ALIGNMENT` blocks become question cards with flynn's recommended answers as buttons; your answers go back as an `ALIGNED` message in the same session. `RESULT` and `BLOCKED` blocks become cards too.
4. Each Penpot file has its own conversation, kept in `~/.flynn/panel/<file id>/` (`events.jsonl`, `state.json`), so reloading Penpot shows the history. "New" starts a fresh conversation and keeps the old one in the history list (below).

## Everything on the canvas

flynn desk is the only place flynn works from this tab; there is no Canvas/Studio switch any more .

- After every flynn turn, each run mentioned in the conversation whose pages changed is built in this file (`../push.py`), on a page named `flynn · <run>`, one board per option. Boards you edited are never overwritten; the new build goes beside them as `… · v2`. Each run also gets a row with **Build here** to rebuild it now.
- Studio is decoupled. flynn is told not to send you there, and starts it and gives you the link only when you explicitly ask to put the designs in Studio. Studio itself still works as before (`studio.py serve`).
- Before revising a design built on the canvas, flynn runs `push.py pull <run>`: a PNG and the texts of every board go to `<run>/penpot-pull/`, with which boards you edited, so your edits carry back into the page.

## Conversation history

The clock button in the panel header opens **Conversations**: the open conversation and every past one in this file, grouped by day, with search. Opening a past one brings back its messages and resumes its Claude session, so flynn remembers it; the conversation you leave goes into history. **New** (or + in the header) starts a fresh conversation and keeps the current one in history. Hovering a past conversation shows a delete button; its designs and runs are kept.

Stored in `~/.flynn/panel/<file id>/history/<id>.jsonl` (events) and `<id>.json` ({title, updated, messages, session, system, runs}). Routes: `GET /api/conversations`, `POST /api/conversations/open` and `/delete`. Archives from before the list (`events-<stamp>.jsonl`) are moved in on first use.

## What flynn knows about your Penpot view

Every message carries a `<penpot_context>` block, read through the connector just before the turn: the file, the page, and up to 12 selected elements (name, type, text, component, path, top-level board, position on that board), plus a PNG of the selection. On a `flynn · <run>` page it also names the run. "This", "here" and "it" mean the selection. The chat shows a small line under your message with the page and what was selected.

## The recorder

The ring button in the canvas toolbar records a live product and builds its components and tokens on a page; see `adapters/recorder/README.md`. The server side is `record_api.py` (`/api/record/start`, `capture`, `stop`, `state`), and each step appears in this chat as a `recording` event. Switch it off with `{"recorder": false}` in `~/.flynn/features.json`.

## Figma import and library sync

- **Figma button:** in the toolbar, it sends a page link to `/api/figma/import`. The server checks the link's shape and starts a flynn turn with the import brief. Only Figma's read-only tools are allowed (`whoami`, `get_metadata`, `get_design_context`, `get_screenshot`, `get_variable_defs`). See `adapters/figma/README.md`.
- **Library icon:** in the tab header, it calls `/api/library/sync`, which syncs every canvas library in the builder's browser and reports `library` events in the chat.

## In the message area

- **Selection box:** shows what is selected on the canvas (count, names, a small preview), which goes to flynn with your next message. **×** leaves it out of that message. It comes from `/api/selection` (a light query to the canvas connector, about every 1.2 s while the tab is visible).
- **Picker chip** (under the field): chooses the design system for this conversation. In Designs these are the libraries, one per design system; in Letters, the document themes. The choice is saved per file (`/api/system`), and flynn designs only from it.

## Design system changes

flynn never edits a design system directly. When a recording, or something you say, means a system should change, flynn proposes a change (`adapters/library/changes.py propose`). The tab shows it as a card with old → new for each item and **Approve** / **Reject**. Approving updates the store, then syncs that system's library, keeping component ids. A recording's review proposes one change for the whole update.

## Questions

flynn's questions arrive as cards. Each question has 2 to 4 options (`options: a | b | c` in the block), shown as choices with flynn's recommendation preselected; `multi: yes` makes them checkboxes. A free-text field is always there for anything else.

## Look and language

The panel loads Penpot's own stylesheet, theme, icons and design-system classes (buttons, tab switcher, notification pills, comment input), so it follows Penpot's dark and light themes. Latin text uses Penpot's Work Sans; Persian uses Vazirmatn (`../stack/flynn-overlay/www/fonts/`). Every paragraph sets its own direction, so English and Persian both read correctly. flynn writes in English by default and switches to Persian when you write in Persian.

## Permissions

- Allowed up front: reading, searching, skills, subagents, web search and fetch, `python3` scripts inside the flynn folder, and writing inside `runs/`.
- Always refused: writes to `~/code`, and `git push`.
- Anything else runs under Claude Code's auto mode; whatever would need an approval is refused automatically and shown in the tab as a card. "Allow and continue" adds that exact rule for this conversation only and resumes flynn.

## Debugging

- `~/.flynn/logs/panel_chat.log`: one line per turn start and end, and every allow.
- `~/.flynn/logs/panel_server.out`: the server's own output.
- `~/.flynn/panel/<file id>/events.jsonl`: every event the tab received.
- `curl http://127.0.0.1:8772/api/health`, and through Penpot: `curl http://127.0.0.1:9001/flynn/api/health`.
- Switch it off: `~/.flynn/features.json` → `{"panel_chat": false}`.
- Tests: `tests/test_panel_stream.py`, `tests/test_panel_server.py` (a fake `claude` replays a recorded stream).

## Notes

- Port 8772, because 8771 was taken by another local server. Change both `FLYNN_PANEL_PORT` and the overlay's nginx file together.
- The overlay mounts single files into the Penpot container. Edit those files in place; a tool that replaces the file (for example `sed -i`) leaves the container pointing at the old copy, and the fix is `docker compose ... up -d --force-recreate penpot-frontend`.
- Drawing into Penpot from the chat comes with the bridge and the page push.

## Tools and engines

flynn works in flynn desk through **tools**, not text it writes for the server to read. `desk_tools.py` is the one list: each tool has a name, a description, an input schema and a handler that runs in the panel server next to its conversation.

| Tool | What it does |
|---|---|
| `ask_user` | a question card (brainstorm, contract, options); flynn ends its turn and the answers come back as `ALIGNED` |
| `show_result` | a result (or blocked) card |
| `canvas_selection`, `canvas_build`, `canvas_pull` | what is selected; build a run's boards now; read the user's edits back |
| `libraries`, `library_sync`, `propose_ds_change` | design system libraries; changes wait for the user's Approve |
| `prototype_outline`, `prototype_propose` | read a page's screens and hotspots; propose links (a flow, its first screen, navigate / pop-up / close / back / URL, with slide or dissolve). The user approves the card ("Apply links"), then presses Play |

The old NEEDS_ALIGNMENT / RESULT / BLOCKED text blocks are still parsed, as a fallback.

**Engines** (`engines.py`) run one turn and all produce the same panel events. One setting for the whole app, `~/.flynn/desk.json` `{"engine": ...}`, also changed with `/settings`:

- `local`: the user's Claude through the Claude Agent SDK (`pip install claude-agent-sdk`). The desk tools reach it as a stdio MCP server (`desk_mcp.py`), which forwards each call to `POST /api/tool`.
- `cli`: the same through `claude -p --mcp-config` (the original path; the default until the SDK engine has been tried on real work).
- `api`: later, the Messages API with tool use: `desk_tools.schemas("api")` and `desk_tools.call()` in-process. Choosing it now says it is not available.

`FLYNN_ENGINE` overrides the setting (the tests use `cli`).

## Slash commands

Type `/` in the message field. `commands.py` holds the list (`GET /api/commands`, `POST /api/command`); Letters files get `/chart` and `/export`, Designs files `/record` and `/figma`.

- Canvas: `/record`, `/figma [link]`, `/prototype [page]`, `/pull [run]`
- Design systems: `/use <library>` (or `none`), `/sync`, `/library <name and source>`, `/ds <what changed>`
- Letters: `/chart <what to show>` (brings in clu for the numbers when needed), `/export`
- Teammates: `/clu <question>`, `/quorra <what to build>` (quorra builds in its own work clone and never ships without the user), `/studio`
- Conversation: `/new`, `/history`, `/attach`, `/context`, `/usage` (this conversation and today, from the turn costs; plan limits live at claude.ai/settings/usage), `/settings`, `/stop`, `/help`

## Attachments

The paperclip next to the library chip, dropping files on the message field, or pasting an image. Files up to 25 MB, ten per message, are kept in `~/.flynn/panel/<file>/uploads/` (local only) and listed in flynn's prompt with their paths; flynn reads them with Read. `POST /api/upload` {file, name, type, data (base64)}; `send` takes `attachments` (upload ids).

## More

- **Letters are a file type**, like Figma Design and Figma Slides: any team or project holds both. The dashboard's Designs / Letters switch filters the files by type (remembered per browser), and **+ New letter** creates the letter in the team and project you are in. The old Letters team is empty and can be deleted.
- **Fonts:** `adapters/library/fonts.py sync` uploads the editorial theme's font and Vazirmatn to every team, so builds and libraries use the real fonts.
- **+ button** left of the library chip: opens the command list (same as typing /).
- **Skills:** `/skill <what it should do>` has flynn draft a flynn skill and call `propose_skill`; the card shows it and **Install skill** puts it in `skills/<name>` and `~/.claude/skills`. `/brainstorm` runs the flynn-brainstorm skill (flynn as a design lead).
- **Status line** above the message box, in light orange, with a spinning orange-lit disc while flynn works.
- **Contrast** tuned for budget IPS and TN panels: lighter background steps, brighter secondary text, stronger hairlines and selection.
- **Menus over the tab:** the flynn tab sits at z-index 300 and the right sidebar no longer makes its own stacking context, so Penpot's menus (zoom, share) open above it.
