// flynn recorder: prepares a page for a faithful full-length capture, and puts it back afterwards.
// Installs window.__flynnStitch with:
//   prep()        find what really scrolls. When the document does not scroll but an inner panel does (an app
//                 shell with a scrolling main area), "unroll" that panel and its ancestors (height auto,
//                 overflow visible) so the whole content becomes the document and keeps its layout.
//   freeze()      stop animations and transitions, turn sticky elements into relative ones (same place in the
//                 flow, but they no longer follow the scroll) and list fixed elements. Returns the page height.
//   fixed(show)   show or hide fixed elements: they appear once, in the first screen, where they sit at the top
//                 of the page, instead of repeating in every stitched screen.
//   late()        after each scroll, hide what the page made float only because it scrolled: elements that
//                 became fixed or sticky after freeze() (a table header copied to the top, a "back to top"
//                 bar), and floating elements added after freeze(). They would land on the content.
//   restore()     undo everything and scroll back to where the page was.
// Every change is recorded and undone; nothing is left behind on the page the user is browsing.
(() => {
  if (window.__flynnStitch) return true;
  const undo = [];
  const setStyle = (el, prop, value) => {
    undo.push([el, prop, el.style.getPropertyValue(prop), el.style.getPropertyPriority(prop)]);
    el.style.setProperty(prop, value, 'important');
  };
  let fixedEls = [], lateEls = [], added = [], mo = null, styleEl = null, startScroll = [0, 0], scroller = null, panel = null, panelTop = 0;
  const docScrolls = () => (document.scrollingElement || document.documentElement).scrollHeight > innerHeight + 2;

  function prep() {
    startScroll = [scrollX, scrollY];
    let unrolled = false;
    // A page can be taller than the window yet refuse to scroll (overflow hidden on html or body).
    if (docScrolls()) {
      const y = scrollY; window.scrollTo(0, y + 1);
      const moved = scrollY !== y; window.scrollTo(0, y);
      if (!moved) { setStyle(document.documentElement, 'overflow', 'visible'); if (document.body) setStyle(document.body, 'overflow', 'visible'); }
    }
    if (!docScrolls()) {
      let best = null, bestArea = 0;
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el);
        if (!/(auto|scroll|overlay)/.test(cs.overflowY)) continue;
        if (el.scrollHeight <= el.clientHeight + 40) continue;
        const r = el.getBoundingClientRect();
        if (r.height < innerHeight * 0.4 || r.width < innerWidth * 0.4) continue;
        const area = r.width * r.height;
        if (area > bestArea) { best = el; bestArea = area; }
      }
      if (best) {
        scroller = { tag: best.tagName.toLowerCase(), cls: String(best.className).slice(0, 80), before: best.scrollHeight };
        panel = best; panelTop = best.scrollTop;          // put back on restore
        best.scrollTop = 0;
        for (let el = best; el && el !== document.documentElement; el = el.parentElement) {
          setStyle(el, 'height', 'auto');
          setStyle(el, 'max-height', 'none');
          setStyle(el, 'overflow', 'visible');
        }
        setStyle(document.documentElement, 'height', 'auto');
        setStyle(document.documentElement, 'overflow', 'visible');
        unrolled = docScrolls();
      }
    }
    return { docScrolls: docScrolls(), unrolled, scroller, height: document.documentElement.scrollHeight, vh: innerHeight };
  }

  function freeze() {
    styleEl = document.createElement('style');
    // no animation, no blinking caret, and no scrollbars (they would repeat in every stitched screen)
    styleEl.textContent = '*,*::before,*::after{animation-play-state:paused!important;transition:none!important;caret-color:transparent!important;scroll-behavior:auto!important}'
      + 'html,body{scrollbar-width:none!important}::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}';
    document.head.appendChild(styleEl);
    fixedEls = [];
    for (const el of document.querySelectorAll('body *')) {
      const p = getComputedStyle(el).position;
      if (p === 'sticky' || p === '-webkit-sticky') setStyle(el, 'position', 'relative');
      else if (p === 'fixed') fixedEls.push(el);
    }
    const known = new Set(fixedEls);
    known.add(styleEl);
    mo = new MutationObserver((recs) => { for (const r of recs) for (const n of r.addedNodes) if (n.nodeType === 1) added.push(n); });
    mo.observe(document.body || document.documentElement, { childList: true, subtree: true });
    window.__flynnStitchKnown = known;
    window.scrollTo(0, 0);
    return { height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0),
             vh: innerHeight, vw: innerWidth, fixed: fixedEls.length, dpr: devicePixelRatio };
  }

  function fixed(show) {
    for (const el of fixedEls) {
      if (show) { if (el.dataset.flynnHidden) { el.style.visibility = el.dataset.flynnHidden === '-' ? '' : el.dataset.flynnHidden; delete el.dataset.flynnHidden; } }
      else if (!el.dataset.flynnHidden) { el.dataset.flynnHidden = el.style.visibility || '-'; el.style.visibility = 'hidden'; }
    }
    return true;
  }

  const floating = (p) => p === 'fixed' || p === 'sticky' || p === '-webkit-sticky';
  function hideLate(el) {
    if (el.dataset.flynnLate) return;
    el.dataset.flynnLate = el.style.visibility || '-';
    el.style.setProperty('visibility', 'hidden', 'important');
    lateEls.push(el);
  }
  function late() {
    const known = window.__flynnStitchKnown || new Set();
    let n = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (known.has(el) || el.dataset.flynnLate || el.id === '__flynn-snap') continue;
      if (floating(getComputedStyle(el).position)) { hideLate(el); n++; }
    }
    for (const el of added) {
      if (!el.isConnected || el.dataset.flynnLate || el.id === '__flynn-snap') continue;
      const p = getComputedStyle(el).position;
      if (floating(p) || p === 'absolute') { hideLate(el); n++; }
    }
    return n;
  }

  function restore() {
    fixed(true);
    if (mo) { mo.disconnect(); mo = null; }
    for (const el of lateEls) {
      const v = el.dataset.flynnLate; delete el.dataset.flynnLate;
      if (v === '-') el.style.removeProperty('visibility'); else el.style.setProperty('visibility', v);
    }
    lateEls = []; added = [];
    delete window.__flynnStitchKnown;
    if (styleEl) styleEl.remove();
    for (let i = undo.length - 1; i >= 0; i--) {
      const [el, prop, value, prio] = undo[i];
      if (value) el.style.setProperty(prop, value, prio); else el.style.removeProperty(prop);
    }
    undo.length = 0;
    if (panel) panel.scrollTop = panelTop;
    window.scrollTo(startScroll[0], startScroll[1]);
    delete window.__flynnStitch;
    return true;
  }

  window.__flynnStitch = { prep, freeze, fixed, late, restore };
  return true;
})();
