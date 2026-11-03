import shutil

import pytest

from pdfsmith import fonts
from pdfsmith.errors import InputError
from pdfsmith.paths import FONTS


@pytest.fixture(scope="module")
def faces():
    return fonts.scan()


def test_scan_finds_bundled(faces):
    fams = {f.family for f in faces if f.source == "bundled"}
    assert {"Inter", "Vazirmatn", "JetBrains Mono"} <= fams


def test_variable_weight_range(faces):
    vz = [f for f in faces if f.family == "Vazirmatn" and f.source == "bundled"][0]
    assert vz.variable and vz.weight_range == (100, 900)


def test_cache_reused(faces):
    assert len(fonts.scan()) == len(faces)


def test_project_font_wins(tmp_path):
    (tmp_path / "fonts").mkdir()
    shutil.copy(FONTS / "Inter" / "Inter[opsz,wght].ttf", tmp_path / "fonts" / "Inter.ttf")
    found = fonts.find_family(fonts.scan(tmp_path), "inter")
    assert found and all(f.source == "project" for f in found)


def test_resolve_records_missing(faces):
    r = fonts.resolve_roles({"body": ["Nope Sans", "Vazirmatn"], "mono": ["JetBrains Mono"]}, {}, faces)
    assert r["body"].missing == ["Nope Sans"] and r["body"].families == ["Vazirmatn"]


def test_resolve_all_missing_fails(faces):
    with pytest.raises(InputError, match="body"):
        fonts.resolve_roles({"body": ["Nope Sans"]}, {}, faces)


def test_override_string_prepends(faces):
    r = fonts.resolve_roles({"body": ["Vazirmatn"]}, {"body": "Inter"}, faces)
    assert r["body"].families == ["Inter", "Vazirmatn"]


def test_override_unknown_role(faces):
    with pytest.raises(InputError, match="caption"):
        fonts.resolve_roles({"body": ["Inter"]}, {"caption": "Inter"}, faces)


def test_font_face_css(faces):
    css = fonts.font_face_css(fonts.resolve_roles({"body": ["Vazirmatn"], "mono": ["JetBrains Mono"]}, {}, faces))
    assert 'font-family: "Vazirmatn"' in css and "font-weight: 700;" in css and 'url("file://' in css


def test_font_face_css_uses_static_instances_of_variable_fonts(faces):
    # Chrome writes variable fonts into the PDF as Type 3 fonts, so each weight gets a static instance.
    css = fonts.font_face_css(fonts.resolve_roles({"body": ["Vazirmatn"], "mono": ["JetBrains Mono"]}, {}, faces))
    assert "100 900" not in css and "[wght]" not in css
    assert css.count('font-family: "Vazirmatn"') == 9 and css.count('font-family: "JetBrains Mono"') == 8


def test_static_instance_has_no_variation_tables(faces):
    from fontTools.ttLib import TTFont

    vz = fonts.find_family(faces, "Vazirmatn")[0]
    weights = dict(fonts.static_instances(vz))
    assert sorted(weights) == list(range(100, 1000, 100))
    bold = TTFont(weights[700])
    assert "fvar" not in bold and "gvar" not in bold and bold["OS/2"].usWeightClass == 700
    mtimes = {w: p.stat().st_mtime_ns for w, p in weights.items()}
    again = fonts.static_instances(vz)
    assert again == list(weights.items())  # cached: same paths
    assert {w: p.stat().st_mtime_ns for w, p in again} == mtimes  # and the files were not rewritten


def test_static_instance_of_variable_font_without_weight_axis(tmp_path):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    opsz_only = instancer.instantiateVariableFont(TTFont(str(FONTS / "Inter" / "Inter[opsz,wght].ttf")), {"wght": 400})
    assert [a.axisTag for a in opsz_only["fvar"].axes] == ["opsz"]
    (tmp_path / "fonts").mkdir()
    opsz_only.save(str(tmp_path / "fonts" / "InterOpsz.ttf"))
    face = [f for f in fonts.scan(tmp_path) if f.source == "project"][0]
    assert face.variable
    (weight, path), = fonts.static_instances(face)
    assert weight == face.weight and "fvar" not in TTFont(path)


def test_static_instance_failure_names_the_font(tmp_path):
    bad = tmp_path / "Broken[wght].ttf"
    bad.write_bytes(b"not a font")
    face = fonts.Face(str(bad), 0, "Broken", "Regular", 400, False, True, (100, 900), "Broken-Regular", "project")
    with pytest.raises(InputError, match=r"Broken\[wght\]\.ttf"):
        fonts.static_instances(face)


def test_font_face_css_skips_system(faces):
    if not fonts.find_family(faces, "Menlo"):
        pytest.skip("Menlo not installed")
    assert fonts.font_face_css(fonts.resolve_roles({"mono": ["Menlo"]}, {}, faces)) == ""


def test_font_stack(faces):
    r = fonts.resolve_roles({"mono": ["JetBrains Mono"]}, {}, faces)
    assert fonts.font_stack(r["mono"], "monospace") == '"JetBrains Mono", monospace'


def test_missing_glyphs(faces):
    r = fonts.resolve_roles({"body": ["Inter"]}, {}, faces)
    assert fonts.missing_glyphs({"a", "ب", " ", "‌"}, r["body"]) == {"ب"}


def test_pdf_font_keys(faces):
    keys = fonts.allowed_font_keys(fonts.resolve_roles({"body": ["Vazirmatn"], "mono": ["JetBrains Mono"]}, {}, faces))
    assert fonts.pdf_font_key("AAAAAA+Vazirmatn-Regular") in keys
    assert fonts.pdf_font_key("BAAAAA+JetBrainsMono-Bold") in keys
    assert fonts.pdf_font_key("CAAAAA+ArialMT") not in keys


def test_pdf_font_keys_include_static_instance_names(faces):
    # The instancer renames JetBrains Mono's instances from its STAT table (JetBrainsMonoRoman-Bold).
    keys = fonts.allowed_font_keys(fonts.resolve_roles({"mono": ["JetBrains Mono"]}, {}, faces))
    assert fonts.pdf_font_key("DAAAAA+JetBrainsMonoRoman-Regular") in keys
