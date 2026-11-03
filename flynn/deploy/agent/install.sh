#!/usr/bin/env bash
# flynn agent installer: runs flynn's AI on this computer, with your own Claude account, for the __AGENT_APP__ at
# __FLYNN_URL__.
#
#   curl -fsSL __FLYNN_URL__/flynn/agent/install.sh | bash                 install (connect later)
#   curl -fsSL __FLYNN_URL__/flynn/agent/install.sh | bash -s -- <token>   install and connect (from Connect flynn)
#
# What it does, each step only if needed: checks Python 3.10+, installs the Python packages flynn uses (from the flynn
# server; claude-agent-sdk includes Claude Code), uses your Google Chrome for renders (else downloads Chromium), downloads flynn's files
# into ~/__AGENT_HOME__, puts `__AGENT_CMD__` in ~/.local/bin, signs it in with your token, and keeps it running in the
# background whenever you are logged in to this computer (a launchd agent on macOS, a systemd user service on Linux).
# It never asks for a password. Uninstall: __AGENT_CMD__ uninstall, or delete ~/__AGENT_HOME__.
set -euo pipefail
URL="__FLYNN_URL__"
TOKEN="${1:-}"
HOME_DIR="$HOME/__AGENT_HOME__"
BIN="$HOME/.local/bin"
say() { printf '\033[1;36m%s\033[0m %s\n' "flynn" "$1"; }
die() { printf '\033[1;31m%s\033[0m %s\n' "flynn" "$1" >&2; exit 1; }

OS="$(uname -s)"
[ "$OS" = "Darwin" ] || [ "$OS" = "Linux" ] || die "flynn agent runs on macOS or Linux."

# 1. Python 3.10+
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do     # the newest Python first
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  if [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    say "installing Python 3.12 with Homebrew"; brew install python@3.12 >/dev/null; PY="$(brew --prefix)/bin/python3.12"
  else
    die "Python 3.10 or newer is needed. Install it (macOS: https://www.python.org/downloads/ or brew install python@3.12), then run this again."
  fi
fi
say "Python: $($PY --version)"

# 2. Python packages, in flynn's own virtual environment. They come from the flynn server first (inside the company
# network, fast) and from PyPI only for anything the server does not have.
mkdir -p "$HOME_DIR" "$BIN"
curl -fsS --max-time 20 "$URL/flynn/agent/connect" -o /dev/null || die "Can't reach $URL. Check the address and your network or VPN."
if [ ! -x "$HOME_DIR/venv/bin/python" ]; then
  say "creating flynn's Python environment"
  "$PY" -m venv "$HOME_DIR/venv"
fi
VPY="$HOME_DIR/venv/bin/python"
PKGS=("claude-agent-sdk>=0.1" "playwright>=1.45" pillow requests PyYAML fonttools python-pptx selenium truststore)
PIPQ=(--disable-pip-version-check --progress-bar on)
say "installing Python packages from __AGENT_APP__ (about 150 MB the first time)"
# A package already installed at a newer version that satisfies these is kept as it is (never downgraded).
PIPQ+=(--upgrade-strategy only-if-needed)
if ! "$VPY" -m pip install "${PIPQ[@]}" --no-index --find-links "$URL/flynn/agent/wheels/" "${PKGS[@]}"; then
  say "some packages are not on __AGENT_APP__; getting the rest from PyPI (slower)"
  "$VPY" -m pip install "${PIPQ[@]}" --find-links "$URL/flynn/agent/wheels/" "${PKGS[@]}" \
    || die "Python packages did not install. Check your network or VPN and run this again."
fi

# 3. Claude Code: the claude-agent-sdk package already contains it, so nothing more is downloaded. An existing
# Claude Code install is used as is.
export PATH="$HOME/.local/bin:$HOME/.claude/local:$PATH"
if ! command -v claude >/dev/null 2>&1; then
  CC="$("$VPY" -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))')"
  [ -x "$CC" ] || die "Claude Code was not found. Install it (https://docs.claude.com/en/docs/claude-code/setup) and run this again."
  ln -sf "$CC" "$BIN/claude"
fi
say "Claude Code: $(claude --version 2>/dev/null | head -1)"

# Chrome for flynn's page renders: the Google Chrome already on this computer, or Playwright's Chromium, downloaded
# in the background after connecting (flynn chats meanwhile; only PDF and picture renders wait for it).
CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         /usr/bin/google-chrome /usr/bin/chromium /usr/bin/chromium-browser; do
  [ -x "$c" ] && { CHROME="$c"; break; }
done
CHROME_LINE=""
[ -n "$CHROME" ] && { say "Chrome: $CHROME"; CHROME_LINE="export FLYNN_CHROME_BIN=$(printf %q "$CHROME")"; }

# Node.js, for flynn Studio's App builder only: the Mac's own Node when it is 18 or newer; otherwise a private copy
# from the server (checked against nodejs.org's checksum) in ~/__AGENT_HOME__/node, on the PATH of the agent only, so
# your Homebrew or nvm Node is never touched.
# The launcher decides at every start: flynn's private Node only when it is newer than the Mac's own (or there is none).
NODE_LINE='PN="$HOME/__AGENT_HOME__/node/bin"; if [ -x "$PN/node" ]; then SV="$(node -p "process.versions.node" 2>/dev/null)"; PV="$("$PN/node" -p "process.versions.node")"; if [ -z "$SV" ] || "$PN/node" -e "const v=s=>s.split(\".\").map(Number);const[a,b]=[v(process.argv[1]),v(process.argv[2])];const d=a[0]-b[0]||a[1]-b[1]||a[2]-b[2];process.exit(d>0?0:1)" "$PV" "$SV"; then export PATH="$PN:$PATH"; fi; fi'
if [ "__AGENT_APP__" = "flynn Studio" ]; then
  NV="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  SYSV="$(node -p 'process.versions.node' 2>/dev/null || true)"
  PRIV="$("$HOME_DIR/node/bin/node" -p 'process.versions.node' 2>/dev/null || true)"
  newer() { "$VPY" -c 'import sys; v=lambda s: tuple(int(x) for x in s.split(".")[:3]); sys.exit(0 if v(sys.argv[1]) > v(sys.argv[2]) else 1)' "$1" "$2"; }
  if [ -n "$PRIV" ] && [ -n "$SYSV" ] && ! newer "$PRIV" "$SYSV"; then
    rm -rf "$HOME_DIR/node"; PRIV=""; say "your own Node.js $SYSV is newer than flynn's copy; flynn uses yours"
  fi
  if [ -n "$PRIV" ]; then
    say "Node.js: v$PRIV (private to flynn)"
  elif [ "${NV:-0}" -ge 18 ] 2>/dev/null; then
    say "Node.js: $(node -v)"
  elif [ "$OS" = "Darwin" ] && M="$(curl -fsS --max-time 20 "$URL/flynn/agent/node/manifest.json" 2>/dev/null)"; then
    [ "$(uname -m)" = "arm64" ] && NA=darwin-arm64 || NA=darwin-x64
    read -r NF NS <<<"$(printf '%s' "$M" | "$VPY" -c "import sys,json;d=json.load(sys.stdin).get('$NA') or {};print(d.get('file','-'),d.get('sha256','-'))")"
    if [ "$NF" != "-" ]; then
      say "installing Node.js for the App builder (from the server, about 50 MB)"
      curl -fsSL "$URL/flynn/agent/node/$NF" -o "$HOME_DIR/node.tgz" \
        && [ "$(shasum -a 256 "$HOME_DIR/node.tgz" | cut -d' ' -f1)" = "$NS" ] \
        && rm -rf "$HOME_DIR/node" && mkdir -p "$HOME_DIR/node" && tar -xzf "$HOME_DIR/node.tgz" -C "$HOME_DIR/node" --strip-components=1 \
        && say "Node.js: $("$HOME_DIR/node/bin/node" -v) (private to flynn)" \
        || say "Node.js didn't install; the App builder needs it: brew install node"
      rm -f "$HOME_DIR/node.tgz"
    fi
  else
    say "Node.js 18 or newer is needed for the App builder only; install it when you want to build apps: brew install node"
  fi
fi

# 4. the agent itself (flynn's files come from the server once it is connected)
cat > "$BIN/__AGENT_CMD__" <<SH
#!/usr/bin/env bash
# flynn agent launcher (written by the installer)
export PATH="\$HOME/.local/bin:\$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:\$PATH"
export FLYNN_AGENT_HOME="\$HOME/__AGENT_HOME__"
$CHROME_LINE
$NODE_LINE
A="\$HOME/__AGENT_HOME__/flynn/adapters/agent/flynn_agent.py"
[ -f "\$A" ] || A="\$HOME/__AGENT_HOME__/flynn_agent.py"
if [ "\${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/\$(id -u)/__AGENT_LABEL__" 2>/dev/null || true
  rm -f "\$HOME/Library/LaunchAgents/__AGENT_LABEL__.plist"
  systemctl --user disable --now __AGENT_CMD__ 2>/dev/null || true
  rm -rf "\$HOME/__AGENT_HOME__" "\$HOME/.local/bin/__AGENT_CMD__"; echo "flynn agent removed."; exit 0
fi
exec "$VPY" "\$A" "\$@"
SH
chmod +x "$BIN/__AGENT_CMD__"
# a bootstrap copy of the agent, until the first update brings flynn's files
curl -fsSL "$URL/flynn/agent/flynn_agent.py" -o "$HOME_DIR/flynn_agent.py" 2>/dev/null || true

# 5. Claude sign-in
if ! claude auth status >/dev/null 2>&1 && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  say "sign in to Claude in the window that opens (or run: claude, then /login)"
  claude auth login </dev/tty || say "Claude is not signed in yet: run 'claude' once and sign in, then '__AGENT_CMD__ start'."
fi

if [ -z "$TOKEN" ]; then
  say "installed. Now sign in to __AGENT_APP__, open $URL/flynn/agent/connect, and run the command it gives you."
  exit 0
fi

# 6. connect, download flynn's files, keep it running
"$BIN/__AGENT_CMD__" login "$URL" "$TOKEN"
"$BIN/__AGENT_CMD__" update >/dev/null && say "flynn's files downloaded"
if [ "$OS" = "Darwin" ]; then
  PL="$HOME/Library/LaunchAgents/__AGENT_LABEL__.plist"
  mkdir -p "$(dirname "$PL")"
  cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>__AGENT_LABEL__</string>
  <key>ProgramArguments</key><array><string>$BIN/__AGENT_CMD__</string><string>start</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>20</integer>
  <key>StandardOutPath</key><string>$HOME_DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/launchd.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/__AGENT_LABEL__" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PL"
else
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/__AGENT_CMD__.service" <<UNIT
[Unit]
Description=flynn agent
[Service]
ExecStart=$BIN/__AGENT_CMD__ start
Restart=always
RestartSec=20
[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload && systemctl --user enable --now __AGENT_CMD__
fi
if [ -z "$CHROME" ]; then
  say "downloading Chromium for page renders in the background (log: $HOME_DIR/chromium.log)"
  nohup "$VPY" -m playwright install chromium >"$HOME_DIR/chromium.log" 2>&1 &
fi
say "connected. flynn now answers in __AGENT_APP__ on your own Claude account. Status: __AGENT_CMD__ status"
