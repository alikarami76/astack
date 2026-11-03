#!/usr/bin/env bash
set -euo pipefail
FLYNN="$(cd "$(dirname "$0")" && pwd)"
A="$HOME/.claude/agents"; S="$HOME/.claude/skills"
mkdir -p "$A" "$S"

link() { # $1 source, $2 target
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "REFUSING: $2 exists and is not a symlink"; exit 1; fi
  ln -sfn "$1" "$2"; echo "linked $2"
}
for f in "$FLYNN"/agents/*.md; do link "$f" "$A/$(basename "$f")"; done
for d in "$FLYNN"/skills/*/; do n="$(basename "$d")"; link "${d%/}" "$S/$n"
  if [ -d "$S/.git" ]; then grep -qxF "/$n" "$S/.gitignore" 2>/dev/null || echo "/$n" >> "$S/.gitignore"; fi; done

ALIAS="alias flynn='claude --agent flynn'"
Z="$HOME/.zshrc"
if ! grep -q "^alias flynn=" "$Z" 2>/dev/null; then
  if [ -s "$Z" ] && [ -n "$(tail -c1 "$Z")" ]; then echo >> "$Z"; fi
  echo "$ALIAS" >> "$Z"; echo "added alias flynn"
fi

python3 "$FLYNN/kb/kb.py" init >/dev/null
if ls "$FLYNN"/kb/export/*.jsonl >/dev/null 2>&1; then python3 "$FLYNN/kb/kb.py" rebuild >/dev/null || echo "WARNING: kb rebuild failed; continuing"; fi
echo "KB ready: $(python3 "$FLYNN/kb/kb.py" stats | tr -d '\n ')"
echo "Done. Open a new terminal, then run: flynn   (or: claude --agent flynn). From any session: /flynn <brief>"
