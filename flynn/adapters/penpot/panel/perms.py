"""The tool permissions every flynn chat turn runs with (flynn desk and Studio share them)."""
from __future__ import annotations

from pathlib import Path

FLYNN = Path(__file__).resolve().parents[3]
PROTECTED_CODE = str(Path.home() / "code")   # code repos flynn must never write to

BASE_ALLOW = [
    "Read", "Glob", "Grep", "Skill", "Agent", "Task", "TodoWrite", "WebSearch", "WebFetch",
    f"Bash(python3 {FLYNN}/*)", f"Bash(python3 \"{FLYNN}/*)",
    f"Write(/{FLYNN}/runs/**)", f"Edit(/{FLYNN}/runs/**)",
    # Figma import: read-only Figma tools, on the user's own Figma connection
    "mcp__plugin_figma_figma__whoami", "mcp__plugin_figma_figma__get_metadata",
    "mcp__plugin_figma_figma__get_design_context", "mcp__plugin_figma_figma__get_screenshot",
    "mcp__plugin_figma_figma__get_variable_defs",
]
DENY = [f"Write(/{PROTECTED_CODE}/**)", f"Edit(/{PROTECTED_CODE}/**)", f"NotebookEdit(/{PROTECTED_CODE}/**)", "Bash(git push:*)"]
