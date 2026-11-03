#!/usr/bin/env python3
"""flynn panel server: the chat behind the flynn tab in Penpot.

  panel_server.py serve [--port 8772]

Each message from the tab runs one turn of `claude -p --agent flynn` (the user's own Claude Code login,
the same flynn agent, skills, store and run folders as the terminal), resuming the same Claude session
for each Penpot file. Output streams to the tab as server-sent events. flynn runs in teammate mode:
its NEEDS_ALIGNMENT / RESULT / BLOCKED blocks become cards in the tab, and answers go back as ALIGNED.

Permissions: a safe list is allowed up front, writes to ~/code and git push are always
refused, and anything else that would need approval is refused automatically and shown in the tab as
a card; "Allow and continue" adds that exact rule for this conversation and resumes.

API (JSON; Penpot's nginx forwards /flynn/api/ here):
  POST /api/send    {file, text, file_name?}
  POST /api/answer  {file, answers: [{n, answer}], note?}
  POST /api/allow   {file, rules: [...]}
  POST /api/mode    {file, mode: penpot|studio}
  POST /api/push    {file, run?}         build the conversation's runs in Penpot now
  POST /api/stop    {file}
  POST /api/reset   {file}             the current conversation goes into history
  GET  /api/conversations?file=ID      the current conversation and past ones, newest first
  POST /api/conversations/open   {file, id}   reopen a past one (resumes its Claude session)
  POST /api/conversations/delete {file, id}
  GET  /api/history?file=ID
  GET  /api/events?file=ID&since=N     (server-sent events)
  GET  /api/health

State: scripts/fstore.py: files under $FLYNN_PANEL_STATE (default ~/.flynn/panel/<file>/events.jsonl and state.json),
or Postgres with FLYNN_DB_URL. Uploads stay files in the file's folder.
Switch: ~/.flynn/features.json {"panel_chat": false} turns it off. Log: ~/.flynn/logs/panel_chat.log.
Listens on 127.0.0.1 only."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import stream  # noqa: E402
import record_api  # noqa: E402
from bridge import PenpotError  # noqa: E402
sys.path.insert(0, str(FLYNN / "scripts"))
import fstore  # noqa: E402
from perms import BASE_ALLOW, DENY, PROTECTED_CODE as PROTECTED_CODE  # noqa: E402,F401  (shared with Studio)

RUNS_DIR = FLYNN / "runs"
STUDIO = os.environ.get("FLYNN_STUDIO_URL", "http://127.0.0.1:8770")
RUN_REF = re.compile(r"runs/(\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*[a-z0-9])")


def push_run_fn(run: Path, file_id: str, team: str | None = None, progress=None) -> dict:
    """Build a run on the conversation's file through flynn's builder browser (its own hidden Chrome and account).
    the user's tab is never used for the build, so it does not matter which file he has open, and a tab reload can no
    longer swallow a build. the user sees the layers arrive live, like a collaborator's. If the builder cannot reach the
    file, the build falls back to the user's open tab as before."""
    import push
    sys.path.insert(0, str(FLYNN / "adapters" / "library"))
    import builder
    say = progress or (lambda *a: None)
    plog = lambda m: log(f"push {run.name}: {m}")  # noqa: E731
    with _lib_lock:                                   # one builder browser: never alongside a library sync
        try:
            team = builder.file_team(file_id) or team    # the file's own team, never a stale hint
        except Exception:  # noqa: BLE001
            pass
        try:
            say("check", "Opening the file in flynn's builder browser", 1)
            bridge = builder.open_file(file_id, team)
        except Exception as e:  # noqa: BLE001
            plog(f"builder route unavailable, using the open tab: {e}")
            say("check", f"flynn's builder could not open the file ({str(e)[:240]}); using your open tab", 1)
            try:
                return push.push_run(run, file_id=file_id, team=team, log=plog, progress=progress)
            except Exception as e2:  # noqa: BLE001 - say both: the tab's error alone hid the real cause
                raise type(e2)(f"{e2} (flynn's builder: {str(e)[:240]})") from e2
        return push.push_run(run, file_id=file_id, team=team, log=plog, progress=progress, bridge=bridge,
                             reopen=lambda: builder.open_file(file_id, team))


CONTEXT_JS = r"""
// What the user is looking at in Penpot right now: the page and the selection, with each selected element's
// top-level board (on a "flynn · <run>" page, that board is one option of the run).
const ROOT = '00000000-0000-0000-0000-000000000000';
const page = penpot.currentPage;
function top(s) { let p = s; while (p.parent && p.parent.id !== ROOT) p = p.parent; return p; }
function path(s) { const out = []; let p = s.parent; while (p && p.id !== ROOT) { out.unshift(p.name); p = p.parent; } return out.join(' / '); }
const sel = (penpot.selection || []).slice(0, 12).map((s) => {
  const b = top(s);
  const inst = s.isComponentInstance && s.isComponentInstance() ? s.component() : null;
  return { id: s.id, name: s.name, type: s.type, path: path(s),
           text: s.type === 'text' ? String(s.characters).slice(0, 400) : null,
           component: inst ? inst.name : null,
           board: b && b !== s ? b.name : null,
           x: Math.round(s.x - (b && b !== s ? b.x : 0)), y: Math.round(s.y - (b && b !== s ? b.y : 0)),
           w: Math.round(s.width), h: Math.round(s.height) };
});
return { file: penpot.currentFile ? penpot.currentFile.name : null, page: page ? page.name : null, selection: sel,
         more: Math.max(0, (penpot.selection || []).length - 12) };
"""


def context_fn(ch: "Channel") -> dict | None:
    """the user's Penpot context for this turn: the page, the selection, and a PNG of the selection."""
    if os.environ.get("FLYNN_PANEL_CONTEXT", "1") == "0":
        return None
    from bridge import Bridge
    b = Bridge(timeout=20)
    ctx = b.run_js(CONTEXT_JS)
    if not isinstance(ctx, dict):
        return None
    if ctx.get("selection"):
        try:
            png = b.export_png("selection")
            out = ch.dir / "selection.png"
            out.write_bytes(png)
            ctx["selection_png"] = str(out)
        except Exception as e:  # noqa: BLE001 - the picture is a bonus
            log(f"{ch.file_id} selection export failed: {e}")
    m = re.match(r"^flynn · (\d{4}-\d{2}-\d{2}-[a-z0-9-]+)$", ctx.get("page") or "")
    if m:
        ctx["run"] = m.group(1)
    return ctx


SELECTION_JS = r"""
const sel = penpot.selection || [];
return { page: penpot.currentPage ? penpot.currentPage.name : null, count: sel.length,
         items: sel.slice(0, 8).map((s) => ({ id: s.id, name: s.name, type: s.type,
           w: Math.round(s.width), h: Math.round(s.height),
           component: (s.isComponentInstance && s.isComponentInstance() && s.component()) ? s.component().name : null })) };
"""


def selection_fn(ch: "Channel") -> dict:
    """What is selected right now (for the box above the message field), with a small preview when it changes."""
    if os.environ.get("FLYNN_PANEL_CONTEXT", "1") == "0":
        return {"count": 0, "items": []}
    from bridge import Bridge
    try:
        b = Bridge(timeout=8)
        out = b.run_js(SELECTION_JS)
    except Exception as e:  # noqa: BLE001 - the box just stays empty
        return {"count": 0, "items": [], "error": str(e)[:200]}
    if not isinstance(out, dict):           # the connector reports some failures as text (a sleeping tab)
        return {"count": 0, "items": [], "error": str(out)[:200]}
    key = ",".join(i["id"] for i in out.get("items", []))
    out["key"] = key
    if key and getattr(ch, "_preview_key", None) != key:
        try:
            import base64
            png = b.export_png("selection")
            ch._preview = "data:image/png;base64," + base64.b64encode(png).decode()
        except Exception:  # noqa: BLE001
            ch._preview = None
        ch._preview_key = key
    out["preview"] = getattr(ch, "_preview", None) if key else None
    return out


def with_context(text: str, ctx: dict | None) -> str:
    if not ctx:
        return text
    return text + "\n\n<penpot_context>\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n</penpot_context>"


def run_pages(run: Path) -> list[Path]:
    pages = [run / "page" / "index.html"] + sorted((run / "options").glob("*/page/index.html"))
    return [p for p in pages if p.is_file()]


def pages_mtime(run: Path) -> float:
    m = 0.0
    for page in run_pages(run):
        for f in page.parent.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".css", ".png", ".jpg", ".svg", ".webp"):
                m = max(m, f.stat().st_mtime)
    return m

FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def flynn_home() -> Path:
    return Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))


def state_root() -> Path:
    return Path(os.environ.get("FLYNN_PANEL_STATE", str(flynn_home() / "panel")))


# Chats live in fstore: the current conversation is the event log "panel/<file>" with the document
# ("panel", <file>); a past one is "panel/<file>/history/<id>" with ("panel-history/<file>", <id>). On files these
# are today's paths under state_root(): <file>/events.jsonl, state.json, history/<id>.jsonl and history/<id>.json.
def _chat_log_path(stream: str) -> Path:
    _, fid, *rest = stream.split("/")
    return state_root() / fid / ("history/" + rest[1] + ".jsonl" if rest else "events.jsonl")


def _chat_logs() -> list[str]:
    root = state_root()
    return ([f"panel/{p.parent.name}" for p in root.glob("*/events.jsonl")] +
            [f"panel/{p.parent.parent.name}/history/{p.stem}" for p in root.glob("*/history/*.jsonl")])


fstore.log_files("panel/", path=_chat_log_path, names=_chat_logs)
fstore.files("panel", path=lambda ns, fid: state_root() / fid / "state.json")
fstore.files("panel-history/", path=lambda ns, cid: state_root() / ns.split("/", 1)[1] / "history" / f"{cid}.json",
             keys=lambda ns: sorted(p.stem for p in (state_root() / ns.split("/", 1)[1] / "history").glob("*.json")))


def claude_bin() -> str:
    return os.environ.get("FLYNN_CLAUDE_BIN", "claude")


def enabled() -> bool:
    try:
        return json.loads((flynn_home() / "features.json").read_text()).get("panel_chat", True) is not False
    except (OSError, ValueError):
        return True


def recorder_enabled() -> bool:
    try:
        return json.loads((flynn_home() / "features.json").read_text()).get("recorder", True) is not False
    except (OSError, ValueError):
        return True


def log(msg: str) -> None:
    try:
        d = flynn_home() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "panel_chat.log", "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


FIGMA_URL = re.compile(r"^https://(?:www\.)?figma\.com/(?:design|file)/([A-Za-z0-9]+)(?:/[^?#]*)?\?(?:[^#]*&)?node-id=([0-9]+(?:-|:|%3[Aa])[0-9]+)")


def figma_brief(url: str, key: str, node: str, page_name: str, file_id: str) -> str:
    """What flynn does for an import (the result must be real, editable elements, not pictures)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (page_name or "figma-page").lower()).strip("-")[:40] or "figma-page"
    return (
        f"Import this Figma page into flynn desk as real, editable elements: {url}\n"
        f"(file key {key}, node {re.sub(r'-|%3[Aa]', ':', node)}; target: a new page in the open canvas file {file_id}"
        + (f", named \"{page_name}\"" if page_name else "") + ").\n"
        "Steps:\n"
        "1. Use the Figma tools (get_metadata on that node) to check the link. It must be a PAGE (a canvas node, "
        "type CANVAS). If it is a frame or another node, stop and tell the user to copy the link of the page itself "
        "(right-click the page name in Figma's Pages list > Copy link). If Figma says the file is not found or "
        "access is denied, stop and tell the user his Figma account cannot open it.\n"
        f"2. Open a run: runs/<today>-figma-{slug}/. For every top-level frame on the page, read it with "
        "get_design_context and look at get_screenshot, then write it as a standalone HTML page "
        "(frames/NN-<name>/index.html): plain HTML and CSS (no external scripts), real text, exact sizes, colours, "
        "radii, fonts and positions, icons as inline SVG, images as files next to the page. The page body is the "
        "frame (same width and height, no margins).\n"
        "3. Render each page (`python3 adapters/html/render.py <page> --width <frame width>`) and compare it with "
        "the Figma screenshot; fix what differs.\n"
        "4. Write frames.json in the run folder: {\"figma\": {\"url\", \"file_key\", \"page_id\", \"page_name\"}, "
        "\"frames\": [{\"name\", \"x\", \"y\", \"w\", \"h\", \"html\"}]} with each frame's Figma position.\n"
        f"5. Build it: `python3 \"{FLYNN}/adapters/figma/import_page.py\" <run folder> --file {file_id}`"
        + (f" --page-name \"{page_name}\"" if page_name else "") + ".\n"
        "6. Tell the user the page name, how many frames and elements were built, and anything that did not come across "
        "(components, auto layout and prototypes are not imported; say if fonts were missing).")


def _desk_tools_context() -> str:
    try:
        import desk_tools
        return desk_tools.instructions()
    except Exception:  # noqa: BLE001
        return ""


def panel_context(file_id: str, file_name: str, mode: str = "penpot", system: str = "") -> str:
    return (
        "You are running inside the flynn tab of flynn desk, flynn's design app (a fork of Penpot). the user types to you here.\n"
        "- The canvas app is built on Penpot, and the code and tools still use that name (push.py, penpot.json, penpot_context), but the user's app is called flynn desk. When you talk to the user, call it flynn desk (or the canvas), never Penpot.\n"
        "- You cannot call AskUserQuestion here, so work in teammate mode: put every question in a "
        "NEEDS_ALIGNMENT block exactly as teammate-protocol.md describes. The tab shows it as a card with "
        "your recommended answers as buttons, and sends the user's answers back as an ALIGNED message. Finish "
        "with a RESULT or BLOCKED block.\n"
        f"- Open runs with --mode teammate --caller-cwd \"{FLYNN}\" (caller: the flynn panel).\n"
        "- In NEEDS_ALIGNMENT questions, give every question 2 to 4 concrete options on an indented line under it, "
        "`options: <a> | <b> | <c>`, with your recommended one among them; add an indented `multi: yes` line when "
        "the user may pick several. The tab shows them as choices plus a free-text field.\n"
        "- the user's messages may end with a <penpot_context> block: the Penpot page he is on and what he has selected "
        "(name, type, text, component, the top-level board, position on that board) and `selection_png`, a picture of the "
        "selection you can Read. Treat 'this', 'here', 'it' and 'these' as the selection. On a 'flynn · <run-id>' page, "
        "the board is one option of that run and positions match the page's layout at 1440px.\n"
        "- Write to the user in English by default, even if earlier messages were Persian; switch to Persian only when the user "
        "writes his message in Persian or asks for it. Design copy for Persian products stays Persian. The tab shows "
        "both directions correctly.\n"
        "- Keep chat text short and plain: the tab renders paragraphs, **bold**, `code`, headings and bullet or "
        "numbered lists. Use a list for three or more parallel items, and keep paragraphs to two or three sentences.\n"
        "- Your turn ends when you stop writing, and nothing of yours keeps running after it: no background work, no "
        "later report. Specialists (flynn-composer, the renderer, the critic, clu, quorra) run in the foreground here, "
        "so wait for their results and finish the step within this turn. Never tell the user you will pass something on "
        "later or that work is continuing; if something is left, say exactly what and that it needs another message. "
        "Never start servers or long-running commands (Studio and the chat server are already running).\n"
        "- Tool calls outside your safe list are refused automatically and shown to the user to allow; if one is "
        "refused, say what you needed and why, then stop.\n"
        f"- The Penpot file open next to this tab: \"{file_name or 'unknown'}\" (id {file_id}).\n"
        "- Build designs as usual: kit pages and theme pages in the run folder are the source. Always mention the run "
        "folder as runs/<run-id> in your reply, so the tab can link it.\n"
        + ("- the user is developing in PENPOT. After each of your turns the panel builds every changed page of your runs "
           "in this Penpot file, on a page named \"flynn · <run-id>\", one board per option. You do not need to do it "
           "yourself. Boards the user edited in Penpot are never overwritten; a new version is placed beside them.\n"
           if mode == "penpot" else
           "- the user is developing in STUDIO (http://127.0.0.1:8770). Designs stay in Studio; the tab has a button to build "
           "them in Penpot on request.\n")
        + "- Test your own work before the user sees it. Every turn that makes or changes a design runs this loop, at most "
          "three rounds, and finishes it within the turn:\n"
          "  1. Render each changed page (flynn-renderer) and run the mechanical checks (flynn-checker; "
          "scripts/lint_page.py for theme pages). Fix every failure.\n"
          "  2. Look at the full-size renders yourself, then have flynn-critic judge them against the contract and the "
          "rubric. Apply its blockers.\n"
          "  3. Call canvas_check: it builds the run on the canvas and compares each board with its page. Read the "
          "canvas and diff pictures it lists. Fix what is wrong in the page, or name it as a canvas builder limit.\n"
          "  4. Repeat from the step that found the problem until nothing real is left.\n"
          "  In your reply, say in one or two lines what you checked, what you fixed, and anything still wrong. Never "
          "call a design ready without having looked at its canvas picture.\n"
        + "- Everything happens in flynn desk: options, reviews, the user's picks and edits live on the canvas here. Flynn "
          "Studio is separate. Do not point the user to Studio or link it for review (freezing versions with studio.py snapshot is fine), unless he explicitly "
          "asks to put the designs in Studio; then start it (studio.py serve) and give him the link.\n"
        + f"- Before revising a design that was built in Penpot, run `python3 \"{FLYNN}/adapters/penpot/push.py\" pull \"{FLYNN}/runs/<run-id>\"` "
        "and look at <run>/penpot-pull/ (a PNG and the texts of every board, and which boards the user edited). Carry his "
        "Penpot edits into the page before you change anything else, and say which edits you carried over.\n"
        + libraries_context()
        + _desk_tools_context()
        + system_context(system, file_id)
        + letter_context(_letters().get(file_id))
    )


_lib_lock = threading.Lock()


def sync_libraries(ch) -> bool:
    """Bring every canvas library up to date (in the builder's browser), reporting in the chat."""
    if not _lib_lock.acquire(blocking=False):
        return False

    def work():
        try:
            sys.path.insert(0, str(FLYNN / "adapters" / "library"))
            import library
            libs = [l["system"] for l in library.libraries() if l.get("file")]
            ch.emit({"t": "library", "state": "syncing", "systems": libs})
            for system in libs:
                try:
                    r = library.sync(system)
                    t, c = r.get("tokens") or {}, r.get("components") or {}
                    ch.emit({"t": "library", "state": "synced", "name": r.get("name"),
                             "colors": (t.get("colors") or {}).get("made", 0) + (t.get("colors") or {}).get("updated", 0),
                             "components": c.get("components"), "skipped": c.get("skipped")})
                except Exception as e:  # noqa: BLE001
                    ch.emit({"t": "library", "state": "error", "name": system, "text": f"{type(e).__name__}: {e}"[:300]})
            log(f"{ch.file_id} libraries synced: {libs}")
        finally:
            _lib_lock.release()
    threading.Thread(target=work, daemon=True).start()
    return True


def change_card(c: dict) -> dict:
    """What the approval card shows: each op as old -> new."""
    items = []
    for op in c["ops"]:
        kind = op["op"]
        before = op.get("before")
        if kind == "token.set":
            items.append({"kind": "token", "name": op["name"], "before": (before or {}).get("value"), "after": op["value"],
                          "color": op["value"] if op.get("kind") == "color" else None, "new": before is None})
        elif kind == "token.remove":
            items.append({"kind": "token", "name": op["name"], "before": (before or {}).get("value"), "after": None, "removed": True})
        elif kind == "component.set":
            items.append({"kind": "component", "name": op["name"], "fields": sorted(op.get("fields") or {}),
                          "after": (op.get("fields") or {}).get("description"), "new": before is None})
        elif kind == "component.remove":
            items.append({"kind": "component", "name": op["name"], "removed": True})
        elif kind == "component.sample":
            items.append({"kind": "sample", "name": op["name"], "after": op.get("variant") or "sample", "new": True})
        elif kind == "recording.apply":
            items.append({"kind": "recording", "name": Path(op["session"]).name})
    return {"id": c["id"], "system": c["system"], "title": c["title"], "why": c.get("why", ""), "source": c.get("source", "chat"),
            "items": items}


def systems_list(file_id: str = "") -> list[dict]:
    """What the user can pick for a conversation. In the Designs space: one design system per library in flynn desk.
    In the Letters space: the document themes (editorial)."""
    try:
        if file_id and _letters().get(file_id):
            return _letters().themes()
    except Exception as e:  # noqa: BLE001
        log(f"themes list failed: {e}")
    out = []
    try:
        sys.path.insert(0, str(FLYNN / "adapters" / "library"))
        import library
        for l in library.libraries():
            if not l.get("file"):
                continue
            name = (l.get("name") or l["system"]).removesuffix(" library")
            out.append({"system": l["system"], "label": name, "library": l.get("name"), "file": l["file"],
                        "components": len(l.get("components") or {}), "synced": l.get("synced")})
    except Exception as e:  # noqa: BLE001
        log(f"systems list failed: {e}")
    return sorted(out, key=lambda v: (v["system"].startswith("rec:"), v["label"]))


def system_context(system: str, file_id: str) -> str:
    """The design system the user picked for this conversation, and how flynn maintains systems."""
    rule = (f"- Design systems change only through the user's approval. When a recording, or something the user says, means a "
            f"system should change (a new or changed token, a component's description, variants or usage, a new or "
            f"replaced component sample, a component to remove), write the change as JSON and propose it: "
            f"`python3 \"{FLYNN}/adapters/library/changes.py\" propose --file <change.json>` (the format is in that "
            f"file's docstring; set \"file\": \"{file_id}\"). The tab shows the user an approval card; on approval the store "
            "and the library update. Never edit a system's store rows or library directly, and say in your reply that "
            "you proposed a change.\n")
    if not system:
        return rule + ("- No design system is selected for this conversation. Ask which system the design belongs to "
                       "when it matters, or design without one (for documents, use the HTML adapter's theme).\n")
    label = next((v["label"] for v in systems_list(file_id) if v["system"] == system), system)
    return rule + (f"- the user selected the design system for this conversation: {label} (store system \"{system}\"). Design "
                   "only from it: its components, tokens and patterns in the store, its guide and kit if it has them "
                   "(systems/<system>/GUIDE.md, the Admin Panel kit), and its canvas library. Do not borrow from "
                   "other systems; if something is missing, say so and propose a change.\n")


def _letters():
    sys.path.insert(0, str(FLYNN / "adapters" / "letters"))
    import letters
    return letters


EXPORTS = {
    "pdf": "a PDF (use the pdfsmith skill; for a deck, one page per slide: render.py --slides)",
    "png": "PNG images: one per section, or one per slide for a deck (render.py; --pad 0 for a newsletter)",
    "pptx": "an editable PowerPoint (adapters/pptx/pptx_export.py from the deck page; check it with --preview)",
    "email": "an Outlook draft (.eml) from the section PNGs (adapters/html/email_pack.py; it never sends)",
    "canva": "a Canva plan (adapters/canva/canva_plan.py) to carry out with read-design and edit-design, showing the user the preview before committing",
}
EXPORTS_FOR = {"deck": ["pdf", "pptx", "png", "canva"], "newsletter": ["email", "png", "pdf"],
               "release-note": ["pdf", "png", "email"], "report": ["pdf", "png", "pptx"]}


def _orgs():
    sys.path.insert(0, str(FLYNN / "adapters" / "orgs"))
    import orgs
    return orgs


def letter_context(meta: dict | None) -> str:
    if not meta:
        return ""
    L = _letters()
    t = L.TYPES.get(meta["type"], {})
    return (f"- This file is a LETTER in flynn desk's Letters space: a {t.get('label', meta['type'])} titled "
            f"\"{meta['title']}\" ({t.get('hint', '')}). Its master is the run {meta['run']} (page/index.html, built from "
            f"the {t.get('template')} template, theme {meta.get('theme', 'editorial')}). Work on that page with the HTML "
            "adapter's rules (adapters/html/README.md, components.md; charts with charts.py and dataviz.md), not product UI. "
            "After each turn the page is rebuilt on the canvas (decks: one board per slide). Before you change the page, "
            f"run `python3 \"{FLYNN}/adapters/penpot/push.py\" pull \"{meta['run']}\"` and carry the user's canvas edits into it "
            "first: the page is the master, the canvas is where the user edits.\n")


def letter_export_brief(meta: dict, fmt: str) -> str:
    return (f"Export this letter ({meta['title']}) as {EXPORTS[fmt]}.\n"
            f"1. Pull the user's canvas edits: `python3 \"{FLYNN}/adapters/penpot/push.py\" pull \"{meta['run']}\"`, and carry "
            "every edit into page/index.html (texts, order, removed or added parts). Say which edits you carried over.\n"
            "2. Render and check the page (render.py); fix any failed check.\n"
            f"3. Produce the export into {meta['run']}/exports/ and give the user the file paths. Do not send or publish anything.")


def libraries_context() -> str:
    """Which design system libraries exist on the canvas, so flynn uses and maintains them."""
    try:
        sys.path.insert(0, str(FLYNN / "adapters" / "library"))
        import library  # noqa: F401                    # owns the "libraries" namespace and where it lives on files
        libs = [*fstore.list("libraries").values()]
    except (OSError, ValueError, ImportError):
        libs = []
    libs = [l for l in libs if l.get("file")]
    if not libs:
        return ""
    names = "; ".join(f"{l.get('name')} (system {l['system']}, {len(l.get('components') or {})} components, last synced "
                      f"{l.get('synced', 'never')})" for l in libs)
    return ("- Design system libraries on the canvas (shared, under Libraries): " + names + ". When a Admin Panel "
            "design is built on the canvas, every element that has an exactly matching library variant is placed as a "
            "real instance of the library component (the rest is drawn as shapes), and the library is linked to the "
            "file. After the store changes for a system (new or changed tokens or components), bring its library up "
            f"to date: `python3 \"{FLYNN}/adapters/library/library.py\" sync <system>`. It runs in flynn's own "
            "builder browser, never in the user's tab.\n")


class Channel:
    def __init__(self, file_id: str):
        self.file_id = file_id
        self.dir = state_root() / file_id               # uploads stay files here, whatever the store
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stream = f"panel/{file_id}"
        self.hist_ns = f"panel-history/{file_id}"
        self.cond = threading.Condition()
        self.events: list[dict] = []
        self.proc: subprocess.Popen | None = None
        self.running = False
        st = fstore.get("panel", file_id, None)
        st = st if isinstance(st, dict) else {}
        self.session: str | None = st.get("session")
        self.extra_allow: list[str] = st.get("extra_allow", [])
        self.file_name: str = st.get("file_name", "")
        self.mode: str = "penpot"          # the Canvas/Studio switch is gone: flynn desk always builds on the canvas
        self.pushed: dict = st.get("pushed", {})
        self.runs: list[str] = st.get("runs", [])
        self.team: str | None = st.get("team")
        self.system: str = st.get("system", "")              # the design system this conversation designs from
        self.announced: list[str] = st.get("announced", [])  # design system changes already shown as cards
        self.events = fstore.read(self.stream)
        self.repair()

    def repair(self) -> None:
        """A server restart can cut a turn or a build off before it says it ended; the tab would then show flynn
        working forever, with Stop in place of Send. Close what was left open, with a plain note (2026-10-06)."""
        last_user = max((i for i, e in enumerate(self.events) if e.get("t") == "user"), default=-1)
        last_end = max((i for i, e in enumerate(self.events) if e.get("t") == "turn_end"), default=-1)
        builds: dict[str, dict] = {}
        for e in self.events:
            if e.get("t") == "penpot" and e.get("build"):
                builds[e["build"]] = e
        for b, e in builds.items():
            if e.get("state") in ("building", "progress"):
                self.emit({"t": "penpot", "state": "error", "run": e.get("run"), "build": b,
                           "text": "The build stopped when the chat server restarted. Press Try again."})
        if last_user > last_end:
            self.emit({"t": "error", "text": "The chat server restarted while flynn was working, so this turn was cut off. "
                                             "Send your message again, or say continue."})
            self.emit({"t": "turn_end", "ok": False, "cost": None, "interrupted": True})
            log(f"{self.file_id} closed a turn cut off by a restart")

    def save_state(self) -> None:
        fstore.put("panel", self.file_id,
                   {"session": self.session, "extra_allow": self.extra_allow, "file_name": self.file_name, "mode": self.mode,
                    "pushed": self.pushed, "runs": self.runs, "team": self.team, "system": self.system,
                    "announced": self.announced[-200:]})

    def emit(self, ev: dict) -> None:
        if ev.get("t") in ("turn_end", "user"):
            ev = {**ev, "at": _dt.datetime.now().isoformat(timespec="seconds")}
        with self.cond:
            self.events.append(ev)
            fstore.append(self.stream, ev)
            self.cond.notify_all()

    def start_turn(self, text: str, shown: str | None = None, context: bool = True, files: list[dict] | None = None,
                   rules: list[str] | None = None) -> bool:
        with self.cond:
            if self.running:
                return False
            self.running = True
        self.emit({"t": "user", "text": shown if shown is not None else text, **({"files": [f["name"] for f in files]} if files else {}),
                   **({"rules": rules} if rules else {})})
        if files:
            text += "\n\nAli attached these files (they stay on this computer; read them with Read, which shows images and PDFs):\n" + \
                "\n".join(f"- {f['path']} ({f['type'] or 'file'}, {f['size'] // 1024 or 1} KB)" for f in files)

        def go():
            prompt = text
            if context:
                try:
                    ctx = context_fn(self)
                except Exception as e:  # noqa: BLE001 - never block a message on context
                    ctx = None
                    log(f"{self.file_id} context failed: {e}")
                if ctx and not getattr(self, "send_selection", True):
                    ctx = {k: v for k, v in ctx.items() if k not in ("selection", "selection_png", "more")}
                    self.send_selection = True
                if ctx:
                    self.emit({"t": "context", "page": ctx.get("page"), "selected": len(ctx.get("selection") or []),
                               "names": [s.get("name") for s in (ctx.get("selection") or [])][:4]})
                prompt = with_context(text, ctx)
            self._run(prompt)
        threading.Thread(target=go, daemon=True).start()
        return True

    def _turn(self, text: str):
        import engines
        return engines.Turn(prompt=text, session=self.session, system=panel_context(self.file_id, self.file_name, self.mode, self.system),
                            allow=[*BASE_ALLOW, *self.extra_allow], deny=list(DENY), file_id=self.file_id, cwd=str(FLYNN),
                            claude=claude_bin(), user=getattr(self, "last_user", ""))

    def _cmd(self, text: str) -> list[str]:
        import engines
        return engines.CliEngine().cmd(self._turn(text))

    def _run(self, text: str) -> None:
        import engines
        eng = engines.choose()
        self.engine = eng
        self.asked = False
        log(f"{self.file_id} turn start engine={eng.name} session={self.session}")

        def emit(ev):
            if ev["t"] == "session":
                if ev.get("id") and ev["id"] != self.session:
                    self.session = ev["id"]
                    self.save_state()
                return
            if ev["t"] == "card" and ev["card"].get("kind") == "needs_alignment" and self.asked:
                return                                     # flynn used ask_user and also wrote the block: show it once
            self.emit(ev)
        try:
            eng.run(self._turn(text), emit)
            log(f"{self.file_id} turn end engine={eng.name}")
            self.note_runs()
            self.note_changes()
            if self.mode == "penpot":
                self.sync(force=False)
        except Exception as e:  # noqa: BLE001
            self.emit({"t": "error", "text": f"{type(e).__name__}: {e}"[:400]})
            self.emit({"t": "turn_end", "ok": False, "cost": None})
        finally:
            with self.cond:
                self.running = False
                self.proc = None
                self.engine = None
                self.cond.notify_all()

    def note_changes(self) -> None:
        """Design system changes flynn proposed for this conversation become approval cards."""
        try:
            sys.path.insert(0, str(FLYNN / "adapters" / "library"))
            import changes
            for c in changes.changes("pending"):
                if c["id"] in self.announced or (c.get("file") and c["file"] != self.file_id):
                    continue
                self.announced.append(c["id"])
                self.emit({"t": "ds-change", "change": change_card(c)})
            self.save_state()
        except Exception as e:  # noqa: BLE001
            log(f"{self.file_id} changes scan failed: {e}")

    def note_runs(self) -> None:
        """Runs mentioned in this conversation (flynn's text, tool lines and cards) that have pages."""
        text = json.dumps(self.events[-400:], ensure_ascii=False)
        for name in dict.fromkeys(RUN_REF.findall(text)):
            run = RUNS_DIR / name
            if name not in self.runs and run.is_dir() and run_pages(run):
                self.runs.append(name)
                self.emit({"t": "run", "run": name, "studio": f"{STUDIO}/run/{name}"})
        self.save_state()

    def sync(self, force: bool = False, only: str | None = None) -> None:
        """Build the conversation's runs in the open Penpot file when their pages changed (or always, if forced)."""
        for name in list(self.runs):
            if only and name != only:
                continue
            run = RUNS_DIR / name
            if not run_pages(run):
                continue
            m = pages_mtime(run)
            if not force and self.pushed.get(name, 0) >= m:
                continue
            build = f"{name}@{time.time():.0f}"            # one card per build, updated in place
            started = time.time()
            self.emit({"t": "penpot", "state": "building", "run": name, "build": build, "ts": started})
            last = {"stage": "", "at": 0.0}

            def progress(stage, text, pct, build=build):
                now = time.time()
                if stage == last["stage"] and now - last["at"] < 0.8:      # at most about one update a second
                    return
                last.update(stage=stage, at=now)
                self.emit({"t": "penpot", "state": "progress", "run": name, "build": build, "stage": stage,
                           "text": text, "pct": round(pct, 1), "ts": now})
            try:
                res = push_run_fn(run, self.file_id, team=self.team, progress=progress)
                self.pushed[name] = m
                self.save_state()
                self.emit({"t": "penpot", "state": "done", "run": name, "build": build, "secs": round(time.time() - started),
                           "page": res.get("pageName"), "link": res.get("link"),
                           "boards": list((res.get("boards") or {}).keys()), "kept": list((res.get("kept_edited") or {}).keys()),
                           "errors": len(res.get("errors") or [])})
                log(f"{self.file_id} pushed {name}")
            except Exception as e:  # noqa: BLE001 - shown to the user, never fatal
                msg = str(e) if isinstance(e, PenpotError) else f"{type(e).__name__}: {e}"
                self.emit({"t": "penpot", "state": "error", "run": name, "build": build, "text": msg[:400],
                           "secs": round(time.time() - started)})
                log(f"{self.file_id} push {name} failed: {msg[:200]}")

    def push_now(self, only: str | None = None) -> bool:
        with self.cond:
            if self.running:
                return False
            self.running = True

        def work():
            try:
                self.note_runs()
                self.sync(force=True, only=only)
            finally:
                with self.cond:
                    self.running = False
                    self.cond.notify_all()
        threading.Thread(target=work, daemon=True).start()
        return True

    def stop(self) -> None:
        eng = getattr(self, "engine", None)
        if eng:
            eng.stop()
            log(f"{self.file_id} stopped by the user")

    # ---------- conversation history ----------
    # The current conversation is self.stream + ("panel", file). Past ones are the logs <stream>/history/<id> with
    # (hist_ns, <id>) = {id, title, started, updated, messages, session, system, runs}, so opening one brings back
    # its events and resumes its Claude session.
    def _hist(self, cid: str) -> str:
        return f"{self.stream}/history/{cid}"

    def _past(self) -> list[str]:
        if not fstore.on_postgres():
            d = self.dir / "history"
            for old in self.dir.glob("events-*.jsonl"):      # file archives from before the history list
                stamp = old.stem.removeprefix("events-")
                d.mkdir(exist_ok=True)
                if not (d / f"{stamp}.jsonl").exists():
                    old.rename(d / f"{stamp}.jsonl")
        return [s.rsplit("/", 1)[1] for s in fstore.streams(self.stream + "/history/")]

    @staticmethod
    def summary(events: list[dict]) -> dict:
        users = [e for e in events if e.get("t") == "user"]
        first = next((e.get("text") or "" for e in users if not str(e.get("text", "")).startswith("ALIGNED")), "")
        title = re.sub(r"\s+", " ", first).strip()
        return {"title": (title[:90] + "…") if len(title) > 90 else (title or "Untitled conversation"), "messages": len(users)}

    def archive(self) -> str | None:
        """Move the current conversation into history (nothing happens when it is empty)."""
        if not any(e.get("t") == "user" for e in self.events):
            return None
        taken = set(self._past())
        cid = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        while cid in taken:
            cid += "x"
        if not fstore.move(self.stream, self._hist(cid)):
            for e in self.events:
                fstore.append(self._hist(cid), e)
        started = getattr(self, "started", None) or cid
        meta = {"id": cid, **self.summary(self.events), "started": started, "updated": _dt.datetime.now().isoformat(timespec="seconds"),
                "session": self.session, "system": self.system, "runs": self.runs}
        fstore.put(self.hist_ns, cid, meta)
        return cid

    def conversations(self) -> list[dict]:
        out = []
        for cid in self._past():
            m = fstore.get(self.hist_ns, cid)
            if not isinstance(m, dict):
                m = {**self.summary(fstore.read(self._hist(cid))), "session": None}
            if "updated" not in m:
                m["updated"] = (fstore.last_at(self._hist(cid)) or _dt.datetime.now()).isoformat(timespec="seconds")
            out.append({"id": cid, "title": m.get("title"), "messages": m.get("messages", 0), "updated": m["updated"],
                        "resumable": bool(m.get("session"))})
        out.sort(key=lambda x: x["updated"], reverse=True)
        cur = None
        if any(e.get("t") == "user" for e in self.events):
            cur = {"id": "current", **self.summary(self.events), "current": True, "resumable": bool(self.session),
                   "updated": (fstore.last_at(self.stream) or _dt.datetime.now()).isoformat(timespec="seconds")}
        return ([cur] if cur else []) + out

    def _clear(self) -> None:
        self.events = []
        self.session = None
        self.extra_allow = []
        self.runs = []
        self.announced = []

    def open_conversation(self, cid: str) -> bool:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}x*", cid or ""):
            raise ValueError("bad conversation id")
        if cid not in self._past():
            raise ValueError("that conversation is gone")
        with self.cond:
            if self.running:
                return False
        self.archive()
        meta = fstore.get(self.hist_ns, cid)
        meta = meta if isinstance(meta, dict) else {}
        with self.cond:
            self._clear()
            fstore.drop(self.stream)             # a conversation without messages is not archived; it gives way
            fstore.move(self._hist(cid), self.stream)
            fstore.delete(self.hist_ns, cid)
            self.events = fstore.read(self.stream)
            self.session = meta.get("session")
            self.runs = meta.get("runs") or []
            self.system = meta.get("system", self.system)
            self.started = meta.get("started")
            self.announced = [e.get("change", {}).get("id") for e in self.events if e.get("t") == "ds-change"]
            self.save_state()
            self.cond.notify_all()
        log(f"{self.file_id} opened conversation {cid}")
        return True

    def delete_conversation(self, cid: str) -> None:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}x*", cid or ""):
            raise ValueError("bad conversation id")
        fstore.drop(self._hist(cid))
        fstore.delete(self.hist_ns, cid)
        log(f"{self.file_id} deleted conversation {cid}")

    def reset(self) -> None:
        self.stop()
        self.archive()
        with self.cond:
            self._clear()
            self.started = None
            self.save_state()
            self.cond.notify_all()


UPLOAD_MAX = 25 * 1024 * 1024


def save_upload(ch: "Channel", name: str, mime: str, data: str) -> dict:
    """Keep a file the user attached in this conversation's folder (local only), for his next message."""
    import base64
    import binascii
    try:
        raw = base64.b64decode(data.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError("the file could not be read") from e
    if not raw:
        raise ValueError("the file is empty")
    if len(raw) > UPLOAD_MAX:
        raise ValueError("files up to 25 MB")
    safe = re.sub(r"[^\w.\-\u0600-\u06FF ]+", "_", Path(name).name).strip() or "file"
    d = ch.dir / "uploads"
    d.mkdir(exist_ok=True)
    f = d / f"{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe[:80]}"
    f.write_bytes(raw)
    log(f"{ch.file_id} upload {f.name} ({len(raw)} bytes)")
    return {"id": f.name, "name": safe, "type": mime[:80], "size": len(raw)}


def uploads_for(ch: "Channel", ids) -> list[dict]:
    import mimetypes
    out = []
    for i in ids if isinstance(ids, list) else []:
        f = ch.dir / "uploads" / Path(str(i)).name
        if f.is_file():
            out.append({"path": str(f), "name": f.name.split("-", 2)[-1], "size": f.stat().st_size,
                        "type": mimetypes.guess_type(f.name)[0] or ""})
    return out[:10]


def context_rows(ch: "Channel") -> list[list[str]]:
    import engines
    sel = selection_fn(ch) if os.environ.get("FLYNN_PANEL_CONTEXT", "1") != "0" else {"count": 0}
    meta = _letters().get(ch.file_id)
    ctx = panel_context(ch.file_id, ch.file_name, ch.mode, ch.system)
    users = sum(1 for e in ch.events if e.get("t") == "user")
    rows = [["File", ch.file_name or ch.file_id],
            ["Kind", f"Letter · {meta['type']}" if meta else "Design"],
            ["Selection", f"{sel.get('count', 0)} selected" + (f" on {sel['page']}" if sel.get("page") else "") + (" (left out)" if not getattr(ch, "send_selection", True) else "")],
            ["Design system", (ch.system or "none") + ("" if not ch.system else "")],
            ["AI", engines.describe()["running"]],
            ["Conversation", f"{users} message{'s' if users != 1 else ''}" + (", continues the same session" if ch.session else ", new session")],
            ["Runs", ", ".join(ch.runs[-4:]) or "none yet"],
            ["flynn desk context", f"about {len(ctx) // 4:,} tokens of instructions with every message"]]
    return rows


def usage_rows(ch: "Channel") -> list[list[str]]:
    def cost(evs):
        return sum(float(e.get("cost") or 0) for e in evs if e.get("t") == "turn_end"), sum(1 for e in evs if e.get("t") == "turn_end")
    c, n = cost(ch.events)
    today = _dt.date.today().isoformat()
    tc = tn = 0.0
    midnight = _dt.datetime.combine(_dt.date.today(), _dt.time())
    for s in fstore.streams("panel/", active_since=midnight):    # every chat written to today, current or past
        evs = [e for e in fstore.read(s) if e.get("t") == "turn_end" and str(e.get("at", "")).startswith(today)]
        a, b = cost(evs)
        tc, tn = tc + a, tn + b
    return [["This conversation", f"${c:.2f} over {n} turn{'s' if n != 1 else ''}"],
            ["Today, all files", f"${tc:.2f} over {int(tn)} turn{'s' if tn != 1 else ''} (turns since the counter started)"],
            ["Plan limits", "Your Claude plan's limits are at claude.ai/settings/usage (Claude does not report them to flynn desk)"]]


CHANNELS: dict[str, Channel] = {}
_lock = threading.Lock()


def channel(file_id: str) -> Channel:
    with _lock:
        if file_id not in CHANNELS:
            CHANNELS[file_id] = Channel(file_id)
        return CHANNELS[file_id]


class Handler(BaseHTTPRequestHandler):
    server_version = "flynn-panel/1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _me(self) -> dict | None:
        """Who is calling: the signed-in person (Penpot session) or their flynn agent (token). On the user's Mac, where
        sign-in is not required, the local owner when FLYNN_OWNER_EMAIL is set."""
        if not hasattr(self, "_who"):
            import session_auth
            self._who = session_auth.identify(self.headers) or (None if session_auth.required() else session_auth.local_owner())
        return self._who

    def _gate(self, path: str) -> bool:
        """On the team server every call but the health check needs a signed-in caller. True when refused."""
        import session_auth
        if session_auth.required() and path != "/api/health" and not self._me():
            self._json({"error": "sign in first"}, 401)
            return True
        return False

    def _file(self, value) -> str | None:
        return value if isinstance(value, str) and FILE_ID.match(value) else None

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.removeprefix("/flynn")
        if path.startswith("/agent/"):
            import agent_hub
            return agent_hub.handle(self, "GET", path, q, self._me())
        if self._gate(path):
            return
        if path == "/api/health":
            return self._json({"ok": True, "enabled": enabled()})
        if path == "/api/me":
            me = self._me()
            if not me:
                return self._json({"error": "nobody is signed in"}, 400)
            import accounts_api
            return self._json({**accounts_api.me(me["email"]), "name": me.get("name")})
        if path == "/api/settings":
            import engines
            return self._json(engines.describe())
        if path == "/api/spaces":
            try:
                L = _letters()
                return self._json({"letters": L.letter_ids(), "types": [{"type": k, **v} for k, v in L.TYPES.items()]})
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:200]}, 500)
        if path == "/api/orgs":
            me = (self._me() or {}).get("email") or (q.get("me") or [""])[0]
            try:
                O = _orgs()
                return self._json(O.view(O.load(), O.owner_rpc(), me))
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"{type(e).__name__}: {e}"[:200]}, 500)
        fid = self._file((q.get("file") or [None])[0])
        if not fid:
            return self._json({"error": "bad file id"}, 400)
        ch = channel(fid)
        if path == "/api/record/state":
            return self._json({**record_api.state(fid), "last": record_api.last_settings(fid), "enabled": recorder_enabled()})
        if path == "/api/commands":
            import commands
            return self._json({"commands": commands.listing("letters" if _letters().get(fid) else "designs")})
        if path == "/api/conversations":
            return self._json({"conversations": ch.conversations(), "running": ch.running})
        if path == "/api/systems":
            meta = _letters().get(fid)
            return self._json({"systems": systems_list(fid), "system": ch.system or ((meta or {}).get("theme") or ""),
                               "letter": meta, "exports": EXPORTS_FOR.get((meta or {}).get("type"), [])})
        if path == "/api/selection":
            return self._json(selection_fn(ch))
        if path == "/api/history":
            ch.note_changes()                      # pending design system changes show as cards on load
            meta = _letters().get(fid)
            if meta and not meta.get("built"):     # a new letter: build its template on the canvas once
                meta["built"] = True
                _letters().save(meta)

                def first_build(ch=ch, run=Path(meta["run"]).name, fid=fid):
                    import time as _t
                    from bridge import Bridge
                    for _ in range(30):            # the connector reaches the new file a moment after it opens
                        try:
                            if Bridge(timeout=10).current().get("file") == fid:
                                break
                        except Exception:  # noqa: BLE001
                            pass
                        _t.sleep(1.5)
                    ch.push_now(only=run)
                threading.Thread(target=first_build, daemon=True).start()
            return self._json({"events": ch.events, "running": ch.running, "session": ch.session, "mode": ch.mode, "runs": ch.runs,
                               "file_name": ch.file_name, "system": ch.system})
        if path == "/api/events":
            since = int((q.get("since") or ["0"])[0] or 0)
            last = self.headers.get("Last-Event-ID")
            if last and last.isdigit():
                since = max(since, int(last) + 1)
            return self._sse(ch, since)
        if path == "/api/ws":
            return self._ws(ch, int((q.get("since") or ["0"])[0] or 0))
        return self._json({"error": "not found"}, 404)

    def _ws(self, ch: Channel, since: int):
        """The panel's live connection (one WebSocket per open panel). Down: the file's chat events (as /api/events),
        a heartbeat with whether flynn is running, and the canvas selection whenever it changes (the server watches
        it while the panel is visible, instead of the panel asking every second). Up: the panel's actions,
        {"type": "call", "id", "method", "path", "body"}, run through the same /api routes as plain requests (with
        the caller's own sign-in), answered with {"type": "reply", "id", "status", "body"}. The panel falls back to
        /api/events and plain requests when a network can't carry WebSockets."""
        import urllib.request
        import wsproto
        ws = wsproto.accept(self)
        if ws is None:
            return self._json({"error": "websocket upgrade expected"}, 400)
        state = {"visible": True, "closed": False}
        auth = {k: v for k in ("Cookie", "Authorization") if (v := self.headers.get(k))}
        port = self.server.server_address[1]

        def pump():
            i = max(0, since)
            while not state["closed"]:
                with ch.cond:
                    if i >= len(ch.events):
                        ch.cond.wait(timeout=15)
                    batch = ch.events[i:]
                    running = ch.running
                try:
                    if batch:
                        for ev in batch:
                            ws.send_json({"type": "event", "id": i, "ev": ev})
                            i += 1
                    else:
                        ws.send_json({"type": "ping", "running": bool(running)})
                except wsproto.Closed:
                    return

        def watch_selection():
            last = None
            while not state["closed"]:
                if state["visible"]:
                    try:
                        sel = selection_fn(ch)
                    except Exception:  # noqa: BLE001 - no canvas connected yet
                        sel = {"count": 0}
                    key = json.dumps(sel, sort_keys=True, default=str)
                    if key != last:
                        last = key
                        try:
                            ws.send_json({"type": "selection", "sel": sel})
                        except wsproto.Closed:
                            return
                time.sleep(1.0 if state["visible"] else 3.0)

        def call(m: dict):
            path = str(m.get("path") or "")
            method = "GET" if m.get("method") == "GET" else "POST"
            if not path.startswith("/api/") or ".." in path or path.startswith(("/api/ws", "/api/events")):
                status, body = 400, {"error": "not an /api route"}
            else:
                data = None if method == "GET" else json.dumps(m.get("body") or {}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/flynn{path}", data=data, method=method,
                                             headers={"Content-Type": "application/json", **auth})
                try:
                    with urllib.request.urlopen(req, timeout=600) as r:
                        status, raw = r.status, r.read()
                except urllib.error.HTTPError as e:
                    status, raw = e.code, e.read()
                except OSError as e:
                    status, raw = 502, json.dumps({"error": str(e)[:200]}).encode()
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"error": "bad reply"}
            try:
                ws.send_json({"type": "reply", "id": m.get("id"), "status": status, "body": body})
            except wsproto.Closed:
                pass

        threading.Thread(target=pump, daemon=True).start()
        threading.Thread(target=watch_selection, daemon=True).start()
        try:
            ws.settimeout(120)                      # the panel sends its visibility every 30s at least
            while True:
                m = ws.recv_json()
                kind = m.get("type") if isinstance(m, dict) else None
                if kind == "visible":
                    state["visible"] = bool(m.get("on"))
                elif kind == "call":
                    threading.Thread(target=call, args=(m,), daemon=True).start()
        except (wsproto.Closed, OSError, ValueError):
            pass
        finally:
            state["closed"] = True
            ws.close()
            with ch.cond:
                ch.cond.notify_all()

    def _sse(self, ch: Channel, since: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        i = max(0, since)
        try:
            while True:
                with ch.cond:
                    if i >= len(ch.events):
                        ch.cond.wait(timeout=15)
                    batch = ch.events[i:]
                    running = ch.running
                if batch:
                    for ev in batch:
                        self.wfile.write(f"id: {i}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        i += 1
                else:
                    # a named event (not stored): the panel knows the server is alive and whether flynn is still running
                    self.wfile.write(f"event: ping\ndata: {json.dumps({'running': bool(running)})}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        path = urlparse(self.path).path.removeprefix("/flynn")
        if path.startswith("/agent/"):
            import agent_hub
            return agent_hub.handle(self, "POST", path, {}, self._me())
        if self._gate(path):
            return
        b = self._body()
        if path == "/api/me/password-changed":
            me = self._me()
            if not me or me.get("via") == "agent":
                return self._json({"error": "sign in first"}, 401)
            import accounts_api
            accounts_api.password_changed(me["email"])
            return self._json({"ok": True})
        if path == "/api/settings":
            import engines
            import session_auth
            if session_auth.required():
                return self._json({"error": "on the team server flynn always runs on your own computer"}, 403)
            eng = b.get("engine")
            if eng not in engines.ENGINES:
                return self._json({"error": "engine is local, cli or api"}, 400)
            engines.save_settings(engine=eng)
            log(f"engine set to {eng}")
            return self._json(engines.describe())
        if path == "/api/orgs":
            O = _orgs()
            me = (self._me() or {}).get("email") or str(b.get("me") or "").strip().lower()
            if not me:
                return self._json({"error": "who is acting is unknown; reload the dashboard"}, 400)
            try:
                r = O.act(str(b.get("action") or ""), b, me)
            except O.OrgError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                log(f"orgs {b.get('action')} failed: {e}")
                return self._json({"error": f"{type(e).__name__}: {e}"[:200]}, 500)
            if r.pop("resources", False):
                org_id = r["org"]

                def share():
                    try:
                        with _lib_lock:                  # one builder browser: never two library syncs at once
                            O.share_resources(O.find(O.load(), org_id))
                    except Exception as e:  # noqa: BLE001
                        log(f"org resources failed: {e}")
                threading.Thread(target=share, daemon=True).start()
                r["sharing"] = True
            return self._json(r)
        if path == "/api/letters/new":
            try:
                team = b.get("team") if isinstance(b.get("team"), str) and FILE_ID.match(b["team"]) else None
                proj = b.get("project") if isinstance(b.get("project"), str) and FILE_ID.match(b["project"]) else None
                meta = _letters().new(str(b.get("type") or ""), str(b.get("title") or "")[:120], project=proj, team=team)
            except (ValueError, Exception) as e:  # noqa: BLE001
                return self._json({"error": str(e)[:300]}, 400)
            ch = channel(meta["file"])
            ch.runs = [Path(meta["run"]).name]
            ch.file_name = meta["title"]
            ch.system = meta.get("theme", "")
            ch.save_state()
            log(f"letter created {meta['file']} {meta['type']} {meta['run']}")
            return self._json({"ok": True, **meta})
        fid = self._file(b.get("file"))
        if fid and self._me() and self._me().get("via") != "agent":
            channel(fid).last_user = self._me()["email"]        # the remote engine routes this person's turns
        if not fid:
            return self._json({"error": "bad file id"}, 400)
        ch = channel(fid)
        if path in ("/api/send", "/api/answer", "/api/allow") and not enabled():
            return self._json({"error": "panel chat is switched off (~/.flynn/features.json)"}, 503)
        if path == "/api/send":
            text = (b.get("text") or "").strip()
            if not text and b.get("attachments"):
                text = "Here are some files."
            if not text:
                return self._json({"error": "empty message"}, 400)
            if b.get("file_name"):
                ch.file_name = str(b["file_name"])[:200]
            if isinstance(b.get("team"), str) and FILE_ID.match(b["team"]):
                ch.team = b["team"]
                try:
                    fstore.put("settings", "penpot", {"team": ch.team})       # push.py reads the last team
                except Exception:  # noqa: BLE001 - a convenience; never blocks the message
                    pass
            ch.save_state()
            ch.send_selection = b.get("selection", True) is not False      # the user can leave the selection out
            files = uploads_for(ch, b.get("attachments"))
            ok = ch.start_turn(text, files=files)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working on the last message"}, 200 if ok else 409)
        if path == "/api/answer":
            answers = [a for a in (b.get("answers") or []) if isinstance(a, dict) and str(a.get("answer", "")).strip()]
            if not answers:
                return self._json({"error": "no answers"}, 400)
            msg = stream.aligned_message([{"n": int(a["n"]), "answer": str(a["answer"]).strip()} for a in answers],
                                         note=str(b.get("note") or ""))
            ok = ch.start_turn(msg)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working"}, 200 if ok else 409)
        if path == "/api/allow":
            rules = [r for r in (b.get("rules") or []) if isinstance(r, str) and r and PROTECTED_CODE not in r]
            for r in rules:
                if r not in ch.extra_allow:
                    ch.extra_allow.append(r)
            ch.save_state()
            log(f"{fid} allowed {rules}")
            ok = ch.start_turn("the user allowed: " + "; ".join(rules) + ". Please continue where you stopped.",
                               shown="Allowed: " + ", ".join(rules), context=False, rules=rules)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working"}, 200 if ok else 409)
        if path == "/api/mode":
            if b.get("mode") not in ("penpot", "studio"):
                return self._json({"error": "mode is penpot or studio"}, 400)
            ch.mode = b["mode"]
            ch.save_state()
            ch.emit({"t": "mode", "mode": ch.mode})
            return self._json({"ok": True, "mode": ch.mode})
        if path == "/api/push":
            ok = ch.push_now(only=b.get("run") if isinstance(b.get("run"), str) else None)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working"}, 200 if ok else 409)
        if path.startswith("/api/record/"):
            return self._record(ch, path, b)
        if path == "/api/system":
            sysname = str(b.get("system") or "")
            if sysname and sysname not in {v["system"] for v in systems_list(ch.file_id)}:
                return self._json({"error": "unknown design system"}, 400)
            ch.system = sysname
            ch.save_state()
            ch.emit({"t": "system", "system": sysname})
            return self._json({"ok": True, "system": sysname})
        if path in ("/api/ds/approve", "/api/ds/reject"):
            cid = str(b.get("id") or "")
            sys.path.insert(0, str(FLYNN / "adapters" / "library"))
            sys.path.insert(0, str(FLYNN / "scripts"))
            import changes
            import systems_policy
            ok, why = systems_policy.can_change((self._me() or {}).get("email"))
            if not ok:
                return self._json({"error": why}, 403)
            if path.endswith("reject"):
                try:
                    c = changes.reject(cid, str(b.get("reason") or ""))
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
                ch.emit({"t": "ds-change-result", "id": cid, "state": "rejected", "title": c["title"]})
                return self._json({"ok": True})

            def work():
                ch.emit({"t": "ds-change-result", "id": cid, "state": "applying"})
                try:
                    c = changes.approve(cid, log_fn=log)
                    ch.emit({"t": "ds-change-result", "id": cid, "state": "approved", "title": c["title"],
                             "applied": c.get("applied", []), "library": (c.get("library") or {}).get("name")})
                except Exception as e:  # noqa: BLE001
                    ch.emit({"t": "ds-change-result", "id": cid, "state": "error", "text": f"{type(e).__name__}: {e}"[:300]})
            threading.Thread(target=work, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/ds/notify":                 # changes.py propose pings this so the card shows at once
            ch.note_changes()
            return self._json({"ok": True})
        if path == "/api/letter/export":
            meta = _letters().get(fid)
            fmt = str(b.get("format") or "")
            if not meta or fmt not in EXPORTS_FOR.get(meta["type"], []):
                return self._json({"error": "not a letter, or that format does not apply to it"}, 400)
            ok = ch.start_turn(letter_export_brief(meta, fmt), shown=f"Export as {fmt.upper()}", context=False)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working"}, 200 if ok else 409)
        if path == "/api/library/sync":
            return self._json({"ok": sync_libraries(ch)})
        if path == "/api/figma/import":
            url = str(b.get("url") or "").strip()
            m = FIGMA_URL.match(url)
            if not m:
                return self._json({"error": "paste a Figma page link (figma.com/design/... with node-id=...)"}, 400)
            if not enabled():
                return self._json({"error": "panel chat is switched off (~/.flynn/features.json)"}, 503)
            page_name = str(b.get("page_name") or "").strip()[:120]
            ok = ch.start_turn(figma_brief(url, m.group(1), m.group(2), page_name, ch.file_id),
                               shown=f"Import from Figma: {url}" + (f" → {page_name}" if page_name else ""), context=False)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working on the last message"}, 200 if ok else 409)
        if path == "/api/upload":
            try:
                return self._json(save_upload(ch, str(b.get("name") or "file"), str(b.get("type") or ""), str(b.get("data") or "")))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/command":
            return self._command(ch, str(b.get("name") or ""), str(b.get("arg") or ""))
        if path == "/api/tool":                       # a desk tool call from flynn (desk_mcp.py), any engine
            import desk_tools
            name = str(b.get("name") or "")
            try:
                res = desk_tools.call(name, ch, b.get("args") or {}, __import__("types").SimpleNamespace(**globals()))
                log(f"{fid} tool {name} ok")
                return self._json({"result": res})
            except desk_tools.ToolError as e:
                log(f"{fid} tool {name}: {e}")
                return self._json({"error": str(e)}, 200)
            except Exception as e:  # noqa: BLE001
                log(f"{fid} tool {name} failed: {type(e).__name__}: {e}")
                return self._json({"error": f"{type(e).__name__}: {e}"[:400]}, 200)
        if path in ("/api/skill/approve", "/api/skill/reject"):
            import desk_tools
            try:
                m = desk_tools.approve_skill(str(b.get("id") or "")) if path.endswith("approve") else desk_tools.reject_skill(str(b.get("id") or ""))
            except (ValueError, OSError) as e:
                return self._json({"error": str(e)[:300]}, 400)
            log(f"{fid} skill {m['name']} {m['status']}")
            ch.emit({"t": "skill-result", "id": m["id"], "name": m["name"], "state": m["status"]})
            return self._json({"ok": True})
        if path in ("/api/prototype/approve", "/api/prototype/reject"):
            import desk_tools
            pid = str(b.get("id") or "")
            if path.endswith("reject"):
                try:
                    plan = desk_tools.reject_proto(pid)
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
                ch.emit({"t": "prototype-result", "id": pid, "state": "rejected", "flow": plan.get("flow")})
                return self._json({"ok": True})

            def work(pid=pid):
                ch.emit({"t": "prototype-result", "id": pid, "state": "applying"})
                try:
                    r = desk_tools.approve_proto(ch, pid)
                    ch.emit({"t": "prototype-result", "id": pid, "state": "applied", "applied": len(r.get("applied", [])),
                             "skipped": len(r.get("skipped", [])), "flow": r.get("flow")})
                except Exception as e:  # noqa: BLE001
                    ch.emit({"t": "prototype-result", "id": pid, "state": "error", "text": str(e)[:300]})
            threading.Thread(target=work, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/stop":
            ch.stop()
            return self._json({"ok": True})
        if path == "/api/reset":
            ch.reset()
            return self._json({"ok": True})
        if path == "/api/conversations/open":
            try:
                ok = ch.open_conversation(str(b.get("id") or ""))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True} if ok else {"error": "flynn is still working; stop it or wait"}, 200 if ok else 409)
        if path == "/api/conversations/delete":
            try:
                ch.delete_conversation(str(b.get("id") or ""))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


    def _command(self, ch: "Channel", name: str, arg: str):
        import commands
        import engines
        c = commands.BY_NAME.get(name)
        if not c:
            return self._json({"error": f"no command /{name}"}, 404)
        if c.kind == "prompt":
            if not enabled():
                return self._json({"error": "panel chat is switched off (~/.flynn/features.json)"}, 503)
            brief, shown = commands.prompt(name, arg, {"letter": _letters().get(ch.file_id)})
            ok = ch.start_turn(brief, shown=shown)
            return self._json({"ok": ok} if ok else {"error": "flynn is still working on the last message"}, 200 if ok else 409)
        if name == "sync":
            return self._json({"ok": sync_libraries(ch)})
        if name == "new":
            if ch.running:
                return self._json({"error": "flynn is still working; /stop first"}, 409)
            ch.reset()
            return self._json({"ok": True, "reload": True})
        if name == "stop":
            ch.stop()
            return self._json({"ok": True})
        if name == "context":
            ch.emit({"t": "info", "title": "Context", "rows": context_rows(ch)})
            return self._json({"ok": True})
        if name == "usage":
            ch.emit({"t": "info", "title": "Usage", "rows": usage_rows(ch)})
            return self._json({"ok": True})
        if name == "settings":
            ch.emit({"t": "settings", **engines.describe()})
            return self._json({"ok": True})
        return self._json({"error": f"/{name} runs in the tab"}, 400)

    def _record(self, ch: "Channel", path: str, b: dict):
        if not recorder_enabled():
            return self._json({"error": "the recorder is switched off (~/.flynn/features.json)"}, 503)
        if path == "/api/record/start":
            sys.path.insert(0, str(FLYNN / "scripts"))
            import systems_policy
            ok, why = systems_policy.can_change((self._me() or {}).get("email"))
            if not ok:
                return self._json({"error": why}, 403)
            if isinstance(b.get("team"), str) and FILE_ID.match(b["team"]):
                ch.team = b["team"]
                ch.save_state()
            try:
                return self._json({"ok": True, **record_api.start(ch, b, log, user=((self._me() or {}).get("email") or ""))})
            except (ValueError, RuntimeError) as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/record/capture":
            return self._json({"ok": record_api.capture(ch.file_id)})
        if path == "/api/record/stop":
            return self._json({"ok": record_api.stop(ch.file_id)})
        return self._json({"error": "not found"}, 404)


def make_server(port: int = 8772, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=int(os.environ.get("FLYNN_PANEL_PORT", "8772")))
    s.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    srv = make_server(a.port, a.host)

    def shutdown(signum, frame):
        """Stop flynn's own running turns before exiting, so no turn outlives the server as an orphan process whose
        end the tab never hears about."""
        for ch in list(CHANNELS.values()):
            if ch.running:
                try:
                    ch.stop()
                    ch.emit({"t": "error", "text": "The chat server restarted while flynn was working, so this turn was "
                                                   "cut off. Send your message again, or say continue."})
                    ch.emit({"t": "turn_end", "ok": False, "cost": None, "interrupted": True})
                except Exception:  # noqa: BLE001
                    pass
        try:                                          # the claude processes this server started end with it
            kids = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()
            for k in kids:
                subprocess.run(["pkill", "-TERM", "-P", k], capture_output=True)
                os.kill(int(k), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
        log("stopped (signal)")
        os._exit(0)
    import signal
    signal.signal(signal.SIGTERM, shutdown)
    log(f"serving on {a.host}:{srv.server_port}")
    print(f"flynn panel server on http://{a.host}:{srv.server_port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
