import shutil

import pytest
import yaml

from pdfsmith.compose import compose
from pdfsmith.paths import TEMPLATES


def _copy(tmp_path, lang):
    d = tmp_path / lang
    shutil.copytree(TEMPLATES / "report" / "example" / lang, d)
    return d


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_example_composes_with_cover_and_toc(tmp_path, lang):
    html = compose(_copy(tmp_path, lang)).html_path.read_text(encoding="utf-8")
    assert 'class="cover"' in html and 'class="toc"' in html and 'href="#h1"' in html


def test_fa_cover_date_is_jalali(tmp_path):
    html = compose(_copy(tmp_path, "fa")).html_path.read_text(encoding="utf-8")
    assert "۸ مهر ۱۴۰۵" in html


@pytest.mark.chrome
@pytest.mark.parametrize("lang", ["en", "fa"])
def test_example_builds_and_passes_checks(tmp_path, lang):
    from pdfsmith.build import build  # created in Task 12

    pdf, report = build(_copy(tmp_path, lang))
    failing = [c for c in report["checks"] if c["status"] == "fail"]
    assert report["passed"], failing
    lo, hi = yaml.safe_load((TEMPLATES / "report" / "expect.yaml").read_text())["example"][lang]["pages"]
    assert lo <= report["pages"] <= hi
