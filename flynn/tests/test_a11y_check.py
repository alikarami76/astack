import pytest

import a11y_check

PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>t</title></head>
<body><main><div data-uikit="Button"><button><svg width="16" height="16"></svg></button></div><img src="x.png"></main></body></html>"""

STUB = """window.axe = {run: async () => ({violations: [
  {id: 'button-name', impact: 'critical', help: 'Buttons must have discernible text', nodes: [{target: ['button']}]},
  {id: 'region', impact: 'moderate', help: 'All page content should be contained by landmarks', nodes: [{target: ['img']}]}]})};"""


def test_skips_with_a_note_when_axe_is_missing(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(PAGE, encoding="utf-8")
    r = a11y_check.check(page, axe=str(tmp_path / "missing-axe.min.js"))
    assert r["result"] == "skipped" and "axe-core not found" in r["detail"] and r["violations"] == []
    assert a11y_check.main([str(page), "--axe", str(tmp_path / "missing-axe.min.js")]) == 0


def test_finds_axe_from_env(tmp_path, monkeypatch):
    js = tmp_path / "axe.min.js"
    js.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("FLYNN_AXE_JS", str(js))
    assert a11y_check.find_axe(None) == js


def test_serious_violation_fails_and_names_the_kit_component(tmp_path):
    pytest.importorskip("playwright")
    page = tmp_path / "index.html"
    page.write_text(PAGE, encoding="utf-8")
    js = tmp_path / "axe.min.js"
    js.write_text(STUB, encoding="utf-8")
    r = a11y_check.check(page, axe=str(js))
    assert r["result"] == "fail"
    ids = {v["id"]: v for v in r["violations"]}
    assert ids["button-name"]["components"] == ["Button"] and ids["region"]["impact"] == "moderate"
