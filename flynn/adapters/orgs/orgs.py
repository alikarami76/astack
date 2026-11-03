"""Organizations in flynn desk: flynn's own feature, not Penpot's paid one (Enterprise "Nitrate").

An org is a name, a logo, its members (each an owner, admin or member) and the teams that belong to it.
Approved by the user on 2026-10-06, with org admins getting team access automatically.

What membership means on the canvas:
  - org owners and admins become admins of every team in the org (never lowered if they are already the owner);
  - members belong to the org and see it in the switcher; they join teams the normal way, team by team;
  - the builder account joins every org team as an editor, so the org's libraries can be kept in sync there.
flynn records every access it grants (`granted`), and takes back only those when a member, a role or a team
changes: someone added by hand, or with a stronger role, keeps it.

Shared resources: the org's fonts (fonts.py) and design system libraries reach every org team. A canvas file can
only use libraries from its own team, so each library gets a synced copy in every org team (library.py).

  orgs.py list
  orgs.py create "<name>" [--owner EMAIL]
  orgs.py team ORG add|remove TEAM_ID
  orgs.py member ORG add|remove|role EMAIL [--role owner|admin|member]
  orgs.py rename ORG "<name>"
  orgs.py sync [ORG] [--resources]      bring team access in line (and fonts and library copies with --resources)

State: fstore (~/.flynn/orgs.json, or Postgres with FLYNN_DB_URL). Log: ~/.flynn/logs/orgs.log. Every canvas call is made as the owner account
(owner@flynn.local), which must be an admin of the org's teams.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "library"))
sys.path.insert(0, str(FLYNN / "scripts"))
import fstore  # noqa: E402

STATE = Path.home() / ".flynn" / "orgs.json"
LOG = Path.home() / ".flynn" / "logs" / "orgs.log"
ROLES = ("owner", "admin", "member")
RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
BUILDER = "builder@flynn.local"
_lock = threading.RLock()


class OrgError(Exception):
    pass


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")


# ---------- the store ----------

# One document (fstore ns "orgs", key "orgs"); on files it is ~/.flynn/orgs.json, as before.
fstore.files("orgs", path=lambda ns, key: STATE, indent=1)


def load(path: Path | None = None) -> dict:
    d = fstore.get("orgs", "orgs", None, file=path)
    d = d if isinstance(d, dict) else {}
    d.setdefault("orgs", [])
    return d


def save(d: dict, path: Path | None = None) -> None:
    fstore.put("orgs", "orgs", d, file=path)


def find(d: dict, org: str) -> dict:
    o = next((x for x in d["orgs"] if x["id"] == org or x["name"].lower() == str(org).lower()), None)
    if not o:
        raise OrgError(f"no org called {org}")
    return o


def org_of_team(d: dict, team: str) -> dict | None:
    return next((o for o in d["orgs"] if team in o["teams"]), None)


def role_of(o: dict, email: str) -> str | None:
    m = next((m for m in o["members"] if m["email"].lower() == (email or "").lower()), None)
    return m["role"] if m else None


def can_manage(o: dict, email: str | None) -> bool:
    """Owners and admins change an org. `None` is the terminal (the user at the command line)."""
    return email is None or role_of(o, email) in ("owner", "admin")


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    if not name:
        raise OrgError("an org needs a name")
    return name


# ---------- changes (pure: they edit the dict; sync() applies them to the canvas) ----------

def create(d: dict, name: str, owner_email: str, owner_name: str = "") -> dict:
    name = _clean_name(name)
    if any(o["name"].lower() == name.lower() for o in d["orgs"]):
        raise OrgError(f"there is already an org called {name}")
    o = {"id": "org-" + uuid.uuid4().hex[:10], "name": name, "logo": "", "created": time.strftime("%Y-%m-%d %H:%M"),
         "members": [{"email": owner_email, "name": owner_name or owner_email.split("@")[0], "role": "owner"}],
         "teams": [], "granted": {}}
    d["orgs"].append(o)
    return o


def rename(o: dict, name: str) -> None:
    o["name"] = _clean_name(name)


def set_logo(o: dict, data_url: str) -> None:
    if data_url and not re.fullmatch(r"data:image/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+", data_url):
        raise OrgError("the logo must be a PNG, JPEG, WebP or SVG image")
    if len(data_url) > 400_000:
        raise OrgError("the logo is too large (keep it under 300 KB)")
    o["logo"] = data_url


def add_team(d: dict, o: dict, team: str) -> None:
    other = org_of_team(d, team)
    if other and other is not o:
        raise OrgError(f"that team already belongs to {other['name']}; take it out there first")
    if team not in o["teams"]:
        o["teams"].append(team)


def remove_team(o: dict, team: str) -> None:
    o["teams"] = [t for t in o["teams"] if t != team]


def add_member(o: dict, email: str, role: str = "member", name: str = "") -> None:
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
        raise OrgError("that is not an email address")
    if role not in ROLES:
        raise OrgError("the role is owner, admin or member")
    if role_of(o, email):
        raise OrgError(f"{email} is already in {o['name']}")
    o["members"].append({"email": email, "name": name or email.split("@")[0], "role": role})


def set_role(o: dict, email: str, role: str) -> None:
    if role not in ROLES:
        raise OrgError("the role is owner, admin or member")
    m = next((m for m in o["members"] if m["email"].lower() == email.lower()), None)
    if not m:
        raise OrgError(f"{email} is not in {o['name']}")
    if m["role"] == "owner" and role != "owner" and sum(x["role"] == "owner" for x in o["members"]) == 1:
        raise OrgError("an org needs at least one owner; make someone else owner first")
    m["role"] = role


def remove_member(o: dict, email: str) -> None:
    if role_of(o, email) == "owner" and sum(x["role"] == "owner" for x in o["members"]) == 1:
        raise OrgError("an org needs at least one owner; make someone else owner first")
    o["members"] = [m for m in o["members"] if m["email"].lower() != email.lower()]


def delete(d: dict, o: dict) -> None:
    """Removing an org takes back the access it granted (sync with an empty org first), then forgets it."""
    d["orgs"] = [x for x in d["orgs"] if x is not o]


# ---------- applying access to the canvas ----------

def wanted(o: dict) -> dict[str, dict[str, str]]:
    """{team: {email: canvas role}} that this org grants."""
    grant = {m["email"].lower(): "admin" for m in o["members"] if m["role"] in ("owner", "admin")}
    if o["teams"]:
        grant[BUILDER] = "editor"
    return {t: dict(grant) for t in o["teams"]}


def _canvas_role(m: dict) -> str:
    if m.get("isOwner"):
        return "owner"
    if m.get("isAdmin"):
        return "admin"
    return "editor" if m.get("canEdit") else "viewer"


def sync(o: dict, rpc, me: str | None = None) -> dict:
    """Bring the canvas in line with the org: grant what is wanted, take back what flynn granted and is no longer
    wanted. Returns what changed and any problems (a team the owner cannot manage, an email with no account)."""
    want = wanted(o)
    granted = o.setdefault("granted", {})
    report = {"granted": [], "revoked": [], "invited": [], "errors": []}
    for team in sorted(set(want) | set(granted)):
        try:
            members = {m["email"].lower(): m for m in rpc("get-team-members", {"teamId": team}) or []}
        except Exception as e:  # noqa: BLE001 - deleted team, or the owner is not in it
            report["errors"].append(f"team {team[:8]}: {e}"[:200])
            continue
        g = granted.setdefault(team, {})
        for email, role in (want.get(team) or {}).items():
            m = members.get(email)
            try:
                if m is None:
                    rpc("create-team-invitations", {"teamId": team, "emails": [email], "role": role})
                    joined = any(x["email"].lower() == email for x in rpc("get-team-members", {"teamId": team}) or [])
                    if joined:
                        g[email] = {"role": role, "before": None}
                        report["granted"].append(f"{email} → {role} in {team[:8]}")
                    else:
                        report["invited"].append(email)            # no account yet: the invitation waits
                elif RANK[_canvas_role(m)] < RANK[role]:
                    rpc("update-team-member-role", {"teamId": team, "memberId": m["id"], "role": role})
                    if email not in g:
                        g[email] = {"role": role, "before": _canvas_role(m)}
                    report["granted"].append(f"{email} → {role} in {team[:8]}")
            except Exception as e:  # noqa: BLE001
                report["errors"].append(f"{email} in team {team[:8]}: {e}"[:200])
        for email in list(g):
            if email in (want.get(team) or {}):
                continue
            m = members.get(email)
            entry = g.pop(email)
            if not m or m.get("isOwner") or (me and email == me.lower()):
                continue                                  # gone already, the team owner, or the person acting
            try:
                if entry.get("before"):
                    rpc("update-team-member-role", {"teamId": team, "memberId": m["id"], "role": entry["before"]})
                else:
                    rpc("delete-team-member", {"teamId": team, "memberId": m["id"]})
                report["revoked"].append(f"{email} in {team[:8]}")
            except Exception as e:  # noqa: BLE001
                g[email] = entry
                report["errors"].append(f"taking back {email} in team {team[:8]}: {e}"[:200])
        if not g:
            granted.pop(team, None)
    if any(report[k] for k in report):
        log(f"sync {o['name']}: {json.dumps(report, ensure_ascii=False)[:600]}")
    return report


def share_resources(o: dict, log_fn=log) -> dict:
    """Fonts on every team, and a synced copy of every library in every org team (slow: runs the builder)."""
    out = {"fonts": None, "libraries": {}}
    try:
        import fonts
        out["fonts"] = {k: v["uploaded"] for k, v in fonts.sync(log=lambda m: None).items() if v["uploaded"]}
    except Exception as e:  # noqa: BLE001
        out["fonts"] = f"error: {e}"[:200]
    try:
        import library
        for lib in library.libraries():
            if not lib.get("file"):
                continue
            for team in o["teams"]:
                if team == lib.get("team"):
                    continue
                try:
                    library.sync(lib["system"], team=team)
                    out["libraries"].setdefault(lib["system"], []).append(team[:8])
                except Exception as e:  # noqa: BLE001
                    out["libraries"].setdefault(lib["system"], []).append(f"{team[:8]} error: {e}"[:200])
    except Exception as e:  # noqa: BLE001
        out["libraries"] = f"error: {e}"[:200]
    log_fn(f"resources {o['name']}: {json.dumps(out, ensure_ascii=False)[:500]}")
    return out


# ---------- views ----------

def view(d: dict, rpc, me: str) -> dict:
    """What the dashboard shows the person signed in: their orgs (with team names), and the teams outside any org."""
    teams = {t["id"]: t for t in rpc("get-teams") or []}
    mine = []
    for o in d["orgs"]:
        role = role_of(o, me)
        if not role:
            continue
        mine.append({"id": o["id"], "name": o["name"], "logo": o.get("logo", ""), "role": role,
                     "members": o["members"],
                     "teams": [{"id": t, "name": (teams.get(t) or {}).get("name", "(no access)"), "access": t in teams}
                               for t in o["teams"]]})
    in_orgs = {t for o in d["orgs"] for t in o["teams"]}
    loose = [{"id": t["id"], "name": t["name"], "personal": bool(t.get("isDefault"))}
             for t in teams.values() if t["id"] not in in_orgs]
    return {"me": me, "orgs": mine, "teams": loose}


def owner_rpc():
    import builder
    return builder.owner()


def act(action: str, body: dict, me: str, rpc=None, path: Path | None = None) -> dict:
    """One change from the dashboard, checked against the actor's role, saved, then applied to the canvas."""
    rpc = rpc or owner_rpc()
    with _lock:
        d = load(path)
        if action == "create":
            prof = {}
            try:
                prof = rpc("get-profile") or {}
            except Exception:  # noqa: BLE001
                pass
            o = create(d, body.get("name", ""), me, prof.get("fullname", "") if prof.get("email", "").lower() == me.lower() else "")
            for t in body.get("teams") or []:
                add_team(d, o, t)
        else:
            o = find(d, str(body.get("org") or ""))
            if not can_manage(o, me):
                raise OrgError("only the org's owners and admins can change it")
            if action == "rename":
                rename(o, body.get("name", ""))
            elif action == "logo":
                set_logo(o, body.get("logo") or "")
            elif action == "team.add":
                add_team(d, o, str(body.get("team") or ""))
            elif action == "team.remove":
                remove_team(o, str(body.get("team") or ""))
            elif action == "member.add":
                add_member(o, body.get("email", ""), body.get("role", "member"))
            elif action == "member.role":
                if body.get("role") == "owner" and role_of(o, me) != "owner":
                    raise OrgError("only an owner can make someone an owner")
                set_role(o, body.get("email", ""), body.get("role", ""))
            elif action == "member.remove":
                remove_member(o, body.get("email", ""))
            elif action == "delete":
                if role_of(o, me) != "owner":
                    raise OrgError("only an owner can delete the org")
                o["members"], o["teams"] = [], []
                rep = sync(o, rpc, me)
                delete(d, o)
                save(d, path)
                log(f"{me} deleted org {o['name']}")
                return {"ok": True, "sync": rep}
            else:
                raise OrgError(f"unknown action {action}")
        save(d, path)
        log(f"{me} {action} {o['name']}: {json.dumps({k: v for k, v in body.items() if k != 'logo'}, ensure_ascii=False)[:200]}")
        rep = sync(o, rpc, me) if action in ("create", "team.add", "team.remove", "member.add", "member.role", "member.remove") else {}
        save(d, path)                                      # sync records what it granted
    return {"ok": True, "org": o["id"], "sync": rep,
            "resources": action in ("create", "team.add")}  # the caller shares fonts and libraries in the background


# ---------- command line ----------

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="orgs.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--owner", default="owner@flynn.local")
    c.add_argument("--team", action="append", default=[])
    t = sub.add_parser("team")
    t.add_argument("org")
    t.add_argument("op", choices=["add", "remove"])
    t.add_argument("team")
    m = sub.add_parser("member")
    m.add_argument("org")
    m.add_argument("op", choices=["add", "remove", "role"])
    m.add_argument("email")
    m.add_argument("--role", default="member", choices=ROLES)
    r = sub.add_parser("rename")
    r.add_argument("org")
    r.add_argument("name")
    s = sub.add_parser("sync")
    s.add_argument("org", nargs="?")
    s.add_argument("--resources", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            d = load()
            for o in d["orgs"]:
                print(f"{o['id']}  {o['name']}  teams={len(o['teams'])}  "
                      + ", ".join(f"{m['email']} ({m['role']})" for m in o["members"]))
            return 0
        if a.cmd == "sync":
            rpc = owner_rpc()
            with _lock:
                d = load()
                for o in d["orgs"]:
                    if a.org and a.org not in (o["id"], o["name"]):
                        continue
                    print(o["name"], json.dumps(sync(o, rpc), ensure_ascii=False))
                    if a.resources:
                        print(json.dumps(share_resources(o), ensure_ascii=False))
                save(d)
            return 0
        body, action = {}, ""
        if a.cmd == "create":
            action, body = "create", {"name": a.name, "teams": a.team}
            print(json.dumps(act(action, body, a.owner), ensure_ascii=False))
            return 0
        if a.cmd == "team":
            action, body = f"team.{a.op}", {"org": a.org, "team": a.team}
        elif a.cmd == "member":
            action = {"add": "member.add", "remove": "member.remove", "role": "member.role"}[a.op]
            body = {"org": a.org, "email": a.email, "role": a.role}
        elif a.cmd == "rename":
            action, body = "rename", {"org": a.org, "name": a.name}
        d = load()
        actor = next((m["email"] for m in find(d, body["org"])["members"] if m["role"] == "owner"), None)
        print(json.dumps(act(action, body, actor), ensure_ascii=False))
        return 0
    except OrgError as e:
        print(f"orgs: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
