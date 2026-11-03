import json

import pytest

from pdfsmith.compose import compose, page_css
from pdfsmith.errors import InputError


def _info(d):
    return json.loads((d / "build" / "compose.json").read_text(encoding="utf-8"))


def test_compose_markdown_en(make_doc):
    d = make_doc({"template": "custom", "meta": {"title": "Hello"}}, content="# Hello\n\nWorld 42.\n")
    c = compose(d)
    html = c.html_path.read_text(encoding="utf-8")
    assert c.html_path == d / "build" / "composed.html"
    assert '<html lang="en-US" dir="ltr"' in html and '<h1 id="h1">Hello</h1>' in html
    assert "window.PagedPolyfill.preview()" in html and "/*__PAGED__*/" not in html and "PagedPolyfill = factory()" in html
    assert "@font-face" in html and '--font-body: "Inter", sans-serif;' in html
    info = _info(d)
    assert info["title"] == "Hello" and info["pdf_lang"] == "en-US" and info["missing_glyphs"] == {}
    assert "inter" in info["allowed_font_keys"] and info["pages_expect"] is None


def test_compose_fa_transforms(make_doc):
    d = make_doc({"template": "custom", "lang": "fa"}, content="# گزارش\n\nنرخ BuyBox برابر 12.5% است.\n")
    html = compose(d).html_path.read_text(encoding="utf-8")
    assert '<html lang="fa-IR" dir="rtl"' in html and '<bdi dir="ltr">BuyBox</bdi>' in html and "۱۲٫۵٪" in html
    info = _info(d)
    assert "گزارش" in info["fa_words"] and info["digits"] == "fa" and info["rtl"] is True


def test_missing_glyph_recorded(make_doc):
    d = make_doc({"template": "custom", "theme": "neutral"}, content="# T\n\nسلام\n")
    compose(d)
    assert "س" in _info(d)["missing_glyphs"]["body"]


def test_raw_html_route(make_doc):
    d = make_doc({"template": "custom", "meta": {"title": "Raw"}}, html='<section class="x"><h1>Raw</h1><p>p</p></section>')
    html = compose(d).html_path.read_text(encoding="utf-8")
    assert '<section class="x">' in html
    assert _info(d)["headings"] == [{"level": 1, "text": "Raw", "id": ""}]


def test_markdown_file_list_joined(make_doc):
    d = make_doc({"template": "custom", "content": ["a.md", "b.md"]})
    (d / "a.md").write_text("# A\n\nfirst\n", encoding="utf-8")
    (d / "b.md").write_text("# B\n\nsecond\n", encoding="utf-8")
    compose(d)
    assert [h["id"] for h in _info(d)["headings"]] == ["h1", "h2"]


def test_relative_images_absolutized(make_doc):
    d = make_doc({"template": "custom"}, content="# I\n\n![chart](img/c.png)\n")
    html = compose(d).html_path.read_text(encoding="utf-8")
    assert f'src="{(d / "img" / "c.png").resolve().as_uri()}"' in html


def test_table_alignment_is_logical(make_doc):
    d = make_doc({"template": "custom", "lang": "fa"}, content="| a | b |\n|:--|--:|\n| x | 1 |\n")
    html = compose(d).html_path.read_text(encoding="utf-8")
    assert 'style="text-align:end"' in html and 'style="text-align:start"' in html and "text-align:right" not in html


def test_unknown_template(make_doc):
    with pytest.raises(InputError, match="template"):
        compose(make_doc({"template": "nope"}, content="x"))


def test_page_css_fa():
    doc = {"page": {"size": "A4", "orientation": "portrait", "margins": "20mm"}, "header": "{title}", "footer": "{page} / {pages}", "digits": "fa", "cover": True}
    css = page_css(doc, "عنوان")
    assert "size: A4 portrait;" in css and "margin: 20mm;" in css
    assert 'counter(page, persian) " / " counter(pages, persian)' in css and '"عنوان"' in css and "@page cover" in css


def test_page_css_16_9_latin():
    doc = {"page": {"size": "16:9", "orientation": "landscape", "margins": "0"}, "header": "", "footer": "{page}", "digits": "latin", "cover": False}
    css = page_css(doc, "T")
    assert "size: 254mm 142.875mm;" in css and "counter(page)" in css and "@top-center" not in css and "@page cover" not in css


def test_raw_html_relative_assets_absolutized(make_doc):
    html = (
        "<h1>A</h1>"
        "<img src='img/a.png'>"
        '<img src="img/b.png" srcset="img/b1.png 1x, img/b2.png 2x">'
        '<link rel="stylesheet" href="css/extra.css">'
        '<picture><source srcset="img/c.webp" type="image/webp"><source src="img/d.png"></picture>'
        "<div style=\"background: url('img/e.png') no-repeat\">x</div>"
        "<style>.x { background-image: url(img/f.png); } .y { background: url(\"img/g.png\"); }</style>"
        '<a href="#h1">jump</a> <a href="https://example.com">site</a> <img src="data:image/png;base64,AA==">'
    )
    d = make_doc({"template": "custom", "meta": {"title": "A"}}, html=html)
    out = compose(d).html_path.read_text(encoding="utf-8")

    def uri(rel):
        return (d / rel).resolve().as_uri()

    assert f"src='{uri('img/a.png')}'" in out
    assert f'src="{uri("img/b.png")}" srcset="{uri("img/b1.png")} 1x, {uri("img/b2.png")} 2x"' in out
    assert f'href="{uri("css/extra.css")}"' in out
    assert f'<source srcset="{uri("img/c.webp")}"' in out and f'<source src="{uri("img/d.png")}">' in out
    assert f"url('{uri('img/e.png')}')" in out
    assert f"url({uri('img/f.png')})" in out and f'url("{uri("img/g.png")}")' in out
    assert '<a href="#h1">' in out and '<a href="https://example.com">' in out and 'src="data:image/png;base64,AA=="' in out


def test_url_in_prose_is_not_rewritten(make_doc):
    d = make_doc({"template": "custom", "meta": {"title": "P"}}, html="<h1>P</h1><p>write url(img/x.png) in CSS</p>")
    assert "url(img/x.png)" in compose(d).html_path.read_text(encoding="utf-8")


def test_page_css_converts_literal_margin_text():
    doc = {"page": {"size": "A4", "orientation": "portrait", "margins": "20mm"}, "header": "", "footer": "صفحهٔ {page} نسخهٔ v2 سال 2026", "digits": "fa", "cover": False, "lang": "fa"}
    css = page_css(doc, "T")
    assert '"صفحهٔ " counter(page, persian) " نسخهٔ v2 سال ۲۰۲۶"' in css


def test_margin_latin_numbers_recorded(make_doc):
    d = make_doc({"template": "custom", "lang": "fa", "header": "{title} نسخهٔ v3", "meta": {"title": "گزارش Q4"}}, content="# گزارش\n\nمتن\n")
    compose(d)
    assert {"3", "4"} <= set(_info(d)["latin_numbers"])


def test_missing_paged_js_is_env_error(make_doc, tmp_path, monkeypatch):
    import pdfsmith.compose as compose_module
    from pdfsmith.errors import EnvError

    monkeypatch.setattr(compose_module, "VENDOR", tmp_path / "no-vendor")
    with pytest.raises(EnvError, match="pdfsmith doctor"):
        compose(make_doc({"template": "custom"}, content="# x\n"))


def test_features_info_defaults_and_engine(make_doc):
    d = make_doc({"template": "custom"}, content="# x")
    compose(d)
    info = _info(d)
    assert info["engine"] == "chrome"
    f = info["features"]
    assert (f["attachments"], f["forms"], f["encrypt"], f["merge"], f["pdfa"]) == ([], "auto", None, {"before": [], "after": []}, False)
