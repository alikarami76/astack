#!/usr/bin/env bash
set -euo pipefail
QUORRA="$(cd "$(dirname "$0")" && pwd)"
A="$HOME/.claude/agents"; S="$HOME/.claude/skills"
mkdir -p "$A" "$S"

link() { # $1 source, $2 target
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "REFUSING: $2 exists and is not a symlink"; exit 1; fi
  ln -sfn "$1" "$2"; echo "linked $2"
}
for f in "$QUORRA"/agents/*.md; do link "$f" "$A/$(basename "$f")"; done
for d in "$QUORRA"/skills/*/; do n="$(basename "$d")"; link "${d%/}" "$S/$n"
  if [ -d "$S/.git" ]; then grep -qxF "/$n" "$S/.gitignore" 2>/dev/null || echo "/$n" >> "$S/.gitignore"; fi; done

ALIAS="alias quorra='claude --agent quorra'"
Z="$HOME/.zshrc"
OLD="alias quorra='claude --agent $(printf '\121')uorra'"  # the earlier capitalised form
if grep -qxF "$OLD" "$Z" 2>/dev/null; then
  sed -i.bak "s|^$OLD\$|$ALIAS|" "$Z" && rm -f "$Z.bak"; echo "updated alias quorra"
elif ! grep -q "^alias quorra=" "$Z" 2>/dev/null; then
  if [ -s "$Z" ] && [ -n "$(tail -c1 "$Z")" ]; then echo >> "$Z"; fi
  echo "$ALIAS" >> "$Z"; echo "added alias quorra"
fi

python3 "$QUORRA/kb/kb.py" init >/dev/null
if ls "$QUORRA"/kb/export/*.jsonl >/dev/null 2>&1; then python3 "$QUORRA/kb/kb.py" rebuild >/dev/null || echo "WARNING: kb rebuild failed; continuing"; fi
echo "KB ready: $(python3 "$QUORRA/kb/kb.py" stats | tr -d '\n ')"
CFG="$QUORRA/config/repos.json"; [ -f "$CFG" ] || { CFG="$QUORRA/config/repos.example.json"; echo "NOTE: config/repos.json not found; using the example. Copy config/*.example.json to config/*.json and edit them."; }
REPOS="$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))))' "$CFG")"
echo "build indexes: for r in $REPOS; do python3 \"$QUORRA/scripts/index_repo.py\" \$r --fetch; done"
echo "Done. Open a new terminal, then run: quorra   (or: claude --agent quorra)"
