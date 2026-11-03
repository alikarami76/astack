import email
import importlib.util
import re
from email import policy
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "html"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"html_{name}_dn", ADAPTER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


new_page = _load("new_page")
render = _load("render")
email_pack = _load("email_pack")


def _render_or_skip(fn, *a, **k):
    pytest.importorskip("playwright.sync_api")
    try:
        return fn(*a, **k)
    except Exception as e:  # chromium not installed in this environment
        pytest.skip(f"chromium unavailable: {e}")


def _pdf_pages(path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except ImportError:
        return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


@pytest.mark.parametrize("template,sheet", [("deck", "deck.css"), ("newsletter", "newsletter.css")])
def test_scaffold_copies_every_theme_sheet(tmp_path, template, sheet):
    page = new_page.new_page(tmp_path / "run", template=template)
    html = (page / "index.html").read_text(encoding="utf-8")
    assert (page / "style.css").exists() and (page / sheet).exists()
    assert f'href="{sheet}"' in html and 'href="style.css"' in html


def test_theme_sheets_add_no_colours_outside_root():
    for sheet in ("deck.css", "newsletter.css"):
        css = (ADAPTER / "themes" / "editorial" / sheet).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", css), f"{sheet} must use :root tokens only"


def test_deck_renders_one_png_and_one_pdf_page_per_slide(tmp_path):
    page = new_page.new_page(tmp_path / "run", template="deck")
    html = (page / "index.html").read_text(encoding="utf-8")
    n = html.count('class="slide')
    r = _render_or_skip(render.render_slides, page / "index.html", tmp_path / "run" / "shots", pdf="deck.pdf")
    from PIL import Image
    pngs = sorted((tmp_path / "run" / "shots" / "slides").glob("*.png"))
    assert r["slides"] == n == len(pngs) == 8
    assert all(Image.open(p).size == (2560, 1440) for p in pngs)
    assert _pdf_pages(tmp_path / "run" / "deck.pdf") == n
    assert r["slideOverflow"] == [] and r["font"]


def test_slide_overflow_is_flagged(tmp_path):
    page = new_page.new_page(tmp_path / "run", template="deck")
    idx = page / "index.html"
    long = "<p>" + "متن خیلی بلند برای آزمایش سرریز اسلاید. " * 200 + "</p>"
    idx.write_text(idx.read_text(encoding="utf-8").replace("</section>", long + "</section>", 1), encoding="utf-8")
    r = _render_or_skip(render.render_slides, idx, tmp_path / "run" / "shots")
    assert r["slideOverflow"] and r["slideOverflow"][0]["slide"] == 1 and not r["ok"]


def test_blocks_parse_order_text_and_links():
    html = ('<main><header data-shot="head"><b>عنوان</b></header>'
            '<section data-shot="lead" data-link="news/1"><h1>تیتر</h1><img alt="تصویر"><script>x=1</script><p>متن</p></section>'
            '<section data-shot="s2" data-link="[لینک]"><p>دوم</p></section></main>')
    b = email_pack.blocks(html)
    assert [x["name"] for x in b] == ["head", "lead", "s2"]
    assert b[1]["text"] == "تیتر تصویر متن" and "x=1" not in b[1]["text"]
    assert email_pack.resolve_link(b[1]["link"], "https://ex.com/nl/") == "https://ex.com/nl/news/1"
    assert email_pack.resolve_link(b[2]["link"], "https://ex.com/") == ""
    assert email_pack.resolve_link("https://a.b/c", None) == "https://a.b/c"


def test_newsletter_eml_embeds_every_block_as_a_cid_image(tmp_path):
    run = tmp_path / "run"
    page = new_page.new_page(run, template="newsletter")
    from PIL import Image
    Image.new("RGB", (1200, 600), "white").save(page / "img" / "[lead].png")
    r = _render_or_skip(render.render, page / "index.html", run / "shots", width=720, pad=0)
    assert r["ok"], r
    out = email_pack.pack(run, "خبرنامهٔ آزمایشی", to="a@example.com", link_base="https://example.com/")
    msg = email.message_from_bytes(out.read_bytes(), policy=policy.default)
    assert msg["X-Unsent"] == "1" and msg["Subject"] == "خبرنامهٔ آزمایشی"
    html_part = msg.get_body(preferencelist=("html",))
    body = html_part.get_content()
    refs = set(re.findall(r'src="cid:([^"]+)"', body))
    parts = {p["Content-ID"].strip("<>") for p in msg.walk() if p.get_content_type() == "image/png"}
    blocks = email_pack.blocks((page / "index.html").read_text(encoding="utf-8"))
    assert refs == parts and len(parts) == len(blocks) == 7
    assert 'dir="rtl"' in body and "display:flex" not in body and "grid" not in body
    assert msg.get_body(preferencelist=("plain",)).get_content().strip()
