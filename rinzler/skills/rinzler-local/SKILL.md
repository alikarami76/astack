---
name: rinzler-local
description: Rinzler's local build protocol - build and run your web app in Rinzler's own work folder (~/rinzler-work), never in your own code clones, updated from origin, health-checked, pointed at a demo or stage backend only. Load when a test needs a running app, or to update, start, stop or check Rinzler's build.
---

# Local builds

`$RINZLER` = `$HOME/agent-stack/rinzler` (write the literal path, quoted). The script refuses a work folder inside your code clones (`~/code`, or `RINZLER_CODE_DIR`); `RINZLER_DRY_RUN=1` prints the commands without running them.

## The app
`bash "$RINZLER/scripts/local/app.sh" <command>`. It is configured with environment variables (listed at the top of the script): `RINZLER_APP_SRC` (your clone of the app, read only), `RINZLER_APP_NAME`, `RINZLER_APP_BRANCH`, `RINZLER_APP_INSTALL`, `RINZLER_APP_START`, `RINZLER_APP_PORT` (default 3333) and `RINZLER_APP_ALLOWED_HOST`.
- `prepare`: clones your app (read only) into `~/rinzler-work/<name>`, points origin at the real remote and checks out `origin/<branch>` on branch `rinzler/<branch>`. Re-run to update.
- `install`: runs the install command (default `npm ci`). If your packages need a private registry login, that login is yours; Rinzler never reads it.
- `start`: when `RINZLER_APP_ALLOWED_HOST` is set, refuses unless the app's config points at that host (so the app only talks to your demo or stage backend), refuses when the port is busy, starts the dev server in the background, then runs `health`.
- `health`: 200 on http://localhost:3333, the app root present, the API host still the allowed one.
- `e2e [playwright args]`: copies `$RINZLER/playwright/` (config, evidence fixture) and `RINZLER_SUITES` into the work copy's `tests/rinzler/` and runs Playwright there. Auth is stubbed by the app's own fixtures; when a real session is needed, the owner logs in by hand.
- `stop`, `status`.

## Backends
A heavy backend stack (many containers, private registries, your git login) is not started by a script here. If a test needs one, ask the owner first, show what will start and how much it needs, and let the owner run any login commands himself. A local stack must have its own database with no production data; never point it at test, demo or production databases.

## Rules
Never edit, check out, pull or reset anything in your own code clones. Never print `.env*` or `.npmrc` values. Port 3333 may be taken by another agent's dev server (flynn uses its own copy); do not stop processes Rinzler did not start.
