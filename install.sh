#!/usr/bin/env bash
# Install astack for Claude Code: link every agent and skill into ~/.claude and prepare each agent's knowledge
# store. Safe to run again. Run it with bash: bash install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
A="$HOME/.claude/agents"; S="$HOME/.claude/skills"
mkdir -p "$A" "$S"

# The agents refer to their files under ~/agent-stack. Link it to this clone when the clone lives elsewhere.
if [ "$ROOT" != "$HOME/agent-stack" ]; then
  if [ -e "$HOME/agent-stack" ] && [ ! -L "$HOME/agent-stack" ]; then
    echo "REFUSING: ~/agent-stack exists and is not a link to this clone"; exit 1
  fi
  ln -sfn "$ROOT" "$HOME/agent-stack"; echo "linked ~/agent-stack -> $ROOT"
fi

link() { # $1 source, $2 target
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "REFUSING: $2 exists and is not a symlink"; return 1; fi
  ln -sfn "$1" "$2"; echo "linked $2"
}

# Agents that bring their own installer: it links their agents and skills and builds their knowledge store.
for p in flynn clu quorra rinzler; do
  echo "== $p"
  bash "$ROOT/$p/install.sh" || echo "WARNING: $p/install.sh did not finish; see above"
done

# The rest: ram (usage analyst) and pdfsmith (PDF builder).
echo "== ram, pdfsmith"
for f in "$ROOT"/ram/agents/*.md; do link "$f" "$A/$(basename "$f")" || true; done
for d in "$ROOT"/ram/skills/*/ "$ROOT"/pdfsmith/skills/*/; do
  link "${d%/}" "$S/$(basename "$d")" || true
done

for alias_name in ram; do
  Z="$HOME/.zshrc"
  grep -q "^alias $alias_name=" "$Z" 2>/dev/null || { [ -s "$Z" ] && [ -n "$(tail -c1 "$Z")" ] && echo >> "$Z"
    echo "alias $alias_name='claude --agent $alias_name'" >> "$Z"; echo "added alias $alias_name"; }
done

echo
echo "Optional Python packages: pip install requests selenium pillow fonttools claude-agent-sdk pyyaml"
echo "pdfsmith has its own environment: cd pdfsmith && uv sync   (see pdfsmith/README.md)"
echo "Done. Open a new terminal, then try: claude --agent flynn | clu | quorra | rinzler | ram"
echo "Next for flynn desk: flynn/bin/flynn-desk-setup (one time), then flynn/bin/flynn-desk."
