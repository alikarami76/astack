// flynn desk settings: "flynn on your computer", on the account settings page (Your account > Profile).
// On the team server flynn's AI runs on each person's own computer through the flynn agent. This section shows
// whether that computer is connected and gives the one-line install command (the same as /flynn/agent/connect).
// It appears only when the server says the AI runs remotely (GET /flynn/agent/status -> remote: true).
(function () {
  const BTN = 'main_ui_ds_buttons_button__button';
  const css = document.createElement('style');
  css.textContent = `
    .fa-card { margin-top: 40px; width: 100%; box-sizing: border-box; padding: 20px; border-radius: 12px;
      background: var(--color-background-tertiary, rgba(255,255,255,.04)); color: var(--color-foreground-primary); }
    .fa-card h2 { margin: 0 0 6px; font-size: 14px; font-weight: 600; }
    .fa-card p { margin: 0 0 12px; color: var(--color-foreground-secondary); font-size: 12px; line-height: 1.5; }
    .fa-st { display: flex; align-items: flex-start; gap: 8px; font-size: 12px; line-height: 1.5; margin-bottom: 12px; }
    .fa-st i { width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--color-foreground-secondary); flex: none; }
    .fa-st.on i { background: #6be3a4; }
    .fa-cmd { margin: 0 0 12px; padding: 10px 12px; border-radius: 8px; background: #06080b; color: #e8f6ff;
      font: 400 11px/1.5 ui-monospace, "SF Mono", Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
      user-select: all; }
    .fa-row { display: flex; gap: 8px; flex-wrap: wrap; }
    .fa-note { margin-top: 10px !important; margin-bottom: 0 !important; }
    .fa-card [hidden] { display: none !important; }      /* Penpot's button style would show hidden buttons */`;
  document.head.appendChild(css);

  let remote = null;       // null: not asked yet; false: this server runs the AI itself (nothing to show)

  async function getStatus() {
    const r = await fetch('/flynn/agent/status', { credentials: 'same-origin' });
    if (!r.ok) return null;
    return r.json();
  }

  function build() {
    const c = document.createElement('section');
    c.className = 'fa-card';
    c.dataset.flynnAgent = '1';
    c.innerHTML = `<h2>flynn on your computer</h2>
      <p>flynn's AI runs on your own computer, with your own Claude account. Install the flynn agent once: paste the
        command into Terminal on your Mac and press Return. It keeps itself up to date and starts when you log in.</p>
      <div class="fa-st" data-st><i></i><span>Checking your computer…</span></div>
      <pre class="fa-cmd" data-cmd hidden></pre>
      <div class="fa-row">
        <button class="${BTN} ${BTN}-primary" data-a="make">Show install command</button>
        <button class="${BTN} ${BTN}-secondary" data-a="copy" hidden>Copy</button>
      </div>
      <p class="fa-note" data-note hidden>The command holds a key for your account only; don't share it. Creating a new
        command turns off the previous one.</p>`;
    const st = c.querySelector('[data-st]'), cmd = c.querySelector('[data-cmd]');
    const make = c.querySelector('[data-a=make]'), copy = c.querySelector('[data-a=copy]');
    const paint = (j) => {
      if (!j) { st.lastElementChild.textContent = 'Could not check your computer'; return; }
      st.classList.toggle('on', !!j.connected);
      st.lastElementChild.textContent = j.connected
        ? `Connected${j.host ? ' from ' + j.host : ''}${j.transport === 'websocket' ? ' · live' : ''}`
        : j.has_token ? 'Installed, but not connected right now. On that computer, run: flynn-agent start'
          : 'Not installed on any computer yet';
    };
    make.onclick = async () => {
      make.disabled = true;
      try {
        const j = await (await fetch('/flynn/agent/token', { method: 'POST', credentials: 'same-origin' })).json();
        if (j.error) { st.lastElementChild.textContent = j.error; return; }
        cmd.textContent = j.command; cmd.hidden = false; copy.hidden = false;
        c.querySelector('[data-note]').hidden = false;
        make.textContent = 'Create a new command';
        make.className = `${BTN} ${BTN}-secondary`; copy.className = `${BTN} ${BTN}-primary`;
      } finally { make.disabled = false; }
    };
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(cmd.textContent); copy.textContent = 'Copied'; }
      catch (e) {
        const r = document.createRange(); r.selectNodeContents(cmd);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r); copy.textContent = 'Press ⌘C';
      }
      setTimeout(() => { copy.textContent = 'Copy'; }, 2000);
    };
    c._paint = paint;
    return c;
  }

  async function place() {
    if (!location.hash.startsWith('#/settings')) return;
    // below the profile form, in the same column
    const host = document.querySelector('[class*="main_ui_settings_profile__form-container"]')
      || document.querySelector('[class*="main_ui_settings_profile__dashboard-settings"]');
    if (!host || host.querySelector('[data-flynn-agent]')) return;
    if (remote === false) return;
    let j = null;
    try { j = await getStatus(); } catch (e) { return; }
    if (!j) return;
    remote = j.remote !== false;
    if (!remote || host.querySelector('[data-flynn-agent]')) return;
    const c = build();
    host.appendChild(c);
    // as wide as the profile form's fields, so the page keeps one column
    const field = host.querySelector('input');
    if (field && field.getBoundingClientRect().width > 200) c.style.maxWidth = `${Math.round(field.getBoundingClientRect().width)}px`;
    c._paint(j);
    const t = setInterval(async () => {
      if (!c.isConnected) { clearInterval(t); return; }
      try { c._paint(await getStatus()); } catch (e) { /* keep the last state */ }
    }, 5000);
  }

  new MutationObserver(() => { place(); }).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('hashchange', place);
  place();
})();
