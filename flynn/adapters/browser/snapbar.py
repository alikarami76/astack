"""The snap bar in flynn's Chrome: a floating Snap button that the user presses to record the page as it is.

  bar = SnapBar(limit=3); bar.start()       put the bar on every page tab (and on every page they open later)
  bar.wait(timeout)  -> tab dict or None    the tab whose Snap was pressed, if any, within `timeout` seconds
  bar.set(mode, count)                      update what the bar shows (idle, busy, done)
  bar.stop()                                take the bar off every tab

How it works: one DevTools connection per page tab (websocket to Chrome's own debugging port on 127.0.0.1).
On each it adds a binding (window.__flynnSnapRequest, which the button calls) and registers snapbar.js to run
on every new document, so the bar comes back after each navigation. Pressing Snap raises a
Runtime.bindingCalled event on that connection. Nothing goes over the network, and no page can reach flynn.

The bar is removed from the page before a capture (hide_js) and put back after (show_js); capture code runs
those through Selenium. Tabs that belong to flynn itself (the canvas, the panel) never get the bar.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
JS = (HERE / "snapbar.js").read_text(encoding="utf-8")
BINDING = "__flynnSnapRequest"
OWN = ("http://localhost:9001", "http://127.0.0.1:9001", "http://127.0.0.1:877", "http://localhost:877",
       "about:", "chrome://", "chrome-extension://", "devtools://")

hide_js = "return window.__flynnSnapBar ? window.__flynnSnapBar.hide() : false"
show_js = "return window.__flynnSnapBar ? window.__flynnSnapBar.show() : false"


def _port() -> int:
    import capture
    return capture.port()


class _Tab:
    """One DevTools connection to one page tab."""

    def __init__(self, info: dict, events: "queue.Queue[dict]"):
        import websocket
        self.info, self.events, self._id = info, events, 0
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.alive = True
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}

    def send(self, method: str, params: dict | None = None, wait: bool = True) -> dict:
        with self._lock:
            self._id += 1
            mid = self._id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not wait:
            return {}
        end = time.time() + 5
        while time.time() < end:
            if mid in self._pending:
                return self._pending.pop(mid)
            self.pump()
        return {}

    def pump(self) -> None:
        import websocket
        try:
            raw = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return
        except Exception:  # noqa: BLE001 - the tab closed
            self.alive = False
            return
        msg = json.loads(raw)
        if "id" in msg:
            self._pending[msg["id"]] = msg
        elif msg.get("method") == "Runtime.bindingCalled" and msg["params"].get("name") == BINDING:
            self.events.put({"tab": self.info["id"], "payload": msg["params"].get("payload", "")})

    def install(self) -> None:
        self.send("Page.enable")          # scripts for new documents run only with the Page domain on
        self.send("Runtime.addBinding", {"name": BINDING})
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": JS})
        self.send("Runtime.evaluate", {"expression": JS})

    def evaluate(self, expr: str) -> None:
        self.send("Runtime.evaluate", {"expression": expr}, wait=False)

    def close(self) -> None:
        try:
            self.send("Runtime.evaluate", {"expression": "window.__flynnSnapBar && window.__flynnSnapBar.hide()"}, wait=False)
            self.send("Runtime.removeBinding", {"name": BINDING}, wait=False)
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        self.alive = False


class SnapBar:
    def __init__(self, limit: int | None = None):
        self.limit = limit
        self.tabs: dict[str, _Tab] = {}
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.count = 0
        self.mode = "idle"

    def _page_tabs(self) -> list[dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{_port()}/json", timeout=2) as r:
            tabs = json.load(r)
        return [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                and not t.get("url", "").startswith(OWN)]

    def refresh(self) -> None:
        """Attach to page tabs opened since the last call; drop closed ones."""
        try:
            live = {t["id"]: t for t in self._page_tabs()}
        except Exception:  # noqa: BLE001 - Chrome is gone; the caller notices on its own
            return
        for tid in [t for t in self.tabs if t not in live or not self.tabs[t].alive]:
            self.tabs.pop(tid).close()
        for tid, info in live.items():
            if tid not in self.tabs:
                try:
                    tab = _Tab(info, self.events)
                    tab.install()
                    self.tabs[tid] = tab
                    tab.evaluate(self._set_expr())
                except Exception:  # noqa: BLE001 - a tab that refuses is skipped and tried again next time
                    pass

    def start(self) -> "SnapBar":
        self.refresh()
        return self

    def wait(self, timeout: float = 0.8) -> dict | None:
        """Pump every tab for up to `timeout` seconds; return {tab, payload} for the first Snap press."""
        end = time.time() + timeout
        while time.time() < end:
            if not self.tabs:
                time.sleep(0.2)
            for t in list(self.tabs.values()):
                t.pump()
            try:
                return self.events.get_nowait()
            except queue.Empty:
                pass
        return None

    def _set_expr(self) -> str:
        label = f"{self.count} of {self.limit}" if self.limit else ""
        return f"window.__flynnSnapBar && window.__flynnSnapBar.set({json.dumps({'mode': self.mode, 'count': self.count, 'label': label})})"

    def set(self, mode: str, count: int | None = None, label: str | None = None) -> None:
        self.mode = mode
        if count is not None:
            self.count = count
        expr = self._set_expr() if label is None else (
            f"window.__flynnSnapBar && window.__flynnSnapBar.set({json.dumps({'mode': mode, 'count': self.count, 'label': label})})")
        for t in list(self.tabs.values()):
            try:
                t.evaluate(expr)
            except Exception:  # noqa: BLE001
                t.alive = False

    def stop(self) -> None:
        for t in self.tabs.values():
            t.close()
        self.tabs.clear()
