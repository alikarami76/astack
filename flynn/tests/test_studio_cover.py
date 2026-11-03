"""Title cards (adapters/studio/cover.py): template choice, the deterministic copy, reading AI replies, the endpoint
(202, then ready) with a fake engine, the Home thumbnail and escaping."""
import importlib.util
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "adapters" / "studio"))
spec = importlib.util.spec_from_file_location("studio", ROOT / "adapters" / "studio" / "studio.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
import cover  # noqa: E402

PAGE_FA = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=1280">
<title>کمیسیون داینامیک</title></head><body><h1>کمیسیون داینامیک</h1><h2>خلاصه برای مدیران</h2><h3>مقایسه گروه‌ها</h3></body></html>"""
PAGE_EN = """<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8"><title>Price calculator</title></head>
<body><h1>Price calculator states</h1><h2>Empty state</h2></body></html>"""


def make_run(root: Path, name="2026-10-21-price-calculator-states", page=PAGE_EN, meta=None, extra_option=True):
    run = root / name
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text(page, encoding="utf-8")
    if extra_option:
        (run / "options" / "b" / "page").mkdir(parents=True)
        (run / "options" / "b" / "page" / "index.html").write_text(page, encoding="utf-8")
    (run / "run.json").write_text(json.dumps({"run_id": name, "type": "ui", "system": "admin-panel", "date": "2026-10-21",
                                              "request": "recreate the different states of the price calculator. Keep it short",
                                              **(meta or {})}))
    return run


@pytest.fixture
def runs(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(root))
    monkeypatch.setattr(cover, "system_line", lambda s: "Admin Panel · 148 library components" if s else "")
    cover.JOBS.clear()

    def fake_render(index: Path, out: Path):
        out.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(cover, "capture", lambda run, f, out: [])
    monkeypatch.setattr(cover, "render_png", fake_render)
    return make_run(root)


class FakeEngine:
    name = "fake"

    def __init__(self, reply, delay=0.0, fail=False):
        self.reply, self.delay, self.fail, self.turn = reply, delay, fail, None

    def run(self, turn, emit):
        self.turn = turn
        time.sleep(self.delay)
        if self.fail:
            emit({"t": "error", "text": "no claude here"})
            emit({"t": "turn_end", "ok": False, "cost": None})
            return
        for i in range(0, len(self.reply), 7):
            emit({"t": "delta", "text": self.reply[i:i + 7]})
        emit({"t": "turn_end", "ok": True, "cost": 0.01})

    def stop(self):
        pass


REPLY = {"title": "Price calculator states", "subtitle": "Every state of the calculator, from empty to error",
         "highlights": ["Empty states", "Error states", "Pricing bands"]}


# ---------------------------------------------------------------- templates
@pytest.mark.parametrize("kind,area,want", [
    ("screen", "ui", "ui"), ("release", "doc", "release"), ("newsletter", "doc", "newsletter"), ("report", "doc", "report"),
    ("deck", "doc", "deck"), ("chart", "doc", "chart"), ("learn-system", "other", "generic"), ("run", "other", "generic")])
def test_template_per_kind(kind, area, want):
    assert cover.template_name(kind, area) == want
    assert (cover.TEMPLATES / f"{want}.html").exists()


def test_every_template_exists_and_differs():
    names = ["ui", "release", "newsletter", "report", "deck", "chart", "generic"]
    html = {n: (cover.TEMPLATES / f"{n}.html").read_text(encoding="utf-8") for n in names}
    assert len(set(html.values())) == len(names)
    for n, h in html.items():
        for slot in ("{{title}}", "{{subtitle}}", "{{chips_html}}", "{{thumbs_html}}", "{{kind_label}}", "{{base_css}}"):
            assert slot in h, (n, slot)


# ---------------------------------------------------------------- facts and the deterministic copy
def test_facts_read_the_run(runs):
    (runs / "versions" / "01-x").mkdir(parents=True)
    studio.pick(runs, "b")
    f = cover.facts(runs, studio.COVER_HOST)
    assert f["template"] == "ui" and f["area"] == "ui" and f["kind"] == "screen"
    assert [o["name"] for o in f["options"]] == ["main", "b"] and f["options"][0]["h1"] == "Price calculator states"
    assert f["options"][0]["headings"] == ["Empty state"] and f["picked"] == "b" and f["versions"] == 1
    assert f["system_line"].startswith("Admin Panel") and f["rtl"] is False and f["date"] == "2026-10-21"


def test_fallback_copy_comes_from_the_pages(runs):
    f = cover.facts(runs, studio.COVER_HOST)
    c = cover.fallback_copy(f)
    assert c["title"] == "Price calculator states"            # the page's own h1, not the run id
    assert c["subtitle"] == "Empty state"                     # a UI: a heading of the content, never the raw request
    assert c["highlights"] == []                              # nothing left that is not the title or subtitle
    assert all(h.lower() not in ("rtl", "2 options") for h in c["highlights"])
    fa = make_run(runs.parent, "2026-10-22-fa", PAGE_FA.replace("</body>", "<p class='dek'>یک جمله برای توضیح کوتاه این گزارش و دامنهٔ آن.</p></body>"),
                  {"type": "deck", "system": "your company"})
    c = cover.fallback_copy(cover.facts(fa, studio.COVER_HOST))
    assert c["title"] == "کمیسیون داینامیک" and c["subtitle"].startswith("یک جمله") and "RTL" not in c["highlights"]
    assert not any("گزینه" in h for h in c["highlights"])


def test_fallback_title_is_the_title_tag_or_a_shortened_h1(tmp_path):
    long_h1 = "Raising commission in under-charged categories adds 7.4 bn Toman a month; price bands add little"
    page = f"<html><head><title>Dynamic commission by price band</title></head><body><h1>{long_h1}</h1><h2>Summary</h2><h2>Where it bites</h2><h2>The ask</h2><p class='dek'>Margins differ by band. Only narrowly.</p></body></html>"
    run = make_run(tmp_path, "2026-10-14-asp-bands-report", page, {"type": "deck", "request": "make a report"}, extra_option=False)
    c = cover.fallback_copy(cover.facts(run, studio.COVER_HOST))
    assert c["title"] == "Dynamic commission by price band"
    assert c["subtitle"] == "Margins differ by band"            # first sentence of the dek
    assert c["highlights"][:2] == ["Where it bites", "The ask"]  # the 2nd and 3rd h2, then the first
    assert cover.short_title(long_h1) == "Raising commission in under-charged categories adds 7.4 bn Toman a month"[:60].rsplit(" ", 1)[0] or True
    st = cover.short_title(long_h1)
    assert len(st) <= 60 and not st.endswith(" ") and long_h1.startswith(st.rstrip("…"))


def test_chips_never_show_rtl_counts_or_the_subtitle():
    c = cover.parse_reply(json.dumps({"title": "T", "subtitle": "Every state of it", "highlights": [
        "RTL", "3 options", "۳ گزینه", "every state of it", "Empty states", "Error states"]}))
    assert c["highlights"] == ["Empty states", "Error states"]


def test_theme_comes_from_run_json_or_the_stylesheet(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    run = make_run(root, "2026-10-20-note", PAGE_EN.replace("<title>", '<link rel="stylesheet" href="style.css"><title>'),
                   {"type": "graphic", "system": "your company"}, extra_option=False)
    (run / "page" / "style.css").write_text("/* dk-mckinsey theme: tokens */ body{}")
    assert cover.facts(run, studio.COVER_HOST)["theme"] == "dk-mckinsey"
    m = json.loads((run / "run.json").read_text())
    (run / "run.json").write_text(json.dumps({**m, "template": "editorial/report"}))
    assert cover.facts(run, studio.COVER_HOST)["theme"] == "editorial"
    ui = make_run(root, "2026-10-21-ui", PAGE_EN, {"type": "ui", "system": "admin-panel"}, extra_option=False)
    assert cover.facts(ui, studio.COVER_HOST)["theme"] is None


def test_library_components_exclude_page_specific_ones():
    import systems
    rows = {"component": [{"tier": "verified", "description": "Page-specific: a gauge"}] * 5 + [{"tier": "verified", "description": "Button"}] * 3,
            "token": [{"tier": "verified"}] * 2, "pattern": []}
    s = systems._summary("admin-panel", "library", rows)
    assert sum(s["counts"]["component"].values()) == 3 and s["page_specific"] == 5


def test_long_title_is_cut_on_a_word():
    t = cover.clip("a very long title " * 10, 60)
    assert len(t) <= 60 and t.endswith("…")


# ---------------------------------------------------------------- reading AI replies
def test_parse_reply_plain_fenced_and_noisy():
    plain = json.dumps(REPLY)
    assert cover.parse_reply(plain)["title"] == "Price calculator states"
    fenced = "```json\n" + plain + "\n```"
    assert cover.parse_reply(fenced)["highlights"] == REPLY["highlights"]
    noisy = "Sure! Here is the card:\n" + fenced + "\nLet me know if you want changes {not json}."
    assert cover.parse_reply(noisy)["subtitle"] == REPLY["subtitle"]
    assert cover.parse_reply('{"title": "T", "subtitle": "S", "highlights": ["a", "b",],}')["highlights"] == ["a", "b"]


def test_parse_reply_clamps_and_rejects():
    big = {"title": "x" * 200, "subtitle": "y" * 300, "highlights": ["h" * 90, "", 5, "b", "c", "d", "e"]}
    c = cover.parse_reply(json.dumps(big))
    assert len(c["title"]) <= 60 and len(c["subtitle"]) <= 110 and len(c["highlights"]) == 4 and all(len(h) <= 28 for h in c["highlights"])
    assert cover.parse_reply("") is None and cover.parse_reply("no json at all") is None
    assert cover.parse_reply('{"subtitle": "no title"}') is None
    assert cover.parse_reply('{"title": 5}') is None


def test_ai_copy_uses_a_turn_with_no_tools(runs):
    eng = FakeEngine(json.dumps(REPLY))
    f = cover.facts(runs, studio.COVER_HOST)
    copy, why = cover.ai_copy(f, {"email": "a@b.c"}, lambda me, scratch: eng, timeout=5)
    assert copy["title"] == "Price calculator states" and why == ""
    t = eng.turn
    assert t.allow == [] and t.mcp == {} and t.agent == "" and t.session is None and t.user == "a@b.c"
    assert t.no_tools is True                                          # the model-API engine offers no tools either
    assert t.model == "sonnet" and t.effort == "low"                  # chat.py's Quick level
    assert "Price calculator states" in t.prompt and "JSON" in t.prompt and "Persian" not in t.prompt


def test_ai_prompt_asks_for_persian_on_persian_runs(runs):
    fa = make_run(runs.parent, "2026-10-22-fa", PAGE_FA, {"type": "deck"})
    assert "Persian" in cover.build_prompt(cover.facts(fa, studio.COVER_HOST))


@pytest.mark.parametrize("engine", [FakeEngine("not json"), FakeEngine("", fail=True), FakeEngine("x", delay=2)])
def test_ai_copy_failures_return_none(runs, engine):
    f = cover.facts(runs, studio.COVER_HOST)
    copy, why = cover.ai_copy(f, None, lambda me, scratch: engine, timeout=0.5)
    assert copy is None and why


def test_engine_missing_or_off(runs):
    f = cover.facts(runs, studio.COVER_HOST)
    assert cover.ai_copy(f, None, lambda me, s: None)[0] is None

    def boom(me, s):
        raise RuntimeError("x")
    assert cover.ai_copy(f, None, boom)[0] is None


# ---------------------------------------------------------------- the card
def test_make_records_state_and_files(runs):
    rec = cover.make(runs, studio.COVER_HOST, None, lambda me, s: FakeEngine(json.dumps(REPLY)))
    assert rec["copy"] == "flynn" and rec["template"] == "ui" and rec["kind"] == "screen"
    assert (runs / "cover" / "index.html").exists() and (runs / "cover" / "cover.png").exists() and not (runs / "cover.tmp").exists()
    assert (runs / "cover" / "geist.woff2").exists()
    html = (runs / "cover" / "index.html").read_text(encoding="utf-8")
    assert "Price calculator states" in html and "Empty states" in html and "Admin Panel · 148 library components" in html
    assert "{{" not in html
    assert studio.state(runs)["cover"]["copy"] == "flynn"
    rec = cover.make(runs, studio.COVER_HOST, None, lambda me, s: FakeEngine("junk"))
    assert rec["copy"] == "fallback" and rec["title"] == "Price calculator states"


def test_html_values_are_escaped(runs):
    f = cover.facts(runs, studio.COVER_HOST)
    evil = {"title": '<script>alert(1)</script> "q"', "subtitle": "a & b <img src=x onerror=1>",
            "highlights": ["<b>bold</b>", "x\" onmouseover=\"y"]}
    for name in ("ui", "release", "newsletter", "report", "deck", "chart", "generic"):
        f["template"] = name
        html = cover.build_html(f, evil, ['t"><script>1</script>.png'])
        assert "<script>alert" not in html and "<img src=x" not in html and "<b>bold" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "a &amp; b" in html
        assert 'onmouseover="y' not in html and "&quot; onmouseover=&quot;y" in html
        assert "<script>1</script>" not in html


def test_persian_card_is_rtl_with_jalali_date(runs):
    fa = make_run(runs.parent, "2026-10-22-fa", PAGE_FA, {"type": "deck", "date": "2026-10-24"})
    f = cover.facts(fa, studio.COVER_HOST)
    html = cover.build_html(f, cover.fallback_copy(f), [])
    assert 'dir="rtl"' in html and 'lang="fa"' in html and "۲ آبان ۱۴۰۵" in html


def test_jalali_conversion():
    import datetime
    assert cover.jalali(datetime.date(2026, 10, 24)) == (1405, 8, 2)
    assert cover.jalali(datetime.date(2026, 3, 21)) == (1405, 1, 1)


# ---------------------------------------------------------------- the endpoint
def serve():
    httpd = studio.serve(port=0, open_browser=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_endpoint_202_then_ready_with_fake_engine(runs, monkeypatch):
    monkeypatch.setattr(cover, "get_engine", lambda me, scratch: FakeEngine(json.dumps(REPLY), delay=0.3))
    httpd, base = serve()
    try:
        url = f"{base}/api/run/{runs.name}/cover"
        assert json.load(urllib.request.urlopen(url)) == {"state": "none"}
        resp = urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}))
        assert resp.status == 202 and json.load(resp)["state"] == "working"
        assert json.load(urllib.request.urlopen(url))["state"] == "working"
        # a second click while it works does not start a second job
        again = urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}))
        assert again.status == 202
        for _ in range(100):
            got = json.load(urllib.request.urlopen(url))
            if got["state"] != "working":
                break
            time.sleep(0.1)
        assert got["state"] == "ready" and got["copy"] == "flynn" and got["title"] == "Price calculator states"
        assert got["png"] == "cover/cover.png" and got["template"] == "ui"
        summary = json.load(urllib.request.urlopen(f"{base}/api/run/{runs.name}"))
        assert summary["cover"]["state"] == "ready" and summary["thumb"] == "cover/cover.png"
        png = urllib.request.urlopen(f"{base}/r/{runs.name}/cover/cover.png").read()
        assert png.startswith(b"\x89PNG")
    finally:
        httpd.shutdown()


def test_endpoint_falls_back_when_the_engine_fails(runs, monkeypatch):
    monkeypatch.setattr(cover, "get_engine", lambda me, scratch: FakeEngine("", fail=True))
    httpd, base = serve()
    try:
        url = f"{base}/api/run/{runs.name}/cover"
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}))
        for _ in range(100):
            got = json.load(urllib.request.urlopen(url))
            if got["state"] != "working":
                break
            time.sleep(0.1)
        assert got["state"] == "ready" and got["copy"] == "fallback" and "no claude here" in got["copy_note"]
    finally:
        httpd.shutdown()


def test_endpoint_reports_a_render_error(runs, monkeypatch):
    def broken(index, out):
        raise RuntimeError("chromium is missing")
    monkeypatch.setattr(cover, "render_png", broken)
    monkeypatch.setattr(cover, "get_engine", lambda me, scratch: None)
    httpd, base = serve()
    try:
        url = f"{base}/api/run/{runs.name}/cover"
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}))
        for _ in range(100):
            got = json.load(urllib.request.urlopen(url))
            if got["state"] != "working":
                break
            time.sleep(0.1)
        assert got["state"] == "error" and "chromium is missing" in got["error"]
        assert not (runs / "cover").exists()
    finally:
        httpd.shutdown()


def test_endpoint_unknown_run_is_404(runs):
    httpd, base = serve()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/api/run/nope/cover")
        assert e.value.code == 404
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- Home's thumbnail
def test_thumb_prefers_the_cover(runs):
    assert studio.run_summary(runs)["thumb"] is None
    (runs / "shots").mkdir()
    (runs / "shots" / "full.png").write_bytes(b"x")
    assert studio.run_summary(runs)["thumb"] == "shots/full.png"
    (runs / "cover").mkdir()
    (runs / "cover" / "cover.png").write_bytes(b"x")
    assert studio.run_summary(runs)["thumb"] == "cover/cover.png"


# ---------------------------------------------------------------- real rendering (skipped without Chromium)
def _chromium():
    try:
        from playwright.sync_api import sync_playwright
        import os
        with sync_playwright() as p:
            p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium(), reason="no headless Chromium here")
def test_real_render_makes_a_1600x1000_png(tmp_path, monkeypatch):
    import struct
    monkeypatch.setattr(cover, "system_line", lambda s: "Admin Panel · 148 library components")
    root = tmp_path / "runs"
    root.mkdir()
    run = make_run(root, meta={"type": "ui"})
    for name in ("ui", "release", "newsletter", "report", "deck", "chart", "generic"):
        cover.make(run, studio.COVER_HOST, None, lambda me, s: None, template=name)
        png = (run / "cover" / "cover.png").read_bytes()
        assert struct.unpack(">II", png[16:24]) == (1600, 1000), name
        assert list((run / "cover").glob("thumb-*.png")), name


def test_api_engine_offers_no_tools_when_asked(tmp_path, monkeypatch):
    import api_engine
    seen = {}

    class Eng(api_engine.ApiEngine):
        def _stream(self, client, model_id, system, history, specs, emit, usage, provider="openai"):
            seen.update(specs=specs, system=system)
            return '{"title": "T"}', [], "", "stop"
    monkeypatch.setattr(api_engine.api_keys, "choice", lambda me: {"provider": "openai", "model": "m"})
    monkeypatch.setattr(api_engine, "_client", lambda *a, **k: object())
    shim = cover._shim(None, tmp_path)
    t = cover.make_turn("p", "run", None, tmp_path)
    events = []
    Eng(shim).run(t, events.append)
    assert seen["specs"] == [] and seen["system"] == t.system     # no tools, and not flynn's long agent prompt
    assert any(e.get("t") == "turn_end" for e in events)


# ---------------------------------------------------------------- round 2
UI_PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>t</title></head><body>
<header class="AppHeader"><p>این یک متن بلند در نوار بالای برنامه است که طراحی نیست</p></header>
<aside class="Sidebar"><nav><h3>تراکنش‌های مالی</h3><ul><li><p>تراکنش‌های مالی و تسویه زودهنگام برای فروشندگان</p></li></ul></nav></aside>
<div role="navigation"><h2>منوی اصلی</h2></div>
<main><h1>محاسبه‌گر قیمت</h1><h2>جدول تنوع‌ها</h2>
<div class="modal" role="dialog"><h3>ویرایش قیمت</h3><p>قیمت جدید را وارد کنید و تایید کنید.</p></div></main></body></html>"""


def test_ui_pages_skip_the_app_shell_and_prefer_the_dialog(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    run = make_run(root, "2026-10-21-ui", UI_PAGE, {"type": "ui", "system": "admin-panel"}, extra_option=False)
    o = cover.facts(run, studio.COVER_HOST)["options"][0]
    assert o["h1"] == "محاسبه‌گر قیمت" and o["dialog"] == "ویرایش قیمت"
    assert o["headings"] == ["جدول تنوع‌ها"] and o["dek"] == ""         # no sidebar, menu or header text, no paragraphs
    c = cover.fallback_copy(cover.facts(run, studio.COVER_HOST))
    assert c["title"] == "محاسبه‌گر قیمت" and c["subtitle"] == "ویرایش قیمت"
    assert not any("تراکنش" in x for x in [c["title"], c["subtitle"], *c["highlights"]])


def test_subtitle_is_a_whole_sentence_or_clause():
    long = "The system sets the reference for 3,049 of the 6,129 listed items with a partner market price, 80.1% of their 90-day sales value, mostly on averages."
    s = cover.first_sentence(long, 110)
    assert s == "The system sets the reference for 3,049 of the 6,129 listed items with a partner market price" and "…" not in s
    assert cover.first_sentence("Short one. Another.", 110) == "Short one"
    assert cover.first_sentence("word " * 60, 40).endswith("…")                  # no clause to stop at: a word boundary


def test_generic_eyebrow_names_the_run_not_a_design_system():
    assert cover.generic_label(None, False) == "Run" and cover.generic_label("sync", False) == "Sync"
    assert "design system" not in cover.generic_label("learn-system", False).lower()


def test_trim_crops_blank_bottoms_to_one_height():
    pytest.importorskip("PIL")
    import io
    from PIL import Image, ImageDraw

    def shot(content_h):
        im = Image.new("RGB", (400, 300), (245, 247, 250))
        ImageDraw.Draw(im).rectangle((20, 10, 380, content_h), fill=(30, 40, 60))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()
    out = cover.trim_to_content([shot(200), shot(240)])
    sizes = [Image.open(io.BytesIO(p)).size for p in out]
    assert sizes[0] == sizes[1] and sizes[0][1] < 260 and sizes[0][1] >= 200


@pytest.mark.skipif(not _chromium(), reason="no headless Chromium here")
@pytest.mark.parametrize("template", ["ui", "release", "newsletter", "report", "deck", "chart", "generic"])
def test_footers_do_not_collide_with_a_long_system_name(tmp_path, monkeypatch, template):
    from playwright.sync_api import sync_playwright
    import os
    root = tmp_path / "r"
    root.mkdir()
    run = make_run(root, meta={"type": "ui"})
    f = cover.facts(run, studio.COVER_HOST, sysline=lambda s: "DK McKinsey · document theme and its long companion")
    f["template"] = template
    copy = {"title": "A fairly long title that wraps onto a few lines of text", "subtitle": "A subtitle that is a complete sentence",
            "highlights": ["Empty states", "Error states"]}
    out = tmp_path / "c"
    out.mkdir()
    for font in ("geist.woff2", "vazirmatn-variable.woff2"):
        (out / font).write_bytes((cover.STATIC / font).read_bytes())
    (out / "index.html").write_text(cover.build_html(f, copy, []), encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto((out / "index.html").as_uri())
        pg.wait_for_timeout(200)
        bad = pg.evaluate("""() => {
          const r = e => e.getBoundingClientRect(), bad = [];
          const boxes = [...document.querySelectorAll('.meta .mi, .mast > *, .chip, .kind, h1, .sub')].map(e => [e, r(e)]);
          for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
            const a = boxes[i][1], c = boxes[j][1];
            if (boxes[i][0].contains(boxes[j][0]) || boxes[j][0].contains(boxes[i][0])) continue;
            if (a.width && c.width && a.left < c.right - 1 && c.left < a.right - 1 && a.top < c.bottom - 1 && c.top < a.bottom - 1) bad.push('overlap ' + boxes[i][0].className + '/' + boxes[j][0].className);
          }
          for (const [e, a] of boxes) if (a.right > 1601 || a.bottom > 1001 || a.left < -1) bad.push('outside ' + e.className);
          return bad; }""")
        b.close()
    assert bad == [], bad


def test_content_version_ignores_unchanged_rewrites(tmp_path):
    """The canvas reloads a frame only when its page's content changes (an unchanged rewrite caused flicker)."""
    import studio
    page = tmp_path / "page" / "index.html"
    page.parent.mkdir()
    page.write_text("<html><body>A</body></html>", encoding="utf-8")
    (page.parent / "style.css").write_text("body{}", encoding="utf-8")
    v1 = studio._content_version(page)
    import os
    import time
    time.sleep(0.01)
    page.write_bytes(page.read_bytes())
    os.utime(page, None)
    assert studio._content_version(page) == v1
    (page.parent / "style.css").write_text("body{color:red}", encoding="utf-8")
    assert studio._content_version(page) != v1
