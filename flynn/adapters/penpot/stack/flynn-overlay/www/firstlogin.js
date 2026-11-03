// flynn first sign-in: the temporary password must be changed before anything else.
// Asks the flynn server who is signed in (GET /flynn/api/me -> {email, must_change_password}). When the flag is set,
// shows a dialog that cannot be dismissed: current (temporary) password, new password twice, min 12 characters.
// Then it calls Penpot's own update-profile-password and tells the flynn server (POST /flynn/api/me/password-changed).
// Dialog classes (fr-*) come from recorder.css, the same dialog the organizations popups use.
(function () {
  const MIN = 12;
  const BTN = 'main_ui_ds_buttons_button__button';
  let shown = false, checking = false;

  const css = document.createElement('style');
  css.textContent = `
    .ff-meter { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 2px; }
    .ff-meter i { height: 4px; border-radius: 2px; background: rgba(150, 226, 255, .16); }
    .ff-meter[data-s="1"] i:nth-child(-n+1) { background: #ff5a6e; }
    .ff-meter[data-s="2"] i:nth-child(-n+2) { background: #ffb454; }
    .ff-meter[data-s="3"] i:nth-child(-n+3) { background: #8fe3ff; }
    .ff-meter[data-s="4"] i:nth-child(-n+4) { background: #6be3a4; }
    .ff-strength { min-height: 1.4em; color: var(--color-foreground-secondary); font-size: 11px; }
    .ff-show { display: flex; align-items: center; gap: 8px; color: var(--color-foreground-secondary); cursor: pointer; }
    .ff-field-err { color: #ff8a99; font-size: 11px; min-height: 1.2em; }
    .fr-dialog.ff-dialog { padding-bottom: 0; }
    .ff-dialog input[aria-invalid="true"] { outline: 1px solid #ff5a6e; }
    /* A must-read dialog: solid panel and a dark backdrop, so Penpot's welcome questionnaire never shows through. */
    .fr-overlay.ff-overlay { background: rgba(3, 6, 10, .82) !important; }
    .fr-dialog.ff-dialog { background: #0d1218 !important; -webkit-backdrop-filter: none !important; backdrop-filter: none !important; }
    .ff-dialog input[type=password], .ff-dialog input[type=text] { width: 100%; box-sizing: border-box; height: 36px;
      padding: 0 10px; border-radius: 8px; border: 1px solid rgba(150, 226, 255, .18); background: #06080b;
      color: #e8f6ff; font: inherit; font-size: 13px; }
    .ff-dialog input[type=password]:focus, .ff-dialog input[type=text]:focus { outline: none; border-color: #8fe3ff; }
    .ff-show input[type=checkbox] { appearance: auto; -webkit-appearance: checkbox; width: 14px; height: 14px; margin: 0;
      accent-color: #8fe3ff; }`;
  document.head.appendChild(css);

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // Penpot refuses a password without a lowercase letter, an uppercase letter, a digit and a symbol (weak-password).
  function missing(pw) {
    const m = [];
    if (!/[a-z]/.test(pw)) m.push('a lowercase letter');
    if (!/[A-Z]/.test(pw)) m.push('an uppercase letter');
    if (!/\d/.test(pw)) m.push('a digit');
    if (!/[^A-Za-z0-9\s]/.test(pw)) m.push('a symbol');
    return m;
  }
  const listing = (m) => (m.length > 1 ? m.slice(0, -1).join(', ') + ' and ' + m[m.length - 1] : m[0]);

  function strength(pw) {
    if (!pw) return { s: 0, label: '' };
    let pts = 0;
    if (pw.length >= MIN) pts++;
    if (pw.length >= 16) pts++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) pts++;
    if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) pts++;
    if (new Set(pw).size < 5) pts = Math.min(pts, 1);
    if (pw.length < MIN) return { s: 1, label: `Too short: ${pw.length} of ${MIN} characters` };
    const m = missing(pw);
    if (m.length) return { s: 1, label: `Still needs ${listing(m)}` };
    const s = Math.max(1, Math.min(4, pts));
    return { s, label: ['', 'Weak', 'Fair', 'Good', 'Strong'][s] };
  }

  async function getMe() {
    try {
      const r = await fetch('/flynn/api/me', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!r.ok) return null;
      const j = await r.json();
      return j && typeof j === 'object' ? j : null;
    } catch (e) { return null; }
  }

  const MESSAGES = {
    'old-password-not-match': 'The current password is not correct.',
    'email-as-password': 'The new password cannot be your email address.',
    'password-too-short': `The new password must have at least ${MIN} characters.`,
    'weak-password': 'The new password needs an uppercase letter, a lowercase letter, a digit and a symbol.',
  };
  async function changePassword(oldPassword, password) {
    let r;
    try {
      r = await fetch('/api/rpc/command/update-profile-password', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ oldPassword, password }),
      });
    } catch (e) { throw new Error('Cannot reach the server. Check your connection and try again.'); }
    if (r.ok) return;
    let body = {};
    try { body = await r.json(); } catch (e) {}
    const code = body && (body.code || (body.hint && body.hint.code) || '');
    if (MESSAGES[code]) throw new Error(MESSAGES[code]);
    if (r.status === 401 || r.status === 403) throw new Error('Your session has ended. Reload the page and sign in again.');
    throw new Error('The password could not be changed. Try again, or ask your admin.');
  }

  async function tellServer() {
    for (let i = 0; i < 3; i++) {
      try {
        const r = await fetch('/flynn/api/me/password-changed', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (r.ok) return true;
      } catch (e) {}
      await new Promise((res) => setTimeout(res, 600 * (i + 1)));
    }
    return false;
  }

  function show(me) {
    if (shown) return;
    shown = true;
    const wrap = document.createElement('div');
    wrap.className = 'fr-overlay ff-overlay';
    wrap.style.zIndex = '2147483600';
    wrap.innerHTML = `<div class="fr-dialog ff-dialog" role="alertdialog" aria-modal="true" aria-labelledby="ff-title" aria-describedby="ff-lead">
      <div class="fr-head"><h2 id="ff-title">Choose your own password</h2></div>
      <p class="fr-lead" id="ff-lead">${me.email ? `You are signed in as ${esc(me.email)}. ` : ''}Your password was set for you. Replace it with one only you know before you continue.</p>
      <form class="fr-form" novalidate autocomplete="off">
        <label class="fr-field"><span>Current (temporary) password</span>
          <input type="password" name="old" autocomplete="current-password" required></label>
        <label class="fr-field"><span>New password</span>
          <input type="password" name="pw1" autocomplete="new-password" required aria-describedby="ff-strength">
          <div class="ff-meter" data-s="0" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
          <small class="ff-strength" id="ff-strength" aria-live="polite">At least ${MIN} characters, with an uppercase letter, a lowercase letter, a digit and a symbol.</small></label>
        <label class="fr-field"><span>New password again</span>
          <input type="password" name="pw2" autocomplete="new-password" required aria-describedby="ff-match">
          <small class="ff-field-err" id="ff-match" aria-live="polite"></small></label>
        <label class="ff-show"><input type="checkbox" name="show"> Show passwords</label>
        <div class="fr-error" role="alert" hidden></div>
        <div class="fr-actions"><button type="submit" class="${BTN} main_ui_ds_buttons_button__button-primary" data-a="go">Save and continue</button></div>
      </form></div>`;
    document.body.appendChild(wrap);

    const dlg = wrap.querySelector('.fr-dialog');
    const f = wrap.querySelector('form');
    const err = f.querySelector('.fr-error'), go = f.querySelector('[data-a=go]');
    const meter = f.querySelector('.ff-meter'), sTxt = f.querySelector('#ff-strength'), mTxt = f.querySelector('#ff-match');
    const old = f.elements.old, p1 = f.elements.pw1, p2 = f.elements.pw2;

    const fail = (el, msg) => { err.textContent = msg; err.hidden = false; el.setAttribute('aria-invalid', 'true'); el.focus(); };
    const clear = () => { err.hidden = true; [old, p1, p2].forEach((e) => e.removeAttribute('aria-invalid')); };
    const matchHint = () => { mTxt.textContent = p2.value && p1.value !== p2.value ? 'The two new passwords do not match yet.' : ''; };
    p1.addEventListener('input', () => {
      const r = strength(p1.value);
      meter.dataset.s = r.s;
      sTxt.textContent = p1.value ? r.label : `At least ${MIN} characters, with an uppercase letter, a lowercase letter, a digit and a symbol.`;
      matchHint(); clear();
    });
    p2.addEventListener('input', () => { matchHint(); clear(); });
    old.addEventListener('input', clear);
    f.elements.show.addEventListener('change', (e) => { [old, p1, p2].forEach((x) => { x.type = e.target.checked ? 'text' : 'password'; }); });

    // Blocking: no close button, Escape and clicks outside do nothing, Tab stays inside the dialog, and the rest of the
    // app is ignored by keyboard and screen readers while it is open.
    const onKey = (e) => {
      if (!wrap.isConnected) return;
      if (e.key === 'Tab') {
        const items = [...dlg.querySelectorAll('input, button')].filter((x) => !x.disabled);
        const first = items[0], last = items[items.length - 1];
        if (!dlg.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        e.stopPropagation();
      } else if (dlg.contains(e.target)) { e.stopPropagation(); }     // keep Penpot's shortcuts from firing while typing
      else { e.stopPropagation(); e.preventDefault(); }
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('focusin', (e) => { if (wrap.isConnected && !dlg.contains(e.target)) old.focus(); }, true);
    const hidden = [];
    [...document.body.children].forEach((n) => { if (n !== wrap) { hidden.push([n, n.getAttribute('aria-hidden')]); n.setAttribute('aria-hidden', 'true'); } });
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) { e.preventDefault(); old.focus(); } });
    setTimeout(() => old.focus(), 30);

    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      clear();
      if (!old.value) return fail(old, 'Enter your current (temporary) password.');
      if (p1.value.length < MIN) return fail(p1, `The new password must have at least ${MIN} characters.`);
      if (missing(p1.value).length) return fail(p1, `The new password also needs ${listing(missing(p1.value))}.`);
      if (p1.value === old.value) return fail(p1, 'The new password must be different from the temporary one.');
      if (me.email && p1.value.toLowerCase() === String(me.email).toLowerCase()) return fail(p1, 'The new password cannot be your email address.');
      if (p1.value !== p2.value) return fail(p2, 'The two new passwords do not match.');
      go.disabled = true; go.textContent = 'Saving…';
      try {
        await changePassword(old.value, p1.value);
      } catch (x) {
        go.disabled = false; go.textContent = 'Save and continue';
        return fail(/current/.test(x.message) ? old : p1, x.message);
      }
      // The password is changed in Penpot. Only then tell flynn, so a failure here never locks the person out.
      const ok = await tellServer();
      if (!ok) {
        go.disabled = false; go.textContent = 'Try again';
        err.textContent = 'Your password was changed, but flynn could not record it. Select Try again to finish.';
        err.hidden = false;
        // Retry only the recording step from now on.
        f.addEventListener('submit', async (ev) => {
          ev.preventDefault(); ev.stopImmediatePropagation();
          go.disabled = true;
          if (await tellServer()) finish(); else { go.disabled = false; err.hidden = false; }
        }, true);
        return;
      }
      finish();
    });

    function finish() {
      document.removeEventListener('keydown', onKey, true);
      hidden.forEach(([n, v]) => { if (v === null) n.removeAttribute('aria-hidden'); else n.setAttribute('aria-hidden', v); });
      wrap.remove();
      old.value = p1.value = p2.value = '';
    }
  }

  async function check() {
    if (shown || checking) return;
    if (/^#\/auth\//.test(location.hash)) return;              // still on the sign-in screens
    checking = true;
    const me = await getMe();
    checking = false;
    if (me && me.must_change_password) show(me);
  }

  window.addEventListener('hashchange', check);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', check); else check();
  // After sign-in Penpot changes the route without a page load, so the hash check above catches it; this is the backstop.
  setInterval(check, 20000);
})();
