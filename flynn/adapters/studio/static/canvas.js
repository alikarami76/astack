(() => {
  const RUN = decodeURIComponent(location.pathname.split("/")[2]);
  const R = p => `/r/${encodeURIComponent(RUN)}/${p}`;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const stage = $("stage"), world = $("world");
  const label = n => n === "main" ? "A" : n.length <= 2 ? n.toUpperCase() : n;  // page/ is option A
  let run = null, comments = [], shownVersions = new Set(), z = 0.4, tx = 0, ty = 0, fitted = false;

  const EXPORTS = [
    { kind: "pdf", label: "PDF", note: "one page" },
    { kind: "png", label: "PNG", note: "section images" },
    { kind: "html", label: "HTML", note: "zip" },
    { kind: "tsx", label: "TSX", note: "web-app" },
    { kind: "pptx", label: "PPTX", note: "decks, editable" },
    { kind: "eml", label: "Email", note: "newsletters" },
    { kind: "penpot", label: "flynn desk", note: "build as a page" },
    { kind: "figma", label: "Figma", note: "via flynn" },
    { kind: "canva", label: "Canva", note: "via flynn" },
  ];

  function toast(t, err) { const d = document.createElement("div"); d.className = "toast" + (err ? " err" : ""); d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 3200); }
  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" });
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j;
  }

  // ---- canvas transform
  function apply() { world.style.transform = `translate(${tx}px, ${ty}px) scale(${z})`; $("zval").textContent = Math.round(z * 100) + "%"; }
  function zoomAt(nz, cx, cy) {
    nz = Math.min(2, Math.max(0.08, nz));
    const r = stage.getBoundingClientRect(); cx = (cx ?? r.width / 2); cy = (cy ?? r.height / 3);
    tx = cx - (cx - tx) * (nz / z); ty = cy - (cy - ty) * (nz / z); z = nz; apply();
  }
  function fit() {
    const r = stage.getBoundingClientRect(), w = world.scrollWidth || 1;
    z = Math.min(1, Math.max(0.08, (r.width - 40) / w)); tx = 20; ty = 10; apply();
  }
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) { const r = stage.getBoundingClientRect(); zoomAt(z * Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top); }
    else { tx -= e.deltaX; ty -= e.deltaY; apply(); }
  }, { passive: false });
  let drag = null;
  const hint = document.querySelector(".hint");
  stage.addEventListener("mousedown", () => hint && setTimeout(() => hint.classList.add("gone"), 600), { once: true });
  stage.addEventListener("mousedown", e => { if (e.target.closest("button, a, select")) return; drag = { x: e.clientX, y: e.clientY, tx, ty }; stage.classList.add("panning"); });
  addEventListener("mousemove", e => { if (!drag) return; tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; apply(); });
  addEventListener("mouseup", () => { drag = null; stage.classList.remove("panning"); });
  $("zin").onclick = () => zoomAt(z * 1.25); $("zout").onclick = () => zoomAt(z / 1.25);
  $("zfit").onclick = fit; $("z100").onclick = () => zoomAt(1);

  // ---- frames
  function frameHTML(f) {
    if (f.cover) {
      const working = run.cover && run.cover.state === "working";
      return `<div class="frame cover" data-key="cover" data-page="${esc(f.page)}" data-option="" data-version="" data-v="${esc(f.v)}">
      <div class="fhead"><span class="name">Title card</span>
        <span class="actions"><button class="btn ${working ? "" : "ghost"}" data-cover-regen ${working ? "disabled" : ""}>${working ? "Generating…" : "Regenerate"}</button></span></div>
      <div class="paper"><iframe src="${R(f.page)}?thumb=1&v=${encodeURIComponent(f.v)}" loading="lazy" scrolling="no"></iframe><div class="shield"></div></div>
    </div>`;
    }
    const picked = run.decision && run.decision.option === f.option && !f.version;
    const open = comments.filter(c => c.status === "open" && (c.page || "page/index.html") === f.page).length;
    return `<div class="frame ${f.version ? "version" : ""} ${picked ? "picked" : ""}" data-key="${esc(f.key)}" data-page="${esc(f.page)}" data-option="${esc(f.option)}" data-version="${esc(f.version || "")}">
      <div class="fhead"><span class="name">${esc(f.title)}</span>
        ${picked ? '<span class="pill ink">picked</span>' : ""}${open ? `<span class="pill red">${open} open</span>` : ""}
        <span class="actions">
          ${f.version ? "" : `<a class="btn ghost" target="_blank" href="${R(f.page)}" title="Open the page and press C to pin a comment">Comment ↗</a><button class="btn ${picked ? "" : "primary"}" data-pick="${esc(f.option)}">${picked ? "Picked" : "Pick this"}</button>`}
        </span></div>
      <div class="paper"><iframe src="${R(f.page)}?thumb=1" loading="lazy" scrolling="no"></iframe><div class="shield"></div></div>
    </div>`;
  }
  const hasCover = () => !!(run.cover && (run.cover.state === "ready" || run.cover.ready));
  function frames() {
    const fs = hasCover() ? [{ key: "cover", cover: true, option: "", page: "cover/index.html", title: "Title card", v: run.cover.time || "" }] : [];
    fs.push(...run.options.map(o => ({ key: "o:" + o.name, option: o.name, page: o.page, title: run.options.length > 1 ? `Option ${label(o.name)}` : "Current" })));
    for (const v of run.versions) if (shownVersions.has(v.id))
      for (const o of v.options) fs.push({ key: `v:${v.id}:${o}`, option: o, version: v.id, page: `versions/${v.id}/${o}/page/index.html`, title: `${v.label} · ${o}` });
    return fs;
  }
  function renderFrames() {
    const keep = new Map([...world.querySelectorAll(".frame")].map(el => [el.dataset.key, el]));
    const want = frames();
    // Reconcile in place. Taking a frame out of the page and putting it back reloads its iframe (a white flash),
    // and this ran every 4 seconds (2026-10-24), so a loaded frame is never detached: only its header changes, new
    // frames are inserted at their place, and frames that are no longer wanted are removed.
    const wantKeys = new Set(want.map(f => f.key));
    for (const [k, el] of keep) if (!wantKeys.has(k)) el.remove();
    let prev = null;
    for (const f of want) {
      const old = keep.get(f.key);
      const tmp = document.createElement("div"); tmp.innerHTML = frameHTML(f); const el = tmp.firstElementChild;
      let node;
      if (old && old.dataset.v === el.dataset.v) { // keep the loaded iframe, refresh the header only
        old.className = el.className; old.querySelector(".fhead").replaceWith(el.querySelector(".fhead")); node = old;
      } else { if (old) old.replaceWith(el); node = el; }          // new, or a new title card: load it afresh
      const at = prev ? prev.nextSibling : world.firstChild;
      if (node !== at) world.insertBefore(node, at);               // only when the order really changed
      prev = node;
    }
    world.querySelectorAll("iframe").forEach(ifr => {
      if (ifr.dataset.sized) return;
      ifr.addEventListener("load", () => {
        try { const d = ifr.contentDocument; const vw = /width=(\d+)/.exec((d.querySelector('meta[name="viewport"]') || {}).content || ""); if (vw && +vw[1] > 400) ifr.style.width = vw[1] + "px"; ifr.style.height = Math.max(d.documentElement.scrollHeight, 600) + "px"; ifr.dataset.sized = 1; } catch (e) { ifr.style.height = "2400px"; }
        if (!fitted) { fitted = true; setTimeout(fit, 50); }
      });
    });
    world.querySelectorAll("[data-cover-regen]").forEach(b => b.onclick = () => generateCover());
    world.querySelectorAll("[data-pick]").forEach(b => b.onclick = async () => {
      try { await api(`/api/run/${encodeURIComponent(RUN)}/pick`, { option: b.dataset.pick }); toast(`Picked ${b.dataset.pick}`); await load(); } catch (e) { toast(e.message, 1); }
    });
  }

  // ---- title card: POST starts it (202), then poll GET until it is ready or failed; the Tools pane and the cover frame follow run.cover
  let coverTimer = null;
  async function generateCover() {
    try { await api(`/api/run/${encodeURIComponent(RUN)}/cover`, {}); } catch (e) { toast(e.message, 1); return; }
    await load(); watchCover();
  }
  function watchCover() {
    if (coverTimer) return;
    coverTimer = setInterval(async () => {
      let c = null;
      try { c = await api(`/api/run/${encodeURIComponent(RUN)}/cover`); } catch (e) { return; }
      if (c.state === "working") return;
      clearInterval(coverTimer); coverTimer = null;
      await load();
      if (c.state === "error") toast("The title card failed: " + (c.error || ""), 1);
      else toast(c.copy === "fallback" ? "Title card ready (written from the run's facts)" : "Title card ready");
    }, 1500);
  }

  // ---- side panel: the Tools tab (tools.js)
  let tools = null, chatH = null, edits = [], features = {};
  function renderSide() {
    if (!run) return;
    $("runname").textContent = RUN;
    const pp = $("penpot-link");
    if (pp) { pp.hidden = !(run.penpot && run.penpot.link); if (run.penpot && run.penpot.link) pp.href = run.penpot.link; }
    const open = comments.filter(c => c.status === "open").length + (run.edits_pending || 0);
    $("tab-badge").textContent = open ? String(open) : "";
    if (tools) tools.render(run, comments, edits);
  }

  // ---- data, plus live reload of frames when flynn re-renders
  let version = null;
  async function load() {
    run = await api(`/api/run/${encodeURIComponent(RUN)}`);
    comments = (await api(R("__review/api/comments"))).comments;
    try { edits = await api(`/api/run/${encodeURIComponent(RUN)}/edits`); } catch (e) { edits = []; }
    renderFrames(); renderSide();
    if (run.cover && run.cover.state === "working") watchCover();
  }
  // Reload only the frames whose page content changed, and swap in the new page only once it has loaded, so the old
  // one stays on screen meanwhile (no blank frame).
  let pageVersions = null;
  function swapIn(ifr) {
    const paper = ifr.parentElement; if (!paper || ifr.dataset.swapping) return;
    ifr.dataset.swapping = "1";
    const next = ifr.cloneNode(false); delete next.dataset.sized; delete next.dataset.swapping;
    next.removeAttribute("loading"); next.classList.add("incoming");
    next.addEventListener("load", () => {
      try { const d = next.contentDocument; next.style.width = ifr.style.width; next.style.height = Math.max(d.documentElement.scrollHeight, d.body ? d.body.scrollHeight : 0) + "px"; } catch (e) { next.style.height = ifr.style.height; }
      next.dataset.sized = "1"; next.classList.remove("incoming"); ifr.remove();
    }, { once: true });
    paper.insertBefore(next, ifr);
  }
  async function poll() {
    try {
      const r = await api(R("__review/api/version"));
      const pv = r.pages || null;
      if (pv && pageVersions) {
        world.querySelectorAll(".frame:not(.version)").forEach(fr => {
          const pg = fr.dataset.page, ifr = fr.querySelector(".paper iframe:not(.incoming)");
          if (ifr && pg in pv && pageVersions[pg] !== undefined && pv[pg] !== pageVersions[pg]) swapIn(ifr);
        });
      } else if (!pv && version !== null && r.version !== version) {   // an older server: reload, still double-buffered
        world.querySelectorAll(".frame:not(.version) .paper iframe:not(.incoming)").forEach(swapIn);
      }
      pageVersions = pv; version = r.version;
    } catch (e) {}
  }
  // ---- side panel: Chat | Details, resizable, Cmd/Ctrl+J toggles it
  const shell = document.querySelector(".shell"), side = $("side");
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  function tab(name) {
    document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
    $("pane-chat").hidden = name !== "chat"; $("pane-tools").hidden = name !== "tools";
    store.set("studio.tab", name);
  }
  $("tabs").onclick = e => { const b = e.target.closest("[data-tab]"); if (b) tab(b.dataset.tab); };
  // resizable (drag the inner edge), closable (header button), re-openable (Chat button, Cmd/Ctrl+J): glass.js
  const dock = window.FlynnGlass ? FlynnGlass.dock({ type: "canvas", shell, side, edge: "right", def: 400, legacyKey: "studio.sidew" }) : null;

  // ---- what the user is looking at: the frame nearest the middle of the stage
  function frameInView() {
    const r = stage.getBoundingClientRect(), cx = r.left + r.width / 2;
    let best = null, bd = Infinity;
    world.querySelectorAll(".frame").forEach(f => { const b = f.getBoundingClientRect(); const d = Math.abs((b.left + b.right) / 2 - cx); if (d < bd) { bd = d; best = f; } });
    return best;
  }
  function getView() {
    const f = frameInView();
    return { run: RUN, option: f ? (f.dataset.option || null) : null, version: f && f.dataset.version ? f.dataset.version : null, zoom: Math.round(z * 1000) / 1000 };
  }

  // ---- point at it: click an element inside a frame (same origin)
  function cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el.tagName !== "BODY" && el.tagName !== "HTML") {
      let s = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(s + "#" + el.id); break; }
      const sib = [...el.parentNode.children].filter(c => c.tagName === el.tagName);
      if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(el) + 1})`;
      parts.unshift(s); el = el.parentElement;
    }
    return parts.join(" > ");
  }
  // Select mode: click elements in any frame to add them (click again to remove); Done, Enter or Esc finishes.
  // Selected elements keep a lit outline until they're sent or removed.
  const KEEP = ["font-size", "font-weight", "color", "background-color", "padding", "margin", "border-radius", "width", "height", "display", "gap"];
  let marked = [];
  function describe(t, frame) {
    let style = {};
    try { const cs = t.ownerDocument.defaultView.getComputedStyle(t); KEEP.forEach(k => { const v = cs.getPropertyValue(k); if (v && v !== "normal" && v !== "0px" && v !== "none" && v !== "rgba(0, 0, 0, 0)") style[k] = v; }); } catch (e) {}
    const html = (t.outerHTML || "").replace(/\s+/g, " ").slice(0, 1200);
    return { selector: cssPath(t), text: (t.innerText || t.getAttribute("alt") || "").trim().replace(/\s+/g, " ").slice(0, 160),
             page: frame ? frame.dataset.page : "", option: frame ? frame.dataset.option : "", version: frame ? frame.dataset.version : "", html, style };
  }
  function frameOf(doc) { const f = [...world.querySelectorAll(".frame iframe")].find(x => { try { return x.contentDocument === doc; } catch (e) { return false; } }); return f && f.closest(".frame"); }
  function elOf(p) {
    const f = world.querySelector(`.frame[data-page="${CSS.escape(p.page)}"][data-version="${CSS.escape(p.version || "")}"] iframe`);
    try { return f && f.contentDocument.querySelector(p.selector); } catch (e) { return null; }
  }
  function showMarks(list) {
    marked.forEach(el => { el.style.outline = el.dataset.fcOutline || ""; el.style.outlineOffset = ""; delete el.dataset.fcMarked; });
    marked = list.map(elOf).filter(Boolean);
    marked.forEach(el => { if (!el.dataset.fcMarked) el.dataset.fcOutline = el.style.outline; el.dataset.fcMarked = 1; el.style.outline = "4px solid #8fe3ff"; el.style.outlineOffset = "3px"; });
  }
  function startPointing(current, done) {
    if (document.body.classList.contains("pointing")) return;
    let list = (current || []).slice();
    const frames = [...world.querySelectorAll(".frame iframe")];
    const docs = frames.map(f => { try { return f.contentDocument; } catch (e) { return null; } }).filter(Boolean);
    const bar = document.createElement("div"); bar.className = "selbar float";
    const draw = () => { bar.innerHTML = `<span>${list.length ? `<b>${list.length}</b> selected` : "Click elements to select them"}</span><button class="btn sm ghost" data-x="clear" ${list.length ? "" : "disabled"}>Clear</button><button class="btn sm primary" data-x="done">Done</button>`; showMarks(list); };
    let hover = null;
    const unhover = () => { if (hover && !hover.dataset.fcMarked) { hover.style.outline = hover.dataset.fcHover || ""; } hover = null; };
    function hov(e) { unhover(); hover = e.target; if (!hover.dataset.fcMarked) { hover.dataset.fcHover = hover.style.outline; hover.style.outline = "1px dashed #8fe3ff"; } }
    function on(e) {
      e.preventDefault(); e.stopPropagation();
      const t = e.target, sel = cssPath(t), frame = frameOf(t.ownerDocument);
      const page = frame ? frame.dataset.page : "", version = frame ? frame.dataset.version : "";
      const i = list.findIndex(p => p.selector === sel && p.page === page && (p.version || "") === version);
      if (i >= 0) list.splice(i, 1); else if (list.length < 8) list.push(describe(t, frame)); else toast("Up to 8 elements per message", 1);
      hover = null; draw(); done(list.slice());
    }
    function finish() { unhover(); document.body.classList.remove("pointing"); bar.remove(); docs.forEach(d => { d.removeEventListener("click", on, true); d.removeEventListener("mouseover", hov, true); }); removeEventListener("keydown", key); done(list.slice()); }
    function key(k) { if (k.key === "Escape" || k.key === "Enter") finish(); }
    bar.onclick = e => { const x = e.target.closest("[data-x]"); if (!x) return; if (x.dataset.x === "clear") { list = []; draw(); done([]); } else finish(); };
    document.body.classList.add("pointing"); stage.appendChild(bar); draw();
    docs.forEach(d => { d.addEventListener("click", on, true); d.addEventListener("mouseover", hov, true); });
    addEventListener("keydown", key);
  }

  // ---- flynn asks to show something
  function onFocus(ev) {
    if (ev.run && ev.run !== RUN) { location.href = "/run/" + encodeURIComponent(ev.run); return; }
    if (ev.version) { shownVersions.add(ev.version); renderFrames(); renderSide(); }
    const key = ev.version ? `v:${ev.version}:${ev.option || "main"}` : `o:${ev.option || "main"}`;
    const f = world.querySelector(`.frame[data-key="${CSS.escape(key)}"]`);
    if (!f) return;
    const r = stage.getBoundingClientRect(), b = f.getBoundingClientRect();
    tx += r.left + r.width / 2 - (b.left + b.width / 2); ty += r.top + 40 - b.top; apply();
    f.classList.add("focus"); setTimeout(() => f.classList.remove("focus"), 1600);
  }

  async function startChat() {
    let on = true;
    try { const s = await api("/api/settings"); features = s.features; on = s.features.studio_chat !== false; } catch (e) {}
    if (!on) { $("tabs").hidden = true; tab("tools"); }
    else tab(store.get("studio.tab") === "tools" ? "tools" : "chat");
    const noChat = { send() { tab("tools"); toast("Turn the chat on in Settings to hand this to Flynn", 1); }, select() {}, pointed: () => [], setAgent: async () => {} };
    chatH = on ? FlynnChat.mount($("pane-chat"), { key: "run:" + RUN, getView, onFocus, point: startPointing, onSelection: showMarks,
                                                   onAgent: a => tools && tools.setAgent(a) }) : noChat;
    tools = StudioTools.mount($("pane-tools"), { RUN, R, api, toast, label, cssPath, tab, shownVersions, features, generateCover,
      chat: chatH, reload: load, renderFrames: reloadFrames => { if (reloadFrames) world.querySelectorAll(".frame iframe").forEach(f => { f.dataset.sized = ""; f.contentWindow.location.reload(); }); renderFrames(); },
      frames: () => [...world.querySelectorAll(".frame")].map(frame => { let doc = null; try { doc = frame.querySelector("iframe").contentDocument; } catch (e) {} return { frame, doc }; }),
      focus: p => { onFocus({ option: p.option || "main", version: p.version || null }); if (p.selector) { const el = elOf(p); if (el) { el.scrollIntoView({ block: "center" }); const o = el.style.outline; el.style.outline = "4px solid #f3b26b"; setTimeout(() => el.style.outline = o, 1800); } } } });
    renderSide();
  }

  load(); apply(); startChat();
  setInterval(load, 4000); setInterval(poll, 1500);
})();
