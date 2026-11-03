/* flynn Studio chat panel (Module 1). One component for the run canvas and Home:
     FlynnChat.mount(el, { key, getView, onFocus, point, wide }) -> { setView(v), pointed(p), focus() }
   key: "home" or "run:<id>". getView(): what the user is looking at. onFocus(ev): open a run/option. point(done): let the user
   click an element on the canvas; done({selector, text, page, option, version}).
   Live connection: one WebSocket (/api/chat/ws), falling back to server-sent events after two failed opens; events
   carry their index, so a reconnect resumes after the last one seen and never shows a message twice (flynn desk's
   approach, panel.html). Markdown and the question card are ported from the desk. */
(() => {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const el = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const money = v => v == null ? "" : v > 0 && v < 0.01 ? "<$0.01" : "$" + v.toFixed(2);

  // ---- markdown (flynn desk panel.html md()/inline(), with Persian runs isolated inside English lines)
  const RTL_RUN = /«?[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF](?:[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF\u200c\u06F0-\u06F90-9\s,.\u066B\u066C:/()%\u2013-]*[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF\u06F0-\u06F9»])?»?|[\u06F0-\u06F9][\u06F0-\u06F9,.\u066B\u066C]*(?:\s+[\u0600-\u06FF\u200c]+)*/g;
  function inline(src) {
    let h = esc(src);
    const ltr = !/^[^A-Za-z]*[\u0600-\u06FF]/.test(src);
    if (ltr) h = h.replace(RTL_RUN, m => /[\u0600-\u06FF\u06F0-\u06F9]/.test(m) ? `<bdi dir="rtl"${m.length <= 40 ? ' class="nw"' : ""}>${m}</bdi>` : m);
    h = h.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s.,;:)!?]|$)/g, "$1<i>$2</i>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    return h.replace(/\bruns\/(\d{4}-\d{2}-\d{2}-[a-z0-9-]+)/g, '<a class="runlink" href="/run/$1">runs/$1</a>');
  }
  function md(text) {
    const lines = String(text).replace(/\r/g, "").trim().split("\n");
    const out = []; let para = [], list = null, code = null;
    const flushPara = () => { if (para.length) { out.push('<p dir="auto">' + para.map(inline).join("<br>") + "</p>"); para = []; } };
    const flushList = () => { if (list) { out.push(`<${list.tag} dir="auto">` + list.items.map(i => `<li dir="auto">${inline(i)}</li>`).join("") + `</${list.tag}>`); list = null; } };
    for (const raw of lines) {
      if (code) { if (/^\s*```/.test(raw)) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; } else code.push(raw); continue; }
      if (/^\s*```/.test(raw)) { flushPara(); flushList(); code = []; continue; }
      const bullet = /^\s*[-*•]\s+(.*)$/.exec(raw), num = /^\s*(\d+)[.)]\s+(.*)$/.exec(raw), head = /^\s*#{1,4}\s+(.*)$/.exec(raw);
      if (!raw.trim()) { flushPara(); flushList(); continue; }
      if (head) { flushPara(); flushList(); out.push(`<h4 dir="auto">${inline(head[1])}</h4>`); continue; }
      if (bullet || num) { flushPara(); const tag = bullet ? "ul" : "ol"; if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; } list.items.push(bullet ? bullet[1] : num[2]); continue; }
      if (list && /^\s{2,}\S/.test(raw)) { list.items[list.items.length - 1] += " " + raw.trim(); continue; }
      flushList(); para.push(raw);
    }
    if (code) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    flushPara(); flushList();
    return out.join("");
  }
  const thinkTitle = t => { const all = [...String(t).matchAll(/\*\*([^*\n]{3,90})\*\*/g)]; return all.length ? all[all.length - 1][1].trim() : ""; };
  const BLOCK = /(^|\n)\s*`{0,3}\s*(NEEDS_ALIGNMENT|RESULT|BLOCKED)\s*\n\s*run:[\s\S]*$/;

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.error || `HTTP ${r.status}`), { status: r.status });
    return j;
  }
  const getJSON = async p => { const r = await fetch(p, { cache: "no-store" }); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j; };

  // ---- live connection
  function connect(key, h) {
    let last = -1, tries = 0, fails = 0, ws = null, es = null, closed = false, epoch = null;
    const q = () => `c=${encodeURIComponent(key)}&since=${last + 1}${epoch == null ? "" : "&e=" + epoch}`;
    // the server says which event list (epoch) it streams; a new or reopened chat replaces the list
    const sawEpoch = e => { if (e == null) return; if (epoch != null && e !== epoch) { last = -1; h.reset(); } epoch = e; };
    const seen = (id, ev) => { if (id <= last) return; last = id; h.event(ev, id); };
    function sse() {
      es = new EventSource(`/api/chat/events?${q()}`);
      es.onmessage = m => seen(+m.lastEventId, JSON.parse(m.data));
      es.addEventListener("ping", m => { const d = JSON.parse(m.data); sawEpoch(d.epoch); h.ping(d); });
      es.addEventListener("hello", m => sawEpoch(JSON.parse(m.data).epoch));
      es.addEventListener("reset", m => { epoch = JSON.parse(m.data).epoch; last = -1; h.reset(); });
      es.onerror = () => h.status(false);
      es.onopen = () => h.status(true);
    }
    function open() {
      if (closed) return;
      if (fails >= 2) return sse();
      ws = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/chat/ws?${q()}`);
      let opened = false;
      ws.onopen = () => { opened = true; tries = 0; h.status(true); };
      ws.onmessage = m => {
        const d = JSON.parse(m.data);
        if (d.type === "event") seen(d.id, d.ev);
        else if (d.type === "ping") { sawEpoch(d.epoch); h.ping(d); }
        else if (d.type === "hello") sawEpoch(d.epoch);
        else if (d.type === "reset") { epoch = d.epoch; last = -1; h.reset(); }
      };
      ws.onclose = () => { h.status(false); if (!opened) fails++; if (closed) return; tries++; setTimeout(open, Math.min(1000 * tries, 8000)); };
    }
    open();
    return {
      view(v) { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "view", view: v })); else post("/api/chat/view", { c: key, view: v }).catch(() => {}); },
      restart() { last = -1; if (ws) { const o = ws; ws = null; o.onclose = null; o.close(); } if (es) { es.close(); es = null; } fails = 0; open(); },
    };
  }

  function mount(root, opts) {
    const key = opts.key;
    root.classList.add("fc"); if (opts.wide) root.classList.add("wide");
    root.innerHTML = `
      <div class="fc-head">
        <span class="fc-who"><img class="fc-av disc" src="/static/flynn-logo.png" alt=""><span class="fc-name">Flynn</span></span>
        <span class="fc-cost num" title="This conversation · today, across Studio"></span>
        <span class="fc-grow"></span>
        <button class="btn ghost sm icon" data-act="history" title="Past conversations" aria-label="History"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9"/><path d="M2.3 2.6v2.6h2.6"/><path d="M8 5v3.2l2.2 1.4"/></svg></button>
        <button class="btn ghost sm icon" data-act="new" title="New chat" aria-label="New chat"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3v10M3 8h10"/></svg></button>
      </div>
      <div class="fc-history" hidden></div>
      <div class="fc-thread" aria-live="polite"></div>
      <div class="fc-status" hidden><span class="fc-disc"></span><span class="fc-status-t">Flynn is working…</span></div>
      <form class="fc-composer">
        <div class="fc-cmds float" role="listbox" hidden></div>
        <div class="fc-attach" hidden></div>
        <div class="fc-pointed" hidden></div>
        <textarea rows="1" dir="auto" placeholder="${key === "home" ? "Describe what you want to make…" : key.startsWith("app:") ? "Describe what to build or change…" : "Ask Flynn to change something…"}"></textarea>
        <div class="fc-bar">
          <button type="button" class="btn ghost sm icon" data-act="attach" title="Attach images or files" aria-label="Attach files"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M10.5 4.5 5.8 9.2a1.6 1.6 0 0 0 2.3 2.3l5-5a3 3 0 0 0-4.3-4.3l-5 5a4.4 4.4 0 0 0 6.2 6.2l4-4"/></svg></button>
          ${opts.point ? '<button type="button" class="btn ghost sm icon" data-act="point" title="Select elements on the canvas to send with your message" aria-label="Select elements"><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none"/></svg></button>' : ""}
          <span class="fc-grow"></span>
          <div class="fc-ds" hidden><button type="button" class="btn ghost sm fc-ds-btn" aria-haspopup="menu" title="Which design system Flynn designs with in this conversation"></button><div class="fc-ds-menu float" role="menu" hidden></div></div>
          <span class="chip fc-ds-lock" hidden></span>
          <div class="fc-effort"><button type="button" class="btn ghost sm fc-effort-btn" title="How much flynn spends on your next message" aria-haspopup="menu"></button><div class="fc-effort-menu float" role="menu" hidden></div></div>
          <button type="submit" class="btn primary sm fc-send">Send</button>
        </div>
        <input type="file" multiple hidden>
      </form>`;
    const $ = s => root.querySelector(s);
    if (opts.headIn) {             // the canvas: cost, History and New chat sit in the Chat | Details row
      const head = root.querySelector(".fc-head"); head.classList.add("fc-head-ext"); head.querySelector(".fc-who").remove();
      head.querySelector(".fc-grow").remove(); opts.headIn.appendChild(head);
    }
    const $$ = s => (opts.headIn && opts.headIn.querySelector(s)) || root.querySelector(s);
    const thread = $(".fc-thread"), ta = $("textarea"), sendBtn = $(".fc-send"), statusEl = $(".fc-status");
    let running = false, idlePings = 0, lastEventAt = Date.now(), effort = "balanced", efforts = [], cur = null, lastUser = "";
    let attachments = [], pointed = [], stick = true, firstState = true, commands = [], cmdSel = 0, cmdList = [];
    const mountedAt = Date.now();

    // ---- scroll: stay at the bottom unless the user scrolled up
    thread.addEventListener("scroll", () => { stick = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40; });
    const toEnd = () => { if (stick) thread.scrollTop = thread.scrollHeight; };

    // ---- state and effort
    async function refresh() {
      try {
        const s = await getJSON(`/api/chat/state?c=${encodeURIComponent(key)}`);
        efforts = s.efforts; setAgent(s.agent); setDesign(s.design);
        if (firstState) { effort = s.effort; setRunning(s.running); firstState = false; }   // later: events and pings decide
        const cc = $$(".fc-cost"); cc.textContent = s.cost.conversation ? money(s.cost.conversation) : ""; cc.title = `This conversation${s.cost.today ? ` · today across Studio ${money(s.cost.today)}` : ""}`;
        drawEffort();
      } catch (e) {}
    }
    const meter = id => { const n = ["quick", "balanced", "thorough", "max"].indexOf(id) + 1; return `<span class="fc-meter" aria-hidden="true">${[1, 2, 3, 4].map(i => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`; };
    const EFFORT_NOTE = { quick: "Sonnet, low effort · checker only, no critic", balanced: "Opus, medium effort · one Sonnet critic round", thorough: "Opus, high effort · up to three rounds", max: "Opus, max effort · Opus composer and critic" };
    function drawEffort() {
      const cur = efforts.find(x => x.id === effort) || { label: effort };
      $(".fc-effort-btn").innerHTML = `${meter(effort)}${esc(cur.label)}<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 6.5 5 3.5l3 3" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`;
      $(".fc-effort-menu").innerHTML = `<div class="label">Effort for your next message</div>` + efforts.map(x => `<button type="button" role="menuitemradio" aria-checked="${x.id === effort}" data-e="${x.id}" class="${x.id === effort ? "on" : ""}">
          ${meter(x.id)}<span class="fc-e-t"><b>${esc(x.label)}</b><small>${esc(EFFORT_NOTE[x.id] || "")}</small></span><span class="num faint">${x.cap ? "≤ $" + (+x.cap).toFixed(x.cap < 10 ? 2 : 0) : "no cap"}</span></button>`).join("");
    }
    $(".fc-effort-btn").addEventListener("click", e => { e.stopPropagation(); const m = $(".fc-effort-menu"); m.hidden = !m.hidden; });
    $(".fc-effort-menu").addEventListener("click", e => { const b = e.target.closest("[data-e]"); if (!b) return; effort = b.dataset.e; drawEffort(); $(".fc-effort-menu").hidden = true; ta.focus(); });
    document.addEventListener("click", e => { if (!e.target.closest(".fc-effort")) $(".fc-effort-menu").hidden = true; });

    // ---- design system (chosen before the first message, then fixed for the conversation)
    let design = { choice: "auto", name: "Flynn chooses", applies: false, locked: false, reason: "" }, dsOptions = null;
    const DS_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.2c3.2 0 5.8 2.2 5.8 4.8 0 1.6-1.3 2.4-2.6 2.4H9.6c-.8 0-1.2.7-.8 1.4.4.7 0 1.6-.9 1.6C4.9 12.4 2.2 10.3 2.2 7.6 2.2 4.6 4.7 2.2 8 2.2Z"/><circle cx="5.2" cy="7" r=".6" fill="currentColor"/><circle cx="8" cy="5" r=".6" fill="currentColor"/><circle cx="10.8" cy="6.6" r=".6" fill="currentColor"/></svg>';
    const DS_CARET = '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 6.5 5 3.5l3 3" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
    function setDesign(d) { if (d) { design = d; drawDesign(); } }
    function drawDesign() {
      const box = $(".fc-ds"), lock = $(".fc-ds-lock");
      box.hidden = !design.applies || design.locked; lock.hidden = !design.applies || !design.locked;
      if (!design.applies) return;
      const tip = design.reason || "Set for this conversation. Start a new conversation to use another design system.";
      lock.innerHTML = `${DS_ICON}<span>${esc(design.name)}</span>`; lock.title = tip;
      $(".fc-ds-btn").innerHTML = `${DS_ICON}${esc(design.name)}${DS_CARET}`;
      drawDsMenu();
    }
    function drawDsMenu() {
      const m = $(".fc-ds-menu"); if (!dsOptions) { m.innerHTML = '<div class="label">Loading…</div>'; return; }
      const item = o => `<button type="button" role="menuitemradio" aria-checked="${o.id === design.choice}" data-ds="${esc(o.id)}" class="${o.id === design.choice ? "on" : ""}"><span class="fc-e-t"><b>${esc(o.name)}</b>${o.id === "auto" ? "<small>Flynn picks the system from your brief</small>" : o.id === "none" ? "<small>Plain, well-crafted HTML with no library or theme</small>" : ""}</span></button>`;
      const group = g => { const xs = dsOptions.filter(o => o.group === g); return xs.length ? `<div class="label">${g}</div>` + xs.map(item).join("") : ""; };
      m.innerHTML = `<div class="label">Design system for this conversation</div>` + dsOptions.filter(o => o.id === "auto").map(item).join("") + group("Product UI") + group("Documents") + dsOptions.filter(o => o.id === "none").map(item).join("");
    }
    async function loadDsOptions() {
      try { const d = await getJSON(`/api/chat/design_system?c=${encodeURIComponent(key)}`); dsOptions = d.options || []; setDesign(d); } catch (e) {}
    }
    $(".fc-ds-btn").addEventListener("click", e => { e.stopPropagation(); const m = $(".fc-ds-menu"); m.hidden = !m.hidden; if (!m.hidden && !dsOptions) loadDsOptions(); });
    $(".fc-ds-menu").addEventListener("click", async e => {
      const b = e.target.closest("[data-ds]"); if (!b) return;
      $(".fc-ds-menu").hidden = true; ta.focus();
      try { setDesign(await post("/api/chat/design_system", { c: key, system: b.dataset.ds })); } catch (x) { system(esc(x.message), "err"); refresh(); }
    });
    document.addEventListener("click", e => { if (!e.target.closest(".fc-ds")) $(".fc-ds-menu").hidden = true; });

    // ---- running state
    function sendState() {
      const ready = !!(ta.value.trim() || attachments.length);
      sendBtn.classList.toggle("primary", !running && ready); sendBtn.disabled = !running && !ready;
    }
    function setRunning(on) {
      running = on; statusEl.hidden = !on;
      sendBtn.textContent = on ? "Stop" : "Send"; sendBtn.classList.toggle("danger", on); sendState();
      if (!on) $(".fc-status-t").textContent = "Flynn is working…";
    }
    setInterval(() => { if (running && Date.now() - lastEventAt > 120000) $(".fc-status-t").textContent = "Still working · quiet for 2 min"; }, 5000);

    // ---- messages
    function msg(who, html) {
      const av = who === "you" ? `<div class="fc-av">A</div>` : `<img class="fc-av disc" src="/static/flynn-logo.png" alt="">`;
      const m = el(`<div class="fc-msg ${who}">${av}<div class="fc-body" dir="auto"></div></div>`);
      if (html) m.querySelector(".fc-body").innerHTML = html;
      thread.appendChild(m); toEnd(); return m;
    }
    function flynnMsg() {
      if (cur) return cur;
      const m = msg("flynn", "");
      cur = { el: m, text: "", steps: null, body: m.querySelector(".fc-body") };
      m._c = cur;
      return cur;
    }
    // ---- activity: what Flynn is doing, live (like Claude Code). One list per reply: thinking, steps, and each
    // specialist's own steps nested under it. Open while it runs; folds to "Worked for 2m 10s · 14 steps" when done.
    let live = null, turnAt = 0;
    const AGENT_TOOLS = new Set(["Agent", "Task"]);
    const dur = ms => { const s = Math.max(0, Math.round(ms / 1000)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; };
    function group() {
      const c = flynnMsg();
      if (!c.steps) {
        if (live && live !== c) finishGroup(live);
        c.steps = el(`<details class="fc-steps live" open><summary><span class="fc-spin" aria-hidden="true"></span><span class="fc-steps-t">Working</span><span class="fc-steps-m"></span></summary><ol></ol></details>`);
        // the server's times carry no time zone: the live timer runs on this browser's clock
        c.el.appendChild(c.steps); c.t0 = Date.now(); c.n = 0; c.agentRow = null;
      }
      live = c; return c;
    }
    function head(c, title) {
      if (title) c.steps.querySelector(".fc-steps-t").textContent = title;
      const quiet = Date.now() - lastEventAt;
      c.steps.querySelector(".fc-steps-m").textContent = [c.n ? `${c.n} step${c.n > 1 ? "s" : ""}` : "", c.done ? "" : dur(Date.now() - c.t0),
        !c.done && running && quiet > 20000 ? `quiet ${dur(quiet)}` : ""].filter(Boolean).join(" · ");
    }
    function addRow(c, cls, html) {
      const ol = c.steps.querySelector("ol"), li = el(`<li class="${cls}">${html}</li>`);
      ol.appendChild(li); if (c.steps.open) ol.scrollTop = ol.scrollHeight; toEnd(); return li;
    }
    function step(name, summary) {
      const c = group(); c.n++;
      const li = addRow(c, "tool", `<b>${esc(name)}</b> <span>${esc(summary || "")}</span>`);
      if (AGENT_TOOLS.has(name)) { c.agentRow = li; li.classList.add("agent"); }
      head(c, `${name} ${summary || ""}`.trim().slice(0, 90));
    }
    function thought(text) {
      const c = group(), ol = c.steps.querySelector("ol"), last = ol.lastElementChild;
      let li = last && last.classList.contains("think") && !c.agentRow ? last : null;
      if (!li) { li = addRow(c, "think", `<span class="fc-think-l">Thinking</span><span class="fc-think-x" dir="auto"></span>`); li.onclick = () => li.classList.toggle("open"); }
      const x = li.querySelector(".fc-think-x"); x.textContent = (x.textContent + text).replace(/\*\*/g, "");
      const t = thinkTitle(text); head(c, t ? `Thinking: ${t}` : (c.steps.querySelector(".fc-steps-t").textContent === "Working" ? "Thinking…" : null));
    }
    function subStep(ev) {
      const c = group();
      if (!c.agentRow) { c.n++; c.agentRow = addRow(c, "tool agent", `<b>Agent</b> <span>${esc(ev.agent || "specialist")}</span>`); }
      let ul = c.agentRow.querySelector("ul");
      if (!ul) { ul = el("<ul></ul>"); c.agentRow.appendChild(ul); }
      ul.appendChild(el(`<li><b>${esc(ev.tool || ev.agent || "")}</b> <span>${esc(ev.text || "")}</span></li>`));
      while (ul.children.length > 6) ul.firstElementChild.remove();
      head(c, `${ev.agent || "specialist"}: ${ev.text || ""}`.slice(0, 90));
      const ol = c.steps.querySelector("ol"); if (c.steps.open) ol.scrollTop = ol.scrollHeight;
    }
    function finishGroup(c, ms) {
      if (!c || !c.steps || c.done) return;
      c.done = true; c.steps.classList.remove("live"); c.steps.open = false;
      // Only the turn's last list says how long the turn took (ms, from the server's start and end); an earlier list in
      // the same turn just counts its steps (its own time isn't known when a conversation is replayed).
      c.steps.querySelector(".fc-steps-t").textContent = ms != null && ms >= 0 ? `Worked for ${dur(ms)}` : `${c.n || 0} step${c.n === 1 ? "" : "s"}`;
      if (ms != null && ms >= 0) head(c, null); else c.steps.querySelector(".fc-steps-m").textContent = "";
      if (live === c) live = null;
    }
    setInterval(() => { if (live && !live.done) head(live, null); }, 1000);
    function system(html, cls = "") { const n = el(`<div class="fc-sys ${cls}">${html}</div>`); thread.appendChild(n); toEnd(); return n; }

    // ---- cards
    function card(c) {
      const d = el('<div class="fc-card" dir="auto"></div>'), add = h => { const n = el(h); d.appendChild(n); return n; };
      if (c.kind === "needs_alignment") {
        add(`<div class="label">${esc(c.stage === "contract" ? "Design contract" : c.stage === "options" ? "Options" : "Questions")}</div>`);
        if (c.brief) add(`<div class="fc-md">${md(c.brief)}</div>`);
        const readers = [];
        (c.questions || []).forEach(q => {
          const box = add(`<div class="fc-q"><div class="fc-q-t" dir="auto">${q.n}. ${esc(q.q)}${q.multi ? ' <span class="faint">(choose any)</span>' : ""}</div>${q.because ? `<div class="fc-q-why" dir="auto">${esc(q.because)}</div>` : ""}</div>`);
          const rec = q.multi ? String(q.recommended || "").split(/\s*[;,]\s*/).filter(Boolean) : [q.recommended].filter(Boolean);
          const optsQ = (q.options || []).slice();
          rec.forEach(r => { if (!optsQ.some(o => o.toLowerCase() === r.toLowerCase())) optsQ.unshift(r); });
          const isRec = o => rec.some(r => r.toLowerCase() === o.toLowerCase());
          const choices = optsQ.map(o => { const ch = el(`<button type="button" class="fc-choice${isRec(o) ? " on" : ""}${q.multi ? " multi" : ""}"><span class="dot"></span><span dir="auto">${esc(o)}${isRec(o) ? "<small>Flynn's recommendation</small>" : ""}</span></button>`); ch.dataset.value = o; box.appendChild(ch); return ch; });
          const other = el(`<textarea class="field" rows="1" dir="auto" placeholder="${optsQ.length ? "Something else…" : "Your answer…"}"></textarea>`); box.appendChild(other);
          choices.forEach(ch => ch.onclick = () => { if (q.multi) ch.classList.toggle("on"); else { choices.forEach(x => x.classList.toggle("on", x === ch)); other.value = ""; } });
          other.oninput = () => { if (!q.multi && other.value.trim()) choices.forEach(x => x.classList.remove("on")); };
          readers.push(() => { const p = choices.filter(x => x.classList.contains("on")).map(x => x.dataset.value); if (other.value.trim()) p.push(other.value.trim()); return { n: q.n, answer: p.join("; ") }; });
        });
        const note = add(`<textarea class="field" rows="2" dir="auto" placeholder="Anything else for Flynn (optional)…"></textarea>`);
        const go = add(`<button type="button" class="btn primary">${c.stage === "contract" ? "Approve contract" : "Send answers"}</button>`);
        go.onclick = async () => {
          const answers = readers.map(r => r()).filter(a => a.answer);
          if (!answers.length && !note.value.trim()) return;
          if (!answers.length) answers.push({ n: 1, answer: note.value.trim() });
          go.disabled = true;
          try { await post("/api/chat/answer", { c: key, answers, note: note.value }); d.classList.add("done"); } catch (e) { go.disabled = false; system(esc(e.message), "err"); }
        };
      } else if (c.kind === "result") {
        add(`<div class="label">${esc(c.status && c.status !== "done" ? "Result · " + c.status : "Result")}</div>`);
        if (c.answer || c.summary) add(`<div class="fc-md">${md(c.answer || c.summary)}</div>`);
        if ((c.deliverables || []).length) add(`<div class="fc-files">${c.deliverables.map(x => inline(x)).join("<br>")}</div>`);
        if ((c.caveats || []).length) add(`<div class="fc-md">${md(c.caveats.map(x => "- " + x).join("\n"))}</div>`);
      } else if (c.kind === "blocked") {
        d.classList.add("warn");
        add(`<div class="label">Blocked</div>`); add(`<div class="fc-md">${md(c.reason || "")}${c.needed ? md("Needed: " + c.needed) : ""}</div>`);
      } else if (c.kind === "effort_cap") {
        const lbl = id => (efforts.find(x => x.id === id) || { label: id }).label;
        d.classList.add("warn");
        add(`<div class="fc-md"><p>Stopped at the <b>${esc(lbl(c.level))}</b> limit (${money(c.spent)} spent).</p></div>`);
        const b = add(`<button type="button" class="btn primary">Continue at ${esc(lbl(c.next))}</button>`);
        b.onclick = () => { effort = c.next; drawEffort(); send("Continue where you stopped."); };
      }
      return d;
    }
    function ruleList(rules) {
      return `<div class="fc-rules">${rules.map(r => { const m = /^([A-Za-z_][\w:.-]*)\(([\s\S]*)\)$/.exec(String(r).trim()); const tool = m ? m[1].replace(/^mcp__(\w+)__/, "$1 · ") : "Rule", arg = m ? m[2] : String(r);
        return `<details><summary><b>${esc(tool)}</b> <span>${esc(arg.replace(/\s+/g, " ").trim() || "(any)")}</span></summary><pre>${esc(arg)}</pre></details>`; }).join("")}</div>`;
    }

    function skillCard(k) {
      const d = el(`<div class="fc-card" data-skill="${esc(k.id)}" dir="auto"><div class="label">${k.update ? "Skill update" : "New skill"} · ${esc(k.name)}</div>
        <div class="fc-md"><p>${esc(k.description || "")}</p>${k.why ? `<p class="faint">${esc(k.why)}</p>` : ""}</div>
        <pre class="fc-pre">${esc(k.preview || "")}</pre>${(k.files || []).length ? `<div class="fc-files">Also: ${k.files.map(esc).join(", ")}</div>` : ""}
        <div class="fc-row"><button type="button" class="btn primary sm" data-a="approve">Install skill</button><button type="button" class="btn ghost sm" data-a="reject">Reject</button></div></div>`);
      d.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => {
        d.querySelectorAll("button").forEach(x => x.disabled = true);
        try { await post("/api/chat/skill", { c: key, id: k.id, action: b.dataset.a }); } catch (e) { system(esc(e.message), "err"); d.querySelectorAll("button").forEach(x => x.disabled = false); }
      });
      return d;
    }
    function setAgent(a) { const n = root.querySelector(".fc-name") || (opts.headIn && opts.headIn.querySelector(".fc-name")); if (n) n.textContent = a === "quorra" ? "quorra" : "Flynn"; if (opts.onAgent) opts.onAgent(a); }

    // ---- events
    function onEvent(ev) {
      lastEventAt = Date.now();
      const flynnBody = () => flynnMsg().body;
      switch (ev.t) {
        case "user": {
          if (design.applies && !design.locked) setDesign({ ...design, locked: true, reason: "Set for this conversation. Start a new conversation to use another design system." });
          cur = null; if (live) finishGroup(live); turnAt = ev.at ? Date.parse(ev.at) : Date.now();
          const files = (ev.files || []).length ? `<div class="fc-files">${ev.files.map(esc).join(" · ")}</div>` : "";
          const eff = ev.effort && ev.effort !== "balanced" ? `<span class="chip">${esc(ev.effort)}</span>` : "";
          msg("you", `<div class="fc-md">${md(ev.text || "")}</div>${files}${eff}`); setRunning(true); idlePings = 0; break;
        }
        case "delta": { const c = flynnMsg(); c.text += ev.text || ""; c.body.innerHTML = `<div class="fc-md">${md(c.text.replace(BLOCK, ""))}</div>`; toEnd(); break; }
        case "note": { thought(ev.text || ""); const t = thinkTitle(ev.text || ""); if (t) $(".fc-status-t").textContent = t; break; }
        case "assistant_end": if (cur && !cur.text) { /* tool-only message: keep it for its steps */ } cur = cur && cur.text ? null : cur; break;
        case "tool": step(ev.name, ev.summary); break;
        case "activity": subStep(ev); $(".fc-status-t").textContent = `${ev.agent || "specialist"}: ${ev.text || ""}`.slice(0, 120); break;
        case "denied": {
          const n = system(`<div class="fc-card warn"><div class="fc-md"><p>Flynn needs your permission to continue:</p></div>${ruleList(ev.tools.map(t => t.rule))}<div class="fc-row"><button type="button" class="btn primary sm" data-yes>Allow and continue</button><button type="button" class="btn ghost sm" data-no>Not now</button></div></div>`);
          n.querySelector("[data-yes]").onclick = async e => { e.currentTarget.disabled = true; try { await post("/api/chat/allow", { c: key, rules: ev.tools.map(t => t.rule) }); } catch (x) { system(esc(x.message), "err"); } };
          n.querySelector("[data-no]").onclick = () => n.remove();
          break;
        }
        case "card": thread.appendChild(card(ev.card)); toEnd(); break;
        case "run_created": {
          const n = system(`<div class="fc-card lit"><div class="fc-md"><p>New run: <b>${esc(ev.run)}</b></p></div><a class="btn primary sm" href="/run/${encodeURIComponent(ev.run)}">Open run</a></div>`);
          if (opts.onRunCreated) opts.onRunCreated(ev.run);
          break;
        }
        case "link": {   // the person's computer dropped off (or came back) while a turn runs there
          $(".fc-status-t").textContent = ev.text || "";
          root.classList.toggle("fc-away", ev.state === "waiting");
          break;
        }
        case "stopped": system("Stopped."); break;
        case "moved": {
          const n = system(`<div class="fc-card lit"><div class="fc-md"><p>This conversation continues in <b>${esc(ev.run)}</b>.</p></div><a class="btn primary sm" href="/run/${encodeURIComponent(ev.run)}">Open run</a></div>`);
          if (opts.onMoved && Date.now() - mountedAt > 3000) opts.onMoved(ev.run);     // live, not a replay: follow it
          break;
        }
        case "skill": thread.appendChild(skillCard(ev.skill)); toEnd(); break;
        case "skill-result": {
          const c = thread.querySelector(`[data-skill="${CSS.escape(ev.id)}"]`); if (c) c.classList.add("done");
          system(ev.state === "installed" ? `Skill <b>${esc(ev.name)}</b> is installed. Flynn can use it from your next message.` : `Skill ${esc(ev.name)} rejected; nothing was installed.`);
          break;
        }
        case "agent": system(ev.agent === "quorra" ? "Switched to <b>quorra</b>, the builder. It starts a fresh session." : "Switched back to <b>Flynn</b>."); setAgent(ev.agent); break;
        case "version": {
          const n = system(`<div class="fc-card fc-ver"><span class="label">Saved as a version</span><span class="fc-ver-t" dir="auto">${esc(ev.message || "")}</span>${opts.onRestore ? `<button type="button" class="btn ghost sm" data-restore="${esc(ev.hash || "")}">Restore this version</button>` : ""}</div>`);
          const rb = n.querySelector("[data-restore]"); if (rb) rb.onclick = () => opts.onRestore(ev.hash);
          if (opts.onVersion) opts.onVersion(ev); break;
        }
        case "linked": system(esc(ev.text || "This conversation continues from Home.")); break;
        case "focus": if (opts.onFocus) opts.onFocus(ev); break;
        case "error": {
          const n = system(`<span>${esc(ev.text || "Something went wrong.")}</span>${lastUser ? ' <button type="button" class="btn ghost sm">Retry</button>' : ""}`, "err");
          const b = n.querySelector("button"); if (b) b.onclick = () => send(lastUser);
          break;
        }
        case "turn_end": {
          const took = ev.at && turnAt ? Date.parse(ev.at) - turnAt : null;
          const live_ = [...thread.querySelectorAll(".fc-steps.live")];
          live_.forEach((d, i) => { const c = [...thread.querySelectorAll(".fc-msg")].map(m => m._c).find(x => x && x.steps === d);
            if (c) finishGroup(c, i === live_.length - 1 ? (took != null && !isNaN(took) ? took : Date.now() - c.t0) : null); else { d.classList.remove("live"); d.open = false; } });
          cur = null; setRunning(false);
          if (ev.cost != null) system(`<span class="num">${money(ev.cost)}</span>${ev.interrupted ? " · stopped" : ""}`, "cost");
          refresh(); break;
        }
        case "context": case "session": break;
        default: break;
      }
    }

    // ---- slash commands (flynn desk's list, plus Studio's own and every installed skill)
    getJSON(`/api/chat/commands?c=${encodeURIComponent(key)}`).then(j => { commands = j.commands || []; }).catch(() => {});
    const cmdBox = $(".fc-cmds");
    function drawCmds() {
      const m = /^\/(\S*)$/.exec(ta.value);
      if (!m || !commands.length) { cmdBox.hidden = true; return; }
      const q = m[1].toLowerCase();
      cmdList = !q ? commands : [...commands.filter(c => c.name.startsWith(q)), ...commands.filter(c => !c.name.startsWith(q) && (c.label + " " + c.hint).toLowerCase().includes(q))];
      if (cmdSel >= cmdList.length) cmdSel = 0;
      let g = "", html = "";
      cmdList.forEach((c, i) => {
        if (!q && c.group !== g) { g = c.group; html += `<div class="label">${esc(g)}</div>`; }
        html += `<button type="button" class="fc-cmd${i === cmdSel ? " on" : ""}" data-i="${i}"><b>/${esc(c.name)}</b><span>${esc(c.label)}</span><small>${esc(c.hint)}</small></button>`;
      });
      cmdBox.innerHTML = html || `<div class="faint">No command matches. Type a message instead, or Esc.</div>`;
      cmdBox.hidden = false;
      const on = cmdBox.querySelector(".fc-cmd.on"); if (on) on.scrollIntoView({ block: "nearest" });
      cmdBox.querySelectorAll(".fc-cmd").forEach(b => b.onmousedown = e => { e.preventDefault(); pickCmd(cmdList[+b.dataset.i]); });
    }
    function pickCmd(c) {
      cmdBox.hidden = true;
      if (c.kind !== "ui" && c.arg) { ta.value = `/${c.name} `; autosize(); sendState(); ta.focus(); return; }
      ta.value = ""; autosize(); sendState(); runCmd(c, "");
    }
    async function runCmd(c, arg) {
      switch (c.name) {
        case "new": return newChat();
        case "history": return toggleHistory();
        case "attach": return fileInput.click();
        case "select": return opts.point ? opts.point(pointed, list => { setPointed(list); ta.focus(); }) : null;
        case "stop": return post("/api/chat/stop", { c: key }).catch(() => {});
        case "settings": location.href = "/settings"; return;
        case "systems": location.href = "/systems"; return;
        case "record": location.href = "/systems#record"; return;
        case "usage": { const s = await getJSON(`/api/chat/state?c=${encodeURIComponent(key)}`); return system(`This conversation: <b>${money(s.cost.conversation) || "$0.00"}</b> · today across Studio: <b>${money(s.cost.today) || "$0.00"}</b>`); }
        case "help": return system(`<div class="fc-card"><div class="label">Commands</div><div class="fc-md">${md(commands.map(x => `- **/${x.name}**${x.arg ? " …" : ""}: ${x.hint}`).join("\n"))}</div></div>`);
      }
      const view = Object.assign({}, opts.getView ? opts.getView() : {}, pointed.length ? { pointed } : {});
      try { await post("/api/chat/command", { c: key, name: c.name, arg, effort, view, ...(design.applies && !design.locked ? { design_system: design.choice } : {}) }); setPointed([]); }
      catch (e) { system(esc(e.message), "err"); }
    }
    ta.addEventListener("input", () => { cmdSel = 0; drawCmds(); });
    ta.addEventListener("keydown", e => {
      if (cmdBox.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); e.stopImmediatePropagation(); cmdSel = (cmdSel + (e.key === "ArrowDown" ? 1 : -1) + cmdList.length) % Math.max(cmdList.length, 1); drawCmds(); }
      else if ((e.key === "Enter" || e.key === "Tab") && cmdList[cmdSel]) { e.preventDefault(); e.stopImmediatePropagation(); pickCmd(cmdList[cmdSel]); }
      else if (e.key === "Escape") { e.preventDefault(); cmdBox.hidden = true; }
    }, true);
    ta.addEventListener("blur", () => setTimeout(() => { cmdBox.hidden = true; }, 150));

    // ---- sending
    async function send(text) {
      text = (text ?? ta.value).trim();
      if (!text && !attachments.length) return;
      const cm = /^\/(\S+)\s*([\s\S]*)$/.exec(text);
      const cmd = cm && commands.find(c => c.name === cm[1]);
      if (cmd) { ta.value = ""; autosize(); sendState(); return runCmd(cmd, cm[2].trim()); }
      const view = Object.assign({}, opts.getView ? opts.getView() : {}, pointed.length ? { pointed } : {});
      try {
        await post("/api/chat/send", { c: key, text: text || "(see the attached files)", effort, view, files: attachments.map(a => a.id),
          ...(design.applies && !design.locked ? { design_system: design.choice } : {}) });
        lastUser = text; ta.value = ""; autosize(); attachments = []; drawAttach(); setPointed([]); stick = true; sendState();
      } catch (e) {
        if (e.status === 503) system(`Studio's chat is off or no engine is available. <a class="link" href="/settings">Open Settings</a>`, "err");
        else system(esc(e.message), "err");
      }
    }
    $("form").addEventListener("submit", async e => {
      e.preventDefault();
      if (running) { sendBtn.disabled = true; try { await post("/api/chat/stop", { c: key }); } catch (x) {} sendBtn.disabled = false; return; }
      send();
    });
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!running) send(); } });
    const autosize = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 220) + "px"; };
    ta.addEventListener("input", () => { autosize(); sendState(); });

    // ---- attachments
    const fileInput = $('input[type="file"]');
    function drawAttach() {
      const box = $(".fc-attach"); box.hidden = !attachments.length;
      sendState();
      box.innerHTML = attachments.map((a, i) => `<span class="chip">${esc(a.name)} <button type="button" data-rm="${i}" aria-label="Remove">×</button></span>`).join("");
    }
    $(".fc-attach").addEventListener("click", e => { const b = e.target.closest("[data-rm]"); if (b) { attachments.splice(+b.dataset.rm, 1); drawAttach(); } });
    async function upload(files) {
      for (const f of [...files].slice(0, 10 - attachments.length)) {
        if (f.size > 25 * 1024 * 1024) { system(`${esc(f.name)} is over 25 MB.`, "err"); continue; }
        const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(String(fr.result).split(",")[1] || ""); fr.readAsDataURL(f); });
        try { attachments.push(await post("/api/chat/upload", { c: key, name: f.name || "pasted.png", type: f.type, data })); drawAttach(); } catch (e) { system(esc(e.message), "err"); }
      }
    }
    const onAct = e => {
      const a = e.target.closest("[data-act]"); if (!a) return;
      if (a.dataset.act === "attach") fileInput.click();
      if (a.dataset.act === "point" && opts.point) opts.point(pointed, list => { setPointed(list); ta.focus(); });
      if (a.dataset.act === "new") newChat();
      if (a.dataset.act === "history") toggleHistory();
    };
    root.addEventListener("click", onAct);
    if (opts.headIn) opts.headIn.addEventListener("click", e => { if (e.target.closest(".fc-head-ext")) onAct(e); });
    thread.addEventListener("click", e => { const b = e.target.closest("[data-prefill]"); if (b) { ta.value = b.dataset.prefill; autosize(); sendState(); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } });
    fileInput.onchange = () => { upload(fileInput.files); fileInput.value = ""; };
    ta.addEventListener("paste", e => { const fs = [...(e.clipboardData || {}).files || []]; if (fs.length) { e.preventDefault(); upload(fs); } });
    root.addEventListener("dragover", e => { e.preventDefault(); root.classList.add("drop"); });
    root.addEventListener("dragleave", e => { if (e.target === root) root.classList.remove("drop"); });
    root.addEventListener("drop", e => { e.preventDefault(); root.classList.remove("drop"); upload(e.dataTransfer.files); });

    // ---- selected elements: chips above the message, sent as context with it
    function setPointed(list) {
      pointed = (list || []).slice(0, 8);
      const box = $(".fc-pointed"); box.hidden = !pointed.length;
      const short = p => p.selector.split(" > ").slice(-1)[0].replace(/:nth-of-type\((\d+)\)/, "·$1");
      box.innerHTML = pointed.length ? `<span class="label">${pointed.length === 1 ? "Selected" : pointed.length + " selected"}</span>` +
        (pointed.length > 1 ? `<button type="button" class="fc-clear" data-unpick="all">Clear</button>` : "") +
        pointed.map((p, i) => `<span class="chip lit" title="${esc(p.selector)}"><code>${esc(short(p))}</code>${p.text ? ` <span class="fa-run" dir="auto">${esc(p.text.slice(0, 28))}</span>` : ""}<button type="button" data-unpick="${i}" aria-label="Remove">×</button></span>`).join("") +
        "" : "";
      if (opts.onSelection) opts.onSelection(pointed);
    }
    root.addEventListener("click", e => {
      const b = e.target.closest("[data-unpick]"); if (!b) return;
      setPointed(b.dataset.unpick === "all" ? [] : pointed.filter((_, i) => i !== +b.dataset.unpick));
    });

    // ---- history and new chat
    async function newChat() {
      if (running) return system("Flynn is still working. Stop the turn first.", "err");
      try { await post("/api/chat/new", { c: key }); } catch (e) { system(esc(e.message), "err"); }
    }
    async function toggleHistory() {
      const box = $(".fc-history");
      if (!box.hidden) { box.hidden = true; return; }
      const items = await getJSON(`/api/chat/history?c=${encodeURIComponent(key)}`).catch(() => []);
      box.innerHTML = items.length ? items.map(h => `<button type="button" class="fc-h" data-h="${esc(h.id)}" ${h.run ? `data-run="${esc(h.run)}"` : ""}><span dir="auto">${esc(h.title || "Untitled")}${h.run ? `<em class="fc-h-run">→ ${esc(h.run)}</em>` : ""}</span><small class="num">${esc(h.id.replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5"))}</small></button>`).join("") : `<div class="faint">No earlier conversations.</div>`;
      box.hidden = false;
      box.querySelectorAll("[data-h]").forEach(b => b.onclick = async () => { if (b.dataset.run) { location.href = "/run/" + encodeURIComponent(b.dataset.run); return; } try { await post("/api/chat/open", { c: key, id: b.dataset.h }); box.hidden = true; } catch (e) { system(esc(e.message), "err"); } });
    }

    function reset() { thread.innerHTML = ""; cur = null; empty(); refresh(); }
    function empty() {
      if (thread.children.length) return;
      const kind = key.split(":")[0];
      const msgs = {
        home: ["What do you want to make?", "Flynn asks a few questions first, then shows you options to pick from."],
        run: ["Talk to Flynn about this run.", "Ask for a change, select elements on the canvas, or ask it to work through the open comments."],
        system: ["Talk to Flynn about this design system.", "Update it from code, check its health, add components or tokens. Every change comes to you for approval."],
        app: ["Describe what to build or change.", "Flynn writes the code, the preview updates, and every turn is saved as a version."],
      }[kind] || ["Talk to Flynn.", ""];
      thread.appendChild(el(`<div class="fc-empty"><b>${msgs[0]}</b><span>${msgs[1]}</span>` +
        (opts.starters ? `<div class="fc-starters">${opts.starters.map(([l, t]) => `<button type="button" class="chip" data-prefill="${esc(t)}">${esc(l)}</button>`).join("")}</div>` : "") + `</div>`));
    }

    const conn = connect(key, {
      event: (ev) => { const e = thread.querySelector(".fc-empty"); if (e) e.remove(); onEvent(ev); },
      ping: p => { if (!p.running) { if (++idlePings >= 2 && running) setRunning(false); } else { idlePings = 0; if (!running) setRunning(true); } },
      reset, status: ok => root.classList.toggle("offline", !ok),
    });
    empty(); refresh(); sendState();
    let lastView = "";
    setInterval(() => { if (!opts.getView) return; const v = JSON.stringify(opts.getView()); if (v !== lastView) { lastView = v; conn.view(JSON.parse(v)); } }, 1500);

    return {
      setView(v) { conn.view(v); },
      select: setPointed,
      pointed: () => pointed.slice(),
      async setAgent(a) { try { const s = await post("/api/chat/agent", { c: key, agent: a }); setAgent(s.agent); } catch (e) { system(esc(e.message), "err"); throw e; } },
      focus() { ta.focus(); },
      prefill(t) { ta.value = t; autosize(); sendState(); ta.focus(); ta.setSelectionRange(t.length, t.length); },
      send,
    };
  }

  window.FlynnChat = { mount, md };
})();
