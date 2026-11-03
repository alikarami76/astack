// flynn overlay: adds a "flynn" tab next to Design / Prototype / Inspect in Penpot's right sidebar.
// It reuses Penpot's own tab classes, so it looks and behaves like the native tabs. When it is selected,
// the flynn panel (an iframe, same origin) covers the tab panel area exactly.
(function () {
  const TAB = 'main_ui_ds_layout_tab_switcher__tab';
  const SEL = 'main_ui_ds_layout_tab_switcher__selected';
  const TEXT = 'main_ui_ds_layout_tab_switcher__tab-text';
  // Saved state: which tab is open in the right sidebar (flynn, Design, Prototype, Inspect), so a reload comes back
  // to it. Penpot keeps the page and the left tab in the address itself (page-id, layout); the canvas position and
  // zoom are view.js.
  const RTAB = 'flynn-rtab';
  const load = (k) => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } };
  const save = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
  let active = load(RTAB) === 'flynn';
  let restoredFor = '';                     // the file whose native tabs were already put back

  // Four tabs in the right sidebar: equal columns, slightly tighter padding, so no label is cut.
  const style = document.createElement('style');
  style.textContent = `
    .main_ui_workspace_sidebar_options__options-tab-switcher .main_ui_ds_layout_tab_switcher__tab-list:has(button[data-id="flynn"]) {
      grid-template-columns: repeat(4, auto); }
    .main_ui_workspace_sidebar_options__options-tab-switcher .main_ui_ds_layout_tab_switcher__tab-list:has(button[data-id="flynn"]) .main_ui_ds_layout_tab_switcher__tab {
      padding-inline: 4px; }`;
  document.head.appendChild(style);

  // The glass theme is flynn's look (approved 2026-10-04): /flynn/tron.css, scoped to <html data-flynn-theme="glass">
  // and Penpot's dark theme, so the light theme is untouched.
  const themeCss = document.createElement('link');
  themeCss.rel = 'stylesheet'; themeCss.href = '/flynn/tron.css';
  document.head.appendChild(themeCss);
  const glassJs = document.createElement('script');
  glassJs.src = '/flynn/glass.js'; glassJs.defer = true;
  document.head.appendChild(glassJs);
  // No Enterprise upsell: hide the banner and prompts (declutter.css), and close any Enterprise popup with its own
  // close button so Penpot's state stays consistent (removing React's nodes would break it).
  const declutter = document.createElement('link');
  declutter.rel = 'stylesheet'; declutter.href = '/flynn/declutter.css';
  document.head.appendChild(declutter);
  const CLOSE_UPSELL = '[class*="main_ui_nitrate_nitrate_form__close-btn"], [class*="main_ui_nitrate_nitrate_code_activation_modal__close-btn"], '
    + '[class*="main_ui_nitrate_nitrate_activation_success_modal__close-btn"], .main_ui_settings_subscription__modal-dialog .main_ui_settings_subscription__close-btn';
  let upsellQueued = false;
  new MutationObserver(() => {
    if (upsellQueued) return; upsellQueued = true;
    requestAnimationFrame(() => { upsellQueued = false; document.querySelectorAll(CLOSE_UPSELL).forEach((b) => b.click()); });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // The recorder: Record button in the canvas toolbar, its setup popup and live status.
  const recCss = document.createElement('link');
  recCss.rel = 'stylesheet'; recCss.href = '/flynn/recorder.css';
  document.head.appendChild(recCss);
  const recJs = document.createElement('script');
  recJs.src = '/flynn/recorder.js'; recJs.defer = true;
  document.head.appendChild(recJs);
  // Spaces: Designs and Letters (dashboard switch, New letter, Export in letters).
  const spJs = document.createElement('script');
  spJs.src = '/flynn/spaces.js'; spJs.defer = true;
  document.head.appendChild(spJs);
  // Organizations: flynn's own (adapters/orgs), in place of Penpot's paid ones.
  const orgJs = document.createElement('script');
  orgJs.src = '/flynn/orgs.js'; orgJs.defer = true;
  document.head.appendChild(orgJs);
  const viewJs = document.createElement('script');
  viewJs.src = '/flynn/view.js'; viewJs.defer = true;
  document.head.appendChild(viewJs);
  // First sign-in: forced change of the temporary password.
  const flJs = document.createElement('script');
  flJs.src = '/flynn/firstlogin.js'; flJs.defer = true;
  document.head.appendChild(flJs);
  // Your account > Profile: "flynn on your computer" (connection state and the agent's install command).
  const agJs = document.createElement('script');
  agJs.src = '/flynn/agent-settings.js'; agJs.defer = true;
  document.head.appendChild(agJs);
  document.documentElement.dataset.flynnTheme = 'glass';
  try { localStorage.removeItem('flynn-theme'); } catch (e) {}   // the old test switch's memory

  const rightTabList = () =>
    [...document.querySelectorAll('.main_ui_ds_layout_tab_switcher__tab-list')]
      .find((ul) => ul.querySelector('button[data-id="design"]'));
  const panelArea = (ul) => {
    const sw = ul && ul.closest('.main_ui_ds_layout_tab_switcher__tabs');
    return sw && sw.querySelector(':scope > .main_ui_ds_layout_tab_switcher__tab-panel');
  };

  function frame() {
    let f = document.getElementById('flynn-frame');
    if (!f) {
      f = document.createElement('iframe');
      f.id = 'flynn-frame';
      f.title = 'flynn';
      // Above the sidebar (z-index 100) but below Penpot's menus and dropdowns (400 and up), so they open over the tab.
      f.style.cssText = 'position:fixed;border:0;z-index:300;display:none;background:transparent';
      document.body.appendChild(f);
    }
    // Only the team and file decide the panel; switching pages must not reload the conversation.
    const h = new URLSearchParams((location.hash.split('?')[1]) || '');
    const want = '/flynn/panel.html?team-id=' + encodeURIComponent(h.get('team-id') || '') + '&file-id=' + encodeURIComponent(h.get('file-id') || '');
    if (f.dataset.src !== want) { f.dataset.src = want; f.src = want; }
    return f;
  }

  function setActive(on, id) {
    active = on;
    save(RTAB, on ? 'flynn' : (id || 'design'));
    sync();
  }

  function sync() {
    placeGrip();
    const ul = rightTabList();
    const f = frame();
    if (!location.hash.includes('/workspace') || !ul) { f.style.display = 'none'; return; }

    let btn = ul.querySelector('button[data-id="flynn"]');
    if (!btn) {
      const li = document.createElement('li');
      btn = document.createElement('button');
      btn.className = TAB;
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', 'false');
      btn.setAttribute('tabindex', '-1');
      btn.title = 'flynn';
      btn.dataset.id = 'flynn';
      btn.id = 'flynn';
      const span = document.createElement('span');
      span.className = TEXT;
      span.textContent = 'flynn';
      btn.appendChild(span);
      li.appendChild(btn);
      ul.appendChild(li);
      btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); setActive(true); });
      // Clicking any native tab hands control back to Penpot.
      ul.addEventListener('click', (e) => {
        const b = e.target.closest('button[role=tab]');
        if (b && b.dataset.id !== 'flynn') setActive(false, b.dataset.id);
      }, true);
    }

    const natives = [...ul.querySelectorAll('button[role=tab]')].filter((b) => b.dataset.id !== 'flynn');
    restoreTabs(ul);
    if (active) {
      natives.forEach((b) => { b.classList.remove(SEL); b.setAttribute('aria-selected', 'false'); });
      btn.classList.add(SEL); btn.setAttribute('aria-selected', 'true');
      const area = panelArea(ul);
      if (area) {
        const r = area.getBoundingClientRect();
        Object.assign(f.style, { display: 'block', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
        // With the glass theme the panel is see-through, so the native tab underneath must not show.
        const hide = !!document.documentElement.dataset.flynnTheme;
        if (area.style.visibility !== (hide ? 'hidden' : '')) area.style.visibility = hide ? 'hidden' : '';
      }
    } else {
      btn.classList.remove(SEL); btn.setAttribute('aria-selected', 'false');
      f.style.display = 'none';
      const area = panelArea(ul);
      if (area && area.style.visibility) area.style.visibility = '';
    }
  }

  // For a few seconds after a file opens, click the saved right-sidebar tab until Penpot shows it (it redraws the
  // sidebars while the file loads, which can undo an early click).
  let restoreUntil = 0;
  function restoreTabs(ul) {
    const file = new URLSearchParams((location.hash.split('?')[1]) || '').get('file-id') || '';
    if (!file) return;
    if (restoredFor !== file) { restoredFor = file; restoreUntil = Date.now() + 8000; }
    if (Date.now() > restoreUntil) return;
    const r = load(RTAB);
    const b = r && r !== 'flynn' && ul.querySelector(`button[data-id="${CSS.escape(r)}"]`);
    if (b && b.getAttribute('aria-selected') !== 'true') b.click();
  }


  // Resizable right sidebar: drag its left edge (double-click resets). Penpot sizes the sidebar from
  // --right-sidebar-width, so the whole panel (Design, Prototype, Inspect and flynn) follows. Remembered.
  const WIDTH_KEY = 'flynn-right-width', MIN_W = 318;
  const maxW = () => Math.min(768, Math.round(innerWidth * 0.6));
  function setWidth(w, save) {
    // Penpot writes its own --right-sidebar-width inline on the sidebar, so the chosen width goes into
    // --flynn-right-width and a stylesheet rule with !important (which beats a plain inline value) applies it.
    const root = document.documentElement;
    if (w == null) { root.style.removeProperty('--flynn-right-width'); delete root.dataset.flynnRw; }
    else { root.style.setProperty('--flynn-right-width', Math.max(MIN_W, Math.min(maxW(), Math.round(w))) + 'px'); root.dataset.flynnRw = '1'; }
    if (save) { try { if (w == null) localStorage.removeItem(WIDTH_KEY); else localStorage.setItem(WIDTH_KEY, String(Math.round(w))); } catch (e) {} }
  }
  try { const w = +localStorage.getItem(WIDTH_KEY); if (w) setWidth(w, false); } catch (e) {}
  const grip = document.createElement('div');
  grip.id = 'flynn-resize';
  grip.title = 'Drag to resize the panel · double-click to reset';
  document.body.appendChild(grip);
  const gripStyle = document.createElement('style');
  gripStyle.textContent = `#flynn-resize { position: fixed; top: 0; width: 7px; height: 100vh; margin-left: -3px; z-index: 301;
      cursor: ew-resize; display: none; touch-action: none; }
    #flynn-resize::after { content: ''; position: absolute; left: 3px; top: 0; bottom: 0; width: 1px; background: transparent;
      transition: background-color .15s, box-shadow .15s; }
    #flynn-resize:hover::after, #flynn-resize.drag::after { background: var(--color-accent-primary); box-shadow: 0 0 8px var(--color-accent-primary); }
    body.flynn-resizing, body.flynn-resizing * { cursor: ew-resize !important; user-select: none !important; }
    body.flynn-resizing #flynn-frame { pointer-events: none; }
    html[data-flynn-rw] .main_ui_workspace_sidebar__right-sidebar { --right-sidebar-width: var(--flynn-right-width) !important; }
    /* The right sidebar is a flex item with z-index 100, which makes it its own stacking context: its menus (zoom,
       share, ...) could then never rise above the flynn tab. Without it, they sit at their own z-index (400 and up)
       above the tab (300). */
    .main_ui_workspace_sidebar__right-sidebar { z-index: auto !important; }`;
  document.head.appendChild(gripStyle);
  function placeGrip() {
    const side = document.querySelector('.main_ui_workspace_sidebar__right-sidebar');
    if (!side || !location.hash.includes('/workspace')) { grip.style.display = 'none'; return; }
    const r = side.getBoundingClientRect();
    grip.style.display = 'block';
    grip.style.left = r.left + 'px';
  }
  let dragging = false, raf = 0;
  grip.addEventListener('pointerdown', (e) => {
    dragging = true; grip.setPointerCapture(e.pointerId); grip.classList.add('drag'); document.body.classList.add('flynn-resizing');
  });
  grip.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    if (e.buttons === 0) { endDrag(e); return; }     // the button came up somewhere we did not hear
    const w = innerWidth - e.clientX;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { setWidth(w, false); placeGrip(); sync(); });
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false; grip.classList.remove('drag'); document.body.classList.remove('flynn-resizing');
    setWidth(innerWidth - e.clientX, true);
    window.dispatchEvent(new Event('resize'));       // let the canvas recompute its viewport
  };
  // End the drag on every way it can end, so the panel never stays in resize mode (which blocks the chat).
  grip.addEventListener('pointerup', endDrag);
  grip.addEventListener('pointercancel', endDrag);
  grip.addEventListener('lostpointercapture', endDrag);
  window.addEventListener('pointerup', endDrag, true);
  window.addEventListener('blur', () => endDrag({ clientX: innerWidth - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--flynn-right-width')) || 318) }));
  grip.addEventListener('dblclick', () => { setWidth(null, true); window.dispatchEvent(new Event('resize')); });

  window.flynnShowChat = () => setActive(true);        // other overlay parts (the Figma import) open the chat
  window.addEventListener('hashchange', sync);
  window.addEventListener('resize', sync);
  new MutationObserver(() => sync()).observe(document.documentElement, { childList: true, subtree: true });
  setInterval(sync, 1000);
})();
