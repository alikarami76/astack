import json

import pikepdf
import pymupdf
import pytest

from pdfsmith.check import run_checks
from pdfsmith.errors import InputError
from pdfsmith.post import postprocess

INFO = {"title": "T", "author": None, "subject": None, "keywords": [], "pdf_lang": "en-US", "rtl": False, "digits": "latin"}


def _raw(path):
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Hello")
    d.save(path)
    return path


def _features(**kw):
    base = {"attachments": [], "forms": "auto", "encrypt": None, "merge": {"before": [], "after": []}, "pdfa": False}
    base.update(kw)
    return base


def test_attachments_are_embedded_with_af_relationship(tmp_path):
    (tmp_path / "q.sql").write_text("select 1;")
    (tmp_path / "r.csv").write_text("a,b\n1,2\n")
    info = dict(INFO, features=_features(attachments=[str(tmp_path / "q.sql"), str(tmp_path / "r.csv")]))
    out = tmp_path / "out.pdf"
    postprocess(_raw(tmp_path / "raw.pdf"), out, info, None)
    with pikepdf.open(out) as p:
        assert sorted(p.attachments) == ["q.sql", "r.csv"]
        spec = p.attachments["q.sql"]
        assert spec.get_file().read_bytes() == b"select 1;"
        assert spec.obj.AFRelationship == pikepdf.Name.Data
        assert spec.get_file().mime_type == "application/x-sql"
        assert "/ModDate" in spec.get_file().obj.Params
        assert [str(s.UF) for s in p.Root.AF] == ["q.sql", "r.csv"]
    rep = run_checks(out, info, None)
    assert next(c for c in rep["checks"] if c["name"] == "features")["status"] == "pass"


def test_duplicate_attachment_names_are_refused(tmp_path):
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "q.sql").write_text(sub)
    info = dict(INFO, features=_features(attachments=[str(tmp_path / "a" / "q.sql"), str(tmp_path / "b" / "q.sql")]))
    with pytest.raises(InputError, match="two files are named 'q.sql'"):
        postprocess(_raw(tmp_path / "raw.pdf"), tmp_path / "out.pdf", info, None)


def test_features_check_reports_a_missing_attachment(tmp_path):
    out = _raw(tmp_path / "plain.pdf")
    info = dict(INFO, features=_features(attachments=[str(tmp_path / "q.sql")]))
    check = next(c for c in run_checks(out, info, None)["checks"] if c["name"] == "features")
    assert check["status"] == "fail" and check["items"] == ["attachment 'q.sql' is missing"]


def test_features_check_skips_without_requests(tmp_path):
    out = _raw(tmp_path / "plain.pdf")
    check = next(c for c in run_checks(out, dict(INFO, features=_features()), None)["checks"] if c["name"] == "features")
    assert check["status"] == "skip"


@pytest.mark.chrome
def test_build_with_attachment_passes(make_doc):
    from pdfsmith.build import build

    d = make_doc({"template": "custom", "meta": {"title": "A"}, "features": {"attachments": ["q.sql"]}}, content="# A\n\ntext\n")
    (d / "q.sql").write_text("select 1;")
    pdf, report = build(d)
    assert report["passed"], [c for c in report["checks"] if c["status"] == "fail"]
    assert json.loads((d / "build" / "compose.json").read_text())["features"]["attachments"] == [str((d / "q.sql").resolve())]
    assert pymupdf.open(pdf).embfile_names() == ["q.sql"]
