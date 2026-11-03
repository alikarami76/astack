"""Who is calling the flynn desk chat server.

Two kinds of callers:
  - a person in the app: Penpot's session cookie (`auth-token`), checked by asking the Penpot backend for that
    session's profile (get-profile); the answer is cached for a minute per cookie;
  - a person's local flynn agent: `Authorization: Bearer fla_...`, an agent token (agent_hub.py).

On the team server FLYNN_REQUIRE_AUTH=1 and every /api call needs one of the two. On the user's Mac it is unset, nobody
signs in to the chat server, and the owner is FLYNN_OWNER_EMAIL (or nobody).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

ANON = "00000000-0000-0000-0000-000000000000"
TTL = 60.0          # a valid session is re-checked after a minute
TTL_BAD = 5.0       # a refused one after 5 seconds; a network error is never remembered
STALE_OK = 3600.0   # when the backend doesn't answer, a session that was valid this long ago still counts
_cache: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def required() -> bool:
    return os.environ.get("FLYNN_REQUIRE_AUTH") == "1"


def rpc_base() -> str:
    return os.environ.get("FLYNN_PENPOT_RPC", "http://127.0.0.1:9001/api/rpc/command/")


def _cookie(headers) -> str | None:
    raw = headers.get("Cookie") or ""
    if not raw:
        return None
    try:
        c = SimpleCookie()
        c.load(raw)
    except Exception:  # noqa: BLE001 - a malformed cookie header is just no session
        return None
    m = c.get("auth-token")
    return m.value if m and m.value else None


def profile_for_cookie(token: str, fetch=None) -> dict | None:
    """The Penpot profile behind a session cookie, or None. `fetch(token)` is for tests."""
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < (TTL if hit[1] else TTL_BAD):
            return hit[1]
    try:
        prof = (fetch or _fetch_profile)(token)
    except OSError:
        # The backend is busy (a build), restarting or unreachable: keep a session that was valid within the hour
        # rather than locking the person out for one slow answer; a refused session is never kept this way.
        if hit and hit[1] and now - hit[0] < STALE_OK:
            return hit[1]
        return None
    with _lock:
        _cache[key] = (now, prof)
        if len(_cache) > 2000:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:500]:
                _cache.pop(k, None)
    return prof


def _fetch_profile(token: str) -> dict | None:
    req = urllib.request.Request(rpc_base() + "get-profile", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "Cookie": f"auth-token={token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            p = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise OSError(f"backend error {e.code}") from e
        return None                                    # 4xx: the session is not valid
    except ValueError:
        return None
    if not isinstance(p, dict) or not p.get("email") or p.get("id") == ANON:
        return None
    return {"id": p.get("id"), "email": str(p["email"]).lower(), "name": p.get("fullname") or p.get("email")}


def identify(headers, fetch=None) -> dict | None:
    """{"email", "name", "via": "session" | "agent"} for the caller, or None."""
    auth = headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        import agent_hub
        who = agent_hub.who(auth[7:].strip())
        return {**who, "via": "agent"} if who else None
    tok = _cookie(headers)
    if tok:
        p = profile_for_cookie(tok, fetch)
        if p:
            return {**p, "via": "session"}
    return None


def local_owner() -> dict | None:
    e = os.environ.get("FLYNN_OWNER_EMAIL")
    return {"email": e.lower(), "name": e, "via": "local"} if e else None
