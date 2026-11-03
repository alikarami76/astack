/* flynn review overlay: pin comments on a page, read flynn's replies, auto-reload on re-render.
   Injected by review.py. Everything lives under #flynn-review so it never collides with the page. */
(() => {
  // API sits next to this script, so the overlay works under review.py (/__review/) and Studio (/r/<run>/__review/)
  const SRC = document.currentScript.src;
  const API = SRC.replace(/overlay\.js(\?.*)?$/, "api/");
  const PAGE = document.currentScript.dataset.page || location.pathname.slice(1);
  if (window.top !== window && new URLSearchParams(location.search).has("thumb")) return;  // Studio thumbnails stay clean
  let comments = [], mode = false, showResolved = false, version = null, openN = null;

  const css = `
  #flynn-review, #flynn-review * { box-sizing: border-box; font-family: "Vazirmatn", system-ui, sans-serif; }
  #flynn-review { direction: ltr; }
  .fr-bar { position: fixed; left: 20px; bottom: 20px; z-index: 2147483000; display: flex; gap: 6px; align-items: center;
    background: #0f1020; color: #fff; border-radius: 14px; padding: 6px; box-shadow: 0 10px 30px rgba(15,16,32,.25); font-size: 13px; }
  .fr-bar button { all: unset; cursor: pointer; padding: 8px 12px; border-radius: 10px; font-weight: 700; color: #fff; }
  .fr-bar button:hover { background: rgba(255,255,255,.08); }
  .fr-bar button.on { background: #0f766e; }
  .fr-bar .fr-count { background: rgba(255,255,255,.12); border-radius: 999px; padding: 2px 8px; margin-left: 4px; font-size: 12px; }
  .fr-brand { padding: 0 8px 0 10px; font-weight: 900; letter-spacing: .2px; opacity: .9; }
  body.fr-commenting, body.fr-commenting * { cursor: crosshair !important; }
  .fr-hover { outline: 2px dashed #0f766e !important; outline-offset: 2px !important; }
  .fr-pin { position: absolute; z-index: 2147482000; width: 28px; height: 28px; margin: -14px 0 0 -14px; border-radius: 50% 50% 50% 4px;
    background: #0f766e; color: #fff; font: 800 13px/28px "Vazirmatn", system-ui; text-align: center; cursor: pointer;
    box-shadow: 0 0 0 3px #fff, 0 6px 16px rgba(15,118,110,.35); transform: rotate(-45deg); }
  .fr-pin span { display: inline-block; transform: rotate(45deg); }
  .fr-pin.resolved { background: #9a9cab; box-shadow: 0 0 0 3px #fff; }
  .fr-pin.draft { background: #0f1020; }
  .fr-pop { position: absolute; z-index: 2147483001; width: 320px; background: #fff; color: #0f1020; border-radius: 14px; padding: 14px;
    box-shadow: 0 20px 50px rgba(15,16,32,.22), 0 0 0 1px #ebebf0; font-size: 14px; line-height: 1.7; }
  .fr-pop .fr-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #6b6e7f; margin-bottom: 6px; }
  .fr-pop .fr-msg { direction: auto; unicode-bidi: plaintext; text-align: start; white-space: pre-wrap; margin: 0 0 8px; }
  .fr-pop .fr-msg b { font-weight: 800; }
  .fr-pop .fr-reply { border-left: 3px solid #ebebf0; padding-left: 10px; margin: 6px 0; }
  .fr-pop .fr-reply.flynn { border-left-color: #0f766e; }
  .fr-pop textarea { width: 100%; min-height: 76px; resize: vertical; border: 1px solid #ebebf0; border-radius: 10px; padding: 8px 10px;
    font: 14px/1.7 "Vazirmatn", system-ui; unicode-bidi: plaintext; outline: none; }
  .fr-pop textarea:focus { border-color: #0f1020; }
  .fr-pop .fr-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; }
  .fr-pop button { all: unset; cursor: pointer; padding: 6px 12px; border-radius: 9px; font-weight: 700; font-size: 13px; background: #f6f6f9; }
  .fr-pop button.primary { background: #0f1020; color: #fff; }
  .fr-pop .fr-x { background: none; padding: 2px 6px; color: #6b6e7f; }
  .fr-panel { position: fixed; left: 20px; bottom: 76px; top: 20px; width: 340px; z-index: 2147483000; background: #fff; border-radius: 16px;
    box-shadow: 0 20px 50px rgba(15,16,32,.2), 0 0 0 1px #ebebf0; display: flex; flex-direction: column; overflow: hidden; }
  .fr-panel header { padding: 14px 16px; border-bottom: 1px solid #ebebf0; font-weight: 800; display: flex; justify-content: space-between; align-items: center; }
  .fr-panel header label { font-weight: 500; font-size: 12px; color: #6b6e7f; display: flex; gap: 6px; align-items: center; }
  .fr-list { overflow: auto; padding: 8px; }
  .fr-item { padding: 10px 12px; border-radius: 12px; cursor: pointer; font-size: 13.5px; line-height: 1.7; }
  .fr-item:hover { background: #f6f6f9; }
  .fr-item .fr-meta { font-size: 11.5px; color: #737584; display: flex; gap: 8px; }
  .fr-item .fr-n { color: #0f766e; font-weight: 900; }
  .fr-item.resolved { opacity: .55; }
  .fr-item .fr-t { unicode-bidi: plaintext; text-align: start; }
  .fr-empty { padding: 24px 16px; color: #6b6e7f; font-size: 13px; line-height: 1.8; }
  .fr-toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: #0f1020; color: #fff; padding: 8px 14px;
    border-radius: 10px; z-index: 2147483002; font-size: 13px; }
  `;

  const root = document.createElement("div");
  root.id = "flynn-review";
  root.innerHTML = `<style>${css}</style>
    <div class="fr-bar">
      <span class="fr-brand">flynn</span>
      <button class="fr-mode" title="Comment (C)">Comment</button>
      <button class="fr-list-btn" title="All comments (L)">Comments <span class="fr-count">0</span></button>
    </div>
    <div class="fr-layer"></div>`;
  document.body.appendChild(root);
  const layer = root.querySelector(".fr-layer");
  const $ = (s, el = root) => el.querySelector(s);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- anchoring: a CSS path to the element plus the click offset inside it (as a fraction of its box)
  function cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      if (el.id) { parts.unshift("#" + CSS.escape(el.id)); break; }
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++;
      parts.unshift(el.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
      el = el.parentElement;
    }
    return (parts[0] && parts[0].startsWith("#") ? "" : "body > ") + parts.join(" > ");
  }
  function anchorFor(el, x, y) {
    const b = el.getBoundingClientRect();
    const shot = el.closest("[data-shot]");
    return { selector: cssPath(el), dx: (x - b.left) / Math.max(b.width, 1), dy: (y - b.top) / Math.max(b.height, 1),
             px: x + scrollX, py: y + scrollY, shot: shot ? shot.dataset.shot : null,
             snippet: (el.innerText || el.getAttribute("alt") || el.tagName).trim().slice(0, 120) };
  }
  function where(a) {
    let el = null;
    try { el = a.selector ? document.querySelector(a.selector) : null; } catch (e) {}
    if (el) { const b = el.getBoundingClientRect(); return { x: b.left + scrollX + a.dx * b.width, y: b.top + scrollY + a.dy * b.height }; }
    return { x: a.px, y: a.py };
  }

  // ---- api
  async function get(p) { const r = await fetch(API + p, { cache: "no-store" }); return r.json(); }
  async function post(p, body) {
    const r = await fetch(API + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    return r.json();
  }
  function toast(t) { const d = document.createElement("div"); d.className = "fr-toast"; d.textContent = t; root.appendChild(d); setTimeout(() => d.remove(), 1800); }

  // ---- rendering
  function render() {
    layer.querySelectorAll(".fr-pin").forEach(p => p.remove());
    const visible = comments.filter(c => (c.page || PAGE) === PAGE && (showResolved || c.status === "open"));
    for (const c of visible) {
      const p = where(c.anchor);
      const pin = document.createElement("div");
      pin.className = "fr-pin" + (c.status === "resolved" ? " resolved" : "");
      pin.style.left = p.x + "px"; pin.style.top = p.y + "px";
      pin.innerHTML = `<span>${c.n}</span>`;
      pin.onclick = e => { e.stopPropagation(); openThread(c.n); };
      layer.appendChild(pin);
    }
    $(".fr-count").textContent = comments.filter(c => c.status === "open").length;
    if ($(".fr-panel")) renderPanel();
    if (openN !== null && $(".fr-pop.thread")) placePop($(".fr-pop.thread"), comments.find(c => c.n === openN));
  }
  function placePop(pop, c) {
    if (!c) return;
    const p = where(c.anchor);
    const left = Math.min(Math.max(p.x + 20, scrollX + 12), scrollX + innerWidth - 336);
    pop.style.left = left + "px"; pop.style.top = (p.y - 10) + "px";
  }
  function closePops() { layer.querySelectorAll(".fr-pop, .fr-pin.draft").forEach(p => p.remove()); openN = null; }

  function openThread(n) {
    closePops();
    const c = comments.find(x => x.n === n); if (!c) return;
    openN = n;
    const pop = document.createElement("div");
    pop.className = "fr-pop thread";
    const replies = (c.replies || []).map(r => `<div class="fr-reply ${r.author === "flynn" ? "flynn" : ""}"><div class="fr-msg"><b>${esc(r.author)}</b> ${esc(r.text)}</div></div>`).join("");
    pop.innerHTML = `<div class="fr-head"><span>#${c.n} · ${esc(c.author)} · ${esc(c.time.replace("T", " ").slice(0, 16))}${c.anchor.shot ? " · " + esc(c.anchor.shot) : ""}</span><button class="fr-x">✕</button></div>
      <div class="fr-msg">${esc(c.text)}</div>${replies}
      <textarea placeholder="Reply…" dir="auto"></textarea>
      <div class="fr-actions"><button class="fr-state">${c.status === "open" ? "Resolve" : "Reopen"}</button><button class="primary fr-send">Reply</button></div>`;
    layer.appendChild(pop); placePop(pop, c);
    $(".fr-x", pop).onclick = closePops;
    $(".fr-state", pop).onclick = async () => { await post("comments/" + c.n, { status: c.status === "open" ? "resolved" : "open" }); await refresh(); closePops(); };
    const send = async () => { const t = $("textarea", pop).value.trim(); if (!t) return; await post("comments/" + c.n, { reply: t }); await refresh(); openThread(c.n); };
    $(".fr-send", pop).onclick = send;
    $("textarea", pop).onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") closePops(); };
  }

  function draft(el, x, y) {
    closePops();
    const a = anchorFor(el, x, y);
    const pin = document.createElement("div");
    pin.className = "fr-pin draft"; pin.style.left = a.px + "px"; pin.style.top = a.py + "px"; pin.innerHTML = "<span>+</span>";
    layer.appendChild(pin);
    const pop = document.createElement("div");
    pop.className = "fr-pop";
    pop.innerHTML = `<div class="fr-head"><span>New comment${a.shot ? " · " + esc(a.shot) : ""}</span><button class="fr-x">✕</button></div>
      <textarea placeholder="What should change here?" dir="auto"></textarea>
      <div class="fr-actions"><button class="fr-cancel">Cancel</button><button class="primary fr-save">Save ⌘↵</button></div>`;
    layer.appendChild(pop);
    pop.style.left = Math.min(Math.max(a.px + 20, scrollX + 12), scrollX + innerWidth - 336) + "px"; pop.style.top = (a.py - 10) + "px";
    const ta = $("textarea", pop); ta.focus();
    const save = async () => {
      const t = ta.value.trim(); if (!t) return;
      try { const c = await post("comments", { text: t, anchor: a, page: PAGE }); await refresh(); closePops(); toast("Saved #" + c.n); }
      catch (e) { toast("Could not save: " + e.message); }
    };
    $(".fr-save", pop).onclick = save;
    $(".fr-cancel", pop).onclick = $(".fr-x", pop).onclick = closePops;
    ta.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") closePops(); };
  }

  function renderPanel() {
    const panel = $(".fr-panel");
    const list = comments.filter(c => (c.page || PAGE) === PAGE && (showResolved || c.status === "open"));
    $(".fr-list", panel).innerHTML = list.length ? list.map(c => `
      <div class="fr-item ${c.status}" data-n="${c.n}">
        <div class="fr-meta"><span class="fr-n">#${c.n}</span><span>${esc(c.anchor.shot || "")}</span><span>${(c.replies || []).length ? (c.replies.length + " repl" + (c.replies.length > 1 ? "ies" : "y")) : ""}</span><span>${c.status === "resolved" ? "resolved" : ""}</span></div>
        <div class="fr-t">${esc(c.text)}</div></div>`).join("")
      : `<div class="fr-empty">No comments yet. Press <b>C</b> or <b>Comment</b>, then click anything on the page.</div>`;
    panel.querySelectorAll(".fr-item").forEach(it => it.onclick = () => {
      const c = comments.find(x => x.n === +it.dataset.n); const p = where(c.anchor);
      scrollTo({ top: p.y - innerHeight / 3, behavior: "smooth" }); setTimeout(() => openThread(c.n), 350);
    });
  }
  function togglePanel(force) {
    const on = force ?? !$(".fr-panel");
    if (!on) { $(".fr-panel") && $(".fr-panel").remove(); $(".fr-list-btn").classList.remove("on"); return; }
    const panel = document.createElement("div");
    panel.className = "fr-panel";
    panel.innerHTML = `<header><span>Comments</span><label><input type="checkbox" class="fr-res" ${showResolved ? "checked" : ""}> show resolved</label></header><div class="fr-list"></div>`;
    root.appendChild(panel); $(".fr-list-btn").classList.add("on");
    $(".fr-res", panel).onchange = e => { showResolved = e.target.checked; render(); };
    renderPanel();
  }
  function setMode(on) { mode = on; document.body.classList.toggle("fr-commenting", on); $(".fr-mode").classList.toggle("on", on); if (!on) clearHover(); }

  // ---- events
  let hovered = null;
  function clearHover() { if (hovered) hovered.classList.remove("fr-hover"); hovered = null; }
  document.addEventListener("mousemove", e => {
    if (!mode || root.contains(e.target)) return clearHover();
    if (hovered !== e.target) { clearHover(); hovered = e.target; hovered.classList.add("fr-hover"); }
  }, true);
  document.addEventListener("click", e => {
    if (root.contains(e.target)) return;
    if (mode) { e.preventDefault(); e.stopPropagation(); const el = e.target; clearHover(); setMode(false); draft(el, e.clientX, e.clientY); }
    else if ($(".fr-pop")) closePops();
  }, true);
  document.addEventListener("keydown", e => {
    if (/input|textarea/i.test(e.target.tagName)) return;
    if (e.key === "c" || e.key === "C") setMode(!mode);
    else if (e.key === "l" || e.key === "L") togglePanel();
    else if (e.key === "Escape") { setMode(false); closePops(); }
  });
  $(".fr-mode").onclick = () => setMode(!mode);
  $(".fr-list-btn").onclick = () => togglePanel();
  addEventListener("resize", render);

  // ---- live data: comments every 2s (flynn's replies appear), page version every 1s (reload on re-render)
  async function refresh() { try { comments = (await get("comments")).comments; render(); } catch (e) {} }
  async function checkVersion() {
    try {
      const v = (await get("version")).version;
      if (version === null) version = v;
      else if (v !== version) {
        sessionStorage.setItem("fr-scroll", String(scrollY));
        location.reload();
      }
    } catch (e) {}
  }
  const saved = sessionStorage.getItem("fr-scroll");
  if (saved) { sessionStorage.removeItem("fr-scroll"); addEventListener("load", () => scrollTo(0, +saved)); }
  refresh(); checkVersion();
  setInterval(refresh, 2000); setInterval(checkVersion, 1000);
  addEventListener("load", render);
  document.fonts && document.fonts.ready.then(render);
})();
