import pikepdf
import pymupdf
import pytest

from pdfsmith.check import run_checks
from pdfsmith.compose import compose
from pdfsmith.paths import FONTS
from pdfsmith.post import postprocess
from pdfsmith.render import render

INTER = str(FONTS / "Inter" / "Inter[opsz,wght].ttf")
GEO = {"pages": 1, "overflow": [], "headings": []}


def _info(**kw):
    base = {"title": "T", "pdf_lang": "en-US", "digits": "latin", "allowed_font_keys": [], "missing_glyphs": {}, "fa_words": [], "latin_numbers": [], "empty_sections": [], "pages_expect": None}
    base.update(kw)
    return base


def _mk(tmp_path, text="Hello", pages=1, title="T", lang="en-US", toc=None, fontfile=INTER, link_uri=None):
    path = tmp_path / "x.pdf"
    d = pymupdf.open()
    for _ in range(pages):
        page = d.new_page()
        if fontfile:
            page.insert_text((72, 72), text, fontfile=fontfile, fontname="F0")
        else:
            page.insert_text((72, 72), text)
        if link_uri:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(0, 0, 50, 50), "uri": link_uri})
    if toc:
        d.set_toc(toc)
    d.save(path)
    with pikepdf.open(path, allow_overwriting_input=True) as p:
        p.docinfo["/Title"] = title
        if lang:
            p.Root.Lang = pikepdf.String(lang)
        p.save(path)
    return path


def by(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_clean_pdf_passes(tmp_path):
    rep = run_checks(_mk(tmp_path), _info(), GEO)
    assert rep["passed"], rep
    assert rep["pages"] == 1


def test_unreadable_pdf(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    rep = run_checks(bad)
    assert not rep["passed"] and by(rep, "opens")["status"] == "fail"


def test_page_range(tmp_path):
    assert by(run_checks(_mk(tmp_path), _info(pages_expect=[2, 3]), GEO), "pages")["status"] == "fail"


def test_unembedded_font(tmp_path):
    c = by(run_checks(_mk(tmp_path, fontfile=None), _info(), GEO), "fonts")
    assert c["status"] == "fail" and "not embedded" in c["items"][0]


def test_fallback_font(tmp_path):
    c = by(run_checks(_mk(tmp_path), _info(allowed_font_keys=["vazirmatn"]), GEO), "fonts")
    assert c["status"] == "fail" and "fell back" in c["items"][0]


def test_missing_glyphs(tmp_path):
    c = by(run_checks(_mk(tmp_path), _info(missing_glyphs={"body": ["س"]}), GEO), "fonts")
    assert c["status"] == "fail" and "U+0633" in c["items"][0]


def test_overflow(tmp_path):
    geo = dict(GEO, overflow=[{"page": 1, "tag": "table", "text": "Region", "px": 40}])
    c = by(run_checks(_mk(tmp_path), _info(), geo), "overflow")
    assert c["status"] == "fail" and "page 1" in c["items"][0]


def test_leftovers(tmp_path):
    c = by(run_checks(_mk(tmp_path, text="Hello {{ name }} TODO"), _info(), GEO), "leftovers")
    assert c["status"] == "fail" and len(c["items"]) == 3


def test_bad_link(tmp_path):
    c = by(run_checks(_mk(tmp_path, link_uri="javascript:alert(1)"), _info(), GEO), "links")
    assert c["status"] == "fail"


def test_outline(tmp_path):
    geo = dict(GEO, headings=[{"level": 1, "text": "Intro", "page": 1}])
    assert by(run_checks(_mk(tmp_path), _info(), geo), "outline")["status"] == "fail"
    assert by(run_checks(_mk(tmp_path, toc=[[1, "Intro", 1]]), _info(), geo), "outline")["status"] == "pass"


def test_metadata(tmp_path):
    c = by(run_checks(_mk(tmp_path, title="X", lang=None), _info(), GEO), "metadata")
    assert c["status"] == "fail" and any("Title" in i for i in c["items"]) and any("Lang" in i for i in c["items"])


@pytest.mark.chrome
def test_persian_words(tmp_path, make_doc):
    d = make_doc({"template": "custom", "lang": "fa"}, content="# \u06af\u0632\u0627\u0631\u0634\n\n\u06a9\u062a\u0627\u0628\n")
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    c = by(run_checks(out, _info(fa_words=["\u06a9\u062a\u0627\u0628", "\u063a\u0627\u06cc\u0628"]), GEO), "persian")
    assert c["status"] == "fail" and len(c["items"]) == 1 and "\u063a\u0627\u06cc\u0628" in c["items"][0]


def test_digit_leak(tmp_path):
    pdf = _mk(tmp_path, text="page 12")
    assert by(run_checks(pdf, _info(digits="fa"), GEO), "digits")["status"] == "fail"
    assert by(run_checks(pdf, _info(digits="fa", latin_numbers=["12"]), GEO), "digits")["status"] == "pass"


def test_empty_sections(tmp_path):
    assert by(run_checks(_mk(tmp_path), _info(empty_sections=["A"]), GEO), "empty-sections")["status"] == "fail"


def test_standalone_skips(tmp_path):
    rep = run_checks(_mk(tmp_path))
    assert rep["passed"]
    for name in ("pages", "overflow", "outline", "persian", "digits", "empty-sections"):
        assert by(rep, name)["status"] == "skip", name


@pytest.mark.chrome
def test_persian_words_with_lam_alef_marks_and_half_space(tmp_path, make_doc):
    # Default extraction reverses the lam-alef ligature (خالصه) and misplaces hamza and kasra; extraction without
    # ActualText gets those right but drops ZWNJ. A word counts as intact when either extraction has it.
    words = "بالا خلاصه می‌شود مجموعهٔ قواعدِ است"
    d = make_doc({"template": "custom", "lang": "fa"}, content=f"# گزارش\n\n{words}\n")
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    assert {"بالا", "می‌شود", "مجموعهٔ"} <= set(composed.info["fa_words"])
    c = by(run_checks(out, composed.info, geo), "persian")
    assert c["status"] == "pass", c["items"]


@pytest.mark.chrome
def test_persian_two_way_reading_still_catches_broken_words(tmp_path, make_doc):
    # The PDF has "mishavad" without its half-space and "kholase" reversed; neither reading may accept them.
    content = "# گزارش\n\nمیشود هصالخ بالا\n"
    d = make_doc({"template": "custom", "lang": "fa"}, content=content)
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    expected = ["می‌شود", "خلاصه", "بالا"]
    c = by(run_checks(out, dict(composed.info, fa_words=expected), geo), "persian")
    assert c["status"] == "fail" and len(c["items"]) == 2, c["items"]
    assert expected[0] in c["items"][0] + c["items"][1] and expected[1] in c["items"][0] + c["items"][1]
    assert all("reversed letters or a dropped half-space" in i and "joining" not in i for i in c["items"])


@pytest.mark.chrome
def test_persian_word_split_by_inline_tag_passes(tmp_path, make_doc):
    # The PDF extracts the whole word, so the word list must hold it whole as well.
    d = make_doc({"template": "custom", "lang": "fa"}, content="# گزارش\n\n**کتاب**ها و [قیمت](https://example.com)‌ها\n")
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    assert {"کتابها", "قیمت‌ها"} <= set(composed.info["fa_words"])
    c = by(run_checks(out, composed.info, geo), "persian")
    assert c["status"] == "pass", c["items"]


def test_resources_clean_passes(tmp_path):
    c = by(run_checks(_mk(tmp_path), _info(missing_fonts={}), dict(GEO, blocked=[], failed=[], page_errors=[])), "resources")
    assert c["status"] == "pass" and c["items"] == []


@pytest.mark.parametrize(
    "info_extra, geo_extra, expected",
    [
        ({"missing_fonts": {"body": ["Nope Sans"]}}, {}, "font role 'body': 'Nope Sans' is not installed; the next font in its chain is used"),
        ({}, {"blocked": ["https://example.com/x.png"]}, "blocked (network is off): https://example.com/x.png"),
        ({}, {"failed": [{"url": "file:///tmp/nope.png", "error": "net::ERR_FILE_NOT_FOUND"}]}, "failed to load: file:///tmp/nope.png (net::ERR_FILE_NOT_FOUND)"),
        ({}, {"page_errors": ["Error: boom"]}, "JavaScript error: Error: boom"),
    ],
)
def test_resources_warns_without_failing(tmp_path, info_extra, geo_extra, expected):
    rep = run_checks(_mk(tmp_path), _info(**info_extra), dict(GEO, **geo_extra))
    c = by(rep, "resources")
    assert c["status"] == "warn" and c["items"] == [expected] and rep["passed"] is True


def _encrypted(tmp_path):
    path = tmp_path / "enc.pdf"
    with pikepdf.open(_mk(tmp_path)) as p:
        p.save(path, encryption=pikepdf.Encryption(owner="o", user="u"))
    return path


def test_encrypted_pdf_reports_instead_of_raising(tmp_path):
    rep = run_checks(_encrypted(tmp_path), _info(), GEO)
    c = by(rep, "opens")
    assert not rep["passed"] and c["status"] == "fail" and "password" in c["detail"]


def test_zero_page_pdf_is_closed(tmp_path, monkeypatch):
    closed = []
    real_open = pymupdf.open

    def tracking_open(*a, **kw):
        doc = real_open(*a, **kw)
        real_close = doc.close
        doc.close = lambda: (closed.append(True), real_close())
        return doc

    path = tmp_path / "x.pdf"
    _mk(tmp_path)
    monkeypatch.setattr(pymupdf, "open", tracking_open)
    monkeypatch.setattr(pymupdf.Document, "page_count", property(lambda self: 0))
    rep = run_checks(path, _info(), GEO)
    assert by(rep, "opens")["detail"] == "the PDF has no pages" and closed


def test_metadata_read_error_is_a_failed_check(tmp_path, monkeypatch):
    def broken(*a, **kw):
        raise pikepdf.PdfError("unable to find trailer dictionary")

    pdf = _mk(tmp_path)
    monkeypatch.setattr(pikepdf, "open", broken)
    c = by(run_checks(pdf, _info(), GEO), "metadata")
    assert c["status"] == "fail" and "unable to find trailer dictionary" in c["detail"]


@pytest.mark.chrome
def test_leftovers_ignore_quoted_code_but_not_prose(tmp_path, make_doc):
    code = '```js\n// TODO: handle zero stock\nconst q = {"max": {"value": 5}}}\n```\n\nInline `TBD {{ x }}` is code too.\n'
    d = make_doc({"template": "custom"}, content=f"# Report\n\nClean prose.\n\n{code}")
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    assert composed.info["mono_font_keys"]
    c = by(run_checks(out, composed.info, geo), "leftovers")
    assert c["status"] == "pass", c["items"]


@pytest.mark.chrome
def test_leftovers_still_fail_for_prose_next_to_code(tmp_path, make_doc):
    d = make_doc({"template": "custom"}, content="# Report\n\nTODO write this section.\n\n```\n// TODO in code\n```\n")
    composed = compose(d)
    raw, out = d / "build" / "raw.pdf", d / "build" / "out.pdf"
    geo = render(composed.html_path, raw, d / "build" / "geometry.json")
    postprocess(raw, out, composed.info, geo)
    c = by(run_checks(out, composed.info, geo), "leftovers")
    assert c["status"] == "fail" and len(c["items"]) == 1, c["items"]
