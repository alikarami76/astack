"""The recorder behind the Record button in the canvas toolbar: one recording at a time per canvas file.

  start    open flynn's Chrome at the address and record (watch: the user browses; crawl: flynn follows links). On the
           team server (FLYNN_ENGINE=remote) the person's flynn agent opens that Chrome on their own computer and
           sends the recording here when it ends (agent_hub.RemoteRecording)
  capture  record the page as it is now (watch mode; same as Snap on the floating bar in flynn's Chrome)
  stop     stop recording; then mine (tokens and component candidates, stored in the flynn store), build the
           results on the chosen canvas page, and, if asked, have flynn review and name the candidates in chat
  state    what the toolbar shows: idle, recording (pages, current address), processing (stage), done, error

Each step is announced in the file's chat as a `recording` event. Switch off with ~/.flynn/features.json
{"recorder": false}. Log: ~/.flynn/logs/recorder.log (sessions) and panel_chat.log (this module).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[2]
sys.path.insert(0, str(FLYNN / "adapters" / "recorder"))

SIZES = {"desktop": 1440, "mobile": 390}
MAX_SNAPS = 3           # pages per recording; recorder.MAX_SNAPS says the same


def _session_cls():
    import recorder
    return recorder.Session


def mine_fn(folder: Path) -> dict:
    import mine
    return mine.mine(folder)


def propose_fn(change: dict) -> str:
    sys.path.insert(0, str(FLYNN / "adapters" / "library"))
    import changes
    return changes.propose(change)["id"]


def library_fn(system: str) -> dict:
    sys.path.insert(0, str(FLYNN / "adapters" / "library"))
    import library
    return library.sync(f"rec:{system}")


def build_fn(folder: Path, target: dict, screens_only: bool = False) -> dict:
    import canvas
    return canvas.build(folder, target, log=lambda m: None, screens_only=screens_only)


def remote() -> bool:
    """On the team server flynn's AI and the recorder run on each person's computer, through their flynn agent:
    the recorder opens a Chrome window the person browses in, and the server has no screen."""
    return os.environ.get("FLYNN_ENGINE") == "remote"


class Recording:
    def __init__(self, ch, cfg: dict, log, user: str = ""):
        self.ch, self.cfg, self.log = ch, cfg, log
        self.session = _session_cls()(cfg)
        if remote():
            if not user:
                raise RuntimeError("flynn doesn't know who you are; sign in again and retry")
            import agent_hub
            self.session = agent_hub.RemoteRecording(self.session, user)
        self.stage = "recording"     # recording, mining, building, reviewing, done, error
        self.error = ""
        self.result: dict = {}
        self._post: threading.Thread | None = None

    def start(self) -> "Recording":
        self.session.start()
        self.ch.emit({"t": "recording", "state": "started", "system": self.cfg["system"], "how": self.cfg["how"],
                      "url": self.session.cfg["url"].split("?")[0]})
        # A crawl ends by itself; a watch ends on Stop. Either way, process when the session thread is done.
        self._post = threading.Thread(target=self._after, daemon=True)
        self._post.start()
        return self

    def stop(self) -> None:
        self.session.stop(wait=0)

    def _after(self) -> None:
        if self.session._thread:
            self.session._thread.join()
        folder = self.session.folder
        try:
            if not self.session.pages:
                raise RuntimeError(self.session.error or "nothing was recorded")
            if not self.cfg.get("design_system", True):
                self._screens_only(folder)
                return
            self.stage = "mining"
            self.ch.emit({"t": "recording", "state": "mining", "pages": len(self.session.pages)})
            mined = mine_fn(folder)
            self.stage = "building"
            self.ch.emit({"t": "recording", "state": "building", "components": len(mined["components"]),
                          "tokens": len(mined["tokens"])})
            built = build_fn(folder, self.cfg["target"])
            team, fid = self.cfg.get("team"), self.cfg.get("file")
            link = (f"/#/workspace?team-id={team}&file-id={fid}&page-id={built['page']}" if team and fid else None)
            self.result = {"page": built["page"], "pageName": built["pageName"], "link": link, "folder": str(folder),
                           "components": [c["name"] for c in mined["components"]], "tokens": len(mined["tokens"]),
                           "pages": len(self.session.pages), "errors": built.get("errors", [])}
            self.ch.emit({"t": "recording", "state": "done", **self.result})
            self.log(f"{self.ch.file_id} recording {self.session.id} done: {len(mined['components'])} components")
            if self.cfg.get("ask_flynn"):
                self.stage = "reviewing"
                self._ask_flynn(folder, mined)
            else:
                # no review: the mined candidates become one change for the user to approve (nothing changes before that)
                self.stage = "library"
                cid = propose_fn({
                    "system": f"rec:{self.cfg['system']}", "file": self.cfg.get("file") or "",
                    "title": f"Library from the {self.cfg['system']} recording ({len(mined['components'])} components)",
                    "why": self.cfg.get("description") or "Recorded from the live product.",
                    "source": f"recording:{folder}",
                    "ops": [{"op": "component.set", "name": c["name"],
                             "fields": {"description": f"Recorded: seen {c['count']} times on {len(c['pages'])} page(s)."}}
                            for c in mined["components"]] + [{"op": "recording.apply", "session": str(folder)}]})
                self.ch.emit({"t": "recording", "state": "library-proposed", "change": cid})
                self.ch.note_changes()
            self.stage = "done"
        except Exception as e:  # noqa: BLE001 - shown in the toolbar and the chat
            self.stage, self.error = "error", f"{type(e).__name__}: {e}"
            self.ch.emit({"t": "recording", "state": "error", "text": self.error, "folder": str(folder)})
            self.log(f"{self.ch.file_id} recording error {self.error}")

    def _screens_only(self, folder: Path) -> None:
        """the user chose not to make a design system: put the recorded screens on the page, as editable layers, and
        stop there (no mining, no library change, no review)."""
        self.stage = "building"
        self.ch.emit({"t": "recording", "state": "building", "components": 0, "tokens": 0, "screens_only": True})
        built = build_fn(folder, self.cfg["target"], screens_only=True)
        team, fid = self.cfg.get("team"), self.cfg.get("file")
        link = (f"/#/workspace?team-id={team}&file-id={fid}&page-id={built['page']}" if team and fid else None)
        self.result = {"page": built["page"], "pageName": built["pageName"], "link": link, "folder": str(folder),
                       "components": [], "tokens": 0, "pages": len(self.session.pages), "screens_only": True,
                       "errors": built.get("errors", [])}
        self.ch.emit({"t": "recording", "state": "done", **self.result})
        self.log(f"{self.ch.file_id} recording {self.session.id} done: {len(self.session.pages)} screens, no design system")
        self.stage = "done"

    def _ask_flynn(self, folder: Path, mined: dict) -> None:
        names = ", ".join(c["name"] for c in mined["components"][:30])
        text = (
            "A design system recording just finished and was built on the canvas page "
            f"\"{self.result['pageName']}\".\n"
            f"- System: {self.cfg['system']} (stored as candidates under system \"rec:{self.cfg['system']}\", tier inferred)\n"
            f"- What the user says he recorded: {self.cfg.get('description') or '(no description)'}\n"
            f"- Recording folder: {folder} (session.json, pages/*/w*/full.png and dom.json, mine.json, components/*/)\n"
            f"- Candidates found: {names}\n\n"
            "Your job now is to turn this recording into a real design system library, the way you build designs from "
            "real components (not screenshots). Look at the screenshots (pages/*/w*/full.png), the component crops "
            "(components/*/) and the user's description, then:\n"
            "1. Decide which candidates are real, reusable components and what they are called in this product. Merge "
            "candidates that are the same component (for example Btn and Button), drop wrappers and one-off layout.\n"
            "2. Name the variants you can see (look, colour, size, state).\n"
            f"3. Write {folder}/review.json exactly in this shape:\n"
            '   {"components": [{"name": "Button", "from": ["Btn", "Button"], "keep": true, '
            '"variants": {"1": "primary · large", "2": "outlined · small"}, "description": "...", "usage": "..."}, '
            '{"name": "Menu", "from": ["Menu"], "keep": false}]}\n'
            "   (variant keys are the candidate's variant numbers in mine.json, starting at 1).\n"
            "4. Propose the result as ONE design system change for the user to approve (do not edit the store or the "
            "library yourself): a JSON file with \"system\": \"rec:" + self.cfg['system'] + "\", a title, why, "
            f"\"source\": \"recording:{folder}\", \"file\": \"{self.cfg.get('file') or ''}\", and ops: one "
            "component.set per kept component (description, usage, variants, states), component.remove for components "
            "the current library has but this product no longer shows, token.set/token.remove for palette and type "
            f"changes, and a final {{\"op\": \"recording.apply\", \"session\": \"{folder}\"}}. Then run "
            f"`python3 \"{FLYNN}/adapters/library/changes.py\" propose --file <that file>`. If this system already has "
            "a library, compare against it (library.py list; the store rows under rec:<system>) and propose only "
            "what changed.\n"
            "5. The tab shows the user the change as a card; when he approves, flynn desk updates the store and rebuilds the "
            "library from the recorded elements (component ids kept).\n"
            "6. Give the user a short summary: what you kept, merged and dropped, and anything he should confirm.\n"
            "Do not change the canvas page with the recorded screens.")
        if not self.ch.start_turn(text, shown=f"Review the {self.cfg['system']} recording", context=False):
            self.ch.emit({"t": "recording", "state": "review-skipped",
                          "text": "flynn was busy, so the review did not start. Ask for it in chat when you are ready."})

    def state(self) -> dict:
        s = self.session.status()
        return {"state": "recording" if self.stage == "recording" else self.stage, "session": s["id"],
                "pages": s["pages"], "current": s["current"], "how": s["how"], "error": self.error or s["error"],
                "result": self.result}


RECORDINGS: dict[str, Recording] = {}
_lock = threading.Lock()


def current_page_id() -> str:
    """The page open on the canvas right now, so the results land there even if the user moves on while recording."""
    try:
        from bridge import Bridge
        return str(Bridge(timeout=10).run_js("return penpot.currentPage.id") or "")
    except Exception:  # noqa: BLE001 - the build falls back to whatever page is open then
        return ""


def config_from(body: dict, ch) -> dict:
    """Validate the setup popup's answers."""
    url = str(body.get("url") or "").strip()
    if not url.startswith(("http://", "https://", "file://")):
        raise ValueError("the start address must begin with http://, https:// or file://")
    if url.startswith(("http://localhost:9001", "http://127.0.0.1:9001")):
        raise ValueError("that is the canvas itself; give the address of the product to record")
    make_ds = body.get("design_system", True) is not False
    system = str(body.get("system") or "").strip()
    if not system and not make_ds:
        from urllib.parse import urlsplit
        system = (urlsplit(url).hostname or "screens").removeprefix("www.")
    if not system:
        raise ValueError("name the design system this recording belongs to")
    if not body.get("rights"):
        raise ValueError("confirm that you have the right to record this product")
    t = body.get("target") or {}
    if t.get("mode") == "new":
        name = str(t.get("page_name") or "").strip()
        if not name:
            raise ValueError("name the new page")
        target = {"mode": "new", "page_name": name[:120]}
    else:
        target = {"mode": "current", "page_id": str(t.get("page_id") or "") or current_page_id()}
    sizes = [SIZES[s] for s in (body.get("sizes") or ["desktop"]) if s in SIZES] or [1440]
    return {"system": system[:60], "description": str(body.get("description") or "").strip()[:2000], "url": url,
            "how": "crawl" if body.get("how") == "crawl" else "watch",
            "max_pages": max(1, min(int(body.get("max_pages") or MAX_SNAPS), MAX_SNAPS)), "sizes": sizes, "target": target,
            "design_system": make_ds, "ask_flynn": make_ds and bool(body.get("ask_flynn", True)),
            "file": ch.file_id, "team": ch.team, "rights": True}


def start(ch, body: dict, log, user: str = "") -> dict:
    with _lock:
        cur = RECORDINGS.get(ch.file_id)
        if cur and cur.stage in ("recording", "mining", "building"):
            raise RuntimeError("a recording is already running for this file")
        cfg = config_from(body, ch)
        rec = Recording(ch, cfg, log, user).start()
        RECORDINGS[ch.file_id] = rec
    log(f"{ch.file_id} recording {rec.session.id} started ({cfg['how']}, {cfg['system']})")
    return rec.state()


def state(file_id: str) -> dict:
    rec = RECORDINGS.get(file_id)
    return rec.state() if rec else {"state": "idle"}


def capture(file_id: str) -> bool:
    rec = RECORDINGS.get(file_id)
    if not rec or rec.stage != "recording":
        return False
    rec.session.capture_now()
    return True


def stop(file_id: str) -> bool:
    rec = RECORDINGS.get(file_id)
    if not rec or rec.stage != "recording":
        return False
    rec.stop()
    return True


def last_settings(file_id: str) -> dict:
    """The previous recording's answers for this file, to prefill the popup."""
    rec = RECORDINGS.get(file_id)
    if not rec:
        return {}
    c = rec.cfg
    return {"system": c["system"], "url": rec.session.cfg["url"], "how": c["how"], "max_pages": c["max_pages"],
            "description": c["description"], "design_system": c.get("design_system", True)}
