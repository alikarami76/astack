// flynn desk organizations (adapters/orgs): flynn's own orgs, not Penpot's paid Enterprise ones.
// Dashboard: an org switcher replaces Penpot's org row ("+ Create org"); the team menu shows only the current org's
// teams; a team created while an org is selected joins it; org settings (Members, Teams, General) and New org.
// The current org follows the open team (team-id in the address), so a reload or a shared link lands in the right org.
// Loaded by inject.js. Popup styles come from recorder.css (fr-*); the switcher's own styles are at the end.
(function () {
  const API = '/flynn/api';
  const SB = 'main_ui_dashboard_sidebar__';
  const BTN = 'main_ui_ds_buttons_button__button';
  const ICON_BTN = 'main_ui_ds_buttons_icon_button__icon-button main_ui_ds_buttons_icon_button__icon-button-ghost';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const params = () => new URLSearchParams((location.hash.split('?')[1]) || '');
  const X = `<svg class="fr-ic" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg>`;
  const CHEV = `<svg class="fo-chev" viewBox="0 0 16 16"><path d="M4.5 6.5 8 10l3.5-3.5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const CHECK = `<svg class="fo-check" viewBox="0 0 16 16"><path d="m3.5 8.2 3 3 6-6.4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  let me = '', data = null, loadedAt = 0, discIcon = '';

  async function whoAmI() {
    if (me) return me;
    try {
      const r = await fetch('/api/rpc/command/get-profile', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: '{}' });
      const p = await r.json();
      me = (p && p.email || '').toLowerCase();
    } catch (e) { me = ''; }
    return me;
  }
  async function load(force) {
    if (data && !force && Date.now() - loadedAt < 8000) return data;
    if (!(await whoAmI())) return data;
    try {
      const r = await fetch(`${API}/orgs?me=${encodeURIComponent(me)}`);
      const j = await r.json();
      if (r.ok) { data = j; loadedAt = Date.now(); }
    } catch (e) { /* keep the last view */ }
    return data;
  }
  async function act(action, body) {
    const r = await fetch(`${API}/orgs`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, action, me }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    await load(true);
    return j;
  }

  const currentTeam = () => params().get('team-id') || '';
  const orgOf = (team) => (data && data.orgs || []).find((o) => o.teams.some((t) => t.id === team)) || null;
  const personal = () => (data && data.teams || []).find((t) => t.personal);
  const canManage = (o) => o && (o.role === 'owner' || o.role === 'admin');
  const goTeam = (team) => { if (team && team !== currentTeam()) location.hash = `#/dashboard/recent?team-id=${team}`; };
  const mark = (o, cls = 'fo-mark') => o && o.logo
    ? `<img class="${cls}" alt="" src="${esc(o.logo)}">`
    : o ? `<span class="${cls} fo-letter" aria-hidden="true">${esc((o.name || '?').trim().charAt(0).toUpperCase())}</span>`
      : `<span class="${cls} fo-disc" aria-hidden="true">${discIcon}</span>`;

  // ---------- the switcher ----------
  function switcher() {
    const box = document.querySelector(`.${SB}organizations-container`);
    if (!box || !data) return;
    const native = box.querySelector(`.${SB}organization-penpot-icon`);
    if (native && !discIcon) discIcon = native.innerHTML;
    document.documentElement.dataset.flynnOrgs = '1';
    const o = orgOf(currentTeam());
    let b = box.querySelector(':scope > .fo-switch');
    if (!b) {
      b = document.createElement('button');
      b.type = 'button'; b.className = 'fo-switch';
      b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
      b.addEventListener('click', (e) => { e.stopPropagation(); menu(b); });
      box.prepend(b);
    }
    const key = (o ? o.id + o.name + (o.logo || '').length : 'none') + discIcon.length;
    if (b.dataset.key !== key) {
      b.dataset.key = key;
      b.innerHTML = `${mark(o)}<span class="fo-name">${esc(o ? o.name : 'No organization')}</span>${CHEV}`;
      b.title = o ? `${o.name}: switch organization` : 'Switch organization';
    }
  }

  function closeMenu() {
    const m = document.querySelector('.fo-menu');
    if (m) m.remove();
    const b = document.querySelector('.fo-switch');
    if (b) b.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', outside, true);
    document.removeEventListener('keydown', menuKey, true);
  }
  const outside = (e) => { if (!e.target.closest('.fo-menu, .fo-switch')) closeMenu(); };
  function menuKey(e) {
    const items = [...document.querySelectorAll('.fo-menu [role=menuitem]')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); document.querySelector('.fo-switch')?.focus(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); items[(i - 1 + items.length) % items.length]?.focus(); }
  }
  function menu(b) {
    if (document.querySelector('.fo-menu')) return closeMenu();
    const cur = orgOf(currentTeam());
    const loose = (data.teams || []);
    const m = document.createElement('ul');
    m.className = 'fo-menu'; m.setAttribute('role', 'menu');
    const row = (attrs, inner, on) => `<li role="menuitem" tabindex="-1" ${attrs} class="fo-item${on ? ' on' : ''}">${inner}${on ? CHECK : ''}</li>`;
    m.innerHTML = `<li role="presentation" class="fo-label">Organizations</li>`
      + (data.orgs.length ? data.orgs.map((o) => row(`data-org="${esc(o.id)}"`,
        `${mark(o, 'fo-mark fo-sm')}<span class="fo-text">${esc(o.name)}</span><span class="fo-role">${esc(o.role)}</span>`, cur && cur.id === o.id)).join('')
        : `<li role="presentation" class="fo-empty">You are not in an organization yet.</li>`)
      + (loose.length ? row('data-org=""', `${mark(null, 'fo-mark fo-sm')}<span class="fo-text">No organization</span><span class="fo-role">${loose.length} team${loose.length > 1 ? 's' : ''}</span>`, !cur) : '')
      + `<hr role="separator" class="fo-sep">`
      + (cur ? row('data-a="settings"', `<span class="fo-text">${canManage(cur) ? 'Organization settings' : 'About this organization'}</span>`) : '')
      + row('data-a="new"', `<span class="fo-text">+ New organization</span>`);
    document.body.appendChild(m);
    const r = b.getBoundingClientRect();
    m.style.left = `${r.left}px`; m.style.top = `${r.bottom + 6}px`; m.style.minWidth = `${Math.max(r.width, 240)}px`;
    b.setAttribute('aria-expanded', 'true');
    m.querySelectorAll('[role=menuitem]').forEach((li) => {
      const pick = () => {
        closeMenu();
        if (li.dataset.a === 'settings') return settings(cur);
        if (li.dataset.a === 'new') return newOrg();
        if (li.dataset.org === '') return goTeam((personal() || loose[0] || {}).id);
        const o = data.orgs.find((x) => x.id === li.dataset.org);
        const t = o && o.teams.find((x) => x.access);
        if (t) goTeam(t.id); else settings(o);         // an org with no team yet: open its settings to add one
      };
      li.addEventListener('click', pick);
      li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    });
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', menuKey, true);
    (m.querySelector('.fo-item.on') || m.querySelector('[role=menuitem]')).focus();
  }

  // ---------- Penpot's team menu: only this org's teams ----------
  const PENDING = 'flynn-org-pending';
  function teamMenu() {
    const dd = document.querySelector(`.${SB}teams-dropdown`);
    if (!dd || !data) return;
    const cur = orgOf(currentTeam());
    const inOrg = new Set(cur ? cur.teams.map((t) => t.id) : []);
    const inAny = new Set(data.orgs.flatMap((o) => o.teams.map((t) => t.id)));
    const per = personal();
    let shown = 0;
    dd.querySelectorAll(`li.${SB}team-dropdown-item[data-value]`).forEach((li) => {
      const id = li.dataset.value;
      const keep = (per && id === per.id) || li.classList.contains(`${SB}team-dropdown-item-no-logo`) || (cur ? inOrg.has(id) : !inAny.has(id));
      li.classList.toggle('fo-hidden', !keep);
      if (keep && !li.classList.contains(`${SB}team-dropdown-item-no-logo`)) shown++;
    });
    const label = dd.querySelector(`.${SB}organization-section-label`);
    if (label) label.textContent = cur ? `${cur.name} teams` : 'Teams outside an organization';
    if (label) label.classList.toggle('fo-hidden', !shown);
    // "Create new team" while an org is open: the new team joins it (picked up when Penpot opens the new team)
    const create = [...dd.querySelectorAll(`li.${SB}action`)].find((li) => /new team/i.test(li.textContent));
    if (create && !create.dataset.fo) {
      create.dataset.fo = '1';
      create.addEventListener('click', () => {
        const o = orgOf(currentTeam());
        try {
          if (o && canManage(o)) sessionStorage.setItem(PENDING, JSON.stringify({ org: o.id, at: Date.now(),
            known: [...data.orgs.flatMap((x) => x.teams.map((t) => t.id)), ...data.teams.map((t) => t.id)] }));
        } catch (e) {}
      }, true);
    }
  }
  async function adoptNewTeam() {
    let p = null;
    try { p = JSON.parse(sessionStorage.getItem(PENDING) || 'null'); } catch (e) {}
    if (!p) return;
    if (Date.now() - p.at > 10 * 60 * 1000) { try { sessionStorage.removeItem(PENDING); } catch (e) {} return; }
    const t = currentTeam();
    if (!t || p.known.includes(t)) return;
    try { sessionStorage.removeItem(PENDING); } catch (e) {}
    try { await act('team.add', { org: p.org, team: t }); switcher(); }
    catch (e) { toast(`The new team was not added to the organization: ${e.message}`); }
  }

  function toast(text) {
    const t = document.createElement('div');
    t.className = 'fo-toast'; t.setAttribute('role', 'status'); t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 6000);
  }

  // ---------- dialogs ----------
  function dialog(title, lead, inner, wide) {
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay';
    wrap.innerHTML = `<div class="fr-dialog${wide ? ' fo-wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="fo-title">
      <div class="fr-head"><h2 id="fo-title">${title}</h2><button class="${ICON_BTN} fr-x" type="button" aria-label="Close">${X}</button></div>
      ${lead ? `<p class="fr-lead">${lead}</p>` : ''}<div class="fo-body">${inner}</div></div>`;
    document.body.appendChild(wrap);
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } else e.stopPropagation(); };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.querySelector('.fr-x').onclick = close;
    return { wrap, close, body: wrap.querySelector('.fo-body') };
  }
  const reportText = (r) => {
    const s = r && r.sync || {};
    const bits = [];
    if ((s.invited || []).length) bits.push(`${s.invited.join(', ')} has no account yet, so an invitation is waiting for them.`);
    if ((s.errors || []).length) bits.push(`Not done: ${s.errors.join('; ')}`);
    if (r && r.sharing) bits.push('Fonts and libraries are being copied into the new team in the background; this takes a minute or two.');
    return bits.join(' ');
  };

  function newOrg() {
    if (document.querySelector('.fr-overlay')) return;
    const teams = (data.teams || []).filter((t) => !t.personal);
    const d = dialog('New organization', 'An organization groups teams. Its owners and admins become admins of every team in it, and its fonts and design system libraries reach every team.', `
      <form class="fr-form" novalidate>
        <label class="fr-field"><span>Name</span><input name="name" dir="auto" required maxlength="60" placeholder="e.g. Marketing"></label>
        ${teams.length ? `<fieldset class="fr-field"><legend>Teams to bring in</legend>
          ${teams.map((t) => `<label class="fr-opt"><input type="checkbox" name="team" value="${esc(t.id)}" ${t.id === currentTeam() ? 'checked' : ''}><span>${esc(t.name)}</span></label>`).join('')}
          <small>You can add or take out teams later. Your personal team stays outside.</small></fieldset>` : ''}
        <div class="fr-error" role="alert" hidden></div>
        <div class="fr-actions"><button type="button" class="${BTN} main_ui_ds_buttons_button__button-secondary" data-a="cancel">Cancel</button>
          <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-primary" data-a="go">Create organization</button></div>
      </form>`);
    const f = d.body.querySelector('form');
    d.body.querySelector('[data-a=cancel]').onclick = d.close;
    setTimeout(() => f.elements.name.focus(), 30);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = f.querySelector('.fr-error'), go = f.querySelector('[data-a=go]');
      const name = f.elements.name.value.trim();
      if (!name) { err.textContent = 'Give the organization a name.'; err.hidden = false; return; }
      const picked = [...f.querySelectorAll('input[name=team]:checked')].map((x) => x.value);
      go.disabled = true; err.hidden = true; go.textContent = 'Creating…';
      try {
        const r = await act('create', { name, teams: picked });
        d.close(); switcher();
        const note = reportText(r);
        if (note) toast(note);
        if (picked.length && !picked.includes(currentTeam())) goTeam(picked[0]);
      } catch (x) { err.textContent = x.message; err.hidden = false; go.disabled = false; go.textContent = 'Create organization'; }
    });
  }

  function settings(org) {
    if (!org || document.querySelector('.fr-overlay')) return;
    const manage = canManage(org);
    const d = dialog(esc(org.name), manage ? '' : 'You are a member of this organization. Its owners and admins manage it.', `
      <div class="fr-seg fo-tabs" role="tablist" aria-label="Organization settings">
        ${['Members', 'Teams', 'General'].map((t, i) => `<label><input type="radio" name="fo-tab" value="${t.toLowerCase()}" ${i === 0 ? 'checked' : ''}><span role="tab">${t}</span></label>`).join('')}
      </div><div class="fo-pane"></div>`, true);
    const pane = d.body.querySelector('.fo-pane');
    let tab = 'members';
    const fresh = () => (data.orgs || []).find((o) => o.id === org.id);
    const paint = () => {
      const o = fresh();
      if (!o) { d.close(); switcher(); return; }
      d.wrap.querySelector('#fo-title').textContent = o.name;
      pane.innerHTML = tab === 'members' ? members(o) : tab === 'teams' ? teamsPane(o) : general(o);
      wire(o);
    };
    const run = async (btn, action, body, after) => {
      const err = pane.querySelector('.fr-error'), note = pane.querySelector('.fo-note');
      if (btn) btn.disabled = true;
      if (err) err.hidden = true;
      try {
        const r = await act(action, { org: org.id, ...body });
        if (after) after(r);
        paint(); switcher();
        const t = reportText(r), n = pane.querySelector('.fo-note');
        if (t && n) { n.textContent = t; n.hidden = false; }
      } catch (x) {
        if (btn) btn.disabled = false;
        const e2 = pane.querySelector('.fr-error');
        if (e2) { e2.textContent = x.message; e2.hidden = false; } else toast(x.message);
      }
      void note;
    };
    const soleOwner = (m, o) => m.role === 'owner' && o.members.filter((x) => x.role === 'owner').length === 1;
    const roleSelect = (m, o) => {
      if (soleOwner(m, o)) return `<span class="fo-role" title="An organization needs at least one owner">owner</span>`;
      const opts = ['owner', 'admin', 'member'].filter((r) => r !== 'owner' || o.role === 'owner' || m.role === 'owner');
      return `<select class="fo-select" data-email="${esc(m.email)}" aria-label="Role of ${esc(m.name)}">${opts.map((r) => `<option ${r === m.role ? 'selected' : ''}>${r}</option>`).join('')}</select>`;
    };
    function members(o) {
      return `<p class="fo-hint">Owners and admins become admins of every team in the organization. Members join teams one by one.</p>
        <ul class="fo-list">${o.members.map((m) => `<li class="fo-row">
          <span class="fo-avatar" aria-hidden="true">${esc((m.name || m.email).charAt(0).toUpperCase())}</span>
          <span class="fo-who"><b dir="auto">${esc(m.name)}${m.email === me ? ' (you)' : ''}</b><small>${esc(m.email)}</small></span>
          ${manage ? roleSelect(m, o) : `<span class="fo-role">${esc(m.role)}</span>`}
          ${manage && !soleOwner(m, o) ? `<button type="button" class="${ICON_BTN} fo-remove" data-email="${esc(m.email)}" aria-label="Remove ${esc(m.name)}">${X}</button>` : manage ? '<span class="fo-gap"></span>' : ''}
        </li>`).join('')}</ul>
        ${manage ? `<form class="fr-form fo-add" novalidate>
          <div class="fo-inline"><label class="fr-field fr-grow"><span>Add a person</span><input name="email" type="email" placeholder="name@example.com" autocomplete="off"></label>
          <label class="fr-field"><span>Role</span><select class="fo-select" name="role"><option>member</option><option>admin</option>${o.role === 'owner' ? '<option>owner</option>' : ''}</select></label>
          <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-secondary fo-addbtn">Add</button></div>
          <small class="fo-hint">They need an account in flynn desk. Someone without one gets an invitation, which waits until they sign up.</small>
        </form>` : ''}
        <div class="fr-error" role="alert" hidden></div><p class="fo-note" role="status" hidden></p>`;
    }
    function teamsPane(o) {
      const loose = (data.teams || []).filter((t) => !t.personal);
      return `<p class="fo-hint">Fonts and design system libraries reach every team here. A team belongs to one organization at a time.</p>
        <ul class="fo-list">${o.teams.length ? o.teams.map((t) => `<li class="fo-row">
          <span class="fo-avatar fo-sq" aria-hidden="true">${esc((t.name || '?').charAt(0).toUpperCase())}</span>
          <span class="fo-who"><b dir="auto">${esc(t.name)}</b>${t.access ? '' : '<small>You are not in this team</small>'}</span>
          ${t.access ? `<button type="button" class="${BTN} main_ui_ds_buttons_button__button-ghost fo-open" data-team="${esc(t.id)}">Open</button>` : ''}
          ${manage ? `<button type="button" class="${BTN} main_ui_ds_buttons_button__button-ghost fo-out" data-team="${esc(t.id)}">Take out</button>` : ''}
        </li>`).join('') : '<li class="fo-empty">No teams yet.</li>'}</ul>
        ${manage ? (loose.length ? `<form class="fr-form fo-add" novalidate><div class="fo-inline">
          <label class="fr-field fr-grow"><span>Bring in a team</span><select class="fo-select" name="team">${loose.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select></label>
          <button type="submit" class="${BTN} main_ui_ds_buttons_button__button-secondary fo-addbtn">Add team</button></div></form>`
          : '<p class="fo-hint">Every team you are in already belongs to an organization. New teams you create while this organization is open join it.</p>') : ''}
        <div class="fr-error" role="alert" hidden></div><p class="fo-note" role="status" hidden></p>`;
    }
    function general(o) {
      return `<form class="fr-form fo-gen" novalidate>
          <div class="fo-inline"><label class="fr-field fr-grow"><span>Name</span><input name="name" dir="auto" maxlength="60" value="${esc(o.name)}" ${manage ? '' : 'disabled'}></label>
          ${manage ? `<button type="submit" class="${BTN} main_ui_ds_buttons_button__button-secondary fo-addbtn">Save</button>` : ''}</div>
          <div class="fr-field"><span>Logo</span><div class="fo-logo">${mark(o, 'fo-mark fo-lg')}
            ${manage ? `<label class="${BTN} main_ui_ds_buttons_button__button-secondary fo-upload">Upload<input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden></label>
            ${o.logo ? `<button type="button" class="${BTN} main_ui_ds_buttons_button__button-ghost fo-nologo">Remove</button>` : ''}` : ''}</div>
            <small class="fo-hint">Square images work best. It is shown at 24 px in the switcher.</small></div>
        </form>
        ${o.role === 'owner' ? `<div class="fo-danger"><div><b>Delete this organization</b><small>Its teams and files stay. Team access that flynn gave through the organization is taken back.</small></div>
          <button type="button" class="${BTN} main_ui_ds_buttons_button__button-secondary fo-delete">Delete…</button></div>` : ''}
        <div class="fr-error" role="alert" hidden></div><p class="fo-note" role="status" hidden></p>`;
    }
    function wire(o) {
      pane.querySelectorAll('select.fo-select[data-email]').forEach((s) => s.onchange = () => run(s, 'member.role', { email: s.dataset.email, role: s.value }));
      pane.querySelectorAll('.fo-remove').forEach((b) => b.onclick = () => run(b, 'member.remove', { email: b.dataset.email }));
      pane.querySelectorAll('.fo-out').forEach((b) => b.onclick = () => run(b, 'team.remove', { team: b.dataset.team }));
      pane.querySelectorAll('.fo-open').forEach((b) => b.onclick = () => { d.close(); goTeam(b.dataset.team); });
      const add = pane.querySelector('.fo-add');
      if (add) add.addEventListener('submit', (e) => {
        e.preventDefault();
        const btn = add.querySelector('.fo-addbtn');
        if (add.elements.email) {
          const email = add.elements.email.value.trim();
          if (!/^[^@\s]+@[^@\s]+$/.test(email)) { const er = pane.querySelector('.fr-error'); er.textContent = 'Type an email address.'; er.hidden = false; return; }
          run(btn, 'member.add', { email, role: add.elements.role.value });
        } else run(btn, 'team.add', { team: add.elements.team.value });
      });
      const gen = pane.querySelector('.fo-gen');
      if (gen) gen.addEventListener('submit', (e) => { e.preventDefault(); run(gen.querySelector('[type=submit]'), 'rename', { name: gen.elements.name.value.trim() }); });
      const up = pane.querySelector('.fo-upload input');
      if (up) up.onchange = async () => {
        const file = up.files && up.files[0];
        if (!file) return;
        try { run(null, 'logo', { logo: await shrink(file) }); } catch (x) { toast(x.message); }
      };
      const nl = pane.querySelector('.fo-nologo');
      if (nl) nl.onclick = () => run(nl, 'logo', { logo: '' });
      const del = pane.querySelector('.fo-delete');
      if (del) del.onclick = () => {
        if (del.dataset.sure) return run(del, 'delete', {}, () => { d.close(); });
        del.dataset.sure = '1'; del.textContent = `Delete ${o.name}`;
        del.classList.add('fo-armed');
      };
    }
    d.body.querySelectorAll('input[name=fo-tab]').forEach((r) => r.onchange = () => { tab = r.value; paint(); });
    paint();
  }

  // A logo is kept small: drawn into a 128 px square (SVG stays as it is when it is small).
  function shrink(file) {
    return new Promise((resolve, reject) => {
      const rd = new FileReader();
      rd.onerror = () => reject(new Error('That file could not be read.'));
      rd.onload = () => {
        if (file.type === 'image/svg+xml' && rd.result.length < 200000) return resolve(rd.result);
        const img = new Image();
        img.onerror = () => reject(new Error('That is not an image flynn desk can read.'));
        img.onload = () => {
          const s = 128, c = document.createElement('canvas');
          c.width = c.height = s;
          const k = Math.max(s / img.width, s / img.height), w = img.width * k, h = img.height * k;
          c.getContext('2d').drawImage(img, (s - w) / 2, (s - h) / 2, w, h);
          resolve(c.toDataURL('image/png'));
        };
        img.src = rd.result;
      };
      rd.readAsDataURL(file);
    });
  }

  // ---------- keep it in place ----------
  let queued = false;
  async function tick() {
    queued = false;
    if (!location.hash.includes('/dashboard')) return;
    await load();
    switcher(); teamMenu(); adoptNewTeam();
  }
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(tick); } })
    .observe(document.body, { childList: true, subtree: true });
  window.addEventListener('hashchange', () => { closeMenu(); tick(); });
  tick();

  const css = document.createElement('style');
  css.textContent = `
  /* Penpot's own org row (with "+ Create org") gives way to flynn's switcher */
  html[data-flynn-orgs] .${SB}selected-organization { display: none !important; }
  .fo-switch { display: flex; align-items: center; gap: 10px; width: 100%; height: 40px; padding: 0 10px; margin: 0; border: 0; border-radius: 10px;
    background: transparent; color: var(--color-foreground-primary); font: 500 14px/1 "worksans", "vazirmatn", sans-serif; cursor: pointer;
    text-align: start; transition: background-color .2s, box-shadow .25s; }
  .fo-switch:hover, .fo-switch[aria-expanded="true"] { background: var(--color-background-tertiary); }
  .fo-switch:focus-visible { outline: 1px solid var(--color-accent-primary); outline-offset: 1px; }
  html[data-flynn-theme="glass"] body.default .fo-switch:hover, html[data-flynn-theme="glass"] body.default .fo-switch[aria-expanded="true"] {
    background: rgba(6,8,11,.5); box-shadow: var(--fg-rim); }
  .fo-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fo-chev { width: 16px; height: 16px; flex: none; color: var(--color-foreground-secondary); }
  .fo-mark { width: 24px; height: 24px; flex: none; border-radius: 6px; object-fit: cover; display: grid; place-items: center; }
  .fo-mark.fo-sm { width: 20px; height: 20px; border-radius: 5px; font-size: 11px; }
  .fo-mark.fo-lg { width: 56px; height: 56px; border-radius: 12px; font-size: 22px; }
  .fo-letter { background: var(--color-background-quaternary); color: var(--color-foreground-primary); font-weight: 600; font-size: 12px; }
  html[data-flynn-theme="glass"] body.default .fo-letter { background: var(--fg-tint-lit); color: var(--fg-ice); box-shadow: var(--fg-rim-lit); }
  .fo-disc svg { width: 100%; height: 100%; }
  .fo-menu { position: fixed; z-index: 2147483000; margin: 0; padding: 6px; list-style: none; border-radius: 10px;
    background: var(--color-background-tertiary); border: 1px solid var(--color-background-quaternary); box-shadow: 0 12px 32px rgba(0,0,0,.35);
    font: 400 13px/1.3 "worksans", "vazirmatn", sans-serif; color: var(--color-foreground-primary); animation: fr-in .16s cubic-bezier(.2,.8,.2,1) both; }
  html[data-flynn-theme="glass"] body.default .fo-menu { border: 0; background: rgba(13,18,24,.92); -webkit-backdrop-filter: blur(18px) saturate(140%);
    backdrop-filter: blur(18px) saturate(140%); box-shadow: inset 0 0 0 1px rgba(150,226,255,.16), 0 18px 48px rgba(0,0,0,.5); }
  .fo-label { padding: 8px 10px 4px; font-size: 11px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; color: var(--color-foreground-secondary); }
  .fo-item { display: flex; align-items: center; gap: 10px; min-height: 32px; padding: 0 10px; border-radius: 7px; cursor: pointer; outline: none; }
  .fo-item:hover, .fo-item:focus-visible { background: var(--color-background-quaternary); }
  html[data-flynn-theme="glass"] body.default .fo-item:hover, html[data-flynn-theme="glass"] body.default .fo-item:focus-visible { background: var(--fg-tint-lit); }
  .fo-text { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fo-role { font-size: 11px; color: var(--color-foreground-secondary); text-transform: capitalize; }
  .fo-check { width: 16px; height: 16px; flex: none; color: var(--color-accent-primary); }
  .fo-sep { border: 0; height: 1px; margin: 6px 4px; background: var(--color-background-quaternary); }
  .fo-empty { padding: 8px 10px; color: var(--color-foreground-secondary); }
  .fo-hidden { display: none !important; }
  .fr-dialog.fo-wide { width: min(640px, calc(100vw - 32px)); padding-bottom: 20px; }
  .fo-tabs { grid-template-columns: repeat(3, 1fr); margin-bottom: 16px; }
  .fo-pane { display: flex; flex-direction: column; gap: 12px; min-height: 330px; }
  .fo-hint { margin: 0; color: var(--color-foreground-secondary); font-size: 12px; }
  small.fo-hint { font-size: 11px; }
  .fo-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
  .fo-row { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 4px 8px; border-radius: 8px; }
  .fo-row:hover { background: var(--color-background-secondary); }
  .fo-avatar { width: 28px; height: 28px; flex: none; border-radius: 50%; display: grid; place-items: center; font-weight: 600; font-size: 12px;
    background: var(--color-background-quaternary); color: var(--color-foreground-primary); }
  .fo-avatar.fo-sq { border-radius: 7px; }
  .fo-who { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .fo-who b { font-weight: 500; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fo-who small { color: var(--color-foreground-secondary); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fo-select { height: 32px; padding: 0 8px; border-radius: 8px; border: 1px solid transparent; font: inherit; font-size: 12px; text-transform: capitalize;
    color: var(--color-foreground-primary); background: var(--color-background-tertiary); cursor: pointer; }
  .fo-select:focus { outline: none; border-color: var(--color-accent-primary); }
  html[data-flynn-theme="glass"] body.default .fo-select { background: rgba(6,8,11,.55); box-shadow: var(--fg-rim); }
  .fo-inline { display: flex; align-items: flex-end; gap: 8px; }
  .fo-pane .fo-inline input, .fo-pane .fo-inline .fo-select, .fo-pane .fo-addbtn { height: 36px; box-sizing: border-box; margin: 0; }
  .fo-gap { width: 32px; flex: none; }
  .fo-add input[type=email] { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 8px; font: inherit; font-size: 13px; outline: none;
    color: var(--color-foreground-primary); background: var(--color-background-tertiary); border: 1px solid var(--color-background-tertiary); }
  .fo-add input[type=email]:focus { border-color: var(--color-accent-primary); }
  html[data-flynn-theme="glass"] body.default .fo-add input[type=email] { background: rgba(6,8,11,.55); border-color: transparent; box-shadow: var(--fg-rim); }
  .fo-addbtn { flex: none; }
  .fo-logo { display: flex; align-items: center; gap: 12px; }
  .fo-upload { cursor: pointer; }
  .fo-actions-row { display: flex; justify-content: flex-end; }
  .fo-danger { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 8px; padding: 12px 14px; border-radius: 10px;
    border: 1px solid var(--color-background-quaternary); }
  .fo-danger div { display: flex; flex-direction: column; gap: 2px; }
  .fo-danger small { color: var(--color-foreground-secondary); font-size: 11px; }
  .fo-armed { color: var(--color-foreground-error, #ff3277) !important; }
  .fo-note { margin: 0; padding: 8px 12px; border-radius: 8px; background: var(--color-background-tertiary); font-size: 12px; }
  .fo-note[hidden] { display: none; }
  .fo-toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 2147483200; max-width: min(520px, calc(100vw - 32px));
    padding: 10px 14px; border-radius: 10px; background: var(--color-background-tertiary); color: var(--color-foreground-primary);
    font: 400 12px/1.5 "worksans", "vazirmatn", sans-serif; box-shadow: 0 12px 32px rgba(0,0,0,.4); animation: fr-in .2s ease-out both; }
  html[data-flynn-theme="glass"] body.default .fo-toast { background: rgba(13,18,24,.92); box-shadow: inset 0 0 0 1px rgba(150,226,255,.16), 0 12px 32px rgba(0,0,0,.5); }
  @media (prefers-reduced-motion: reduce) { .fo-menu, .fo-toast { animation: none; } }`;
  document.head.appendChild(css);
})();
