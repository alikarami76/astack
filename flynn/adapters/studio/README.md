# flynn Studio

Studio is one local place for everything flynn makes. You can see every run, compare a run's options side by side on a canvas, step through its versions, comment on the live page, pick the winner and export it.

```
python3 "$FLYNN/adapters/studio/studio.py" serve          # http://127.0.0.1:8770, opens the browser
```

## What you see

- **Home:** every run as a card with a thumbnail, its type, options, versions, open comments, the decision and any exports still waiting. You can search and filter by open, has comments and decided.
- **Run canvas:** each option (`page/` is option A, and `options/<name>/page/` are the others) appears as a full-height frame on a pannable, zoomable canvas. Drag to pan, scroll to move, and ⌘ or Ctrl plus scroll to zoom.
  - Tick a version in the side panel to lay it next to the current page.
  - Frames reload by themselves when flynn re-renders.
  - "Comment ↗" opens the page with the review layer (see `../review/`): press C, click anything, type.
  - "Pick this" records the decision in `<run>/studio.json`.
- **Exports** for the selected option. The first six run locally and finish at once; the last two are queued for flynn:

| Export | What you get | How it runs |
|---|---|---|
| PDF | The page, or a deck as one page per slide. | Local, through `adapters/html/render.py`. |
| PNG | A zip of the section images, or of the slide images for a deck. | Local. |
| HTML | A zip of the page folder. | Local. |
| TSX | A React component in common React conventions. See below. | Local, through `html_to_tsx.py`. |
| PPTX | An editable PowerPoint file, for decks. | Local, through `adapters/pptx/pptx_export.py`. |
| Email | An Outlook draft (.eml), for newsletters. | Local, through `adapters/html/email_pack.py`. |
| Figma | The page as Figma layers. | Queued for flynn. |
| Canva | A Canva design. | Queued for flynn. |

Exports land in `<run>/exports/` and are listed in the panel with links.

## Figma and Canva exports (flynn's side)

Studio can't talk to Figma or Canva itself; flynn has those connectors. A click in Studio queues a request:

```
studio.py requests                       # what is waiting
```

- **Figma:** call `generate_figma_design` with the target `fileKey`. Use a file the user named, never a shared library file, and create a file with `create_new_file` if the user named none. Save the returned capture script to a file, then run `studio.py figma-script <run> <id> <script file>`. It prints the page URL with `?capture=<id>`, and Studio injects the capture script into that page. Open the URL in a browser, poll `generate_figma_design` with the `captureId` until it completes, and then run `studio.py done <run> <id> --link <figma url>`.
- **Canva:** run `adapters/canva/canva_plan.py` for the page, apply the plan with the Canva MCP tools to the design the user named, then run `studio.py done <run> <id> --link <canva url>`. If Canva's limits block it (fonts, exports, public URLs), run `studio.py failed <run> <id> --reason "..."`; the reason shows in Studio.

## TSX export (`html_to_tsx.py`)

The export follows these React conventions (checked by quorra against a real front-end repo); adjust them to your own repo's conventions:

- React 18 function components, as named arrow exports: `export const X: React.FC<XProps>`, with `export interface XProps`.
- A co-located `<name>.module.scss`, and classes used as `styles.x`.
- No `dir` and no font-family, because the repo sets RTL and the font globally.
- Every Persian string lives in `constants.ts` as `TRANSLATIONS`.
- Images are imported from `./assets`.
- Imports stay inside the folder: no `../`.
- Prettier-style code: 4 spaces and single quotes.

Verified: the generated TSX passes `tsc --strict --noImplicitAny`, and the SCSS compiles with dart-sass.

Not done yet: it emits raw elements rather than your repo's own component library, because that library's props haven't been mapped to flynn's components. A developer swaps in the repo's Button, Tab and Table components where the page uses those patterns, then runs the repo's formatter and linter.

## Versions

- `studio.py snapshot <run> --label "after round 2"` freezes every option into `versions/NN-<label>/`. flynn does this after each audit round, and the user can with the "Save current as a version" button.

## Chat (Module 1 of Studio with AI)

Talk to flynn inside Studio. On a run's canvas the right panel has two tabs: **Chat** and **Details** (the brief,
decision, exports, versions and comments, as before). Home has its own conversation for new work, with starter chips.

- **Conversations:** one per run (`run:<id>`) and one on Home (`home`), stored in `~/.flynn/studio/chats/<key>/`
  (numbered `events.jsonl`, `state.json`, `uploads/`, `history/`). "New chat" archives the current one; History reopens it.
  When a Home turn creates a run, the thread moves to that run's chat.
- **What flynn sees:** each message starts with a short `<studio_context>` (run, option and version in view, the element
  you pointed at, open comments, the effort line). The long Studio instructions go only with a session's first turn.
- **Select elements** (crosshair button): click elements in any frame to add them (click again to remove; Done, Enter or
  Esc finishes). They keep a lit outline and show as chips above your message; up to 8 go with it as context, each with
  its selector, option, page, text, a trimmed HTML snippet and its key styles, so "this" and "these" mean them.
- **Tools flynn gets in Studio** (`studio_tools.py`, served per turn by `studio_mcp.py`): `ask_user` (question cards),
  `show_result`, `studio_view`, `studio_comments`, `studio_snapshot`, `studio_focus`, `studio_export`.
  Tool calls carry a per-process token; only turns Studio started can use them.
- **Engine:** flynn desk's engines (`adapters/penpot/panel/engines.py`): the Agent SDK by default (Studio adds a Stop
  that reaches a silent turn), the claude CLI as a fallback. Same permissions as the desk (`perms.py`): no writes under
  your company code folders, no `git push`; "Allow and continue" accepts only rules flynn asked for.
- **Design system** (a chip in the composer, beside Effort; Home and run conversations): **Flynn chooses** (default, Flynn
  picks from the brief), any design system (**Product UI**) or document theme (**Documents**) Studio knows, or **No design
  system** (plain, well-crafted HTML that still follows flynn's rubric). It can change only before the conversation's first
  message; then the chip is read-only ("Start a new conversation to use another design system"), and the server refuses a
  change with 409. It is stored in the conversation's `state.json` (`design_system`; per person on a team server), through
  `GET/POST /api/chat/design_system` (the first `send` can carry `design_system` too). A run's conversation starts with the
  system its `run.json` names (`system`, `theme`, or `design_system: "none"`), locked. A run made from a conversation gets
  the choice in `run.json`. Every turn tells the agent the rule in its system text, on every engine (Agent SDK, remote
  agent, model API): use only that system, or none. The MCP tools `studio_create_run` and `flynn_ask` take an optional `system`.
- **Effort** (per message, remembered per conversation; caps in Settings):

| Level | Model, effort | Specialists and audit | Cap |
|---|---|---|---|
| Quick | Sonnet, low | checker on Haiku, lint only | $1 |
| Balanced | Opus, medium | usual mix, one Sonnet critic round | $3 |
| Thorough | Opus, high | up to three rounds, Opus final critic | $8 |
| Max | Opus, max | Opus composer and critic where useful | none |

  Caps stop a turn early on the SDK engine only; a capped turn offers "Continue at <next level>".
  This applies only to Studio's chat, never to the flynn agent you run directly in `claude`.
- **Live connection:** one WebSocket (`/api/chat/ws`), SSE fallback (`/api/chat/events`); events are numbered and the list
  has an epoch, so a reconnect resumes exactly and a new chat resets every open tab.
- **Log:** `~/.flynn/logs/studio_chat.log` (`turn start` / `turn end` with engine, model, effort, cost, seconds).
  **Before restarting Studio, check it has no `turn start` without its `turn end`** (Settings shows a warning too).
  A restart closes cut-off turns with a note.

## Tools tab (run canvas)

The run canvas's right panel has two tabs in their own row: **Chat** and **Tools**. Tools holds the run at a glance (brief,
type, system, open page, copy run path) and:

- **Decision:** pick an option.
- **Comments** (switch `studio_comments_ai`): every thread with where it sits; **Ask Flynn** sends one comment with its
  element as context; **Fix all open** hands every open comment to Flynn; **Show** jumps to the pin; **Reply** (start
  with `@flynn` or `@quorra` to hand it over); **Resolve / Reopen**; and a box to comment on the elements you selected.
- **Edits** (switch `studio_quick_edit`): turn on **Edit on canvas**, double-click text to change it, Alt-click an element to
  hide it. Edits are stored in `<run>/edits.json` and shown at once; **Apply with Flynn** carries them into the source
  page (tool `studio_edits`), and **Undo** withdraws one.
- **Code** (switch `studio_quorra`): switch the chat to **quorra**; for UI runs, **Build in the product's front-end repo** has
  quorra export the TSX, map it to its UI kit in its work clone, run the checks and show the publish plan. Nothing is pushed
  until you approve. MR links come back through `studio_link` and show under **Links**.
- **Export** and **Versions**, as before.

## Title cards

Each run can have a title card (cover): a 1600x1000 card that says what the run is. In the Tools tab, **Title card** has
**Generate title card** (and **Regenerate** once one exists, with a preview). The card then shows as the first frame on the
canvas, titled "Title card", with only a Regenerate button (no Pick or Comment), and it becomes the run's thumbnail on
Home (`cover/cover.png` before `shots/full.png`). Code: `cover.py`, templates in `cover_templates/`.

- **Facts it uses:** run name and date, `run.json` (request, type, system, template, outcome), the kind from
  `classify()`, each option's page title and first headings, versions, comments, the picked option, and the design
  system's line from the Systems page (for example "Acme Console · 148 library components", or "Editorial · document theme").
- **Copy:** the title (60 characters), a one-line subtitle (110) and 2 to 4 chips are written by Flynn in a turn with no
  tools, at the Quick level (Sonnet, low effort), through the engine the person's chat uses: the model API when they chose
  it in Settings, the flynn agent on their own computer on a team server, otherwise the local Agent SDK (or the CLI). It
  asks for strict JSON in the run's language (Persian for RTL pages) and tolerates code fences and extra text. If no engine
  is available, it fails, or it takes over 60 seconds, the card is written from the facts alone and the record says
  `"copy": "fallback"` with the reason.
- **Templates** (one HTML file per kind, filled with escaped values): `ui`, `release`, `newsletter`, `report`, `deck`,
  `chart` and `generic` (system learning, questions and anything else). Each has its own layout: browser frames side by
  side, a tall page beside the text, a paper masthead with sheets, a report page stack, a slide strip, one large chart,
  a centred title. Thumbnails of up to three options are taken with the same headless Chromium as exports.
- **API:** `POST /api/run/<run>/cover` returns 202 and works in a background thread; `GET /api/run/<run>/cover` returns
  `{state: none | working | ready | error, ...}` (the Tools tab polls it). On a team server it reads only the signed-in
  person's own run. Output: `<run>/cover/index.html` (fonts and images beside it) and `<run>/cover/cover.png`; the record
  (time, kind, template, copy source) is in `<run>/studio.json` under `cover`.

## Home

Tabs for **All**, **UI designs**, **Docs** (with Deck, Report, Newsletter, Release note and Chart filters) and **Other**,
a design-system filter, search and the state filters (switch `studio_home_v2`). Runs are classified in
`studio.py classify()`. When a Home conversation creates a run, the whole conversation moves into that run's chat, Home's
History links to the run, and the page follows it to the canvas.

## Design systems (`/systems`)

Switch `studio_systems`. Every system Flynn designs from (libraries, themes, recorded ones) with counts and a tier bar,
its source and last update, and proposals waiting for review. A system's detail shows components, tokens, its guide and
recordings. Each system has its own Flynn conversation (`system:<id>`) with **Update from code**, **Check its health**
and **Review proposals**; **Add a system** starts one. Changes go through `scripts/proposals.py`; Flynn never marks
anything verified. Service: `systems.py`.

**Recorder** (switch `studio_recorder`): **Record a live product** opens Flynn's own Chrome (`adapters/recorder`): you
browse and Snap, or Flynn follows links; up to three pages; a live bar shows progress with Snap and Stop. Recordings stay
in `refs/recordings/` and show with thumbnails. Recorded HTML is never served by Studio, only images and data.

**Ownership and deployment.** Design systems are part of flynn's core: `systems/systems.json` lists the systems that
ship with every deployment, which of them carry machine-measured (recorded) rows, and the owner. Systems change only
on the owner's own copy of flynn; on a server (`FLYNN_REQUIRE_AUTH=1`) the systems page, its conversations, the recorder
and every approve or reject are read-only, and the server brings its systems in line with the release on every start
(`deploy/seed/systems_sync.py`: `kb.py sync-systems`, then a library per system). `kb.py sync-systems` refuses to run
off a server, because there the store, not the export, is the source of truth.

## App builder (`/apps`, `/app/<name>`)

Switch `studio_apps`. Describe an app and Flynn builds it, like Lovable: Vite + React + TypeScript in
`~/flynn-apps/<name>` (`FLYNN_APPS_DIR`), one git repo each, optionally styled with a design system's tokens
(`src/theme.css`). The workspace has the chat on the left and **Preview** (live, with Desktop/Tablet/Phone),
**Code** (file tree and viewer) and **Versions** (restore any) on the right. Every Flynn turn is committed as a version.
Studio runs `npm install` and the dev server (ports 5200-5299) and stops them when it stops. The app conversation is a
plain coding agent in the app's folder: it may write there, run npm install, build and type-check, never commit or push.
Service: `apps.py`.

## Skills and slash commands

Type `/` in any chat: flynn desk's commands (`/brainstorm`, `/skill`, `/clu`, `/quorra`, `/ds`, `/library`, `/chart`), Studio's
own (`/new`, `/history`, `/attach`, `/select`, `/usage`, `/stop`, `/settings`, `/systems`, `/record`, `/help`), and every
installed flynn skill as `/<skill-name>`. Flynn can propose a new skill (`propose_skill`, the desk's tool); the card has
**Install skill** and **Reject**, and an installed skill works from the next message.

## Settings (`/settings`)

Setup commands with Copy buttons, live health checks (SDK, `claude`, sign-in status only, flynn agent, runs folder,
engine, modules), the engine (Agent SDK, CLI, Off), effort defaults and caps, and a switch per Studio module
(`~/.flynn/features.json`, keys `studio_*`; `studio_chat` is on by default; off = Studio exactly as before).

```
python3 -m pip install --user "claude-agent-sdk>=0.1"
claude auth status || claude auth login
python3 "$FLYNN/adapters/studio/studio.py" serve
```

## Look

Studio uses flynn desk's approved glass theme (2026-10-04): tokens in `static/theme.css`, layout in `static/studio.css`,
the chat in `static/chat.css`, Settings in `static/settings.css`, the left rail in `static/rail.js`, the glass motion and chat dock in `static/glass.js`.

## On a team server (`STUDIO_TEAM=1`)

The same Studio can serve a team (`team.py`). Everyone signs in through the company's OpenID Connect portal (authorization
code with PKCE; the person comes from its userinfo endpoint and is matched by `sso_sub`, then `gitlab_id`, then `sub`),
with a session cookie that also works when the portal opens Studio in a frame. Each person has their own folder
(`users/<uid>/runs` and `users/<uid>/chats`): nobody sees anyone else's runs, chats or uploads. The server's data folder is
mirrored into Studio's own Postgres. The AI never runs on the server: each person's turns go to the flynn Studio agent
on their own computer (`adapters/agent/flynn_agent.py`, installed with one command from Settings), which runs them on
their own Claude account and sends Studio's tool calls back with its token. The App builder works the same way:
each app is built, installed and previewed on the person's own Mac by their agent (the preview loads from
127.0.0.1 in Studio's frame), and after each change the agent saves the app's source and history on the server, so it
comes back on another computer. App code never runs on the server. The recorder and quorra need the person's own
Mac and are off on a team server.

Packages come from the server: `pkgcache.py` keeps a cache of the npm registry at `/npm/` (the person's agent token is
required), the installer's Python packages and Node.js for Macs that have none, all in Studio's Postgres so a redeploy
keeps them. The agent's npm uses its own settings file (never the person's `~/.npmrc`), lockfiles keep public
addresses, npm checks every package's integrity, and a failed install retries from the public registry. The newer
copy on the Mac always wins: its own Node, Claude Code or packages are used when they are newer than flynn's. Design systems are read-only there: the admin (the owner in
`systems/systems.json`, after unlocking with the server's admin key at `/admin`) maintains them on their own computer and
they ship with flynn's core. Tests: `tests/test_studio_team.py`.

**Updates (deploys) on a team server.** A deploy replaces the server, and the new one takes a few minutes to restore
data and rebuild before Studio listens. So `deploy/studio/start.sh` starts a tiny maintenance server on :8080 first
(`maintenance.py`, standard library only): every page answers "Flynn is being updated" (503, `Retry-After: 30`,
`X-Flynn-Maintenance: 1`; the page, `static/maintenance.html`, polls `/__maintenance/status` and reloads itself when Studio
is back), and `/healthz` answers `{"ok": true, "maintenance": true}` so the platform's readiness check passes. Studio
stops it just before binding (`studio.py serve --takeover-pidfile`); if Studio fails to start, the maintenance server
returns. Before a deploy, `deploy/studio/announce.py <server url> --minutes 3` (key from `--key-file`, default
`~/.flynn/server/studio-admin-key`; `--clear` removes it) shows a banner on every open page ("Flynn will update in
about 3 minutes"): `POST /admin/maintenance` with `{message, minutes}` (admin session or an `X-Studio-Admin-Key`
header), `DELETE` to clear, `GET /api/maintenance` for pages (`team.js` polls every 30 s). The announcement is kept in
`$FLYNN_HOME/maintenance.json`, is cleared when a new server starts and expires 10 minutes after it is due. When Studio's own
calls fail for more than about 5 seconds and `/healthz` agrees, `team.js` covers the page with the same message,
polls `/healthz` every 5 s, then reloads and puts back the chat box text. Tests: `tests/test_studio_maintenance.py`.

## Any AI can design here (MCP), and Flynn can run on other models (Model API)

**MCP server** (`mcp_server.py`, tools in `design_tools.py`): Studio answers MCP over Streamable HTTP at `/mcp`. In
Settings, "Connect an AI" makes a token per app and shows the setup for Claude Code, Cursor, Claude Desktop (through
`mcp-remote`), Codex and any other client. Tools: list and read runs, create a run from a template and theme, write and
edit page files, add options, render (a screenshot the AI can see, plus layout checks), lint, comments, versions, pick,
export, read the design systems (read-only), and `flynn_ask` to hand a request to Flynn itself. A token works only inside
its owner's runs and opens nothing but `/mcp`.

**Model API** (`api_engine.py`, keys in `api_keys.py`): Settings can run Flynn on another provider's model instead of the
Claude Agent SDK, through aisuite: Anthropic, OpenAI, DeepSeek, Google Gemini, Mistral, xAI, Groq, OpenRouter, Qwen,
Ollama on this computer, or any OpenAI-compatible address. The turn loop is Studio's own (its streaming patterns follow
OpenWorker's turn engine): text and thinking stream as they arrive, tool calls are assembled from their pieces and
parsed once, a garbled call is explained to the model instead of run, Stop works even when a stream stalls, rate limits
are retried before the first word, a reply cut off by the length limit continues, and a conversation that is too long is
compacted. In this mode Flynn has the design tools and read-only access to its own guides, but no specialists and no
shell. Keys are kept in a private file on this computer, or encrypted per person on a team server (STUDIO_KEYS_SECRET),
and never go back to the browser. Prices for the cost line: `api_prices.json` or each person's own, in Settings.

## Safety

- The server listens on 127.0.0.1 only and serves files from inside the runs folder only; path traversal is refused, and the tests check it.
- Chat routes refuse other origins (writes and the WebSocket) and other hosts (reads), so another web page can't drive or read the chat.
- Nothing is uploaded anywhere. Figma and Canva exports happen only when flynn processes a request, and only into files the user named or created for the run.
