#!/usr/bin/env python3
"""clu telemetry hook: append one JSON line per clu-family event. Never fails."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact as _redact  # noqa: E402


def _in_family(agent_type) -> bool:
    return isinstance(agent_type, str) and (agent_type == "clu" or agent_type.startswith("clu-"))


def handle(payload: dict, out_path: Path) -> bool:
    if not _in_family(payload.get("agent_type")):
        return False
    tin = payload.get("tool_input")
    tin_s = json.dumps(tin, ensure_ascii=False) if tin is not None else ""
    tout = payload.get("tool_response")
    tout_s = json.dumps(tout, ensure_ascii=False) if tout is not None else ""
    prompt = payload.get("prompt") or ""
    error = payload.get("error")
    rec = {
        "schema_version": 1,
        "ts": _dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "prompt_id": payload.get("prompt_id"),
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "duration_ms": payload.get("duration_ms"),
        "input_chars": len(tin_s),
        "output_chars": len(tout_s),
        "error": _redact(error)[:2000] if error else None,
        "input_hash": hashlib.sha1(tin_s.encode()).hexdigest()[:16] if tin_s else None,
        "input_preview": _redact(tin_s)[:300] if tin_s else None,
        "prompt_preview": _redact(prompt)[:2000] if prompt else None,
        "transcript_path": payload.get("transcript_path"),
        "cwd": payload.get("cwd"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        out = Path(os.environ.get("CLU_TELEMETRY_FILE", REPO / "telemetry" / "events.jsonl"))
        handle(payload, out)
    except Exception:  # noqa: BLE001 - a telemetry failure must never block Claude
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
