import json

import pymupdf
import pytest

from pdfsmith.compose import compose
from pdfsmith.errors import RenderError
from pdfsmith.render import render

pytestmark = pytest.mark.chrome


def _render(d, **kw):
    c = compose(d)
    b = d / "build"
    return render(c.html_path, b / "raw.pdf", b / "geometry.json", **kw), b


def test_render_basic(make_doc):
    geo, b = _render(make_doc({"template": "custom", "meta": {"title": "R"}}, content="# One\n\ntext\n\n# Two\n\nmore\n"))
    assert geo["pages"] >= 1 and (b / "raw.pdf").stat().st_size > 5000
    assert [h["text"] for h in geo["headings"]] == ["One", "Two"] and geo["overflow"] == []
    assert json.loads((b / "geometry.json").read_text())["pages"] == geo["pages"]
    assert pymupdf.open(b / "raw.pdf").page_count == geo["pages"]
    assert geo["chrome"]


def test_render_persian_extracts_words(make_doc):
    geo, b = _render(make_doc({"template": "custom", "lang": "fa"}, content="# گزارش\n\nمی‌خواهیم نرخ BuyBox را بررسی کنیم.\n"))
    text = pymupdf.open(b / "raw.pdf")[0].get_text()
    assert "می‌خواهیم" in text and "BuyBox" in text


def test_render_detects_overflow(make_doc):
    geo, _ = _render(make_doc({"template": "custom"}, html='<h1>X</h1><div style="width:150%">wide</div>'))
    assert len(geo["overflow"]) == 1 and geo["overflow"][0]["tag"] == "div" and geo["overflow"][0]["text"] == "wide"


def test_render_detects_rtl_overflow(make_doc):
    geo, _ = _render(make_doc({"template": "custom", "lang": "fa"}, html='<h1>ت</h1><div style="width:150%">پهن</div>'))
    assert geo["overflow"] and geo["overflow"][0]["tag"] == "div"


def test_render_allow_overflow(make_doc):
    geo, _ = _render(make_doc({"template": "custom"}, html='<h1>X</h1><div class="ok" style="width:150%">wide</div>'), allow_overflow=[".ok"])
    assert geo["overflow"] == []


def test_render_blocks_network(make_doc):
    geo, _ = _render(make_doc({"template": "custom"}, html='<h1>N</h1><img src="https://example.com/x.png">'))
    assert geo["blocked"] == ["https://example.com/x.png"]


def test_render_timeout(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<html><body>no paged.js here</body></html>")
    with pytest.raises(RenderError, match="did not finish"):
        render(page, tmp_path / "x.pdf", tmp_path / "g.json", timeout_s=3)


def test_render_rtl_content_flows_onto_next_page(make_doc):
    # Paged.js 0.4.3 only looks for overflow to the right; in RTL it used to drop the rest of the text off the page.
    body = "\n\n".join(f"بند {i}: این متن فقط برای پر کردن صفحه نوشته شده است تا به صفحهٔ بعد برسد." for i in range(60))
    geo, b = _render(make_doc({"template": "custom", "lang": "fa"}, content="# گزارش\n\n" + body + "\n\nپایان‌نامه\n"))
    doc = pymupdf.open(b / "raw.pdf")
    assert geo["pages"] >= 2 and doc.page_count == geo["pages"] and geo["overflow"] == []
    assert "پایان‌نامه" in doc[-1].get_text()


LTR_PHRASES = ["Hello, World", "R&D", "Admin Panel", "https://example.com/p?id=42&x=7", "C:\\data\\q3.sql", "/usr/local/bin"]


def test_render_rtl_keeps_ltr_phrase_order(make_doc):
    # Extraction returns each run in visual order, so a reversed phrase reads "World ,Hello" here.
    content = (
        "# گزارش\n\n"
        "پیام Hello, World را دیدیم.\n\n"
        "تیم R&D کار کرد.\n\n"
        "پنل **Admin** Panel آماده است.\n\n"
        "نشانی https://example.com/p?id=42&x=7 است.\n\n"
        "فایل C:\\\\data\\\\q3.sql و پوشهٔ /usr/local/bin را ببینید.\n"
    )
    geo, b = _render(make_doc({"template": "custom", "lang": "fa"}, content=content))
    text = pymupdf.open(b / "raw.pdf")[0].get_text()
    missing = [p for p in LTR_PHRASES if p not in text]
    assert not missing, (missing, text)


SYMBOL_PHRASES = ["#BlackFriday", "@acme", "--verbose", "$HOME", ".env", "Hello, World!", "Hello World?"]


def test_render_rtl_keeps_leading_symbols_and_marks_with_their_phrase(make_doc):
    # Before the fix these came out as "BlackFriday#", "verbose--" and "!Hello, World".
    content = (
        "# آزمون\n\n"
        "تگ #BlackFriday را ببین.\n\n"
        "حساب @acme فعال است.\n\n"
        "گزینهٔ --verbose را بزن.\n\n"
        "متغیر $HOME و فایل .env را ببین.\n\n"
        "پیام Hello, World! را دید.\n\n"
        "آیا Hello World? را دید.\n"
    )
    geo, b = _render(make_doc({"template": "custom", "lang": "fa"}, content=content))
    text = pymupdf.open(b / "raw.pdf")[0].get_text()
    missing = [p for p in SYMBOL_PHRASES if p not in text]
    assert not missing, (missing, text)


def test_render_records_failed_local_load_and_page_error(make_doc):
    html = '<h1>R</h1><img src="img/missing.png"><script>throw new Error("boom")</script>'
    d = make_doc({"template": "custom"}, html=html)
    geo, _ = _render(d)
    assert [f["url"] for f in geo["failed"]] == [(d / "img" / "missing.png").resolve().as_uri()]
    assert geo["failed"][0]["error"] and any("boom" in e for e in geo["page_errors"])


def test_render_blocked_request_is_not_also_failed(make_doc):
    geo, _ = _render(make_doc({"template": "custom"}, html='<h1>N</h1><img src="https://example.com/x.png">'))
    assert geo["blocked"] == ["https://example.com/x.png"] and geo["failed"] == []


def test_render_loads_relative_assets_in_raw_html(make_doc):
    html = "<h1>A</h1><img src='img/a.png'><div style=\"background: url('img/a.png'); height: 10px\"></div>"
    d = make_doc({"template": "custom"}, html=html)
    (d / "img").mkdir()
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), 0).save(d / "img" / "a.png")
    geo, _ = _render(d)
    assert geo["failed"] == [] and geo["page_errors"] == []


def test_render_goto_failure_is_render_error(tmp_path):
    with pytest.raises(RenderError, match="Chrome failed"):
        render(tmp_path / "missing.html", tmp_path / "x.pdf", tmp_path / "g.json", timeout_s=10)


def test_render_pdf_failure_is_render_error(make_doc, monkeypatch):
    from playwright.sync_api import Error, Page

    def boom(self, **kw):
        raise Error("Printing failed")

    monkeypatch.setattr(Page, "pdf", boom)
    with pytest.raises(RenderError, match="Printing failed"):
        _render(make_doc({"template": "custom"}, content="# x\n\ny\n"))
