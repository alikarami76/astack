import importlib.util
import json
import pathlib
import sys
import threading
import time
import urllib.request

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "recorder"))
import mine  # noqa: E402


@pytest.mark.parametrize("tok,expected", [
    ("ReportCard-dQesSG", ("ReportCard", None)),
    ("Button__filledPrimary-OvgwGb", ("Button", "filledPrimary")),
    ("Chip--small-kb12fC", ("Chip", "small")),
    ("hide-tablet", None),
    ("user-select-none", None),
    ("px-4", None),
    ("product-card", ("ProductCard", None)),
    ("main_ui_ds_buttons_button__button-primary", ("Button", "button-primary")),
])
def test_parse_class(tok, expected):
    assert mine.parse_class(tok) == expected


def _el(i, tag, cls, box, bg="rgba(0, 0, 0, 0)", color="rgb(66, 71, 80)", text="", border="0px", role="", h=None):
    return {"i": i, "p": -1, "tag": tag, "cls": cls, "id": "", "role": role, "type": "", "aria": "", "text": text,
            "box": box, "fixed": False, "disabled": False, "html": f"<{tag} class='{cls}'>{text} 09121234567</{tag}>",
            "s": {"color": color, "backgroundColor": bg, "borderTopWidth": border, "borderTopStyle": "solid",
                  "borderTopColor": "rgb(161, 163, 168)", "borderRadius": "8px", "boxShadow": "none",
                  "fontFamily": "Vazirmatn", "fontSize": "12px", "fontWeight": "700", "lineHeight": "24px",
                  "paddingTop": "8px", "paddingRight": "16px", "paddingBottom": "8px", "paddingLeft": "16px",
                  "gap": "normal", "marginTop": "0px", "marginBottom": "0px"}}


def _session(tmp_path):
    from PIL import Image
    s = tmp_path / "rec"
    d = s / "pages" / "001-home" / "w1440"
    d.mkdir(parents=True)
    Image.new("RGB", (2880, 1600), "white").save(d / "full.png")
    els = [_el(0, "button", "Button-MNNP44 Button__filledPrimary-Ovg", [10, 10, 120, 40], bg="rgb(15, 118, 110)", text="ذخیره"),
           _el(1, "button", "Button-MNNP44 Button__filledPrimary-Ovg", [10, 60, 120, 40], bg="rgb(15, 118, 110)", text="ثبت"),
           _el(2, "button", "Button-MNNP44 Button__outlinedPrimary-V1", [10, 110, 120, 32], border="1px", text="انصراف"),
           _el(3, "div", "ReportCard-dQesSG", [200, 10, 300, 200], bg="rgb(255, 255, 255)"),
           _el(4, "div", "ReportCard-dQesSG", [200, 240, 300, 200], bg="rgb(255, 255, 255)"),
           _el(5, "span", "hide-tablet", [0, 0, 50, 20], text="x")]
    for e in els[:3]:
        e["text"] = e["text"] or "t"
    (d / "dom.json").write_text(json.dumps({"dpr": 2, "doc": [1440, 800], "vars": {"--primary": "#0f766e"}, "fonts": ["Vazirmatn 400"], "els": els}))
    (s / "session.json").write_text(json.dumps({"id": "t1", "system": "demo", "description": "a test", "url": "https://x.test/",
                                                "pages": [{"n": 1, "title": "Home", "url": "https://x.test/", "sizes": [{"width": 1440, "dir": str(d), "dpr": 2}]}]}))
    return s


def test_mine_finds_components_variants_and_tokens(tmp_path):
    s = _session(tmp_path)
    r = mine.mine(s, store_rows=False)
    names = {c["name"]: c for c in r["components"]}
    assert "Button" in names and "ReportCard" in names and not any("Tablet" in n for n in names)
    btn = names["Button"]
    assert btn["count"] == 3 and {"filledPrimary", "outlinedPrimary"} <= set(btn["class_variants"])
    assert {v["look"] for v in btn["variants"]} == {"filled", "outlined"}
    assert all(pathlib.Path(v["crop"]).exists() for v in btn["variants"])
    snippet = pathlib.Path(btn["snippet"]).read_text()
    assert "09121234567" not in snippet                      # phone-like numbers are scrubbed
    prim = [t for t in r["tokens"] if t["kind"] == "color" and t["value"] == "#0f766e"]
    assert not prim or prim[0]["name"] == "--primary"         # a CSS variable name wins when the value matches


# ---------- API ----------

class FakeSession:
    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.id = "fake-1"
        self.folder = pathlib.Path(cfg["_folder"])
        self.pages, self.error, self.current = [], "", ""
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        def run():
            self.pages.append({"n": 1})
            self._stop.wait(5)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def stop(self, wait=0):
        self._stop.set()

    def capture_now(self):
        self.pages.append({"n": len(self.pages) + 1})

    def status(self):
        return {"id": self.id, "state": "recording", "error": "", "pages": len(self.pages), "current": "", "how": self.cfg["how"]}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_PANEL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLYNN_PANEL_CONTEXT", "0")
    spec = importlib.util.spec_from_file_location("panel_server", FLYNN / "adapters/penpot/panel/panel_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ra = mod.record_api
    ra.RECORDINGS.clear()
    monkeypatch.setattr(ra, "_session_cls", lambda: (lambda cfg: FakeSession({**cfg, "_folder": str(tmp_path)})))
    monkeypatch.setattr(ra, "mine_fn", lambda folder: {"components": [{"name": "Button", "count": 3, "pages": [1]}], "tokens": [1, 2]})
    built = []
    monkeypatch.setattr(ra, "build_fn", lambda folder, target, screens_only=False: built.append(
        {**target, **({"screens_only": True} if screens_only else {})}) or {"page": "pg1", "pageName": "Rec", "errors": []})
    mined = []
    real_mine = ra.mine_fn
    monkeypatch.setattr(ra, "mine_fn", lambda folder: mined.append(folder) or real_mine(folder))
    monkeypatch.setattr(ra, "current_page_id", lambda: "cur-page")
    monkeypatch.setattr(ra, "propose_fn", lambda change: "chg-1")
    srv = mod.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mod._mined = mined
    yield mod, f"http://127.0.0.1:{srv.server_port}", built
    srv.shutdown()


def post(base, path, body, ok=True):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if ok:
            raise
        return {"code": e.code, **json.loads(e.read())}


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


START = {"file": "f1", "url": "https://shop.example.com/", "system": "demo", "description": "the checkout",
         "target": {"mode": "current"}, "how": "watch", "sizes": ["desktop", "mobile"], "ask_flynn": False, "rights": True}


def test_start_capture_stop_builds_on_current_page(server):
    mod, base, built = server
    r = post(base, "/api/record/start", START)
    assert r["ok"] and r["state"] == "recording"
    assert post(base, "/api/record/capture", {"file": "f1"})["ok"]
    assert get(base, "/api/record/state?file=f1")["pages"] == 2
    assert post(base, "/api/record/stop", {"file": "f1"})["ok"]
    end = time.time() + 5
    while time.time() < end and get(base, "/api/record/state?file=f1")["state"] != "done":
        time.sleep(0.05)
    st = get(base, "/api/record/state?file=f1")
    assert st["state"] == "done" and st["result"]["pageName"] == "Rec" and st["result"]["components"] == ["Button"]
    assert built == [{"mode": "current", "page_id": "cur-page"}]
    kinds = [e.get("state") for e in get(base, "/api/history?file=f1")["events"] if e["t"] == "recording"]
    assert kinds[:4] == ["started", "mining", "building", "done"] and kinds[-1] == "library-proposed"
    assert st["last"]["system"] == "demo"


def test_new_page_target_and_validation(server):
    mod, base, built = server
    assert post(base, "/api/record/start", {**START, "file": "f2", "rights": False}, ok=False)["code"] == 400
    assert post(base, "/api/record/start", {**START, "file": "f2", "url": "http://localhost:9001/#/x"}, ok=False)["code"] == 400
    assert post(base, "/api/record/start", {**START, "file": "f2", "target": {"mode": "new"}}, ok=False)["code"] == 400
    r = post(base, "/api/record/start", {**START, "file": "f2", "how": "crawl", "max_pages": 999,
                                          "target": {"mode": "new", "page_name": "Checkout recording"}})
    assert r["ok"]
    rec = mod.record_api.RECORDINGS["f2"]
    assert rec.cfg["max_pages"] == 3 and rec.cfg["sizes"] == [1440, 390]
    assert rec.cfg["target"] == {"mode": "new", "page_name": "Checkout recording"}
    assert post(base, "/api/record/start", {**START, "file": "f2"}, ok=False)["code"] == 400   # one at a time
    post(base, "/api/record/stop", {"file": "f2"})


def test_recorder_switch_off(server, tmp_path):
    mod, base, built = server
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "features.json").write_text(json.dumps({"recorder": False}))
    assert post(base, "/api/record/start", {**START, "file": "f3"}, ok=False)["code"] == 503


def test_tile_positions_cover_the_page_and_end_flush():
    import recorder
    assert recorder.tile_positions(800, 900) == [0]
    assert recorder.tile_positions(1800, 900) == [0, 900]
    assert recorder.tile_positions(2000, 900) == [0, 900, 1100]
    ys = recorder.tile_positions(16000, 844)
    assert ys[0] == 0 and ys[-1] == 16000 - 844 and all(b - a <= 844 for a, b in zip(ys, ys[1:]))


def test_screens_only_skips_the_design_system(server):
    """the user can record screens without making a design system: no mining, no library change, no review."""
    mod, base, built = server
    body = {**START, "file": "f3", "system": "", "design_system": False, "ask_flynn": True}
    assert post(base, "/api/record/start", body)["ok"]
    rec = mod.record_api.RECORDINGS["f3"]
    assert rec.cfg["system"] == "shop.example.com" and rec.cfg["ask_flynn"] is False
    assert post(base, "/api/record/stop", {"file": "f3"})["ok"]
    end = time.time() + 5
    while time.time() < end and get(base, "/api/record/state?file=f3")["state"] != "done":
        time.sleep(0.05)
    st = get(base, "/api/record/state?file=f3")
    assert st["state"] == "done" and st["result"]["screens_only"] and st["result"]["components"] == []
    assert built == [{"mode": "current", "page_id": "cur-page", "screens_only": True}]
    assert mod._mined == []
    kinds = [e.get("state") for e in get(base, "/api/history?file=f3")["events"] if e["t"] == "recording"]
    assert "library-proposed" not in kinds and "mining" not in kinds
    assert st["last"]["design_system"] is False


def test_a_design_system_recording_still_needs_a_name(server):
    mod, base, built = server
    assert post(base, "/api/record/start", {**START, "file": "f4", "system": ""}, ok=False)["code"] == 400


def test_canvas_plan_screens_only_has_no_component_or_token_boards(tmp_path):
    import sys
    sys.path.insert(0, str(FLYNN / "adapters/recorder"))
    import canvas
    from PIL import Image
    shot = tmp_path / "pages/001-x/w1440"
    shot.mkdir(parents=True)
    Image.new("RGB", (1440, 1800), "white").save(shot / "full.png")
    (tmp_path / "session.json").write_text(json.dumps({"id": "2026-10-06-x", "system": "demo", "url": "https://x", "pages": [
        {"n": 1, "title": "تخفیف هوشمند", "url": "https://x/a", "sizes": [{"width": 1440, "dir": str(shot), "dpr": 1}]}]}))
    L = canvas.plan(tmp_path, None, 0, 0)
    names = [n.get("name") for n in L.nodes]
    assert "Components" not in names and "Tokens" not in names
    assert any(n["k"] == "board" and n["name"].startswith("01 ·") for n in L.nodes)
    title = next(n for n in L.nodes if n.get("name") == "01 title")
    label = next(n for n in L.nodes if n.get("name") == "01 label")
    assert title["dir"] == "rtl" and title["align"] == "right" and label["text"] == "01 · 1440px"
    board = next(n for n in L.nodes if n["k"] == "board")
    assert board["y"] < 400                      # screens start right under the title
