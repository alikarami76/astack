#!/usr/bin/env python3
"""flynn page linter: the mechanical checks on an HTML page in a flynn theme, one rule per lesson the user taught.

  lint_page.py <run>/page/index.html [--json] [--width 1040] [--only rule,rule]

Every rule reports pass or fail with located findings: the data-shot block, a CSS path and the offending text.
Exit code 1 if any rule fails. flynn-checker runs it on every HTML render; evals/run_lint_eval.py measures it.

Rules (id: what fails; the lesson behind it):
  render.font            the page's first font family did not load
  render.images          an image did not load
  render.overflow        an element is wider than the page (horizontal scroll)
  render.small-shots     a screenshot in .shot shows below 0.8 of its 2x capture size (product text under ~11px)
  color.off-token        a computed text, background, border, fill or stroke colour is not a theme token
                         (plus white, transparent and the few allowed tints)
  type.off-scale         a heading's size is not one the theme's stylesheets declare, or text sets its own inline size
  type.contrast          text below 4.5:1 against its background (3:1 at 18px and up)
  fa.marker              a numbered .mk marker not glued to the word before it (not inside .nw), or with
                         space/punctuation left hanging after it
  fa.decimal             the Arabic decimal separator U+066B (many Persian fonts draw it like an apostrophe)
  fa.ss01                the font's ss01 set on body text (in fonts that have one, it turns every «تومان» into the toman sign)
  fa.flipped-sign        +N, −N, N× or ×N in right-to-left text outside an LTR isolate (the sign renders at the wrong end)
  fa.latin-heading       a heading whose first letter is Latin
  fa.term                «مدیرمحصول» without a space (write «مدیر محصول»)
  fa.tilde               «~» before a number (write «حدود»)
  struct.repeated-block  the same run of components (and h3 wording) repeated in three or more blocks
  struct.notes           more than one .note in one block
  struct.eyebrow         a .sec-label whose words all repeat in the h2 under it
  struct.lead            a lead (.lead, .answer p) longer than two sentences
  struct.example-label   an .example without the fictional-numbers label (فرضی / ساختگی)
"""
from __future__ import annotations

import os

import argparse
import json
import re
import sys
from pathlib import Path

# ---------- colour and text helpers (pure, tested) ----------

ALLOWED_EXTRA = {
    "#ffffff", "#000000",
    # concept-mock warning box and legacy lifecycle tints (see tests/test_html_adapter.py)
    "#fff8e6", "#7a5600", "#c9cad6", "#ffd7df", "#e7f6ee",
    # charts.py grey series
    "#a3a5b3", "#c7c8d1",
}


def parse_color(c: str):
    """'rgb(1, 2, 3)' / 'rgba(1,2,3,.5)' / '#abc' / '#aabbcc' -> (r, g, b, a) or None."""
    c = (c or "").strip().lower()
    if not c or c in ("none", "currentcolor", "inherit", "transparent"):
        return (0, 0, 0, 0.0) if c == "transparent" else None
    m = re.match(r"rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", c)
    if m:
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return (round(float(m.group(1))), round(float(m.group(2))), round(float(m.group(3))), alpha)
    m = re.match(r"#([0-9a-f]{3}|[0-9a-f]{6})$", c)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    return None


def hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def luminance(rgb) -> float:
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(fg, bg) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg):
    """Composite a colour with alpha over an opaque background."""
    a = fg[3] if len(fg) > 3 else 1.0
    return tuple(round(fg[i] * a + bg[i] * (1 - a)) for i in range(3)) + (1.0,)


SENT_END = re.compile(r"(?<![0-9۰-۹])[.!?؟](?=\s|$)")


def sentences(text: str) -> int:
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return 0
    n = len(SENT_END.findall(t))
    return n + (0 if SENT_END.search(t[-1:] or " ") else 1) if not re.search(r"[.!?؟]\s*$", t) else n


DIGIT = "0-9۰-۹"
FLIPPED = re.compile(rf"(?:(?:^|[\s(«:،؛])([+\-−])(?=[{DIGIT}]))|(?:[{DIGIT}]\s?×)|(?:×\s?[{DIGIT}])")
LATIN = re.compile(r"[A-Za-z]")
LETTER = re.compile(r"[A-Za-z؀-ۿ]")


def first_letter_is_latin(text: str) -> bool:
    m = LETTER.search(text or "")
    return bool(m and LATIN.match(m.group(0)))


# ---------- page facts (collected in one pass in the browser) ----------

COLLECT = r"""(minRatio) => {
  const out = {render: {}, colors: [], texts: [], headings: [], inlineSizes: [], markers: [], ss01: [], blocks: [],
               leads: [], examples: []};
  const skip = el => el.closest('#flynn-review, script, style, noscript, template');
  const shotOf = el => { const s = el.closest('[data-shot]'); return s ? s.dataset.shot : ''; };
  const pathOf = el => { const p = []; while (el && el.nodeType === 1 && el !== document.body) {
      let s = el.tagName.toLowerCase(); if (el.id) { p.unshift(s + '#' + el.id); break; }
      if (el.classList.length) s += '.' + [...el.classList].join('.');
      let i = 1, e = el; while ((e = e.previousElementSibling)) if (e.tagName === el.tagName) i++;
      p.unshift(s + ':nth-of-type(' + i + ')'); el = el.parentElement; } return p.join(' > '); };
  const visible = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0; };

  // render checks
  const fam = getComputedStyle(document.body).fontFamily.split(',')[0].replace(/["']/g, '').trim();
  out.render.font = document.fonts.check(`20px "${fam}"`) && [...document.fonts].some(f => f.family.replace(/["']/g, '') === fam && f.status === 'loaded');
  out.render.fontFamily = fam;
  out.render.badImages = [...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => ({path: pathOf(i), shot: shotOf(i), text: i.getAttribute('src')}));
  const vw = document.documentElement.clientWidth;
  out.render.overflow = [];
  for (const el of document.querySelectorAll('body *')) { if (skip(el)) continue; const b = el.getBoundingClientRect();
    if (b.width && (b.right > vw + 1 || b.left < -1) && !el.closest('.slide')) out.render.overflow.push({path: pathOf(el), shot: shotOf(el), text: Math.round(b.width) + 'px wide'}); }
  out.render.overflow = out.render.overflow.slice(0, 20);
  out.render.smallShots = [];
  for (const img of document.querySelectorAll('.shot img')) { if (!img.naturalWidth) continue;
    const ratio = img.getBoundingClientRect().width / (img.naturalWidth / 2);
    if (ratio < minRatio) out.render.smallShots.push({path: pathOf(img), shot: shotOf(img), text: img.getAttribute('src') + ' at ' + ratio.toFixed(2)}); }

  // elements: colours, direct text, headings, inline sizes, ss01
  for (const el of document.querySelectorAll('body, body *')) {
    if (skip(el) || !visible(el)) continue;
    const cs = getComputedStyle(el);
    const inSvg = el instanceof SVGElement;
    const loc = {path: pathOf(el), shot: shotOf(el)};
    if (inSvg) {
      for (const k of ['fill', 'stroke']) { const v = el.getAttribute(k); if (v && v !== 'none') out.colors.push({...loc, prop: k, value: v}); }
      continue;
    }
    const direct = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue).join('');
    const hasText = direct.trim().length > 0;
    out.colors.push({...loc, prop: 'background-color', value: cs.backgroundColor});
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none')
      out.colors.push({...loc, prop: 'border-' + side.toLowerCase() + '-color', value: cs['border' + side + 'Color']});
    if (hasText) {
      out.colors.push({...loc, prop: 'color', value: cs.color});
      // effective background: first ancestor-or-self with a non-transparent background
      let bg = null, e = el;
      while (e && e.nodeType === 1) { const b = getComputedStyle(e).backgroundColor; if (b && !/rgba\(0, 0, 0, 0\)|transparent/.test(b)) { bg = b; break; } e = e.parentElement; }
      out.texts.push({...loc, text: direct.trim().slice(0, 140), full: direct, color: cs.color, bg: bg || 'rgb(255, 255, 255)',
                      size: parseFloat(cs.fontSize), weight: +cs.fontWeight, ss01: /ss01/.test(cs.fontFeatureSettings) && !el.closest('.toman-sign'),
                      dir: cs.direction, isolated: !!el.closest('[dir=ltr], bdi, .en') || cs.direction === 'ltr'});
    }
    if (/^H[1-4]$/.test(el.tagName)) out.headings.push({...loc, tag: el.tagName.toLowerCase(), text: el.innerText.trim().slice(0, 140), size: cs.fontSize});
    if (el.style && el.style.fontSize && hasText) out.inlineSizes.push({...loc, text: direct.trim().slice(0, 80), size: el.style.fontSize});
  }

  // markers: glued (inside .nw), and what follows / precedes them
  for (const mk of document.querySelectorAll('.mk')) {
    if (skip(mk)) continue;
    const after = mk.nextSibling && mk.nextSibling.nodeType === 3 ? mk.nextSibling.nodeValue : (mk.nextSibling ? '' : '');
    const before = mk.previousSibling && mk.previousSibling.nodeType === 3 ? mk.previousSibling.nodeValue : null;
    out.markers.push({path: pathOf(mk), shot: shotOf(mk), text: mk.parentElement.innerText.trim().slice(0, 80), glued: !!mk.closest('.nw'),
                      after: after.slice(0, 3), before: before === null ? null : before.slice(-2)});
  }

  // blocks: per data-shot block, the sequence of component children, notes, eyebrow vs h2
  const blocks = [...document.querySelectorAll('[data-shot]')].filter(b => !b.parentElement.closest('[data-shot]') || b.matches('section'));
  for (const b of blocks) {
    const seq = [];
    for (const c of b.children) {
      if (c.tagName === 'H3') seq.push('h3:' + c.innerText.trim());
      else if (c.classList.length && !c.matches('.sec-label, .intro, .col, .concept, .aside')) seq.push(c.tagName.toLowerCase() + '.' + c.classList[0]);
    }
    const label = b.querySelector(':scope > .sec-label, :scope > .col > .sec-label');
    const h2 = b.querySelector('h2');
    out.blocks.push({shot: b.dataset.shot, path: pathOf(b), seq, notes: b.querySelectorAll('.note').length,
                     label: label ? label.innerText.trim() : null, h2: h2 ? h2.innerText.trim() : null});
  }
  for (const l of document.querySelectorAll('.lead, .answer p')) if (!skip(l)) out.leads.push({path: pathOf(l), shot: shotOf(l), text: l.innerText.trim()});
  for (const x of document.querySelectorAll('.example')) if (!skip(x)) { const k = x.querySelector('.k'); out.examples.push({path: pathOf(x), shot: shotOf(x), text: k ? k.innerText.trim() : ''}); }
  return out;
}"""


def _sheets(page: Path) -> list[str]:
    """The CSS text of the page's linked stylesheets and inline <style> blocks."""
    html = page.read_text(encoding="utf-8")
    out = [m for m in re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)]
    for href in re.findall(r'<link[^>]+href="([^"]+\.css)"', html):
        p = page.parent / href
        if p.exists():
            out.append(p.read_text(encoding="utf-8"))
    return out


def declared_sizes(page: Path) -> set[float]:
    """Font sizes (px) declared anywhere in the page's stylesheets: the theme's type scale. (Chrome does not
    expose cssRules for file:// sheets, so they are read here rather than in the browser.)"""
    return {round(float(v), 1) for css in _sheets(page) for v in re.findall(r"font-size:\s*([\d.]+)px", css)}


def theme_tokens(page: Path) -> set[str]:
    """Hex colours declared in :root of the page's stylesheets, plus the allowed extras."""
    toks = set(ALLOWED_EXTRA)
    html = page.read_text(encoding="utf-8")
    for href in re.findall(r'<link[^>]+href="([^"]+\.css)"', html):
        css_p = (page.parent / href)
        if css_p.exists():
            css = css_p.read_text(encoding="utf-8")
            for m in re.finditer(r"^:root\s*\{(.*?)\}", css, re.S | re.M):
                toks |= {hex_of(parse_color(h)) for h in re.findall(r"#[0-9a-fA-F]{3,6}\b", m.group(1))}
    return toks


# ---------- rules (pure: facts -> findings) ----------

def _f(item, detail=""):
    return {"shot": item.get("shot", ""), "path": item.get("path", ""), "text": item.get("text", ""), "detail": detail}


def rule_render(facts):
    r = facts["render"]
    return {
        "render.font": [] if r["font"] else [{"shot": "", "path": "body", "text": r["fontFamily"], "detail": "font did not load"}],
        "render.images": [_f(i, "image did not load") for i in r["badImages"]],
        "render.overflow": [_f(i, "wider than the page") for i in r["overflow"]],
        "render.small-shots": [_f(i, "below 0.8 of capture size") for i in r["smallShots"]],
    }


def rule_colors(facts, tokens):
    out, seen = [], set()
    for c in facts["colors"]:
        rgba = parse_color(c["value"])
        if rgba is None or rgba[3] == 0:
            continue
        h = hex_of(rgba)
        if h in tokens:
            continue
        key = (c["path"], c["prop"], h)
        if key in seen:
            continue
        seen.add(key)
        out.append({"shot": c["shot"], "path": c["path"], "text": h, "detail": f"{c['prop']} {h} is not a theme token"})
    return out


def rule_sizes(facts, declared):
    out = []
    for h in facts["headings"]:
        px = round(float(h["size"][:-2]), 1)
        if px not in declared:
            out.append({"shot": h["shot"], "path": h["path"], "text": h["text"][:80], "detail": f"{h['tag']} at {px}px is not on the theme's scale"})
    for i in facts["inlineSizes"]:
        out.append(_f(i, f"inline font-size {i['size']}; use a theme class"))
    return out


def rule_contrast(facts):
    out = []
    for t in facts["texts"]:
        fg, bg = parse_color(t["color"]), parse_color(t["bg"])
        if not fg or not bg:
            continue
        bg = blend(bg, (255, 255, 255))
        fg = blend(fg, bg)
        need = 3.0 if t["size"] >= 18 else 4.5
        ratio = contrast(fg, bg)
        if ratio + 1e-6 < need:
            out.append(_f(t, f"{ratio:.2f}:1 ({hex_of(fg)} on {hex_of(bg)}, {t['size']:g}px) needs {need}:1"))
    return out


PUNCT = ".:؛،!؟,;"


def rule_markers(facts):
    out = []
    for m in facts["markers"]:
        if not m["glued"]:
            out.append(_f(m, "marker not inside .nw, so a line break can separate it from its word"))
        elif m["after"] and (m["after"][0].isspace() and len(m["after"]) > 1 and m["after"][1] in PUNCT):
            out.append(_f(m, "punctuation left hanging after the marker"))
        if m.get("before") is not None and m["before"] and m["before"][-1].isspace():
            out.append(_f(m, "space before the marker; glue it to the word"))
    return out


def rule_text(facts):
    dec, ss, flip, term, tilde = [], [], [], [], []
    for t in facts["texts"]:
        s = t["full"]
        if "٫" in s:
            dec.append(_f(t, "U+066B decimal separator; use «.»"))
        if t["ss01"] and "تومان" in s:  # ss01's only visible effect: «تومان» becomes the toman sign
            ss.append(_f(t, "ss01 turns «تومان» into the toman sign in a sentence; use .toman-sign for prices only"))
        if not t["isolated"]:
            for m in FLIPPED.finditer(s):
                span = s[max(0, m.start() - 1):m.end() + 1]
                if "⁦" in s[:m.start() + 1] and "⁩" in s[m.end() - 1:]:
                    continue
                flip.append(_f(t, f"«{span.strip()}» flips in RTL; write it in words or wrap it in an LTR isolate"))
                break
        if "مدیرمحصول" in s:
            term.append(_f(t, "«مدیرمحصول»: write «مدیر محصول»"))
        if re.search(rf"~\s?[{DIGIT}]", s):
            tilde.append(_f(t, "«~» before a number; write «حدود»"))
    return dec, ss, flip, term, tilde


def rule_latin_heading(facts):
    return [_f(h, "heading starts with Latin") for h in facts["headings"] if first_letter_is_latin(h["text"])]


def rule_repeated(facts, n=2, min_blocks=3):
    grams: dict[tuple, set] = {}
    for b in facts["blocks"]:
        seq = b["seq"]
        for i in range(len(seq) - n + 1):
            g = tuple(seq[i:i + n])
            grams.setdefault(g, set()).add(b["shot"])
    out = []
    for g, shots in grams.items():
        if len(shots) >= min_blocks:
            out.append({"shot": ", ".join(sorted(shots)), "path": "", "text": " + ".join(g),
                        "detail": f"the same blocks repeat in {len(shots)} sections; merge them (for example one table)"})
    return out


def rule_notes(facts):
    return [{"shot": b["shot"], "path": b["path"], "text": "", "detail": f"{b['notes']} notes in one block; keep at most one"}
            for b in facts["blocks"] if b["notes"] > 1]


def _words(s):
    return {w for w in re.split(r"[\s‌،؛:؟?!.()«»]+", s or "") if w}


def rule_eyebrow(facts):
    out = []
    for b in facts["blocks"]:
        if b["label"] and b["h2"]:
            lw = _words(b["label"])
            if lw and lw <= _words(b["h2"]):
                out.append({"shot": b["shot"], "path": b["path"], "text": b["label"], "detail": f"eyebrow repeats the heading «{b['h2']}»"})
    return out


def rule_lead(facts):
    return [_f(l, f"{sentences(l['text'])} sentences; keep the lead to two") for l in facts["leads"] if sentences(l["text"]) > 2]


def rule_example(facts):
    return [_f(x, "example box without «اعداد فرضی‌اند» or «ساختگی» label") for x in facts["examples"]
            if not re.search(r"فرضی|ساختگی", x["text"])]


RULE_ORDER = ["render.font", "render.images", "render.overflow", "render.small-shots", "color.off-token", "type.off-scale",
              "type.contrast", "fa.marker", "fa.decimal", "fa.ss01", "fa.flipped-sign", "fa.latin-heading", "fa.term",
              "fa.tilde", "struct.repeated-block", "struct.notes", "struct.eyebrow", "struct.lead", "struct.example-label"]


def evaluate(facts: dict, tokens: set[str], sizes: set[float]) -> dict:
    res = rule_render(facts)
    res["color.off-token"] = rule_colors(facts, tokens)
    res["type.off-scale"] = rule_sizes(facts, sizes)
    res["type.contrast"] = rule_contrast(facts)
    res["fa.marker"] = rule_markers(facts)
    dec, ss, flip, term, tilde = rule_text(facts)
    res.update({"fa.decimal": dec, "fa.ss01": ss, "fa.flipped-sign": flip, "fa.term": term, "fa.tilde": tilde})
    res["fa.latin-heading"] = rule_latin_heading(facts)
    res["struct.repeated-block"] = rule_repeated(facts)
    res["struct.notes"] = rule_notes(facts)
    res["struct.eyebrow"] = rule_eyebrow(facts)
    res["struct.lead"] = rule_lead(facts)
    res["struct.example-label"] = rule_example(facts)
    return {k: {"result": "fail" if res[k] else "pass", "findings": res[k]} for k in RULE_ORDER}


# ---------- running ----------

def collect(page: Path, browser=None, width: int = 1040, min_ratio: float = 0.8) -> dict:
    from playwright.sync_api import sync_playwright
    own = browser is None
    pw = None
    if own:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
    try:
        pg = browser.new_page(viewport={"width": width, "height": 1200}, device_scale_factor=2)
        pg.goto(page.resolve().as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(150)
        facts = pg.evaluate(COLLECT, min_ratio)
        pg.close()
        return facts
    finally:
        if own:
            browser.close()
            pw.stop()


def lint(page: Path, browser=None, width: int = 1040, only=None) -> dict:
    page = Path(page)
    res = evaluate(collect(page, browser, width), theme_tokens(page), declared_sizes(page))
    if only:
        res = {k: v for k, v in res.items() if k in only}
    return res


def failed(res: dict) -> list[str]:
    return [k for k, v in res.items() if v["result"] == "fail"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--width", type=int, default=1040)
    ap.add_argument("--only", help="comma-separated rule ids")
    a = ap.parse_args(argv)
    res = lint(Path(a.page), width=a.width, only=set(a.only.split(",")) if a.only else None)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for k, v in res.items():
            print(f"{'PASS' if v['result'] == 'pass' else 'FAIL'}  {k}" + (f"  ({len(v['findings'])})" if v["findings"] else ""))
            for f in v["findings"][:8]:
                print(f"      [{f['shot']}] {f['detail']}" + (f" — «{f['text'][:70]}»" if f["text"] else "") + (f"\n        at {f['path']}" if f["path"] else ""))
            if len(v["findings"]) > 8:
                print(f"      … {len(v['findings']) - 8} more")
    return 1 if failed(res) else 0


if __name__ == "__main__":
    sys.exit(main())
