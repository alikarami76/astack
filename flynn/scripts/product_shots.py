#!/usr/bin/env python3
"""Screenshot every page of a design system's product map from a running build, in flynn's own Chrome.

  product_shots.py --system admin-panel --base http://localhost:3333 [--only /orders] [--limit 300] [--wait 6]

flynn's Chrome (adapters/browser/capture.py, port 9223) must be open and signed in by the person themselves (a demo
account for a local build). For every screen row without a parameter, it opens base + route, waits for the page to
settle, and saves a full-page JPEG (1440 wide, at most 3600 px tall) to systems/<system>/screens/. Pages with a
parameter (/orders/:id) are filled from links seen on the pages visited before (a real id from the demo data). A page
that sends the browser elsewhere (sign-in, not eligible) is recorded as such, not shot. Screen rows get the picture's
address in `screenshots`; nothing is marked verified.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parent
sys.path[:0] = [str(HERE), str(FLYNN / "kb")]
import store  # noqa: E402

PORT = 9223


class Cdp:
    """flynn's Chrome through its DevTools protocol (no chromedriver, so a Chrome update never breaks it): one tab."""

    def __init__(self):
        import urllib.request
        import websocket
        # its own tab, so it never takes over the one the person is using (signed-in cookies are shared)
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
        page = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.tab = page["id"]
        self.sock = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=120, suppress_origin=True)
        self.n = 0

    def execute_cdp_cmd(self, method, params=None):
        self.n += 1
        mid = self.n
        self.sock.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.sock.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})

    def get(self, url):
        self.execute_cdp_cmd("Page.navigate", {"url": url})

    @property
    def current_url(self):
        return self.execute_script("return location.href")

    def execute_script(self, js):
        body = js[len("return "):] if js.startswith("return ") else js
        r = self.execute_cdp_cmd("Runtime.evaluate", {"expression": body, "returnByValue": True})
        return (r.get("result") or {}).get("value")


def driver():
    return Cdp()


STATE_JS = r"""function () {
  const t = (document.body.innerText || "").replace(/\s+/g, " ");
  const has = re => re.test(t);
  if (/lock/.test(location.pathname) || has(/قفل شده|دسترسی شما محدود|حساب شما مسدود/)) return "locked";
  if (has(/امکان (استفاده|دسترسی|شرکت)[^.]{0,40}(ندارید|وجود ندارد)|شرایط (لازم|شرکت)[^.]{0,30}را ندارید|واجد شرایط نیستید|فعال نیست/)) return "not-eligible";
  if (has(/خطایی رخ داد|مشکلی پیش آمد|دوباره تلاش کنید|خطا در دریافت/)) return "error";
  if (document.querySelector('[class*="skeleton" i], [class*="loading" i], [class*="spinner" i]') && t.length < 400) return "loading";
  if (has(/(موردی|کالایی|سفارشی|اطلاعاتی|داده‌ای|نتیجه‌ای|درخواستی|تراکنشی|پیامی|گزارشی)[^.]{0,20}(یافت نشد|وجود ندارد|ثبت نشده|ندارید)|هنوز[^.]{0,40}(ندارید|نشده)|خالی است/)) return "empty";
  const rows = document.querySelectorAll("table tbody tr, [role=row]").length;
  return rows > 1 ? "with-data" : "page";
}"""


def slug(route: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-") or "home"


def pattern(route: str) -> re.Pattern:
    return re.compile("^" + re.sub(r":[A-Za-z_][\w]*", r"[^/?#]+", re.escape(route).replace(r"\:", ":")) + r"/?(\?.*)?$")


def shot(d, out: Path) -> tuple[int, int]:
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    m = d.execute_cdp_cmd("Page.getLayoutMetrics", {})
    h = int(min(3600, max(900, (m.get("cssContentSize") or m.get("contentSize") or {}).get("height", 900))))
    r = d.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": 72, "captureBeyondViewport": True,
                                                     "clip": {"x": 0, "y": 0, "width": 1440, "height": h, "scale": 1}})
    d.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
    im.save(out, "JPEG", quality=72, optimize=True)
    return im.size


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--system", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--wait", type=float, default=6.0)
    ap.add_argument("--missing", action="store_true", help="only pages without a picture, or caught loading")
    a = ap.parse_args(argv)
    conn = store.connect()
    cur = conn.execute("SELECT * FROM screens WHERE system = ?", (a.system,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    rows = [r for r in rows if "#" not in r["route"] and (not a.only or r["route"].startswith(a.only))]
    if a.missing:
        def needs(r):
            try:
                sh = json.loads(r.get("screenshots") or "[]")
            except ValueError:
                sh = []
            return not sh or (isinstance(sh[0], dict) and sh[0].get("state") == "loading")
        rows = [r for r in rows if needs(r)]
    plain = [r for r in rows if ":" not in r["route"]]
    params = {r["route"]: (pattern(r["route"]), r) for r in rows if ":" in r["route"]}
    found: dict[str, str] = {}
    outdir = FLYNN / "systems" / a.system / "screens"
    outdir.mkdir(parents=True, exist_ok=True)
    base = a.base.rstrip("/")
    d = driver()
    report = {"shot": [], "redirected": [], "failed": [], "no_example": []}
    queue = [(r["route"], r["route"], r) for r in plain]
    done = set()
    while queue and len(report["shot"]) < a.limit:
        route, path, row = queue.pop(0)
        if route in done:
            continue
        done.add(route)
        try:
            d.get(base + path)
            time.sleep(a.wait)
            now = d.current_url
            if not now.startswith(base) or ("/login" in now and "login" not in route):
                report["redirected"].append({"route": route, "to": now[:120]})
                continue
            f = outdir / f"{slug(route)}.jpg"
            size = shot(d, f)
            state = d.execute_script("return (" + STATE_JS + ")()") or "unknown"
            hrefs = d.execute_script("return [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))") or []
            for h in hrefs:
                hp = str(h).split(base)[-1]
                for pr, (rx, prow) in params.items():
                    if pr not in found and rx.match(hp):
                        found[pr] = hp
                        queue.append((pr, hp, prow))
            rel = f"/sysfile/{a.system}/screens/{f.name}"
            # what the picture shows: a demo account often has no data, so many pages show their empty, locked or
            # not-eligible state; Flynn must know the populated state isn't pictured (the record's states say it).
            store.upsert(conn, "screen", {"system": a.system, "route": route, "tier": row.get("tier") or "inferred",
                                          "screenshots": [{"src": rel, "state": state, "account": "demo",
                                                           "captured": time.strftime("%Y-%m-%d")}]}, origin="scan")
            report["shot"].append({"route": route, "url": path, "size": size, "state": state})
        except Exception as e:  # noqa: BLE001 - one page never stops the rest
            report["failed"].append({"route": route, "error": f"{type(e).__name__}: {e}"[:200]})
    report["no_example"] = [r for r in params if r not in found]
    try:                                                   # close the tab this run opened
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{d.tab}", timeout=10).read()
    except Exception:  # noqa: BLE001
        pass
    conn.commit()
    store.export(conn, etype="screen")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}))
    (FLYNN / "systems" / a.system / "screens" / "_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
