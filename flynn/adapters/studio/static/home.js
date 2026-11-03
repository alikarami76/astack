(() => {
  const grid = document.getElementById("grid"), q = document.getElementById("q");
  let runs = [], filter = "all", area = "all", kind = "", system = "";
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  area = store.get("studio.area") || "all";
  const KINDS = { deck: "Decks", report: "Reports", newsletter: "Newsletters", release: "Release notes", chart: "Charts" };
  const KIND1 = { deck: "Deck", report: "Report", newsletter: "Newsletter", release: "Release note", chart: "Chart", screen: "UI design" };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const SMALL = new Set(["a", "an", "and", "for", "of", "the", "to", "with", "in", "on", "vs"]);
  const UP = { dk: "DK", ui: "UI", ceo: "CEO", pdf: "PDF" };
  const title = r => { const words = (r.id.length > 11 ? r.id.slice(11) : r.id).split("-").filter(Boolean);
    return words.map((w, i) => UP[w] || (w.length <= 3 && !/[aeiou]/.test(w) ? w.toUpperCase() : (i && SMALL.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))).join(" "); };
  const prompt1 = r => { const t = (r.request || "").trim(); return t ? t.charAt(0).toUpperCase() + t.slice(1) : ""; };
  const TYPES = { chart: "Report / chart", graphic: "Graphic", deck: "Deck", ui: "UI", "learn-system": "System", sync: "Sync", question: "Question" };

  function render() {
    const s = q.value.trim().toLowerCase();
    const inArea = r => area === "all" || r.area === area;
    document.querySelectorAll("#areas button").forEach(b => { b.classList.toggle("on", b.dataset.a === area);
      const n = runs.filter(r => b.dataset.a === "all" || r.area === b.dataset.a).length; b.querySelector("span").textContent = n ? String(n) : ""; });
    const kinds = $k();
    function $k() {
      const box = document.getElementById("kinds");
      const present = [...new Set(runs.filter(r => r.area === "doc").map(r => r.kind))].filter(k => KINDS[k]);
      box.hidden = area !== "doc" || present.length < 2;
      if (!box.hidden) box.innerHTML = `<button data-k="" class="chip ${kind ? "" : "lit"}">All docs</button>` + present.map(k => `<button data-k="${k}" class="chip ${kind === k ? "lit" : ""}">${KINDS[k]}</button>`).join("");
      return present;
    }
    const sysSel = document.getElementById("sys"), systems = [...new Set(runs.map(r => r.system).filter(Boolean))].sort();
    if (sysSel.options.length - 1 !== systems.length) { const cur = sysSel.value; sysSel.innerHTML = `<option value="">All design systems</option>` + systems.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join(""); sysSel.value = cur; }
    const shown = runs.filter(r =>
      inArea(r) && (area !== "doc" || !kind || r.kind === kind) && (!system || r.system === system) &&
      (!s || (r.id + " " + r.request).toLowerCase().includes(s)) &&
      (filter === "all" || (filter === "open" && !r.closed) || (filter === "comments" && r.comments_open > 0) || (filter === "decided" && r.decision)));
    grid.innerHTML = shown.map(r => `
      <a class="card" href="/run/${encodeURIComponent(r.id)}">
        <div class="thumb" style="${r.thumb ? `background-image:url('/r/${encodeURIComponent(r.id)}/${r.thumb}')` : ""}">${r.thumb ? "" : `<div class="empty"><span class="label">${esc(KIND1[r.kind] || TYPES[r.type] || "Run")}</span><b>${esc(title(r))}</b><small>Not rendered yet</small></div>`}</div>
        <div class="body">
          <div class="title">${esc(title(r))}</div>
          <div class="prompt" dir="auto" title="${esc(r.request || "")}">${esc(prompt1(r))}</div>
          <div class="muted" style="font-size:12px">${esc(r.date)}</div>
          <div class="meta">
            <span class="pill">${esc(KIND1[r.kind] || TYPES[r.type] || r.type || "run")}</span>
            ${r.system ? `<span class="pill">${esc(r.system)}</span>` : ""}
            ${r.options.length > 1 ? `<span class="pill ink">${r.options.length} options</span>` : ""}
            ${r.versions.length ? `<span class="pill">${r.versions.length} version${r.versions.length > 1 ? "s" : ""}</span>` : ""}
            ${r.comments_open ? `<span class="pill red">${r.comments_open} open comments</span>` : ""}
            ${r.decision ? `<span class="pill green">picked ${esc(r.decision.option)}</span>` : ""}
            ${r.requests.filter(x => x.status !== "done" && x.status !== "failed").length ? `<span class="pill red">export waiting</span>` : ""}
            ${r.closed ? `<span class="pill">closed</span>` : ""}
          </div>
        </div>
      </a>`).join("") || `<div class="muted">No runs match.</div>`;
  }
  document.getElementById("seg").onchange = e => { filter = e.target.value; render(); };
  q.oninput = render;
  document.getElementById("areas").onclick = e => { const b = e.target.closest("[data-a]"); if (!b) return; area = b.dataset.a; kind = ""; store.set("studio.area", area); render(); };
  document.getElementById("kinds").onclick = e => { const b = e.target.closest("[data-k]"); if (!b) return; kind = b.dataset.k; render(); };
  document.getElementById("sys").onchange = e => { system = e.target.value; render(); };
  fetch("/api/settings", { cache: "no-store" }).then(r => r.json()).then(j => { if (j.features && j.features.studio_home_v2 === false) { document.getElementById("areas").hidden = true; area = "all"; render(); } }).catch(() => {});
  const load = async () => { runs = await (await fetch("/api/runs", { cache: "no-store" })).json(); render(); };
  load(); setInterval(load, 5000);

  // the rail (rail.js) draws itself; its New button asks Home to focus the conversation
  let homeChat = null, dock = null;
  addEventListener("rail:new", () => { if (dock && !dock.isOpen()) dock.open(); if (homeChat) homeChat.focus(); else location.href = "/settings"; });
  if (new URLSearchParams(location.search).get("new")) { history.replaceState(null, "", location.pathname); setTimeout(() => homeChat && homeChat.focus(), 400); }

  // the Home conversation: new work starts here; a run it creates opens with the thread
  (async () => {
    let on = true;
    try { const r = await fetch("/api/settings", { cache: "no-store" }); on = (await r.json()).features.studio_chat !== false; } catch (e) {}
    if (!on || !window.FlynnChat) return;
    document.getElementById("homeshell").classList.add("with-chat");
    document.getElementById("home-side").hidden = false;
    if (window.FlynnGlass) dock = FlynnGlass.dock({ type: "home", shell: document.getElementById("homeshell"), side: document.getElementById("home-side"), edge: "right", def: 440 });
    homeChat = FlynnChat.mount(document.getElementById("home-chat"), {
      key: "home", wide: true,
      starters: [
        ["Admin Panel screen", "A new Admin Panel screen: "],
        ["Report", "A report for "],
        ["Deck", "A deck for "],
        ["Newsletter", "A newsletter about "],
        ["Release note", "A release note for "],
        ["Chart", "A chart showing "],
      ],
      onRunCreated: () => { load(); if (window.FlynnRail) FlynnRail.reload(); },
      onMoved: run => { location.href = "/run/" + encodeURIComponent(run); },
    });
  })();
})();
