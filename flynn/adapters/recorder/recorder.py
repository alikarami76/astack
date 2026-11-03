"""flynn design system recorder: record a live product in flynn's Chrome, page by page.

A session records, for every page, at each chosen width: a viewport and a full-page screenshot and `dom.json`
(boxes, computed styles, roles, classes, CSS variables, fonts, media queries; see record.js), plus the page's
scrubbed HTML once. Two ways to record:
  watch   the user browses in the recording window. A floating Snap bar sits on every page (adapters/browser/
          snapbar.py); a page is recorded only when he presses Snap (or Alt+Shift+S, or Capture in the canvas
          toolbar), exactly as it is then (an open menu, a filled form, a tab). The bar is removed before each
          capture, so it is never in a recording.
  crawl   flynn follows same-site links from the start address by itself, up to a page limit. It only
          navigates: it never clicks, types or submits forms, and skips links that look like sign-out,
          delete or download.
A recording holds at most MAX_SNAPS pages (3): a watch ends by itself after the third snap, and a crawl stops
after three pages.
Everything stays on this Mac: refs/recordings/<system>/<session>/ (git-ignored).

Library use (the panel server runs sessions in a thread):  Session(cfg).start() / .capture_now() / .stop()
CLI:  recorder.py list            recent sessions
      recorder.py show <session>  a session's settings and pages
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "browser"))
import capture  # noqa: E402
import snapbar  # noqa: E402

MAX_SNAPS = 3           # pages per recording (the user, 2026-10-06)

# Leading comment lines are removed: "return // ..." would end the statement before the function runs.
RECORD_JS = re.sub(r"^(\s*//[^\n]*\n)+", "", (HERE / "record.js").read_text(encoding="utf-8"))
ROOT = FLYNN / "refs" / "recordings"
LOG = Path.home() / ".flynn" / "logs" / "recorder.log"
# Tabs that belong to flynn itself, never recorded.
OWN = ("http://localhost:9001", "http://127.0.0.1:9001", "http://127.0.0.1:877", "http://localhost:877",
       "about:", "chrome://", "chrome-extension://", "devtools://")
DENY = re.compile(r"(log-?out|sign-?out|exit|delete|remove|unsubscribe|deactivate|/api/|\.(pdf|zip|xlsx?|csv|docx?)(\?|$)|^mailto:|^tel:|^javascript:)", re.I)
MOBILE = 390
MAX_HEIGHT = 16000      # CSS pixels; longer pages (endless feeds) are cut here and marked truncated
WALK_JS = "return (" + re.sub(r"^(\s*//[^\n]*\n)+", "", (FLYNN / "adapters" / "penpot" / "html2pnode.js").read_text(encoding="utf-8")) + ")()"
STITCH_JS = re.sub(r"^(\s*//[^\n]*\n)+", "", (HERE / "stitch.js").read_text(encoding="utf-8"))


def tile_positions(height: int, vh: int) -> list[int]:
    """Scroll offsets that cover a page of `height` with screens of `vh`: 0, vh, 2vh, ... and the last screen
    flush with the bottom (the browser would clamp it there anyway)."""
    if height <= vh:
        return [0]
    ys = list(range(0, height - vh, vh))
    ys.append(height - vh)
    return ys


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def slug(text: str, n: int = 40) -> str:
    return capture.slugify(text or "page", n) or "page"


class Session:
    """One recording. `cfg` keys: system, description, url, how (watch|crawl), max_pages, sizes (list of widths),
    target ({mode: current|new, page_id, page_name}), file, team, ask_flynn."""

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.cfg.setdefault("how", "watch")
        self.cfg.setdefault("sizes", [1440])
        self.cfg["max_pages"] = max(1, min(int(self.cfg.get("max_pages") or MAX_SNAPS), MAX_SNAPS))
        now = dt.datetime.now()
        self.id = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{slug(urlsplit(self.cfg['url']).netloc or self.cfg['system'], 30)}"
        self.folder = ROOT / slug(self.cfg["system"]) / self.id
        self.pages: list[dict] = []
        self.state = "starting"          # starting, recording, stopping, stopped, error
        self.error = ""
        self.current = ""
        self._stop = threading.Event()
        self._snap_now = threading.Event()
        self._lock = threading.Lock()   # one Selenium call at a time
        self._tab = None
        self.bar: snapbar.SnapBar | None = None
        self._thread: threading.Thread | None = None

    # ---- public -------------------------------------------------------------------------------------------
    def start(self) -> "Session":
        self.folder.mkdir(parents=True, exist_ok=True)
        self._save()
        self._thread = threading.Thread(target=self._run, name=f"recorder-{self.id}", daemon=True)
        self._thread.start()
        return self

    def capture_now(self) -> None:
        self._snap_now.set()

    def stop(self, wait: float = 60) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(wait)

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "pages": len(self.pages),
                "current": self.current, "folder": str(self.folder), "how": self.cfg["how"]}

    # ---- recording ----------------------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._open_tab()
            self.state = "recording"
            log(f"{self.id} start {self.cfg['how']} {capture.public_url(self.cfg['url'])}")
            if self.cfg["how"] == "crawl":
                self._crawl()
                self._close_tab()                 # flynn opened this tab for the crawl; tidy it away
            else:
                self._watch()
            self.state = "stopped"
        except Exception as e:  # the panel shows it; the session folder keeps what was recorded
            self.state, self.error = "error", f"{type(e).__name__}: {e}"
            log(f"{self.id} error {self.error}")
        finally:
            self.cfg["stopped"] = dt.datetime.now().isoformat(timespec="seconds")
            self._save()
            log(f"{self.id} stop pages={len(self.pages)}")

    def _open_tab(self) -> None:
        if not capture.running():
            capture.open_browser()
            time.sleep(2)
        d = capture._driver()
        try:
            d.switch_to.new_window("tab")
            self._tab = d.current_window_handle
            d.get(self.cfg["url"])
        finally:
            d.quit()

    def _close_tab(self) -> None:
        with self._lock:
            d = capture._driver()
            try:
                if self._tab in d.window_handles and len(d.window_handles) > 1:
                    d.switch_to.window(self._tab)
                    d.close()
                    for h in d.window_handles:     # bring the canvas back to the front
                        d.switch_to.window(h)
                        if d.current_url.startswith(("http://localhost:9001", "http://127.0.0.1:9001")):
                            break
            except Exception as e:  # noqa: BLE001 - closing the tab is a courtesy
                log(f"{self.id} could not close the crawl tab: {e}")
            finally:
                d.quit()

    def _tab_url(self) -> str:
        """The page the user is looking at in the recording: the front tab, unless it is one of flynn's own."""
        tab = capture.front_tab()
        url = tab.get("url", "")
        if url.startswith(OWN):
            return ""
        self._tab = tab["id"]
        return url

    def _watch(self) -> None:
        """Record a page each time the user presses Snap on the floating bar (or Capture in the canvas toolbar)."""
        limit = self.cfg["max_pages"]
        self.bar = snapbar.SnapBar(limit=limit).start()
        refreshed = time.time()
        try:
            while not self._stop.is_set() and len(self.pages) < limit:
                if not capture.running():
                    raise RuntimeError("flynn's Chrome was closed")
                if time.time() - refreshed > 2:          # tabs the user opened since
                    self.bar.refresh()
                    refreshed = time.time()
                ev = self.bar.wait(0.8)
                forced = self._snap_now.is_set()
                if not ev and not forced:
                    continue
                self._snap_now.clear()
                url = ""
                try:
                    if ev:
                        self._tab = ev["tab"]
                        url = next((t["url"] for t in capture._tabs() if t["id"] == ev["tab"]), "")
                    else:
                        url = self._tab_url()
                except (Exception, SystemExit):
                    url = ""
                if not url or url.startswith(OWN):
                    self.bar.set("idle", label="Open a product page first")
                    continue
                self.bar.set("busy")
                self._record(url, note="snapped" if ev else "captured from the toolbar")
                self.bar.set("done", count=len(self.pages))
            if len(self.pages) >= limit:
                self.bar.set("done", count=len(self.pages), label=f"{limit} of {limit} · finished")
                time.sleep(1.2)
        finally:
            self.bar.stop()
            self.bar = None

    def _crawl(self) -> None:
        start = self.cfg["url"]
        origin = urlsplit(start).netloc
        queue, seen = [start], set()
        while queue and not self._stop.is_set() and len(self.pages) < self.cfg["max_pages"]:
            url = queue.pop(0)
            key = url.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            with self._lock:
                d = capture._driver()
                try:
                    d.switch_to.window(self._tab)
                    d.get(url)
                    links = d.execute_script("return [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))")
                finally:
                    d.quit()
            time.sleep(1.5)
            self._record(url)
            for h in links or []:
                if not h or DENY.search(h):
                    continue
                full = urljoin(url, h).split("#")[0]
                if urlsplit(full).netloc == origin and full not in seen and full not in queue:
                    queue.append(full)
        # crawl ends by itself; the panel then processes it like a stopped watch session

    def _record(self, url: str, note: str = "") -> None:
        n = len(self.pages) + 1
        self.current = capture.public_url(url)
        with self._lock:
            d = capture._driver()
            try:
                if self._tab:
                    d.switch_to.window(self._tab)
                d.execute_script(snapbar.hide_js)          # the snap bar is never part of a recording
                self._wait_settled(d)
                title = d.title
                folder = self.folder / "pages" / f"{n:03d}-{slug(title or urlsplit(url).path)}"
                folder.mkdir(parents=True, exist_ok=True)
                sizes = []
                for w in self.cfg["sizes"]:
                    sizes.append(self._shoot(d, folder, int(w)))
                html = capture.scrub_html(d.execute_script(capture.SERIALIZE_JS))
                (folder / "page.html").write_text(html, encoding="utf-8")
            finally:
                try:
                    if self.bar:
                        d.execute_script(snapbar.show_js)
                finally:
                    d.quit()
        page = {"n": n, "url": capture.public_url(url), "title": title, "folder": str(folder), "sizes": sizes,
                "note": note, "time": dt.datetime.now().isoformat(timespec="seconds")}
        (folder / "meta.json").write_text(json.dumps({**page, "full_url": url}, ensure_ascii=False, indent=1), encoding="utf-8")
        self.pages.append(page)
        self._save()
        log(f"{self.id} page {n} {page['url']}")

    @staticmethod
    def _wait_settled(d, quiet: float = 0.6, limit: float = 8.0) -> None:
        """Wait for the document to finish loading and its element count to stop changing."""
        end, prev = time.time() + limit, -1
        while time.time() < end:
            st = d.execute_script("return [document.readyState, document.getElementsByTagName('*').length]")
            if st[0] == "complete" and st[1] == prev:
                return
            prev = st[1]
            time.sleep(quiet)

    def _shoot(self, d, folder: Path, width: int) -> dict:
        """Screenshots and dom.json at one width (emulated), for a page of any length.

        The page is prepared first (stitch.js): an inner scrolling panel is unrolled into the document, lazy
        content is loaded by scrolling through once, animations stop, sticky elements stay in their place in the
        flow, and fixed elements appear once, in the first screen. Then dom.json is measured at the top of the
        page and full.png is stitched from screen-sized shots in exactly that state, so every box in dom.json
        lands on its element in full.png. The page is put back as it was afterwards."""
        out = folder / f"w{width}"
        out.mkdir(exist_ok=True)
        d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": 900 if width > 700 else 844, "deviceScaleFactor": 0, "mobile": width <= 700})
        time.sleep(0.9)
        self._wait_settled(d, quiet=0.4, limit=4)
        info: dict = {}
        try:
            d.execute_script("return " + STITCH_JS)
            info["prep"] = d.execute_script("return window.__flynnStitch.prep()")
            info["lazy"] = self._load_lazy(d)
            frozen = d.execute_script("return window.__flynnStitch.freeze()")
            info["frozen"] = frozen
            dom = d.execute_script("return " + RECORD_JS)
            # the page's real elements, in the same state as the screenshot (editable on the canvas)
            try:
                tree = d.execute_script(WALK_JS)
                (out / "pnode.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
                info["elements"] = len(tree.get("nodes", []))
            except Exception as e:  # noqa: BLE001 - the screenshot still records the page
                info["elements_error"] = f"{type(e).__name__}: {e}"[:200]
            full, view, tiles = self._stitch(d, frozen)
            full.save(out / "full.png", optimize=True)
            view.save(out / "viewport.png", optimize=True)
            info["tiles"] = tiles
            dom["capture"] = info
            (out / "dom.json").write_text(json.dumps(dom, ensure_ascii=False), encoding="utf-8")
        finally:
            try:
                d.execute_script("return window.__flynnStitch && window.__flynnStitch.restore()")
            finally:
                d.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        return {"width": width, "dir": str(out), "els": len(dom.get("els", [])), "doc": dom.get("doc"), "dpr": dom.get("dpr"),
                "unrolled": bool(info["prep"].get("unrolled")), "tiles": len(tiles), "truncated": frozen["height"] > MAX_HEIGHT,
                "elements": info.get("elements", 0)}

    @staticmethod
    def _load_lazy(d, step_wait: float = 0.25, limit: float = 12.0) -> dict:
        """Scroll through the page once so content that loads on scroll is there, then wait for images."""
        end, y, steps = time.time() + limit, 0, 0
        while time.time() < end:
            h, vh = d.execute_script("return [document.documentElement.scrollHeight, innerHeight]")
            if y >= min(h, MAX_HEIGHT) - vh:
                break
            y += int(vh * 0.85)
            d.execute_script("window.scrollTo(0, arguments[0])", y)
            steps += 1
            time.sleep(step_wait)
        d.execute_script("window.scrollTo(0, 0)")
        pending = 0
        for _ in range(20):                     # up to 2s for images to finish
            pending = d.execute_script("return [...document.images].filter(i => !i.complete).length")
            if not pending:
                break
            time.sleep(0.1)
        return {"steps": steps, "images_pending": pending}

    @staticmethod
    def _stitch(d, frozen: dict):
        """Shoot the page screen by screen and stitch the screens into one image (device pixels)."""
        from PIL import Image
        height, vh = min(int(frozen["height"]), MAX_HEIGHT), int(frozen["vh"])
        dpr = float(frozen.get("dpr") or 1)
        full, view, tiles = None, None, []
        for i, y in enumerate(tile_positions(height, vh)):
            d.execute_script("window.__flynnStitch.fixed(arguments[0])", i == 0)
            actual = d.execute_script("window.scrollTo(0, arguments[0]); return scrollY", y)
            time.sleep(0.12)                                        # let the frame paint (and scroll handlers run)
            if d.execute_script("return window.__flynnStitch.late()"):
                time.sleep(0.05)                                    # a floating copy was hidden; repaint
            png = d.execute_cdp_cmd("Page.captureScreenshot", {"format": "png"})["data"]
            tile = Image.open(io.BytesIO(base64.b64decode(png))).convert("RGB")
            if full is None:
                full = Image.new("RGB", (tile.width, int(round(height * dpr))), "white")
                view = tile.copy()
            top = int(round(actual * dpr))
            full.paste(tile, (0, top))
            tiles.append({"y": y, "scroll": actual})
        d.execute_script("window.__flynnStitch.fixed(true); window.scrollTo(0, 0)")
        return full, view, tiles

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, **{k: v for k, v in self.cfg.items() if k != "url"},
                "url": capture.public_url(self.cfg["url"]), "state": self.state, "error": self.error, "pages": self.pages}
        (self.folder / "session.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def sessions(limit: int = 20) -> list[dict]:
    out = []
    for f in sorted(ROOT.glob("*/*/session.json"), key=lambda p: p.parent.name, reverse=True)[:limit]:
        s = json.loads(f.read_text(encoding="utf-8"))
        out.append({"id": s["id"], "system": s.get("system"), "pages": len(s.get("pages", [])), "folder": str(f.parent)})
    return out


def find(session_id: str) -> Path:
    hits = list(ROOT.glob(f"*/{session_id}"))
    if not hits:
        raise SystemExit(f"no recording {session_id}")
    return hits[0]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="recorder.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("session")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in sessions():
            print(f"{s['id']}  {s['system']}  {s['pages']} pages  {s['folder']}")
    else:
        print((find(a.session) / "session.json").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
