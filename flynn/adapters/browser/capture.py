#!/usr/bin/env python3
"""flynn browser capture: a real Chrome, driven by Selenium, that the user browses himself. flynn snaps whatever
tab is in front (screenshots plus the live HTML) as reference material for the current Admin Panel,
website or admin panel.

  capture.py open [--url URL] [--headless]     start flynn's Chrome (its own profile; the user logs in himself once)
  capture.py snap [--note "..."] [--product admin-panel|website|admin|other] [--mhtml]
  capture.py watch [--minutes 30] [--limit N] [--auto]   put a floating Snap button on every page; snap when
                                               the user presses it (--auto: snap on every navigation instead)
  capture.py list [--limit 20]                 recent snaps
  capture.py status                            is the browser running, and which tab is in front
  capture.py close                             quit flynn's Chrome

Each snap goes to <snaps>/<date>/<time>-<slug>/:
  viewport.png   what the user sees, at the display's pixel ratio (2x on a Retina screen)
  full.png       the whole page, beyond the viewport
  page.html      the live DOM, including open tooltips and expanded rows, with secrets scrubbed (see below)
  page.mhtml     only with --mhtml: a self-contained offline copy. It keeps the page's scripts, so it may hold
                 tokens; it is still local-only
  meta.json      url, title, time, product, note, viewport and pixel ratio
and is recorded in flynn's store as a `candidate` reference (pattern = product), which the user approves or rejects.

Safety:
  - Chrome runs with its own profile (~/.flynn/chrome-profile, mode 700). flynn never reads cookies,
    passwords or storage, and never copies the profile.
  - page.html drops inline script bodies (they often embed session data), csrf/token meta tags,
    password and hidden input values, and anything shaped like a JWT or a long token in token-like attributes.
  - Snaps can show real seller and customer data: they live under $FLYNN/refs/, which git ignores. Never
    upload or share them without asking the user.

Environment: FLYNN_SNAPS_DIR, FLYNN_CHROME_PROFILE, FLYNN_CHROME_PORT (default 9223), FLYNN_CHROME_BIN."""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FLYNN / "kb"))

CHROME_BIN = os.environ.get("FLYNN_CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def snaps_dir() -> Path:
    return Path(os.environ.get("FLYNN_SNAPS_DIR", FLYNN / "refs" / "snaps"))


def profile_dir() -> Path:
    return Path(os.environ.get("FLYNN_CHROME_PROFILE", Path.home() / ".flynn" / "chrome-profile"))


def port() -> int:
    return int(os.environ.get("FLYNN_CHROME_PORT", "9223"))


def _state_file() -> Path:
    return profile_dir().parent / f"browser-{port()}.json"


# ---------- pure helpers (tested) ----------

def product_of(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if "seller" in host:
        return "admin-panel"
    if "admin" in host or "backoffice" in host or "panel" in host:
        return "admin"
    site = os.environ.get("FLYNN_SITE_HOST", "shop.example.com").lower()
    if host == site or host.endswith("." + site):
        return "website"
    return "other"


def slugify(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^\w؀-ۿ]+", "-", text.strip().lower()).strip("-")
    return (s[:limit].rstrip("-")) or "snap"


JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
SECRET_ATTR = re.compile(r"""(\s(?:[\w:-]*(?:token|csrf|xsrf|auth|session|secret|api[-_]?key)[\w:-]*)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def scrub_html(html: str) -> str:
    """Second pass after the in-browser scrub: blank token-like attribute values and anything shaped like a JWT."""
    html = SECRET_ATTR.sub(lambda m: f'{m.group(1)}{m.group(2)}[scrubbed]{m.group(2)}', html)
    return JWT.sub("[scrubbed-jwt]", html)


# In the page: clone the live DOM and strip what may carry secrets before serializing.
SERIALIZE_JS = r"""
const root = document.documentElement.cloneNode(true);
for (const b of root.querySelectorAll('#__flynn-snap')) b.remove();
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


HIDE_BAR = "return window.__flynnSnapBar ? window.__flynnSnapBar.hide() : false"
SHOW_BAR = "return window.__flynnSnapBar ? window.__flynnSnapBar.show() : false"

# ---------- browser ----------

def _tabs(timeout: float = 1.0) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port()}/json", timeout=timeout) as r:
        tabs = json.load(r)
    return [t for t in tabs if t.get("type") == "page" and not t.get("url", "").startswith(("devtools://", "chrome-extension://"))]


def running() -> bool:
    try:
        _tabs()
        return True
    except Exception:
        return False


def front_tab() -> dict:
    """The most recently focused page (DevTools lists pages most recent first)."""
    tabs = _tabs()
    if not tabs:
        raise SystemExit("flynn's Chrome has no open tab")
    return tabs[0]


def open_browser(url: str | None = None, headless: bool = False) -> str:
    if running():
        return f"already running on port {port()}"
    prof = profile_dir()
    prof.mkdir(parents=True, exist_ok=True)
    os.chmod(prof, 0o700)
    os.chmod(prof.parent, 0o700)
    args = [CHROME_BIN, f"--remote-debugging-port={port()}", f"--user-data-dir={prof}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1440,960",
            "--disable-popup-blocking"]  # panels open details in pop-up windows; snaps follow the front window
    if headless:
        args.append("--headless=new")
    args.append(url or "about:blank")
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(75):
        if running():
            _state_file().write_text(json.dumps({"pid": p.pid, "port": port()}))
            return f"opened (pid {p.pid}, port {port()}, profile {prof})"
        time.sleep(0.2)
    p.terminate()
    raise SystemExit("Chrome did not start its DevTools port in 15s")


def _driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    # Skip any chromedriver on PATH: it is often older than Chrome (e.g. Homebrew's). Selenium Manager fetches
    # the matching one into ~/.cache/selenium.
    drv = SeleniumManager().binary_paths(["--browser", "chrome", "--skip-driver-in-path"])["driver_path"]
    o = Options()
    o.debugger_address = f"127.0.0.1:{port()}"
    return webdriver.Chrome(options=o, service=Service(drv))


def snap(note: str = "", product: str | None = None, mhtml: bool = False, record: bool = True, tab_id: str | None = None) -> dict:
    if not running():
        raise SystemExit("flynn's Chrome is not running; start it with: capture.py open")
    tab = {"id": tab_id} if tab_id else front_tab()
    d = _driver()
    bar = False
    try:
        if tab["id"] in d.window_handles:
            d.switch_to.window(tab["id"])
        bar = bool(d.execute_script(HIDE_BAR))      # the snap bar never appears in a snap
        url, title = d.current_url, d.title
        m = d.execute_cdp_cmd("Page.getLayoutMetrics", {})
        dpr = d.execute_script("return window.devicePixelRatio")
        now = _dt.datetime.now()
        folder = snaps_dir() / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M%S')}-{slugify(note or title or url)}"
        folder.mkdir(parents=True, exist_ok=True)
        view = d.execute_cdp_cmd("Page.captureScreenshot", {"format": "png"})
        (folder / "viewport.png").write_bytes(base64.b64decode(view["data"]))
        full = d.execute_cdp_cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        (folder / "full.png").write_bytes(base64.b64decode(full["data"]))
        (folder / "page.html").write_text(scrub_html(d.execute_script(SERIALIZE_JS)), encoding="utf-8")
        if mhtml:
            (folder / "page.mhtml").write_text(d.execute_cdp_cmd("Page.captureSnapshot", {"format": "mhtml"})["data"], encoding="utf-8")
    finally:
        try:
            if bar:
                d.execute_script(SHOW_BAR)
        finally:
            d.quit()  # detaches chromedriver; flynn's Chrome keeps running
    prod = product or product_of(url)
    meta = {"url": url, "title": title, "time": now.isoformat(timespec="seconds"), "product": prod, "note": note,
            "viewport": m.get("cssLayoutViewport"), "content": m.get("cssContentSize"), "pixel_ratio": dpr,
            "files": sorted(p.name for p in folder.iterdir())}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    meta["folder"] = str(folder)
    if record:
        meta["ref_id"] = record_reference(meta, folder)
    return meta


def public_url(url: str) -> str:
    """The store (and its git export) keeps only scheme, host and path: queries can hold seller names,
    national ids or sign-in state. The full URL stays in the local snap's meta.json."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}" if u.scheme else url.split("?")[0].split("#")[0]


def record_reference(meta: dict, folder: Path) -> str:
    import store
    c = store.connect()
    rid = store.upsert(c, "reference", {
        "source_url": f"{public_url(meta['url'])}#flynn-snap={folder.parent.name}/{folder.name}",
        "title": meta["note"] or meta["title"] or public_url(meta["url"]),
        "pattern": meta["product"],
        "image_path": str(folder / "viewport.png"),
        "why_it_works": f"Snap of the live {meta['product']} page ({meta['time']}). Candidate until the user approves it.",
        "status": "candidate",
    }, tags=["snap", meta["product"]], origin="browser-capture")
    try:
        store.export(c, etype="reference")
    except Exception as e:  # never lose a snap because the export guard refused
        print(f"note: reference stored, export skipped: {e}", file=sys.stderr)
    return rid


def watch(minutes: float = 30, interval: float = 1.0, settle: float = 1.5, product: str | None = None,
          auto: bool = False, limit: int | None = None) -> int:
    """Snap when the user presses Snap on the floating bar in flynn's Chrome (or Alt+Shift+S). With auto=True, snap
    each time the front tab settles on a new URL instead (the old way). Stops after `minutes`, after `limit`
    snaps, or on Ctrl-C / SIGTERM."""
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    end, last, count = time.time() + minutes * 60, None, 0
    bar = None
    if not auto:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import snapbar
        bar = snapbar.SnapBar(limit=limit).start()
        print("press Snap on the bar in flynn's Chrome (or Alt+Shift+S) to snap a page", flush=True)
    try:
        refreshed = time.time()
        while not stop["now"] and time.time() < end and not (limit and count >= limit):
            if not running():
                print("browser closed; stopping", flush=True)
                break
            if bar:
                if time.time() - refreshed > 2:
                    bar.refresh(); refreshed = time.time()
                ev = bar.wait(interval)
                if not ev:
                    continue
                bar.set("busy")
                s = snap(note="", product=product, tab_id=ev["tab"])
                count += 1
                bar.set("done", count=count)
                print(f"snap {count}: {s['title'] or s['url']} -> {s['folder']}", flush=True)
                continue
            try:
                url = front_tab()["url"]
            except BaseException:
                print("browser closed; stopping", flush=True)
                break
            if url != last and not url.startswith(("about:", "chrome://")):
                time.sleep(settle)
                if front_tab()["url"] == url:
                    s = snap(note="", product=product)
                    count += 1
                    print(f"snap {count}: {s['title'] or s['url']} -> {s['folder']}", flush=True)
                    last = url
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if bar:
            bar.stop()
    return count


def list_snaps(limit: int = 20) -> list[dict]:
    metas = sorted(snaps_dir().glob("*/*/meta.json"), reverse=True)[:limit]
    return [json.loads(p.read_text(encoding="utf-8")) | {"folder": str(p.parent)} for p in metas]


def close_browser() -> str:
    sf = _state_file()
    if sf.exists():
        pid = json.loads(sf.read_text())["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        sf.unlink()
        return f"closed (pid {pid})"
    return "not running" if not running() else "running, but not started by flynn; close it from Chrome"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open"); o.add_argument("--url"); o.add_argument("--headless", action="store_true")
    s = sub.add_parser("snap"); s.add_argument("--note", default=""); s.add_argument("--product"); s.add_argument("--mhtml", action="store_true")
    w = sub.add_parser("watch"); w.add_argument("--minutes", type=float, default=30); w.add_argument("--interval", type=float, default=1.0); w.add_argument("--product")
    w.add_argument("--auto", action="store_true", help="snap on every navigation instead of on the Snap button")
    w.add_argument("--limit", type=int, help="stop after this many snaps")
    l = sub.add_parser("list"); l.add_argument("--limit", type=int, default=20)
    sub.add_parser("status")
    sub.add_parser("close")
    a = ap.parse_args(argv)
    if a.cmd == "open":
        print(open_browser(a.url, a.headless))
    elif a.cmd == "snap":
        print(json.dumps(snap(a.note, a.product, a.mhtml), ensure_ascii=False, indent=1))
    elif a.cmd == "watch":
        print(f"watching for {a.minutes} min; {watch(a.minutes, a.interval, product=a.product, auto=a.auto, limit=a.limit)} snaps")
    elif a.cmd == "list":
        for m in list_snaps(a.limit):
            print(f"{m['time']}  {m['product']:<13} {m['note'] or m['title']}  ->  {m['folder']}")
    elif a.cmd == "status":
        print(json.dumps({"running": True, "port": port(), "front": {k: front_tab()[k] for k in ("title", "url")}}, ensure_ascii=False)
              if running() else json.dumps({"running": False}))
    elif a.cmd == "close":
        print(close_browser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
