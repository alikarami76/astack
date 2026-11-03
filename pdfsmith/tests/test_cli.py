import json
from pathlib import Path

import pytest
import yaml

from pdfsmith.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "0.1.0" in capsys.readouterr().out


def test_new_copies_example_and_sets_theme(tmp_path, capsys):
    target = tmp_path / "r"
    assert main(["new", "report", str(target), "--lang", "fa", "--theme", "neutral"]) == 0
    assert capsys.readouterr().out.strip() == str(target.resolve())
    assert yaml.safe_load((target / "doc.yaml").read_text())["theme"] == "neutral"
    assert (target / "content.md").is_file()


def test_new_refuses_non_empty(tmp_path):
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "x").write_text("x")
    assert main(["new", "report", str(tmp_path / "r")]) == 1


def test_bad_input_exits_1(make_doc, capsys):
    d = make_doc({"lang": "de"}, content="x")
    assert main(["build", str(d)]) == 1
    assert "lang" in capsys.readouterr().err


def test_stage_needs_previous_stage(make_doc, capsys):
    d = make_doc({"template": "custom"}, content="# x")
    assert main(["build", str(d), "--stage", "render"]) == 1
    assert "compose" in capsys.readouterr().err


def test_fonts_list(capsys):
    assert main(["fonts", "list", "--filter", "vazirmatn"]) == 0
    assert "Vazirmatn" in capsys.readouterr().out


@pytest.mark.chrome
def test_build_prints_only_pdf_path(tmp_path, capsys):
    d = tmp_path / "en"
    main(["new", "report", str(d)])
    capsys.readouterr()
    assert main(["build", str(d)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].endswith("sample-report.pdf") and Path(out[0]).is_file()


@pytest.mark.chrome
def test_failed_checks_exit_3_and_still_write_pdf(make_doc, capsys):
    d = make_doc({"template": "custom", "meta": {"title": "X"}}, html='<h1>X</h1><div style="width:150%">wide</div>')
    assert main(["build", str(d)]) == 3
    captured = capsys.readouterr()
    assert "overflow" in captured.err and Path(captured.out.strip()).is_file()


@pytest.mark.chrome
def test_check_and_pages_commands(tmp_path, capsys):
    d = tmp_path / "en"
    main(["new", "report", str(d)])
    main(["build", str(d)])
    capsys.readouterr()
    pdf = d / "out" / "sample-report.pdf"
    assert main(["check", str(pdf), "--spec", str(d / "doc.yaml")]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert main(["pages", str(pdf), "--range", "1-2", "--dpi", "40", "--out", str(tmp_path / "png")]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


@pytest.mark.chrome
def test_doctor(capsys):
    assert main(["doctor"]) == 0
    assert "chrome" in capsys.readouterr().err


def test_check_missing_pdf_exits_1(tmp_path, capsys):
    assert main(["check", str(tmp_path / "missing.pdf")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "not found" in err


def test_pages_missing_pdf_exits_1(tmp_path, capsys):
    assert main(["pages", str(tmp_path / "missing.pdf")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "not found" in err


@pytest.mark.parametrize("spec", ["a-b", "2-x", "-"])
def test_pages_malformed_range_exits_1(tmp_path, capsys, spec):
    import pymupdf

    pdf = tmp_path / "x.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    assert main(["pages", str(pdf), "--range", spec, "--out", str(tmp_path / "png")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "--range" in err and "Traceback" not in err


@pytest.mark.chrome
def test_build_prints_resource_warnings_and_passes(make_doc, capsys):
    d = make_doc({"template": "custom", "meta": {"title": "W"}}, html='<h1>W</h1><img src="https://example.com/x.png">')
    assert main(["build", str(d)]) == 0
    err = capsys.readouterr().err
    assert "WARN resources:" in err and "https://example.com/x.png" in err and "PASSED" in err


@pytest.mark.chrome
def test_render_failure_exits_2(make_doc, capsys):
    d = make_doc({"template": "custom"}, content="# x\n\ny\n")
    assert main(["build", str(d), "--stage", "compose"]) == 0
    (d / "build" / "composed.html").unlink()
    capsys.readouterr()
    assert main(["build", str(d), "--stage", "render"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "Traceback" not in err


def _bad_pdfs(tmp_path):
    import pikepdf
    import pymupdf

    notapdf = tmp_path / "notapdf.pdf"
    notapdf.write_text("hello")
    plain = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(plain)
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(plain) as p:
        p.save(enc, encryption=pikepdf.Encryption(owner="o", user="u"))
    return notapdf, enc


@pytest.mark.parametrize("which", [0, 1])
def test_pages_on_unreadable_pdf_exits_1(tmp_path, capsys, which):
    pdf = _bad_pdfs(tmp_path)[which]
    assert main(["pages", str(pdf), "--out", str(tmp_path / "png")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and str(pdf.name) in err and "Traceback" not in err


@pytest.mark.parametrize("which", [0, 1])
def test_check_on_unreadable_pdf_reports_failure(tmp_path, capsys, which):
    pdf = _bad_pdfs(tmp_path)[which]
    assert main(["check", str(pdf)]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)["passed"] is False and "Traceback" not in captured.err


def test_unreadable_user_file_error_is_input_error(tmp_path, capsys, monkeypatch):
    import pdfsmith.cli as cli

    def locked(a):
        raise PermissionError(13, "Permission denied", str(tmp_path / "locked.pdf"))

    monkeypatch.setattr(cli, "cmd_pages", locked)
    assert main(["pages", str(tmp_path / "locked.pdf")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "Permission denied" in err


def test_new_refuses_existing_file(tmp_path, capsys):
    target = tmp_path / "r"
    target.write_text("x")
    assert main(["new", "report", str(target)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pdfsmith: ") and "not a folder" in err and "Traceback" not in err
