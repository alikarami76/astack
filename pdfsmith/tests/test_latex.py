import json
import re
import shutil
from pathlib import Path

import pikepdf
import pymupdf
import pytest
import yaml

from pdfsmith.cli import main
from pdfsmith.paths import FONTS

pytestmark = pytest.mark.tectonic


def _scaffold(tmp_path, name="art"):
    d = tmp_path / name
    assert main(["new", "latex", str(d)]) == 0
    return d


def _report(d):
    return json.loads((d / "build" / "report.json").read_text(encoding="utf-8"))


def _check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_scaffold_builds_and_passes(tmp_path, capsys):
    d = _scaffold(tmp_path)
    assert {p.name for p in d.iterdir()} == {"doc.yaml", "main.tex", "references.bib"}
    capsys.readouterr()
    assert main(["build", str(d)]) == 0
    captured = capsys.readouterr()
    pdf = Path(captured.out.strip())
    assert pdf == d / "out" / "sample-article.pdf" and pdf.is_file()
    assert "PASSED" in captured.err
    report = _report(d)
    assert report["passed"] is True
    assert _check(report, "outline")["status"] == "pass"
    assert _check(report, "resources")["status"] == "pass"
    assert _check(report, "overflow") == {"name": "overflow", "status": "pass", "detail": "no overfull boxes in the TeX log", "items": []}
    for name in ("persian", "digits", "empty-sections"):
        assert _check(report, name) == {"name": name, "status": "skip", "detail": "skipped: n/a for latex", "items": []}
    doc = pymupdf.open(pdf)
    text = "".join(page.get_text() for page in doc)
    assert "[1]" in text and "Knuth" in text and "?" not in text.replace("(?)", "")  # bibtex ran, no undefined citations
    assert [e[1] for e in doc.get_toc()][:2] == ["1 Introduction", "2 Model"]


def test_doc_yaml_meta_overrides_pdf_info(tmp_path):
    d = _scaffold(tmp_path)
    doc = yaml.safe_load((d / "doc.yaml").read_text())
    doc["meta"] = {"title": "Override Title", "author": "Someone", "subject": "S", "keywords": ["a", "b"]}
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 0
    with pikepdf.open(d / "out" / "sample-article.pdf") as pdf:
        assert str(pdf.docinfo["/Title"]) == "Override Title"
        assert str(pdf.docinfo["/Author"]) == "Someone"
        assert str(pdf.docinfo["/Keywords"]) == "a, b"
        assert str(pdf.Root.Lang) == "en-US"


def test_meta_fills_from_hyperref_when_not_in_doc_yaml(tmp_path):
    d = _scaffold(tmp_path)
    doc = yaml.safe_load((d / "doc.yaml").read_text())
    doc["meta"] = {}
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 0
    with pikepdf.open(d / "out" / "sample-article.pdf") as pdf:
        assert str(pdf.docinfo["/Title"]) == "A Short Study of Price Elasticity"
        assert str(pdf.docinfo["/Author"]) == "Product Team"


def test_latex_error_exits_2_with_file_line_summary(tmp_path, capsys):
    d = _scaffold(tmp_path)
    tex = (d / "main.tex").read_text()
    (d / "main.tex").write_text(tex.replace("\\section{Introduction}", "\\section{Introduction}\n\\undefinedmacro"))
    capsys.readouterr()
    assert main(["build", str(d)]) == 2
    err = capsys.readouterr().err
    assert "main.tex:" in err and "Undefined control sequence" in err
    assert len(err.splitlines()) < 12 and "Transcript written" not in err
    assert not (d / "out" / "sample-article.pdf").exists()


def test_error_in_input_file_names_that_file(tmp_path, capsys):
    d = _scaffold(tmp_path)
    (d / "part.tex").write_text("text\n\\nosuchmacro\n")
    tex = (d / "main.tex").read_text()
    (d / "main.tex").write_text(tex.replace("\\section{Conclusion}", "\\section{Conclusion}\n\\input{part}"))
    assert main(["build", str(d)]) == 2
    assert "part.tex:" in capsys.readouterr().err


def test_missing_tectonic_exits_4_with_install_hint(tmp_path, monkeypatch, capsys):
    d = _scaffold(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    capsys.readouterr()
    assert main(["build", str(d)]) == 4
    err = capsys.readouterr().err
    assert "tectonic" in err and "brew install tectonic" in err


def test_missing_tex_file_is_bad_input(tmp_path, capsys):
    d = _scaffold(tmp_path)
    (d / "main.tex").unlink()
    assert main(["build", str(d)]) == 1
    assert "main.tex" in capsys.readouterr().err


def test_custom_tex_name_and_markdown_keys_refused(tmp_path, capsys):
    d = _scaffold(tmp_path)
    (d / "main.tex").rename(d / "paper.tex")
    doc = yaml.safe_load((d / "doc.yaml").read_text())
    doc["tex"] = "paper.tex"
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 0
    doc["content"] = "x.md"
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 1
    assert "content" in capsys.readouterr().err


def test_fonts_folder_is_usable_from_fontspec(tmp_path):
    d = _scaffold(tmp_path)
    (d / "fonts").mkdir()
    shutil.copy(FONTS / "Inter" / "Inter[opsz,wght].ttf", d / "fonts" / "Inter.ttf")
    tex = (d / "main.tex").read_text()
    (d / "main.tex").write_text(tex.replace("\\usepackage{microtype}", "\\setmainfont{Inter.ttf}[Path=fonts/]\n\\usepackage{microtype}"))
    assert main(["build", str(d)]) == 0
    doc = pymupdf.open(d / "out" / "sample-article.pdf")
    fonts = {f[3] for page in doc for f in page.get_fonts()}
    assert any("Inter" in name for name in fonts), fonts


def test_attachments_are_embedded(tmp_path):
    d = _scaffold(tmp_path)
    (d / "data.csv").write_text("a,b\n1,2\n")
    doc = yaml.safe_load((d / "doc.yaml").read_text())
    doc["features"] = {"attachments": ["data.csv"]}
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 0
    with pikepdf.open(d / "out" / "sample-article.pdf") as pdf:
        assert list(pdf.attachments) == ["data.csv"]
        assert [str(s.UF) for s in pdf.Root.AF] == ["data.csv"]
    assert _check(_report(d), "features")["status"] == "pass"


def test_document_without_hyperref_fails_outline(tmp_path):
    d = _scaffold(tmp_path)
    tex = (d / "main.tex").read_text()
    start, end = tex.index("\\usepackage[\n  pdftitle"), tex.index("]{hyperref}") + len("]{hyperref}")
    body = tex[:start] + tex[end:]
    (d / "main.tex").write_text(body.replace("\\usepackage{xcolor}", ""))
    assert main(["build", str(d)]) == 3
    outline = _check(_report(d), "outline")
    assert outline["status"] == "fail" and "hyperref" in outline["items"][0]


def test_undefined_citation_is_a_warning(tmp_path):
    d = _scaffold(tmp_path)
    tex = (d / "main.tex").read_text()
    (d / "main.tex").write_text(tex.replace("\\citep{knuth1984}", "\\citep{nobody2000}"))
    assert main(["build", str(d)]) == 0
    resources = _check(_report(d), "resources")
    assert resources["status"] == "warn" and "nobody2000" in resources["items"][0]


def test_new_latex_fa_is_not_implemented(tmp_path, capsys):
    assert main(["new", "latex", str(tmp_path / "x"), "--lang", "fa"]) == 1
    assert "not implemented yet" in capsys.readouterr().err


def _add_to_conclusion(d, text):
    tex = (d / "main.tex").read_text()
    (d / "main.tex").write_text(tex.replace("\\section{Conclusion}", "\\section{Conclusion}\n" + text, 1))


def test_too_wide_table_fails_overflow_with_file_and_lines(tmp_path, capsys):
    d = _scaffold(tmp_path)
    _add_to_conclusion(d, "\\begin{tabular}{ll}\nname & \\hspace{600pt}value\\\\\n\\end{tabular}\n")
    capsys.readouterr()
    assert main(["build", str(d)]) == 3
    overflow = _check(_report(d), "overflow")
    assert overflow["status"] == "fail"
    assert re.fullmatch(r"main\.tex:\d+(-\d+)?: [\d.]+pt too wide", overflow["items"][0]), overflow
    assert "FAIL overflow" in capsys.readouterr().err


def test_overfull_in_input_file_names_that_file(tmp_path):
    d = _scaffold(tmp_path)
    (d / "part.tex").write_text("text\n\n\\noindent\\hbox to 3pt{\\hskip 20pt}\n")
    _add_to_conclusion(d, "\\input{part}\n")
    assert main(["build", str(d)]) == 3
    assert _check(_report(d), "overflow")["items"][0] == "part.tex:3: 17pt too wide"


def test_one_point_overfull_only_warns(tmp_path):
    d = _scaffold(tmp_path)
    _add_to_conclusion(d, "\\noindent\\hbox to 100pt{\\hskip 101pt}\n")
    assert main(["build", str(d)]) == 0
    report = _report(d)
    overflow = _check(report, "overflow")
    assert report["passed"] is True and overflow["status"] == "warn"
    assert overflow["items"] == ["main.tex:" + overflow["items"][0].split(":")[1] + ": 1pt too wide"]


def test_overflow_pt_threshold_is_configurable(tmp_path):
    d = _scaffold(tmp_path)
    _add_to_conclusion(d, "\\noindent\\hbox to 100pt{\\hskip 105pt}\n")
    assert main(["build", str(d)]) == 3
    doc = yaml.safe_load((d / "doc.yaml").read_text())
    doc["checks"] = {"overflow_pt": 10}
    (d / "doc.yaml").write_text(yaml.safe_dump(doc))
    assert main(["build", str(d)]) == 0
    assert _check(_report(d), "overflow")["status"] == "warn"
