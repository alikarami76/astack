// flynn saved view: after a reload, the canvas comes back to where the user was (same page, same position, same zoom).
// Penpot keeps the page in the address but not the position or zoom. Both are readable from the viewport's SVG
// viewBox (x, y, width, height in canvas units), so this saves the view's centre and zoom per file and page
// whenever it changes, and after a reload moves the view back with the same wheel events a trackpad sends
// (ctrl+wheel zooms around the pointer, a plain wheel pans), checking the viewBox after each step.
(function () {
  const KEY = (file, page) => `flynn-view:${file}:${page}`;
  const params = () => new URLSearchParams((location.hash.split('?')[1]) || '');
  const svg = () => document.querySelector('svg.viewport-controls');
  const vb = (s) => { const v = (s.getAttribute('viewBox') || '').split(/\s+/).map(Number); return v.length === 4 && v.every(isFinite) ? v : null; };
  const read = (k) => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  let restoring = false, done = new Set(), watched = null, saveT = 0;

  function current(s) {
    const v = vb(s), r = s.getBoundingClientRect();
    if (!v || !r.width) return null;
    return { cx: v[0] + v[2] / 2, cy: v[1] + v[3] / 2, zoom: r.width / v[2] };
  }

  function save() {
    const s = svg(), file = params().get('file-id'), page = params().get('page-id');
    if (restoring || !s || !file || !page) return;
    const c = current(s);
    if (c) write(KEY(file, page), { ...c, at: Date.now() });
  }

  function wheel(s, init) {
    const r = s.getBoundingClientRect();
    s.dispatchEvent(new WheelEvent('wheel', { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
      bubbles: true, cancelable: true, deltaMode: 0, ...init }));
  }

  // Wait until Penpot has drawn the page and finished its own first move (it zooms to fit or to a board).
  async function settled(s) {
    let last = '', same = 0;
    for (let i = 0; i < 60; i++) {
      const v = s.getAttribute('viewBox');
      same = v === last ? same + 1 : 0;
      last = v;
      if (same >= 4) return true;
      await sleep(150);
    }
    return false;
  }

  async function restore(file, page) {
    const want = read(KEY(file, page));
    const s = svg();
    if (!want || !s || !(await settled(s))) return;
    if (params().get('page-id') !== page) return;
    restoring = true;
    try {
      // zoom: about 0.5% per wheel unit (measured); a few proportional steps land within 0.5%
      for (let i = 0; i < 12; i++) {
        const c = current(s);
        if (!c) return;
        const r = Math.log(want.zoom / c.zoom);
        if (Math.abs(r) < 0.005) break;
        wheel(s, { ctrlKey: true, deltaY: Math.max(-200, Math.min(200, -r / 0.005)) });
        await sleep(60);
      }
      // pan: a wheel of d pixels moves the view by d / zoom canvas units
      for (let i = 0; i < 4; i++) {
        const c = current(s);
        if (!c) return;
        const dx = (want.cx - c.cx) * c.zoom, dy = (want.cy - c.cy) * c.zoom;
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) break;
        wheel(s, { deltaX: dx, deltaY: dy });
        await sleep(60);
      }
    } finally {
      restoring = false;
    }
  }

  function tick() {
    const s = svg(), file = params().get('file-id'), page = params().get('page-id');
    if (!s || !file || !page || !location.hash.includes('/workspace')) return;
    if (watched !== s) {                     // a new viewport (first load, another file)
      watched = s;
      new MutationObserver(() => { clearTimeout(saveT); saveT = setTimeout(save, 400); })
        .observe(s, { attributes: true, attributeFilter: ['viewBox'] });
    }
    const key = file + ':' + page;
    if (!done.has(key)) { done.add(key); restore(file, page); }   // once per page per load of the app
  }

  setInterval(tick, 500);
  window.addEventListener('hashchange', tick);
  window.addEventListener('beforeunload', save);
})();
