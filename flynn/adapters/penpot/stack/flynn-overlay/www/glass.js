// flynn glass theme: the sliding selection capsule for tab switchers.
// Every Penpot tab list gets one lit capsule behind its tabs. When the selection changes, the capsule glides
// to the new tab (CSS transitions, see tron.css). This file only measures the selected tab and sets four CSS
// variables on its list; all drawing and motion live in CSS, so nothing shows without the theme.
// Loaded by inject.js in the editor and by panel.html in the flynn tab.
//
// Cost: each tab list has its own observers (class changes inside that list, and its size), so canvas edits
// and other Penpot activity never trigger a measurement. New lists are found with one cheap query per frame
// at most, and only when nodes were added somewhere.
(function () {
  const LIST = 'main_ui_ds_layout_tab_switcher__tab-list';
  const SEL = 'main_ui_ds_layout_tab_switcher__selected';
  const watched = new WeakSet();
  let queued = false;
  // classList.add/remove always count as a mutation, even when nothing changes, so only touch a class
  // when it really has to change; otherwise a list would wake its own observer forever.
  const setClass = (el, c, want) => { if (el.classList.contains(c) !== want) el.classList.toggle(c, want); };

  const on = () => !!document.documentElement.dataset.flynnTheme;

  function place(ul) {
    if (!on()) return;
    const tab = ul.querySelector('.' + SEL);
    if (!tab || !tab.offsetWidth) { setClass(ul, 'fg-ind', false); if (ul.dataset.fgInd) delete ul.dataset.fgInd; return; }
    // Measure against the list itself (each tab sits in its own positioned <li>, so offsetLeft would be 0).
    const r = tab.getBoundingClientRect(), u = ul.getBoundingClientRect();
    const key = [r.left - u.left - ul.clientLeft, r.width, r.top - u.top - ul.clientTop, r.height].map((n) => Math.round(n * 2) / 2);
    if (ul.dataset.fgInd === key.join()) return;
    const first = !ul.dataset.fgInd;
    ul.dataset.fgInd = key.join();
    if (first) setClass(ul, 'fg-ind-still', true);        // first placement: appear in place, no slide
    ['x', 'w', 'y', 'h'].forEach((k, i) => ul.style.setProperty('--fg-ind-' + k, key[i] + 'px'));
    setClass(ul, 'fg-ind', true);
    if (first) requestAnimationFrame(() => requestAnimationFrame(() => setClass(ul, 'fg-ind-still', false)));
  }

  function watch(ul) {
    if (watched.has(ul)) return;
    watched.add(ul);
    // At most one measurement per list per frame, and never in response to our own edits on the list itself.
    let pending = false;
    const later = () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; place(ul); }); } };
    // Class changes (selection) and tabs added or removed (Inspect mode drops tabs) both move the capsule.
    new MutationObserver((recs) => { if (recs.some((r) => r.target !== ul || r.type === 'childList')) later(); })
      .observe(ul, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    new ResizeObserver(later).observe(ul);
    place(ul);
  }

  function scan() {
    queued = false;
    if (!on()) return;
    document.querySelectorAll('.' + LIST).forEach(watch);
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(scan); } }

  new MutationObserver((records) => { if (records.some((r) => r.addedNodes.length)) schedule(); })
    .observe(document.body || document.documentElement, { subtree: true, childList: true });
  // Theme switched on: place every capsule now.
  new MutationObserver(() => {
    document.querySelectorAll('.' + LIST).forEach((ul) => { if (ul.dataset.fgInd) delete ul.dataset.fgInd; });
    schedule();
    requestAnimationFrame(() => document.querySelectorAll('.' + LIST).forEach((ul) => watched.has(ul) && place(ul)));
  })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-flynn-theme'] });
  schedule();
})();
