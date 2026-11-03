// flynn recorder: one page's design evidence, run inside the recorded page (Selenium execute_script).
// Returns {url, title, dpr, viewport, doc, vars, fonts, media, els}. Each element in `els` has its box in page
// coordinates, a compact set of computed styles, its role and classes, its own short text, and, for elements
// that look like components (controls, roles, component-like class names), a trimmed outerHTML snippet.
// Real data is scrubbed later in Python; this script never reads input values or storage.
(() => {
  const MAX = 5000;
  const COMPONENTY = /(btn|button|card|chip|badge|tag|pill|avatar|tab|toggle|switch|checkbox|radio|select|dropdown|menu|input|field|textfield|search|modal|dialog|drawer|tooltip|popover|toast|snackbar|alert|banner|notice|breadcrumb|pagination|pager|stepper|accordion|table|list-?item|navbar|nav|sidebar|header|footer|toolbar|filter|empty|skeleton|spinner|loader|progress|divider|icon)/i;
  const CONTROL = 'button,input,select,textarea,a[href],[role],table,nav,dialog,label,img,svg,hr,progress';
  const STY = ['display', 'position', 'color', 'backgroundColor', 'backgroundImage', 'borderTopWidth', 'borderTopStyle',
    'borderTopColor', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'borderRadius', 'boxShadow',
    'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'textTransform', 'textAlign',
    'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
    'gap', 'flexDirection', 'justifyContent', 'alignItems', 'opacity', 'cursor', 'direction', 'zIndex'];
  const sx = window.scrollX, sy = window.scrollY;

  const els = [], index = new Map();
  const all = document.body ? document.body.getElementsByTagName('*') : [];
  for (const el of all) {
    if (els.length >= MAX) break;
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'noscript', 'template', 'meta', 'link', 'head', 'path', 'g', 'defs', 'use', 'symbol', 'clippath', 'mask', 'lineargradient', 'stop'].includes(tag)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    const cls = typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '';
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    own = own.replace(/\s+/g, ' ').trim().slice(0, 80);
    const st = {};
    for (const k of STY) st[k] = cs[k];
    if (st.backgroundImage === 'none') delete st.backgroundImage; else st.backgroundImage = st.backgroundImage.slice(0, 200);
    const role = el.getAttribute('role') || '';
    const item = {
      i: els.length, p: index.has(el.parentElement) ? index.get(el.parentElement) : -1, tag, cls: cls.slice(0, 200),
      id: (el.id || '').slice(0, 60), role, type: tag === 'input' ? (el.getAttribute('type') || 'text') : '',
      aria: (el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || '').slice(0, 80),
      text: own, box: [Math.round(r.left + sx), Math.round(r.top + sy), Math.round(r.width), Math.round(r.height)],
      fixed: cs.position === 'fixed', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true', s: st,
    };
    if (el.matches(CONTROL) || COMPONENTY.test(cls) || COMPONENTY.test(role)) {
      item.html = el.outerHTML.slice(0, 2400);
    }
    index.set(el, item.i);
    els.push(item);
  }

  // CSS custom properties defined on :root (names from the stylesheets, values as computed).
  const names = new Set(), media = new Set();
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.style) for (const p of rule.style) if (p.startsWith('--')) names.add(p);
      if (rule.media) media.add(rule.media.mediaText);
      if (rule.cssRules) walk(rule.cssRules);
    }
  };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch (e) { /* cross-origin sheet */ } }
  const root = getComputedStyle(document.documentElement), vars = {};
  for (const n of names) { const v = root.getPropertyValue(n).trim(); if (v) vars[n] = v.slice(0, 200); }

  const fonts = [...new Set([...document.fonts].filter((f) => f.status === 'loaded').map((f) => `${f.family.replace(/"/g, '')} ${f.weight}`))];
  return {
    url: location.href, title: document.title, dpr: window.devicePixelRatio, lang: document.documentElement.lang,
    dir: document.documentElement.dir || getComputedStyle(document.documentElement).direction,
    viewport: [innerWidth, innerHeight], doc: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    vars, fonts, media: [...media].slice(0, 200), els, truncated: els.length >= MAX,
  };
})();
