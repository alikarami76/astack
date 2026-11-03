// flynn recorder: the Record button in the canvas toolbar, its setup popup, and the live status.
// Loaded by inject.js. Talks to the panel server (/flynn/api/record/*), which drives flynn's Chrome, mines the
// recording and builds the results on the canvas. Penpot's toolbar is React-rendered, so the button is put back
// whenever a re-render drops it.
(function () {
  const API = '/flynn/api/record';
  const LI = 'main_ui_workspace_top_toolbar__toolbar-option';
  const ICON_BTN = 'main_ui_ds_buttons_icon_button__icon-button main_ui_ds_buttons_icon_button__icon-button-ghost';
  const BTN = 'main_ui_ds_buttons_button__button';
  let st = { state: 'idle' }, poll = null, doneTimer = null;
  const MAX = 3;                       // snaps per recording (the server enforces it too)

  const params = () => new URLSearchParams((location.hash.split('?')[1]) || '');
  const fileId = () => params().get('file-id') || '';
  const teamId = () => params().get('team-id') || '';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pageName = () => { const s = document.querySelector('[class*=sitemap__selected] [class*=page-item-label], [class*=sitemap__selected]'); return s ? s.textContent.trim() : 'this page'; };

  const DOT = '<svg class="fr-ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="8" cy="8" r="2.6" fill="currentColor"/></svg>';

  const FIGMA = '<svg class="fr-ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M5.5 1.75h2.5v4.5H5.5a2.25 2.25 0 0 1 0-4.5ZM8 1.75h2.5a2.25 2.25 0 0 1 0 4.5H8ZM5.5 6.25H8v4.5H5.5a2.25 2.25 0 0 1 0-4.5Z"/><circle cx="10.5" cy="8.5" r="2.25"/><path d="M5.5 10.75H8V13a2.25 2.25 0 1 1-2.5-2.25Z"/></svg>';

  // ---------- Figma import ----------
  function openFigma() {
    if (document.querySelector('.fr-overlay')) return;
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay';
    wrap.innerHTML = `
<div class="fr-dialog" role="dialog" aria-modal="true" aria-labelledby="fi-title">
  <div class="fr-head"><h2 id="fi-title">Import from Figma</h2>
    <button class="${ICON_BTN} fr-x" type="button" aria-label="Close"><svg class="fr-ic" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg></button></div>
  <p class="fr-lead">flynn opens the page through your Figma account, rebuilds every frame as real, editable layers, and puts them on a new page here, in the same arrangement.</p>
  <form class="fr-form" novalidate>
    <label class="fr-field"><span>Figma page link</span>
      <input name="url" type="url" required placeholder="https://www.figma.com/design/…?node-id=0-1">
      <small>It has to be a whole page: right-click the page in Figma's Pages list, then Copy link.</small></label>
    <label class="fr-field"><span>New page name (optional)</span>
      <input name="page_name" placeholder="Imported · the Figma page's name"></label>
    <div class="fr-error" role="alert" hidden></div>
    <div class="fr-actions">
      <button type="button" class="${BTN} main_ui_ds_buttons_button__button-secondary" data-a="cancel">Cancel</button>
      <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-primary" data-a="start">Import</button></div>
  </form>
</div>`;
    document.body.appendChild(wrap);
    const f = wrap.querySelector('form');
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } else e.stopPropagation(); };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.querySelector('.fr-x').onclick = close;
    wrap.querySelector('[data-a=cancel]').onclick = close;
    setTimeout(() => f.elements.url.focus(), 30);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = wrap.querySelector('.fr-error'), go = wrap.querySelector('[data-a=start]');
      go.disabled = true; err.hidden = true;
      try {
        const r = await fetch('/flynn/api/figma/import', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: fileId(), url: f.elements.url.value.trim(), page_name: f.elements.page_name.value.trim() }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        close();
        if (window.flynnShowChat) window.flynnShowChat();   // follow the import in the flynn tab
      } catch (x) { err.textContent = x.message; err.hidden = false; go.disabled = false; }
    });
  }

  async function api(path, body) {
    const r = await fetch(API + path + (body ? '' : `?file=${encodeURIComponent(fileId())}`), body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file: fileId(), team: teamId(), ...body }),
    } : {});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  // ---------- toolbar control ----------
  function control() {
    const ul = document.querySelector('.main_ui_workspace_top_toolbar__toolbar-options');
    if (!ul) return null;
    let li = ul.querySelector(':scope > li.fr-li');
    if (!li) {
      li = document.createElement('li');
      li.className = LI + ' fr-li';
      const mcp = ul.querySelector('[data-tool="mcp"]');
      const anchor = mcp ? mcp.closest('li') : null;
      if (anchor) ul.insertBefore(li, anchor); else ul.appendChild(li);
      li.dataset.state = '';
    }
    if (li.dataset.state !== stateKey()) render(li);
    let fli = ul.querySelector(':scope > li.fi-li');
    if (!fli) {
      fli = document.createElement('li');
      fli.className = LI + ' fi-li';
      fli.innerHTML = `<button class="${ICON_BTN} fr-rec" type="button" title="Import a page from Figma" aria-label="Import a page from Figma">${FIGMA}</button>`;
      fli.querySelector('button').onclick = openFigma;
      li.after(fli);
    }
    return li;
  }
  const stateKey = () => [st.state, st.pages, st.current, (st.result || {}).pageName].join('|');

  function render(li) {
    li.dataset.state = stateKey();
    const s = st.state;
    if (s === 'idle' || s === 'done-hidden') {
      li.innerHTML = `<button class="${ICON_BTN} fr-rec" type="button" title="Record a product" aria-label="Record a product">${DOT}</button>`;
      li.querySelector('button').onclick = openSetup;
    } else if (s === 'recording') {
      li.innerHTML = `<div class="fr-live" role="group" aria-label="Recording">
        <span class="fr-pulse" aria-hidden="true"></span><span class="fr-count">${st.pages || 0} of ${MAX} snaps</span>
        ${st.how === 'watch' ? `<button class="${BTN} main_ui_ds_buttons_button__button-ghost fr-small" type="button" data-a="capture" title="Snap the page open in flynn's Chrome, as it is now (same as Snap on the floating bar there)">Snap</button>` : ''}
        <button class="${BTN} main_ui_ds_buttons_button__button-secondary fr-small" type="button" data-a="stop">Stop</button></div>`;
      li.querySelector('[data-a=stop]').onclick = () => api('/stop', {}).then(refresh);
      const c = li.querySelector('[data-a=capture]');
      if (c) c.onclick = () => api('/capture', {}).then(refresh);
      li.title = st.current ? 'Recording ' + st.current : 'Recording';
    } else if (['mining', 'building', 'reviewing'].includes(s)) {
      const label = { mining: 'Finding components…', building: 'Building on the canvas…', reviewing: 'flynn is reviewing…' }[s];
      li.innerHTML = `<div class="fr-live fr-busy"><span class="fr-spin" aria-hidden="true"></span><span>${label}</span></div>`;
    } else if (s === 'done') {
      const r = st.result || {};
      const what = r.screens_only ? `${r.pages || 0} screen${r.pages === 1 ? '' : 's'} on the page` : `${(r.components || []).length} components · ${r.tokens || 0} tokens`;
      li.innerHTML = `<div class="fr-live fr-done">${DOT}<span>${what}</span>
        ${r.link ? `<button class="${BTN} main_ui_ds_buttons_button__button-ghost fr-small" type="button" data-a="open">Show page</button>` : ''}</div>`;
      const o = li.querySelector('[data-a=open]');
      if (o) o.onclick = () => { location.hash = r.link.replace(/^\/#/, ''); };
    } else if (s === 'error') {
      li.innerHTML = `<div class="fr-live fr-err" title="${esc(st.error)}">${DOT}<span>Recording failed</span>
        <button class="${BTN} main_ui_ds_buttons_button__button-ghost fr-small" type="button" data-a="again">Details</button></div>`;
      li.querySelector('[data-a=again]').onclick = () => { alert(st.error || 'The recording failed.'); st = { state: 'idle' }; render(li); };
    }
  }

  async function refresh() {
    if (!fileId()) return;                              // the dashboard has no file
    try { st = await api('/state'); } catch (e) { return; }
    if (st.state === 'done' && !doneTimer) doneTimer = setTimeout(() => { st = { state: 'done-hidden' }; doneTimer = null; control(); stopPoll(); }, 15000);
    control();
    if (['recording', 'mining', 'building', 'reviewing'].includes(st.state)) startPoll(); else if (st.state !== 'done') stopPoll();
  }
  // The flynn tab's /record and /figma commands open these same popups.
  window.flynnOpenRecorder = () => openSetup();
  window.flynnOpenFigma = () => openFigma();
  function startPoll() { if (!poll) poll = setInterval(refresh, 1000); }
  function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

  // ---------- setup popup ----------
  async function openSetup() {
    if (document.querySelector('.fr-overlay')) return;     // one popup at a time, even on a double click
    let last = {};
    try { last = (await api('/state')).last || {}; } catch (e) {}
    if (document.querySelector('.fr-overlay')) return;
    const page = pageName();
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay';
    wrap.innerHTML = `
<div class="fr-dialog" role="dialog" aria-modal="true" aria-labelledby="fr-title">
  <div class="fr-head"><h2 id="fr-title">Record a product</h2>
    <button class="${ICON_BTN} fr-x" type="button" aria-label="Close"><svg class="fr-ic" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg></button></div>
  <p class="fr-lead">flynn opens the product in its own Chrome window. You snap up to ${MAX} screens there, and flynn rebuilds them here as editable layers, and, if you want, turns them into a design system library.</p>
  <form class="fr-form" novalidate>
    <div class="fr-two">
    <label class="fr-field fr-grow"><span>Start address</span>
      <input name="url" type="url" required placeholder="https://seller.example.com/dashboard" value="${esc(last.url || '')}"></label>
    <label class="fr-field fr-sys" title="Recorded parts stay apart from the verified library until you approve them."><span>Design system</span>
      <input name="system" required placeholder="my-system" list="fr-systems" value="${esc(last.system || '')}">
      <datalist id="fr-systems"><option value="my-system"><option value="storefront"></datalist></label>
    </div>
    <label class="fr-field"><span>What are you recording?</span>
      <textarea name="description" rows="2" dir="auto" placeholder="e.g. The coupons flow: list, create form, empty and error states. Tables and filters matter most.">${esc(last.description || '')}</textarea>
      <small>flynn reads this to understand the screens and name what it finds.</small></label>
    <fieldset class="fr-field"><legend>Design system</legend>
      <div class="fr-seg" role="radiogroup">
        <label><input type="radio" name="ds" value="yes" ${last.design_system !== false ? 'checked' : ''}><span>Create or update the library</span></label>
        <label><input type="radio" name="ds" value="no" ${last.design_system === false ? 'checked' : ''}><span>Just the screens</span></label></div>
      <small class="fr-ds-hint"></small></fieldset>
    <fieldset class="fr-field"><legend>Put the results on</legend>
      <div class="fr-row"><label class="fr-opt"><input type="radio" name="target" value="current" checked> <span>This page, <b>${esc(page)}</b></span></label>
      <label class="fr-opt"><input type="radio" name="target" value="new"> <span>A new page</span></label></div>
      <input name="page_name" class="fr-sub" placeholder="Page name" hidden></fieldset>
    <fieldset class="fr-field"><legend>How</legend>
      <div class="fr-seg" role="radiogroup">
        <label><input type="radio" name="how" value="watch" ${last.how !== 'crawl' ? 'checked' : ''}><span>I'll browse</span></label>
        <label><input type="radio" name="how" value="crawl" ${last.how === 'crawl' ? 'checked' : ''}><span>flynn explores</span></label></div>
      <small class="fr-how-hint"></small>
      <label class="fr-inline fr-max" hidden>Up to <input name="max_pages" type="number" min="1" max="${MAX}" value="${esc(Math.min(last.max_pages || MAX, MAX))}"> pages (at most ${MAX})</label></fieldset>
    <fieldset class="fr-field"><legend>Sizes</legend>
      <div class="fr-row"><label class="fr-opt"><input type="checkbox" name="sizes" value="desktop" checked> <span>Desktop, 1440</span></label>
      <label class="fr-opt"><input type="checkbox" name="sizes" value="mobile"> <span>Mobile, 390</span></label></div></fieldset>
    <label class="fr-opt fr-ask"><input type="checkbox" name="ask_flynn" checked> <span>Ask flynn to review and name what it finds</span></label>
    <label class="fr-opt fr-rights"><input type="checkbox" name="rights"> <span>I have the right to record this product. Recordings stay on this Mac.</span></label>
    <div class="fr-error" role="alert" hidden></div>
    <div class="fr-actions">
      <button type="button" class="${BTN} main_ui_ds_buttons_button__button-secondary" data-a="cancel">Cancel</button>
      <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-primary" data-a="start">Start recording</button></div>
  </form>
</div>`;
    document.body.appendChild(wrap);
    const f = wrap.querySelector('form');
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } else e.stopPropagation(); };
    document.addEventListener('keydown', onKey, true);   // keep Penpot's shortcuts out of the form
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.querySelector('.fr-x').onclick = close;
    wrap.querySelector('[data-a=cancel]').onclick = close;
    const pn = f.elements.page_name;
    const syncTarget = () => {
      const isNew = f.elements.target.value === 'new';
      pn.hidden = !isNew;
      if (isNew && !pn.value) pn.value = `Recording · ${f.elements.system.value.trim() || 'design system'}`;
    };
    const syncHow = () => {
      const crawl = f.elements.how.value === 'crawl';
      wrap.querySelector('.fr-max').hidden = !crawl;
      wrap.querySelector('.fr-how-hint').textContent = crawl
        ? 'flynn follows links on the same site. It never clicks buttons, types or submits forms, and skips sign-out and delete links.'
        : `Browse in the window flynn opens. Nothing is recorded until you press Snap on the floating bar there (or ⌥⇧S): open the menu, tab or form first, then snap. The bar is never in the snap. Recording ends after ${MAX} snaps, or when you press Stop.`;
    };
    const sysLabel = wrap.querySelector('.fr-sys > span');
    const syncDs = () => {
      const ds = f.elements.ds.value !== 'no';
      wrap.querySelector('.fr-ask').hidden = !ds;
      f.elements.system.required = ds;
      sysLabel.textContent = ds ? 'Design system' : 'Product (optional)';
      wrap.querySelector('.fr-ds-hint').textContent = ds
        ? 'flynn finds the components and tokens and proposes them as a library change for you to approve.'
        : 'Only the screens go on the page. No components, no tokens, and no library is created or changed.';
      wrap.querySelector('[data-a=start]').textContent = ds ? 'Start recording' : 'Start';
    };
    f.addEventListener('change', () => { syncTarget(); syncHow(); syncDs(); });
    syncTarget(); syncHow(); syncDs();
    setTimeout(() => (f.elements.url.value ? f.elements.description : f.elements.url).focus(), 30);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = wrap.querySelector('.fr-error');
      const body = {
        url: f.elements.url.value.trim(), system: f.elements.system.value.trim(), description: f.elements.description.value.trim(),
        target: { mode: f.elements.target.value, page_name: pn.value.trim() }, how: f.elements.how.value,
        max_pages: Math.min(+f.elements.max_pages.value || MAX, MAX), design_system: f.elements.ds.value !== 'no', sizes: [...f.querySelectorAll('[name=sizes]:checked')].map((i) => i.value),
        ask_flynn: f.elements.ask_flynn.checked, rights: f.elements.rights.checked,
      };
      const go = wrap.querySelector('[data-a=start]');
      go.disabled = true; err.hidden = true;
      try { st = await api('/start', body); close(); control(); startPoll(); }
      catch (x) { err.textContent = x.message; err.hidden = false; go.disabled = false; }
    });
  }

  // ---------- keep the control in Penpot's toolbar ----------
  let queued = false;
  new MutationObserver(() => {
    if (queued) return; queued = true;
    requestAnimationFrame(() => { queued = false; if (location.hash.includes('/workspace')) control(); });
  }).observe(document.body, { childList: true, subtree: true });
  let lastFile = fileId();
  window.addEventListener('hashchange', () => {        // a page switch keeps the control; a different file resets it
    if (fileId() !== lastFile) { lastFile = fileId(); st = { state: 'idle' }; refresh(); }
  });
  refresh();
})();
