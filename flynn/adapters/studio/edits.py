"""Quick edits (Studio module 3): changes the user makes directly on a frame (text, hide, a style value, a note), stored as
requests in <run>/edits.json for flynn to apply to the source page. The frame shows them at once; the source page
changes only when flynn applies them (studio_edits tool), so a reload shows the real page again until then.

Edit: {n, page, option, version, selector, kind: text|hide|style|note, before, after, prop, value, note,
       status: pending|applied|rejected, time, reply}"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path

KINDS = ("text", "hide", "style", "note")
STATUSES = ("pending", "applied", "rejected")
STYLE_PROPS = ("color", "background-color", "font-size", "font-weight", "padding", "margin", "gap", "border-radius",
               "width", "height", "text-align", "letter-spacing", "line-height")
_lock = threading.Lock()


def _path(run: Path) -> Path:
    return run / "edits.json"


def load(run: Path) -> list[dict]:
    try:
        d = json.loads(_path(run).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(run: Path, edits: list[dict]) -> None:
    tmp = _path(run).with_suffix(".tmp")
    tmp.write_text(json.dumps(edits, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(run))


def _clean(e: dict) -> dict:
    if not isinstance(e, dict):
        raise ValueError("an edit must be an object")
    kind = e.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    sel = str(e.get("selector") or "")[:400]
    if not sel and kind != "note":
        raise ValueError("an edit needs the element's selector")
    out = {"kind": kind, "selector": sel, "page": str(e.get("page") or "page/index.html")[:200],
           "option": str(e.get("option") or "main")[:60], "version": str(e.get("version") or "")[:80]}
    if ".." in out["page"] or out["page"].startswith("/"):
        raise ValueError("bad page")
    if kind == "text":
        out["before"], out["after"] = str(e.get("before") or "")[:4000], str(e.get("after") or "")[:4000]
        if out["before"] == out["after"]:
            raise ValueError("the text didn't change")
    elif kind == "style":
        prop = str(e.get("prop") or "")
        if prop not in STYLE_PROPS:
            raise ValueError(f"style edits support {', '.join(STYLE_PROPS)}")
        out["prop"], out["before"], out["value"] = prop, str(e.get("before") or "")[:120], str(e.get("value") or "")[:120]
    elif kind == "hide":
        out["after"] = "hidden"
    if e.get("note"):
        out["note"] = str(e["note"])[:2000]
    if kind == "note" and not out.get("note"):
        raise ValueError("a note needs text")
    return out


def add(run: Path, items: list[dict]) -> list[dict]:
    cleaned = [_clean(e) for e in items]
    with _lock:
        edits = load(run)
        n = max((e["n"] for e in edits), default=0)
        now = _dt.datetime.now().isoformat(timespec="seconds")
        added = []
        for c in cleaned:
            # a second text edit of the same element replaces the pending one
            same = next((e for e in edits if e["status"] == "pending" and e["kind"] == c["kind"] == "text"
                         and e["selector"] == c["selector"] and e["page"] == c["page"]), None)
            if same:
                same.update({"after": c["after"], "time": now})
                added.append(same)
                continue
            n += 1
            e = {"n": n, **c, "status": "pending", "time": now}
            edits.append(e)
            added.append(e)
        _save(run, edits)
    return added


def set_status(run: Path, n: int, status: str, reply: str | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with _lock:
        edits = load(run)
        e = next((x for x in edits if x["n"] == int(n)), None)
        if e is None:
            raise KeyError(f"no edit {n}")
        e["status"] = status
        if reply:
            e["reply"] = str(reply)[:2000]
        _save(run, edits)
    return e


def pending(run: Path) -> list[dict]:
    return [e for e in load(run) if e["status"] == "pending"]
