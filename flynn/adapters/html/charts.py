#!/usr/bin/env python3
"""flynn HTML adapter: charts as inline SVG in the editorial language.

  from charts import bar_h, line, columns, bridge, dot_range, stacked_share, small_multiples, sparkline,
                     table_bars, scatter, heatmap, fa
  html = bar_h([("گوشی موبایل", 412), ...], highlight="گوشی موبایل")

  Callers with data rather than code use chartspec.py (the clu handoff); the choice table and the rules live in
  skills/flynn-core/references/dataviz.md.

  charts.py demo <out.html>      render every chart type into a page, for review

Rules the functions enforce (data-viz lessons; see components.md, "Charts"):
  - one message per chart: the title states it, the chart proves it;
  - label directly (value at the bar end, series name at the line end); no legend boxes;
  - red (--accent) marks only the one thing the title is about; everything else is ink or grey;
  - light horizontal gridlines only, no chart borders, no 3D, no gradients;
  - Persian digits everywhere; bars grow from the right (RTL); time runs left to right, like a
    dashboard, with Persian labels;
  - bars start at zero; a line chart may start above zero but says so in the axis labels.
SVGs inherit the page font and use currentColor-free explicit token colours so they also render in PNG/PDF."""
from __future__ import annotations

import functools as _functools
import html as _html
import json as _json
import sys
from contextlib import contextmanager
from pathlib import Path as _Path

INK, TEXT, MUTED, FAINT, LINE, SOFT, ACCENT = "#0f1020", "#2e3040", "#6b6e7f", "#737584", "#ebebf0", "#f6f6f9", "#0f766e"
GREY_SERIES = ("#6b6e7f", "#a3a5b3", "#c7c8d1")
HAIR = "#c7c8d1"  # hairline strokes: grids drawn on top of fills, connectors, context lines
RAMP = ("#0f1020", "#454759", "#6b6e7f", "#9a9cab", "#c7c8d1", "#e3e4ea")  # ink -> light grey, for stacks and bins
RX = None  # None keeps each chart's own corner radius; a palette sets it to 0 for square bars
HALO = 'stroke="#fff" stroke-width="4" stroke-linejoin="round" paint-order="stroke"'  # keeps labels readable over lines
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# The colours every chart reads. The module globals above hold the ACTIVE palette; `palette=` on any chart function
# (a name from PALETTES, a dict of overrides, or a theme folder name) swaps them for that call only. "editorial"
# is today's look and the default, so nothing existing changes.
PALETTES = {
    "editorial": {"INK": INK, "TEXT": TEXT, "MUTED": MUTED, "FAINT": FAINT, "LINE": LINE, "SOFT": SOFT, "ACCENT": ACCENT,
                     "GREY_SERIES": GREY_SERIES, "HAIR": HAIR, "RAMP": RAMP, "RX": None},
}
_PAL_KEYS = tuple(PALETTES["editorial"])
_THEMES_DIR = _Path(__file__).resolve().parent / "themes"


def load_palette(palette) -> dict:
    """Resolve a palette: None -> editorial; a dict -> editorial with those keys overridden; a string -> a name in
    PALETTES, else themes/<name>/palette.json (same keys, any subset). Raises ValueError for an unknown name."""
    base = dict(PALETTES["editorial"])
    if palette is None:
        return base
    if isinstance(palette, dict):
        over = palette
    elif palette in PALETTES:
        over = PALETTES[palette]
    else:
        f = _THEMES_DIR / str(palette) / "palette.json"
        if not f.is_file():
            raise ValueError(f"unknown palette/theme {palette!r}: not in PALETTES and no {f}")
        over = _json.loads(f.read_text(encoding="utf-8"))
    unknown = set(over) - set(_PAL_KEYS)
    if unknown:
        raise ValueError(f"palette keys not recognised: {sorted(unknown)}; allowed: {list(_PAL_KEYS)}")
    base.update(over)
    return base


@contextmanager
def use_palette(palette):
    """Make `palette` the active one inside the block, then restore the previous values."""
    g = globals()
    new = load_palette(palette)
    old = {k: g[k] for k in _PAL_KEYS}
    try:
        g.update(new)
        g["GREY_SERIES"] = tuple(new["GREY_SERIES"])
        g["RAMP"] = tuple(new["RAMP"])
        yield
    finally:
        g.update(old)


def _themed(fn):
    """Give a chart function a `palette=` keyword; without it the active palette is used unchanged."""
    @_functools.wraps(fn)
    def wrapper(*a, palette=None, **k):
        if palette is None:
            return fn(*a, **k)
        with use_palette(palette):
            return fn(*a, **k)
    return wrapper


def _rx(default) -> str:
    """Corner radius for bars: the chart's own default unless the palette sets RX (0 = square)."""
    return f"{default if RX is None else RX:g}"


def _sgn(v, decimals: int = 0, plus: bool = False) -> str:
    """A signed number inside an LTR isolate, with the true minus: «−۱۷»; plus=True adds «+» to positives."""
    if v < 0:
        return "⁦−" + fa(abs(v), decimals) + "⁩"
    return "⁦+" + fa(v, decimals) + "⁩" if plus and v > 0 else fa(v, decimals)


def _dark(c: str) -> bool:
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140


def fa(n, decimals: int = 0, sep: bool = True) -> str:
    """Persian digits, '٬' thousands separator, '.' decimal point (like the Admin Panel UI)."""
    if isinstance(n, str):
        return n.translate(FA)
    s = f"{n:,.{decimals}f}" if sep else f"{n:.{decimals}f}"
    return s.replace(",", "٬").translate(FA)


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _t(x, y, s, align="right", size=13, weight=400, fill=TEXT, extra="") -> str:
    """Text in an RTL page. align='right': the text's right edge is at x (it runs leftwards from x);
    align='left': its left edge is at x (it runs rightwards); 'center': centred on x."""
    anchor = {"right": "start", "left": "end", "center": "middle"}[align]
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" direction="rtl" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" {extra}>{_esc(s)}</text>')


def _nice_max(v: float) -> float:
    import math
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def _svg(w, h, body, label) -> str:
    return (f'<svg class="chart-svg" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_esc(label)}" '
            f'xmlns="http://www.w3.org/2000/svg" style="display:block;font-family:inherit">{body}</svg>')


@_themed
def bar_h(items, unit: str = "", highlight=None, width: int = 680, decimals: int = 0, label_w: int = 150,
          row_h: int = 34, note: str = "", zero_line=None, ref_lines=None, annotations=None) -> str:
    """Ranked horizontal bars, growing from the right. items: [(label, value)], shown in the given order.
    Negative values grow the other way from a drawn zero line (same colour, value label on the empty side).
    ref_lines=[(value, label)] draws dashed vertical references; annotations=[{"at": label, "text": ..}] puts a short
    note beside that row's value."""
    vals = [v for _, v in items]
    vmax = max(max(vals), 0) or 1
    lo = min(0, min(vals))
    neg = lo < 0
    plot_r, plot_l = width - label_w, 70
    scale = (plot_r - plot_l) / (vmax - lo)
    x0 = plot_r - (-lo) * scale  # where zero sits; plot_r when nothing is negative
    off = 18 if ref_lines else 0
    h = len(items) * row_h + (36 if (note or unit) else 8) + off
    notes_at = {a.get("at"): a.get("text", "") for a in (annotations or [])}
    out = []
    for i, (lab, v) in enumerate(items):
        y = i * row_h + 4 + off
        hot = lab == highlight or (isinstance(highlight, (set, list, tuple)) and lab in highlight)
        bw = max(abs(v) * scale, 1)
        col = ACCENT if hot else MUTED if highlight else INK
        out.append(_t(width - 2, y + row_h / 2 + 4, lab, "right", 13.5, 700 if hot else 500, INK if hot else TEXT))
        bx = x0 - bw if v >= 0 else x0
        out.append(f'<rect x="{bx:.1f}" y="{y + 7}" width="{bw:.1f}" height="{row_h - 14}" rx="{_rx(4)}" fill="{col}" opacity="{1 if hot or not highlight else .55}"/>')
        vtxt = _sgn(v, decimals) if v < 0 else fa(v, decimals)
        vx = x0 - bw - 8 if v >= 0 else x0 - 8
        out.append(_t(vx, y + row_h / 2 + 4, vtxt, "right", 13, 700 if hot else 500, ACCENT if hot else TEXT))
        if lab in notes_at and v >= 0:
            ax = vx - _tw(vtxt, 13) - 10
            if ax - _tw(notes_at[lab], 11.5) > 0:
                out.append(_t(ax, y + row_h / 2 + 4, notes_at[lab], "right", 11.5, 600, MUTED, 'font-style="italic"'))
    if neg or zero_line:
        out.append(f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="{off}" y2="{len(items) * row_h + 4 + off}" stroke="{INK}" stroke-width="1"/>')
    for val, rlab in (ref_lines or []):
        rx_ = x0 - val * scale
        out.append(f'<line x1="{rx_:.1f}" x2="{rx_:.1f}" y1="{off - 2}" y2="{len(items) * row_h + 4 + off}" stroke="{FAINT}" stroke-dasharray="4 3"/>')
        out.append(_t(rx_, 12, f"{rlab} {fa(val, decimals)}", "center", 11.5, 700, TEXT, 'font-style="italic"'))
    if note or unit:
        out.append(_t(width - 2, h - 4, note or f"واحد: {unit}", "right", 12, 400, FAINT))
    return _svg(width, h, "".join(out), note or unit)


@_themed
def line(series: dict, x_labels, highlight=None, unit: str = "", width: int = 680, height: int = 280,
         band=None, decimals: int = 0, y_min=0, ticks: int = 4, end_values: bool = False, context=None,
         event=None, dashed_from=None, ref_lines=None) -> str:
    """Time series. series: {name: [values]}; the highlighted series is red and thick, the rest grey.
    band = (i_start, i_end, label) shades a period (for example a campaign window). Names label the line ends.
    context=[names] draws those series as thin light-grey lines with no label (the field the highlight stands out
    from). event={"x": label or index, "text": ..} rings the highlighted line at that point with a dotted drop line
    and a short note. dashed_from=i draws every plotted line dashed after point i (a forecast).
    ref_lines=[(value, label)] draws dashed horizontal references."""
    names = list(series)
    ctx = [s for s in (context or []) if s in series]
    n = len(x_labels)
    vmax = _nice_max(max(max(v) for v in series.values()))
    lo = y_min if y_min is not None else 0
    L, R, T, B = 44, width - 110, 34, height - 34
    X = lambda i: L + (R - L) * (i / (n - 1 if n > 1 else 1))
    Y = lambda v: B - (B - T) * ((v - lo) / (vmax - lo))
    out = []
    if band:
        i0, i1, blab = band
        out.append(f'<rect x="{X(i0):.1f}" y="{T}" width="{X(i1) - X(i0):.1f}" height="{B - T}" fill="{SOFT}"/>')
        out.append(_t((X(i0) + X(i1)) / 2, T + 14, blab, "center", 12, 700, MUTED))
    for k in range(ticks + 1):
        v = lo + (vmax - lo) * k / ticks
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{LINE}" stroke-width="1"/>')
        out.append(_t(L - 8, Y(v) + 4, fa(v, decimals), "right", 11.5, 400, FAINT))
    step = max(1, round(n / 7))
    for i in range(0, n, step):
        out.append(_t(X(i), height - 12, x_labels[i], "center", 11.5, 400, FAINT))
    for val, rlab in (ref_lines or []):
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(val):.1f}" y2="{Y(val):.1f}" stroke="{FAINT}" stroke-dasharray="4 3"/>')
        out.append(_t(L + 4, Y(val) - 5, f"{rlab} {fa(val, decimals)}", "left", 11.5, 700, TEXT, 'font-style="italic" ' + HALO))
    for s in ctx:  # context goes underneath the highlight: thin, light, unlabelled
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(series[s]))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{HAIR}" stroke-width="1" stroke-linejoin="round"/>')
    plotted = [s for s in names if s not in ctx]
    order = [s for s in plotted if s != highlight] + ([highlight] if highlight in plotted else [])
    ends = []
    for j, name in enumerate(order):
        hot = name == highlight
        col = ACCENT if hot else GREY_SERIES[min(j, len(GREY_SERIES) - 1)] if highlight else (INK if j == 0 else GREY_SERIES[min(j - 1, len(GREY_SERIES) - 1)])
        vals = series[name]
        sw = 3 if hot else 2
        if dashed_from is not None and 0 < dashed_from < n - 1:
            pa = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals) if i <= dashed_from)
            pb = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals) if i >= dashed_from)
            out.append(f'<polyline points="{pa}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round"/>')
            out.append(f'<polyline points="{pb}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-dasharray="5 4" stroke-linejoin="round"/>')
        else:
            pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
            out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round"/>')
        yv = Y(vals[-1])
        out.append(f'<circle cx="{X(n - 1):.1f}" cy="{yv:.1f}" r="{4 if hot else 3}" fill="{col}"/>')
        ends.append([yv, name, vals[-1], col, hot])
    ends.sort()
    for a, b in zip(ends, ends[1:]):  # keep end labels from colliding
        if b[0] - a[0] < 16:
            b[0] = a[0] + 16
    for yv, name, v, col, hot in ends:
        # the last day's value is rarely the point, so line ends carry the series name only unless asked
        out.append(_t(R + 10, yv + 4, f"{name} {fa(v, decimals)}" if end_values else name, "left", 12.5, 700 if hot else 500, col))
    if event and order:
        tgt = highlight if highlight in series else order[-1]
        ex = event.get("x")
        ei = ex if isinstance(ex, int) else (list(x_labels).index(ex) if ex in list(x_labels) else None)
        if ei is not None:
            px, py = X(ei), Y(series[tgt][ei])
            out.append(f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{py + 7:.1f}" y2="{B}" stroke="{FAINT}" stroke-dasharray="1 3" stroke-linecap="round"/>')
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6.5" fill="#fff" stroke="{ACCENT}" stroke-width="2"/>')
            txt = str(event.get("text", ""))
            tw = _tw(txt, 11.5)
            cx = min(max(px, L + tw / 2), R - tw / 2)
            ty = py - 14 if py - 14 > T - 6 else py + 24
            out.append(_t(cx, ty, txt, "center", 11.5, 700, TEXT, HALO))
    if unit:
        out.append(_t(0, 14, unit, "left", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit)


@_themed
def columns(groups, series_order, highlight=None, unit: str = "", width: int = 680, height: int = 260,
            decimals: int = 0, single=None, value_pos: str = "outside", zero_line=None, ref_lines=None) -> str:
    """Grouped columns, for example before/after per segment. groups: [(label, {series: value})].
    A one-series call (single, automatic when there is one series) draws one bar per group, 58% of its slot, and
    `highlight` then names the GROUP that is red; with several series `highlight` names the series.
    Negative values hang below a drawn zero line in the same colour. value_pos: "outside" (above the bar, the
    default), "inside" (white on dark bars) or "auto" (inside when the bar is tall enough, else outside).
    ref_lines=[(value, label)] draws dashed horizontal references."""
    k = len(series_order)
    single = (k == 1) if single is None else single
    vals = [v for _, d in groups for v in d.values()]
    hi = _nice_max(max(vals))
    lo = -_nice_max(-min(vals)) if min(vals) < 0 else 0
    L, R, T, B = (104 if ref_lines else 20), width - 20, 24, height - 40 - (22 if lo < 0 else 0)
    gw = (R - L) / len(groups)
    cw = min(180, gw * 0.58) + 4 if single else min(34, gw * 0.7 / k)
    Y = lambda v: B - (B - T) * (v - lo) / (hi - lo)
    Y0 = Y(0)
    neg = lo < 0
    out = []
    if not (neg or zero_line):
        out.append(f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="{LINE}"/>')
    for val, rlab in (ref_lines or []):
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(val):.1f}" y2="{Y(val):.1f}" stroke="{FAINT}" stroke-dasharray="4 3"/>')
        out.append(_t(L - 6, Y(val) + 4, f"{rlab} {fa(val, decimals)}", "right", 11.5, 700, TEXT, 'font-style="italic"'))
    for gi, (lab, d) in enumerate(groups):
        # RTL: first group on the right
        cx = R - gw * (gi + 0.5)
        for si, s in enumerate(series_order):
            v = d.get(s, 0)
            hot = _hot(lab, highlight) if single else s == highlight
            x = cx - (cw - 4) / 2 if single else cx + (k / 2 - si - 1) * cw + 2
            top, hgt = min(Y(v), Y0), abs(Y(v) - Y0)
            if single:
                col = ACCENT if hot else (MUTED if highlight is not None else INK)
            else:
                col = ACCENT if hot else (MUTED if highlight else GREY_SERIES[si % 3] if si else INK)
            out.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{cw - 4:.1f}" height="{hgt:.1f}" rx="{_rx(3)}" fill="{col}"/>')
            inside = value_pos == "inside" or (value_pos == "auto" and hgt >= 20 and cw - 4 >= 28)
            txt = _sgn(v, decimals) if v < 0 else fa(v, decimals)
            if inside and hgt >= 18:
                ty = top + 16 if v >= 0 else top + hgt - 7
                out.append(_t(x + (cw - 4) / 2, ty, txt, "center", 11.5, 700, "#fff" if _dark(col) else INK))
            else:
                ty = top - 6 if v >= 0 else top + hgt + 15
                out.append(_t(x + (cw - 4) / 2, ty, txt, "center", 11.5, 700 if hot else 500, ACCENT if hot else TEXT))
        out.append(_t(cx, height - 22, lab, "center", 12.5, 500, TEXT))
    if neg or zero_line:
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y0:.1f}" y2="{Y0:.1f}" stroke="{INK}" stroke-width="1"/>')
    if not single:
        # series key as inline text, right-aligned, since columns cannot be labelled at their ends
        x = R
        for si, s in enumerate(series_order):
            col = ACCENT if s == highlight else (MUTED if highlight else GREY_SERIES[si % 3] if si else INK)
            out.append(f'<rect x="{x - 10}" y="4" width="10" height="10" rx="{_rx(2)}" fill="{col}"/>')
            out.append(_t(x - 16, 13, s, "right", 12, 600, TEXT))
            x -= 16 + 8 * len(s) + 26
    if unit:
        out.append(_t(L, 13, unit, "left", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit)


@_themed
def bridge(steps, unit: str = "", width: int = 680, height: int = 260, decimals: int = 0, connectors: str = "dashed",
           highlight=None, callout=None) -> str:
    """A bridge (waterfall) from one total to another, first step on the right (RTL).
    steps: [(label, value, kind)] with kind "total" or "subtotal" (a bar from zero; a subtotal's value is the running
    total at that point) or "delta" (floats from the running total). Totals are ink.
    Default colours: negative deltas red, positive grey. With `highlight` (a step label or list) only those steps are
    red and every other delta is grey, loss or gain (red marks the point, not "bad").
    connectors: "dashed" (default) or "solid" (a hairline). callout={"step": label, "big": "۳۶٪", "text": "..."} sets a
    header and a big number in the blank space above that step. Totals below zero are allowed (a drawn zero line)."""
    run_tot, bars = 0.0, []
    for lab, v, kind in steps:
        if kind in ("total", "subtotal"):
            lo_, hi_ = sorted((0, v)); bars.append((lab, lo_, hi_, v, kind)); run_tot = v
        else:
            lo_, hi_ = sorted((run_tot, run_tot + v)); bars.append((lab, lo_, hi_, v, kind)); run_tot += v
    vmax = _nice_max(max(b[2] for b in bars))
    vmin = min(b[1] for b in bars)
    lo = -_nice_max(-vmin) if vmin < 0 else 0
    extra = (54 if callout.get("big") else 28) if callout else 0
    height += extra
    L, R, T, B = 20, width - 20, 24 + extra, height - 40
    gw = (R - L) / len(bars)
    bw = min(96, gw * 0.55)
    Y = lambda v: B - (B - T) * (v - lo) / (vmax - lo)
    out = []
    if lo < 0:
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="{INK}" stroke-width="1"/>')
    else:
        out.append(f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="{LINE}"/>')
    prev_top = None
    for i, (lab, lo_, hi_, v, kind) in enumerate(bars):
        cx = R - gw * (i + 0.5)
        is_tot = kind in ("total", "subtotal")
        hot = highlight is not None and _hot(lab, highlight)
        if highlight is None:
            col = INK if is_tot else (ACCENT if v < 0 else MUTED)
            tcol = ACCENT if (not is_tot and v < 0) else INK
        else:
            col = INK if is_tot else (ACCENT if hot else MUTED)
            tcol = ACCENT if hot else INK
        out.append(f'<rect x="{cx - bw / 2:.1f}" y="{Y(hi_):.1f}" width="{bw:.1f}" height="{Y(lo_) - Y(hi_):.1f}" rx="{_rx(3)}" fill="{col}"/>')
        # a signed number in RTL text flips its sign to the wrong end; an LTR isolate keeps «−۱۲۰» intact
        txt = _sgn(v, decimals) if is_tot else _sgn(v, decimals, plus=True) if v >= 0 else _sgn(v, decimals)
        out.append(_t(cx, Y(hi_) - 8, txt, "center", 13, 800, tcol))
        out.append(_t(cx, B + 18, lab, "center", 12.5, 500, TEXT))
        if callout and callout.get("step") == lab:
            cxx = min(max(cx, L + 40), R - 40)
            if callout.get("big"):
                out.append(_t(cxx, Y(hi_) - 34, callout["big"], "center", 22, 800, ACCENT))
                if callout.get("text"):
                    out.append(_t(cxx, Y(hi_) - 62, callout["text"], "center", 11.5, 600, TEXT))
            elif callout.get("text"):
                out.append(_t(cxx, Y(hi_) - 34, callout["text"], "center", 13, 800, ACCENT))
        if prev_top is not None:  # connector from the previous bar's running total
            style = f'stroke="{FAINT}" stroke-dasharray="3 3"' if connectors != "solid" else f'stroke="{FAINT}" stroke-width=".75"'
            out.append(f'<line x1="{prev_top[0]:.1f}" x2="{cx + bw / 2:.1f}" y1="{prev_top[1]:.1f}" y2="{prev_top[1]:.1f}" {style}/>')
        end = hi_ if (is_tot or v >= 0) else lo_
        if is_tot and v < 0:
            end = lo_
        prev_top = (cx - bw / 2, Y(end))
    if unit:
        out.append(_t(L, 13, unit, "left", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit)


# ---------- more chart types (sub-project 5) ----------

def _hot(label, highlight) -> bool:
    if highlight is None:
        return False
    if isinstance(highlight, (set, list, tuple, frozenset)):
        return label in highlight
    return label == highlight


def _tw(s, size=12.5) -> float:
    """Rough text width for collision checks (Vazirmatn, Persian and digits)."""
    return len(str(s)) * size * 0.56 + 4


def _nice_range(lo: float, hi: float) -> tuple[float, float]:
    """A padded range for axes that need not start at zero (dumbbells, scatter)."""
    import math
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    step = 10 ** math.floor(math.log10(span))
    for m in (1, 2, 2.5, 5, 10):
        if span / (m * step) <= 5:
            step = m * step
            break
    return math.floor(lo / step) * step, math.ceil(hi / step) * step


def _step(lo: float, hi: float) -> float:
    import math
    span = (hi - lo) or 1
    step = 10 ** math.floor(math.log10(span))
    for m in (1, 2, 2.5, 5, 10):
        if span / (m * step) <= 5:
            return m * step
    return 10 * step


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Ticks on a nice step between lo and hi (never quarter-spans that round to duplicate labels)."""
    st = _step(lo, hi)
    out, v = [], lo
    while v <= hi + st * 1e-9:
        out.append(round(v, 10))
        v += st
    return out


def _tick_dec(ticks) -> int:
    return 0 if all(float(t).is_integer() for t in ticks) else 1


@_themed
def dot_range(rows, highlight=None, labels=("قبل", "بعد"), width: int = 680, row_h: int = 36, decimals: int = 0,
              label_w: int = 150, zero: bool = False) -> str:
    """Before -> after per row (a dumbbell). rows: [(label, before, after)]. The hollow dot is 'before', the filled
    dot 'after'; the highlighted rows' after-dot is red. Values grow leftwards (RTL, like bar_h). The first row
    carries the two dot names, so no legend is needed. zero=True starts the axis at zero."""
    vals = [v for _, a, b in rows for v in (a, b)]
    lo, hi = _nice_range(0 if zero else min(vals), max(vals))
    plot_r, plot_l = width - label_w, 40
    X = lambda v: plot_r - (v - lo) / (hi - lo) * (plot_r - plot_l)
    top = 26
    h = top + len(rows) * row_h + 26
    out = []
    tks = _ticks(lo, hi)
    tdec = 0 if all(float(v).is_integer() for v in tks) else decimals
    for v in tks:
        out.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{top - 6}" y2="{h - 22}" stroke="{LINE}"/>')
        out.append(_t(X(v), h - 6, fa(v, tdec), "center", 11.5, 400, FAINT))
    for i, (lab, a, b) in enumerate(rows):
        y = top + i * row_h + row_h / 2
        hot = _hot(lab, highlight)
        col = ACCENT if hot else INK
        out.append(_t(width - 2, y + 4, lab, "right", 13.5, 700 if hot else 500, INK if hot else TEXT))
        out.append(f'<line x1="{X(a):.1f}" x2="{X(b):.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{col if hot else MUTED}" stroke-width="{3 if hot else 2}" opacity="{1 if hot else .6}"/>')
        out.append(f'<circle cx="{X(a):.1f}" cy="{y:.1f}" r="5" fill="#fff" stroke="{MUTED}" stroke-width="2"/>')
        out.append(f'<circle cx="{X(b):.1f}" cy="{y:.1f}" r="6" fill="{col}"/>')
        # value labels outside the pair: after-value beyond the after-dot, before-value beyond the before-dot
        grow = b >= a
        out.append(_t(X(b) - 10 if grow else X(b) + 10, y + 4, fa(b, decimals), "right" if grow else "left", 12.5, 700, col))
        out.append(_t(X(a) + 10 if grow else X(a) - 10, y + 4, fa(a, decimals), "left" if grow else "right", 11.5, 400, FAINT))
        if i == 0:  # name the two dots once; push the names outward when they would touch
            ta, tb = _tw(labels[0], 11.5), _tw(labels[1], 11.5)
            if abs(X(a) - X(b)) >= (ta + tb) / 2 + 10:
                out.append(_t(X(a), y - 13, labels[0], "center", 11.5, 600, MUTED))
                out.append(_t(X(b), y - 13, labels[1], "center", 11.5, 700, col))
            else:
                a_left = X(a) < X(b)
                out.append(_t(X(a) + (6 if not a_left else -6), y - 13, labels[0], "left" if not a_left else "right", 11.5, 600, MUTED))
                out.append(_t(X(b) + (6 if a_left else -6), y - 13, labels[1], "left" if a_left else "right", 11.5, 700, col))
    return _svg(width, h, "".join(out), f"{labels[0]} و {labels[1]}")


@_themed
def stacked_share(rows, parts, highlight=None, width: int = 680, row_h: int = 34, label_w: int = 150, decimals: int = 0) -> str:
    """100% bars for mix or share. rows: [(label, {part: value})]; values are normalised per row. Segments run from
    the right in `parts` order. The highlighted part is red, the others step from ink to light grey. Part names sit
    above the first row; names that would collide go to a key line under the chart."""
    # without a highlight the parts step from ink to light grey; with one, the others stay light so red carries it
    ramp = ("#8a8c9a", "#b9bac5", "#d6d7de", "#e4e5ea") if highlight is not None else (INK, MUTED, "#a3a5b3", "#c7c8d1", "#dcdde3")
    colors, k = {}, 0
    for p in parts:
        if _hot(p, highlight):
            colors[p] = ACCENT
        else:
            colors[p] = ramp[min(k, len(ramp) - 1)]
            k += 1
    plot_r, plot_l = width - label_w, 4
    W = plot_r - plot_l
    top = 30
    h = top + len(rows) * row_h + 8
    out, key = [], []
    placed = []  # (x0, x1) of names above the first row
    for i, (lab, d) in enumerate(rows):
        tot = sum(max(d.get(p, 0), 0) for p in parts) or 1
        y = top + i * row_h
        out.append(_t(width - 2, y + row_h / 2 + 4, lab, "right", 13.5, 500, TEXT))
        x = plot_r
        for p in parts:
            share = max(d.get(p, 0), 0) / tot
            w = share * W
            if w <= 0:
                continue
            out.append(f'<rect x="{x - w:.1f}" y="{y + 6}" width="{max(w - 1.5, .5):.1f}" height="{row_h - 12}" fill="{colors[p]}"/>')
            pct = fa(share * 100, decimals) + "٪"
            if w >= _tw(pct, 12) + 6:
                dark = colors[p] in (INK, MUTED, ACCENT, "#8a8c9a")
                out.append(_t(x - w / 2, y + row_h / 2 + 4, pct, "center", 12, 700, "#fff" if dark else INK))
            if i == 0:
                cx, tw = x - w / 2, _tw(p, 11.5)
                x0, x1 = cx - tw / 2, cx + tw / 2
                if w >= tw * 0.6 and all(x1 < a or x0 > b for a, b in placed) and x0 > 0:
                    placed.append((x0, x1))
                    out.append(_t(cx, top - 8, p, "center", 11.5, 700 if colors[p] == ACCENT else 600, ACCENT if colors[p] == ACCENT else TEXT))
                else:
                    key.append(p)
            x -= w
    if key:
        h += 22
        x = plot_r
        for p in key:
            out.append(f'<rect x="{x - 9}" y="{h - 16}" width="9" height="9" rx="{_rx(2)}" fill="{colors[p]}"/>')
            out.append(_t(x - 14, h - 8, p, "right", 11.5, 500, TEXT))
            x -= _tw(p, 11.5) + 30
    return _svg(width, h, "".join(out), "، ".join(parts))


@_themed
def small_multiples(series: dict, x_labels, highlight=None, cols: int = 3, width: int = 680, cell_h: int = 118,
                    decimals: int = 0, y_min=0) -> str:
    """A grid of mini line charts sharing one y-scale, first cell on the right (RTL reading order). Each cell is
    titled with its series name; the highlighted series is red. Time runs left to right inside each cell."""
    names = list(series)
    rows = (len(names) + cols - 1) // cols
    gap = 18
    cw = (width - gap * (cols - 1)) / cols
    vmax = _nice_max(max(max(v) for v in series.values()))
    lo = y_min if y_min is not None else min(min(v) for v in series.values())
    n = len(x_labels)
    h = rows * (cell_h + 30) + 4
    out = []
    for idx, name in enumerate(names):
        r, c = divmod(idx, cols)
        x0 = width - (c + 1) * cw - c * gap
        y0 = r * (cell_h + 30)
        L, R, T, B = x0 + 4, x0 + cw - 4, y0 + 26, y0 + cell_h
        hot = _hot(name, highlight)
        col = ACCENT if hot else INK
        out.append(_t(R, y0 + 15, name, "right", 12.5, 700 if hot else 600, col if hot else TEXT))
        for v in (lo, vmax):
            yy = B - (B - T) * (v - lo) / (vmax - lo)
            out.append(f'<line x1="{L:.1f}" x2="{R:.1f}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{LINE}"/>')
        X = lambda i: L + (R - L - 34) * (i / (n - 1 if n > 1 else 1))
        Y = lambda v: B - (B - T) * (v - lo) / (vmax - lo)
        vals = series[name]
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{2.5 if hot else 1.8}" stroke-linejoin="round"/>')
        out.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(vals[-1]):.1f}" r="3" fill="{col}"/>')
        out.append(_t(X(n - 1) + 6, Y(vals[-1]) + 4, fa(vals[-1], decimals), "left", 11.5, 700, col))
        if r == rows - 1 or idx + cols >= len(names):
            out.append(_t(L, B + 16, x_labels[0], "left", 10.5, 400, FAINT))
            out.append(_t(X(n - 1), B + 16, x_labels[-1], "center", 10.5, 400, FAINT))
        if c == cols - 1 or idx == len(names) - 1:  # shared scale, printed once per row inside the leftmost cell
            out.append(_t(L, T - 4, fa(vmax, decimals), "left", 10, 400, FAINT))
            out.append(_t(L, B - 4, fa(lo, decimals), "left", 10, 400, FAINT))
    return _svg(width, h, "".join(out), "، ".join(names))


@_themed
def sparkline(values, width: int = 120, height: int = 32, highlight_last: bool = True, color: str = INK,
              band=None) -> str:
    """A word-sized trend for KPI cards and tables. Time runs left to right; the last point is marked (red when
    highlight_last). band=(i0, i1) shades a period. Returns an inline SVG with a fixed size."""
    n = len(values)
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    X = lambda i: 3 + (width - 6) * (i / (n - 1 if n > 1 else 1))
    Y = lambda v: height - 4 - (height - 8) * (v - lo) / (hi - lo)
    out = []
    if band:
        out.append(f'<rect x="{X(band[0]):.1f}" y="0" width="{X(band[1]) - X(band[0]):.1f}" height="{height}" fill="{SOFT}"/>')
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(values))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>')
    out.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(values[-1]):.1f}" r="2.6" fill="{ACCENT if highlight_last else color}"/>')
    return (f'<svg class="sparkline" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
            f'aria-label="روند" xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle">{"".join(out)}</svg>')


@_themed
def table_bars(rows, columns, bar: str, label: str = "label", highlight=None, width: int = 680, row_h: int = 34,
               bar_w: int = 200, label_w: int = 150) -> str:
    """A ranked table with an inline bar for one column. rows: [{label: .., <key>: ..}]; columns:
    [(key, header, decimals)] for the extra numeric columns, drawn left of the bar. The label column is on the right,
    then the bar with its value, then the other columns. The highlighted row is bold with a red bar."""
    col_w = (width - label_w - bar_w - 16) / max(len(columns), 1)
    top = 28
    h = top + len(rows) * row_h + 6
    vmax = max(r[bar] for r in rows) or 1
    bx_r = width - label_w - 8
    out = [f'<line x1="0" x2="{width}" y1="{top - 6}" y2="{top - 6}" stroke="{LINE}"/>']
    bar_hdr = next((hd for k, hd, _ in columns if k == bar), None)
    others = [c for c in columns if c[0] != bar]
    out.append(_t(bx_r, 14, bar_hdr or "", "right", 11.5, 700, FAINT))
    for j, (k, hd, _) in enumerate(others):
        cx = bx_r - bar_w - 8 - (j + 0.5) * col_w
        out.append(_t(cx, 14, hd, "center", 11.5, 700, FAINT))
    bar_dec = next((d for k, _, d in columns if k == bar), 0)
    for i, r in enumerate(rows):
        y = top + i * row_h
        hot = _hot(r[label], highlight)
        out.append(_t(width - 2, y + row_h / 2 + 4, r[label], "right", 13.5, 700 if hot else 500, INK if hot else TEXT))
        bw = max(r[bar] / vmax * (bar_w - 56), 1)
        out.append(f'<rect x="{bx_r - bw:.1f}" y="{y + 9}" width="{bw:.1f}" height="{row_h - 18}" rx="{_rx(3)}" fill="{ACCENT if hot else (MUTED if highlight is not None else INK)}" opacity="{1 if hot or highlight is None else .55}"/>')
        out.append(_t(bx_r - bw - 6, y + row_h / 2 + 4, fa(r[bar], bar_dec), "right", 12.5, 700 if hot else 500, ACCENT if hot else TEXT))
        for j, (k, _, d) in enumerate(others):
            cx = bx_r - bar_w - 8 - (j + 0.5) * col_w
            out.append(_t(cx, y + row_h / 2 + 4, fa(r[k], d) if isinstance(r[k], (int, float)) else r[k], "center", 12.5, 700 if hot else 400, INK if hot else TEXT))
        out.append(f'<line x1="0" x2="{width}" y1="{y + row_h:.1f}" y2="{y + row_h:.1f}" stroke="{LINE}"/>')
    return _svg(width, h, "".join(out), bar_hdr or bar)


@_themed
def scatter(points, x_label: str = "", y_label: str = "", highlight=None, quadrants=None, width: int = 680,
            height: int = 360, decimals: int = 0, label_all: bool | None = None) -> str:
    """Two measures per item. points: [(label, x, y)]. Numeric x grows left to right (like time); y grows upward.
    Highlighted points are red and labelled; with 8 points or fewer every point is labelled. quadrants=(xv, yv)
    draws two dashed guide lines. Labels that would collide are nudged, then dropped."""
    xs, ys = [p[1] for p in points], [p[2] for p in points]
    xlo, xhi = _nice_range(min(xs), max(xs))
    ylo, yhi = _nice_range(min(ys), max(ys))
    L, R, T, B = 48, width - 16, 36, height - 36
    X = lambda v: L + (R - L) * (v - xlo) / (xhi - xlo)
    Y = lambda v: B - (B - T) * (v - ylo) / (yhi - ylo)
    out = []
    yt, xt = _ticks(ylo, yhi), _ticks(xlo, xhi)
    for v in yt:
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{LINE}"/>')
        out.append(_t(L - 8, Y(v) + 4, fa(v, _tick_dec(yt)), "right", 11, 400, FAINT))
    for v in xt:
        out.append(_t(X(v), B + 16, fa(v, _tick_dec(xt)), "center", 11, 400, FAINT))
    if quadrants:
        qx, qy = quadrants
        out.append(f'<line x1="{X(qx):.1f}" x2="{X(qx):.1f}" y1="{T}" y2="{B}" stroke="{FAINT}" stroke-dasharray="4 4"/>')
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(qy):.1f}" y2="{Y(qy):.1f}" stroke="{FAINT}" stroke-dasharray="4 4"/>')
    if y_label:
        out.append(_t(4, 14, y_label + " ↑", "left", 11.5, 600, MUTED))
    if x_label:
        out.append(_t(R, height - 2, x_label + " →", "right", 11.5, 600, MUTED))
    label_all = len(points) <= 8 if label_all is None else label_all
    order = sorted(points, key=lambda p: _hot(p[0], highlight))  # highlighted drawn last, on top
    boxes = []
    for lab, x, y in order:
        hot = _hot(lab, highlight)
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{6 if hot else 4.5}" fill="{ACCENT if hot else MUTED}" opacity="{1 if hot else .75}"/>')
    for lab, x, y in sorted(points, key=lambda p: not _hot(p[0], highlight)):  # label highlighted points first
        hot = _hot(lab, highlight)
        if not (hot or label_all):
            continue
        tw = _tw(lab, 12)
        for dx, dy, align in ((10, 4, "left"), (-10, 4, "right"), (0, -12, "center"), (0, 18, "center")):
            ax = X(x) + dx
            x0 = ax if align == "left" else ax - tw if align == "right" else ax - tw / 2
            y0 = Y(y) + dy - 12
            box = (x0, y0, x0 + tw, y0 + 15)
            if box[0] < 0 or box[2] > width or any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
                continue
            boxes.append(box)
            out.append(f'<rect x="{box[0] - 2:.1f}" y="{box[1] - 1:.1f}" width="{tw + 4:.1f}" height="17" rx="{_rx(3)}" fill="#fff" opacity=".9"/>')
            out.append(_t(ax, Y(y) + dy, lab, align, 12, 700 if hot else 500, ACCENT if hot else TEXT))
            break
    return _svg(width, height, "".join(out), f"{y_label} / {x_label}")


@_themed
def heatmap(rows, cols, values, highlight=None, width: int = 680, row_h: int = 30, label_w: int = 130,
            decimals: int = 0, low: str = "کم", high: str = "زیاد", bins=None, legend: bool = True,
            totals: bool = False) -> str:
    """A category x period grid on a single-hue ramp from soft grey to ink (never a rainbow). rows: labels;
    cols: period labels, left to right; values: rows x cols. highlight = (row_label, col_label) or a list of them
    gets a red outline. Values are printed when cells are wide enough. A five-step key sits under the grid.
    bins=[10, 20, 30, 40] colours by value range instead (len(bins)+1 steps, never a continuous ramp) and, with
    legend=True, puts a legend row above the grid giving each step's range. totals=True adds bold row totals (left
    of the grid) and column totals (a last row); use it only when the values are additive."""
    flat = [v for r in values for v in r if v is not None]
    lo, hi = min(flat), max(flat)
    span = (hi - lo) or 1
    n = len(cols)
    binned = bool(bins)
    nb = len(bins) if binned else 0
    gx_r = width - label_w - 6
    gx0 = 46 if totals else 0
    cw = (gx_r - gx0) / n
    lg_h = 26 if (binned and legend) else 0
    top = 22 + lg_h
    tot_h = row_h if totals else 0
    h = top + len(rows) * row_h + tot_h + (8 if binned else 36)

    def mix(t):
        a = (0xf6, 0xf6, 0xf9); b = (0x0f, 0x10, 0x20)
        return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def bin_of(v):
        return sum(1 for b in bins if v >= b)
    hl = set()
    if highlight:
        hl = {tuple(highlight)} if isinstance(highlight, tuple) and len(highlight) == 2 and isinstance(highlight[0], str) else {tuple(x) for x in highlight}
    out = []
    if binned and legend:
        x = gx_r
        for k in range(nb + 1):
            lab = (f"کمتر از {fa(bins[0], decimals)}" if k == 0 else f"{fa(bins[-1], decimals)} و بیشتر" if k == nb
                   else f"{fa(bins[k - 1], decimals)} تا {fa(bins[k], decimals)}")
            out.append(f'<rect x="{x - 14:.1f}" y="3" width="14" height="11" rx="{_rx(2)}" fill="{mix(0.08 + 0.92 * k / nb)}"/>')
            out.append(_t(x - 20, 13, lab, "right", 11, 500, TEXT))
            x -= 20 + _tw(lab, 11) + 14
    step = max(1, round(n / 8))
    for j, c in enumerate(cols):
        if j % step == 0:
            out.append(_t(gx0 + j * cw + cw / 2, lg_h + 14, c, "center", 11 if binned else 10.5, 400, FAINT))
    if totals:
        out.append(_t(gx0 - 6, lg_h + 14, "جمع", "right", 11, 700, FAINT))
    show = cw >= 30
    for i, r in enumerate(rows):
        y = top + i * row_h
        out.append(_t(width - 2, y + row_h / 2 + 4, r, "right", 12.5, 500, TEXT))
        for j, c in enumerate(cols):
            v = values[i][j]
            if v is None:
                continue
            t = (v - lo) / span
            if binned:
                t = bin_of(v) / nb
                fill = mix(0.08 + 0.92 * t)
            else:
                fill = mix(0.08 + 0.92 * t)
            x = gx0 + j * cw
            out.append(f'<rect x="{x + 1:.1f}" y="{y + 1}" width="{cw - 2:.1f}" height="{row_h - 2}" rx="{_rx(3)}" fill="{fill}"/>')
            if (r, c) in hl:
                out.append(f'<rect x="{x + 1:.1f}" y="{y + 1}" width="{cw - 2:.1f}" height="{row_h - 2}" rx="{_rx(3)}" fill="none" stroke="{ACCENT}" stroke-width="2.5"/>')
            if show:
                out.append(_t(x + cw / 2, y + row_h / 2 + 4, fa(v, decimals), "center", 11, 600, "#fff" if t > 0.5 else INK))
        if totals:
            out.append(_t(gx0 - 6, y + row_h / 2 + 4, fa(sum(v for v in values[i] if v is not None), decimals), "right", 12, 700, INK))
    if totals:
        y = top + len(rows) * row_h
        out.append(f'<line x1="0" x2="{width}" y1="{y + 1}" y2="{y + 1}" stroke="{HAIR}" stroke-width=".75"/>')
        out.append(_t(width - 2, y + row_h / 2 + 4, "جمع", "right", 12.5, 700, INK))
        for j in range(n):
            out.append(_t(gx0 + j * cw + cw / 2, y + row_h / 2 + 4, fa(sum(values[i][j] for i in range(len(rows)) if values[i][j] is not None), decimals), "center", 11.5, 700, INK))
    if not binned:
        ky = h - 14
        out.append(_t(gx_r, ky + 4, high, "right", 11, 500, FAINT))
        for k in range(5):
            out.append(f'<rect x="{gx_r - 30 - (k + 1) * 22:.1f}" y="{ky - 6}" width="20" height="10" rx="{_rx(2)}" fill="{mix(0.08 + 0.92 * (4 - k) / 4)}"/>')
        out.append(_t(gx_r - 30 - 5 * 22 - 6, ky + 4, low, "right", 11, 500, FAINT))
    return _svg(width, h, "".join(out), f"{low} تا {high}")


def _spread(ys, gap):
    """Push label positions apart (top to bottom) so neighbours are at least `gap` px apart. ys: [(y, key)]."""
    ys = sorted(ys)
    out = []
    for y, key in ys:
        if out and y - out[-1][0] < gap:
            y = out[-1][0] + gap
        out.append((y, key))
    return out


def _stack_colors(parts, highlight) -> dict:
    """Ink -> light grey steps for stacked parts; the highlighted part is red and the rest stay in the lighter greys."""
    pool = RAMP if highlight is None else RAMP[1:]
    rest = [p for p in parts if not _hot(p, highlight)]
    cols = {}
    for k, p in enumerate(rest):
        idx = round(k * (len(pool) - 1) / max(len(rest) - 1, 1)) if len(rest) > 1 else 0
        cols[p] = pool[idx]
    for p in parts:
        if _hot(p, highlight):
            cols[p] = ACCENT
    return cols


@_themed
def stacked_columns(groups, parts, highlight=None, unit: str = "", width: int = 680, height: int = 300,
                    decimals: int = 0, totals: bool = True, labels: str = "end", change=None) -> str:
    """Absolute stacked columns: parts of a total per period. groups: [(label, {part: value})], first group on the
    right (RTL, like `columns`). Parts stack from the bottom in `parts` order on an ink -> light grey ramp, 1px white
    seams; the highlighted part is red. Segment values sit inside when the segment is tall enough; totals (bold)
    sit on top. labels="end" names the parts in a column beside the last bar with leaders (no legend box);
    "none" omits them. change={"label": "+۲۳٪ در سال", "from": 0, "to": -1} draws a bracket over two columns.
    Values must be non-negative (use a bridge, or columns, for negatives)."""
    if any(d.get(p, 0) < 0 for _, d in groups for p in parts):
        raise ValueError("stacked_columns: negative values; use columns with a zero line or a bridge")
    cols = _stack_colors(parts, highlight)
    tots = [sum(d.get(p, 0) for p in parts) for _, d in groups]
    hi = _nice_max(max(tots))
    nl = min(200, max(_tw(p, 12) for p in parts) + 34) if labels == "end" else 0
    L, R, T, B = 20 + nl, width - 20, (48 if change else 30), height - 34
    gw = (R - L) / len(groups)
    bw = min(120, gw * 0.58)
    Y = lambda v: B - (B - T) * v / hi
    out = [f'<line x1="{L - 8}" x2="{R}" y1="{B}" y2="{B}" stroke="{HAIR}"/>']
    cxs = []
    for gi, (lab, d) in enumerate(groups):
        cx = R - gw * (gi + 0.5)
        cxs.append(cx)
        cum = 0
        for p in parts:
            v = d.get(p, 0)
            if v <= 0:
                continue
            y1, y0 = Y(cum + v), Y(cum)
            out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{y0 - y1:.1f}" fill="{cols[p]}" stroke="#fff" stroke-width="1"/>')
            if y0 - y1 >= 16 and bw >= 26:
                out.append(_t(cx, (y0 + y1) / 2 + 4, fa(v, decimals), "center", 11.5, 500, "#fff" if _dark(cols[p]) else INK))
            cum += v
        if totals:
            out.append(_t(cx, Y(cum) - 7, fa(cum, decimals), "center", 13, 800, INK))
        out.append(_t(cx, B + 18, lab, "center", 12.5, 500, TEXT))
    if labels == "end":
        last = groups[-1][1]
        edge = cxs[-1] - bw / 2
        cum, pos = 0, []
        for p in parts:
            v = last.get(p, 0)
            if v > 0:
                pos.append(((Y(cum) + Y(cum + v)) / 2, p))
                cum += v
        mids = dict((p, y) for y, p in pos)
        for ly, p in _spread(pos, 15):
            hot = _hot(p, highlight)
            out.append(f'<line x1="{edge:.1f}" x2="{edge - 12:.1f}" y1="{mids[p]:.1f}" y2="{ly:.1f}" stroke="{FAINT}" stroke-width=".75"/>')
            out.append(_t(edge - 16, ly + 4, p, "right", 12, 700 if hot else 500, ACCENT if hot else TEXT))
    if change:
        a, b = cxs[change.get("from", 0)], cxs[change.get("to", -1)]
        ya, yb = Y(tots[change.get("from", 0)]) - 26, Y(tots[change.get("to", -1)]) - 26
        yy = min(ya, yb, T - 6)
        out.append(f'<polyline points="{a:.1f},{ya + 6:.1f} {a:.1f},{yy:.1f} {b:.1f},{yy:.1f} {b:.1f},{yb + 6:.1f}" fill="none" stroke="{MUTED}" stroke-width="1"/>')
        out.append(_t((a + b) / 2, yy - 6, change.get("label", ""), "center", 12.5, 800, INK, HALO))
    if unit:
        out.append(_t(width - 20, 13, unit, "right", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit or "، ".join(parts))


def _wrap2(s: str) -> list[str]:
    """Split a label into at most two lines at the space nearest its middle."""
    w = str(s).split()
    if len(w) < 2:
        return [str(s)]
    best = min(range(1, len(w)), key=lambda i: abs(len(" ".join(w[:i])) - len(" ".join(w[i:]))))
    return [" ".join(w[:best]), " ".join(w[best:])]


@_themed
def marimekko(columns, parts=None, highlight=None, ref_line=None, unit: str = "", width: int = 680,
              height: int = 320, decimals: int = 0) -> str:
    """Column width = size, column height = share or rate. columns: [(label, width_value, data)], first on the right.
    data is {part: share} (parts mode: each column is split 100% into `parts`, values printed inside when the cell is
    big enough, a legend row above) or one number, a rate (rate mode: bars from a zero line, negatives below it).
    `highlight` names a part (parts mode) or a column label (rate mode). ref_line=(value, label) is a dashed
    horizontal reference (rate mode). Seams are white; values print inside when the column is at least 28px wide,
    otherwise above (rate mode) so every rate stays readable."""
    rate = not isinstance(columns[0][2], dict)
    total = sum(c[1] for c in columns) or 1
    L, R = (104 if (rate and ref_line) else 20), width - 20
    px = [(R - L) * c[1] / total for c in columns]
    lines = [_wrap2(c[0]) for c in columns]
    narrow = [max(_tw(s, 11.5) for s in ln) > pw for ln, pw in zip(lines, px)]
    T = 34
    B = height - (62 if any(narrow) else 40)
    out = []
    if rate:
        rates = [c[2] for c in columns]
        hi = _nice_max(max(rates))
        lo = -_nice_max(-min(rates)) if min(rates) < 0 else 0
        Y = lambda v: B - (B - T) * (v - lo) / (hi - lo)
        Y0 = Y(0)
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y0:.1f}" y2="{Y0:.1f}" stroke="{INK if lo < 0 else HAIR}" stroke-width="1"/>')
    else:
        cols = _stack_colors(parts, highlight)
        x = R
        for p in parts:  # legend row above the plot: a stack of 3+ parts has nowhere else to name them
            out.append(f'<rect x="{x - 12:.1f}" y="3" width="12" height="11" fill="{cols[p]}"/>')
            out.append(_t(x - 18, 13, p, "right", 11.5, 700 if _hot(p, highlight) else 500, ACCENT if _hot(p, highlight) else TEXT))
            x -= 18 + _tw(p, 11.5) + 14
        out.append(f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="{HAIR}"/>')
    x = R
    for i, (lab, wv, d) in enumerate(columns):
        pw = px[i]
        x1, cxm = x - pw, x - pw / 2
        if rate:
            hot = _hot(lab, highlight)
            col = ACCENT if hot else (MUTED if highlight is not None else INK)
            top, hgt = min(Y(d), Y0), abs(Y(d) - Y0)
            out.append(f'<rect x="{x1:.1f}" y="{top:.1f}" width="{pw:.1f}" height="{hgt:.1f}" fill="{col}" stroke="#fff" stroke-width="1.2"/>')
            txt = _sgn(d, decimals) if d < 0 else fa(d, decimals)
            if pw >= 28 and hgt >= 20:
                out.append(_t(cxm, (top + 16) if d >= 0 else (top + hgt - 7), txt, "center", 11.5, 700, "#fff"))
            else:
                lift = 0 if i % 2 == 0 or pw >= 28 else 14
                out.append(_t(cxm, (top - 6 - lift) if d >= 0 else (top + hgt + 15 + lift), txt, "center", 11.5, 700 if hot else 500, ACCENT if hot else TEXT))
        else:
            tot = sum(max(d.get(p, 0), 0) for p in parts) or 1
            cum = 0
            for p in parts:
                v = max(d.get(p, 0), 0)
                if v <= 0:
                    continue
                y1, y0 = B - (B - T) * (cum + v) / tot, B - (B - T) * cum / tot
                out.append(f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{pw:.1f}" height="{y0 - y1:.1f}" fill="{cols[p]}" stroke="#fff" stroke-width="1.2"/>')
                if pw >= 28 and y0 - y1 >= 16:
                    out.append(_t(cxm, (y0 + y1) / 2 + 4, fa(d.get(p, 0), decimals), "center", 11.5, 500, "#fff" if _dark(cols[p]) else INK))
                cum += v
        ly = B + 18 + (30 if (narrow[i] and i % 2) else 0)
        if narrow[i] and i % 2:
            out.append(f'<line x1="{cxm:.1f}" x2="{cxm:.1f}" y1="{B + 3}" y2="{ly - 12}" stroke="{HAIR}" stroke-width=".75"/>')
        for k, s in enumerate(lines[i]):
            out.append(_t(cxm, ly + 15 * k, s, "center", 11.5, 500, TEXT))
        x = x1
    if rate and ref_line:
        val, rlab = ref_line
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(val):.1f}" y2="{Y(val):.1f}" stroke="{FAINT}" stroke-dasharray="4 3"/>')
        out.append(_t(L - 6, Y(val) + 4, f"{rlab} {fa(val, decimals)}", "right", 11.5, 700, TEXT, 'font-style="italic"'))
    if unit:
        out.append(_t(L, 13, unit, "left", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit or "marimekko")


@_themed
def stacked_area(x_labels, series: dict, highlight=None, unit: str = "", width: int = 680, height: int = 300,
                 decimals: int = 0, ticks: int = 4) -> str:
    """Parts of a total over time as stacked bands, time left to right. series: {band: [values]}; bands stack from the
    bottom in the given order on an ink -> light grey ramp with white seams; the highlighted band is red. At most
    6 bands (more cannot be told apart: use small_multiples). Each band is named at the right end with a leader, so
    there is no legend. Values must be non-negative."""
    names = list(series)
    if len(names) > 6:
        raise ValueError(f"stacked_area: {len(names)} bands; at most 6 (use small_multiples or group the rest)")
    if any(v < 0 for vs in series.values() for v in vs):
        raise ValueError("stacked_area: negative values")
    n = len(x_labels)
    cols = _stack_colors(names, highlight)
    totals = [sum(series[s][i] for s in names) for i in range(n)]
    hi = _nice_max(max(totals))
    nr = min(190, max(_tw(s, 12.5) for s in names) + 26)
    L, R, T, B = 44, width - nr, 20, height - 34
    X = lambda i: L + (R - L) * (i / (n - 1 if n > 1 else 1))
    Y = lambda v: B - (B - T) * v / hi
    out = []
    for k in range(ticks + 1):
        v = hi * k / ticks
        out.append(f'<line x1="{L}" x2="{R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{HAIR}" stroke-width=".5"/>')
        out.append(_t(L - 8, Y(v) + 4, fa(v, decimals), "right", 11.5, 400, FAINT))
    cum = [0.0] * n
    mids = []
    for s in names:
        lowl = list(cum)
        cum = [cum[i] + series[s][i] for i in range(n)]
        top = " ".join(f"{X(i):.1f},{Y(cum[i]):.1f}" for i in range(n))
        bot = " ".join(f"{X(i):.1f},{Y(lowl[i]):.1f}" for i in reversed(range(n)))
        out.append(f'<polygon points="{top} {bot}" fill="{cols[s]}" stroke="#fff" stroke-width="1" stroke-linejoin="round"/>')
        mids.append(((Y(lowl[-1]) + Y(cum[-1])) / 2, s))
    step = max(1, round(n / 7))
    for i in range(0, n, step):
        out.append(_t(X(i), height - 12, x_labels[i], "center", 11.5, 400, FAINT))
    base = dict((s, y) for y, s in mids)
    for ly, s in _spread(mids, 16):
        hot = _hot(s, highlight)
        out.append(f'<line x1="{R + 2:.1f}" x2="{R + 9:.1f}" y1="{base[s]:.1f}" y2="{ly:.1f}" stroke="{FAINT}" stroke-width=".75"/>')
        out.append(_t(R + 12, ly + 4, s, "left", 12.5, 700 if hot else 500, ACCENT if hot else TEXT))
    if unit:
        out.append(_t(0, 12, unit, "left", 11.5, 400, FAINT))
    return _svg(width, height, "".join(out), unit or "، ".join(names))


@_themed
def table_plain(rows, columns, highlight=None, total_row: bool = False, label: str = "label", width: int = 680,
                row_h: int = 32, label_w: int = 170, missing: str = "—") -> str:
    """The plain exhibit table, for numbers that must be read exactly: no bars. rows: [{label: .., <key>: ..}];
    columns: [(key, header, decimals)] drawn left of the label column in the given order, numerals right-aligned.
    A bold header over a 1.5px ink rule, hairline row rules, no verticals. total_row=True sets the last row bold
    under an ink rule. A missing value (None or text) prints as given, «—» for None, never blank."""
    col_w = (width - label_w) / max(len(columns), 1)
    top = 32
    h = top + len(rows) * row_h + 6
    out = []
    for j, (k, hd, _) in enumerate(columns):
        out.append(_t(width - label_w - j * col_w - 8, 18, hd, "right", 12, 700, TEXT))
    out.append(f'<line x1="0" x2="{width}" y1="{top - 6}" y2="{top - 6}" stroke="{INK}" stroke-width="1.5"/>')
    for i, r in enumerate(rows):
        y = top + i * row_h
        is_tot = total_row and i == len(rows) - 1
        hot = _hot(r[label], highlight)
        wt = 700 if (hot or is_tot) else 400
        colr = ACCENT if hot else INK if is_tot else TEXT
        if is_tot:
            out.append(f'<line x1="0" x2="{width}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1"/>')
        out.append(_t(width - 2, y + row_h / 2 + 4, r[label], "right", 13.5, 700 if (hot or is_tot) else 500, colr))
        for j, (k, _, d) in enumerate(columns):
            v = r.get(k)
            txt = missing if v is None else (_sgn(v, d) if isinstance(v, (int, float)) and v < 0 else fa(v, d) if isinstance(v, (int, float)) else v)
            out.append(_t(width - label_w - j * col_w - 8, y + row_h / 2 + 4, txt, "right", 13, wt, colr))
        if not is_tot and not (total_row and i == len(rows) - 2):
            out.append(f'<line x1="0" x2="{width}" y1="{y + row_h:.1f}" y2="{y + row_h:.1f}" stroke="{LINE}"/>')
    return _svg(width, h, "".join(out), "، ".join(hd for _, hd, _ in columns))


def demo_charts() -> dict:
    """Every chart type with fictional data, keyed by name (used by `demo` and the tests)."""
    import math
    days = [f"{fa(d)} مهر" for d in range(1, 29)]
    base = [100 + 6 * math.sin(i / 2) for i in range(28)]
    camp = [b * (1.45 if 9 <= i <= 15 else 1.05) for i, b in enumerate(base)]
    months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور"]
    return {
        "bar_h": bar_h([("گوشی موبایل", 412), ("لپ‌تاپ", 268), ("هدفون", 121), ("ساعت هوشمند", 96)], highlight="گوشی موبایل"),
        "line": line({"کالاهای جشنواره": [round(v) for v in camp], "بقیهٔ کالاها": [round(v * .8) for v in base]}, days,
                     highlight="کالاهای جشنواره", band=(9, 15, "جشنواره")),
        "columns": columns([("دیجیتال", {"قبل": 12, "حین": 19}), ("خانه", {"قبل": 9, "حین": 11}), ("مد", {"قبل": 7, "حین": 8})],
                           ["قبل", "حین"], highlight="حین"),
        "bridge": bridge([("هفتهٔ جشنواره", 840, "total"), ("افت پس از آن", -120, "delta"), ("خالص", 720, "total")]),
        "dot_range": dot_range([("گوشی موبایل", 9.2, 7.8), ("لپ‌تاپ", 8.1, 7.9), ("لوازم خانگی", 10.4, 8.6),
                                ("مد و پوشاک", 14.0, 13.8), ("کتاب", 11.0, 11.2)], highlight={"گوشی موبایل", "لوازم خانگی"},
                               labels=("پیش از بازنگری", "پس از بازنگری"), decimals=1),
        "stacked_share": stacked_share([("تهران", {"نقدی": 38, "اعتباری": 44, "اقساطی": 18}),
                                        ("اصفهان", {"نقدی": 51, "اعتباری": 36, "اقساطی": 13}),
                                        ("مشهد", {"نقدی": 57, "اعتباری": 31, "اقساطی": 12}),
                                        ("تبریز", {"نقدی": 60, "اعتباری": 33, "اقساطی": 7})],
                                       ["نقدی", "اعتباری", "اقساطی"], highlight="اعتباری"),
        "small_multiples": small_multiples({"گوشی موبایل": [40, 42, 45, 43, 50, 58], "لپ‌تاپ": [30, 31, 29, 33, 35, 34],
                                            "لوازم خانگی": [22, 25, 27, 26, 30, 31], "مد و پوشاک": [18, 17, 19, 21, 20, 22],
                                            "کتاب": [8, 9, 9, 10, 9, 11], "سوپرمارکت": [25, 27, 30, 33, 36, 41]},
                                           months, highlight="سوپرمارکت"),
        "sparkline": "".join(f'<span style="margin-left:18px">{sparkline(v)}</span>' for v in
                             ([3, 4, 4, 5, 7, 6, 8, 9], [9, 8, 8, 7, 7, 6, 5, 5], [5, 5, 6, 5, 5, 6, 5, 6])),
        "table_bars": table_bars([{"label": "گوشی موبایل", "gmv": 412, "orders": 38.2, "aov": 10.8},
                                  {"label": "لپ‌تاپ", "gmv": 268, "orders": 9.6, "aov": 27.9},
                                  {"label": "لوازم خانگی", "gmv": 148, "orders": 12.1, "aov": 12.2},
                                  {"label": "مد و پوشاک", "gmv": 64, "orders": 21.4, "aov": 3.0}],
                                 [("gmv", "فروش (میلیارد تومان)", 0), ("orders", "سفارش (هزار)", 1), ("aov", "میانگین سبد (میلیون)", 1)],
                                 bar="gmv", highlight="لپ‌تاپ"),
        "scatter": scatter([("گوشی موبایل", 3.3, 312), ("لپ‌تاپ", 3.1, 186), ("لوازم خانگی", 2.7, 148), ("مد و پوشاک", 1.3, 64),
                            ("کتاب", 2.1, 22), ("سوپرمارکت", 2.4, 96), ("آرایشی", 1.9, 58)],
                           x_label="بازده تخفیف (برابر)", y_label="فروش افزوده (میلیارد تومان)", highlight="مد و پوشاک",
                           quadrants=(2.5, 150), decimals=0),
        "heatmap": heatmap(["گوشی موبایل", "لپ‌تاپ", "لوازم خانگی", "مد و پوشاک"], months,
                           [[40, 42, 45, 43, 50, 58], [30, 31, 29, 33, 35, 34], [22, 25, 27, 26, 30, 31], [18, 17, 19, 21, 20, 22]],
                           highlight=("گوشی موبایل", "شهریور")),
    }


def demo_charts_new() -> dict:
    """The chart forms added with the exhibit grammar, with fictional data (rendered by `demo` after the originals)."""
    import math
    q = ["۱۴۰۱", "۱۴۰۲", "۱۴۰۳", "۱۴۰۴", "۱۴۰۵"]
    days = [f"{fa(d)} مهر" for d in range(1, 21)]
    base = [100 + 6 * math.sin(i / 2) for i in range(20)]
    cats = {"دیجیتال": [40, 44, 48, 55, 61], "خانه و آشپزخانه": [30, 31, 33, 35, 36], "مد و پوشاک": [18, 20, 21, 21, 22],
            "سوپرمارکت": [12, 16, 21, 27, 35], "کتاب": [6, 6, 7, 7, 7]}
    return {
        "columns_single": columns([("دیجیتال", {"رشد": 23}), ("خانه", {"رشد": 9}), ("مد", {"رشد": 4}), ("سوپرمارکت", {"رشد": 31})],
                                  ["رشد"], highlight="سوپرمارکت", value_pos="auto"),
        "columns_negative": columns([("دیجیتال", {"تغییر": 12}), ("خانه", {"تغییر": -7}), ("مد", {"تغییر": 4}), ("کتاب", {"تغییر": -15})],
                                    ["تغییر"], highlight="کتاب", value_pos="outside"),
        "bar_h_negative": bar_h([("سوپرمارکت", 31), ("دیجیتال", 23), ("مد و پوشاک", -6), ("کتاب", -15)], highlight="کتاب",
                                ref_lines=[(10, "میانگین")]),
        "stacked_columns": stacked_columns([(y, {k: v[i] for k, v in cats.items()}) for i, y in enumerate(q)], list(cats),
                                           highlight="سوپرمارکت", change={"label": "۵۲٪ رشد در ۴ سال", "from": 0, "to": -1}),
        "bridge_v2": bridge([("فروش ۱۴۰۴", 410, "total"), ("ترافیک", 62, "delta"), ("نرخ تبدیل", 48, "delta"),
                             ("بدون تخفیف", 71, "delta"), ("مرجوعی", -16, "delta"), ("جمع کل", 575, "subtotal")],
                            connectors="solid", highlight="بدون تخفیف",
                            callout={"step": "بدون تخفیف", "big": "۳۶٪", "text": "از رشد، ۱۴۰۴ تا ۱۴۰۵"}),
        "line_context": line({"سوپرمارکت": [round(b * (1 + i / 40)) for i, b in enumerate(base)], "دیجیتال": [round(b * .9) for b in base],
                              "خانه": [round(b * .7) for b in base], "کتاب": [round(b * .4) for b in base],
                              "مد": [round(b * .55) for b in base]}, days, highlight="سوپرمارکت", context=["دیجیتال", "خانه", "کتاب", "مد"],
                             event={"x": "۱۰ مهر", "text": "شروع جشنواره"}, dashed_from=16, ref_lines=[(80, "هدف")]),
        "marimekko_rate": marimekko([("دیجیتال", 38, 12), ("خانه و آشپزخانه", 24, -4), ("مد و پوشاک", 16, 6), ("سوپرمارکت", 14, 19),
                                     ("کتاب", 5, 3), ("اسباب‌بازی", 3, 8)], highlight="سوپرمارکت", ref_line=(7, "میانگین")),
        "marimekko_parts": marimekko([("تهران", 45, {"نقدی": 38, "اعتباری": 44, "اقساطی": 18}),
                                      ("اصفهان", 25, {"نقدی": 51, "اعتباری": 36, "اقساطی": 13}),
                                      ("مشهد", 20, {"نقدی": 57, "اعتباری": 31, "اقساطی": 12}),
                                      ("تبریز", 10, {"نقدی": 60, "اعتباری": 33, "اقساطی": 7})],
                                     parts=["نقدی", "اعتباری", "اقساطی"], highlight="اعتباری"),
        "stacked_area": stacked_area(q, cats, highlight="سوپرمارکت"),
        "heatmap_bins": heatmap(["گوشی موبایل", "لپ‌تاپ", "لوازم خانگی", "مد و پوشاک"], ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور"],
                                [[40, 42, 45, 43, 50, 58], [30, 31, 29, 33, 35, 34], [22, 25, 27, 26, 30, 31], [18, 17, 19, 21, 20, 22]],
                                bins=[20, 30, 40, 50], highlight=("گوشی موبایل", "شهریور"), totals=True),
        "table_plain": table_plain([{"label": "دیجیتال", "a": 61, "b": 23.4, "c": 12}, {"label": "خانه و آشپزخانه", "a": 36, "b": 9.1, "c": -4},
                                    {"label": "سوپرمارکت", "a": 35, "b": 31.0, "c": None}, {"label": "جمع", "a": 132, "b": 18.2, "c": 8}],
                                   [("a", "فروش", 0), ("b", "رشد ٪", 1), ("c", "تغییر سهم", 0)], highlight="سوپرمارکت", total_row=True),
    }


def demo(path: str) -> None:
    allc = {**demo_charts(), **demo_charts_new()}
    body = "".join(f"<figure class='chart' data-shot='{k}'><figcaption style='margin:0 0 10px;font-weight:700'>{k}</figcaption>{c}</figure>"
                   for k, c in allc.items())
    open(path, "w", encoding="utf-8").write(f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><link rel='stylesheet' href='style.css'><link rel='stylesheet' href='report.css'></head><body><main><div class='col' style='padding:40px 0'>{body}</div></main></body></html>")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "demo":
        demo(sys.argv[2])
    else:
        print(__doc__)
