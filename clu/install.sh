#!/usr/bin/env bash
set -euo pipefail
CLU="$(cd "$(dirname "$0")" && pwd)"
A="$HOME/.claude/agents"; S="$HOME/.claude/skills"
mkdir -p "$A" "$S"

link() { # $1 source, $2 target
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "REFUSING: $2 exists and is not a symlink"; exit 1; fi
  ln -sfn "$1" "$2"; echo "linked $2"
}
for f in "$CLU"/agents/*.md; do link "$f" "$A/$(basename "$f")"; done
for d in "$CLU"/skills/*/; do n="$(basename "$d")"; link "${d%/}" "$S/$n"
  grep -qxF "/$n" "$S/.gitignore" 2>/dev/null || echo "/$n" >> "$S/.gitignore"; done

python3 "$CLU/scripts/install_hooks.py"

grep -q "alias clu=" "$HOME/.zshrc" 2>/dev/null || { echo "alias clu='claude --agent clu'" >> "$HOME/.zshrc"; echo "added alias clu"; }

python3 "$CLU/kb/kb.py" init >/dev/null
if [ "$(python3 "$CLU/kb/kb.py" stats | python3 -c 'import json,sys; print(json.load(sys.stdin)["fact"])')" = "0" ]; then
  if [ -d "$CLU/kb/export" ] && ls "$CLU"/kb/export/*.jsonl >/dev/null 2>&1; then python3 "$CLU/kb/kb.py" rebuild >/dev/null; fi
  echo "KB ready: $(python3 "$CLU/kb/kb.py" stats | tr -d '\n ')"
fi
if command -v uv >/dev/null; then
  [ -x "$CLU/.venv-causal/bin/python" ] || { echo "building the causal toolkit env (slow network: this can take a while)"; "$CLU/scripts/causal/setup.sh"; }
else
  echo "uv not found: skip the causal toolkit env (install uv, then run scripts/causal/setup.sh)"
fi
[ -f "$HOME/.claude/secrets/growthbook.env" ] || echo "GrowthBook: put GROWTHBOOK_API_URL and a read-only GROWTHBOOK_API_KEY in ~/.claude/secrets/growthbook.env"
echo "Done. Open a new terminal, then run: clu"
