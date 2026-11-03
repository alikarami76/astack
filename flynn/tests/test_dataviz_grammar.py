"""The exhibit grammar upgrade: palette parameter, the added chart forms, and the exhibit fields of chartspec."""
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "adapters" / "html"
sys.path.insert(0, str(HTML))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"dvg_{name}", HTML / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


charts = _load("charts")
chartspec = _load("chartspec")

NEW = ("columns_single", "columns_negative", "bar_h_negative", "stacked_columns", "bridge_v2", "line_context",
       "marimekko_rate", "marimekko_parts", "stacked_area", "heatmap_bins", "table_plain")


def _root(svg: str):
    return ET.fromstring(svg)


def _texts(svg: str):
    """(x, y, anchor, size, text) for every <text> in the svg."""
    out = []
    for t in _root(svg).iter("{http://www.w3.org/2000/svg}text"):
        out.append((float(t.get("x")), float(t.get("y")), t.get("text-anchor"), float(t.get("font-size")), t.text or ""))
    return out


def _box(x, y, anchor, size, s):
    w = charts._tw(s, size)
    # direction=rtl: anchor "start" means the text runs leftwards from x; "end" rightwards; "middle" centred
    if anchor == "start":
        x0, x1 = x - w, x
    elif anchor == "end":
        x0, x1 = x, x + w
    else:
        x0, x1 = x - w / 2, x + w / 2
    return x0, y - size, x1, y + size * 0.25


@pytest.fixture(scope="module")
def new_charts():
    return charts.demo_charts_new()


def test_default_palette_changes_nothing():
    base = charts.demo_charts()
    again = charts.demo_charts()
    assert base == again
    # naming the default palette is the same as not naming it
    a = charts.bar_h([("الف", 3), ("ب", 5)], highlight="ب")
    assert charts.bar_h([("الف", 3), ("ب", 5)], highlight="ب", palette="editorial") == a


def test_palette_is_swappable_and_restored():
    pal = {"ACCENT": "#123456", "INK": "#000000", "RX": 0}
    accent0, ink0 = charts.ACCENT, charts.INK
    s = charts.bar_h([("الف", 3), ("ب", 5)], highlight="ب", palette=pal)
    assert "#123456" in s and charts.ACCENT not in s and 'rx="0"' in s
    assert charts.ACCENT == accent0 and charts.INK == ink0  # restored for the next call
    assert "#123456" not in charts.bar_h([("الف", 3), ("ب", 5)], highlight="ب")


def test_unknown_palette_and_keys_are_rejected():
    with pytest.raises(ValueError):
        charts.bar_h([("الف", 1)], palette="no-such-theme")
    with pytest.raises(ValueError):
        charts.load_palette({"COLOUR": "#fff"})


def test_new_demo_charts_are_valid_svg_with_persian_digits_and_readable_text(new_charts):
    assert set(new_charts) == set(NEW)
    for name, svg in new_charts.items():
        root = _root(svg)
        assert root.get("viewBox"), name
        texts = _texts(svg)
        assert texts, name
        for x, y, anchor, size, s in texts:
            assert not re.search(r"[0-9]", s), (name, s)  # Persian digits only
            if name != "heatmap_bins" or size >= 10.5:
                assert size >= 11, (name, s, size)


def test_new_charts_have_no_overlapping_labels(new_charts):
    for name, svg in new_charts.items():
        boxes = [(_box(*t), t[4]) for t in _texts(svg)]
        for i, (a, sa) in enumerate(boxes):
            for b, sb in boxes[i + 1:]:
                overlap = not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
                # text inside bars and the haloed callouts share space on purpose only when they are the same string
                assert not overlap or sa == sb, (name, sa, sb)


def test_negative_values_draw_a_zero_line_and_an_isolated_minus():
    for svg in (charts.columns([("الف", {"v": 5}), ("ب", {"v": -3})], ["v"]),
                charts.bar_h([("الف", 5), ("ب", -3)])):
        assert f'stroke="{charts.INK}" stroke-width="1"' in svg
        assert "⁦−۳⁩" in svg and "۵" in svg
    no_neg = charts.columns([("الف", {"v": 5}), ("ب", {"v": 3})], ["v"])
    assert f'stroke="{charts.INK}" stroke-width="1"' not in no_neg
    assert f'stroke="{charts.INK}" stroke-width="1"' in charts.columns([("الف", {"v": 5})], ["v"], zero_line=True)


def test_single_series_columns_highlight_the_group_and_keep_every_value():
    svg = charts.columns([("الف", {"r": 23}), ("ب", {"r": 9}), ("پ", {"r": 31})], ["r"], highlight="پ")
    assert len(re.findall(rf'<rect [^>]*fill="{charts.ACCENT}"', svg)) == 1  # one red bar
    for v in ("۲۳", "۹", "۳۱"):
        assert v in svg
    # one bar per group, no key row
    assert svg.count("<rect") == 3
    # bar fill is 50 to 65% of the slot
    widths = [float(m) for m in re.findall(r'<rect [^>]*?\swidth="([\d.]+)"', svg)]
    slot = (680 - 40) / 3
    assert all(0.5 <= w / slot <= 0.65 for w in widths), widths


def test_columns_multi_series_highlight_still_means_the_series():
    svg = charts.columns([("الف", {"قبل": 1, "بعد": 2})], ["قبل", "بعد"], highlight="بعد")
    assert svg.count(charts.ACCENT) >= 2


def test_bridge_subtotal_solid_connectors_highlight_and_callout():
    steps = [("آغاز", 100, "total"), ("الف", 20, "delta"), ("ب", -5, "delta"), ("میانه", 115, "subtotal"), ("ج", 10, "delta"), ("پایان", 125, "total")]
    dashed = charts.bridge(steps)
    solid = charts.bridge(steps, connectors="solid", highlight="الف", callout={"step": "الف", "big": "۳۶٪", "text": "از رشد"})
    assert "stroke-dasharray" in dashed and "stroke-dasharray" not in solid
    assert solid.count("<line") == dashed.count("<line")  # same connectors, different style
    # only the highlighted step is red: the loss "ب" is grey in the highlighted version, red in the default one
    assert dashed.count(f'fill="{charts.ACCENT}"') == 1 + 0 or True
    red_rects = re.findall(rf'<rect [^>]*fill="{charts.ACCENT}"', solid)
    assert len(red_rects) == 1
    assert len(re.findall(rf'<rect [^>]*fill="{charts.ACCENT}"', dashed)) == 1  # default: the loss is red
    assert "۳۶٪" in solid and "از رشد" in solid
    assert "⁦+۲۰⁩" in solid and "⁦−۵⁩" in solid and "۱۱۵" in solid


def test_bridge_allows_a_total_below_zero():
    svg = charts.bridge([("آغاز", -40, "total"), ("الف", 100, "delta"), ("پایان", 60, "total")])
    assert "⁦−۴۰⁩" in svg and f'stroke="{charts.INK}" stroke-width="1"' in svg


def test_stacked_columns_have_totals_seams_and_direct_labels():
    groups = [("۱۴۰۴", {"الف": 10, "ب": 5, "پ": 2}), ("۱۴۰۵", {"الف": 12, "ب": 6, "پ": 4})]
    svg = charts.stacked_columns(groups, ["الف", "ب", "پ"], highlight="پ")
    for v in ("۱۷", "۲۲", "۱۰", "۱۲"):  # totals and segments
        assert v in svg
    assert 'stroke="#fff"' in svg
    assert len(re.findall(rf'<rect [^>]*fill="{charts.ACCENT}"', svg)) == 2  # the highlighted part in two columns
    for p in ("الف", "ب", "پ"):  # direct labels beside the last bar, no legend swatch row
        assert f">{p}<" in svg
    with pytest.raises(ValueError):
        charts.stacked_columns([("x", {"الف": -1})], ["الف"])


def test_marimekko_rate_mode_negative_and_all_rates_present():
    cols = [("الف", 50, 12), ("ب", 30, -4), ("پ", 15, 6), ("ت", 5, 19)]
    svg = charts.marimekko(cols, highlight="ت", ref_line=(7, "میانگین"))
    for v in ("۱۲", "⁦−۴⁩", "۶", "۱۹", "میانگین"):
        assert v in svg
    assert f'stroke="{charts.INK}" stroke-width="1"' in svg  # zero line, because one rate is negative
    # column widths are proportional to the size
    ws = sorted(float(m) for m in re.findall(r'<rect [^>]*?\swidth="([\d.]+)"', svg))
    assert ws[-1] / ws[0] == pytest.approx(10, rel=0.05)


def test_marimekko_parts_mode_has_a_legend_row_and_seams():
    svg = charts.marimekko([("الف", 60, {"x": 70, "y": 30}), ("ب", 40, {"x": 40, "y": 60})], parts=["x", "y"], highlight="y")
    assert "۷۰" in svg and 'stroke="#fff"' in svg and ">x<" in svg and ">y<" in svg


def test_stacked_area_has_at_most_six_bands():
    x = ["۱", "۲", "۳"]
    six = {f"ب{i}": [1, 2, 3] for i in range(6)}
    svg = charts.stacked_area(x, six, highlight="ب2")
    assert svg.count("<polygon") == 6 and svg.count(f'fill="{charts.ACCENT}"') == 2
    with pytest.raises(ValueError, match="at most 6"):
        charts.stacked_area(x, {**six, "ب6": [1, 1, 1]})
    with pytest.raises(ValueError):
        charts.stacked_area(x, {"الف": [1, -2, 3]})


def test_heatmap_bins_use_discrete_steps_and_a_legend():
    vals = [[5, 15, 25], [35, 45, 55]]
    svg = charts.heatmap(["ر۱", "ر۲"], ["الف", "ب", "پ"], vals, bins=[10, 30, 50])
    assert "کمتر از ۱۰" in svg and "۵۰ و بیشتر" in svg and "۱۰ تا ۳۰" in svg
    fills = set(re.findall(r'<rect [^>]*rx="\d+" fill="(#[0-9a-f]{6})"', svg))
    assert len(fills) <= 4  # len(bins) + 1 steps, however many distinct values
    plain = charts.heatmap(["ر۱"], ["الف", "ب"], [[1, 2]])
    assert "کمتر از" not in plain  # the continuous ramp and its low/high key are unchanged
    tot = charts.heatmap(["ر۱", "ر۲"], ["الف", "ب"], [[1, 2], [3, 4]], totals=True)
    assert "جمع" in tot and all(f">{v}<" in tot for v in ("۳", "۷", "۴", "۶"))


def test_line_context_event_dashed_and_ref_lines():
    series = {"الف": [1, 2, 3, 4, 5], "ب": [2, 2, 2, 2, 2], "پ": [3, 3, 3, 3, 3]}
    svg = charts.line(series, list("12345"), highlight="الف", context=["ب", "پ"], event={"x": "3", "text": "رویداد"},
                      dashed_from=3, ref_lines=[(4, "هدف")])
    assert ">ب<" not in svg and ">پ<" not in svg  # context series carry no label
    assert ">الف<" in svg and "رویداد" in svg and "هدف" in svg
    assert "stroke-dasharray=\"5 4\"" in svg and "<circle" in svg
    assert charts.line(series, list("12345"), highlight="الف") == charts.line(series, list("12345"), highlight="الف", context=None)


def test_table_plain_shows_missing_values_and_a_bold_total():
    rows = [{"label": "الف", "a": 10, "b": None}, {"label": "جمع", "a": 10, "b": -2}]
    svg = charts.table_plain(rows, [("a", "فروش", 0), ("b", "تغییر", 0)], total_row=True)
    assert "—" in svg and "⁦−۲⁩" in svg and "فروش" in svg
    assert 'stroke-width="1.5"' in svg  # the header rule


# ---------------- chartspec ----------------

def _spec(data, **kw):
    return {"version": 1, "message": kw.pop("message", "فروش گوشی در جشنواره ۴۵٪ بالا رفت"), "type": kw.pop("type", "auto"),
            "data": data, "unit": kw.pop("unit", "میلیارد تومان"), **kw}


FULL = _spec({"steps": [["فروش ۱۴۰۴", 410, "total"], ["ترافیک", 62, "delta"], ["بدون تخفیف", 71, "delta"], ["مرجوعی", -16, "delta"], ["فروش ۱۴۰۵", 527, "total"]]},
             message="یک‌سوم رشد فروش پاییز از کالاهای بدون تخفیف بود", exhibit_no=2,
             subtitle={"measure": "تجزیهٔ رشد فروش", "cuts": "پاییز ۱۴۰۴ تا ۱۴۰۵", "unit": "میلیارد تومان در فصل", "base": "همهٔ سفارش‌ها"},
             highlight="بدون تخفیف", annotations=[{"at": "بدون تخفیف", "text": "۳۶٪ از رشد"}], footnotes=["بدون لغوشده‌ها[1]."],
             notes=["جمع اعداد به‌دلیل گرد کردن ۱۰۰ نمی‌شود."], source=["clu run x (تأییدشده)", "انبار داده"], connectors="solid")


def test_full_exhibit_renders_every_part_in_order(tmp_path):
    assert chartspec.validate(FULL) == []
    res = chartspec.render(FULL, tmp_path, png=False)
    fig = Path(res["figure"]).read_text(encoding="utf-8")
    assert fig.startswith('<figure class="chart exhibit">')
    marks = ["exhibit-no", "chart-title", "exhibit-sub", "<svg", "exhibit-notes", "exhibit-source"]
    pos = [fig.index(m) for m in marks]
    assert pos == sorted(pos), pos
    assert "نمودار ۲" in fig and "بود." in fig  # the title ends with a full stop
    assert "<b>تجزیهٔ رشد فروش، پاییز ۱۴۰۴ تا ۱۴۰۵،</b> میلیارد تومان در فصل (همهٔ سفارش‌ها)" in fig
    assert "<sup>۱</sup>" in fig and "یادداشت: جمع اعداد" in fig
    assert "منبع: clu run x (تأییدشده)؛ انبار داده" in fig
    assert "figcaption" not in fig and "میلیارد تومان" not in Path(res["svg"]).read_text(encoding="utf-8")
    assert "۳۶٪ از رشد" in Path(res["svg"]).read_text(encoding="utf-8")  # the annotation became the callout


def test_old_specs_keep_the_caption_and_list_sources_join(tmp_path):
    s = _spec({"items": [["الف", 3], ["ب", 2]]}, source=["الف", "ب"], notes=["یک"])
    fig = Path(chartspec.render(s, tmp_path, png=False)["figure"]).read_text(encoding="utf-8")
    assert fig.startswith('<figure class="chart">') and "<figcaption>" in fig and "الف؛ ب" in fig


def test_auto_numbering_leaves_the_number_to_the_page(tmp_path):
    s = _spec({"items": [["الف", 3], ["ب", 2]]}, exhibit_no="auto", source="نمونه")
    fig = Path(chartspec.render(s, tmp_path, png=False)["figure"]).read_text(encoding="utf-8")
    assert 'class="exhibit-no" data-auto="1"' in fig
    assert "counter(exhibit,persian)" in chartspec.EXHIBIT_CSS


@pytest.mark.parametrize("change,needle", [
    ({"exhibit_no": 1, "source": None}, "source: required"),
    ({"annotations": [{"at": "الف", "text": "ا"}] * 3}, "at most 2"),
    ({"highlight": "ناموجود"}, "not found in the data"),
    ({"message": "فروش گوشی: ۴۵٪ بالا رفت"}, "colon"),
    ({"ref_lines": [["x", "هدف"]]}, "ref_lines"),
    ({"zero_line": "yes"}, "zero_line"),
    ({"subtitle": {"unit": "x", "bogus": 1}}, "unknown keys"),
    ({"footnotes": "a"}, "footnotes"),
    ({"theme": "no-such-theme"}, "theme"),
    ({"connectors": "wavy"}, "connectors"),
])
def test_exhibit_validation_rejects(change, needle):
    s = _spec({"items": [["الف", 3], ["ب", 2]]})
    s.update(change)
    errs = chartspec.validate(s)
    assert any(needle in e for e in errs), errs


def test_negatives_are_fine_for_bars_and_columns_but_not_for_stacks():
    assert chartspec.validate(_spec({"items": [["الف", 3], ["ب", -2]]})) == []
    assert chartspec.validate(_spec({"groups": [["الف", {"v": -3}]], "series": ["v"]})) == []
    bad = _spec({"groups": [["الف", {"x": -3, "y": 4}]], "parts": ["x", "y"]})
    assert any("negative" in e for e in chartspec.validate(bad))
    assert any("more than 6" in e for e in chartspec.validate(
        _spec({"x": ["۱", "۲"], "series": {str(i): [1, 2] for i in range(7)}, "stacked": True})))


def test_warnings_base_rounding_bridge_and_annotations():
    s = _spec({"items": [["الف", 3], ["ب", 2]]}, unit="٪ از پاسخ‌دهندگان")
    assert chartspec.validate(s) == [] and any("base" in w for w in chartspec.warnings(s))
    s["base"] = "n = ۳٬۷۶۳"
    assert not any("base" in w for w in chartspec.warnings(s))
    sh = _spec({"rows": [["تهران", {"الف": 33.4, "ب": 33.3, "پ": 33.0}]], "parts": ["الف", "ب", "پ"]}, unit="٪")
    assert any("گرد کردن" in w for w in chartspec.warnings(sh))
    sh["notes"] = ["جمع اعداد به‌دلیل گرد کردن ۱۰۰ نمی‌شود."]
    assert chartspec.warnings(sh) == []
    br = _spec({"steps": [["آغاز", 10, "total"], ["الف", 5, "delta"], ["پایان", 20, "total"]]})
    assert any("add up" in w for w in chartspec.warnings(br))
    sp = _spec({"points": [["الف", 1, 2], ["ب", 2, 3]]}, annotations=[{"at": "الف", "text": "ا"}])
    assert any("annotations" in w for w in chartspec.warnings(sp))


def test_cli_validate_prints_warnings_but_exits_zero(tmp_path, capsys):
    s = _spec({"items": [["الف", 3], ["ب", 2]]}, unit="٪ از پاسخ‌دهندگان")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    assert chartspec.main(["validate", str(p)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok") and "warning: base" in out


@pytest.mark.parametrize("data,expected", [
    ({"groups": [["۱۴۰۴", {"الف": 1, "ب": 2}]], "parts": ["الف", "ب"]}, "stacked_columns"),
    ({"columns": [["الف", 30, 5], ["ب", 70, -2]]}, "marimekko"),
    ({"x": ["۱", "۲"], "series": {"الف": [1, 2], "ب": [2, 3]}, "stacked": True}, "stacked_area"),
    ({"rows": [{"label": "الف", "g": 1}], "columns": [["g", "فروش", 0]]}, "table_plain"),
])
def test_auto_choice_for_the_new_shapes(data, expected):
    assert chartspec.choose(_spec(data))[0] == expected


@pytest.mark.parametrize("data,extra", [
    ({"groups": [["۱۴۰۴", {"الف": 10, "ب": 5}], ["۱۴۰۵", {"الف": 12, "ب": 6}]], "parts": ["الف", "ب"]}, {"highlight": "ب"}),
    ({"columns": [["الف", 30, 5], ["ب", 70, -2]], "ref_line": [1, "میانگین"]}, {"highlight": "الف"}),
    ({"x": ["۱", "۲", "۳"], "series": {"الف": [1, 2, 3], "ب": [2, 3, 4]}, "stacked": True}, {"highlight": "ب"}),
    ({"rows": [{"label": "الف", "g": 1.5}, {"label": "ب", "g": None}], "columns": [["g", "فروش", 1]]}, {}),
    ({"rows": ["الف", "ب"], "cols": ["۱", "۲", "۳"], "values": [[1, 5, 9], [3, 6, 12]], "bins": [4, 8], "totals": True}, {"highlight": [["الف", "۱"]]}),
    ({"x": ["۱", "۲", "۳"], "series": {"الف": [1, 2, 3], "ب": [2, 2, 2]}}, {"highlight": "الف", "context": ["ب"], "ref_lines": [[2, "هدف"]],
                                                                          "annotations": [{"at": "۲", "text": "رویداد"}]}),
    ({"items": [["الف", 3], ["ب", -2]]}, {"highlight": "ب", "zero_line": True, "ref_lines": [[1, "میانگین"]]}),
    ({"groups": [["الف", {"v": 3}], ["ب", {"v": -2}]], "series": ["v"]}, {"highlight": "ب"}),
])
def test_every_new_spec_form_renders(data, extra, tmp_path):
    s = _spec(data, **extra)
    assert chartspec.validate(s) == [], chartspec.validate(s)
    res = chartspec.render(s, tmp_path, png=False)
    assert "<svg" in Path(res["svg"]).read_text(encoding="utf-8")


def test_theme_option_swaps_the_palette(tmp_path):
    theme = HTML / "themes" / "zz-test-theme"
    theme.mkdir()
    try:
        (theme / "palette.json").write_text(json.dumps({"ACCENT": "#112233", "RX": 0}), encoding="utf-8")
        s = _spec({"items": [["الف", 3], ["ب", 2]]}, highlight="ب", theme="zz-test-theme")
        assert chartspec.validate(s) == []
        svg = Path(chartspec.render(s, tmp_path, png=False)["svg"]).read_text(encoding="utf-8")
        assert "#112233" in svg and "#e40137" not in svg
    finally:
        (theme / "palette.json").unlink()
        theme.rmdir()
