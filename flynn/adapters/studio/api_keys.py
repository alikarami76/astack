"""Model API keys and settings for Studio's API engine (api_engine.py).

On this computer: $FLYNN_HOME/studio-keys.json (mode 600). On a team server: each person's keys in their own folder
(users/<uid>/keys.json), encrypted with the server's STUDIO_KEYS_SECRET (Fernet) before they are written, so the copy
mirrored into Postgres is not readable without it. Keys never go back to the browser: it sees the last four characters.
Each person's API choice (provider, model, address) is in users/<uid>/api.json; on this computer in studio.json.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import team


def _home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


def _folder(me: dict | None) -> Path:
    if team.on():
        if not me or not me.get("uid"):
            raise PermissionError("sign in first")
        return team.user_root(me["uid"])
    return _home()


def _fernet():
    secret = team._secret("STUDIO_KEYS_SECRET")
    if not secret:
        raise RuntimeError("the server has no STUDIO_KEYS_SECRET, so it can't keep API keys safely")
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _file(me) -> Path:
    return _folder(me) / ("keys.json" if team.on() else "studio-keys.json")


def _load(me) -> dict:
    p = _file(me)
    try:
        raw = p.read_bytes()
    except OSError:
        return {}
    try:
        if team.on():
            raw = _fernet().decrypt(raw)
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 - an unreadable file is treated as empty, never shown
        return {}


def _save(me, d: dict) -> None:
    p = _file(me)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(d).encode()
    if team.on():
        raw = _fernet().encrypt(raw)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def get(me, provider: str) -> str:
    return str(_load(me).get(provider) or "")


def set_key(me, provider: str, key: str) -> None:
    d = _load(me)
    if key:
        d[provider] = key.strip()
    else:
        d.pop(provider, None)
    _save(me, d)


def masked(me) -> dict:
    """{provider: "…abcd"} for the keys this person has saved."""
    return {k: "…" + v[-4:] for k, v in _load(me).items() if v}


# ---------------------------------------------------------------- the person's API choice
def _choice_file(me) -> Path:
    return _folder(me) / ("api.json" if team.on() else "studio-api.json")


def choice(me) -> dict:
    try:
        d = json.loads(_choice_file(me).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_choice(me, **kw) -> dict:
    d = {**choice(me), **{k: v for k, v in kw.items() if k in ("engine", "provider", "model", "base_url", "prices") and v is not None}}
    p = _choice_file(me)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1))
    return d
