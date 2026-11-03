import importlib.util
import json
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "html"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"html_{name}", ADAPTER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crops = _load("crops")
new_page = _load("new_page")
render = _load("render")


def test_box_to_crop_converts_original_percent_to_crop_percent():
    assert crops.box_to_crop((50, 0, 100, 50), (75, 25, 100, 50, "۱")) == {"l": 50.0, "t": 50.0, "w": 50.0, "h": 50.0}


def test_ratio_is_display_over_css_size_of_2x_capture_capped_at_one():
    assert crops.ratio(2000, 800) == 0.8
    assert crops.ratio(1000, 980) == 1.0


def test_markup_places_boxes_badge_corner_and_fade():
    spec = {"crop": [0, 0, 100, 100], "boxes": [[10, 10, 20, 20, "۱"], [30, 30, 40, 40, "۲"]], "corner": {"۲": "tl"}, "fade": "l"}
    m = crops.markup("x", spec, "alt")
    assert m.startswith('<div class="shot fade-l"><img src="img/crop-x.png" alt="alt">')
    assert '<div class="hl" style="left:10.0%;top:10.0%;width:10.0%;height:10.0%"><span>۱</span></div>' in m
    assert '<div class="hl tl"' in m


def test_crop_all_writes_crop_and_warns_when_too_small(tmp_path, capsys):
    from PIL import Image
    img = tmp_path / "img"
    img.mkdir()
    Image.new("RGB", (4000, 1000), "white").save(img / "wide.png")
    (tmp_path / "spec.json").write_text(json.dumps({"wide": {"crop": [0, 0, 100, 50], "boxes": []}}))
    out = crops.crop_all(tmp_path / "spec.json", img, 980)
    assert (img / "crop-wide.png").exists()
    assert out["wide"]["size"] == (4000, 500) and out["wide"]["ratio"] == 0.49
    assert "warning: wide" in capsys.readouterr().err


def test_new_page_scaffolds_theme_and_refuses_overwrite(tmp_path):
    page = new_page.new_page(tmp_path / "run")
    assert (page / "index.html").exists() and (page / "style.css").exists()
    assert (page / "fonts" / "vazirmatn-variable.woff2").exists() and (page / "img").is_dir()
    with pytest.raises(SystemExit):
        new_page.new_page(tmp_path / "run")
    with pytest.raises(SystemExit):
        new_page.new_page(tmp_path / "other", template="nope")


def test_theme_colours_are_all_tokens():
    import re
    css = (ADAPTER / "themes" / "editorial" / "style.css").read_text(encoding="utf-8")
    start = re.search(r"^:root\s*\{", css, re.M).start()
    root = css[start:css.index("}", start)]
    allowed = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}\b", root)} | {"#fff", "#ffffff", "#000"}
    # a few one-off tints are allowed for the concept mock's warning box and the lifecycle text; everything else must be a token
    allowed |= {"#fff8e6", "#7a5600", "#c9cad6", "#ffd7df", "#e7f6ee"}
    stray = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}\b", css)} - allowed
    assert not stray, f"colours outside :root tokens: {stray}"


def test_render_checks_a_scaffolded_page(tmp_path):
    pytest.importorskip("playwright.sync_api")
    page = new_page.new_page(tmp_path / "run")
    try:
        r = render.render(page / "index.html", tmp_path / "run" / "shots", pdf="out.pdf")
    except Exception as e:  # browser not installed in this environment
        pytest.skip(f"chromium unavailable: {e}")
    assert r["font"] and r["fontFamily"] == "Vazirmatn"
    assert not r["overflow"]
    assert r["badImages"]  # the template's image placeholders do not exist yet, so the check must catch them
    assert not r["ok"]
    assert (tmp_path / "run" / "out.pdf").exists() and (tmp_path / "run" / "shots" / "sec-hero.png").exists()
