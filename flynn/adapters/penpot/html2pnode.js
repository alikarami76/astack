// flynn page capture: walks a rendered page and returns a flat list of nodes with exact page boxes.
// Node types: box (background, border, radius, shadow), text (one per text run), svg (inline icons,
// sprite <use> resolved), image (<img> and CSS background images). Boxes nest: each node's "parent"
// is the index of the nearest enclosing box (-1 = page). Runs as page.evaluate(SOURCE)().
() => {
  const nodes = [];
  const warnings = new Set();
  const SX = window.scrollX, SY = window.scrollY;
  const W = Math.ceil(document.documentElement.scrollWidth);
  const H = Math.ceil(Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));

  const color = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || '');
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    const a = p.length > 3 ? p[3] : 1;
    if (!(a > 0)) return null;
    return { hex: '#' + p.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join(''), a: +a.toFixed(3) };
  };
  const px = (v) => parseFloat(v) || 0;
  const box = (r) => ({ x: +(r.left + SX).toFixed(2), y: +(r.top + SY).toFixed(2), w: +r.width.toFixed(2), h: +r.height.toFixed(2) });
  const nameOf = (el) => el.getAttribute('data-uikit') || el.getAttribute('data-kit') || el.id ||
    ((el.classList && el.classList[0]) || '').replace(/__.*$/, '').replace(/[_-][a-f0-9]{5,}$/, '') || el.tagName.toLowerCase();
  const family = (f) => (f || '').split(',')[0].replace(/["']/g, '').trim();

  function shadowOf(cs) {
    if (!cs.boxShadow || cs.boxShadow === 'none') return null;
    const m = /(rgba?\([^)]+\))\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px(?:\s+(-?[\d.]+)px)?(\s+inset)?/.exec(cs.boxShadow);
    if (!m) return null;
    if (m[6]) { warnings.add('inset shadow skipped'); return null; }
    const c = color(m[1]);
    return c ? { x: +m[2], y: +m[3], blur: +m[4], spread: +(m[5] || 0), color: c } : null;
  }

  function bgImage(cs) {
    const m = /url\(["']?([^"')]+)["']?\)/.exec(cs.backgroundImage || '');
    return m ? new URL(m[1], document.baseURI).href : null;
  }

  // Copy computed paint onto every SVG part, because stylesheet rules don't travel with the markup.
  const PAINT = ['fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'fill-rule', 'stroke-linecap', 'stroke-linejoin'];
  function inlinePaint(orig, clone) {
    const o = [orig, ...orig.querySelectorAll('*')], c = [clone, ...clone.querySelectorAll('*')];
    o.forEach((el, i) => {
      const k = c[i]; if (!k) return;
      const st = getComputedStyle(el);
      for (const prop of PAINT) {
        let v = st.getPropertyValue(prop);
        if (!v) continue;
        if (/^rgba?\(/.test(v)) { const col = color(v); v = col ? col.hex : 'none'; if (col && col.a < 1 && (prop === 'fill' || prop === 'stroke')) k.setAttribute(prop + '-opacity', col.a); }
        if (v === 'currentcolor' || v === 'currentColor') { const col = color(st.color); if (col) v = col.hex; }
        k.setAttribute(prop, v);
      }
      if (st.transform && st.transform !== 'none' && el !== orig) k.setAttribute('transform', st.transform);
    });
  }

  function svgMarkup(svg, cs, r) {
    const clone = svg.cloneNode(true);
    inlinePaint(svg, clone);
    clone.querySelectorAll('use').forEach((u) => {
      const href = u.getAttribute('href') || u.getAttribute('xlink:href') || '';
      const target = href.startsWith('#') ? document.getElementById(href.slice(1)) : null;
      if (!target) { u.remove(); return; }
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      for (const a of ['fill', 'stroke', 'stroke-width', 'opacity', 'transform']) if (u.getAttribute(a)) g.setAttribute(a, u.getAttribute(a));
      for (const k of target.childNodes) g.appendChild(k.cloneNode(true));
      if (target.getAttribute('viewBox') && !clone.getAttribute('viewBox')) clone.setAttribute('viewBox', target.getAttribute('viewBox'));
      u.replaceWith(g);
    });
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', r.width);
    clone.setAttribute('height', r.height);
    clone.removeAttribute('class');
    clone.removeAttribute('style');
    const fill = color(cs.color);
    let s = clone.outerHTML;
    if (fill) s = s.replace(/currentColor/g, fill.hex);
    if (!clone.getAttribute('fill') && fill && !/fill=/.test(s)) s = s.replace('<svg', `<svg fill="${fill.hex}"`);
    return s;
  }

  function textAlign(cs) {
    const a = cs.textAlign;
    if (a === 'start') return cs.direction === 'rtl' ? 'right' : 'left';
    if (a === 'end') return cs.direction === 'rtl' ? 'left' : 'right';
    return ['left', 'right', 'center', 'justify'].includes(a) ? a : (cs.direction === 'rtl' ? 'right' : 'left');
  }

  function addText(tn, el, cs, parent) {
    let text = tn.textContent;
    if (!/(pre|pre-wrap|pre-line|break-spaces)/.test(cs.whiteSpace)) text = text.replace(/\s+/g, ' ');
    if (!text.trim()) return;
    const range = document.createRange();
    range.selectNodeContents(tn);
    const r = range.getBoundingClientRect();
    if (r.width < 0.5 || r.height < 0.5) return;
    const size = px(cs.fontSize);
    const lh = cs.lineHeight === 'normal' ? size * 1.5 : px(cs.lineHeight);
    const c = color(cs.color) || { hex: '#000000', a: 1 };
    const lines = Math.max(1, Math.round(r.height / (lh || size * 1.5)));
    nodes.push({ type: 'text', parent, name: text.trim().slice(0, 40), box: box(r), text: text.trim(),
      font: { family: family(cs.fontFamily), size, weight: String(cs.fontWeight), lineHeight: lh, letterSpacing: px(cs.letterSpacing) },
      color: c, align: textAlign(cs), dir: cs.direction, lines,
      decoration: cs.textDecorationLine && cs.textDecorationLine !== 'none' ? cs.textDecorationLine : null });
  }

  // Form fields draw their value (or placeholder, or the chosen option) without a text node: add it as text,
  // inside the field's content box (2026-10-06: every input on a Admin Panel board came out empty).
  const NOT_TEXT = ['checkbox', 'radio', 'hidden', 'range', 'color', 'file', 'image'];
  function addField(el, cs, parent) {
    const tag = el.tagName.toLowerCase();
    let text = '', ph = false;
    if (tag === 'select') text = (el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '') || '';
    else if (['submit', 'button', 'reset'].includes(el.type)) text = el.value || '';
    else { text = el.value || ''; if (!text && el.placeholder) { text = el.placeholder; ph = true; } }
    if (el.type === 'password' && !ph) text = '•'.repeat(text.length);
    text = text.replace(/\s+/g, ' ').trim();
    if (!text) return;
    const r = el.getBoundingClientRect();
    const pl = px(cs.paddingLeft) + px(cs.borderLeftWidth), pr = px(cs.paddingRight) + px(cs.borderRightWidth);
    const size = px(cs.fontSize);
    const lh = cs.lineHeight === 'normal' ? size * 1.5 : px(cs.lineHeight);
    const multi = tag === 'textarea';
    const h = multi ? r.height - px(cs.paddingTop) - px(cs.paddingBottom) - px(cs.borderTopWidth) - px(cs.borderBottomWidth) : lh;
    const y = multi ? r.top + px(cs.paddingTop) + px(cs.borderTopWidth) : r.top + (r.height - lh) / 2;
    const pcs = ph ? getComputedStyle(el, '::placeholder') : cs;
    const c = color(pcs.color) || color(cs.color) || { hex: '#000000', a: 1 };
    nodes.push({ type: 'text', parent, name: text.slice(0, 40), text,
      box: { x: +(r.left + SX + pl).toFixed(2), y: +(y + SY).toFixed(2), w: +Math.max(1, r.width - pl - pr).toFixed(2), h: +Math.max(1, h).toFixed(2) },
      font: { family: family(cs.fontFamily), size, weight: String(cs.fontWeight), lineHeight: lh, letterSpacing: px(cs.letterSpacing) },
      color: c, align: textAlign(cs), dir: cs.direction, lines: 1, decoration: null, field: ph ? 'placeholder' : 'value' });
  }

  function walk(el, parent) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return;
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'noscript', 'template', 'head', 'meta', 'link'].includes(tag)) return;
    const r = el.getBoundingClientRect();
    const sized = r.width >= 0.5 && r.height >= 0.5;

    if (tag === 'svg') {
      if (!sized) return;
      // Browsers fit the viewBox inside the box and centre it (preserveAspectRatio meet); Penpot stretches.
      // So size the icon to the fitted area and place it where the browser draws it.
      const b = box(r);
      const vbAttr = el.getAttribute('viewBox') || (el.querySelector('use') && (() => {
        const h = el.querySelector('use').getAttribute('href') || el.querySelector('use').getAttribute('xlink:href') || '';
        const t = h.startsWith('#') && document.getElementById(h.slice(1));
        return t && t.getAttribute('viewBox');
      })());
      const vb = (vbAttr || '').split(/[\s,]+/).map(Number);
      if (vb.length === 4 && vb[2] > 0 && vb[3] > 0 && (el.getAttribute('preserveAspectRatio') || '') !== 'none') {
        const k = Math.min(b.w / vb[2], b.h / vb[3]);
        const fw = vb[2] * k, fh = vb[3] * k;
        b.x = +(b.x + (b.w - fw) / 2).toFixed(2); b.y = +(b.y + (b.h - fh) / 2).toFixed(2); b.w = +fw.toFixed(2); b.h = +fh.toFixed(2);
      }
      nodes.push({ type: 'svg', parent, name: nameOf(el), box: b, svg: svgMarkup(el, cs, { width: b.w, height: b.h }) });
      return;
    }
    if (tag === 'img') {
      if (sized && (el.currentSrc || el.src)) nodes.push({ type: 'image', parent, name: el.alt || nameOf(el), box: box(r), src: el.currentSrc || el.src, fit: cs.objectFit });
      return;
    }

    let me = parent;
    if (sized && el !== document.body) {
      const fill = color(cs.backgroundColor);
      const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => ({ w: px(cs[`border${s}Width`]), style: cs[`border${s}Style`], c: color(cs[`border${s}Color`]) }))
        .map((b) => (b.w > 0 && b.style !== 'none' && b.c ? b : null));
      const sh = shadowOf(cs);
      const radius = ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].map((k) => Math.min(px(cs[`border${k}Radius`]), r.width / 2, r.height / 2));
      const clip = /(hidden|clip|auto|scroll)/.test(cs.overflow) && el.children.length > 0;
      const bg = bgImage(cs);
      if (fill || sides.some(Boolean) || sh || bg || el.getAttribute('data-uikit') || el.getAttribute('data-kit') || (clip && radius.some((v) => v > 0))) {
        nodes.push({ type: 'box', parent, name: nameOf(el), uikit: el.getAttribute('data-uikit') || null, box: box(r),
          fill, borders: sides, radius, shadow: sh, clip, opacity: +cs.opacity < 1 ? +cs.opacity : 1 });
        me = nodes.length - 1;
        if (bg) nodes.push({ type: 'image', parent: me, name: 'background', box: box(r), src: bg, fit: cs.backgroundSize === 'cover' ? 'cover' : 'contain' });
      }
      if (cs.content && cs.content !== 'none' && cs.content !== 'normal') warnings.add('pseudo-element content skipped');
    }
    if ((tag === 'input' && !NOT_TEXT.includes(el.type)) || tag === 'textarea' || tag === 'select') {
      if (sized) addField(el, cs, me);
      if (tag !== 'select') return;               // an input has no children; a textarea's text node is its value
    }
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) addText(child, el, cs, me);
      else if (child.nodeType === Node.ELEMENT_NODE && tag !== 'select') walk(child, me);
    }
  }

  const bodyBg = color(getComputedStyle(document.body).backgroundColor) || color(getComputedStyle(document.documentElement).backgroundColor) || { hex: '#ffffff', a: 1 };
  walk(document.body, -1);
  return { pnode: 1, w: W, h: H, background: bodyBg, title: document.title, nodes, warnings: [...warnings] };
}
