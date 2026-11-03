/* flynn Studio left rail: one script for every page.
   Put <div id="rail-slot" data-page="home|systems|apps|settings|canvas|app"></div> where the rail goes.
   - home, systems, apps, settings: the rail sits in a grid column beside the page (wrap page + slot in .railshell,
     the page in .railmain). It can collapse to an icon strip (remembered).
   - canvas, app: the rail is a drawer over the page, closed by default; open it from any [data-rail-toggle] button.
     Esc or a click outside closes it. Open/closed is remembered per page type.
   It also fills the Recent list and the footer (health and today's spend) from /api/runs and /api/health. */
(() => {
  const slot = document.getElementById("rail-slot");
  if (!slot) return;
  const page = slot.dataset.page || "home";
  const drawer = page === "canvas" || page === "app";
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const KEY = drawer ? `studio.rail.${page}` : "studio.rail.page";       // "1" = open (drawer) / collapsed (page)
  const ON = { home: "runs", canvas: "runs", systems: "systems", apps: "apps", app: "apps", settings: "settings" }[page] || "runs";

  const ICON = {
    runs: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/></svg>',
    systems: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 2 14 5 8 8 2 5z"/><path d="M2 8l6 3 6-3"/><path d="M2 11l6 3 6-3"/></svg>',
    apps: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 5 2.5 8l3 3M10.5 5l3 3-3 3M9 3.5 7 12.5"/></svg>',
    settings: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M3 4.5h10M3 8h10M3 11.5h10"/><circle cx="6" cy="4.5" r="1.4" fill="#10151b"/><circle cx="10.5" cy="8" r="1.4" fill="#10151b"/><circle cx="7.5" cy="11.5" r="1.4" fill="#10151b"/></svg>',
  };
  const ITEMS = [["runs", "/", "Runs"], ["systems", "/systems", "Design systems"], ["apps", "/apps", "Apps"], ["settings", "/settings", "Settings"]];
  const SIDEBAR = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"><rect x="2" y="3" width="12" height="10" rx="2"/><path d="M6 3v10"/></svg>';

  slot.className = "rail" + (drawer ? " rail-drawer" : " persist");
  slot.setAttribute("role", "navigation"); slot.setAttribute("aria-label", "Studio");
  slot.innerHTML = `
    <div class="rail-head">
      <a class="rail-brand" href="/" title="Flynn Studio home"><img src="/static/flynn-logo.png" width="26" height="26" alt=""><span class="word">Flynn</span><span class="sub">Studio</span></a>
      <button class="rail-collapse" type="button" data-rail-toggle title="${drawer ? "Close sidebar" : "Collapse sidebar"}" aria-label="${drawer ? "Close sidebar" : "Collapse sidebar"}">${SIDEBAR}</button>
    </div>
    <button class="rail-new" id="rail-new" type="button" title="New"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8 3v10M3 8h10"/></svg><span>New</span></button>
    <div class="rail-items" id="rail-items"><i class="rail-pill"></i>
      ${ITEMS.map(([k, href, label]) => `<a class="rail-item${k === ON ? " on" : ""}" data-k="${k}" href="${href}" title="${label}" ${k === ON ? 'aria-current="page"' : ""}>${ICON[k]}<span>${label}</span>${k === "runs" ? '<small id="count"></small>' : ""}</a>`).join("")}
    </div>
    <div class="rail-label">Recent</div>
    <div class="rail-recent" id="rail-recent"></div>
    <a class="rail-foot" href="/settings" id="rail-foot" title="Studio status"><span class="dot"></span><span id="rail-status">Checking…</span></a>`;

  // ---- open / collapsed state
  const shell = slot.closest(".railshell");
  let scrim = null;
  function setDrawer(open, remember = true) {
    slot.classList.toggle("open", open);
    document.documentElement.classList.toggle("rail-open", open);
    if (scrim) scrim.classList.toggle("on", open);
    slot.inert = !open;
    document.querySelectorAll("[data-rail-toggle].outer").forEach(b => b.setAttribute("aria-expanded", String(open)));
    if (remember) store.set(KEY, open ? "1" : "0");
    if (open) { const a = slot.querySelector(".rail-item.on") || slot.querySelector("a"); try { a.focus({ preventScroll: true }); } catch (e) {} }
  }
  function setCollapsed(c, remember = true) {
    if (shell) shell.classList.toggle("rail-collapsed", c);
    slot.classList.toggle("collapsed", c);
    if (remember) store.set(KEY, c ? "1" : "0");
    const b = slot.querySelector(".rail-collapse"); if (b) { b.title = c ? "Expand sidebar" : "Collapse sidebar"; b.setAttribute("aria-label", b.title); }
    requestAnimationFrame(placePill);
  }
  if (drawer) {
    scrim = document.createElement("div"); scrim.className = "rail-scrim"; document.body.appendChild(scrim);
    scrim.addEventListener("click", () => setDrawer(false));
    addEventListener("keydown", e => { if (e.key === "Escape" && slot.classList.contains("open")) { setDrawer(false); const t = document.querySelector("[data-rail-toggle].outer"); if (t) t.focus(); } });
    setDrawer(store.get(KEY) === "1", false);
    slot.querySelectorAll(".rail-item, .rail-brand, .rail-foot").forEach(a => a.addEventListener("click", () => setDrawer(false, false)));
  } else {
    setCollapsed(store.get(KEY) === "1", false);
    slot.querySelector(".rail-collapse").addEventListener("click", () => setCollapsed(!slot.classList.contains("collapsed")));
    if (shell) requestAnimationFrame(() => requestAnimationFrame(() => shell.classList.add("rail-anim")));   // no slide on first paint
  }
  // toggle buttons placed in a page's own top bar (canvas, app): they open the drawer
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-rail-toggle]:not(.rail-collapse)"); if (!b) return;
    e.stopPropagation(); if (drawer) setDrawer(!slot.classList.contains("open")); else setCollapsed(!slot.classList.contains("collapsed"));
  });
  const tagToggles = () => document.querySelectorAll("[data-rail-toggle]:not(.rail-collapse)").forEach(b => { b.classList.add("outer"); b.setAttribute("aria-controls", "rail-slot"); b.setAttribute("aria-expanded", String(slot.classList.contains("open"))); });
  tagToggles(); addEventListener("DOMContentLoaded", tagToggles);
  slot.querySelector(".rail-collapse").addEventListener("click", e => { if (drawer) setDrawer(false); });
  if (drawer) { const c = slot.querySelector(".rail-collapse"); c.title = "Close sidebar"; }

  // ---- the glass pill: glides to the active item (and, across pages, from where the last page left it)
  const items = slot.querySelector("#rail-items"), pill = slot.querySelector(".rail-pill");
  function placePill(instant) {
    const on = items.querySelector(".rail-item.on"); if (!on) { pill.style.opacity = 0; return; }
    pill.style.opacity = 1;
    pill.style.transform = `translateY(${on.offsetTop}px)`; pill.style.height = on.offsetHeight + "px";
  }
  (function initPill() {
    let from = null; try { from = sessionStorage.getItem("studio.rail.from"); } catch (e) {}
    const target = items.querySelector(".rail-item.on"), start = items.querySelector(`.rail-item[data-k="${from}"]`);
    if (start && target && start !== target) {
      pill.style.transition = "none"; pill.style.opacity = 1;
      pill.style.transform = `translateY(${start.offsetTop}px)`; pill.style.height = start.offsetHeight + "px";
      pill.offsetHeight; pill.style.transition = "";
      requestAnimationFrame(() => requestAnimationFrame(placePill));
    } else { pill.style.transition = "none"; placePill(); pill.offsetHeight; pill.style.transition = ""; }
    try { sessionStorage.setItem("studio.rail.from", ON); } catch (e) {}
  })();
  if (window.ResizeObserver) new ResizeObserver(() => placePill()).observe(items);
  addEventListener("pageshow", () => placePill());

  // ---- New: Home focuses its chat (home.js listens); elsewhere it goes to Home's chat
  slot.querySelector("#rail-new").addEventListener("click", () => {
    if (drawer) setDrawer(false, false);
    const ev = new CustomEvent("rail:new", { cancelable: true });
    if (page === "home") { dispatchEvent(ev); return; }
    location.href = "/?new=1";
  });

  // ---- Recent runs and the footer
  const KIND1 = { deck: "Deck", report: "Report", newsletter: "Newsletter", release: "Release note", chart: "Chart", screen: "UI design" };
  const TYPES = { chart: "Report / chart", graphic: "Graphic", deck: "Deck", ui: "UI", "learn-system": "System", sync: "Sync", question: "Question" };
  const SMALL = new Set(["a", "an", "and", "for", "of", "the", "to", "with", "in", "on", "vs"]);
  const UP = { dk: "DK", ui: "UI", ceo: "CEO", pdf: "PDF" };
  const title = r => { const words = (r.id.length > 11 ? r.id.slice(11) : r.id).split("-").filter(Boolean);
    return words.map((w, i) => UP[w] || (w.length <= 3 && !/[aeiou]/.test(w) ? w.toUpperCase() : (i && SMALL.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))).join(" "); };
  const curRun = page === "canvas" ? decodeURIComponent(location.pathname.split("/")[2] || "") : "";
  let lastSig = "";
  async function loadRuns() {
    try {
      const runs = await (await fetch("/api/runs", { cache: "no-store" })).json();
      const sig = JSON.stringify(runs.map(r => [r.id, r.date]));
      const cnt = document.getElementById("count"); if (cnt) cnt.textContent = String(runs.length);
      if (sig === lastSig) return; lastSig = sig;
      document.getElementById("rail-recent").innerHTML = runs.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)) || b.id.localeCompare(a.id)).slice(0, 10)
        .map(r => `<a href="/run/${encodeURIComponent(r.id)}" class="${r.id === curRun ? "cur" : ""}" title="${esc(r.request || "")}">${esc(title(r))}<small>${esc(KIND1[r.kind] || TYPES[r.type] || "Run")} · ${esc(r.date)}</small></a>`).join("");
    } catch (e) {}
  }
  async function loadFoot() {
    const foot = document.getElementById("rail-foot"), st = document.getElementById("rail-status");
    try {
      const h = await (await fetch("/api/health", { cache: "no-store" })).json();
      const fails = h.checks.filter(c => c.status === "fail").length, warns = h.checks.filter(c => c.status === "warn").length;
      let spend = "";
      try { const s = await (await fetch("/api/chat/state?c=home", { cache: "no-store" })).json(); if (s.cost && s.cost.today) spend = ` · today $${s.cost.today.toFixed(2)}`; } catch (e) {}
      foot.classList.remove("ok", "warn", "fail"); foot.classList.add(fails ? "fail" : warns ? "warn" : "ok");
      st.textContent = (fails ? `${fails} setup issue${fails > 1 ? "s" : ""}` : "All systems go") + spend;
      foot.title = st.textContent;
    } catch (e) { st.textContent = "Studio status unknown"; }
  }
  loadRuns(); loadFoot(); setInterval(loadRuns, 8000);
  window.FlynnRail = { reload: loadRuns, open: () => drawer && setDrawer(true), close: () => drawer && setDrawer(false) };
})();
