"""Account flags behind the flynn desk first sign-in (module D4).

Pure functions, no HTTP: panel_server.py calls them for
    GET  /flynn/api/me                    -> me(email)
    POST /flynn/api/me/password-changed   -> password_changed(email)
and the seed job calls mark_must_change(emails) after it creates the accounts.

Storage is one small JSON file, $FLYNN_HOME/accounts.json (default ~/.flynn). Another module will move it to
Postgres later; only this file has to change then. Shape:
    {"accounts": {"<email, lower case>": {"must_change_password": true, "marked_at": "...", "changed_at": "..."}}}
An email that is not in the file does not have to change its password.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()


def _path():
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn"))) / "accounts.json"


def _norm(email):
    return str(email or "").strip().lower()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path):
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"accounts": {}}
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return {"accounts": {}}
    return data


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".accounts.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def me(email):
    """The answer to GET /flynn/api/me."""
    e = _norm(email)
    rec = _load(_path())["accounts"].get(e) or {}
    return {"email": e, "must_change_password": bool(rec.get("must_change_password"))}


def password_changed(email):
    """The person has set their own password: clear the flag. Unknown emails are ignored."""
    e = _norm(email)
    if not e:
        return None
    with _LOCK:
        path = _path()
        data = _load(path)
        rec = data["accounts"].get(e)
        if rec is None:
            return None
        rec["must_change_password"] = False
        rec["changed_at"] = _now()
        _save(path, data)
    return None


def mark_must_change(emails):
    """Called by the seed job: these accounts have a temporary password and must change it at first sign-in.
    Accepts one email or a list. Returns the normalized emails that were marked."""
    if isinstance(emails, str):
        emails = [emails]
    marked = []
    with _LOCK:
        path = _path()
        data = _load(path)
        for raw in emails or []:
            e = _norm(raw)
            if not e:
                continue
            rec = data["accounts"].setdefault(e, {})
            rec["must_change_password"] = True
            rec["marked_at"] = _now()
            rec.pop("changed_at", None)
            marked.append(e)
        _save(path, data)
    return marked
