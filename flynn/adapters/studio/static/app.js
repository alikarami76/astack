/* App builder workspace (Studio module 8): chat on the left, the app on the right (live preview, code, versions).
   Every Flynn turn in this conversation is committed as a version (chat.py _commit_app). */
(() => {
  const NAME = decodeURIComponent(location.pathname.split("/")[2] || "");
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const A = p => `/api/apps/${encodeURIComponent(NAME)}${p}`;
  function toast(t, err) { const d = document.createElement("div"); d.className = "toast" + (err ? " err" : ""); d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 3200); }
  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" });
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.status); return j;
  }
  $("appname").textContent = NAME; document.title = `Flynn Studio · ${NAME}`;
  let info = null, view = "preview", installing = false;

  // ---------- views
  $("views").onclick = e => { const b = e.target.closest("[data-v]"); if (!b) return; view = b.dataset.v;
    $("views").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    ["preview", "code", "versions"].forEach(v => $("v-" + v).hidden = v !== view);
    $("sizes").hidden = view !== "preview";
    if (view === "code") loadTree(); if (view === "versions") loadVersions(); };
  $("sizes").onclick = e => { const b = e.target.closest("[data-w]"); if (!b) return; $("sizes").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); $("frame-wrap").style.maxWidth = b.dataset.w === "100%" ? "calc(100% - 32px)" : b.dataset.w; };
  // The app runs on its own port, so Studio can't reload the frame from inside it (cross-origin): every reload loads
  // a fresh address instead, and the first load waits until the preview server answers.
  function loadFrame(url) { const f = $("frame"); f.dataset.url = url; f.src = url + (url.includes("?") ? "&" : "?") + "_studio=" + Date.now(); }
  async function whenUp(url, tries = 20) {
    for (let i = 0; i < tries; i++) { try { await fetch(url, { mode: "no-cors", cache: "no-store" }); return true; } catch (e) { await new Promise(r => setTimeout(r, 500)); } }
    return false;
  }
  $("reload").onclick = () => { const u = info && info.preview && info.preview.url; if (u) loadFrame(u); };

  // ---------- state: installed? preview running?
  async function refresh() {
    try { info = await api(A("")); } catch (e) {
      $("empty").hidden = false;
      $("empty").innerHTML = /connect|reach/i.test(e.message)
        ? `<b>Your computer isn't connected.</b><span class="faint">Apps are built and previewed on your own Mac.</span><a class="btn sm" href="/settings#connect">Connect it</a>`
        : `<b>No app called ${esc(NAME)}.</b><a class="btn sm" href="/apps">All apps</a>`;
      return;
    }
    const p = info.preview || {}, inst = info.installed;
    let st = installing ? (await api(A("/install")).catch(() => ({}))) : null;
    if (st && st.state !== "running") { installing = false; if (st.state === "failed") toast("Installing packages failed; the log is in the app's .flynn folder", 1); }
    $("install").hidden = inst || installing;
    $("run").hidden = !inst || p.running;
    $("open").hidden = !p.running; if (p.url) $("open").href = p.url;
    $("ai-on").hidden = !!info.ai;
    $("ai").hidden = !info.ai;
    if (info.ai) { const u = info.ai_usage || {}; $("ai").innerHTML = `<b>AI</b> ${u.calls ? `${u.calls} call${u.calls > 1 ? "s" : ""} · $${(u.cost || 0).toFixed(2)}` : "on"}`; $("ai").title = "AI features through the Claude Agent SDK on your Claude sign-in. Spend shown is this app's total."; }
    const onMac = document.documentElement.dataset.team ? " · on your Mac" : "";
    $("status").innerHTML = installing ? `<span class="fc-disc"></span> Installing packages…` : p.running ? `<span class="live"></span> Live on port ${p.port}${onMac}` : inst ? "Preview stopped" : "Packages not installed";
    const frame = $("frame");
    if (p.running && p.url) {
      if (frame.dataset.url !== p.url && frame.dataset.pending !== p.url) { frame.dataset.pending = p.url; whenUp(p.url).then(() => { loadFrame(p.url); frame.dataset.pending = ""; }); }
      $("empty").hidden = true;
    }
    else {
      $("empty").hidden = false;
      $("empty").innerHTML = installing ? `<span class="fc-disc"></span><b>Installing packages</b><span class="faint">This takes a minute the first time.</span>`
        : !inst ? `<b>Install the app's packages to see it.</b><span class="faint">npm install, once per app.</span><button class="btn primary sm" data-go="install">Install packages</button>`
        : `<b>The preview is stopped.</b><button class="btn primary sm" data-go="run">Start preview</button>`;
    }
  }
  async function install() { try { installing = true; await api(A("/install"), {}); refresh(); } catch (e) { installing = false; toast(e.message, 1); } }
  async function run() { try { await api(A("/preview/start"), {}); setTimeout(refresh, 1200); } catch (e) { toast(e.message, 1); } }
  $("install").onclick = install; $("run").onclick = run;
  $("ai-on").onclick = async () => {
    if (!confirm("Give this app AI features? Flynn adds a small /api/ai endpoint (the Claude Agent SDK on your Claude sign-in) and src/lib/ai.ts, then reinstalls packages.")) return;
    try { await api(A("/ai/enable"), {}); installing = true; toast("AI enabled. Ask Flynn for an AI feature."); refresh(); } catch (e) { toast(e.message, 1); }
  };
  $("empty").addEventListener("click", e => { const b = e.target.closest("[data-go]"); if (b) (b.dataset.go === "install" ? install : run)(); });

  // ---------- code
  async function loadTree() {
    const { files } = await api(A("/files"));
    $("tree").innerHTML = files.map(f => `<button data-p="${esc(f.path)}" style="padding-left:${10 + (f.path.split("/").length - 1) * 12}px">${esc(f.path.split("/").pop())}</button>`).join("");
    $("tree").querySelectorAll("[data-p]").forEach(b => b.onclick = async () => {
      $("tree").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      try { const r = await api(A(`/file?path=${encodeURIComponent(b.dataset.p)}`)); $("src").textContent = r.content; } catch (e) { $("src").textContent = e.message; }
    });
  }
  // ---------- versions
  async function loadVersions() {
    const { versions } = await api(A("/versions"));
    $("versions").innerHTML = versions.map((v, i) => `<div class="app-ver"><span class="num faint">${esc(v.hash.slice(0, 7))}</span><span class="grow" dir="auto">${esc(v.message)}</span><small class="faint">${esc((v.time || "").replace("T", " ").slice(0, 16))}</small>${i ? `<button class="btn ghost sm" data-h="${esc(v.hash)}">Restore</button>` : `<span class="chip lit">Current</span>`}</div>`).join("");
    $("versions").querySelectorAll("[data-h]").forEach(b => b.onclick = async () => {
      if (!confirm("Bring the app back to this version? Your current state stays in the history, so you can return to it.")) return;
      try { await api(A("/restore"), { hash: b.dataset.h }); toast("Restored"); loadVersions(); $("reload").click(); } catch (e) { toast(e.message, 1); }
    });
  }

  // ---------- chat (a coding conversation in the app's folder): resizable, closable, Cmd/Ctrl+J
  if (window.FlynnGlass) FlynnGlass.dock({ type: "app", shell: $("appshell"), side: $("app-side"), edge: "left", def: 420 });
  const chat = FlynnChat.mount($("chat"), { key: `app:${NAME}`, wide: false, getView: () => ({}),
    onRestore: async h => { if (!confirm("Bring the app back to this version? The current state stays in the history.")) return; try { await api(A("/restore"), { hash: h }); toast("Restored"); $("reload").click(); } catch (e) { toast(e.message, 1); } },
    onVersion: () => { $("reload").click(); if (view === "versions") loadVersions(); if (view === "code") loadTree(); } });

  (async () => {
    await refresh();
    if (new URLSearchParams(location.search).get("new") && info) {
      history.replaceState(null, "", location.pathname);
      if (!info.installed) install();
      chat.prefill(`Build the first version: ${info.description}`);
    } else if (info && info.installed && !(info.preview || {}).running) run();
    setInterval(refresh, 3000);
  })();
})();
