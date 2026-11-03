"""Design system changes: flynn proposes, the user approves, then the store and the canvas library update.

A design system evolves from two places: recordings of the live product, and what the user tells flynn in chat
("our buttons are 8px round now", "add a warning chip"). flynn never edits a system directly. It writes a change,
the flynn tab shows it as a card (old -> new for every item), and only the user's Approve applies it: the store rows
change, then `library.py sync <system>` brings the library up to date with component ids kept.

A change (JSON) is a list of operations on one system:
  {"system": "admin-panel", "title": "...", "why": "...", "source": "chat" | "recording:<session folder>",
   "file": "<canvas file id the conversation belongs to>",
   "ops": [
     {"op": "token.set", "name": "radius-medium", "kind": "radius", "value": "8px"},           # add or update
     {"op": "token.remove", "name": "color-old-brand"},
     {"op": "component.set", "name": "Button", "fields": {"description": "...", "usage": {...}, "variants": {...}}},
     {"op": "component.remove", "name": "LegacyChip"},
     {"op": "component.sample", "name": "Chip", "variant": "warning", "markup": "<span class=...>...</span>"},
     {"op": "recording.apply", "session": "<recording folder>"}                               # use its review.json
   ]}

  changes.py propose --file change.json     validate, record the current values ("before"), store as pending
  changes.py list [--status pending] / show ID
  changes.py approve ID [--no-sync] / reject ID [--reason TEXT]

Changes live in fstore ns ds-changes (~/.flynn/ds-changes/<id>.json, or Postgres with FLYNN_DB_URL). Log: ~/.flynn/logs/ds-changes.log.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "kb"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FLYNN / "scripts"))
import fstore  # noqa: E402

DIR = Path.home() / ".flynn" / "ds-changes"
LOG = Path.home() / ".flynn" / "logs" / "ds-changes.log"
SAMPLES = FLYNN / "systems"                       # systems/<system>/library-samples/<Name>-<variant>.html
OPS = {"token.set", "token.remove", "component.set", "component.remove", "component.sample", "recording.apply"}
COMPONENT_FIELDS = {"description", "anatomy", "variants", "props", "states", "usage", "rtl_notes"}
# fstore ns "ds-changes", one record per id; on files, ~/.flynn/ds-changes/<id>.json as before.
fstore.files("ds-changes", folder=lambda: DIR, indent=1)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(dt.datetime.now().isoformat(timespec="seconds") + " " + msg + "\n")


def _conn():
    import store
    return store.connect()


def _row(conn, table: str, system: str, name: str) -> dict | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE system = ? AND name = ?", (system, name)).fetchone()
    return dict(r) if r else None


def _clean(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "change"


def validate(ch: dict) -> list[str]:
    errs = []
    if not ch.get("system"):
        errs.append("a change needs a system")
    if not ch.get("title"):
        errs.append("a change needs a one-line title")
    if not isinstance(ch.get("ops"), list) or not ch["ops"]:
        errs.append("a change needs at least one op")
    for i, op in enumerate(ch.get("ops") or []):
        kind = op.get("op")
        if kind not in OPS:
            errs.append(f"op {i + 1}: unknown op {kind!r}")
            continue
        if kind != "recording.apply" and not op.get("name"):
            errs.append(f"op {i + 1}: {kind} needs a name")
        if kind == "token.set" and (not op.get("value") or not op.get("kind")):
            errs.append(f"op {i + 1}: token.set needs kind and value")
        if kind == "component.set" and not (set(op.get("fields") or {}) & COMPONENT_FIELDS):
            errs.append(f"op {i + 1}: component.set needs fields ({', '.join(sorted(COMPONENT_FIELDS))})")
        if kind == "component.sample" and not op.get("markup"):
            errs.append(f"op {i + 1}: component.sample needs markup")
        if kind == "recording.apply" and not (op.get("session") and Path(op["session"]).is_dir()):
            errs.append(f"op {i + 1}: recording.apply needs an existing session folder")
    return errs


def propose(ch: dict) -> dict:
    _locked()
    errs = validate(ch)
    if errs:
        raise ValueError("; ".join(errs))
    conn = _conn()
    for op in ch["ops"]:                              # what it is today, so the card can show old -> new
        if op["op"].startswith("token."):
            r = _row(conn, "tokens", ch["system"], op["name"])
            op["before"] = {"value": r["value"], "kind": r["kind"]} if r else None
        elif op["op"].startswith("component.") and op["op"] != "component.sample":
            r = _row(conn, "components", ch["system"], op["name"])
            op["before"] = {k: r[k] for k in ("description", "variants", "states", "usage") if r and r.get(k)} if r else None
    now = dt.datetime.now()
    ch.update({"id": f"{now.strftime('%Y%m%d-%H%M%S')}-{_clean(ch['title'])}", "status": "pending",
               "created": now.isoformat(timespec="seconds"), "source": ch.get("source") or "chat"})
    _save(ch)
    log(f"proposed {ch['id']} ({ch['system']}, {len(ch['ops'])} ops)")
    if ch.get("file"):                                # show the card in the flynn tab right away
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8772/api/ds/notify", json.dumps({"file": ch["file"]}).encode(),
                                         {"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:  # noqa: BLE001 - the card shows after the turn instead
            pass
    return ch


def load(cid: str) -> dict:
    if not re.fullmatch(r"[\w.-]+", cid or ""):
        raise ValueError("bad change id")
    ch = fstore.get("ds-changes", cid)
    if ch is None:
        raise FileNotFoundError(f"no change {cid}")       # an OSError, as when it was only a file
    return ch


def _save(ch: dict) -> None:
    fstore.put("ds-changes", ch["id"], ch)


def changes(status: str | None = None) -> list[dict]:
    out = [*fstore.list("ds-changes").values()]
    return [c for c in out if not status or c.get("status") == status]


def _delete(conn, etype: str, table: str, system: str, name: str) -> bool:
    r = conn.execute(f"SELECT id FROM {table} WHERE system = ? AND name = ?", (system, name)).fetchone()
    if not r:
        return False
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (r["id"],))
    conn.execute("DELETE FROM fts WHERE eid = ?", (r["id"],))
    conn.commit()
    return True


def apply(ch: dict) -> list[str]:
    import store
    conn = _conn()
    system = ch["system"]
    recorded = system.startswith("rec:")
    done = []
    for op in ch["ops"]:
        kind = op["op"]
        if kind == "token.set":
            cur = _row(conn, "tokens", system, op["name"])
            store.upsert(conn, "token", {
                "system": system, "name": op["name"], "kind": op["kind"], "value": op["value"],
                "level": op.get("level") or (cur or {}).get("level") or "raw",
                "refers_to": op.get("refers_to"), "notes": op.get("notes") or ch.get("why"),
                "tier": (cur or {}).get("tier") or ("inferred" if recorded else "proposed"),
                "source": (cur or {}).get("source") or f"ds-change:{ch['id']}", "run_id": ch["id"]},
                origin="generated" if recorded else "flynn", tags=["ds-change"])
            done.append(f"token {op['name']} = {op['value']}")
        elif kind == "token.remove":
            if _delete(conn, "token", "tokens", system, op["name"]):
                done.append(f"token {op['name']} removed")
        elif kind == "component.set":
            cur = _row(conn, "components", system, op["name"])
            row = {"system": system, "name": op["name"], "tier": (cur or {}).get("tier") or ("inferred" if recorded else "proposed"),
                   "run_id": ch["id"], **{k: v for k, v in op["fields"].items() if k in COMPONENT_FIELDS}}
            store.upsert(conn, "component", row, origin="generated" if recorded else "flynn", tags=["ds-change"])
            done.append(f"component {op['name']} updated ({', '.join(sorted(set(op['fields']) & COMPONENT_FIELDS))})")
        elif kind == "component.remove":
            if _delete(conn, "component", "components", system, op["name"]):
                done.append(f"component {op['name']} removed")
        elif kind == "component.sample":
            folder = SAMPLES / _clean(system) / "library-samples"
            folder.mkdir(parents=True, exist_ok=True)
            f = folder / f"{re.sub(r'[^A-Za-z0-9.]+', '', op['name'])}-{_clean(op.get('variant') or 'sample')}.html"
            f.write_text(op["markup"], encoding="utf-8")
            done.append(f"component {op['name']} sample {f.name}")
        elif kind == "recording.apply":
            done.append(f"recording {Path(op['session']).name} applied")
    try:
        store.export(conn)
    except Exception as e:  # noqa: BLE001 - the store already holds the change
        done.append(f"(export skipped: {e})")
    return done


def _locked(who: str | None = None) -> None:
    """Design systems change only on the owner's own copy of flynn (systems/systems.json); refuse anywhere else."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import systems_policy
    systems_policy.check_change(who)


def approve(cid: str, sync: bool = True, log_fn=log, who: str | None = None) -> dict:
    _locked(who)
    ch = load(cid)
    if ch["status"] != "pending":
        raise ValueError(f"change {cid} is already {ch['status']}")
    ch["applied"] = apply(ch)
    ch["status"] = "approved"
    ch["approved"] = dt.datetime.now().isoformat(timespec="seconds")
    _save(ch)
    log_fn(f"approved {cid}: {ch['applied']}")
    if sync:
        import library
        if library.state(ch["system"]).get("file") or ch["system"].startswith("rec:") or ch["system"] == "admin-panel":
            r = library.sync(ch["system"])
            ch["library"] = {"name": r.get("name"), "tokens": (r.get("tokens") or {}).get("tokens"),
                             "components": {k: v for k, v in (r.get("components") or {}).items() if k != "map"}}
            _save(ch)
    return ch


def reject(cid: str, reason: str = "", who: str | None = None) -> dict:
    _locked(who)
    ch = load(cid)
    if ch["status"] != "pending":
        raise ValueError(f"change {cid} is already {ch['status']}")
    ch.update({"status": "rejected", "reason": reason, "rejected": dt.datetime.now().isoformat(timespec="seconds")})
    _save(ch)
    log(f"rejected {cid}: {reason}")
    return ch


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="changes.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--file", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    a_ = sub.add_parser("approve")
    a_.add_argument("id")
    a_.add_argument("--no-sync", action="store_true")
    r = sub.add_parser("reject")
    r.add_argument("id")
    r.add_argument("--reason", default="")
    a = ap.parse_args(argv)
    if a.cmd == "propose":
        ch = propose(json.loads(Path(a.file).read_text(encoding="utf-8")))
        print(f"proposed {ch['id']}: {ch['title']} ({len(ch['ops'])} ops). the user approves it in the flynn tab.")
    elif a.cmd == "list":
        for c in changes(a.status):
            print(f"{c['id']}  {c['status']:9} {c['system']:20} {c['title']}")
    elif a.cmd == "show":
        print(json.dumps(load(a.id), ensure_ascii=False, indent=1))
    elif a.cmd == "approve":
        print(json.dumps(approve(a.id, sync=not a.no_sync), ensure_ascii=False, indent=1)[:2000])
    else:
        print(json.dumps(reject(a.id, a.reason), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
