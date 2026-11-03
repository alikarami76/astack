import pytest

from pdfsmith import themes
from pdfsmith.errors import InputError
from pdfsmith.themes import list_themes, load_theme, theme_css


def test_neutral():
    t = load_theme("neutral")
    assert t["name"] == "neutral" and t["fonts"]["body"] == ["Inter"] and t["colors"]["accent"].startswith("#")


def test_fa_extends_neutral():
    t = load_theme("neutral-fa")
    base = load_theme("neutral")
    assert t["fonts"]["body"] == ["Vazirmatn", "Inter"]
    assert t["colors"] == base["colors"]
    assert t["type"]["leading"] == 1.8 and t["type"]["scale"] == base["type"]["scale"]


def test_unknown_lists_available():
    with pytest.raises(InputError, match="neutral"):
        load_theme("nope")


def test_extends_cycle(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("extends: b\n")
    (tmp_path / "b.yaml").write_text("extends: a\n")
    monkeypatch.setattr(themes, "THEMES", tmp_path)
    with pytest.raises(InputError, match="cycle"):
        load_theme("a")


def test_missing_required_key(tmp_path, monkeypatch):
    (tmp_path / "bad.yaml").write_text("colors: {text: '#000'}\n")
    monkeypatch.setattr(themes, "THEMES", tmp_path)
    with pytest.raises(InputError, match="fonts"):
        load_theme("bad")


def test_css_variables():
    css = theme_css(load_theme("neutral"), {"body": '"Inter", sans-serif'})
    assert css.startswith(":root {")
    assert "--color-accent:" in css and '--font-body: "Inter", sans-serif;' in css and "--leading: 1.55;" in css


def test_list_themes():
    assert {"neutral", "neutral-fa"} <= set(list_themes())
