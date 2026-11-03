"""Studio's design systems (Module: Systems): what flynn knows per system, and recording a live product.

Libraries come from the knowledge store (rows of type component, pattern and token carry a `system`, and components
and tokens a `tier`) plus the folders in systems/; themes are the folders in adapters/html/themes/. Recording wraps
adapters/recorder/recorder.py: one session at a time, started from the browser, saved under
refs/recordings/<system>/<session>/ (git-ignored, local to this Mac). Routes: handle_get / handle_post, like chat.py."""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import quote, unquote

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FLYNN / "kb"))
import chat  # noqa: E402  (origin_ok, host_ok)
import store  # noqa: E402

SYSTEMS_DIR = FLYNN / "systems"
THEMES_DIR = FLYNN / "adapters" / "html" / "themes"
PROPOSALS_DIR = FLYNN / "proposals"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
SOURCE = re.compile(r"`([\w.-]+)`\s+at\s+`([0-9a-f]{7,40})`")
COUNTED = ("component", "pattern", "token")
PRODUCT = ("screen", "feature", "flow")      # product knowledge: pages, functionalities, UX flows
GUIDES = ("GUIDE.md", "README.md")
ACTIVE = ("starting", "recording", "stopping")
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".json": "application/json; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------- design systems
def _theme_ids() -> list[str]:
    return sorted(p.name for p in THEMES_DIR.iterdir() if p.is_dir()) if THEMES_DIR.is_dir() else []


def _library_ids() -> list[str]:
    return sorted(p.name for p in SYSTEMS_DIR.iterdir() if p.is_dir()) if SYSTEMS_DIR.is_dir() else []


def _rows() -> dict[str, dict[str, list[dict]]]:
    """{system: {type: [rows]}} from the knowledge store."""
    out: dict[str, dict[str, list[dict]]] = {}
    c = store.connect()
    try:
        for t in COUNTED + PRODUCT:
            try:
                rows = store.list_rows(c, t)
            except Exception:  # noqa: BLE001 - a store from before the product types
                rows = []
            for r in rows:
                if r.get("system"):
                    out.setdefault(r["system"], {t2: [] for t2 in COUNTED + PRODUCT})[t].append(r)
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _folder(sid: str, kind: str) -> Path | None:
    p = (THEMES_DIR if kind == "theme" else SYSTEMS_DIR) / sid
    return p if p.is_dir() else None


def _guide(folder: Path | None) -> Path | None:
    for n in GUIDES:
        if folder and (folder / n).is_file():
            return folder / n
    return None


def _rel(p: Path) -> str:
    """A path as Studio shows it: relative to flynn when inside it, else to the systems folder's parent."""
    for base in (FLYNN, SYSTEMS_DIR.parent):
        try:
            return str(p.relative_to(base))
        except ValueError:
            continue
    return p.name


def _source(guide: Path | None) -> dict | None:
    if not guide:
        return None
    m = SOURCE.search(guide.read_text(encoding="utf-8", errors="replace"))
    return {"repo": m.group(1), "commit": m.group(2)} if m else None


def _updated(folder: Path | None) -> str | None:
    if not folder:
        return None
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(folder)], cwd=FLYNN, capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _proposals(sid: str) -> int:
    """Pending proposals (proposals/*.md with `status: pending` in the front matter) that mention the system."""
    if not PROPOSALS_DIR.is_dir():
        return 0
    n, word = 0, re.compile(rf"(?<![\w-]){re.escape(sid)}(?![\w-])")
    for p in PROPOSALS_DIR.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        head = text.split("\n---", 1)[0]
        if re.search(r"^status:\s*pending\s*$", head, re.M) and word.search(text):
            n += 1
    return n


def _sessions(system: str) -> int:
    d = recordings_root() / system
    return sum(1 for p in d.iterdir() if p.is_dir()) if d.is_dir() else 0


def _summary(sid: str, kind: str, rows: dict[str, list[dict]] | None) -> dict:
    folder = _folder(sid, kind)
    guide = _guide(folder)
    counts: dict[str, dict[str, int]] = {}
    local = 0
    for t in COUNTED:
        c: dict[str, int] = {}
        for r in (rows or {}).get(t, []):
            if t == "component" and (r.get("description") or "").startswith("Page-specific"):
                local += 1                      # a component made for one page is not part of the library
                continue
            tier = r.get("tier") or "none"
            c[tier] = c.get(tier, 0) + 1
        counts[t] = c
    return {"id": sid, "name": display_name(sid), "kind": "recorded" if sid.startswith("rec:") else kind, "counts": counts,
            "page_specific": local, "total": sum(sum(v.values()) for v in counts.values()),
            "guide": _rel(guide) if guide else None, "source": _source(guide),
            "updated": _updated(folder), "proposals": _proposals(sid), "recordings": _sessions(sid)}


NAMES = {"dk-app": "your company App", "editorial": "DK Editorial", "dk-mckinsey": "DK McKinsey", "dk-publication": "DK Publication", "dk-release-note": "DK Release Note",
         "admin-panel": "Admin Panel", "your company": "your company",
         "dk-internal": "your company Internal Tools"}


def display_name(sid: str) -> str:
    """How a system reads in Studio: known names as written, recorded libraries marked as recorded."""
    base = sid[4:] if sid.startswith("rec:") else sid
    name = NAMES.get(base) or " ".join(w.upper() if w in ("dk", "ui") else w.capitalize() for w in base.split("-"))
    return f"{name} (recorded)" if sid.startswith("rec:") else name


def list_systems() -> list[dict]:
    rows = _rows()
    themes = _theme_ids()
    libs = sorted(set(_library_ids()) | {s for s in rows if s not in themes})
    return ([_summary(s, "library", rows.get(s)) for s in libs] + [_summary(s, "theme", rows.get(s)) for s in themes])


def choices() -> list[dict]:
    """What a conversation can be pinned to: every library and document theme, by display name (recordings are the
    raw material of a library, not a system to design with, so they are left out). Cheap: no git or counts."""
    themes = _theme_ids()
    rows = _rows()

    def retired(sid: str) -> bool:             # every row of the system is tagged retired (an old system kept for history)
        items = [r for rs in rows.get(sid, {}).values() for r in rs]
        return bool(items) and all("retired" in str(r.get("tags") or "") for r in items)
    libs = sorted(s for s in set(_library_ids()) | set(rows)
                  if s not in themes and not s.startswith("rec:") and not retired(s))
    return ([{"id": s, "name": display_name(s), "kind": "library"} for s in libs] +
            [{"id": s, "name": display_name(s), "kind": "theme"} for s in themes])


def record_choice(folder: Path, choice: str) -> bool:
    """Write a conversation's design system into the run's run.json (`system` for a library; `theme` and `system` for a
    document theme; `design_system: "none"` for none). Only an existing run.json is touched. True when it changed."""
    p = Path(folder) / "run.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(rec, dict) or choice in ("", "auto"):
        return False
    before = json.dumps(rec, sort_keys=True)
    if choice == "none":
        rec["system"], rec["design_system"] = "", "none"
    else:
        rec.pop("design_system", None)
        rec["system"] = choice
        if choice in _theme_ids():
            rec["theme"] = choice
    if json.dumps(rec, sort_keys=True) == before:
        return False
    p.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


def run_choice(folder: Path) -> str | None:
    """The design system a run's run.json names (an id, or "none"); None when it names nothing."""
    try:
        rec = json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    if rec.get("design_system") == "none":
        return "none"
    return str(rec.get("system") or rec.get("theme") or "").strip() or None


def system_detail(sid: str) -> dict:
    rows = _rows()
    kind = "theme" if sid in _theme_ids() else "library" if (sid in _library_ids() or sid in rows) else None
    if kind is None:
        raise KeyError(f"no system {sid}")
    out = _summary(sid, kind, rows.get(sid))
    mine = rows.get(sid, {})
    out["components"] = [{"name": r.get("name"), "tier": r.get("tier"), "description": (r.get("description") or "")[:140],
                          # one page's own component (ingest_system.py writes this prefix): listed apart from the library
                          "scope": "page-local" if (r.get("description") or "").startswith("Page-specific") else "library"}
                         for r in mine.get("component", [])]
    out["patterns"] = [{"name": r.get("name"), "description": (r.get("description") or "")[:140]}
                       for r in mine.get("pattern", [])]
    out["tokens"] = [{"name": r.get("name"), "value": r.get("value"), "tier": r.get("tier")}
                     for r in mine.get("token", [])[:400]]
    def j(v):
        if isinstance(v, str) and v[:1] in "[{":
            try:
                return json.loads(v)
            except ValueError:
                return v
        return v
    keep = {"screen": ("route", "name", "name_fa", "parent", "nav_group", "nav_order", "purpose", "audience", "key_actions",
                       "data_shown", "states", "components", "apis", "modules", "code_path", "screenshots", "tier"),
            "feature": ("name", "name_fa", "description", "business_rules", "screens", "entities", "apis", "tier"),
            "flow": ("name", "name_fa", "goal", "steps", "entry_points", "edge_cases", "screens", "tier")}
    for t, cols in keep.items():
        out[t + "s"] = [{c: j(r.get(c)) for c in cols} for r in mine.get(t, [])]
    nav = _folder(sid, kind) and (_folder(sid, kind) / "navigation.json")
    if nav and nav.is_file():
        try:
            out["navigation"] = json.loads(nav.read_text(encoding="utf-8"))
        except ValueError:
            pass
    guide = _guide(_folder(sid, kind))
    out["guide_excerpt"] = "\n".join(guide.read_text(encoding="utf-8", errors="replace").splitlines()[:60]) if guide else ""
    if kind == "theme":
        out["theme_files"] = sorted(str(p.relative_to(THEMES_DIR / sid)) for p in (THEMES_DIR / sid).rglob("*.css"))
    return out


# ---------------------------------------------------------------- recording
_LOCK = threading.Lock()
_ACTIVE: dict = {}          # {"session": Session, "system", "mode", "started"} while a recording is current


def _recorder():
    """adapters/recorder/recorder.py, imported on first use (it pulls in the browser adapters)."""
    sys.path.insert(0, str(FLYNN / "adapters" / "recorder"))
    import recorder
    return recorder


def recordings_root() -> Path:
    return Path(os.environ.get("FLYNN_RECORDINGS_DIR", FLYNN / "refs" / "recordings"))


def start_recording(cfg: dict) -> dict:
    cfg = cfg or {}
    url, system, mode = cfg.get("url"), cfg.get("system"), cfg.get("mode") or "watch"
    if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url):
        raise ValueError("url must start with http:// or https://")
    if not isinstance(system, str) or not SLUG.match(system):
        raise ValueError("system must be a slug: lowercase letters, digits and dashes, 2 to 41 characters")
    if mode not in ("watch", "crawl"):
        raise ValueError("mode must be watch or crawl")
    if cfg.get("rights") is not True:
        raise ValueError("confirm you have the right to record this product (rights: true)")
    widths = cfg.get("widths")
    if widths is not None:
        if (not isinstance(widths, list) or not widths or len(widths) > 4
                or not all(isinstance(w, int) and not isinstance(w, bool) and 200 <= w <= 4000 for w in widths)):
            raise ValueError("widths must be one to four whole numbers between 200 and 4000")
    with _LOCK:
        cur = _ACTIVE.get("session")
        if cur is not None and cur.state in ACTIVE:
            raise RuntimeError(f"a recording is already running ({_ACTIVE['system']})")
        rcfg = {"system": system, "url": url, "how": mode, "description": f"recorded from Studio ({mode})"}
        if widths:
            rcfg["sizes"] = widths
        sess = _recorder().Session(rcfg)
        _ACTIVE.clear()
        _ACTIVE.update(session=sess, system=system, mode=mode, started=_dt.datetime.now().isoformat(timespec="seconds"))
        sess.start()
    return recording_state()


def recording_state() -> dict:
    with _LOCK:
        s = _ACTIVE.get("session")
        if s is None:
            return {"state": "idle"}
        return {"state": s.state, "system": _ACTIVE["system"], "session": s.id, "pages": len(s.pages),
                "mode": _ACTIVE["mode"], "started": _ACTIVE["started"], "error": s.error}


def _current():
    with _LOCK:
        s = _ACTIVE.get("session")
    if s is None or s.state not in ACTIVE:
        raise RuntimeError("no recording is running")
    return s


def capture_now() -> dict:
    _current().capture_now()
    return recording_state()


def stop_recording() -> dict:
    _current().stop()
    return recording_state()


def _thumb(folder: Path, page: dict) -> str | None:
    name = Path(str(page.get("folder") or "")).name
    if not name:
        return None
    hits = sorted((folder / "pages" / name).glob("w*/viewport.png"))
    return str(hits[0].relative_to(folder)) if hits else None


def list_recordings(system: str | None = None, limit: int = 20) -> list[dict]:
    root = recordings_root()
    if system is not None and not SLUG.match(system):
        return []
    found = sorted(root.glob(f"{system or '*'}/*/session.json"), key=lambda p: p.parent.name, reverse=True)
    out = []
    for f in found[:max(1, int(limit))]:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sysid, sess = f.parent.parent.name, f.parent.name
        pages = []
        for p in s.get("pages") or []:
            t = _thumb(f.parent, p)
            pages.append({"title": p.get("title"), "url": p.get("url"), "thumb": t,
                          "thumb_url": f"/rec/{quote(sysid)}/{quote(sess)}/{quote(t)}" if t else None})
        started = next(iter(sorted(p.get("time") for p in s.get("pages") or [] if p.get("time"))), None)
        out.append({"system": sysid, "session": sess, "started": started or _stamp(sess), "mode": s.get("how"),
                    "url": s.get("url"), "state": s.get("state"), "pages": pages})
    return out


def _stamp(session: str) -> str | None:
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)(\d\d)", session)
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}" if m else None


def recording_file(system: str, session: str, rel: str) -> Path:
    for part in (system, session):
        if not part or "/" in part or "\\" in part or part in (".", "..") or "\0" in part:
            raise KeyError("no such recording")
    base = (recordings_root() / system / session).resolve()
    root = recordings_root().resolve()
    if not base.is_dir() or base != root / system / session:
        raise KeyError("no such recording")
    if not rel or "\0" in rel:
        raise KeyError("no such file")
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file():
        raise KeyError("no such file")
    return p


# ---------------------------------------------------------------- health
def health() -> list[dict]:
    out = []

    def add(i, label, status, detail, fix=""):
        out.append({"id": i, "label": label, "status": status, "detail": detail, "fix": fix})
    ok = importlib.util.find_spec("selenium") is not None
    add("selenium", "Selenium (recording)", "ok" if ok else "warn",
        "installed" if ok else "not installed; recording a live product needs it", "" if ok else "python3 -m pip install --user selenium")
    chrome = os.environ.get("FLYNN_CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    found = chrome if os.path.exists(chrome) else next(filter(None, (shutil.which(n) for n in
                                                                      ("google-chrome", "chromium", "chrome"))), None)
    add("chrome", "Chrome (recording)", "ok" if found else "warn", found or f"not found at {chrome}",
        "" if found else "Install Google Chrome, or set FLYNN_CHROME_BIN to its binary")
    return out


# ---------------------------------------------------------------- HTTP
def handle_get(h, parts: list[str], query: dict) -> bool:
    if parts[:1] == ["sysfile"] and len(parts) == 4 and parts[2] == "screens":
        # a design system's page screenshots (product knowledge), pictures only
        sid, name = unquote(parts[1]), unquote(parts[3])
        d = (SYSTEMS_DIR / sid / "screens").resolve()
        f = (d / name).resolve()
        if d not in f.parents or f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not f.is_file():
            h._json({"error": "not found"}, 404)
            return True
        h._send(f.read_bytes(), TYPES.get(f.suffix.lower()) or "image/webp")
        return True
    if parts[:1] == ["rec"] and len(parts) >= 4:
        if not chat.host_ok(h):
            h._json({"error": "host not allowed"}, 403)
            return True
        try:
            p = recording_file(unquote(parts[1]), unquote(parts[2]), "/".join(unquote(x) for x in parts[3:]))
        except KeyError as e:
            h._json({"error": str(e.args[0] if e.args else e)}, 404)
            return True
        ctype = TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        h._send(p.read_bytes(), ctype)
        return True
    if parts[:2] not in (["api", "systems"], ["api", "record"], ["api", "recordings"]):
        return False
    if not chat.host_ok(h):
        h._json({"error": "host not allowed"}, 403)
        return True
    if parts == ["api", "systems"]:
        h._json(list_systems())
    elif parts[:2] == ["api", "systems"] and len(parts) == 3:
        try:
            h._json(system_detail(unquote(parts[2])))
        except KeyError as e:
            h._json({"error": str(e.args[0] if e.args else e)}, 404)
    elif parts == ["api", "record", "state"]:
        h._json(recording_state())
    elif parts == ["api", "recordings"]:
        q = query.get("system")
        q = q[0] if isinstance(q, list) else q
        h._json(list_recordings(q or None))
    else:
        return False
    return True


def _policy():
    sys.path.insert(0, str(FLYNN / "scripts"))
    import systems_policy
    return systems_policy


def handle_post(h, parts: list[str], body: dict) -> bool:
    if parts[:2] == ["api", "record"] and parts[2:3] == ["start"]:
        ok, why = _policy().can_change()
        if not ok:
            h._json({"error": why}, 403)
            return True
    if parts[:2] != ["api", "record"] or len(parts) != 3 or parts[2] not in ("start", "capture", "stop"):
        return False
    if not chat.origin_ok(h):
        h._json({"error": "origin not allowed"}, 403)
        return True
    fn = {"start": lambda: start_recording(body or {}), "capture": capture_now, "stop": stop_recording}[parts[2]]
    try:
        h._json(fn())
    except ValueError as e:
        h._json({"error": str(e)}, 400)
    except RuntimeError as e:
        h._json({"error": str(e)}, 409)
    return True
