import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "adapters" / "html"
sys.path.insert(0, str(HTML))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"dv_{name}", HTML / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


charts = _load("charts")
chartspec = _load("chartspec")

ALL = ("bar_h", "line", "columns", "bridge", "dot_range", "stacked_share", "small_multiples", "sparkline",
       "table_bars", "scatter", "heatmap")


def _svgs(html: str):
    """Parse every <svg> in a string (the sparkline demo holds three)."""
    out, i = [], 0
    while (i := html.find("<svg", i)) != -1:
        j = html.index("</svg>", i) + 6
        out.append(ET.fromstring(html[i:j]))
        i = j
    return out


def test_every_chart_type_returns_valid_svg():
    demo = charts.demo_charts()
    assert set(demo) == set(ALL)
    for name, s in demo.items():
        roots = _svgs(s)
        assert roots, name
        for r in roots:
            assert r.tag.endswith("svg") and r.get("viewBox"), name


def test_highlight_is_the_only_red_and_digits_are_persian():
    s = charts.bar_h([("الف", 10), ("ب", 20)], highlight="ب")
    assert s.count(charts.ACCENT) == 2  # the bar and its value label
    assert "۲۰" in s and "20<" not in s
    assert charts.bar_h([("الف", 10), ("ب", 20)]).count(charts.ACCENT) == 0


def test_signed_bridge_delta_is_isolated_for_rtl():
    s = charts.bridge([("آغاز", 10, "total"), ("افت", -3, "delta"), ("پایان", 7, "total")])
    assert "⁦−۳⁩" in s


def test_ticks_use_nice_steps_without_duplicate_labels():
    t = charts._ticks(1, 4)
    assert t == [1, 2, 3, 4]
    labels = [charts.fa(v, charts._tick_dec(t)) for v in t]
    assert len(labels) == len(set(labels))


def _spec(data, **kw):
    return {"version": 1, "message": kw.pop("message", "فروش گوشی در جشنواره ۴۵٪ بالا رفت"), "type": "auto",
            "data": data, "unit": kw.pop("unit", "میلیارد تومان"), **kw}


@pytest.mark.parametrize("data,expected", [
    ({"x": ["۱", "۲", "۳"], "series": {"الف": [1, 2, 3], "ب": [2, 2, 2]}}, "line"),
    ({"x": ["۱", "۲"], "series": {k: [1, 2] for k in "الف ب پ ت ث".split()}}, "small_multiples"),
    ({"items": [["الف", 3], ["ب", 2]]}, "bar_h"),
    ({"rows": [["الف", 1, 2], ["ب", 2, 3], ["پ", 3, 1], ["ت", 4, 5]]}, "dot_range"),
    ({"rows": [["الف", 1, 2], ["ب", 2, 3]]}, "columns"),
    ({"rows": [["تهران", {"نقدی": 40, "اعتباری": 60}]], "parts": ["نقدی", "اعتباری"]}, "stacked_share"),
    ({"steps": [["آغاز", 10, "total"], ["افت", -2, "delta"], ["پایان", 8, "total"]]}, "bridge"),
    ({"points": [["الف", 1, 2], ["ب", 2, 3]]}, "scatter"),
    ({"rows": ["الف"], "cols": ["فروردین", "اردیبهشت"], "values": [[1, 2]]}, "heatmap"),
    ({"rows": [{"label": "الف", "g": 1}], "columns": [["g", "فروش", 0]], "bar": "g"}, "table_bars"),
    ({"values": [1, 2, 3]}, "sparkline"),
])
def test_auto_choice_by_data_shape(data, expected):
    t, why = chartspec.choose(_spec(data))
    assert t == expected and why


def test_auto_choice_share_from_message():
    s = _spec({"items": [["نقدی", 40], ["اعتباری", 60]]}, message="سهم پرداخت اعتباری به ۶۰٪ رسید")
    assert chartspec.choose(s)[0] == "stacked_share"


@pytest.mark.parametrize("change,needle", [
    ({"message": "نرخ تبدیل ماهانه"}, "message"),
    ({"message": "سهم درآمد فروش در دیجیتال"}, "message"),
    ({"unit": ""}, "unit"),
    ({"unit": ["٪", "تومان"]}, "mixed units"),
    ({"highlight": list("ابپتثجچح")}, "highlight"),
    ({"data": {"rows": [["تهران", {"نقدی": -40, "اعتباری": 60}]], "parts": ["نقدی", "اعتباری"]}}, "negative"),  # bar_h and columns may go negative now; share bars may not
    ({"version": 2}, "version"),
    ({"type": "pie"}, "type"),
    ({"data": {"nothing": 1}}, "no known shape"),
])
def test_validation_rejects(change, needle):
    s = _spec({"items": [["الف", 3], ["ب", 2]]})
    s.update(change)
    errs = chartspec.validate(s)
    assert any(needle in e for e in errs), errs


def test_validation_accepts_a_good_spec_and_a_bridge_with_a_loss():
    assert chartspec.validate(_spec({"items": [["الف", 3], ["ب", 2]]}, highlight="الف")) == []
    assert chartspec.validate(_spec({"steps": [["آغاز", 10, "total"], ["افت", -2, "delta"], ["پایان", 8, "total"]]},
                                    message="افت بعد از جشنواره ۲ میلیارد از فروش کم کرد")) == []


def test_sentence_check():
    assert chartspec.is_sentence("سفارش کالاهای جشنواره ۴۵٪ بالا رفت")[0]
    assert chartspec.is_sentence("نزدیک به ۶۰٪ فروش افزوده از گوشی و لپ‌تاپ آمد")[0]
    assert chartspec.is_sentence("Phone orders rose 45% during the festival")[0]  # English specs work too
    assert not chartspec.is_sentence("فروش روزانه")[0]


def test_figure_html_has_title_svg_and_caption_with_unit(tmp_path):
    s = _spec({"items": [["الف", 3], ["ب", 2]]}, source="نمونه", window="مهر ۱۴۰۵", definition="فروش خالص")
    res = chartspec.render(s, tmp_path, png=False)
    fig = Path(res["figure"]).read_text(encoding="utf-8")
    assert fig.startswith('<figure class="chart">') and "<svg" in fig
    assert "واحد: میلیارد تومان" in fig and "مهر ۱۴۰۵" in fig and "نمونه" in fig
    assert "میلیارد تومان" not in Path(res["svg"]).read_text(encoding="utf-8")  # unit lives in the caption only


def test_render_refuses_an_invalid_spec(tmp_path):
    with pytest.raises(ValueError):
        chartspec.render(_spec({"items": [["الف", 3]]}, unit=""), tmp_path, png=False)


def test_render_to_png(tmp_path):
    pytest.importorskip("playwright.sync_api")
    s = _spec({"items": [["گوشی موبایل", 3.3], ["مد و پوشاک", 1.3]]}, highlight="مد و پوشاک", decimals=1,
              message="بازده تخفیف در مد و پوشاک نصف گوشی بود", unit="برابر")
    try:
        res = chartspec.render(s, tmp_path)
    except Exception as e:  # chromium not installed
        pytest.skip(f"chromium unavailable: {e}")
    from PIL import Image
    w, h = Image.open(res["png"]).size
    assert w > 1000 and h > 200 and res["font"]
    assert chartspec.main(["validate", str(_write(tmp_path, s))]) == 0


def _write(tmp_path, s):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    return p
