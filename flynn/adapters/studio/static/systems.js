/* Design systems page (Studio modules 5 and 6): every system Flynn designs from, its health, a detail view, a Flynn
   conversation per system (changes go through approvals), and the recorder for live products. */
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const TIERS = [["verified", "Verified"], ["code-only", "Code only"], ["inferred", "Inferred"], ["proposed", "Proposed"]];
  const slug = id => id.replace(/^rec:/, "rec-").replace(/[^a-z0-9-]/g, "-");
  let systems = [], current = null, chatH = null, dock = null, mode = "watch", features = {};
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

  function toast(t, err) { const d = document.createElement("div"); d.className = "toast" + (err ? " err" : ""); d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 3200); }
  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" });
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.status); return j;
  }
  const sum = o => Object.values(o || {}).reduce((a, b) => a + b, 0);
  const date = t => t ? String(t).slice(0, 10) : "";

  function tierBar(counts) {
    const all = {}; ["component", "token"].forEach(k => Object.entries(counts[k] || {}).forEach(([t, n]) => all[t] = (all[t] || 0) + n));
    const total = sum(all) || 1;
    return `<div class="tierbar" title="${esc(Object.entries(all).map(([t, n]) => `${n} ${t}`).join(" · "))}">${TIERS.filter(([t]) => all[t]).map(([t]) => `<i class="t-${t}" style="width:${(all[t] / total * 100).toFixed(1)}%"></i>`).join("")}</div>
      <div class="tierlegend">${TIERS.filter(([t]) => all[t]).map(([t, l]) => `<span><i class="t-${t}"></i>${l} <b>${all[t]}</b></span>`).join("")}</div>`;
  }
  function card(s) {
    const c = s.counts || {};
    return `<button class="ds-card ${current === s.id ? "on" : ""}" data-id="${esc(s.id)}">
      <div class="ds-card-top"><span class="ds-name">${esc(s.name)}</span><span class="chip">${s.kind === "theme" ? "Theme" : s.kind === "recorded" ? "Recorded" : "Library"}</span></div>
      <div class="ds-nums"><span><b>${sum(c.component)}</b> components${s.page_specific ? ` <span class="faint">+ ${s.page_specific} page-specific</span>` : ""}</span><span><b>${sum(c.token)}</b> tokens</span><span><b>${sum(c.pattern)}</b> patterns</span></div>
      ${tierBar(c)}
      <div class="ds-meta">${s.source ? `<span>${esc(s.source.repo)}@${esc(s.source.commit)}</span>` : ""}${s.updated ? `<span>updated ${esc(date(s.updated))}</span>` : ""}
        ${s.proposals ? `<span class="chip amber">${s.proposals} waiting for review</span>` : ""}${s.recordings ? `<span>${s.recordings} recordings</span>` : ""}</div>
    </button>`;
  }
  async function loadSystems() {
    const rank = s => s.id === "admin-panel" ? 0 : s.kind === "theme" ? 1 : s.kind === "library" ? 2 : 3;
    systems = (await api("/api/systems")).sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
    $("grid").innerHTML = systems.map(card).join("") || `<div class="faint">No design systems yet. Add one, or record a live product.</div>`;
  }

  let tab = "components";
  async function showDetail(id) {
    current = id; store.set("studio.system", id);
    document.querySelectorAll(".ds-card").forEach(b => b.classList.toggle("on", b.dataset.id === id));
    const d = await api(`/api/systems/${encodeURIComponent(id)}`);
    const recs = await api(`/api/recordings?system=${encodeURIComponent(id.replace(/^rec:/, ""))}`).catch(() => []);
    const sec = $("detail"); sec.hidden = false;
    const sc = d.screens || [], fe = d.features || [], fl = d.flows || [];
    const tabs = [["components", `Components ${d.components.filter(c => c.scope !== "page-local").length}`], ["tokens", `Tokens ${d.tokens.length}`],
      ...(sc.length ? [["pages", `Pages ${sc.length}`]] : []), ...(fe.length ? [["features", `Features ${fe.length}`]] : []),
      ...(fl.length ? [["flows", `Flows ${fl.length}`]] : []), ["guide", "Guide"], ["recordings", `Recordings ${recs.length}`]];
    if (!tabs.some(([k]) => k === tab)) tab = "components";
    const body = {
      components: () => {
        if (!d.components.length) return `<div class="faint">No components.</div>`;
        const row = c => `<div class="ds-row"><b>${esc(c.name)}</b><span class="chip t-${esc(c.tier)}">${esc(c.tier)}</span><span class="faint">${esc(c.description)}</span></div>`;
        const lib = d.components.filter(c => c.scope !== "page-local"), local = d.components.filter(c => c.scope === "page-local");
        return `<div class="ds-list">${lib.map(row).join("")}</div>` + (local.length ? `<details class="ds-local"><summary>Page-specific components <span class="faint">${local.length} · each one page's own; reuse the library pieces they are built from</span></summary><div class="ds-list">${local.map(row).join("")}</div></details>` : "");
      },
      tokens: () => d.tokens.length ? `<div class="ds-tokens">${d.tokens.map(t => `<div class="ds-tok"><span class="sw" style="${/^#|^rgb|^hsl/.test(String(t.value)) ? `background:${esc(t.value)}` : ""}"></span><code>${esc(t.name)}</code><span class="faint">${esc(String(t.value ?? "").slice(0, 40))}</span></div>`).join("")}</div>` : `<div class="faint">No tokens.</div>`,
      guide: () => d.guide_excerpt ? `<pre class="fc-pre ds-guide">${esc(d.guide_excerpt)}</pre><div class="faint">${esc(d.guide || "")}</div>` : `<div class="faint">This system has no guide yet. Ask Flynn to write one.</div>`,
      recordings: () => recs.length ? recList(recs) : `<div class="faint">No recordings of this system.</div>`,
      pages: () => pagesTree(sc, fe, fl),
      features: () => `<div class="pm-list">${fe.slice().sort((a, b) => String(a.name).localeCompare(String(b.name))).map(f => `<details class="pm-item">
          <summary><b>${esc(f.name)}</b>${f.name_fa ? `<span dir="rtl" class="faint">${esc(f.name_fa)}</span>` : ""}<span class="chip t-${esc(f.tier)}">${esc(f.tier)}</span></summary>
          <p>${esc(f.description || "")}</p>${list("Rules", f.business_rules)}${list("Pages", f.screens, true)}${list("Touches", f.entities)}${list("APIs", f.apis, false, true)}</details>`).join("")}</div>`,
      flows: () => `<div class="pm-list">${fl.slice().sort((a, b) => String(a.name).localeCompare(String(b.name))).map(f => `<details class="pm-item">
          <summary><b>${esc(f.name)}</b>${f.name_fa ? `<span dir="rtl" class="faint">${esc(f.name_fa)}</span>` : ""}<span class="chip t-${esc(f.tier)}">${esc(f.tier)}</span></summary>
          <p>${esc(f.goal || "")}</p><ol class="pm-steps">${(f.steps || []).map(st => `<li><code>${esc(st.screen || "")}</code> ${esc(st.action || "")}${st.result ? ` <span class="faint">→ ${esc(st.result)}</span>` : ""}</li>`).join("")}</ol>
          ${list("Starts from", f.entry_points)}${list("When it branches or fails", f.edge_cases)}</details>`).join("")}</div>`,
    };
    sec.innerHTML = `<div class="ds-detail-head"><h2>${esc(d.name)}</h2>
        <div class="ds-actions"><button class="btn sm" data-ask="update">Update from ${d.source ? "code" : "its source"}</button><button class="btn sm" data-ask="audit">Check its health</button>${d.proposals ? `<button class="btn sm" data-ask="review">Review ${d.proposals} proposals</button>` : ""}</div></div>
      <div class="seg ds-tabs">${tabs.map(([k, l]) => `<button data-t="${k}" class="${tab === k ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <div class="ds-body">${body[tab]()}</div>`;
    sec.querySelector(".ds-tabs").onclick = e => { const b = e.target.closest("[data-t]"); if (b) { tab = b.dataset.t; showDetail(id); } };
    sec.querySelectorAll("[data-ask]").forEach(b => b.onclick = () => ask(b.dataset.ask, d));
    mountChat(`system:${slug(id)}`);
  }
  // ---- product knowledge: the page hierarchy (by sidebar group, then parent), features and flows
  function list(label, xs, routes, code) {
    xs = Array.isArray(xs) ? xs.filter(Boolean) : xs ? [xs] : [];
    if (!xs.length) return "";
    return `<div class="pm-field"><span class="pm-l">${esc(label)}</span><ul>${xs.map(x => `<li>${routes || code ? `<code>${esc(typeof x === "string" ? x : JSON.stringify(x))}</code>` : esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul></div>`;
  }
  function pagesTree(sc, fe, fl) {
    const byRoute = Object.fromEntries(sc.map(s => [s.route, s])), kids = {};
    sc.forEach(s => { const p = s.parent && byRoute[s.parent] ? s.parent : ""; (kids[p] = kids[p] || []).push(s); });
    const ord = s => String(s.nav_order || "~") + s.route;
    const node = (s, depth) => {
      const feats = fe.filter(f => (f.screens || []).includes(s.route)), flows = fl.filter(f => (f.screens || []).includes(s.route));
      const sh = (s.screenshots || [])[0], shot = sh && (typeof sh === "string" ? sh : sh.src);
      const shotState = sh && typeof sh === "object" ? sh.state : "";
      return `<details class="pm-node" style="--d:${depth}"><summary><span class="pm-name"><b>${esc(s.name || s.route)}</b>${s.name_fa ? ` <span dir="rtl">${esc(s.name_fa)}</span>` : ""}</span><code class="faint">${esc(s.route)}</code><span class="chip t-${esc(s.tier)}">${esc(s.tier)}</span></summary>
        <div class="pm-body">${shot ? `<figure class="pm-fig"><img class="pm-shot" loading="lazy" src="${esc(shot)}" alt=""><figcaption class="faint">${esc({ "with-data": "with data", empty: "empty: the demo account has no data here", locked: "locked for the demo account", "not-eligible": "the demo account isn't eligible", error: "error on the demo account", loading: "still loading", page: "" }[shotState] ?? shotState)}${sh.account ? " · demo account" : ""}</figcaption></figure>` : `<div class="faint">Not pictured yet.</div>`}<p>${esc(s.purpose || "")}</p>${s.audience ? `<p class="faint">${esc(s.audience)}</p>` : ""}
        ${list("Actions", s.key_actions)}${list("Shows", s.data_shown)}${list("States", s.states)}${list("Features", feats.map(f => f.name))}${list("Flows", flows.map(f => f.name))}
        ${list("Components", s.components)}${list("APIs", s.apis, false, true)}${list("Turned on by", s.modules, false, true)}${s.code_path ? `<div class="faint"><code>${esc(s.code_path)}</code></div>` : ""}</div></details>
        ${(kids[s.route] || []).sort((a, b) => ord(a).localeCompare(ord(b))).map(k => node(k, depth + 1)).join("")}`;
    };
    const groups = {};
    (kids[""] || []).forEach(s => { const g = s.nav_group || "Not in the menu"; (groups[g] = groups[g] || []).push(s); });
    const gOrd = g => Math.min(...groups[g].map(s => parseFloat(s.nav_order) || 999));
    return `<div class="pm-tree">${Object.keys(groups).sort((a, b) => gOrd(a) - gOrd(b)).map(g => `<div class="pm-group"><div class="pm-gh" dir="auto">${esc(g)} <span class="faint">${groups[g].length}</span></div>
      ${groups[g].sort((a, b) => ord(a).localeCompare(ord(b))).map(s => node(s, 0)).join("")}</div>`).join("")}</div>`;
  }
  function recList(recs) {
    return `<div class="rec-list">${recs.map(r => `<div class="rec"><div class="rec-top"><b>${esc(r.system)}</b><span class="faint">${esc((r.started || "").replace("T", " ").slice(0, 16))} · ${esc(r.mode || "")}</span>${r.state === "error" ? `<span class="chip red">stopped early</span>` : ""}</div>
      <div class="rec-pages">${(r.pages || []).map(p => `<a class="rec-page" title="${esc(p.url)}">${p.thumb_url ? `<img loading="lazy" src="${esc(p.thumb_url)}" alt="">` : `<span class="faint">no image</span>`}<span dir="auto">${esc(p.title || p.url)}</span></a>`).join("")}</div></div>`).join("")}</div>`;
  }
  function ask(what, d) {
    const name = d ? d.name : "";
    const t = {
      update: `Update the ${name} design system from ${d && d.source ? `${d.source.repo} (it was built from ${d.source.commit}); check what changed since then` : "its source"}. Show me what changed and propose the changes for approval; don't mark anything verified yourself.`,
      audit: `Check the ${name} design system's health: tiers, components without usage rules, tokens that drifted from the product, gaps against recent designs. Give me a short list of what to fix first.`,
      review: `Walk me through the ${d ? d.proposals : ""} pending proposals for ${name}, one at a time, with your recommendation for each.`,
      add: `I want to add a new design system. Ask me where it comes from (a code repo, a live product to record, a Figma library or a description), then build it the flynn way: the store, tiers and a guide, with every change as a proposal for my approval.`,
    }[what];
    if (chatH) chatH.send(t);
  }
  function mountChat(key) {
    if (!features.studio_chat && features.studio_chat !== undefined) return;
    if (chatH && chatH.key === key) return;
    const el = $("chat"); el.innerHTML = ""; el.className = "pane chat";
    chatH = FlynnChat.mount(el, { key, getView: () => ({ system: current }) });
    chatH.key = key;
    if (!dock && window.FlynnGlass && !$("side").hidden) dock = FlynnGlass.dock({ type: "systems", shell: $("shell"), side: $("side"), edge: "right", def: 440 });
  }

  // ---------- recorder
  async function recState() {
    const s = await api("/api/record/state").catch(() => ({ state: "idle" }));
    const bar = $("rec-bar"), live = s.state && !["idle", "done", "error", "stopped"].includes(s.state);
    bar.hidden = !live && s.state !== "error";
    bar.innerHTML = live ? `<span class="fc-disc"></span><span><b>Recording ${esc(s.system || "")}</b> · ${esc(s.mode === "crawl" ? "Flynn is following links" : "browse in Flynn's Chrome window, then Snap")} · ${s.pages || 0} of 3 pages</span><span class="grow"></span>
        ${s.mode !== "crawl" ? `<button class="btn sm" id="rec-snap">Snap this page</button>` : ""}<button class="btn sm danger" id="rec-stop">Stop</button>`
      : s.state === "error" ? `<span class="chip red">The last recording stopped</span><span class="faint">${esc(s.error || "")}</span>` : "";
    const snap = $("rec-snap"), stop = $("rec-stop");
    if (snap) snap.onclick = () => api("/api/record/capture", {}).then(() => toast("Snapped")).catch(e => toast(e.message, 1));
    if (stop) stop.onclick = () => api("/api/record/stop", {}).then(() => { toast("Recording stopped"); loadRecs(); }).catch(e => toast(e.message, 1));
    return live;
  }
  async function loadRecs() {
    const recs = await api("/api/recordings").catch(() => []);
    $("recs").innerHTML = `<h2>Recent recordings</h2>` + (recs.length ? recList(recs.slice(0, 6)) : `<div class="faint">No recordings yet. Record a live product to capture its screens, tokens and components.</div>`);
  }
  $("record-btn").onclick = () => {
    if (features.studio_recorder === false) return toast("The recorder is switched off in Settings", 1);
    const f = $("rec-form"); if (current) f.system.value = current.replace(/^rec:/, ""); $("rec-dialog").showModal();
  };
  $("rec-mode").onclick = e => { const b = e.target.closest("[data-m]"); if (!b) return; mode = b.dataset.m; $("rec-mode").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); };
  $("rec-form").addEventListener("submit", async e => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    const f = e.target;
    try { await api("/api/record/start", { url: f.url.value.trim(), system: f.system.value.trim(), mode, rights: f.rights.checked }); $("rec-dialog").close(); toast("Recording started in Flynn's Chrome window"); recState(); }
    catch (x) { toast(x.message, 1); }
  });

  $("add-btn").onclick = () => { current = null; document.querySelectorAll(".ds-card").forEach(b => b.classList.remove("on")); $("detail").hidden = true; mountChat("system:new"); setTimeout(() => ask("add"), 300); };
  $("grid").onclick = e => { const b = e.target.closest(".ds-card"); if (b) showDetail(b.dataset.id); };

  (async () => {
    let locked = null;
    try { const st = await api("/api/settings"); features = st.features || {}; if (st.systems && !st.systems.editable) locked = st.systems.why; } catch (e) {}
    if (locked) {          // a server: systems are read-only here; they change on the owner's own copy of flynn
      ["record-btn", "add-btn"].forEach(id => $(id).hidden = true);
      $("shell").classList.remove("with-chat"); $("side").hidden = true;
      const n = document.createElement("div"); n.className = "rec-bar"; n.innerHTML = `<span>${esc(locked)}</span>`; $("grid").before(n);
      document.body.classList.add("systems-locked");
    }
    if (features.studio_systems === false) { $("grid").innerHTML = `<div class="faint">The design systems page is switched off in Settings.</div>`; return; }
    if (features.studio_chat === false) { $("shell").classList.remove("with-chat"); $("side").hidden = true; }
    await loadSystems(); loadRecs();
    const want = store.get("studio.system"); const first = systems.find(s => s.id === want) || systems.find(s => s.id === "admin-panel") || systems[0];
    if (first) showDetail(first.id); else mountChat("system:new");
    if (location.hash === "#record") $("record-btn").click();
    let live = await recState();
    setInterval(async () => { const was = live; live = await recState(); if (was && !live) { loadRecs(); loadSystems(); } }, 2000);
  })();
})();
