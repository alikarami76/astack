(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const MODULES = {
    studio_chat: ["Chat", "Talk to Flynn beside the canvas, on Home, in design systems and apps."],
    studio_comments_ai: ["Comments for Flynn", "Ask Flynn about any comment, fix all open ones, @flynn and @quorra in replies."],
    studio_quick_edit: ["Edits on the canvas", "Change text and hide elements on a frame; Flynn carries them into the page."],
    studio_home_v2: ["Home areas", "UI designs, Docs and Other as separate tabs, with doc kinds and systems."],
    studio_systems: ["Design systems", "The systems page: health, detail, and a Flynn conversation per system."],
    studio_recorder: ["Recorder", "Record a live product's screens and design system from the systems page."],
    studio_quorra: ["quorra", "Switch a run's chat to quorra and build Admin Panel designs as merge requests."],
    studio_apps: ["App builder", "Describe an app and Flynn builds it, with a live preview and versions."],
  };
  const NOTES = { quick: "Sonnet, low effort · checker only, no critic", balanced: "Opus, medium effort · one Sonnet critic round",
                  thorough: "Opus, high effort · up to three rounds, Opus final critic", max: "Opus, max effort · Opus composer and critic" };
  let data = null;

  function toast(t, err) { const d = document.createElement("div"); d.className = "toast" + (err ? " err" : ""); d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 3000); }
  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" });
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.status); return j;
  }

  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-copy]"); if (!b) return;
    const code = b.parentElement.querySelector("code").textContent;
    try { await navigator.clipboard.writeText(code); b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy", 1400); } catch (x) { toast("Copy failed; select the command instead", 1); }
  });

  async function health() {
    $("checks").innerHTML = `<div class="faint">Checking…</div>`;
    try {
      const h = await api("/api/health");
      $("checks").innerHTML = h.checks.map(c => `
        <div class="check ${c.status}">
          <span class="dot" aria-label="${c.status}"></span>
          <div class="check-t"><b>${esc(c.label)}</b><span>${esc(c.detail)}</span></div>
          ${c.status !== "ok" && c.fix ? `<div class="cmd small"><code>${esc(c.fix)}</code><button class="btn sm" data-copy>Copy</button></div>` : ""}
        </div>`).join("");
      $("setup").open = h.checks.some(c => c.status === "fail");
      const busy = h.running || [];
      $("busy").hidden = !busy.length;
      $("busy").textContent = busy.length ? `flynn is working in ${busy.length} conversation${busy.length > 1 ? "s" : ""} (${busy.join(", ")}). Don't restart Studio until ${busy.length > 1 ? "they finish" : "it finishes"}.` : "";
    } catch (e) { $("checks").innerHTML = `<div class="check fail"><span class="dot"></span><div class="check-t"><b>Studio didn't answer</b><span>${esc(e.message)}</span></div></div>`; }
  }

  function draw() {
    const s = data.settings;
    document.querySelectorAll("#engine button").forEach(b => b.classList.toggle("on", b.dataset.v === s.engine));
    $("engine-note").textContent = { sdk: "Spending caps stop a message early when it reaches its limit.",
      cli: "With the CLI, flynn can't be stopped at a spending cap mid-message; the cost shows when it ends.",
      api: "Flynn runs with the provider and model below. It has no specialists or shell: it designs and reviews with Studio's tools.",
      off: "The chat is off. Studio works as before, without it." }[s.engine] || "";
    $("efforts").innerHTML = data.efforts.map(x => `
      <label class="effort ${s.effort === x.id ? "on" : ""}">
        <input type="radio" name="default-effort" value="${x.id}" ${s.effort === x.id ? "checked" : ""}>
        <span class="e-name"><b>${esc(x.label)}</b><small>${esc(NOTES[x.id] || "")}</small></span>
        <span class="e-cap"><span class="faint">cap $</span><input class="field num" inputmode="decimal" data-cap="${x.id}" value="${x.cap == null ? "" : x.cap}" placeholder="none"></span>
      </label>`).join("");
    $("modules").innerHTML = Object.entries(data.features).map(([k, on]) => {
      const [name, note] = MODULES[k] || [k, ""];
      return `<label class="module"><span class="m-t"><b>${esc(name)}</b><small>${esc(note)}</small></span>
        <span class="switch"><input type="checkbox" data-f="${esc(k)}" ${on ? "checked" : ""}><span></span></span></label>`;
    }).join("");
    document.querySelectorAll("[data-f]").forEach(cb => cb.onchange = async () => {
      try { data = await api("/api/settings", { features: { [cb.dataset.f]: cb.checked } }); toast(`${(MODULES[cb.dataset.f] || [cb.dataset.f])[0]} ${cb.checked ? "on" : "off"}`); draw(); health(); } catch (e) { toast(e.message, 1); cb.checked = !cb.checked; }
    });
  }

  $("engine").onclick = async e => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    try {
      if (data.team && data.team.on) { prov = await api("/api/models/choice", { engine: b.dataset.v }); drawApi(); }
      else { data = await api("/api/settings", { engine: b.dataset.v }); draw(); await loadApi(); }
      health(); toast("Saved");
    } catch (x) { toast(x.message, 1); }
  };

  // ---- Model API (api_engine.py, through aisuite)
  let prov = null;
  const curEngine = () => data.team && data.team.on ? ((prov && prov.choice.engine) || "agent") : data.settings.engine;
  async function loadApi() { try { prov = await api("/api/models/providers"); } catch (e) { prov = null; } drawApi(); }
  function drawApi() {
    const eng = curEngine();
    document.querySelectorAll("#engine button").forEach(b => b.classList.toggle("on", b.dataset.v === eng));
    if (data.team && data.team.on) $("engine-note").textContent = eng === "api" ? "Flynn runs on the server with your own API key; no agent needed on your computer." : "Flynn runs on your own computer with your Claude account (the flynn Studio agent).";
    $("api-box").hidden = eng !== "api" || !prov;
    if (!prov) return;
    const ch = prov.choice || {}, sel = $("api-provider");
    sel.innerHTML = `<option value="">Choose…</option>` + prov.providers.map(p => `<option value="${esc(p.id)}" ${ch.provider === p.id ? "selected" : ""}>${esc(p.label)}</option>`).join("");
    const p = prov.providers.find(x => x.id === ch.provider);
    $("api-model").value = ch.model || ""; $("api-model").placeholder = p ? p.hint : "model id";
    $("api-base-row").hidden = !(p && p.base_url); $("api-base").value = ch.base_url || "";
    $("api-key-row").hidden = !(p && p.key);
    $("api-key-state").textContent = p && p.has_key ? `saved ${p.key_hint}` : "no key yet";
    $("api-key").value = "";
    $("api-pin").value = (ch.prices || {}).in || ""; $("api-pout").value = (ch.prices || {}).out || "";
    $("api-note").innerHTML = !prov.sdk.ok ? `aisuite isn't installed here: <code>${esc(prov.sdk.fix)}</code>` :
      p && !p.vision ? "This provider doesn't take pictures, so Flynn can't see its renders; it relies on the layout checks and lint." : "";
  }
  async function saveChoice(extra) {
    try { prov = await api("/api/models/choice", { provider: $("api-provider").value, model: $("api-model").value, base_url: $("api-base").value,
      prices: $("api-pin").value || $("api-pout").value ? { in: $("api-pin").value, out: $("api-pout").value } : undefined, ...(extra || {}) }); drawApi(); }
    catch (e) { toast(e.message, 1); }
  }
  $("api-provider").onchange = () => { $("api-model").value = ""; saveChoice(); };
  ["api-model", "api-base", "api-pin", "api-pout"].forEach(id => $(id).addEventListener("change", () => saveChoice()));
  $("api-key-save").onclick = async () => {
    const key = $("api-key").value.trim(); if (!key) return toast("Paste the key first", 1);
    try { prov = await api("/api/models/key", { provider: $("api-provider").value, key }); drawApi(); toast("Key saved"); } catch (e) { toast(e.message, 1); }
  };
  $("api-load").onclick = async () => {
    try { const q = new URLSearchParams({ provider: $("api-provider").value, base_url: $("api-base").value });
      const r = await api(`/api/models/list?${q}`); $("api-models").innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
      toast(`${r.models.length} models`); $("api-model").focus(); } catch (e) { toast(e.message, 1); }
  };
  $("api-test").onclick = async () => {
    $("api-test-state").textContent = "testing…";
    try { const r = await api("/api/models/test", { provider: $("api-provider").value, model: $("api-model").value, base_url: $("api-base").value });
      $("api-test-state").textContent = `works: "${r.reply}" in ${r.seconds}s`; } catch (e) { $("api-test-state").textContent = ""; toast(e.message, 1); }
  };

  // ---- Connect an AI (MCP)
  async function loadMcp() {
    try {
      const r = await api("/api/mcp/tokens");
      $("mcp-list").innerHTML = r.tokens.length ? r.tokens.map(t => `<div class="mcp-item"><b>${esc(t.name)}</b><span class="faint">…${esc(t.hint || "")}</span><span class="grow"></span>
        <span class="faint">${t.last ? "used " + new Date(t.last * 1000).toLocaleDateString() : "not used yet"}</span><button class="btn ghost sm" data-revoke="${esc(t.id)}">Revoke</button></div>`).join("")
        : `<div class="faint">No apps connected yet.</div>`;
      $("mcp-list").querySelectorAll("[data-revoke]").forEach(b => b.onclick = async () => {
        if (!confirm("Revoke this connection? The app stops working with Studio until you connect it again.")) return;
        try { await api("/api/mcp/tokens/revoke", { id: b.dataset.revoke }); loadMcp(); } catch (e) { toast(e.message, 1); }
      });
    } catch (e) { $("mcp-list").innerHTML = `<div class="faint">${esc(e.message)}</div>`; }
  }
  const MCP_CLIENTS = {
    "Claude Code": (u, t) => `claude mcp add --transport http flynn-studio ${u} --header "Authorization: Bearer ${t}"`,
    "Cursor / Windsurf": (u, t) => JSON.stringify({ mcpServers: { "flynn-studio": { url: u, headers: { Authorization: `Bearer ${t}` } } } }, null, 2),
    "Claude Desktop": (u, t) => JSON.stringify({ mcpServers: { "flynn-studio": { command: "npx", args: ["-y", "mcp-remote", u, "--header", `Authorization: Bearer ${t}`] } } }, null, 2),
    "Codex": (u, t) => `# ~/.codex/config.toml\n[mcp_servers.flynn-studio]\nurl = "${u}"\nhttp_headers = { Authorization = "Bearer ${t}" }`,
    "Any app": (u, t) => `MCP address (Streamable HTTP): ${u}\nHeader: Authorization: Bearer ${t}`,
  };
  $("mcp-make").onclick = async () => {
    try {
      const r = await api("/api/mcp/tokens", { name: $("mcp-name").value.trim() || "AI app" });
      const names = Object.keys(MCP_CLIENTS);
      $("mcp-new").hidden = false;
      $("mcp-new").className = "mcp-new";
      $("mcp-new").innerHTML = `<b>Connected "${esc(r.name)}". Copy the setup now: the token isn't shown again.</b>
        <div class="seg" id="mcp-tabs">${names.map((n, i) => `<button data-n="${esc(n)}" class="${i ? "" : "on"}">${esc(n)}</button>`).join("")}</div>
        <div class="cmd"><pre id="mcp-snippet"></pre><button class="btn sm" id="mcp-copy">Copy</button></div>`;
      const show = n => { $("mcp-snippet").textContent = MCP_CLIENTS[n](r.url, r.token); $("mcp-tabs").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.n === n)); };
      $("mcp-tabs").onclick = e => { const b = e.target.closest("[data-n]"); if (b) show(b.dataset.n); };
      $("mcp-copy").onclick = async () => { try { await navigator.clipboard.writeText($("mcp-snippet").textContent); $("mcp-copy").textContent = "Copied"; } catch (e) { toast("Copy failed; select the text", 1); } };
      show(names[0]); $("mcp-name").value = ""; loadMcp();
    } catch (e) { toast(e.message, 1); }
  };
  async function saveEfforts() {
    const caps = {}; document.querySelectorAll("[data-cap]").forEach(i => caps[i.dataset.cap] = i.value.trim());
    const eff = (document.querySelector("#efforts input[type=radio]:checked") || {}).value;
    try { data = await api("/api/settings", { caps, effort: eff }); draw(); toast("Saved"); } catch (x) { toast(x.message, 1); draw(); }
  }
  $("efforts").addEventListener("change", saveEfforts);
  $("recheck").onclick = health;

  // ---- team server: connect your computer; settings are the admin's
  async function team() {
    const t = data.team;
    if (!t || !t.on) return;
    document.body.classList.add("team");
    $("connect").hidden = false;
    $("setup").hidden = true;
    if (!$("engine").dataset.team) { $("engine").dataset.team = "1"; $("engine").innerHTML = `<button data-v="agent">Your computer (Claude)</button><button data-v="api">Model API</button>`; }
    if (prov) drawApi();
    $("team-who").textContent = `Signed in as ${t.name || t.email} (${t.email}) through Karvansara. Your runs, chats and files are yours only.` +
      (t.admin ? " Admin mode is on." : t.owner ? " Open /admin to unlock admin mode." : " The admin sets spending caps and modules.");
    if (!t.admin) document.querySelectorAll("#efforts input, #modules input").forEach(i => i.disabled = true);
    const st = t.agent || {};
    $("agent-state").textContent = st.connected ? "Connected" : st.has_token ? "Not connected right now" : "Not set up yet";
    $("agent-state").className = "chip " + (st.connected ? "t-verified" : "t-inferred");
    if (location.hash === "#connect") $("connect").scrollIntoView({ block: "start" });
  }
  $("agent-make").onclick = async () => {
    try {
      const r = await fetch("/flynn/agent/token", { method: "POST", credentials: "same-origin" });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status);
      $("agent-cmd-row").innerHTML = `<code>${esc(j.command)}</code><button class="btn sm" data-copy>Copy</button>`;
    } catch (e) { toast(e.message, 1); }
  };
  setInterval(async () => { if (!data || !data.team) return; try { data.team = (await api("/api/settings")).team; team(); } catch (e) { /* offline */ } }, 5000);

  (async () => { data = await api("/api/settings"); draw(); team(); await loadApi(); loadMcp(); health(); })();
})();
