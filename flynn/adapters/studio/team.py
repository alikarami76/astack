"""Studio on a team server (STUDIO_TEAM=1): who is signed in, and where each person's work lives.

Off (the default), Studio is the user's local app on 127.0.0.1 and nothing here runs. On, every request needs a person:
  - a browser session (cookie fs_session: HttpOnly, Secure, SameSite=None so it works inside Karvansara's frame),
    made by signing in through Karvansara (OpenID Connect, authorization code with PKCE; Karvansara issues no
    id_token, so the person comes from its userinfo endpoint);
  - or a flynn agent token (Authorization: Bearer fla_...), for the agent on the person's own computer.

Accounts are matched by sso_sub, then gitlab_id, then sub (never by username or email), and each id is kept as it
arrives. Each person gets a folder, users/<uid>/, with their own runs/ and chats/: nobody sees anyone else's work. The
server's /data folder is mirrored into Postgres (deploy/studio/start.sh, datasync.py), so these files live there.

Admin: a Karvansara sign-in never makes anyone an admin (whoever controlled Karvansara could otherwise act as one).
The admin is the design systems owner (systems/systems.json) who also unlocks the session with the admin key
(STUDIO_ADMIN_KEY, kept as a server secret). Admins see every person's usage and change the server's settings.
Design systems themselves change only on the owner's own computer and ship with flynn's core (systems_policy).

Settings (environment):
  STUDIO_TEAM=1                 turn this on
  STUDIO_PUBLIC_URL             the address people open (https://...)
  KARVANSARA_ISSUER             the OIDC issuer (…/api/oauth); endpoints are named, not discovered
  KARVANSARA_CLIENT_ID          the project's address in Karvansara's Bazaar
  KARVANSARA_CLIENT_SECRET      or KARVANSARA_CLIENT_SECRET_FILE: the client secret (never logged)
  KARVANSARA_SDK                optional: the SDK script added to Studio's pages (karvansara.global.js)
  STUDIO_FRAME_ANCESTORS        optional: who may frame Studio (Karvansara's origin)
  STUDIO_ALLOWED_DOMAINS        optional: email domains allowed in, comma separated (empty: anyone Karvansara signs in)
  STUDIO_ADMIN_KEY              optional: or STUDIO_ADMIN_KEY_FILE; the admin unlock key
  STUDIO_USERS_DIR              where users/<uid>/ live (default $FLYNN_HOME/users)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COOKIE = "fs_session"
SESSION_DAYS = 14
PENDING_TTL = 600
_lock = threading.Lock()
_pending: dict[str, dict] = {}          # OIDC state -> {verifier, next, at}
_signin_fail: dict[str, float] = {}


def on() -> bool:
    return os.environ.get("STUDIO_TEAM") == "1"


def flynn_home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


def _dir() -> Path:
    d = flynn_home() / "team"
    d.mkdir(parents=True, exist_ok=True)
    return d


def users_dir() -> Path:
    return Path(os.environ.get("STUDIO_USERS_DIR") or flynn_home() / "users")


def public_url() -> str:
    return (os.environ.get("STUDIO_PUBLIC_URL") or "").rstrip("/")


def public_origin() -> str:
    u = urllib.parse.urlsplit(public_url())
    return f"{u.scheme}://{u.netloc}" if u.scheme else ""


def _secret(name: str) -> str:
    v = os.environ.get(name, "")
    f = os.environ.get(name + "_FILE")
    if not v and f:
        try:
            v = Path(f).read_text().strip()
        except OSError:
            v = ""
    return v


def _read(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def log(msg: str) -> None:
    try:
        d = flynn_home() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "studio_team.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- people and their folders
def _identities() -> list[dict]:
    return _read(_dir() / "identities.json", [])


def match(claims: dict) -> dict:
    """The account for these userinfo claims: by sso_sub, then gitlab_id, then sub; a new one when none matches.
    Every id is saved as it arrives (sub can change when the person links their other account in Karvansara)."""
    ids = {k: str(claims[k]) for k in ("sso_sub", "gitlab_id", "sub") if claims.get(k) not in (None, "")}
    if not ids.get("sub"):
        raise ValueError("Karvansara sent no account id")
    with _lock:
        people = _identities()
        found = None
        for k in ("sso_sub", "gitlab_id", "sub"):
            if k in ids:
                found = next((p for p in people if p.get(k) == ids[k]), None)
                if found:
                    break
        if found is None:
            seed = ids.get("sso_sub") or ids.get("gitlab_id") or ids["sub"]
            found = {"uid": "u" + hashlib.sha256(seed.encode()).hexdigest()[:12], "created": int(time.time())}
            people.append(found)
        found.update(ids)
        for k in ("email", "name", "preferred_username", "picture"):
            if claims.get(k):
                found[k] = str(claims[k])[:200]
        found["seen"] = int(time.time())
        _write(_dir() / "identities.json", people)
        return dict(found)


def person(uid: str) -> dict | None:
    return next((dict(p) for p in _identities() if p.get("uid") == uid), None)


def people() -> list[dict]:
    return [dict(p) for p in _identities()]


def by_email(email: str) -> dict | None:
    e = (email or "").lower()
    return next((dict(p) for p in _identities() if (p.get("email") or "").lower() == e), None) if e else None


def user_root(uid: str) -> Path:
    if not re.fullmatch(r"u[0-9a-f]{12}", uid or ""):
        raise ValueError("bad user id")
    d = users_dir() / uid
    (d / "runs").mkdir(parents=True, exist_ok=True)
    (d / "chats").mkdir(parents=True, exist_ok=True)
    return d


def runs_of(me: dict | None) -> Path | None:
    return user_root(me["uid"]) / "runs" if me and me.get("uid") else None


def chats_of(me: dict | None) -> Path | None:
    return user_root(me["uid"]) / "chats" if me and me.get("uid") else None


def apps_for_email(email: str) -> Path | None:
    """Saved copies of a person's apps (their source and history; the apps themselves run on their own Mac)."""
    p = by_email(email)
    return user_root(p["uid"]) / "apps" if p else None


def runs_for_email(email: str) -> Path | None:
    """The agent hub keys people by email: their runs folder."""
    p = by_email(email)
    return user_root(p["uid"]) / "runs" if p else None


# ---------------------------------------------------------------- sessions
def _h(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _sessions() -> dict:
    return _read(_dir() / "sessions.json", {})


def new_session(p: dict) -> str:
    tok = "fss_" + secrets.token_urlsafe(32)
    with _lock:
        s = {k: v for k, v in _sessions().items() if v.get("exp", 0) > time.time()}
        s[_h(tok)] = {"uid": p["uid"], "exp": time.time() + SESSION_DAYS * 86400, "via": "karvansara", "admin": False}
        _write(_dir() / "sessions.json", s)
    return tok


def end_session(tok: str) -> None:
    with _lock:
        s = _sessions()
        if s.pop(_h(tok), None) is not None:
            _write(_dir() / "sessions.json", s)


def set_admin(tok: str, on_: bool) -> None:
    with _lock:
        s = _sessions()
        if _h(tok) in s:
            s[_h(tok)]["admin"] = bool(on_)
            _write(_dir() / "sessions.json", s)


def cookie_token(h) -> str:
    raw = h.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and v:
            return v
    return ""


def _owners() -> list[str]:
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import systems_policy
        return [o.lower() for o in systems_policy.owners()]
    except Exception:  # noqa: BLE001
        return []


def who(h) -> dict | None:
    """The person behind this request, or None. {uid, email, name, via: session|agent, admin, owner}."""
    auth = h.headers.get("Authorization") or ""
    if auth.startswith("Bearer fsm_"):                     # an AI app connected over MCP (mcp_server.py)
        import mcp_server
        m = mcp_server.who(auth[7:].strip())
        p = person(m["uid"]) if m and m.get("uid") else None
        return {**p, "via": "mcp", "app": m.get("app"), "admin": False, "owner": False} if p else None
    if auth.startswith("Bearer fla_"):
        import agent_hub
        a = agent_hub.who(auth[7:].strip())
        if not a:
            return None
        p = by_email(a.get("email", ""))
        if not p:
            return None
        return {**p, "via": "agent", "admin": False, "owner": False}
    tok = cookie_token(h)
    if not tok:
        return None
    s = _sessions().get(_h(tok))
    if not s or s.get("exp", 0) < time.time():
        return None
    p = person(s["uid"])
    if not p:
        return None
    owner = (p.get("email") or "").lower() in _owners()
    return {**p, "via": "session", "owner": owner, "admin": bool(s.get("admin")) and owner}


def check_admin_key(key: str) -> bool:
    want = _secret("STUDIO_ADMIN_KEY")
    return bool(want) and hmac.compare_digest(key.encode(), want.encode())


def cookie_header(tok: str, clear: bool = False) -> str:
    secure = public_url().startswith("https://")
    attrs = "Path=/; HttpOnly" + ("; Secure; SameSite=None" if secure else "; SameSite=Lax")
    if clear:
        return f"{COOKIE}=; {attrs}; Max-Age=0"
    return f"{COOKIE}={tok}; {attrs}; Max-Age={SESSION_DAYS * 86400}"


# ---------------------------------------------------------------- Karvansara sign-in (OIDC, PKCE)
def oidc() -> dict:
    iss = (os.environ.get("KARVANSARA_ISSUER") or "").rstrip("/")
    return {"issuer": iss, "authorize": iss + "/authorize", "token": iss + "/token", "userinfo": iss + "/userinfo",
            "client_id": os.environ.get("KARVANSARA_CLIENT_ID", ""), "secret": _secret("KARVANSARA_CLIENT_SECRET"),
            "callback": public_url() + "/auth/callback"}


def configured() -> tuple[bool, str]:
    c = oidc()
    missing = [n for n, v in (("STUDIO_PUBLIC_URL", public_url()), ("KARVANSARA_ISSUER", c["issuer"]),
                              ("KARVANSARA_CLIENT_ID", c["client_id"]), ("KARVANSARA_CLIENT_SECRET", c["secret"])) if not v]
    return (not missing, "missing " + ", ".join(missing) if missing else "ready")


def safe_next(nxt: str) -> str:
    nxt = nxt or "/"
    return nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else "/"


def login_url(nxt: str) -> str:
    c = oidc()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL]:
            _pending.pop(k, None)
        _pending[state] = {"verifier": verifier, "next": safe_next(nxt), "at": now}
    q = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": c["callback"], "scope": "openid profile email",
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return c["authorize"] + "?" + urllib.parse.urlencode(q)


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def finish(code: str, state: str) -> tuple[dict, str]:
    """The callback: trade the code for a token, read the person, match the account. Returns (person, next)."""
    with _lock:
        pend = _pending.pop(state or "", None)
    if not pend or time.time() - pend["at"] > PENDING_TTL:
        raise ValueError("this sign-in expired or came from somewhere else; start again")
    if not code:
        raise ValueError("Karvansara sent no code")
    c = oidc()
    try:
        tok = _post_form(c["token"], {"grant_type": "authorization_code", "code": code, "redirect_uri": c["callback"],
                                      "client_id": c["client_id"], "client_secret": c["secret"],
                                      "code_verifier": pend["verifier"]})
    except urllib.error.HTTPError as e:
        raise ValueError(f"Karvansara refused the sign-in (HTTP {e.code})") from None
    at = tok.get("access_token")
    if not at:
        raise ValueError("Karvansara sent no access token")
    claims = _get_json(c["userinfo"], at)
    email = (claims.get("email") or "").lower()
    if not email:
        raise ValueError("your Karvansara account has no email; connect your company account in Karvansara first")
    allowed = [d.strip().lower().lstrip("@") for d in (os.environ.get("STUDIO_ALLOWED_DOMAINS") or "").split(",") if d.strip()]
    if allowed and email.rsplit("@", 1)[-1] not in allowed:
        raise ValueError("this account's email domain isn't allowed in Studio")
    p = match(claims)
    log(f"signin ok {p['uid']}")
    return p, pend["next"]


def frame_header() -> str:
    anc = os.environ.get("STUDIO_FRAME_ANCESTORS", "").strip()
    return f"frame-ancestors 'self' {anc}".strip()


def sdk_tag() -> str:
    src = os.environ.get("KARVANSARA_SDK", "").strip()
    tags = '<script src="/static/team.js" defer></script>'
    if src.startswith("https://"):
        tags = f'<script src="{src}"></script>' + tags
    return tags


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flynn Studio</title><link rel="icon" type="image/png" href="/static/flynn-logo-64.png"><link rel="stylesheet" href="/static/theme.css">
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg,#0b0d10);color:var(--text,#e8eaee);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:440px;padding:32px 16px;text-align:center}}
h1{{font-size:22px;margin:16px 0 8px}}p{{color:var(--muted,#9aa1ab);margin:0 0 20px}}a.b,button{{display:inline-block;border:0;border-radius:10px;
padding:10px 18px;background:#fff;color:#0b0d10;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;border:1px solid #333;background:#111;color:inherit;font:inherit;margin-bottom:12px}}</style>
</head><body><main><img src="/static/flynn-logo.png" width="44" height="44" alt=""><h1>{title}</h1><p>{text}</p>{action}</main></body></html>"""


def page(title: str, text: str, action: str = "") -> bytes:
    import html
    return PAGE.format(title=html.escape(title), text=html.escape(text), action=action).encode()


# ---------------------------------------------------------------- maintenance announcement
# An admin announces a deploy ("Flynn will update in about 3 minutes"); every open Studio page shows a slim banner.
# Kept in memory and in $FLYNN_HOME/maintenance.json. A new server clears it on start (the update it announced has
# happened), and it expires on its own 10 minutes after the announced time.
MAINT_GRACE = 600
_maint: dict | None = None


def _maint_file() -> Path:
    return flynn_home() / "maintenance.json"


def maintenance_set(message: str, minutes: int) -> dict:
    global _maint
    now = time.time()
    _maint = {"message": message, "minutes": minutes, "at": now, "ends_at": now + minutes * 60}
    try:
        _maint_file().parent.mkdir(parents=True, exist_ok=True)
        _maint_file().write_text(json.dumps(_maint))
    except OSError:
        pass
    return dict(_maint)


def maintenance_clear() -> None:
    global _maint
    _maint = None
    try:
        _maint_file().unlink()
    except OSError:
        pass


def maintenance_get() -> dict:
    """The live announcement plus the server's clock (the page counts down from it), or {} when there is none."""
    global _maint
    m = _maint
    if m is None:
        try:
            m = json.loads(_maint_file().read_text())
        except (OSError, ValueError):
            return {}
    if not isinstance(m, dict) or time.time() > float(m.get("ends_at") or 0) + MAINT_GRACE:
        maintenance_clear()
        return {}
    _maint = m
    return {**m, "now": time.time()}
