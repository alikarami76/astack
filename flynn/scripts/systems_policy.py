"""Who may change flynn's design systems, and which systems ship with flynn's core.

The manifest is systems/systems.json:
  owners          the people who own the design systems (FLYNN_SYSTEMS_OWNERS, comma-separated, overrides it)
  changes         "local-only": systems change only on an owner's own copy of flynn (the repo is the source of truth);
                  a server (FLYNN_REQUIRE_AUTH=1, the team server) keeps them read-only for everyone
  ship            the systems every deployment carries (kb/export -> the server's store on every start, plus a library)
  ship_generated  systems whose machine-measured rows (origin "generated") are exported and shipped too

  check_change(who)  raises SystemsLocked when a design-system change may not happen here
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[1]
MANIFEST = FLYNN / "systems" / "systems.json"


class SystemsLocked(PermissionError):
    pass


def manifest() -> dict:
    try:
        d = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def owners() -> list[str]:
    env = os.environ.get("FLYNN_SYSTEMS_OWNERS")
    src = env.split(",") if env else manifest().get("owners", [])
    return [o.strip().lower() for o in src if o and o.strip()]


def shipped() -> list[str]:
    return list(manifest().get("ship", []))


def ship_generated() -> set[str]:
    return set(manifest().get("ship_generated", []))


def on_server() -> bool:
    return os.environ.get("FLYNN_REQUIRE_AUTH") == "1" or os.environ.get("FLYNN_SERVER") == "1"


def can_change(who: str | None = None) -> tuple[bool, str]:
    """(allowed, reason). On a server nobody may change systems (they come from the repo). On an owner's own copy,
    a named person must be an owner; an unnamed caller is the person at this computer."""
    if on_server() and manifest().get("changes", "local-only") == "local-only":
        return False, ("Design systems are read-only here. They change only on the owner's own copy of flynn and "
                       "reach this server with the next deployment.")
    if who and owners() and who.strip().lower() not in owners():
        return False, "Only the design systems' owner can change them."
    return True, ""


def check_change(who: str | None = None) -> None:
    ok, why = can_change(who)
    if not ok:
        raise SystemsLocked(why)
