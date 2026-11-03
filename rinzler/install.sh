#!/usr/bin/env bash
# Rinzler installer: links its agents and skills into ~/.claude, adds the alias, builds the knowledge store.
# agent-stack's own install.sh does the same for everyone; run this one to install Rinzler alone.
set -euo pipefail
RINZLER="$(cd "$(dirname "$0")" && pwd)"
A="$HOME/.claude/agents"; S="$HOME/.claude/skills"
mkdir -p "$A" "$S"

link() { # $1 source, $2 target
  if [ "$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")" -ef "$1" ]; then echo "already linked $2"; return; fi
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "REFUSING: $2 exists and is not a symlink"; exit 1; fi
  ln -sfn "$1" "$2"; echo "linked $2"
}
for f in "$RINZLER"/agents/*.md; do link "$f" "$A/$(basename "$f")"; done
for d in "$RINZLER"/skills/*/; do link "${d%/}" "$S/$(basename "$d")"; done

Z="$HOME/.zshrc"
grep -q "^alias rinzler=" "$Z" 2>/dev/null || { [ -s "$Z" ] && [ -n "$(tail -c1 "$Z")" ] && echo >> "$Z"; echo "alias rinzler='claude --agent rinzler'" >> "$Z"; echo "added alias rinzler"; }

python3 -c 'import yaml' 2>/dev/null || echo "WARNING: PyYAML missing: python3 -m pip install --user pyyaml"
python3 "$RINZLER/kb/kb.py" init >/dev/null
if ls "$RINZLER"/kb/export/*.jsonl >/dev/null 2>&1; then python3 "$RINZLER/kb/kb.py" rebuild >/dev/null; fi
echo "KB ready: $(python3 "$RINZLER/kb/kb.py" stats | tr -d '\n ')"
command -v node >/dev/null || echo "node not found: the node eval adapter and Playwright need Node.js"
command -v k6 >/dev/null || echo "k6 not found (optional, for API load tests): brew install k6"
echo "Optional: build Rinzler's admin-panel copy with: bash \"$RINZLER/scripts/local/app.sh\" prepare && bash \"$RINZLER/scripts/local/app.sh\" install"
echo "Done. Open a new terminal, then run: rinzler   (or: claude --agent rinzler)"
