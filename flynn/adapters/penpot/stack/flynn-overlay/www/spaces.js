// flynn desk file types: Designs (product UI) and Letters (release notes, reports, decks, newsletters), in any team.
// Dashboard: a Designs / Letters switch that filters files by type, and "New letter" in the Letters view.
// Workspace: in a letter, the toolbar's Record and Figma buttons step aside for Export (formats by letter type).
// Loaded by inject.js. Styles: recorder.css (popups) and the rules at the end of this file.
(function () {
  const API = '/flynn/api';
  const BTN = 'main_ui_ds_buttons_button__button';
  const ICON_BTN = 'main_ui_ds_buttons_icon_button__icon-button main_ui_ds_buttons_icon_button__icon-button-ghost';
  let spaces = null, letter = undefined, exportsFor = [];
  const params = () => new URLSearchParams((location.hash.split('?')[1]) || '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const TYPE_ICON = {
    deck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="11" rx="1.5"/><path d="M12 16v3M8 21h8"/></svg>',
    newsletter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="m3.5 6 8.5 7 8.5-7"/></svg>',
    'release-note': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M6 3h9l3 3v15H6Z"/><path d="M9 10h6M9 13.5h6M9 17h4"/></svg>',
    report: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 20V4M4 20h16"/><path d="M8 16v-4M12 16V8M16 16v-6"/></svg>',
  };
  const FORMAT = { pdf: ['PDF', 'For reading and printing'], pptx: ['PowerPoint', 'Editable slides'], png: ['Images', 'One PNG per section or slide'],
                   email: ['Email draft', 'An Outlook draft, never sent'], canva: ['Canva', 'To keep editing in Canva'] };

  // Design and Letter are file types (like Figma Design and Figma Slides), not teams: every team and project can
  // hold both. The switch filters the dashboard by type; it is remembered per browser.
  let lettersAt = 0;
  async function loadSpaces(force) {
    if (spaces && !force && Date.now() - lettersAt < 8000) return spaces;
    try { spaces = await (await fetch(API + '/spaces')).json(); } catch (e) { spaces = spaces || {}; }
    spaces.set = new Set(spaces.letters || []); lettersAt = Date.now();
    return spaces;
  }
  const mode = () => { try { return localStorage.getItem('flynn-space') === 'letters' ? 'letters' : 'designs'; } catch (e) { return 'designs'; } };
  const setMode = (m) => { try { localStorage.setItem('flynn-space', m); } catch (e) {} };

  // ---------- dashboard ----------
  function dashboard() {
    const side = document.querySelector('.main_ui_dashboard_sidebar__sidebar-content');
    if (!side || !spaces) return;
    let sw = side.querySelector(':scope > .fs-spaces');
    const cur = mode();
    if (!sw) {
      sw = document.createElement('div');
      sw.className = 'fs-spaces';
      sw.setAttribute('role', 'tablist');
      sw.setAttribute('aria-label', 'File type');
      sw.innerHTML = `<button role="tab" data-s="designs">Designs</button><button role="tab" data-s="letters">Letters</button>`;
      sw.querySelectorAll('button').forEach((b) => b.onclick = () => { setMode(b.dataset.s); dashboard(); });
      side.prepend(sw);
    }
    sw.querySelectorAll('button').forEach((b) => { const on = b.dataset.s === cur; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    document.documentElement.dataset.flynnSpace = cur;
    // every file card (grid and list) carries its file id on its options button: mark each card with its type
    document.querySelectorAll('[id$="-action-menu"]').forEach((m) => {
      const fid = m.id.slice(0, -'-action-menu'.length);
      const card = m.closest('li') || m.closest('[class*=grid-item]');
      if (card) { const k = spaces.set.has(fid) ? 'letter' : 'design'; if (card.dataset.flynnKind !== k) card.dataset.flynnKind = k; }
    });
    const actions = document.querySelector('.main_ui_dashboard_projects__dashboard-header-actions');
    let nb = document.querySelector('.fs-newletter');
    // One "new" button per view, in the same place and style: + New project for designs, + New letter for letters
    // (Penpot's own button is hidden there by CSS).
    if (cur === 'letters' && actions && !nb) {
      const np = actions.querySelector('[data-testid="new-project-button"]');
      nb = document.createElement('button');
      nb.className = (np ? np.className : `${BTN} main_ui_ds_buttons_button__button-secondary`) + ' fs-newletter';
      nb.type = 'button'; nb.textContent = '+ New letter';
      nb.onclick = newLetter;
      if (np) np.after(nb); else actions.append(nb);
    } else if (cur !== 'letters' && nb) nb.remove();
  }

  function newLetter() {
    if (document.querySelector('.fr-overlay')) return;
    const types = spaces.types || [];
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay';
    wrap.innerHTML = `
<div class="fr-dialog" role="dialog" aria-modal="true" aria-labelledby="fl-title">
  <div class="fr-head"><h2 id="fl-title">New letter</h2>
    <button class="${ICON_BTN} fr-x" type="button" aria-label="Close"><svg class="fr-ic" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg></button></div>
  <p class="fr-lead">The letter goes into this team${params().get('project-id') ? ' and project' : '’s Drafts'}. flynn starts it from its own template, builds it here as editable layers, and keeps the page as the master for exports.</p>
  <form class="fr-form" novalidate>
    <fieldset class="fr-field"><legend>Type</legend>
      <div class="fs-types">${types.map((t, i) => `<label class="fs-type"><input type="radio" name="type" value="${esc(t.type)}" ${i === 0 ? 'checked' : ''}>
        <span class="fs-card">${TYPE_ICON[t.type] || ''}<b>${esc(t.label)}</b><small>${esc(t.hint)}</small></span></label>`).join('')}</div></fieldset>
    <label class="fr-field"><span>Title</span><input name="title" dir="auto" required placeholder="e.g. Q3 pricing review"></label>
    <div class="fr-error" role="alert" hidden></div>
    <div class="fr-actions">
      <button type="button" class="${BTN} main_ui_ds_buttons_button__button-secondary" data-a="cancel">Cancel</button>
      <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-primary" data-a="go">Create</button></div>
  </form></div>`;
    document.body.appendChild(wrap);
    const f = wrap.querySelector('form');
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } else e.stopPropagation(); };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.querySelector('.fr-x').onclick = close;
    wrap.querySelector('[data-a=cancel]').onclick = close;
    setTimeout(() => f.elements.title.focus(), 30);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = wrap.querySelector('.fr-error'), go = wrap.querySelector('[data-a=go]');
      if (!f.elements.title.value.trim()) { err.textContent = 'Give the letter a title.'; err.hidden = false; return; }
      go.disabled = true; err.hidden = true; go.textContent = 'Creating…';
      try {
        const r = await fetch(API + '/letters/new', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: f.elements.type.value, title: f.elements.title.value.trim(), team: params().get('team-id'), project: params().get('project-id') }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        close(); loadSpaces(true);
        location.hash = `#/workspace?team-id=${j.team}&file-id=${j.file}`;
      } catch (x) { err.textContent = x.message; err.hidden = false; go.disabled = false; go.textContent = 'Create'; }
    });
  }

  // ---------- workspace: letters export ----------
  let letterFor = '';
  async function workspace() {
    const fid = params().get('file-id') || '';
    if (fid !== letterFor) {
      letterFor = fid; letter = undefined;
      try { const j = await (await fetch(`${API}/systems?file=${encodeURIComponent(fid)}`)).json(); letter = j.letter || null; exportsFor = j.exports || []; }
      catch (e) { letter = null; }
    }
    document.documentElement.dataset.flynnSpace = letter ? 'letters' : 'designs';
    const ul = document.querySelector('.main_ui_workspace_top_toolbar__toolbar-options');
    if (!ul) return;
    let li = ul.querySelector(':scope > li.ex-li');
    if (letter && !li) {
      li = document.createElement('li');
      li.className = 'main_ui_workspace_top_toolbar__toolbar-option ex-li';
      li.innerHTML = `<button class="${BTN} main_ui_ds_buttons_button__button-ghost fs-export" type="button" title="Export this letter">Export</button>`;
      li.querySelector('button').onclick = exportMenu;
      const mcp = ul.querySelector('[data-tool="mcp"]');
      if (mcp && mcp.closest('li')) ul.insertBefore(li, mcp.closest('li')); else ul.appendChild(li);
    } else if (!letter && li) li.remove();
  }

  window.flynnOpenExport = () => exportMenu();      // the flynn tab's /export command
  function exportMenu() {
    if (document.querySelector('.fr-overlay') || !letter) return;
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay';
    wrap.innerHTML = `
<div class="fr-dialog fs-export-dialog" role="dialog" aria-modal="true" aria-labelledby="fe-title">
  <div class="fr-head"><h2 id="fe-title">Export ${esc(letter.title)}</h2>
    <button class="${ICON_BTN} fr-x" type="button" aria-label="Close"><svg class="fr-ic" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg></button></div>
  <p class="fr-lead">flynn first reads your edits from the canvas back into the letter's page, then renders it with its own pipeline. Follow it in the flynn tab.</p>
  <div class="fs-formats">${exportsFor.map((k) => `<button class="fs-format" type="button" data-f="${k}"><b>${esc((FORMAT[k] || [k])[0])}</b><small>${esc((FORMAT[k] || ['', ''])[1])}</small></button>`).join('')}</div>
</div>`;
    document.body.appendChild(wrap);
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.querySelector('.fr-x').onclick = close;
    wrap.querySelectorAll('.fs-format').forEach((b) => b.onclick = async () => {
      close();
      await fetch(API + '/letter/export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: params().get('file-id'), format: b.dataset.f }) }).catch(() => {});
      if (window.flynnShowChat) window.flynnShowChat();
    });
  }

  // ---------- keep it in place ----------
  let queued = false;
  async function tick() {
    queued = false;
    await loadSpaces();
    if (location.hash.includes('/dashboard')) dashboard();
    else if (location.hash.includes('/workspace')) workspace();
  }
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(tick); } })
    .observe(document.body, { childList: true, subtree: true });
  window.addEventListener('hashchange', tick);
  tick();

  const css = document.createElement('style');
  css.textContent = `
  .fs-spaces { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; padding: 4px; margin: 4px var(--sp-m, 12px) 12px; border-radius: 10px;
               background: var(--color-background-secondary); box-sizing: border-box; }
  html[data-flynn-theme="glass"] body.default .fs-spaces { background: rgba(6,8,11,.5); box-shadow: var(--fg-rim); }
  .fs-spaces button { border: 0; background: transparent; color: var(--color-foreground-secondary); height: 28px; border-radius: 7px; min-width: 0;
               font: 500 11px/1 "worksans", sans-serif; letter-spacing: .06em; text-transform: uppercase; cursor: pointer;
               transition: background-color .2s, color .2s, box-shadow .25s; }
  .fs-spaces button:hover { color: var(--color-foreground-primary); }
  .fs-spaces button.on { color: var(--color-foreground-primary); background: var(--color-background-quaternary); }
  html[data-flynn-theme="glass"] body.default .fs-spaces button.on { background: var(--fg-tint-lit); color: var(--fg-ice); box-shadow: var(--fg-rim-lit); }
  html[data-flynn-space="letters"] .fr-li, html[data-flynn-space="letters"] .fi-li { display: none !important; }
  html[data-flynn-space="letters"] .main_ui_dashboard_projects__dashboard-header-actions [data-testid="new-project-button"] { display: none !important; }
  html[data-flynn-space="letters"] [data-flynn-kind="design"], html[data-flynn-space="designs"] [data-flynn-kind="letter"] { display: none !important; }
  .fs-export { height: 32px; padding: 0 12px; }
  .fs-types { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .fs-type { position: relative; cursor: pointer; }
  .fs-type input { position: absolute; opacity: 0; pointer-events: none; }
  .fs-card { display: grid; grid-template-columns: 24px 1fr; grid-template-rows: auto auto; column-gap: 10px; align-items: center; padding: 12px;
             border-radius: 10px; background: var(--color-background-tertiary); color: var(--color-foreground-primary);
             transition: box-shadow .25s, background-color .2s; }
  .fs-card svg { grid-row: 1 / 3; width: 24px; height: 24px; color: var(--color-foreground-secondary); }
  .fs-card b { font-weight: 500; font-size: 13px; }
  .fs-card small { color: var(--color-foreground-secondary); font-size: 11px; }
  .fs-type input:checked + .fs-card { box-shadow: inset 0 0 0 1px var(--color-accent-primary); }
  .fs-type input:checked + .fs-card svg { color: var(--color-accent-primary); }
  .fs-type input:focus-visible + .fs-card { outline: 1px solid var(--color-accent-primary); outline-offset: 2px; }
  html[data-flynn-theme="glass"] body.default .fs-card { background: rgba(6, 8, 11, .5); box-shadow: var(--fg-rim); }
  html[data-flynn-theme="glass"] body.default .fs-type input:checked + .fs-card { background: var(--fg-tint-lit); box-shadow: var(--fg-rim-lit); }
  .fs-export-dialog { padding-bottom: 24px; }
  .fs-formats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .fs-format { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 12px; border: 0; border-radius: 10px; cursor: pointer;
               text-align: start; background: var(--color-background-tertiary); color: var(--color-foreground-primary); font: 400 12px/1.4 "worksans", sans-serif; }
  .fs-format b { font-weight: 500; font-size: 13px; }
  .fs-format small { color: var(--color-foreground-secondary); }
  .fs-format:hover { box-shadow: inset 0 0 0 1px var(--color-accent-primary); }
  html[data-flynn-theme="glass"] body.default .fs-format { background: rgba(6, 8, 11, .5); box-shadow: var(--fg-rim); }
  html[data-flynn-theme="glass"] body.default .fs-format:hover { box-shadow: var(--fg-rim-lit); }`;
  document.head.appendChild(css);
})();
