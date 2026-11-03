#!/usr/bin/env python3
"""flynn chart handoff: turn a JSON chart spec (written by clu or any caller) into a editorial chart.

  chartspec.py validate <spec.json>              check the spec; exit 1 with the problems
  chartspec.py choose <spec.json>                say which chart type `auto` picks, and why
  chartspec.py render <spec.json> --out <dir>    write chart.svg, chart.png (2x) and figure.html

The spec (version 1):
  {"version": 1,
   "message": "<the finding as a sentence; it becomes the chart title>",
   "type": "auto" | one of TYPES,
   "data": {...},                     # shape depends on the type, see DATA_SHAPES
   "unit": "<unit of the plotted measure, for the caption>",
   "highlight": "<label>" | ["<label>", ...] | null,
   "source": "..." | ["...", ...], "window": "...", "definition": "...", "notes": ["..."],
   "decimals": 0,
   # optional exhibit apparatus (a spec without any of these renders as before, with a caption):
   "exhibit_no": 2 | "۱۰ الف" | "auto", "subtitle": "<measure, cuts>" | {"measure", "cuts", "unit", "base"},
   "base": "n = ۳٬۷۶۳", "footnotes": ["..."], "highlight": ..., "zero_line": true,
   "ref_lines": [[value, "label"]], "annotations": [{"at": "<label>", "text": "..."}],   # at most 2
   "context": ["<series>"], "connectors": "solid", "theme": "<palette name>"}

figure.html is a `figure.chart` block to paste into a report page built from templates/report.html. Without
exhibit fields it is title, SVG and a caption (unit, definition, window, source). With any of exhibit_no, subtitle,
base or footnotes it is a full exhibit: number, finding title, subtitle (measure and cuts in bold, then unit and
base), SVG, footnotes, «یادداشت:» notes and «منبع:» source. Charts never repeat the unit inside the plot. `theme`
picks the chart palette (charts.PALETTES or themes/<name>/palette.json); the default is editorial. Chart rules and the choice table: skills/flynn-core/references/dataviz.md."""
from __future__ import annotations

import os

import argparse
import html as _html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import charts  # noqa: E402

TYPES = ("bar_h", "line", "columns", "bridge", "dot_range", "stacked_share", "small_multiples", "sparkline",
         "table_bars", "scatter", "heatmap", "stacked_columns", "marimekko", "stacked_area", "table_plain")
DATA_SHAPES = {
    "bar_h": '{"items": [[label, value], ...]}',
    "line": '{"x": [labels], "series": {name: [values]}, "band": [i0, i1, label]?}',
    "small_multiples": '{"x": [labels], "series": {name: [values]}}',
    "columns": '{"groups": [[label, {series: value}]], "series": [names]}',
    "dot_range": '{"rows": [[label, before, after]], "labels": [before_name, after_name]?}',
    "stacked_share": '{"rows": [[label, {part: value}]], "parts": [names]}',
    "bridge": '{"steps": [[label, value, "total"|"delta"]]}',
    "scatter": '{"points": [[label, x, y]], "x_label": "...", "y_label": "...", "quadrants": [x, y]?}',
    "heatmap": '{"rows": [labels], "cols": [period labels], "values": [[row values]], "bins": [10, 20, 30]?, "legend": true?, "totals": false?}',
    "stacked_columns": '{"groups": [[label, {part: value}]], "parts": [names], "totals": true?, "change": {"label", "from", "to"}?}',
    "marimekko": '{"columns": [[label, width, {part: share} | rate]], "parts": [names]?, "ref_line": [value, label]?}',
    "stacked_area": '{"x": [labels], "series": {band: [values]}, "stacked": true}  (at most 6 bands)',
    "table_plain": '{"rows": [{"label": .., key: ..}], "columns": [[key, header, decimals]], "total_row": false?}',
    "table_bars": '{"rows": [{"label": .., key: ..}], "columns": [[key, header, decimals]], "bar": key}',
    "sparkline": '{"values": [numbers]}',
}
# bar_h and columns may hang negative values below a zero line; these types cannot
BAR_TYPES = {"stacked_share", "table_bars", "dot_range", "stacked_columns", "stacked_area"}
MAX_HIGHLIGHT = 7
MAX_ANNOTATIONS = 2
MAX_BANDS = 6

# A Persian sentence ends with its verb. These endings cover the common 3rd-person forms (است، بود، شد، کرد،
# رفت، ماند، رسید، دارد، می‌شود، هستند ...); the stoplist holds metric nouns that happen to end the same way.
VERB_END = re.compile(r"(است|بود|شد|کرد|رفت|ماند|رسید|آمد|داد|دارد|داشت|یافت|گرفت|نیست|هست|ند|د|ت)$")
NOUN_STOP = {"درآمد", "رشد", "سود", "تعداد", "سبد", "کد", "برند", "واحد", "نسبت", "کیفیت", "وضعیت", "مدت", "قیمت",
             "خدمت", "ظرفیت", "جمعیت", "فعالیت", "عملیات", "پرداخت", "دریافت", "بازگشت", "مرجوعیت", "دست", "درصد",
             "بازدید", "خرید", "تولید", "رضایت", "فروشگاه‌ها", "مدیریت", "اولویت", "هفت", "هشت", "بیست", "صد", "یکصد"}
EN_VERB = re.compile(r"\b(is|are|was|were|rose|fell|grew|dropped|doubled|halved|increased|decreased|beat|lost|gained|"
                     r"came|made|has|have|had|leads|lags|stayed|reached|cut|added)\b", re.I)


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[\s،,.:;؛!?؟()«»\"]+", s.strip()) if w]


def is_sentence(msg: str) -> tuple[bool, str]:
    """True when the message states a finding: four words or more, and it ends with (or contains) a verb."""
    if ":" in (msg or "") or "：" in (msg or ""):
        return False, "the message has a colon; a title is one sentence stating the finding, not «topic: detail»"
    w = _words(msg or "")
    if len(w) < 4:
        return False, "the message is shorter than four words; state the finding, not the metric"
    if EN_VERB.search(msg):
        return True, ""
    last = w[-1]
    if (VERB_END.search(last) and last not in NOUN_STOP and len(last) > 1) or any(x.startswith(("می‌", "نمی‌")) for x in w):
        return True, ""
    return False, "the message has no verb at its end; write the finding as a sentence (e.g. «… ۴۵٪ بالا رفت»)"


def _highlights(spec) -> list:
    h = spec.get("highlight")
    if h is None:
        return []
    return list(h) if isinstance(h, (list, tuple)) else [h]


def _numbers(data) -> list[float]:
    out = []
    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(data)
    return out


def choose(spec: dict) -> tuple[str, str]:
    """The chart type for `auto`, from the data shape (and the message for shares). Returns (type, reason)."""
    d = spec.get("data") or {}
    msg = spec.get("message", "")
    if "steps" in d:
        return "bridge", "the data is a start total, steps and an end total: a bridge shows how one became the other"
    if "points" in d:
        return "scatter", "each item has two measures: a scatter shows how they relate"
    if "values" in d and "rows" in d and "cols" in d:
        return "heatmap", "the data is a category x period grid: a heatmap shows where the values concentrate"
    if "columns" in d and "rows" not in d and isinstance(d["columns"], list) and d["columns"] and len(d["columns"][0]) == 3:
        return "marimekko", "column width is a size and height a share or rate: a marimekko shows importance and performance together"
    if "x" in d and "series" in d and d.get("stacked"):
        return "stacked_area", f"{len(d['series'])} parts of a total over time: stacked bands show the total and which part drives it"
    if "x" in d and "series" in d:
        n = len(d["series"])
        if n > 4:
            return "small_multiples", f"{n} time series: more than 4 lines tangle, so each gets its own small chart on a shared scale"
        return "line", f"{n} time series over {len(d['x'])} periods: a line chart shows the trend"
    if "rows" in d and d["rows"] and isinstance(d["rows"][0], dict):
        if "bar" not in d:
            return "table_plain", "exact numbers per row and no ranking to show: a plain table with the exhibit header"
        return "table_bars", "each row has several measures: a table with an inline bar ranks them and keeps the detail"
    if "rows" in d and d["rows"] and isinstance(d["rows"][0], (list, tuple)):
        r0 = d["rows"][0]
        if len(r0) == 2 and isinstance(r0[1], dict):
            return "stacked_share", "each row is split into parts: 100% bars compare the mix across rows"
        if len(r0) == 3:
            if len(d["rows"]) <= 3:
                return "columns", "before and after for 3 categories or fewer: grouped columns are the plainest comparison"
            return "dot_range", f"before and after for {len(d['rows'])} categories: a dumbbell shows each change without a forest of columns"
    if "groups" in d and "parts" in d:
        return "stacked_columns", "parts of a total per period: the top total gives the headline, the segments show the driver"
    if "groups" in d:
        return "columns", "a few categories with a few series each: grouped columns"
    if "items" in d:
        vals = [v for _, v in d["items"]]
        if re.search(r"سهم|share|mix|ترکیب", msg, re.I) and abs(sum(vals) - 100) < 1.5 and len(vals) <= 6:
            return "stacked_share", "parts that add up to 100% and a message about share: one 100% bar"
        return "bar_h", "one measure per category: ranked horizontal bars, the easiest to read and label in RTL"
    if "values" in d:
        return "sparkline", "a single short series: a sparkline"
    return "", "no known data shape; see DATA_SHAPES"


def validate(spec: dict) -> list[str]:
    errs = []
    if spec.get("version") != 1:
        errs.append('"version" must be 1')
    ok, why = is_sentence(spec.get("message", ""))
    if not ok:
        errs.append("message: " + why)
    t = spec.get("type", "auto")
    if t != "auto" and t not in TYPES:
        errs.append(f'"type" must be "auto" or one of {", ".join(TYPES)}')
    resolved = choose(spec)[0] if t == "auto" else t
    if not resolved:
        errs.append("data: no known shape; expected one of: " + "; ".join(f"{k} {v}" for k, v in DATA_SHAPES.items()))
    unit = spec.get("unit")
    if isinstance(unit, (list, tuple)):
        if len(set(unit)) > 1:
            errs.append(f"unit: mixed units {sorted(set(unit))}; one chart plots one unit (split it, or index the series)")
        unit = unit[0] if unit else ""
    if not unit or not str(unit).strip():
        errs.append('"unit" is missing; say what the numbers are (e.g. «میلیارد تومان»، «٪»، «هزار سفارش»)')
    units = (spec.get("data") or {}).get("units")
    if isinstance(units, dict) and len(set(units.values())) > 1:
        errs.append(f"data.units: mixed units {sorted(set(units.values()))}; one chart plots one unit")
    hl = _highlights(spec)
    if len(hl) > MAX_HIGHLIGHT:
        errs.append(f"highlight: {len(hl)} items; highlight at most {MAX_HIGHLIGHT} (red means 'look here')")
    if resolved in BAR_TYPES:
        neg = [v for v in _numbers(spec.get("data")) if v < 0]
        if neg:
            errs.append(f"data: negative values in a {resolved} chart ({neg[:3]}); use a bridge for losses, or a diverging design")
    if resolved == "marimekko":
        cols_ = (spec.get("data") or {}).get("columns") or []
        if any(isinstance(c[2], dict) and any(v < 0 for v in c[2].values()) for c in cols_ if len(c) == 3):
            errs.append("data: negative shares in a marimekko; negatives are allowed only as one rate per column")
        if any(c[1] <= 0 for c in cols_ if len(c) == 3):
            errs.append("data: a marimekko column width must be positive")
    if resolved == "stacked_area" and len((spec.get("data") or {}).get("series", {})) > MAX_BANDS:
        errs.append(f"data: stacked area with more than {MAX_BANDS} bands cannot be read; group the small ones or use small_multiples")
    # ---- exhibit fields (all optional)
    no = spec.get("exhibit_no")
    if no is not None and not (isinstance(no, (int, str)) and not isinstance(no, bool)):
        errs.append('exhibit_no: an integer, a string such as "۱۰ الف", or "auto"')
    if no is not None and not _source_text(spec):
        errs.append("source: required when exhibit_no is set (name the provider, and this run for clu charts)")
    sub_ = spec.get("subtitle")
    if sub_ is not None:
        if isinstance(sub_, dict):
            bad = set(sub_) - {"measure", "cuts", "unit", "base"}
            if bad:
                errs.append(f"subtitle: unknown keys {sorted(bad)}; use measure, cuts, unit, base")
        elif not isinstance(sub_, str):
            errs.append("subtitle: a string or an object {measure, cuts, unit, base}")
    for k in ("footnotes", "notes"):
        v = spec.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"{k}: a list of strings")
    if spec.get("zero_line") is not None and not isinstance(spec["zero_line"], bool):
        errs.append("zero_line: true or false")
    rl = spec.get("ref_lines")
    if rl is not None and not (isinstance(rl, list) and all(isinstance(r, (list, tuple)) and len(r) == 2 and isinstance(r[0], (int, float)) for r in rl)):
        errs.append("ref_lines: a list of [value, label] pairs")
    an = spec.get("annotations")
    if an is not None:
        if not (isinstance(an, list) and all(isinstance(a, dict) and a.get("at") is not None and a.get("text") for a in an)):
            errs.append('annotations: a list of {"at": label, "text": ...}')
        elif len(an) > MAX_ANNOTATIONS:
            errs.append(f"annotations: {len(an)} given; at most {MAX_ANNOTATIONS} (annotate the one or two moments that matter)")
    if spec.get("connectors") not in (None, "solid", "dashed"):
        errs.append('connectors: "solid" or "dashed"')
    ctx = spec.get("context")
    if ctx is not None and not (isinstance(ctx, list) and all(isinstance(c, str) for c in ctx)):
        errs.append("context: a list of series names")
    th = spec.get("theme")
    if th is not None:
        try:
            charts.load_palette(th)
        except ValueError as e:
            errs.append(f"theme: {e}")
    if hl and resolved and resolved not in ("heatmap", "sparkline") and not any(isinstance(x, (list, tuple)) for x in hl):
        labs = _labels(spec, resolved)
        missing = [x for x in hl if str(x) not in labs]
        if labs and missing:
            errs.append(f"highlight: {missing[:3]} not found in the data; a highlight must be an item, series, step or part that exists")
    return errs


def _source_text(spec: dict) -> str:
    s = spec.get("source")
    return "؛ ".join(str(x) for x in s) if isinstance(s, (list, tuple)) else str(s or "")


def _labels(spec: dict, t: str) -> set[str]:
    """Every label a highlight may name, for the resolved chart type."""
    d = spec.get("data") or {}
    out: set[str] = set()
    for key in ("items", "rows", "steps", "points", "groups", "columns"):
        for r in d.get(key, []) if isinstance(d.get(key), list) else []:
            if isinstance(r, (list, tuple)) and r:
                out.add(str(r[0]))
            elif isinstance(r, dict):
                out.add(str(r.get(d.get("label", "label"), "")))
            elif isinstance(r, str):
                out.add(r)
    out |= {str(x) for x in d.get("series", [])} if isinstance(d.get("series"), (list, dict)) else set()
    out |= {str(x) for x in d.get("parts", [])}
    out |= {str(x) for x in d.get("labels", [])}
    return out


def warnings(spec: dict) -> list[str]:
    """Things worth fixing that do not stop a render. `validate` returns errors only; the CLI prints both."""
    w = []
    unit = spec.get("unit")
    unit = unit[0] if isinstance(unit, (list, tuple)) and unit else unit
    sub_ = spec.get("subtitle")
    has_base = bool(spec.get("base") or (isinstance(sub_, dict) and sub_.get("base")))
    if isinstance(unit, str) and re.search(r"٪\s*(از|پاسخ)|%\s*of\b", unit) and not has_base:
        w.append("base: the unit is a share of a group but no base is given; add base (for example «n = ۳٬۷۶۳ نفر»)")
    t = spec.get("type", "auto")
    resolved = choose(spec)[0] if t == "auto" else t
    d = spec.get("data") or {}
    notes = " ".join(spec.get("notes") or [])
    if resolved == "stacked_share" and "گرد" not in notes and not re.search(r"round", notes, re.I):
        sums = [sum(r[1].values()) for r in d.get("rows", []) if isinstance(r, (list, tuple)) and len(r) == 2 and isinstance(r[1], dict)]
        if "items" in d:
            sums.append(sum(v for _, v in d["items"]))
        if any(abs(s - 100) <= 1.5 and abs(s - 100) > 1e-9 for s in sums):
            w.append("notes: rounded shares do not add up to exactly 100; add «یادداشت: مجموع ارقام به‌دلیل گرد کردن ۱۰۰ نیست.»")
    if resolved == "bridge":
        run = 0.0
        for i, s in enumerate(d.get("steps", [])):
            lab, v, kind = s[0], s[1], s[2]
            if kind == "delta":
                run += v
            elif i > 0 and abs(v - run) > max(0.5 * 10 ** -int(spec.get("decimals", 0)), abs(run) * 0.002):
                w.append(f"steps: «{lab}» is {v} but the steps before it add up to {run:g}; check the numbers (flynn never changes them)")
                run = v
            else:
                run = v
    if spec.get("annotations") and resolved not in ("bridge", "line", "bar_h"):
        w.append(f"annotations: not drawn for {resolved} charts (supported: bridge, line, bar_h); say it in the title or a footnote")
    if spec.get("context") and resolved != "line":
        w.append("context: only a line chart draws context series")
    return w


def build_svg(spec: dict) -> tuple[str, str]:
    """Return (chart type, svg) for a valid spec."""
    t = spec.get("type", "auto")
    t = choose(spec)[0] if t == "auto" else t
    d, hl, dec = spec["data"], spec.get("highlight"), int(spec.get("decimals", 0))
    cells = [tuple(c) for c in hl] if isinstance(hl, list) and hl and all(isinstance(c, (list, tuple)) for c in hl) else None
    hl = None if cells else (set(hl) if isinstance(hl, list) else hl)
    pal = {"palette": spec["theme"]} if spec.get("theme") else {}
    refs = [tuple(r) for r in spec.get("ref_lines") or []]
    notes_ = spec.get("annotations") or []
    zl = spec.get("zero_line")
    if t == "bar_h":
        return t, charts.bar_h([tuple(x) for x in d["items"]], highlight=hl, decimals=dec, zero_line=zl, ref_lines=refs or None,
                               annotations=notes_ or None, **pal)
    if t == "line":
        band = tuple(d["band"]) if d.get("band") else None
        ev = None
        if notes_:
            ev = {"x": notes_[0]["at"], "text": notes_[0]["text"]}
        return t, charts.line(d["series"], d["x"], highlight=hl, band=band, decimals=dec, context=spec.get("context"), event=ev,
                              dashed_from=d.get("dashed_from"), ref_lines=refs or None, **pal)
    if t == "stacked_columns":
        return t, charts.stacked_columns([(g, v) for g, v in d["groups"]], d["parts"], highlight=hl, decimals=dec,
                                         totals=d.get("totals", True), change=d.get("change"), **pal)
    if t == "marimekko":
        return t, charts.marimekko([tuple(c) for c in d["columns"]], parts=d.get("parts"), highlight=hl, decimals=dec,
                                   ref_line=tuple(refs[0]) if refs else (tuple(d["ref_line"]) if d.get("ref_line") else None), **pal)
    if t == "stacked_area":
        return t, charts.stacked_area(d["x"], d["series"], highlight=hl, decimals=dec, **pal)
    if t == "table_plain":
        return t, charts.table_plain(d["rows"], [tuple(c) for c in d["columns"]], highlight=hl, total_row=d.get("total_row", False),
                                     label=d.get("label", "label"), **pal)
    if t == "small_multiples":
        return t, charts.small_multiples(d["series"], d["x"], highlight=hl, decimals=dec, **pal)
    if t == "columns":
        if "groups" in d:
            groups, series = [(g, s) for g, s in d["groups"]], d["series"]
        else:  # before/after rows
            names = d.get("labels", ["قبل", "بعد"])
            groups, series = [(r[0], {names[0]: r[1], names[1]: r[2]}) for r in d["rows"]], names
        return t, charts.columns(groups, series, highlight=hl, decimals=dec, zero_line=zl, ref_lines=refs or None, **pal)
    if t == "dot_range":
        return t, charts.dot_range([tuple(r) for r in d["rows"]], highlight=hl, labels=tuple(d.get("labels", ("قبل", "بعد"))), decimals=dec, **pal)
    if t == "stacked_share":
        if "rows" in d:
            return t, charts.stacked_share([(r[0], r[1]) for r in d["rows"]], d["parts"], highlight=hl, decimals=dec, **pal)
        parts = [lab for lab, _ in d["items"]]
        return t, charts.stacked_share([("", dict(d["items"]))], parts, highlight=hl, decimals=dec, **pal)
    if t == "bridge":
        co = {"step": notes_[0]["at"], "text": notes_[0]["text"]} if notes_ else None
        return t, charts.bridge([tuple(s) for s in d["steps"]], decimals=dec, connectors=spec.get("connectors", "dashed"),
                                highlight=hl, callout=co, **pal)
    if t == "scatter":
        q = tuple(d["quadrants"]) if d.get("quadrants") else None
        return t, charts.scatter([tuple(p) for p in d["points"]], d.get("x_label", ""), d.get("y_label", ""), highlight=hl, quadrants=q, decimals=dec, **pal)
    if t == "heatmap":
        h = d.get("highlight_cells")
        h = [tuple(c) for c in h] if h else cells
        return t, charts.heatmap(d["rows"], d["cols"], d["values"], highlight=h, decimals=dec, bins=d.get("bins"),
                                 legend=d.get("legend", True), totals=d.get("totals", False), **pal)
    if t == "table_bars":
        return t, charts.table_bars(d["rows"], [tuple(c) for c in d["columns"]], bar=d["bar"], label=d.get("label", "label"), highlight=hl, **pal)
    if t == "sparkline":
        return t, charts.sparkline(d["values"], **pal)
    raise ValueError(f"unknown type {t}")


def caption(spec: dict) -> str:
    unit = spec["unit"][0] if isinstance(spec["unit"], list) else spec["unit"]
    parts = [f"واحد: {unit}"]
    for k in ("definition", "window"):
        if spec.get(k):
            parts.append(str(spec[k]).rstrip("."))
    if _source_text(spec):
        parts.append(_source_text(spec).rstrip("."))
    parts += [str(n).rstrip(".") for n in spec.get("notes", [])]
    return ". ".join(parts) + "."


EXHIBIT_FIELDS = ("exhibit_no", "subtitle", "base", "footnotes")


def _marks(s: str) -> str:
    """Escape text and turn footnote marks [1] into superscript Persian digits."""
    return re.sub(r"\[(\d+)\]", lambda m: f"<sup>{charts.fa(int(m.group(1)))}</sup>", _html.escape(str(s)))


def subtitle_parts(spec: dict) -> tuple[str, str]:
    """(bold part, regular part) of the exhibit subtitle: measure and cuts, then unit and base."""
    unit = spec["unit"][0] if isinstance(spec["unit"], list) else spec["unit"]
    sub_ = spec.get("subtitle")
    if isinstance(sub_, dict):
        bold = "، ".join(x for x in (sub_.get("measure"), sub_.get("cuts")) if x)
        unit, base = sub_.get("unit") or unit, sub_.get("base") or spec.get("base")
    else:
        bold = sub_ if isinstance(sub_, str) else "، ".join(str(spec[k]) for k in ("definition", "window") if spec.get(k))
        base = spec.get("base")
    return bold, f"{unit}" + (f" ({base})" if base else "")


def figure_html(spec: dict, svg: str) -> str:
    esc = lambda s: _html.escape(str(s))
    if not any(spec.get(k) for k in EXHIBIT_FIELDS):
        return (f'<figure class="chart">\n  <div class="chart-title">{esc(spec["message"])}</div>\n  {svg}\n'
                f'  <figcaption>{esc(caption(spec))}</figcaption>\n</figure>\n')
    out = ['<figure class="chart exhibit">']
    no = spec.get("exhibit_no")
    if no == "auto":
        out.append('  <div class="exhibit-no" data-auto="1"></div>')
    elif no is not None:
        out.append(f'  <div class="exhibit-no">نمودار {esc(charts.fa(no))}</div>')
    title = str(spec["message"]).strip()
    if title[-1] not in ".؟!?":
        title += "."
    out.append(f'  <div class="chart-title">{esc(title)}</div>')
    bold, reg = subtitle_parts(spec)
    out.append('  <div class="exhibit-sub">' + (f"<b>{_marks(bold)}،</b> " if bold else "") + f"{_marks(reg)}</div>")
    out.append(f"  {svg}")
    notes = []
    for i, fn in enumerate(spec.get("footnotes") or [], 1):
        notes.append(f'    <p class="exhibit-fn"><sup>{charts.fa(i)}</sup> {_marks(fn)}</p>')
    if spec.get("notes"):
        notes.append('    <p class="exhibit-note">یادداشت: ' + " ".join(_marks(n) for n in spec["notes"]) + "</p>")
    if notes:
        out.append('  <div class="exhibit-notes">\n' + "\n".join(notes) + "\n  </div>")
    if _source_text(spec):
        out.append(f'  <div class="exhibit-source">منبع: {_marks(_source_text(spec))}</div>')
    out.append("</figure>\n")
    return "\n".join(out)


EXHIBIT_CSS = """body{counter-reset:exhibit}
figure.exhibit{border:0;padding:0;background:none;margin:28px 0}
.exhibit-no{font-size:12px;font-weight:700;color:var(--muted,#6b6e7f);margin:0 0 6px}
.exhibit-no[data-auto]::before{counter-increment:exhibit;content:"نمودار " counter(exhibit,persian)}
.exhibit-sub{font-size:14px;line-height:1.7;color:var(--muted,#6b6e7f);margin:0 0 16px}.exhibit-sub b{color:var(--ink,#0f1020);font-weight:700}
.exhibit-notes{margin:10px 0 0;font-size:11.5px;line-height:1.7;color:var(--faint,#737584)}
.exhibit-notes p,.exhibit-source{margin:0}.exhibit-source{font-size:11.5px;line-height:1.7;color:var(--faint,#737584);margin-top:2px}"""


PREVIEW = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<link rel="stylesheet" href="{theme}/style.css"><link rel="stylesheet" href="{theme}/report.css">
<style>.chart-title{{font-weight:700;font-size:18px;line-height:1.6;color:var(--ink);margin:0 0 12px}} main{{padding:24px}}
{exhibit_css}</style>
</head><body><main><div class="col">{fig}</div></main></body></html>"""


def render(spec: dict, out: Path, png: bool = True) -> dict:
    errs = validate(spec)
    if errs:
        raise ValueError("invalid spec:\n- " + "\n- ".join(errs))
    t, svg = build_svg(spec)
    out.mkdir(parents=True, exist_ok=True)
    (out / "chart.svg").write_text(svg, encoding="utf-8")
    fig = figure_html(spec, svg)
    (out / "figure.html").write_text(fig, encoding="utf-8")
    res = {"type": t, "svg": str(out / "chart.svg"), "figure": str(out / "figure.html")}
    if png:
        tdir = HERE / "themes" / str(spec.get("theme") or "editorial")
        theme = (tdir if (tdir / "style.css").is_file() else HERE / "themes" / "editorial").resolve().as_uri()
        prev = out / "preview.html"
        prev.write_text(PREVIEW.format(theme=theme, fig=fig, exhibit_css=EXHIBIT_CSS), encoding="utf-8")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
            pg = b.new_page(viewport={"width": 760, "height": 800}, device_scale_factor=2)
            pg.goto(prev.resolve().as_uri())
            pg.wait_for_load_state("networkidle")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(200)
            font_ok = pg.evaluate("document.fonts.check('16px Vazirmatn')")
            pg.locator("figure.chart").screenshot(path=str(out / "chart.png"))
            b.close()
        res.update(png=str(out / "chart.png"), font=font_ok)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("validate", "choose"):
        sub.add_parser(c).add_argument("spec")
    r = sub.add_parser("render"); r.add_argument("spec"); r.add_argument("--out", required=True); r.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    if a.cmd == "validate":
        errs = validate(spec)
        print("ok" if not errs else "\n".join("- " + e for e in errs))
        if not errs:
            for w in warnings(spec):
                print("warning: " + w)
        return 1 if errs else 0
    if a.cmd == "choose":
        t, why = (spec["type"], "set explicitly in the spec") if spec.get("type", "auto") != "auto" else choose(spec)
        print(json.dumps({"type": t, "why": why}, ensure_ascii=False))
        return 0 if t else 1
    try:
        print(json.dumps(render(spec, Path(a.out), png=not a.no_png), ensure_ascii=False))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
