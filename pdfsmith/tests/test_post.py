import pikepdf
import pymupdf

from pdfsmith.post import postprocess

FA_INFO = {"title": "گزارش", "author": "the user", "subject": "s", "keywords": ["a", "b"], "pdf_lang": "fa-IR", "rtl": True, "digits": "fa"}
EN_INFO = {"title": "Report", "author": None, "subject": None, "keywords": [], "pdf_lang": "en-US", "rtl": False, "digits": "latin"}


def _pdf(path, pages=2, toc=None):
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"Page {i + 1}")
    if toc:
        d.set_toc(toc)
    d.save(path)
    return path


def test_metadata_lang_direction_labels(tmp_path):
    out = tmp_path / "out.pdf"
    geo = {"headings": [{"level": 1, "text": "One", "page": 1, "y_pt": 50}, {"level": 2, "text": "Sub", "page": 2, "y_pt": 80}]}
    postprocess(_pdf(tmp_path / "raw.pdf", 3), out, FA_INFO, geo)
    with pikepdf.open(out) as p:
        assert str(p.docinfo["/Title"]) == "گزارش" and str(p.docinfo["/Author"]) == "the user" and str(p.docinfo["/Keywords"]) == "a, b"
        assert str(p.Root.Lang) == "fa-IR"
        assert p.Root.ViewerPreferences.Direction == pikepdf.Name.R2L
        nums = list(p.Root.PageLabels.Nums)
        assert str(nums[1].P) == "۱" and str(nums[5].P) == "۳"
        with p.open_metadata() as xmp:
            assert xmp["dc:title"] == "گزارش"
    assert pymupdf.open(out).get_toc() == [[1, "One", 1], [2, "Sub", 2]]


def test_existing_outline_kept(tmp_path):
    out = tmp_path / "out.pdf"
    postprocess(_pdf(tmp_path / "raw.pdf", 1, toc=[[1, "Chrome", 1]]), out, EN_INFO, {"headings": [{"level": 1, "text": "Other", "page": 1, "y_pt": 0}]})
    assert pymupdf.open(out).get_toc() == [[1, "Chrome", 1]]


def test_latin_has_no_labels_or_direction(tmp_path):
    out = tmp_path / "out.pdf"
    postprocess(_pdf(tmp_path / "raw.pdf", 1), out, EN_INFO, None)
    with pikepdf.open(out) as p:
        assert "/PageLabels" not in p.Root and "/Direction" not in p.Root.ViewerPreferences
        assert str(p.Root.Lang) == "en-US"


def test_outline_levels_normalised(tmp_path):
    out = tmp_path / "out.pdf"
    geo = {"headings": [{"level": 2, "text": "A", "page": 1, "y_pt": 0}, {"level": 4, "text": "B", "page": 1, "y_pt": 10}]}
    postprocess(_pdf(tmp_path / "raw.pdf", 1), out, EN_INFO, geo)
    assert pymupdf.open(out).get_toc() == [[1, "A", 1], [2, "B", 1]]


def test_no_tmp_left(tmp_path):
    postprocess(_pdf(tmp_path / "raw.pdf", 1), tmp_path / "o" / "out.pdf", EN_INFO, None)
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["out.pdf"]
