import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parent.parent / "adapters" / "review" / "review.py"
spec = importlib.util.spec_from_file_location("review", MOD)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>t</title></head>
<body><main><section data-shot="hero"><h1>تخفیف ویژه</h1><p>متن</p></section></main></body></html>"""


@pytest.fixture
def server(tmp_path):
    run = tmp_path / "run"
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope")
    httpd = review.serve(run, port=0, open_browser=False)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield run, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_store_add_reply_resolve_and_markdown_mirror(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    c = review.add_comment(run, "  عنوان کوتاه‌تر  ", {"selector": "h1", "dx": .5, "dy": .5, "shot": "hero"})
    assert c["n"] == 1 and c["text"] == "عنوان کوتاه‌تر" and c["status"] == "open"
    review.update(run, 1, reply="انجام شد", author="flynn")
    review.update(run, 1, status="resolved")
    data = review.load(run)
    assert data["comments"][0]["status"] == "resolved" and data["comments"][0]["replies"][0]["author"] == "flynn"
    md = (run / "feedback.md").read_text(encoding="utf-8")
    assert "عنوان کوتاه‌تر" in md and "[resolved]" in md
    with pytest.raises(ValueError):
        review.add_comment(run, "   ", {})
    with pytest.raises(KeyError):
        review.update(run, 9, status="resolved")


def test_server_injects_overlay_and_blocks_paths_outside_the_run(server):
    run, base = server
    html = urllib.request.urlopen(base + "/page/index.html").read().decode()
    assert '/__review/overlay.js' in html
    assert urllib.request.urlopen(base + "/__review/overlay.js").status == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/../secret.txt")
    assert e.value.code in (403, 404)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/%2e%2e/secret.txt")
    assert e.value.code in (403, 404)


def test_ui_end_to_end_comment_reply_and_live_reload(server):
    pw = pytest.importorskip("playwright.sync_api")
    run, base = server
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        pg = b.new_page()
        pg.goto(base + "/page/index.html")
        pg.wait_for_selector("#flynn-review .fr-bar")
        pg.keyboard.press("c")
        pg.click("h1")
        pg.fill(".fr-pop textarea", "عنوان را کوتاه‌تر کن")
        pg.click(".fr-save")
        pg.wait_for_selector(".fr-pin:not(.draft)")
        c = review.load(run)["comments"][0]
        assert c["text"] == "عنوان را کوتاه‌تر کن" and c["anchor"]["shot"] == "hero" and "h1" in c["anchor"]["selector"]
        # flynn replies from the CLI; the thread shows it within the polling interval
        review.main(["reply", str(run), "1", "کوتاه شد"])
        pg.wait_for_timeout(2500)
        pg.click(".fr-pin:not(.draft)")
        assert "کوتاه شد" in pg.inner_text(".fr-pop.thread")
        # a re-render reloads the page by itself
        time.sleep(1.1)
        (run / "page" / "index.html").write_text(PAGE.replace("متن", "متن تازه"), encoding="utf-8")
        pg.wait_for_function("document.body.innerText.includes('متن تازه')", timeout=6000)
        b.close()
