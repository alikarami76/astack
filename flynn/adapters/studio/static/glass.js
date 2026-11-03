/* flynn Studio glass motion and the chat dock, shared by every page.
   1. Sliding indicators: every .seg, .areas and .side .tabs gets a glass pill that glides to the active tab.
   2. FlynnGlass.dock(...): makes the chat panel resizable (drag its inner edge), closable (header button), and re-openable
      (floating Chat button, Cmd/Ctrl+J). Hiding the panel never unmounts it, so the conversation keeps streaming. */
(() => {
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

  // ---------- 1. sliding indicators
  const SEL = ".seg, .areas, .side .tabs";
  function place(box) {
    const ind = box._ind, on = [...box.children].find(c => c.tagName === "BUTTON" && c.classList.contains("on"));
    if (!on || !on.offsetWidth) { ind.classList.remove("ready"); box.classList.remove("has-ind"); return; }
    const first = !ind.classList.contains("ready");
    if (first) ind.style.transition = "none";
    ind.style.width = on.offsetWidth + "px"; ind.style.height = on.offsetHeight + "px";
    ind.style.transform = `translate(${on.offsetLeft}px, ${on.offsetTop}px)`;
    box.classList.add("has-ind"); ind.classList.add("ready");
    if (first) { ind.offsetWidth; ind.style.transition = ""; }
  }
  function setup(box) {
    if (box._ind) return;
    const ind = document.createElement("i"); ind.className = "seg-ind"; box._ind = ind;
    if (getComputedStyle(box).position === "static") box.style.position = "relative";
    box.insertBefore(ind, box.firstChild);
    const again = () => requestAnimationFrame(() => place(box));
    new MutationObserver(muts => { if (muts.some(m => m.type === "childList" && [...m.addedNodes, ...m.removedNodes].every(n => n === ind))) return; again(); })
      .observe(box, { attributes: true, attributeFilter: ["class", "hidden"], subtree: true, childList: true });
    if (window.ResizeObserver) { const ro = new ResizeObserver(again); ro.observe(box); }
    again(); document.fonts && document.fonts.ready.then(again);
  }
  const scan = () => document.querySelectorAll(SEL).forEach(setup);
  scan(); addEventListener("DOMContentLoaded", scan); addEventListener("load", scan);
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });

  // ---------- 2. the chat dock
  const CHAT = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z"/></svg>';
  const XICON = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>';
  /* o: { type, shell, side, edge: "right"|"left", def, min, label, onChange }
     shell gets --side-w and the class "side-closed"; side must have a .resize child (created if missing). */
  function dock(o) {
    const { shell, side } = o, min = o.min || 320, left = o.edge === "left", KEYO = `studio.chat.open.${o.type}`, KEYW = `studio.chat.w.${o.type}`;
    const maxW = () => Math.max(min, Math.round(innerWidth * 0.6));
    const clamp = w => Math.min(maxW(), Math.max(min, w));
    let handle = side.querySelector(":scope > .resize");
    if (!handle) { handle = document.createElement("div"); handle.className = "resize"; side.prepend(handle); }
    handle.setAttribute("role", "separator"); handle.setAttribute("aria-orientation", "vertical"); handle.title = "Drag to resize";
    handle.tabIndex = 0;
    let w = +store.get(KEYW) || +(o.legacyKey && store.get(o.legacyKey)) || o.def;
    const setW = (x, save) => { w = clamp(x); shell.style.setProperty("--side-w", w + "px"); if (save) store.set(KEYW, String(w)); };
    setW(w, false);
    addEventListener("resize", () => setW(w, false));

    // open / close
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "chat-fab float"; btn.hidden = true;
    btn.innerHTML = `${CHAT}<span>${o.label || "Chat"}</span><span class="kbd">${/Mac|iP/.test(navigator.platform) ? "⌘J" : "Ctrl J"}</span>`;
    btn.title = `Open ${(o.label || "chat").toLowerCase()} (${/Mac|iP/.test(navigator.platform) ? "Cmd" : "Ctrl"}+J)`;
    if (left) btn.classList.add("left");
    document.body.appendChild(btn);
    let open = store.get(KEYO) !== "0";
    function set(v, save = true, focus = false) {
      open = v; shell.classList.toggle("side-closed", !v); side.inert = !v; btn.hidden = v;
      if (save) store.set(KEYO, v ? "1" : "0");
      if (!v && side.contains(document.activeElement)) document.activeElement.blur();
      if (v && focus) setTimeout(() => { const t = side.querySelector("textarea"); if (t && t.offsetParent) t.focus(); }, 60);
      if (o.onChange) o.onChange(v);
    }
    btn.addEventListener("click", () => set(true, true, true));
    addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "j") { e.preventDefault(); set(!open, true, true); } });

    // the close button lives in the chat header (re-added if the chat re-renders its header)
    function ensureClose() {
      const host = side.querySelector(".tabs:not([hidden])") || side.querySelector(".fc-head");
      if (!host || host.querySelector(":scope > .dock-close")) return;
      const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost sm icon dock-close";
      b.title = `Hide ${(o.label || "chat").toLowerCase()} (${/Mac|iP/.test(navigator.platform) ? "Cmd" : "Ctrl"}+J)`; b.setAttribute("aria-label", "Hide chat panel"); b.innerHTML = XICON;
      b.addEventListener("click", () => set(false));
      host.appendChild(b);
    }
    ensureClose();
    new MutationObserver(ensureClose).observe(side, { childList: true, subtree: true });

    // drag the inner edge
    handle.addEventListener("pointerdown", e => {
      e.preventDefault(); e.stopPropagation(); handle.setPointerCapture(e.pointerId);
      shell.classList.add("dragging"); document.body.classList.add("resizing");
      const move = m => setW(left ? m.clientX - shell.getBoundingClientRect().left : innerWidth - m.clientX, false);
      const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); handle.removeEventListener("pointercancel", up);
        shell.classList.remove("dragging"); document.body.classList.remove("resizing"); store.set(KEYW, String(w)); };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("dblclick", () => setW(o.def, true));
    handle.addEventListener("keydown", e => { const d = e.key === "ArrowLeft" ? 1 : e.key === "ArrowRight" ? -1 : 0; if (d) { e.preventDefault(); setW(w + d * (left ? -1 : 1) * 24, true); } });

    set(open, false);
    return { open: () => set(true), close: () => set(false), toggle: () => set(!open), isOpen: () => open };
  }
  window.FlynnGlass = { dock, placeAll: () => document.querySelectorAll(SEL).forEach(b => b._ind && place(b)) };
})();
