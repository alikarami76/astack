/* Studio on a team server: only loaded there (team.py sdk_tag). Follows Karvansara's session when Studio is open inside
   it: if the person signs out of Karvansara, they are signed out here too. The session's user is only for showing,
   never for trusting; who someone is comes from the server's own sign-in. */
(() => {
  let me = null;
  fetch("/api/me", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).then(j => {
    me = j;
    if (!me || !me.email) return;
    document.documentElement.dataset.team = "1";
    if (me.agent && !me.agent.connected && !sessionStorage.getItem("fs-agent-hint")) {
      try { sessionStorage.setItem("fs-agent-hint", "1"); } catch (e) { /* private mode */ }
      const b = document.createElement("a");
      b.className = "team-hint";
      b.href = "/settings#connect";
      b.textContent = "Connect your computer so Flynn can answer →";
      document.body.appendChild(b);
      setTimeout(() => b.classList.add("gone"), 9000);
    }
  }).catch(() => {});
  window.addEventListener("karvansara:session", ev => {
    const d = ev.detail || {};
    if (d.user === null && me && me.email) {
      fetch("/auth/logout", { method: "POST", credentials: "same-origin" }).finally(() => location.reload());
    }
  });
})();

/* Updates on a team server: a deploy replaces the server for a few minutes.
   (a) An admin's announcement ("Flynn will update in about 3 minutes") shows as a slim banner at the top.
   (b) When Studio's own calls keep failing (network errors, 502/503/504 from the proxy, or the maintenance page's
       X-Flynn-Maintenance header) for more than ~5 s and /healthz agrees, a full overlay replaces the broken UI, polls
       /healthz every 5 s and reloads the page when Studio is back. One failed request never shows the overlay.
   The chat box text is kept in sessionStorage across that reload. */
(() => {
  const POLL_BANNER = 30000, GRACE = 5000, POLL_HEALTH = 5000;
  const DRAFT_KEY = "fs-maint-drafts";
  const rawFetch = window.fetch.bind(window);
  let failTimer = null, overlay = null, healthTimer = null, shownAt = 0;

  const sameOrigin = (input) => {
    try { return new URL(typeof input === "string" ? input : input.url, location.href).origin === location.origin; }
    catch (e) { return false; }
  };
  const isMaint = (r) => r.headers.get("X-Flynn-Maintenance") === "1" ||
    r.status === 502 || r.status === 504 ||
    (r.status === 503 && !/json/.test(r.headers.get("Content-Type") || ""));

  /* ---- drafts: the chat composer's text survives the reload ---- */
  const saveDrafts = () => {
    try {
      const box = document.querySelector(".fc-composer textarea");
      if (box && box.value.trim()) sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ path: location.pathname, text: box.value }));
    } catch (e) { /* private mode */ }
  };
  const restoreDrafts = () => {
    let d = null;
    try { d = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null"); } catch (e) { return; }
    if (!d || d.path !== location.pathname) return;
    const put = () => {
      const box = document.querySelector(".fc-composer textarea");
      if (!box) return false;
      if (!box.value) { box.value = d.text; box.dispatchEvent(new Event("input", { bubbles: true })); }
      try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ }
      return true;
    };
    if (put()) return;
    const mo = new MutationObserver(() => { if (put()) mo.disconnect(); });
    mo.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => mo.disconnect(), 20000);
  };
  restoreDrafts();

  /* ---- styles shared by the banner and the overlay (Studio's glass tokens, with fallbacks) ---- */
  const css = document.createElement("style");
  css.textContent = `
.fm-banner{position:fixed;top:0;left:0;right:0;z-index:2147483000;display:flex;justify-content:center;padding:6px 40px;
  background:var(--g2,#141a21);color:var(--ink,#eef6fb);font:13px/1.4 var(--sans,system-ui,sans-serif);text-align:center;
  box-shadow:inset 0 -1px 0 var(--edge,rgba(150,226,255,.26))}
.fm-banner b{color:var(--light,#8fe3ff);font-weight:600}
.fm-banner button{position:absolute;right:8px;top:2px;background:none;border:0;color:var(--muted,#aab7c4);font-size:18px;line-height:1;padding:4px 8px;cursor:pointer}
.fm-overlay{position:fixed;inset:0;z-index:2147483001;display:grid;place-items:center;padding:24px 16px;
  background:var(--g0,#0b0e13);color:var(--text,#c9d4dd);font:15px/1.55 var(--sans,system-ui,sans-serif)}
.fm-card{width:100%;max-width:460px;text-align:center;background:var(--sheen,none),var(--g1,#10151b);border-radius:14px;padding:34px 24px 24px;
  box-shadow:inset 0 0 0 1px var(--edge,rgba(150,226,255,.26)),var(--lift,0 18px 48px -20px rgba(0,0,0,.75))}
.fm-card img{width:52px;height:52px;border-radius:12px;display:block;margin:0 auto 16px}
.fm-card h2{margin:0 0 10px;font-size:21px;font-weight:600;color:var(--ink,#eef6fb)}
.fm-card p{margin:0 auto;max-width:36ch;color:var(--muted,#aab7c4)}
.fm-card small{display:block;margin-top:20px;font:12.5px var(--mono,ui-monospace,monospace);color:var(--faint,#7d8a97)}`;
  document.head.appendChild(css);

  /* ---- (b) the overlay ---- */
  const health = () => rawFetch("/healthz", { cache: "no-store", credentials: "same-origin", redirect: "manual" })
    .then(r => r.json().then(j => ({ up: r.ok && !!j && !j.maintenance && !isMaint(r) }), () => ({ up: false })), () => ({ up: false }));

  const showOverlay = () => {
    if (overlay) return;
    shownAt = Date.now();
    overlay = document.createElement("div");
    overlay.className = "fm-overlay";
    overlay.setAttribute("role", "status");
    overlay.innerHTML = '<div class="fm-card"><img src="/static/flynn-logo.png" alt="">' +
      "<h2>Flynn is being updated</h2><p>New changes are being installed. This page reconnects by itself in a minute or two; your work is saved.</p>" +
      '<small>reconnecting… <span class="fm-t">0s</span></small></div>';
    document.body.appendChild(overlay);
    const t = overlay.querySelector(".fm-t");
    const tick = setInterval(() => { t.textContent = Math.floor((Date.now() - shownAt) / 1000) + "s"; }, 1000);
    const poll = () => health().then(h => {
      if (!h.up) return;
      clearInterval(tick); clearInterval(healthTimer);
      saveDrafts();
      location.reload();
    });
    healthTimer = setInterval(poll, POLL_HEALTH);
  };

  /* A failure starts a 5 s timer; when it ends, only /healthz failing too (or answering as the maintenance server)
     shows the overlay. Any success in between cancels it. */
  const noteFailure = () => {
    if (failTimer || overlay) return;
    failTimer = setTimeout(() => {
      failTimer = null;
      health().then(h => { if (!h.up) showOverlay(); });
    }, GRACE);
  };
  const noteSuccess = () => { if (failTimer) { clearTimeout(failTimer); failTimer = null; } };

  window.fetch = function (input, init) {
    const mine = sameOrigin(input);
    return rawFetch(input, init).then(r => {
      if (mine) { if (isMaint(r)) noteFailure(); else noteSuccess(); }
      return r;
    }, e => {
      if (mine && !(e && e.name === "AbortError")) noteFailure();
      throw e;
    });
  };

  /* ---- (a) the announcement banner ---- */
  let banner = null, ann = null, annAt = 0, dismissed = "";
  const drawBanner = () => {
    if (!ann || !ann.ends_at || overlay) { if (banner) { banner.remove(); banner = null; document.documentElement.style.paddingTop = ""; } return; }
    const key = String(ann.at);
    if (dismissed === key) return;
    const left = ann.ends_at - (ann.now + (Date.now() - annAt) / 1000);
    let text;
    if (ann.message) text = ann.message;
    else if (left > 90) text = `Flynn will update in about ${Math.round(left / 60)} minutes. Finish what you're typing; nothing will be lost.`;
    else if (left > 30) text = "Flynn will update in about a minute. Finish what you're typing; nothing will be lost.";
    else text = "Flynn is about to update. Finish what you're typing; nothing will be lost.";
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "fm-banner";
      banner.setAttribute("role", "status");
      banner.innerHTML = "<span></span><button type=\"button\" aria-label=\"Dismiss\">×</button>";
      banner.querySelector("button").onclick = () => { dismissed = key; banner.remove(); banner = null; document.documentElement.style.paddingTop = ""; };
      document.body.appendChild(banner);
    }
    banner.firstChild.textContent = text;
    document.documentElement.style.paddingTop = banner.offsetHeight + "px";      /* the page sits below the banner */
  };
  const pollBanner = () => {
    rawFetch("/api/maintenance", { cache: "no-store", credentials: "same-origin", redirect: "manual" }).then(r => {
      if (isMaint(r)) { noteFailure(); return null; }
      noteSuccess();
      return r.ok ? r.json() : null;
    }).then(j => { if (j === null) return; ann = j && j.ends_at ? j : null; annAt = Date.now(); drawBanner(); }).catch(() => noteFailure());
  };
  setInterval(drawBanner, 15000);
  setInterval(pollBanner, POLL_BANNER);
  pollBanner();
})();
