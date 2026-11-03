#!/usr/bin/env python3
"""flynn pptx adapter: turn a editorial deck (templates/deck.html, 1280x720 `.slide` sections) into an
editable 16:9 PowerPoint file.

  pptx_export.py <run>/page/index.html --out <run>/deck.pptx [--font Vazirmatn]

How it maps the page (measured in a real browser with Playwright, so positions match the PDF render):
  text      every block that holds text (headings, paragraphs, list items, labels, the footer) becomes a native
            text box at the same position and size, right-to-left (a:pPr rtl="1", right-aligned unless the
            page centres it), in the Vazirmatn font, keeping size, weight, colour, line height and <b> runs.
            List bullets and CSS counters (slide number, numbered steps) become text too.
  shapes    boxes with a background or border (cards, pills, rules, the chart frame) become rectangles with
            the same fill and border colour; rounded when the corner radius is at least 8px. A box with a border
            on one side only becomes a thin filled bar on that side.
  pictures  <img>, inline <svg> charts and <canvas> become PNG pictures cut from a 2x screenshot of the element.
  slide     the slide's background colour becomes the slide background.
Layer order: shapes, then pictures, then text, each in document order.

Limits: PowerPoint and Keynote need the Vazirmatn font installed to show it; pptx cannot embed it here, and
without it they substitute another font and lines may wrap differently. Charts are pictures, not editable
PowerPoint charts. Gradients, shadows and masks are dropped."""
from __future__ import annotations

import os

import argparse
import io
import json
import re
import sys
from pathlib import Path

PX = 9525  # EMU per CSS px (96 dpi)
SLIDE_W, SLIDE_H = 1280, 720

MEASURE_JS = r"""
() => {
  const slides = [...document.querySelectorAll('.slide')];
  const px = v => parseFloat(v) || 0;
  const rgba = c => { const m = (c || '').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s)); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const isBlock = el => { const d = getComputedStyle(el).display; return d !== 'inline' && d !== 'contents' && d !== 'none'; };
  const hidden = el => { const cs = getComputedStyle(el); return cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0; };
  const PERSIAN = '۰۱۲۳۴۵۶۷۸۹';
  const fa = n => String(n).replace(/\d/g, d => PERSIAN[d]);

  function pseudo(el, which, slideIndex) {
    const cs = getComputedStyle(el, which); const c = cs.content;
    if (!c || c === 'none' || c === 'normal') return null;
    let text = null;
    if (/counter\(/.test(c)) {
      const name = (c.match(/counter\(\s*([\w-]+)/) || [])[1];
      let n;
      if (name === 'slide') n = slideIndex + 1;
      else { n = 1; let s = el; while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) n++; }
      text = /persian/.test(c) ? fa(n) : String(n);
    } else {
      const m = c.match(/^"(.*)"$/); if (m) text = m[1];
    }
    if (!text || !text.trim()) return null;
    return {text, cs};
  }

  return slides.map((slide, si) => {
    const S = slide.getBoundingClientRect();
    const rel = r => ({x: r.left - S.left, y: r.top - S.top, w: r.width, h: r.height});
    const inside = r => r.width > 0.5 && r.height > 0.5 && r.right > S.left && r.left < S.right && r.bottom > S.top && r.top < S.bottom;
    const out = {bg: rgba(getComputedStyle(slide).backgroundColor), shapes: [], pictures: [], texts: []};
    const all = [slide, ...slide.querySelectorAll('*')];
    for (const el of all) {
      if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
      if (hidden(el)) continue;
      const r = el.getBoundingClientRect();
      if (!inside(r)) continue;
      const cs = getComputedStyle(el);
      const tag = el.tagName.toLowerCase();

      // pictures
      if (tag === 'img' || tag === 'svg' || tag === 'canvas') {
        if (tag === 'img' && !(el.complete && el.naturalWidth)) continue;
        out.pictures.push({...rel(r), sel: si + ':' + [...slide.querySelectorAll(tag)].indexOf(el) + ':' + tag});
        continue;
      }

      // shapes (not the slide itself; its colour becomes the slide background)
      if (el !== slide) {
        const bg = rgba(cs.backgroundColor);
        const bw = ['Top', 'Right', 'Bottom', 'Left'].map(s => cs['border' + s + 'Style'] === 'none' ? 0 : px(cs['border' + s + 'Width']));
        const bc = ['Top', 'Right', 'Bottom', 'Left'].map(s => rgba(cs['border' + s + 'Color']));
        const hasBg = bg && bg.a > 0.02, hasBorder = bw.some(w => w > 0);
        if (hasBg || hasBorder) {
          const uniform = bw.every(w => w === bw[0]) && bw[0] > 0;
          out.shapes.push({...rel(r), fill: hasBg ? bg : null, radius: px(cs.borderTopLeftRadius),
            border: uniform ? {w: bw[0], c: bc[0], dashed: cs.borderTopStyle === 'dashed'} : null,
            sides: uniform ? [] : bw.map((w, i) => w > 0 ? {side: i, w, c: bc[i]} : null).filter(Boolean)});
        }
      }

      // text blocks: an element that is not inline and holds text outside its own block children
      if (!isBlock(el)) continue;
      const runs = [], textNodes = [];
      const walk = (node, bold) => {
        for (const ch of node.childNodes) {
          if (ch.nodeType === 3) {
            const t = ch.textContent; if (!t) continue;
            if (t.trim()) textNodes.push(ch);
            const pcs = getComputedStyle(ch.parentElement);
            runs.push({t, bold: bold || +pcs.fontWeight >= 600, color: rgba(pcs.color), size: px(pcs.fontSize)});
          } else if (ch.nodeType === 1) {
            const tg = ch.tagName.toLowerCase();
            if (tg === 'br') { runs.push({t: '\n', br: true}); continue; }
            if (tg === 'svg' || tg === 'img' || hidden(ch) || isBlock(ch)) continue;
            walk(ch, bold || tg === 'b' || tg === 'strong');
          }
        }
      };
      walk(el, false);
      // collapse whitespace like the browser does
      let joined = '';
      for (const r0 of runs) { if (!r0.br) r0.t = r0.t.replace(/\s+/g, ' '); }
      const clean = runs.filter(r0 => r0.br || r0.t.length);
      if (clean.length && !clean[0].br) clean[0].t = clean[0].t.replace(/^\s+/, '');
      if (clean.length && !clean[clean.length - 1].br) clean[clean.length - 1].t = clean[clean.length - 1].t.replace(/\s+$/, '');
      for (const r0 of clean) joined += r0.t;
      const before = pseudo(el, '::before', si), after = pseudo(el, '::after', si);
      if (tag === 'li' && cs.display === 'list-item' && cs.listStyleType !== 'none' && !before) clean.unshift({t: '•  ', bold: false, color: rgba(cs.color), size: px(cs.fontSize)});
      if (joined.trim()) {
        const box = rel(r);
        const pad = {t: px(cs.paddingTop) + px(cs.borderTopWidth), r: px(cs.paddingRight) + px(cs.borderRightWidth),
                     b: px(cs.paddingBottom) + px(cs.borderBottomWidth), l: px(cs.paddingLeft) + px(cs.borderLeftWidth)};
        const ta = cs.textAlign;
        const align = ta === 'center' ? 'center' : (ta === 'end' || ta === 'left') ? 'left' : 'right';
        const lh = cs.lineHeight === 'normal' ? px(cs.fontSize) * 1.5 : px(cs.lineHeight);
        // vertical extent from this block's own text (a block child, like <b> in a step, has its own box);
        // horizontal extent from the element's content box, which is the line length the text wraps at
        let top = Infinity, bottom = -Infinity;
        for (const tn of textNodes) { const rg = document.createRange(); rg.selectNodeContents(tn);
          for (const cr of rg.getClientRects()) { if (cr.height > 0) { top = Math.min(top, cr.top); bottom = Math.max(bottom, cr.bottom); } } }
        const ty = isFinite(top) ? top - S.top : box.y + pad.t, th = isFinite(top) ? bottom - top : box.h - pad.t - pad.b;
        out.texts.push({x: box.x + pad.l, y: ty, w: box.w - pad.l - pad.r, h: th,
          align, lineHeight: lh, size: px(cs.fontSize), weight: +cs.fontWeight, color: rgba(cs.color),
          runs: clean, tag, cls: el.className || ''});
      }
      // CSS counters and quoted content (slide number, step numbers) as their own text boxes
      for (const [ps, which] of [[before, 'before'], [after, 'after']]) {
        if (!ps) continue;
        const pc = ps.cs; const size = px(pc.fontSize);
        const w = Math.max(px(pc.width) || 0, size * (ps.text.length + 1));
        const h = Math.max(px(pc.height) || 0, (pc.lineHeight === 'normal' ? size * 1.4 : px(pc.lineHeight)) || size * 1.4);
        let x, y;
        const box = rel(r);
        if (pc.position === 'absolute') {
          x = pc.right !== 'auto' ? box.x + box.w - px(pc.right) - w : box.x + px(pc.left);
          y = pc.top !== 'auto' ? box.y + px(pc.top) : box.y + box.h - px(pc.bottom) - h;
        } else {
          // an in-flow ::after in an RTL row sits at the left end, ::before at the right end
          x = which === 'after' ? box.x : box.x + box.w - w; y = box.y + (box.h - h) / 2;
        }
        const pbg = rgba(pc.backgroundColor);
        if (pbg && pbg.a > 0.02) out.shapes.push({x, y, w, h, fill: pbg, radius: px(pc.borderTopLeftRadius), border: null, sides: []});
        out.texts.push({x, y, w, h, align: pc.textAlign === 'center' || (pbg && pbg.a > 0.02) ? 'center' : (which === 'after' ? 'left' : 'right'),
          lineHeight: h, size, weight: +pc.fontWeight, color: rgba(pc.color),
          runs: [{t: ps.text, bold: +pc.fontWeight >= 600, color: rgba(pc.color), size}], tag: which, cls: 'pseudo'});
      }
    }
    return out;
  });
}
"""


def _rgb(c):
    from pptx.dml.color import RGBColor
    return RGBColor(int(round(c["r"])), int(round(c["g"])), int(round(c["b"])))


def measure(page: Path, pictures_2x: bool = True):
    """Open the deck and return per-slide measurements plus PNG bytes for each picture."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        pg = b.new_page(viewport={"width": 1400, "height": 800}, device_scale_factor=2 if pictures_2x else 1)
        pg.goto(page.resolve().as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(200)
        slides = pg.evaluate(MEASURE_JS)
        for si, s in enumerate(slides):
            loc = pg.locator(".slide").nth(si)
            loc.scroll_into_view_if_needed()
            sb = loc.bounding_box()
            for pic in s["pictures"]:
                clip = {"x": sb["x"] + pic["x"], "y": sb["y"] + pic["y"], "width": pic["w"], "height": pic["h"]}
                # clip to the slide, since slides hide overflow
                x1, y1 = max(clip["x"], sb["x"]), max(clip["y"], sb["y"])
                x2, y2 = min(clip["x"] + clip["width"], sb["x"] + sb["width"]), min(clip["y"] + clip["height"], sb["y"] + sb["height"])
                pic.update(x=x1 - sb["x"], y=y1 - sb["y"], w=x2 - x1, h=y2 - y1)
                # bounding_box() is in viewport coordinates, so the clip must be too (no full_page)
                pic["png"] = pg.screenshot(clip={"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})
        b.close()
    return slides


def _set_rtl(paragraph, align: str) -> None:
    from pptx.oxml.ns import qn
    pPr = paragraph._p.get_or_add_pPr()
    pPr.set("rtl", "1")
    pPr.set("algn", {"right": "r", "left": "l", "center": "ctr"}[align])


def _font_run(run, font: str, size_px: float, bold: bool, color) -> None:
    from pptx.oxml.ns import qn
    from pptx.util import Pt
    run.font.name = font
    run.font.size = Pt(size_px * 0.75)
    run.font.bold = bool(bold)
    if color:
        run.font.color.rgb = _rgb(color)
    rPr = run._r.get_or_add_rPr()
    rPr.set("lang", "fa-IR")
    for tag in ("a:cs", "a:ea"):  # complex-script (Persian) and east-asian slots also use the font
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", font)


def build(slides: list[dict], out: Path, font: str = "Vazirmatn") -> Path:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(SLIDE_W * PX), Emu(SLIDE_H * PX)
    blank = prs.slide_layouts[6]
    E = lambda v: Emu(int(round(v * PX)))

    for s in slides:
        sl = prs.slides.add_slide(blank)
        if s.get("bg"):
            sl.background.fill.solid()
            sl.background.fill.fore_color.rgb = _rgb(s["bg"])
        for sh in s["shapes"]:
            if sh["sides"]:
                for sd in sh["sides"]:  # one-sided border -> thin bar on that side (0=top,1=right,2=bottom,3=left)
                    x, y, w, h = sh["x"], sh["y"], sh["w"], sh["h"]
                    bw = sd["w"]
                    geo = {0: (x, y, w, bw), 1: (x + w - bw, y, bw, h), 2: (x, y + h - bw, w, bw), 3: (x, y, bw, h)}[sd["side"]]
                    bar = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, *map(E, geo))
                    bar.fill.solid(); bar.fill.fore_color.rgb = _rgb(sd["c"]); bar.line.fill.background(); bar.shadow.inherit = False
                    _set_rtl(bar.text_frame.paragraphs[0], "right")
            if not sh["fill"] and not sh["border"]:
                continue
            rounded = sh["radius"] >= 8
            shp = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, E(sh["x"]), E(sh["y"]), E(sh["w"]), E(sh["h"]))
            shp.shadow.inherit = False
            if rounded:
                shp.adjustments[0] = min(0.5, sh["radius"] / max(1, min(sh["w"], sh["h"])))
            if sh["fill"]:
                shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(sh["fill"])
            else:
                shp.fill.background()
            if sh["border"]:
                shp.line.color.rgb = _rgb(sh["border"]["c"]); shp.line.width = Pt(sh["border"]["w"] * 0.75)
                if sh["border"].get("dashed"):
                    from pptx.enum.dml import MSO_LINE_DASH_STYLE
                    shp.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            else:
                shp.line.fill.background()
            shp.text_frame.text = ""
            _set_rtl(shp.text_frame.paragraphs[0], "right")  # anything typed into a card later starts right-to-left
        for pic in s["pictures"]:
            if pic["w"] < 1 or pic["h"] < 1:
                continue
            sl.shapes.add_picture(io.BytesIO(pic["png"]), E(pic["x"]), E(pic["y"]), E(pic["w"]), E(pic["h"]))
        for t in s["texts"]:
            # a little extra width on the left: PowerPoint's shaping differs slightly from the browser's, and a
            # too-tight box would wrap a line the page kept on one
            slack = 0.04 * t["w"] + 4
            x = t["x"] - (slack if t["align"] == "right" else slack / 2 if t["align"] == "center" else 0)
            tb = sl.shapes.add_textbox(E(x), E(t["y"]), E(t["w"] + slack), E(max(t["h"], t["lineHeight"])))
            tf = tb.text_frame
            tf.word_wrap = True
            tf.auto_size = None
            tf.vertical_anchor = MSO_ANCHOR.TOP
            tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
            # split the runs into paragraphs at <br>, then write each paragraph wrapped in RLM marks: rtl="1" is
            # enough for PowerPoint, but Keynote, Quick Look and Google Slides resolve edge punctuation and
            # brackets against an LTR base unless the paragraph starts and ends with a strong RTL character
            paras, cur = [], []
            for r in t["runs"]:
                if r.get("br"):
                    paras.append(cur); cur = []
                else:
                    cur.append(r)
            paras.append(cur)
            for pi, runs in enumerate(paras):
                para = tf.paragraphs[0] if pi == 0 else tf.add_paragraph()
                _set_rtl(para, t["align"])
                para.line_spacing = Pt(t["lineHeight"] * 0.75)
                for ri, r in enumerate(runs):
                    run = para.add_run()
                    run.text = ("\u200f" if ri == 0 else "") + r["t"] + ("\u200f" if ri == len(runs) - 1 else "")
                    _font_run(run, font, r.get("size") or t["size"], r.get("bold"), r.get("color") or t["color"])
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def preview(slides: list[dict], out_dir: Path, font: str = "Vazirmatn") -> list[Path]:
    """macOS only: render each slide through Quick Look (qlmanage), which draws the first slide of a pptx, by
    writing one single-slide pptx per slide. It shows what a non-PowerPoint viewer makes of the file; fonts that
    are not installed are substituted, exactly as they would be for the reader."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("qlmanage"):
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(slides, 1):
            one = Path(tmp) / f"{i:02d}.pptx"
            build([s], one, font)
            subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", tmp, str(one)], capture_output=True, timeout=60)
            png = Path(tmp) / f"{i:02d}.pptx.png"
            if png.exists():
                dst = out_dir / f"{i:02d}.png"
                shutil.move(str(png), dst)
                made.append(dst)
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--out", help="default: <page folder>/../deck.pptx")
    ap.add_argument("--font", default="Vazirmatn")
    ap.add_argument("--preview", action="store_true", help="macOS: also render each slide via Quick Look into <out>-preview/NN.png")
    a = ap.parse_args(argv)
    page = Path(a.page)
    out = Path(a.out) if a.out else page.resolve().parent.parent / "deck.pptx"
    slides = measure(page)
    if not slides:
        print("no .slide sections found; pptx export needs a deck built from templates/deck.html", file=sys.stderr)
        return 1
    build(slides, out, a.font)
    summary = [{"slide": i + 1, "texts": len(s["texts"]), "shapes": len(s["shapes"]), "pictures": len(s["pictures"])} for i, s in enumerate(slides)]
    result = {"out": str(out), "slides": summary}
    if a.preview:
        pv = preview(slides, out.with_name(out.stem + "-preview"), a.font)
        result["preview"] = [str(p) for p in pv] or "qlmanage not available"
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
