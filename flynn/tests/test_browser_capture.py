import importlib.util
import json
import os
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "browser" / "capture.py"
spec = importlib.util.spec_from_file_location("browser_capture", ADAPTER)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@pytest.mark.parametrize("url,product", [
    ("https://seller.shop.example.com/rules", "admin-panel"),
    ("https://www.shop.example.com/product/p-1/", "website"),
    ("https://shop.example.com/", "website"),
    ("https://admin.shop.example.com/rules", "admin"),
    ("https://example.com/", "other"),
    ("file:///tmp/x.html", "other"),
])
def test_product_of(url, product):
    assert capture.product_of(url) == product


def test_slugify_keeps_persian_and_is_never_empty():
    assert capture.slugify("صفحهٔ قیمت، ردیف باز") .startswith("صفحه")
    assert capture.slugify("!!!") == "snap"
    assert len(capture.slugify("a" * 200)) <= 48


def test_scrub_html_blanks_tokens_and_jwts():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    html = f'<div data-auth-token="abc123secret" data-x="keep"><a href="/x?t={jwt}">x</a><i csrf_token=\'zz\'></i></div>'
    out = capture.scrub_html(html)
    assert "abc123secret" not in out and jwt not in out and "zz" not in out
    assert 'data-x="keep"' in out and "[scrubbed-jwt]" in out


PAGE = """<!doctype html><html><head><meta name="csrf-token" content="SECRET-CSRF"><title>Test panel</title>
<script>window.__STATE__ = {"session": "SECRET-SESSION"};</script></head>
<body style="height:3000px"><h1>قیمت</h1><input type="hidden" name="k" value="SECRET-HIDDEN">
<div id="tip"></div><script>document.getElementById('tip').textContent = 'tooltip open';</script></body></html>"""


@pytest.fixture
def browser(tmp_path, monkeypatch):
    pytest.importorskip("selenium")
    if not Path(capture.CHROME_BIN).exists():
        pytest.skip("Chrome not installed")
    monkeypatch.setenv("FLYNN_SNAPS_DIR", str(tmp_path / "snaps"))
    monkeypatch.setenv("FLYNN_CHROME_PROFILE", str(tmp_path / "flynn-home" / "chrome-profile"))
    monkeypatch.setenv("FLYNN_CHROME_PORT", "9391")
    page = tmp_path / "panel.html"
    page.write_text(PAGE, encoding="utf-8")
    capture.open_browser(page.as_uri(), headless=True)
    yield page
    capture.close_browser()


def test_snap_end_to_end(browser, tmp_path):
    try:
        meta = capture.snap(note="price row expanded")
    except Exception as e:  # driver download needs the network
        pytest.skip(f"chromedriver unavailable: {e}")
    folder = Path(meta["folder"])
    assert {"viewport.png", "full.png", "page.html", "meta.json"} <= set(os.listdir(folder))
    html = (folder / "page.html").read_text(encoding="utf-8")
    assert "tooltip open" in html and "قیمت" in html          # live DOM, not the source
    for secret in ("SECRET-CSRF", "SECRET-SESSION", "SECRET-HIDDEN"):
        assert secret not in html
    assert json.loads((folder / "meta.json").read_text())["title"] == "Test panel"
    assert capture.running()                                      # snapping never closes Chrome
    import store
    ref = store.get(store.connect(), meta["ref_id"])
    assert ref["status"] == "candidate" and ref["pattern"] == "other" and "#flynn-snap=" in ref["source_url"]
    assert capture.list_snaps()[0]["note"] == "price row expanded"


def test_public_url_drops_queries_and_fragments():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("capture_pu", Path(__file__).resolve().parent.parent / "adapters" / "browser" / "capture.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.public_url("https://admin.example.com/shop/members/?title=%D9%BE%D8%AA&member_id=1") == "https://admin.example.com/shop/members/"
    assert m.public_url("https://auth.x/realms/a/auth?state=abc#x") == "https://auth.x/realms/a/auth"
