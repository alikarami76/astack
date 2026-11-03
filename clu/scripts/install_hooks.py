#!/usr/bin/env python3
"""Merge clu's hooks into ~/.claude/settings.json (shows a diff, asks first).

Telemetry runs only on the events clu's scripts read: tool events (patterns.py failures, run_tokens.py)
and subagent start/stop (run_tokens.py maps helpers to transcripts). Older installs that hooked 9 events
are cleaned up. The role guard runs on PreToolUse for the tools it checks.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVENTS = ["PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop"]
OLD_EVENTS = ["SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "SessionEnd"]
TOOL_EVENTS = {"PostToolUse", "PostToolUseFailure"}
MARK = "clu/hooks/telemetry.py"
GUARD_MARK = "clu/hooks/guard.py"
GUARD_MATCHER = "Read|Grep|Glob|Bash|Write|Edit|NotebookEdit"


def _strip(groups: list, mark: str) -> list:
    return [g for g in groups if not any(mark in h.get("command", "") for h in g.get("hooks", []))]


def merge(settings: dict, command: str, guard_command: str | None = None) -> dict:
    out = copy.deepcopy(settings)
    hooks = out.setdefault("hooks", {})
    for e in OLD_EVENTS:
        if e in hooks:
            hooks[e] = _strip(hooks[e], MARK)
            if not hooks[e]:
                del hooks[e]
    for e in EVENTS:
        groups = _strip(hooks.get(e, []), MARK)
        entry = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        if e in TOOL_EVENTS:
            entry = {"matcher": "*", **entry}
        groups.append(entry)
        hooks[e] = groups
    if guard_command:
        groups = _strip(hooks.get("PreToolUse", []), GUARD_MARK)
        groups.append({"matcher": GUARD_MATCHER, "hooks": [{"type": "command", "command": guard_command, "timeout": 5}]})
        hooks["PreToolUse"] = groups
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--settings", default=str(Path.home() / ".claude/settings.json"))
    a = ap.parse_args(argv)
    p = Path(a.settings)
    cur = json.loads(p.read_text()) if p.exists() else {}
    new = merge(cur, f'python3 "{REPO / "hooks" / "telemetry.py"}"', f'python3 "{REPO / "hooks" / "guard.py"}"')
    before = json.dumps(cur, indent=2).splitlines()
    after = json.dumps(new, indent=2).splitlines()
    diff = list(difflib.unified_diff(before, after, "settings.json", "settings.json (clu)", lineterm=""))
    if not diff:
        print("hooks already installed")
        return 0
    print("\n".join(diff))
    if not a.yes and input("Apply? [y/N] ").strip().lower() != "y":
        print("skipped")
        return 0
    if p.exists():
        shutil.copy2(p, p.with_suffix(".json.bak"))
    p.write_text(json.dumps(new, indent=2) + "\n")
    print(f"updated {p} (backup {p.with_suffix('.json.bak')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
