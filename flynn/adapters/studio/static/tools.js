/* Studio's Tools pane (the run canvas's second tab): the run at a glance, the decision, comments that flynn can act on,
   direct edits on the canvas, code and merge requests with quorra, links, exports and versions.
   StudioTools.mount(el, ctx) -> { render(run, comments, edits) }
   ctx: { RUN, R(path), api(path, body), toast(text, err), label(option), frames(), focus({option, version, selector}),
          renderFrames(), reload(), chat (the chat handle: send, select, pointed, setAgent), tab(name),
          shownVersions (Set), features, generateCover() (POST /api/run/<run>/cover, then polls) } */
(() => {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ago = t => { if (!t) return ""; const d = (Date.now() - new Date(t).getTime()) / 60000; return d < 1 ? "just now" : d < 60 ? `${Math.round(d)} min ago` : d < 1440 ? `${Math.round(d / 60)} h ago` : String(t).slice(0, 10); };
  const EXPORTS = [
    { kind: "pdf", label: "PDF", note: "one page" }, { kind: "png", label: "PNG", note: "section images" },
    { kind: "html", label: "HTML", note: "zip" }, { kind: "tsx", label: "TSX", note: "web-app" },
    { kind: "pptx", label: "PPTX", note: "decks, editable" }, { kind: "eml", label: "Email", note: "newsletters" },
    { kind: "penpot", label: "flynn desk", note: "build as a page" }, { kind: "figma", label: "Figma", note: "via Flynn" },
    { kind: "canva", label: "Canva", note: "via Flynn" },
  ];

  function mount(root, ctx) {
    const RUN = ctx.RUN;
    let run = null, comments = [], edits = [], filter = "open", editing = false, agent = "flynn";
    root.innerHTML = `
      <section class="t-head"></section>
      <section class="t-sec" data-sec="cover"><h3>Title card</h3><div class="t-body"></div></section>
      <section class="t-sec" data-sec="decision"><h3>Decision</h3><div class="t-body"></div></section>
      <section class="t-sec" data-sec="comments"><div class="t-h3"><h3>Comments</h3><div class="seg sm t-filter"><button data-f="open" class="on">Open</button><button data-f="resolved">Resolved</button><button data-f="all">All</button></div></div><div class="t-body"></div></section>
      <section class="t-sec" data-sec="edits"><div class="t-h3"><h3>Edits</h3><label class="t-switch"><input type="checkbox" class="t-editmode"><span></span>Edit on canvas</label></div><div class="t-body"></div></section>
      <section class="t-sec" data-sec="code"><h3>Code</h3><div class="t-body"></div></section>
      <section class="t-sec" data-sec="links" hidden><h3>Links</h3><div class="t-body"></div></section>
      <section class="t-sec" data-sec="export"><h3>Export</h3><div class="t-body"></div></section>
      <section class="t-sec" data-sec="versions"><h3>Versions</h3><div class="t-body"></div></section>`;
    const sec = n => root.querySelector(`[data-sec="${n}"] .t-body`);
    const F = ctx.features || {};
    if (F.studio_comments_ai === false) root.querySelector('[data-sec="comments"]').hidden = true;
    if (F.studio_quick_edit === false) root.querySelector('[data-sec="edits"]').hidden = true;
    if (F.studio_quorra === false) root.querySelector('[data-sec="code"]').hidden = true;

    // ---------- comments (module 2): every thread here, each one a task Flynn can take
    root.querySelector(".t-filter").onclick = e => { const b = e.target.closest("[data-f]"); if (!b) return; filter = b.dataset.f; root.querySelectorAll(".t-filter button").forEach(x => x.classList.toggle("on", x === b)); drawComments(); };
    const where = c => { const p = c.page || "page/index.html"; const m = /^versions\/([^/]+)\/([^/]+)\//.exec(p); const o = /^options\/([^/]+)\//.exec(p);
      return m ? `${ctx.label(m[2])} · ${m[1]}` : o ? `Option ${ctx.label(o[1])}` : (run && run.options.length > 1 ? "Option A" : "Page"); };
    const anchorOf = c => { const a = c.anchor || {}; const p = c.page || "page/index.html"; const m = /^versions\/([^/]+)\/([^/]+)\//.exec(p); const o = /^options\/([^/]+)\//.exec(p);
      const list = [a.selector, ...((a.multi || []).map(x => x.selector))].filter(Boolean);
      return list.map(sel => ({ selector: sel, page: p, option: m ? m[2] : o ? o[1] : "main", version: m ? m[1] : "", text: a.snippet || "" })); };
    function drawComments() {
      const old = root.querySelector(".t-newc textarea"), draft = old ? old.value : "", had = old && document.activeElement === old;
      drawCommentsInner();
      const ta = root.querySelector(".t-newc textarea"); if (ta) { ta.value = draft; if (had) { ta.focus(); ta.setSelectionRange(draft.length, draft.length); } }
    }
    function drawCommentsInner() {
      const shown = comments.filter(c => filter === "all" || c.status === filter).slice().reverse();
      const open = comments.filter(c => c.status === "open");
      sec("comments").innerHTML = `
        ${open.length > 1 ? `<button class="btn primary sm t-wide" data-act="fixall">Fix all ${open.length} open comments with Flynn</button>` : ""}
        <div class="t-newc"><textarea class="field" rows="1" dir="auto" placeholder="Comment on the selected elements…"></textarea><button class="btn sm" data-act="newc">Add</button></div>
        ${shown.length ? shown.map(c => `
          <div class="t-c ${c.status}" data-n="${c.n}">
            <div class="t-c-top"><span class="t-c-n">#${c.n}</span><span class="t-c-where">${esc(where(c))}</span><span class="t-c-t">${esc(ago(c.time))}</span></div>
            <div class="t-c-text" dir="auto">${esc(c.text)}</div>
            ${(c.replies || []).map(r => `<div class="t-c-reply" dir="auto"><b>${esc(r.author)}</b> ${esc(r.text)}</div>`).join("")}
            <div class="t-c-acts">
              ${c.status === "open" ? `<button class="btn sm" data-act="ask">Ask Flynn</button>` : ""}
              <button class="btn ghost sm" data-act="jump">Show</button>
              <button class="btn ghost sm" data-act="reply">Reply</button>
              <button class="btn ghost sm" data-act="${c.status === "open" ? "resolve" : "reopen"}">${c.status === "open" ? "Resolve" : "Reopen"}</button>
            </div>
          </div>`).join("") : `<div class="t-empty">${filter === "open" ? "No open comments." : "No comments here yet."} Select something on the canvas, then type above.</div>`}`;
    }
    root.addEventListener("click", async e => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const card = b.closest(".t-c"), n = card && +card.dataset.n, c = card && comments.find(x => x.n === n);
      try {
        switch (b.dataset.act) {
          case "ask": ctx.tab("chat"); ctx.chat.select(anchorOf(c)); return ctx.chat.send(`Please handle comment #${c.n} (${where(c)}): “${c.text}”. Change the page, then reply to the comment and resolve it.`);
          case "fixall": ctx.tab("chat"); return ctx.chat.send(`Work through all ${comments.filter(x => x.status === "open").length} open comments on this run (studio_comments list): change the pages, reply to each comment saying what changed, and resolve it. Ask me only where two readings are equally likely.`);
          case "jump": { const a = anchorOf(c)[0]; return ctx.focus(a || { option: "main" }); }
          case "reply": {
            const t = prompt(`Reply to #${c.n}. Start with @flynn or @quorra to hand it to them.`, ""); if (!t) return;
            await ctx.api(ctx.R(`__review/api/comments/${c.n}`), { reply: t });
            const m = /^@(flynn|quorra)\b\s*(.*)$/i.exec(t.trim());
            if (m) { ctx.tab("chat"); if (m[1].toLowerCase() === "quorra") await ctx.chat.setAgent("quorra"); ctx.chat.select(anchorOf(c)); ctx.chat.send(`About comment #${c.n} (“${c.text}”): ${m[2] || "please handle it"}`); }
            return ctx.reload();
          }
          case "resolve": case "reopen": await ctx.api(ctx.R(`__review/api/comments/${c.n}`), { status: b.dataset.act === "resolve" ? "resolved" : "open" }); return ctx.reload();
          case "newc": {
            const ta = root.querySelector(".t-newc textarea"), text = ta.value.trim(); if (!text) return;
            const sel = ctx.chat.pointed();
            if (!sel.length) { ctx.toast("Select one or more elements first (the crosshair in the chat)", 1); return; }
            const first = sel[0];
            await ctx.api(ctx.R("__review/api/comments"), { text, page: first.page || "page/index.html",
              anchor: { selector: first.selector, snippet: (first.text || "").slice(0, 80), multi: sel.slice(1).map(x => ({ selector: x.selector, page: x.page })) } });
            ta.value = ""; ctx.chat.select([]); ctx.toast(`Comment added on ${sel.length} element${sel.length > 1 ? "s" : ""}`); return ctx.reload();
          }
          case "apply": ctx.tab("chat"); return ctx.chat.send(`Apply my ${edits.filter(x => x.status === "pending").length} direct edits on this run (studio_edits list) to the source pages, re-render if needed, and mark each applied.`);
          case "undo": { const en = +b.dataset.n; await ctx.api(`/api/run/${encodeURIComponent(RUN)}/edits/${en}`, { status: "rejected", note: "withdrawn by the user" }); ctx.renderFrames(true); return ctx.reload(); }
          case "pick": await ctx.api(`/api/run/${encodeURIComponent(RUN)}/pick`, { option: b.dataset.o }); ctx.toast(`Picked ${ctx.label(b.dataset.o)}`); return ctx.reload();
          case "agent": { const a = b.dataset.a; await ctx.chat.setAgent(a); agent = a; drawCode(); return; }
          case "build": {
            ctx.tab("chat"); await ctx.chat.setAgent("quorra"); agent = "quorra"; drawCode();
            const opt = run.decision ? run.decision.option : "main";
            return ctx.chat.send(`Build option ${ctx.label(opt)} of this run as a web-app change: export it as TSX (studio_export kind tsx, option ${opt}), map it to the real UIKit components, build it in your work clone with your writer and reviewers, run the checks, then show me the publish plan (ask_user: Approve / Change something). Push and open the MR only after I approve, and attach the MR with studio_link.`);
          }
          case "export": {
            b.classList.add("busy");
            try { const r = await ctx.api(`/api/run/${encodeURIComponent(RUN)}/export`, { kind: b.dataset.kind, option: root.querySelector(".t-exopt").value });
              ctx.toast(r.status === "waiting" ? `${b.dataset.kind} export queued for Flynn (${r.id})` : b.dataset.kind === "penpot" ? "Built in flynn desk" : `Exported ${r.path}`); await ctx.reload(); }
            finally { b.classList.remove("busy"); }
            return;
          }
          case "snap": { const l = prompt("Name this version", ""); if (l === null) return; const r = await ctx.api(`/api/run/${encodeURIComponent(RUN)}/snapshot`, { label: l }); ctx.toast("Saved " + r.version); return ctx.reload(); }
          case "cover": b.disabled = true; return ctx.generateCover();
          case "copy": await navigator.clipboard.writeText(b.dataset.v); return ctx.toast("Copied");
        }
      } catch (x) { ctx.toast(x.message, 1); }
    });

    // ---------- edits (module 3): change text, hide elements, right on the frames; Flynn carries them into the source
    root.querySelector(".t-editmode").onchange = e => { editing = e.target.checked; document.body.classList.toggle("editing", editing); armFrames(); if (editing) ctx.toast("Double-click text to edit it · Alt-click an element to hide it"); };
    const armed = new WeakSet();
    function armFrames() {
      ctx.frames().forEach(({ doc, frame }) => {
        if (!doc || armed.has(doc)) return;
        armed.add(doc);
        doc.addEventListener("dblclick", ev => {
          if (!editing) return;
          const t = ev.target; if (!t || t === doc.body || t.closest("[contenteditable]")) return;
          ev.preventDefault();
          const before = t.innerText; t.contentEditable = "true"; t.focus(); t.style.outline = "2px dashed #f3b26b";
          const done = async () => {
            t.removeEventListener("blur", done); t.contentEditable = "false"; t.style.outline = "";
            const after = t.innerText; if (after === before) return;
            try { await ctx.api(`/api/run/${encodeURIComponent(RUN)}/edits`, { edits: [{ kind: "text", selector: ctx.cssPath(t), page: frame.dataset.page, option: frame.dataset.option, version: frame.dataset.version, before, after }] }); ctx.reload(); }
            catch (x) { ctx.toast(x.message, 1); }
          };
          t.addEventListener("blur", done);
        }, true);
        doc.addEventListener("click", async ev => {
          if (!editing || !ev.altKey) return;
          ev.preventDefault(); ev.stopPropagation();
          const t = ev.target; t.style.display = "none";
          try { await ctx.api(`/api/run/${encodeURIComponent(RUN)}/edits`, { edits: [{ kind: "hide", selector: ctx.cssPath(t), page: frame.dataset.page, option: frame.dataset.option, version: frame.dataset.version }] }); ctx.reload(); }
          catch (x) { ctx.toast(x.message, 1); }
        }, true);
      });
    }
    function drawEdits() {
      const p = edits.filter(x => x.status === "pending"), done = edits.filter(x => x.status !== "pending").slice(-4).reverse();
      sec("edits").innerHTML = (p.length ? `<button class="btn primary sm t-wide" data-act="apply">Apply ${p.length} edit${p.length > 1 ? "s" : ""} with Flynn</button>` : "") +
        (p.length || done.length ? [...p, ...done].map(x => `<div class="t-e ${x.status}"><span class="chip ${x.status === "pending" ? "amber" : x.status === "applied" ? "green" : ""}">${esc(x.kind)}</span>
          <span class="t-e-t" dir="auto">${x.kind === "text" ? `${esc(x.before.slice(0, 40))} → <b>${esc(x.after.slice(0, 40))}</b>` : x.kind === "hide" ? `hide <code>${esc(x.selector.split(" > ").pop())}</code>` : esc(x.note || x.value || "")}</span>
          ${x.status === "pending" ? `<button class="btn ghost sm" data-act="undo" data-n="${x.n}">Undo</button>` : `<small class="faint">${esc(x.status)}</small>`}</div>`).join("")
        : `<div class="t-empty">Turn on “Edit on canvas”, then double-click any text to change it, or Alt-click an element to hide it. Flynn carries your edits into the source page.</div>`);
    }

    // ---------- code (module 7): quorra builds the design in web-app, MRs come back here
    function drawCode() {
      const isUI = run && run.area === "ui";
      sec("code").innerHTML = `
        <div class="t-row"><span class="faint">Chat with</span><div class="seg sm"><button data-act="agent" data-a="flynn" class="${agent === "flynn" ? "on" : ""}">Flynn</button><button data-act="agent" data-a="quorra" class="${agent === "quorra" ? "on" : ""}">quorra</button></div></div>
        ${isUI ? `<button class="btn sm t-wide" data-act="build">Build in web-app with quorra</button>
          <p class="t-note">quorra builds the picked option in its own work clone with the real UIKit components and the repo's checks. Nothing is pushed until you approve its plan.</p>`
          : `<p class="t-note">quorra builds Admin Panel screens as merge requests. Switch the chat to quorra for code questions about this run.</p>`}`;
    }

    // ---------- the rest
    function drawHead() {
      let brief = (run.request || "").split(/(?<=[.!?؟])\s/)[0].slice(0, 180);
      brief = brief.charAt(0).toUpperCase() + brief.slice(1);
      const chips = [run.area === "ui" ? "UI design" : run.area === "doc" ? ({ deck: "Deck", report: "Report", newsletter: "Newsletter", release: "Release note", chart: "Chart" }[run.kind] || "Doc") : (run.type || "Run"), run.system].filter(Boolean);
      root.querySelector(".t-head").innerHTML = `
        <div class="t-brief" dir="auto">${esc(brief || RUN)}</div>
        <div class="t-meta">${chips.map(c => `<span class="chip">${esc(c)}</span>`).join("")}<span class="faint">${esc(run.date || "")}${run.closed ? " · closed" : ""}</span></div>
        <div class="t-row"><a class="btn ghost sm" target="_blank" href="${ctx.R(run.options[0] ? run.options[0].page : "")}">Open page ↗</a>
          <button class="btn ghost sm" data-act="copy" data-v="runs/${esc(RUN)}">Copy run path</button></div>`;
    }
    // ---------- title card: made by Flynn (cover.py); the frame on the canvas and this preview both follow run.cover
    let coverSig = "";
    function drawCover() {
      const c = run.cover || { state: "none" }, ready = c.state === "ready" || c.ready, working = c.state === "working";
      const sig = [c.state, c.time, c.ready].join("|");
      if (sig === coverSig && sec("cover").children.length) return;
      coverSig = sig;
      const how = c.copy === "flynn" ? `Written by Flynn · ${esc(ago(c.time))}` : c.copy === "fallback" ? `Written from the run's facts${c.copy_note ? ` (Flynn: ${esc(c.copy_note)})` : ""} · ${esc(ago(c.time))}` : "";
      sec("cover").innerHTML = `
        ${ready ? `<div class="t-cover"><img src="${ctx.R("cover/cover.png")}?v=${encodeURIComponent(c.time || "")}" alt="Title card"></div>` : ""}
        ${working ? `<p class="t-note">Flynn is writing the title card…</p>` : c.state === "error" ? `<p class="t-note t-err">${esc(c.error || "It failed.")}</p>` : ready ? `<p class="t-note">${how}</p>` : `<p class="t-note">A cover for this run: its kind, a title, the design system and small pictures of the designs.</p>`}
        <button class="btn ${ready ? "" : "primary"} sm t-wide ${working ? "busy" : ""}" data-act="cover" ${working ? "disabled" : ""}>${working ? "Generating…" : ready ? "Regenerate" : "Generate title card"}</button>`;
    }
    function drawDecision() {
      const d = run.decision;
      sec("decision").innerHTML = d
        ? `<div class="t-opt on"><span>${run.options.length > 1 ? "Option " + esc(ctx.label(d.option)) : "Current page"} is picked</span><span class="faint">${esc((d.time || "").replace("T", " ").slice(0, 16))}</span></div>${d.note ? `<p class="t-note" dir="auto">${esc(d.note)}</p>` : ""}`
        : `<p class="t-note">No option picked yet. Use “Pick this” above the frame you want.</p>`;
    }
    function drawLinks() {
      const L = run.links || [];
      root.querySelector('[data-sec="links"]').hidden = !L.length;
      sec("links").innerHTML = L.slice().reverse().map(l => `<a class="t-link" target="_blank" href="${esc(l.url)}"><span class="chip">${esc(l.kind)}</span><span>${esc(l.label)}</span>↗</a>`).join("");
    }
    function drawExport() {
      const cur = root.querySelector(".t-exopt") ? root.querySelector(".t-exopt").value : "";
      sec("export").innerHTML = `<select class="sel t-exopt">${run.options.map(o => `<option value="${esc(o.name)}">${run.options.length > 1 ? "Option " + esc(ctx.label(o.name)) : "Current page"}</option>`).join("")}</select>
        <div class="t-exports">${EXPORTS.map(x => `<button class="btn sm" data-act="export" data-kind="${x.kind}" title="${esc(x.note)}">${x.label}</button>`).join("")}</div>
        <div class="list">${[...run.exports.slice(-5).reverse().map(e => `<div class="row"><span class="pill">${esc(e.kind)}</span><span class="grow">${esc(e.path.split("/").pop())}</span><a class="link" href="${ctx.R(e.path)}" ${/\.(zip|pptx|eml)$/.test(e.path) ? "download" : 'target="_blank"'}>open</a></div>`),
          ...run.requests.slice().reverse().map(q => `<div class="row"><span class="pill ${q.status === "done" ? "green" : "red"}">${esc(q.kind)}</span><span class="grow">${esc(q.status === "waiting" ? "waiting for Flynn" : q.status === "failed" ? (q.reason || "failed") : q.status)}</span>${q.link ? `<a class="link" target="_blank" href="${esc(q.link)}">open</a>` : ""}</div>`)].join("")}</div>`;
      const sel = root.querySelector(".t-exopt"); if (cur) sel.value = cur; else if (run.decision) sel.value = run.decision.option;
    }
    function drawVersions() {
      sec("versions").innerHTML = (run.versions.length ? `<div class="list">${run.versions.slice().reverse().map(v => `<label class="row"><input type="checkbox" data-v="${esc(v.id)}" ${ctx.shownVersions.has(v.id) ? "checked" : ""}><span class="grow">${esc(v.label)}</span><small>${esc((v.time || "").slice(5, 16).replace("T", " "))}</small></label>`).join("")}</div>`
        : `<div class="t-empty">No versions yet. Flynn saves one after each review round.</div>`) + `<button class="btn ghost sm t-wide" data-act="snap">Save current as a version</button>`;
      sec("versions").querySelectorAll("input[data-v]").forEach(cb => cb.onchange = () => { cb.checked ? ctx.shownVersions.add(cb.dataset.v) : ctx.shownVersions.delete(cb.dataset.v); ctx.renderFrames(); });
    }

    return {
      render(r, cs, es) {
        run = r; comments = cs || []; edits = es || [];
        drawHead(); drawCover(); drawDecision(); drawComments(); drawEdits(); drawCode(); drawLinks(); drawExport(); drawVersions();
        if (editing) armFrames();
      },
      setAgent(a) { agent = a || "flynn"; if (run) drawCode(); },
    };
  }
  window.StudioTools = { mount };
})();
