// flynn snap bar: a small floating bar on every page of flynn's Chrome while a recording or a watch runs.
// the user presses Snap (or Alt+Shift+S) and flynn records the page exactly as it is now. The bar lives in a closed
// shadow root on <html>, so the page's styles cannot reach it, and flynn removes it before every screenshot
// and HTML capture, so it never appears in a snap. It talks to flynn through a DevTools binding
// (window.__flynnSnapRequest), never over the network.
(() => {
  if (window.top !== window) return;                 // the top page only, not iframes
  if (window.__flynnSnapBar) { window.__flynnSnapBar.mount(); return; }
  const ID = '__flynn-snap';
  let host = null, ui = null, state = { mode: 'idle', count: 0, label: '' }, doneTimer = null;
  const POS = 'flynn-snapbar-pos';
  const readPos = () => { try { return JSON.parse(localStorage.getItem(POS) || 'null'); } catch (e) { return null; } };
  const savePos = (p) => { try { localStorage.setItem(POS, JSON.stringify(p)); } catch (e) {} };

  const CSS = `
:host{all:initial}
.bar{position:fixed;z-index:2147483647;display:flex;align-items:center;gap:8px;padding:6px 6px 6px 8px;
  background:#0f141a;color:#e8eef4;border:1px solid rgba(255,160,74,.45);border-radius:999px;
  box-shadow:0 6px 24px rgba(0,0,0,.35),0 0 0 4px rgba(255,140,40,.08);
  font:500 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;user-select:none;direction:ltr}
.grip{width:18px;height:24px;cursor:grab;display:grid;place-items:center;color:#7d8b99}
.grip:active{cursor:grabbing}
.disc{width:16px;height:16px;border-radius:50%;border:2px solid #ffa04a;box-shadow:0 0 8px rgba(255,160,74,.7);
  position:relative;flex:none}
.disc::after{content:"";position:absolute;inset:3px;border-radius:50%;background:#ffa04a}
.busy .disc{animation:spin 1s linear infinite;border-color:#ffa04a transparent #ffa04a #ffa04a}
.busy .disc::after{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.count{color:#aab7c4;min-width:52px;font-variant-numeric:tabular-nums}
button{all:unset;cursor:pointer;background:#ff8a2a;color:#1a0f05;font:600 13px/1 inherit;font-family:inherit;
  padding:8px 14px;border-radius:999px}
button:hover{background:#ff9d4a}
button:focus-visible{outline:2px solid #ffd2a8;outline-offset:2px}
.busy button{background:#5a4330;color:#d9c3ad;cursor:default}
.done button{background:#2fbf71;color:#04170c}
.key{color:#7d8b99;font-size:11px;padding-right:6px}`;

  function render() {
    if (!ui) return;
    const { mode, count, label } = state;
    ui.bar.className = 'bar ' + mode;
    ui.btn.textContent = mode === 'busy' ? 'Snapping…' : mode === 'done' ? 'Snapped' : 'Snap';
    ui.btn.disabled = mode === 'busy';
    ui.count.textContent = label || `${count} snap${count === 1 ? '' : 's'}`;
  }

  function request() {
    if (state.mode === 'busy') return;
    if (typeof window.__flynnSnapRequest !== 'function') { state.label = 'flynn is not listening'; render(); return; }
    state.mode = 'busy'; state.label = ''; render();
    window.__flynnSnapRequest(JSON.stringify({ url: location.href, at: Date.now() }));
  }

  function place(bar) {
    const p = readPos();
    if (p && p.x >= 0 && p.y >= 0 && p.x < innerWidth - 40 && p.y < innerHeight - 20) {
      bar.style.left = p.x + 'px'; bar.style.top = p.y + 'px';
    } else { bar.style.left = '50%'; bar.style.bottom = '20px'; bar.style.transform = 'translateX(-50%)'; }
  }

  function drag(bar, grip) {
    grip.addEventListener('pointerdown', (e) => {
      e.preventDefault(); grip.setPointerCapture(e.pointerId);
      const r = bar.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      const move = (ev) => {
        const x = Math.max(0, Math.min(innerWidth - r.width, ev.clientX - dx));
        const y = Math.max(0, Math.min(innerHeight - r.height, ev.clientY - dy));
        Object.assign(bar.style, { left: x + 'px', top: y + 'px', bottom: '', transform: '' });
      };
      const up = () => {
        grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up);
        const b = bar.getBoundingClientRect(); savePos({ x: Math.round(b.left), y: Math.round(b.top) });
      };
      grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
    });
  }

  function mount() {
    if (document.getElementById(ID)) return true;
    host = document.createElement('div');
    host.id = ID;
    host.setAttribute('data-flynn', 'snapbar');
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = `<style>${CSS}</style><div class="bar" role="toolbar" aria-label="flynn snap">
      <span class="grip" title="Drag to move" aria-hidden="true"><svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor"><circle cx="2" cy="2" r="1.2"/><circle cx="6" cy="2" r="1.2"/><circle cx="2" cy="7" r="1.2"/><circle cx="6" cy="7" r="1.2"/><circle cx="2" cy="12" r="1.2"/><circle cx="6" cy="12" r="1.2"/></svg></span>
      <span class="disc" aria-hidden="true"></span><span class="count" aria-live="polite"></span>
      <span class="key">⌥⇧S</span><button type="button" title="Snap this page as it is now (Alt+Shift+S)">Snap</button></div>`;
    ui = { bar: root.querySelector('.bar'), btn: root.querySelector('button'), count: root.querySelector('.count') };
    ui.btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); request(); });
    for (const t of ['mousedown', 'pointerdown', 'mouseup', 'click']) ui.bar.addEventListener(t, (e) => e.stopPropagation());
    drag(ui.bar, root.querySelector('.grip'));
    place(ui.bar);
    (document.documentElement || document).appendChild(host);
    render();
    return true;
  }

  function unmount() { const h = document.getElementById(ID); if (h) h.remove(); host = null; ui = null; return true; }

  function set(next) {
    state = { ...state, ...next };
    clearTimeout(doneTimer);
    if (state.mode === 'done') doneTimer = setTimeout(() => { state.mode = 'idle'; render(); }, 1600);
    render();
    return true;
  }

  document.addEventListener('keydown', (e) => {
    if (e.altKey && e.shiftKey && (e.code === 'KeyS')) { e.preventDefault(); e.stopPropagation(); request(); }
  }, true);
  // The page can replace <html>'s children (single-page apps); put the bar back when it goes missing.
  setInterval(() => { if (window.__flynnSnapBar && window.__flynnSnapBar.wanted && !document.getElementById(ID)) mount(); }, 1500);

  window.__flynnSnapBar = { mount, unmount, set, wanted: true,
    hide() { this.wanted = false; return unmount(); }, show() { this.wanted = true; return mount(); } };
  if (document.documentElement) mount(); else document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
