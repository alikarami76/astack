#!/usr/bin/env bash
# Shared helpers for Rinzler's local build script. Sourced, not run.
set -euo pipefail

RINZLER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
CODE_DIR="${RINZLER_CODE_DIR:-$HOME/code}"      # your own clones; Rinzler only reads them
WORK="${RINZLER_WORK:-$HOME/rinzler-work}"      # Rinzler's own copies; the only place it builds
DRY="${RINZLER_DRY_RUN:-}"

say()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# run CMD...: prints the command; runs it unless RINZLER_DRY_RUN is set
run() { printf '    $ %s\n' "$*"; [ -n "$DRY" ] || "$@"; }

real() { python3 -c 'import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$1"; }

# guard_work DIR: refuse a work folder inside your own code clones
guard_work() {
  local w d; w="$(real "$1")"; d="$(real "$CODE_DIR")"
  case "$w/" in "$d"/*) die "$1 is inside $CODE_DIR. Rinzler never writes to your repos; set RINZLER_WORK elsewhere." ;; esac
}

# clone_or_update SRC DEST BRANCH: a private clone of one of your repos, updated from its real origin. SRC is only
# read (git clone reads it; nothing is checked out, fetched or changed there).
clone_or_update() {
  local src="$1" dest="$2" branch="$3" url
  guard_work "$dest"
  [ -d "$src/.git" ] || die "$src is not a git clone"
  url="$(git -C "$src" remote get-url origin)"
  if [ ! -d "$dest/.git" ]; then
    run mkdir -p "$(dirname "$dest")"
    run git clone --no-hardlinks --quiet "$src" "$dest"
    run git -C "$dest" remote set-url origin "$url"
  fi
  if run git -C "$dest" fetch --quiet origin "$branch"; then
    run git -C "$dest" checkout --quiet -B "rinzler/$branch" "origin/$branch"
    ok "$(basename "$dest") at $( [ -n "$DRY" ] && echo '<dry-run>' || git -C "$dest" log -1 --format='%h %cd' --date=short) (origin/$branch)"
  else
    warn "could not fetch origin/$branch (network or VPN off?); keeping the commit the clone already has"
  fi
}

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

http_code() { curl -s -o /dev/null -m "${2:-10}" -w '%{http_code}' "$1" || true; }
