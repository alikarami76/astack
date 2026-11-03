"""Prototyping on the flynn desk canvas: read a page's screens, then wire them up as a clickable prototype.

flynn (any engine) calls the `prototype_outline` tool to see every top-level board on a page with its likely
hotspots (buttons, links, tabs, rows: small shapes with a short label), proposes the links with
`prototype_propose`, and the user approves the card. Only then `apply` writes the interactions through the canvas
connector: a named flow with its starting screen, then one interaction per link.

Link actions (Penpot's prototype model):
  navigate   go to another board              {to, animation?}
  overlay    open a board as a pop-up          {to, position?: center|top-left|..., close_outside?: true, backdrop?: true}
  close      close the open pop-up
  back       previous screen
  url        open a web address                {url}
Triggers: click (default), hover (mouse-enter), after (after-delay, with delay ms).
Animations: dissolve, slide-left, slide-right, slide-up, slide-down, push-left, push-right (300 ms ease-out).
"""
from __future__ import annotations

import json

OUTLINE_JS = r"""
const WANT = __PAGE__;
const page = WANT ? penpot.currentFile.pages.find((p) => p.name === WANT || p.id === WANT) : penpot.currentPage;
if (!page) return { error: 'no page named ' + WANT, pages: penpot.currentFile.pages.map((p) => p.name) };
if (page.id !== penpot.currentPage.id) await penpot.openPage(page);
const ROOT = '00000000-0000-0000-0000-000000000000';
const all = penpot.currentPage.findShapes();
const boards = all.filter((s) => s.type === 'board' && s.parent && s.parent.id === ROOT);
const label = (s, n = 0) => {
  if (s.type === 'text') return (s.characters || '').trim();
  if (!s.children || n > 4) return '';
  return s.children.map((c) => label(c, n + 1)).filter(Boolean).join(' ').slice(0, 60);
};
const ints = (s) => (s.interactions || []).map((i) => ({ trigger: i.trigger, action: i.action.type,
  to: i.action.destination ? i.action.destination.name : undefined }));
const out = boards.map((b) => {
  const spots = [];
  const walk = (s, depth) => {
    if (spots.length >= 80 || depth > 7) return;
    for (const c of (s.children || [])) {
      const small = c.width <= Math.max(640, b.width * 0.9) && c.height <= 140 && c.width >= 12 && c.height >= 12;
      const lab = label(c);
      const container = c.children && c.children.length;
      const named = /button|btn|link|tab|chip|back|close|cta|menu|item|row|card|icon|nav|دکمه/i.test(c.name);
      if (small && (named || (container && lab && lab.length <= 40) || (c.type === 'text' && lab.length <= 30))) {
        spots.push({ id: c.id, name: c.name, type: c.type, label: lab, x: Math.round(c.x - b.x), y: Math.round(c.y - b.y),
                     w: Math.round(c.width), h: Math.round(c.height), component: c.isComponentInstance && c.isComponentInstance() ? (c.component() || {}).name : undefined,
                     interactions: ints(c) });
        if (container && named) continue;          // a button: its own label text is not a separate hotspot
      }
      walk(c, depth + 1);
    }
  };
  walk(b, 0);
  return { id: b.id, name: b.name, x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height),
           interactions: ints(b), hotspots: spots };
});
return { page: page.name, pageId: page.id, flows: (penpot.currentPage.flows || []).map((f) => ({ name: f.name, start: f.startingBoard && f.startingBoard.name })),
         boards: out };
"""

APPLY_JS = r"""
const P = __PLAN__;
const page = penpot.currentFile.pages.find((p) => p.id === P.pageId || p.name === P.page);
if (!page) return { error: 'the page is gone: ' + P.page };
if (page.id !== penpot.currentPage.id) await penpot.openPage(page);
const byId = (id) => penpot.currentPage.getShapeById(id);
const ANIM = {
  'dissolve': { type: 'dissolve', duration: 300, easing: 'ease-out' },
  'slide-left': { type: 'slide', way: 'in', direction: 'left', duration: 300, easing: 'ease-out' },
  'slide-right': { type: 'slide', way: 'in', direction: 'right', duration: 300, easing: 'ease-out' },
  'slide-up': { type: 'slide', way: 'in', direction: 'up', duration: 300, easing: 'ease-out' },
  'slide-down': { type: 'slide', way: 'in', direction: 'down', duration: 300, easing: 'ease-out' },
  'push-left': { type: 'push', direction: 'left', duration: 300, easing: 'ease-out' },
  'push-right': { type: 'push', direction: 'right', duration: 300, easing: 'ease-out' },
};
const TRIG = { click: 'click', hover: 'mouse-enter', after: 'after-delay' };
const res = { applied: [], skipped: [], flow: null };
// The named flow first: Penpot makes a "Flow 1" by itself when a page gets its first link and has no flow.
if (P.flow && P.start) {
  const start = byId(P.start);
  const flows = penpot.currentPage.flows || [];
  let f = flows.find((x) => x.name === P.flow);
  if (!f && start) f = penpot.currentPage.createFlow(P.flow, start);
  res.flow = f ? f.name : null;
}
for (const L of P.links) {
  const from = byId(L.from);
  if (!from) { res.skipped.push({ link: L, why: 'the shape is gone' }); continue; }
  const to = L.to ? byId(L.to) : null;
  let action;
  if (L.action === 'navigate') action = to && { type: 'navigate-to', destination: to, ...(L.animation && ANIM[L.animation] ? { animation: ANIM[L.animation] } : {}) };
  else if (L.action === 'overlay') action = to && { type: 'open-overlay', destination: to, position: L.position || 'center',
      closeWhenClickOutside: L.close_outside !== false, addBackgroundOverlay: L.backdrop !== false,
      ...(L.animation && ANIM[L.animation] ? { animation: ANIM[L.animation] } : {}) };
  else if (L.action === 'close') action = { type: 'close-overlay', ...(to ? { destination: to } : {}) };
  else if (L.action === 'back') action = { type: 'previous-screen' };
  else if (L.action === 'url') action = L.url && { type: 'open-url', url: L.url };
  if (!action) { res.skipped.push({ link: L, why: 'the target board is gone or the action is incomplete' }); continue; }
  const trig = TRIG[L.trigger || 'click'] || 'click';
  // Replace what this shape already does on the same trigger, so applying twice never doubles a link.
  for (const i of [...(from.interactions || [])]) if (i.trigger === trig) { try { from.removeInteraction(i); } catch (e) {} }
  try { from.addInteraction(trig, action, trig === 'after-delay' ? (L.delay || 1000) : undefined); res.applied.push(L.from_name || from.name); }
  catch (e) { res.skipped.push({ link: L, why: String(e).slice(0, 160) }); }
}
const fl = penpot.currentPage.flows || [];
if (P.flow && fl.length > 1) for (const f of fl) if (/^Flow \d+$/.test(f.name) && f.name !== P.flow) { try { f.remove(); } catch (e) {} }
res.flows = (penpot.currentPage.flows || []).map((f) => f.name);
return res;
"""

ACTIONS = {"navigate", "overlay", "close", "back", "url"}
TRIGGERS = {"click", "hover", "after"}
ANIMATIONS = {"dissolve", "slide-left", "slide-right", "slide-up", "slide-down", "push-left", "push-right"}


def _js(src: str, **kw) -> str:
    for k, v in kw.items():
        src = src.replace(f"__{k}__", json.dumps(v, ensure_ascii=False))
    return src


def outline(bridge, page: str | None = None) -> dict:
    return bridge.run_js(_js(OUTLINE_JS, PAGE=page or ""))


def validate(plan: dict) -> list[str]:
    errs = []
    if not plan.get("page") and not plan.get("pageId"):
        errs.append("a prototype needs the page")
    links = plan.get("links")
    if not isinstance(links, list) or not links:
        errs.append("a prototype needs at least one link")
    for i, L in enumerate(links or []):
        if not L.get("from"):
            errs.append(f"link {i + 1}: from (the shape id) is missing")
        a = L.get("action", "navigate")
        if a not in ACTIONS:
            errs.append(f"link {i + 1}: action must be one of {', '.join(sorted(ACTIONS))}")
        if a in ("navigate", "overlay") and not L.get("to"):
            errs.append(f"link {i + 1}: {a} needs to (the target board id)")
        if a == "url" and not L.get("url"):
            errs.append(f"link {i + 1}: url needs the address")
        if L.get("trigger", "click") not in TRIGGERS:
            errs.append(f"link {i + 1}: trigger must be click, hover or after")
        if L.get("animation") and L["animation"] not in ANIMATIONS:
            errs.append(f"link {i + 1}: animation must be one of {', '.join(sorted(ANIMATIONS))}")
    if plan.get("flow") and not plan.get("start"):
        errs.append("a named flow needs start (the first board's id)")
    return errs


def apply(bridge, plan: dict) -> dict:
    errs = validate(plan)
    if errs:
        raise ValueError("; ".join(errs))
    plan = {**plan, "links": [{"action": "navigate", "trigger": "click", **L} for L in plan["links"]]}
    return bridge.run_js(_js(APPLY_JS, PLAN=plan))


def describe(L: dict) -> str:
    a = L.get("action", "navigate")
    src = L.get("from_name") or L.get("from")
    on = {"hover": "hover on ", "after": "after a delay, "}.get(L.get("trigger", "click"), "")
    tail = {"navigate": f"→ {L.get('to_name') or L.get('to')}", "overlay": f"opens {L.get('to_name') or L.get('to')} as a pop-up",
            "close": "closes the pop-up", "back": "goes back", "url": f"opens {L.get('url')}"}[a]
    return f"{on}{src} {tail}" + (f" · {L['animation']}" if L.get("animation") else "")
