#!/usr/bin/env python3
"""Capture live pages into a recorder session folder, through the DevTools protocol (no Selenium).

Writes the same layout as adapters/recorder/recorder.py, so `adapters/recorder/mine.py <session>` reads it as is:
  refs/recordings/<system>/<session>/session.json
  refs/recordings/<system>/<session>/pages/NNN-<title>/page.html (scrubbed), meta.json
  refs/recordings/<system>/<session>/pages/NNN-<title>/w<width>/viewport.png, full.png, dom.json, pnode.json

Two ways to get a browser:
  default      Playwright's headless Chromium. For public pages.
  --cdp PORT   an already-running Chrome with remote debugging on that port (flynn's own Chrome, where the owner has
               logged in by hand). The runner opens its own tab through /json/new, uses only that tab, and closes it
               at the end. It never lists, switches to or reads the owner's tabs, and never reads cookies or storage.

It only navigates and scrolls: no clicks, no typing. Per page and width it follows recorder.py's steps: emulate the
width (mobile widths get touch, a mobile user agent and a device scale factor of 2), load, unroll an inner scroller,
scroll once for lazy content, freeze, measure (record.js), stitch full.png (stitch.js), restore.

Usage:
  site_capture.py --system <id> --session <name> --urls file.txt [--widths 1440,390] [--cdp PORT] [--wait 4]
  urls file: one URL per line, an optional note after " # ", blank lines and lines starting with # are skipped.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import re
import signal
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[1]
REC = FLYNN / "adapters" / "recorder"
ROOT = FLYNN / "refs" / "recordings"
MAX_HEIGHT = 16000          # CSS pixels; longer pages are cut here and marked truncated (as recorder.py)
MOBILE_MAX = 700
PAGE_LIMIT = 300            # seconds for one page at all its widths; then it is marked as a problem and skipped


def _timeout(signum, frame):
    raise TimeoutError(f"page took longer than {PAGE_LIMIT}s")


VIEW_H = {"desktop": 900, "mobile": 844}
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.5 Mobile/15E148 Safari/604.1")
# Same in-page scrub as the recorder's page serializer: no inline script bodies, token-like meta, password and
# hidden inputs blanked. Python then blanks token-like attributes and JWT shapes.
SERIALIZE_JS = r"""
const root = document.documentElement.cloneNode(true);
for (const s of root.querySelectorAll('script')) { if (!s.src) s.textContent = ''; }
for (const m of root.querySelectorAll('meta')) {
  const n = (m.getAttribute('name') || m.getAttribute('property') || '').toLowerCase();
  if (/csrf|xsrf|token|auth|session/.test(n)) m.setAttribute('content', '[scrubbed]');
}
for (const i of root.querySelectorAll('input')) {
  const t = (i.getAttribute('type') || '').toLowerCase();
  if (t === 'password' || t === 'hidden') i.setAttribute('value', '');
}
const dt = document.doctype ? '<!doctype ' + document.doctype.name + '>\n' : '';
return dt + root.outerHTML;
"""
SECRET_ATTR = re.compile(r"""(\s(?:[\w:-]*(?:token|csrf|xsrf|auth|session|secret|api[-_]?key)[\w:-]*)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")


def scrub_html(html: str) -> str:
    html = SECRET_ATTR.sub(lambda m: f"{m.group(1)}{m.group(2)}[scrubbed]{m.group(2)}", html)
    return JWT.sub("[scrubbed-jwt]", html)


def public_url(url: str) -> str:
    """Scheme, host and path only: queries can hold names, ids or sign-in state."""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}" if u.scheme else url.split("?")[0].split("#")[0]


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^\w؀-ۿ]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:n].rstrip("-")) or "page"


def _js(path: Path) -> str:
    # leading comment lines go: "return // ..." would end the statement before the expression runs
    return re.sub(r"^(\s*//[^\n]*\n)+", "", path.read_text(encoding="utf-8")).rstrip().rstrip(";")


RECORD_JS = _js(REC / "record.js")
STITCH_JS = _js(REC / "stitch.js")
WALK_JS = "(" + _js(FLYNN / "adapters" / "penpot" / "html2pnode.js") + ")()"


def tile_positions(height: int, vh: int) -> list[int]:
    if height <= vh:
        return [0]
    ys = list(range(0, height - vh, vh))
    ys.append(height - vh)
    return ys


def parse_urls(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, note = line.partition(" #")
        url = url.strip()
        if url:
            out.append((url, note.strip()))
    return out


def _eval(page, expr: str):
    return page.evaluate("() => { return " + expr + "; }")


class Capture:
    def __init__(self, system: str, session: str, urls: list[tuple[str, str]], widths: list[int],
                 cdp: int | None = None, wait: float = 4.0, root: Path | None = None, headed: bool = False):
        self.system, self.urls, self.widths, self.cdp, self.wait, self.headed = system, urls, widths, cdp, wait, headed
        self.folder = (root or ROOT) / slug(system) / slug(session, 60)
        self.id = slug(session, 60)
        self.pages: list[dict] = []
        self.blocked: list[dict] = []
        self.cfg = {"system": system, "description": f"site capture {session}", "how": "capture",
                    "sizes": widths, "url": urls[0][0] if urls else "", "cdp": bool(cdp)}
        self._tab_id = ""
        self._ua = ""
        self._pw = self._browser = self._ctx = self.page = self.cdp_session = None

    # ---- browser ------------------------------------------------------------------------------------------
    def _open(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        if self.cdp:
            marker = "flynn-capture-" + uuid.uuid4().hex[:10]
            base = f"http://127.0.0.1:{self.cdp}"
            req = urllib.request.Request(f"{base}/json/new?about:blank%23{marker}", method="PUT")
            with urllib.request.urlopen(req, timeout=10) as r:
                info = json.load(r)
            self._tab_id = info["id"]
            self._browser = self._pw.chromium.connect_over_cdp(base)
            for ctx in self._browser.contexts:
                for p in ctx.pages:
                    if marker in p.url:        # only our own tab; no other page is touched
                        self._ctx, self.page = ctx, p
            if self.page is None:
                raise RuntimeError("could not attach to the tab opened for the capture")
        else:
            self._browser = self._pw.chromium.launch(headless=not self.headed)
            probe = self._browser.new_page()
            ua = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()
            self._ctx = self._browser.new_context(user_agent=ua)
            self.page = self._ctx.new_page()
        self.cdp_session = self._ctx.new_cdp_session(self.page)
        self._ua = self.page.evaluate("navigator.userAgent")

    def _close(self) -> None:
        try:
            if self.cdp and self._tab_id:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{self.cdp}/json/close/{self._tab_id}", timeout=10).read()
                except Exception:  # noqa: BLE001 - closing is a courtesy; the tab is ours
                    pass
            elif self._ctx:
                self._ctx.close()
        finally:
            try:
                if self._browser and not self.cdp:
                    self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            if self._pw:
                self._pw.stop()

    def _emulate(self, width: int) -> None:
        mobile = width <= MOBILE_MAX
        s = self.cdp_session
        s.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": VIEW_H["mobile" if mobile else "desktop"],
            "deviceScaleFactor": 2 if mobile else 1, "mobile": mobile})
        s.send("Emulation.setTouchEmulationEnabled", {"enabled": mobile, "maxTouchPoints": 5 if mobile else 1})
        if mobile:
            s.send("Emulation.setUserAgentOverride", {"userAgent": MOBILE_UA, "platform": "iPhone"})
        else:
            s.send("Emulation.setUserAgentOverride", {"userAgent": self._ua})

    def _unemulate(self) -> None:
        for cmd, args in (("Emulation.clearDeviceMetricsOverride", {}),
                          ("Emulation.setTouchEmulationEnabled", {"enabled": False}),
                          ("Emulation.setUserAgentOverride", {"userAgent": self._ua})):
            try:
                self.cdp_session.send(cmd, args)
            except Exception:  # noqa: BLE001
                pass

    # ---- one page -----------------------------------------------------------------------------------------
    def _goto(self, url: str) -> dict:
        info = {"status": None, "final_url": url}
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            info["status"] = resp.status if resp else None
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        try:
            self.page.wait_for_load_state("load", timeout=15000)
        except Exception:  # noqa: BLE001
            info["load_timeout"] = True
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(self.wait)
        self._settle()
        info["final_url"] = self.page.url
        return info

    def _settle(self, quiet: float = 0.6, limit: float = 8.0) -> None:
        end, prev = time.time() + limit, -1
        while time.time() < end:
            try:
                st = self.page.evaluate("() => [document.readyState, document.getElementsByTagName('*').length]")
            except Exception:  # noqa: BLE001 - a navigation is still going on
                time.sleep(quiet)
                continue
            if st[0] == "complete" and st[1] == prev:
                return
            prev = st[1]
            time.sleep(quiet)

    def _load_lazy(self, step_wait: float = 0.35, limit: float = 20.0) -> dict:
        end, y, steps = time.time() + limit, 0, 0
        while time.time() < end:
            h, vh = self.page.evaluate("() => [document.documentElement.scrollHeight, innerHeight]")
            if y >= min(h, MAX_HEIGHT) - vh:
                break
            y += int(vh * 0.85)
            self.page.evaluate("(y) => window.scrollTo(0, y)", y)
            steps += 1
            time.sleep(step_wait)
        self.page.evaluate("() => window.scrollTo(0, 0)")
        pending = 0
        for _ in range(30):
            pending = self.page.evaluate("() => [...document.images].filter(i => !i.complete).length")
            if not pending:
                break
            time.sleep(0.1)
        return {"steps": steps, "images_pending": pending}

    def _stitch(self, frozen: dict):
        from PIL import Image
        p = self.page
        height, vh = min(int(frozen["height"]), MAX_HEIGHT), int(frozen["vh"])
        dpr = float(frozen.get("dpr") or 1)
        full, view, tiles = None, None, []
        for i, y in enumerate(tile_positions(height, vh)):
            p.evaluate("(s) => window.__flynnStitch.fixed(s)", i == 0)
            actual = p.evaluate("(y) => { window.scrollTo(0, y); return scrollY; }", y)
            time.sleep(0.15)
            if p.evaluate("() => window.__flynnStitch.late()"):
                time.sleep(0.05)
            png = self.cdp_session.send("Page.captureScreenshot", {"format": "png"})["data"]
            tile = Image.open(io.BytesIO(base64.b64decode(png))).convert("RGB")
            if full is None:
                full = Image.new("RGB", (tile.width, int(round(height * dpr))), "white")
                view = tile.copy()
            full.paste(tile, (0, int(round(actual * dpr))))
            tiles.append({"y": y, "scroll": actual})
        p.evaluate("() => { window.__flynnStitch.fixed(true); window.scrollTo(0, 0); }")
        return full, view, tiles

    def _shoot(self, folder: Path, width: int) -> dict:
        p = self.page
        out = folder / f"w{width}"
        out.mkdir(exist_ok=True)
        info: dict = {}
        try:
            _eval(p, STITCH_JS)
            info["prep"] = _eval(p, "window.__flynnStitch.prep()")
            info["lazy"] = self._load_lazy()
            frozen = _eval(p, "window.__flynnStitch.freeze()")
            info["frozen"] = frozen
            dom = _eval(p, RECORD_JS)
            try:
                tree = _eval(p, WALK_JS)
                (out / "pnode.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
                info["elements"] = len((tree or {}).get("nodes", []))
            except Exception as e:  # noqa: BLE001 - the screenshot still records the page
                info["elements_error"] = f"{type(e).__name__}: {e}"[:200]
            full, view, tiles = self._stitch(frozen)
            full.save(out / "full.png", optimize=True)
            view.save(out / "viewport.png", optimize=True)
            info["tiles"] = tiles
            dom["capture"] = info
            (out / "dom.json").write_text(json.dumps(dom, ensure_ascii=False), encoding="utf-8")
        finally:
            try:
                p.evaluate("() => window.__flynnStitch && window.__flynnStitch.restore()")
            except Exception:  # noqa: BLE001
                pass
        return {"width": width, "dir": str(out), "els": len(dom.get("els", [])), "doc": dom.get("doc"),
                "dpr": dom.get("dpr"), "unrolled": bool(info["prep"].get("unrolled")), "tiles": len(tiles),
                "truncated": frozen["height"] > MAX_HEIGHT, "elements": info.get("elements", 0)}

    def _record(self, n: int, url: str, note: str) -> None:
        sizes, html, title, navs = [], None, "", {}
        folder = None
        for w in self.widths:
            self._emulate(w)
            nav = self._goto(url)
            navs[str(w)] = nav
            if str(nav.get("final_url", "")).startswith("chrome-error:") and not nav.get("error"):
                nav["error"] = "the browser showed its own error page"
            if nav.get("error") and not nav.get("status"):
                self.blocked.append({"url": public_url(url), "width": w, "why": nav["error"]})
                continue
            if nav.get("status") and nav["status"] >= 400:
                self.blocked.append({"url": public_url(url), "width": w, "why": f"HTTP {nav['status']}"})
            title = self.page.title() or title
            if folder is None:
                folder = self.folder / "pages" / f"{n:03d}-{slug(title or urllib.parse.urlsplit(url).path)}"
                folder.mkdir(parents=True, exist_ok=True)
            sizes.append(self._shoot(folder, w))
            if html is None:
                html = scrub_html(_eval(self.page, "(() => {" + SERIALIZE_JS + "})()"))
                (folder / "page.html").write_text(html, encoding="utf-8")
        if not sizes:
            return
        page = {"n": n, "url": public_url(url), "title": title, "folder": str(folder), "sizes": sizes, "note": note,
                "final_url": public_url(navs[str(self.widths[0])].get("final_url", url)),
                "status": {k: v.get("status") for k, v in navs.items()},
                "time": dt.datetime.now().isoformat(timespec="seconds")}
        (folder / "meta.json").write_text(json.dumps({**page, "full_url": url}, ensure_ascii=False, indent=1), encoding="utf-8")
        self.pages.append(page)
        self._save("recording")

    # ---- run ----------------------------------------------------------------------------------------------
    def run(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._save("starting")
        state, error = "stopped", ""
        try:
            self._open()
            for i, (url, note) in enumerate(self.urls, 1):
                print(f"[{i}/{len(self.urls)}] {public_url(url)}", flush=True)
                signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(PAGE_LIMIT)           # one stuck page must not hang the session
                try:
                    self._record(len(self.pages) + 1, url, note)
                except Exception as e:  # noqa: BLE001 - one bad page does not end the session
                    self.blocked.append({"url": public_url(url), "width": None, "why": f"{type(e).__name__}: {str(e)[:200]}"})
                    print(f"   failed: {e}", flush=True)
                finally:
                    signal.alarm(0)
                    try:
                        self._unemulate()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            state, error = "error", f"{type(e).__name__}: {e}"
        finally:
            self._close()
            self._save(state, error)
        if error:
            raise SystemExit(error)
        return self.folder

    def _save(self, state: str, error: str = "") -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, **{k: v for k, v in self.cfg.items() if k != "url"}, "url": public_url(self.cfg["url"]),
                "state": state, "error": error, "pages": self.pages, "blocked": self.blocked}
        if state in ("stopped", "error"):
            data["stopped"] = dt.datetime.now().isoformat(timespec="seconds")
        (self.folder / "session.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="site_capture.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--system", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--urls", required=True, help="file with one URL per line, optional ' # note'")
    ap.add_argument("--widths", default="1440,390")
    ap.add_argument("--cdp", type=int, default=None, help="port of an already-running Chrome (opens and closes its own tab)")
    ap.add_argument("--wait", type=float, default=4.0, help="seconds to wait after load for lazy content")
    ap.add_argument("--headed", action="store_true", help="show the Playwright browser window")
    a = ap.parse_args(argv)
    urls = parse_urls(Path(a.urls).read_text(encoding="utf-8"))
    if not urls:
        raise SystemExit("no URLs in " + a.urls)
    widths = [int(w) for w in a.widths.split(",") if w.strip()]
    folder = Capture(a.system, a.session, urls, widths, cdp=a.cdp, wait=a.wait, headed=a.headed).run()
    s = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    print(f"{len(s['pages'])} pages, {len(s['blocked'])} problems -> {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
