#!/usr/bin/env bash
# Rinzler's own copy of a web app, built and run in ~/rinzler-work/<app> (never in your own clones).
#
#   app.sh prepare    clone RINZLER_APP_SRC (read only) into the work folder and update it from origin
#   app.sh install    run RINZLER_APP_INSTALL (default: npm ci)
#   app.sh start      run RINZLER_APP_START in the background on RINZLER_APP_PORT, then check health
#   app.sh health     is the server up and serving the app?
#   app.sh e2e [args] copy Rinzler's Playwright suites into the work copy and run them
#   app.sh stop | status
#
# Settings (environment variables):
#   RINZLER_APP_SRC      path of your clone of the app           (required for prepare)
#   RINZLER_APP_NAME     work folder name                        (default: web-app)
#   RINZLER_APP_BRANCH   branch to build from                    (default: main)
#   RINZLER_APP_INSTALL  install command                        (default: npm ci)
#   RINZLER_APP_START    start command                           (default: npm start)
#   RINZLER_APP_PORT     dev server port                         (default: 3333)
#   RINZLER_APP_ALLOWED_HOST  when set, start refuses unless the app's config mentions this API host (so the app
#                             can only talk to your demo or stage backend, never production)
#   RINZLER_APP_CONFIG   file to look for that host in           (default: .env.development)
# Set RINZLER_DRY_RUN=1 to print the commands without running them.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

NAME="${RINZLER_APP_NAME:-web-app}"
SRC="${RINZLER_APP_SRC:-$CODE_DIR/$NAME}"
APP="$WORK/$NAME"
BRANCH="${RINZLER_APP_BRANCH:-main}"
PORT="${RINZLER_APP_PORT:-3333}"
LOGS="$WORK/logs"
PIDF="$WORK/$NAME.pid"
ALLOWED="${RINZLER_APP_ALLOWED_HOST:-}"
CONFIG="${RINZLER_APP_CONFIG:-.env.development}"

cmd_prepare() { say "Preparing $APP"; clone_or_update "$SRC" "$APP" "$BRANCH"; }

cmd_install() {
  guard_work "$APP"; [ -d "$APP" ] || die "run prepare first"
  say "Installing packages (this can take several minutes)"
  mkdir -p "$LOGS"
  (cd "$APP" && run bash -c "${RINZLER_APP_INSTALL:-npm ci}") >"$LOGS/install.log" 2>&1 \
    || die "install failed; see $LOGS/install.log"
  ok "installed"
}

check_host() {
  [ -z "$ALLOWED" ] && return 0
  grep -q -- "$ALLOWED" "$APP/$CONFIG" 2>/dev/null || die "$CONFIG does not point at $ALLOWED. Rinzler only runs against the host you allowed."
  ok "API host: $ALLOWED"
}

cmd_start() {
  guard_work "$APP"; [ -d "$APP" ] || die "run prepare first"
  [ -n "$DRY" ] || check_host
  port_busy "$PORT" && die "port $PORT is already in use"
  mkdir -p "$LOGS"
  say "Starting the app on http://localhost:$PORT"
  if [ -z "$DRY" ]; then
    (cd "$APP" && PORT="$PORT" nohup bash -c "${RINZLER_APP_START:-npm start}" >"$LOGS/dev.log" 2>&1 & echo $! >"$PIDF")
    for _ in $(seq 1 90); do [ "$(http_code "http://localhost:$PORT/" 3)" = 200 ] && break; sleep 2; done
  else
    run "(cd $APP && ${RINZLER_APP_START:-npm start})"
  fi
  cmd_health
}

cmd_health() {
  say "Health check"
  local code; code="$(http_code "http://localhost:$PORT/")"
  [ -n "$DRY" ] && { ok "dry run: would check http://localhost:$PORT/ for 200 and the app root"; return 0; }
  [ "$code" = 200 ] || die "http://localhost:$PORT/ answered '$code' (see $LOGS/dev.log)"
  curl -s -m 10 "http://localhost:$PORT/" | grep -q -E 'id="root"|id="app"' || die "the page does not contain the app root"
  ok "app served on http://localhost:$PORT (200)"
  check_host
}

cmd_e2e() {
  guard_work "$APP"
  local dest="$APP/tests/rinzler"
  run mkdir -p "$dest"
  run cp -R "$RINZLER/playwright/." "$dest/"
  [ -n "${RINZLER_SUITES:-}" ] && run cp -R "$RINZLER_SUITES/." "$dest/scenarios/"
  say "Running Rinzler's Playwright suites (auth is stubbed; no login, no credentials)"
  (cd "$APP" && run npx playwright test --config tests/rinzler/rinzler.config.ts "$@")
}

cmd_stop() {
  if [ -f "$PIDF" ]; then run pkill -P "$(cat "$PIDF")" 2>/dev/null || true; run kill "$(cat "$PIDF")" 2>/dev/null || true; rm -f "$PIDF"; ok "stopped"
  else warn "no pid file; nothing started by Rinzler"; fi
}

cmd_status() {
  [ -d "$APP/.git" ] && ok "clone: $(git -C "$APP" log -1 --format='%h %cd' --date=short) on $(git -C "$APP" rev-parse --abbrev-ref HEAD)" || warn "no clone yet"
  [ -d "$APP/node_modules" ] && ok "packages installed" || warn "packages not installed"
  port_busy "$PORT" && ok "something listens on $PORT" || warn "nothing on $PORT"
}

case "${1:-}" in
  prepare|install|start|health|e2e|stop|status) c="$1"; shift; "cmd_$c" "$@" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
