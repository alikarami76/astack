import json
import subprocess
import sys
from pathlib import Path

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "scripts"))
import site_capture as sc  # noqa: E402

FIXTURE = FLYNN / "tests" / "fixtures" / "recorder" / "long.html"


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_ok(), reason="Playwright's Chromium is not installed")


def test_parse_urls_notes_and_comments():
    text = "# header\n\nhttps://a.test/x?q=1 # home page\nhttps://a.test/y\n"
    assert sc.parse_urls(text) == [("https://a.test/x?q=1", "home page"), ("https://a.test/y", "")]


def test_public_url_drops_query_and_scrub_blanks_tokens():
    assert sc.public_url("https://a.test/p/1?token=abc#x") == "https://a.test/p/1"
    html = sc.scrub_html('<div data-csrf-token="s3cr3t"></div> eyJabcdefgh12.eyJabcdefgh12.abcdefghij12')
    assert "s3cr3t" not in html and "eyJabc" not in html


def test_tile_positions_end_flush():
    assert sc.tile_positions(500, 900) == [0]
    assert sc.tile_positions(2000, 900) == [0, 900, 1100]


@needs_chromium
def test_capture_fixture_session_is_minable(tmp_path):
    folder = sc.Capture("demo", "fx", [(FIXTURE.as_uri(), "fixture")], [1440, 390], wait=0.5, root=tmp_path / "rec").run()
    s = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert s["state"] == "stopped" and len(s["pages"]) == 1 and not s["blocked"]
    page = s["pages"][0]
    assert [z["width"] for z in page["sizes"]] == [1440, 390]
    pdir = Path(page["folder"])
    assert (pdir / "page.html").exists() and (pdir / "meta.json").exists()
    for w in (1440, 390):
        for f in ("viewport.png", "full.png", "dom.json"):
            assert (pdir / f"w{w}" / f).exists()
    desk = json.loads((pdir / "w1440" / "dom.json").read_text(encoding="utf-8"))
    mob = json.loads((pdir / "w390" / "dom.json").read_text(encoding="utf-8"))
    assert desk["dpr"] == 1 and mob["dpr"] == 2 and 390 <= mob["viewport"][0] < 1440
    assert page["sizes"][0]["unrolled"]              # the inner scroller became one long page
    from PIL import Image
    with Image.open(pdir / "w1440" / "full.png") as im:
        assert im.height > 2000
    r = subprocess.run([sys.executable, str(FLYNN / "adapters" / "recorder" / "mine.py"), str(folder), "--no-store"],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    assert (folder / "mine.json").exists()
