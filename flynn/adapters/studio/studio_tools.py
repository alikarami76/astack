"""The tools flynn gets inside Studio's chat. Each handler takes (conv, args, srv): conv is the chat.Conversation,
srv gives Studio's run functions (run_path, snapshot, export). Cards use flynn desk's shapes so both UIs agree."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "review"))

QUESTION = {"type": "object", "properties": {
    "q": {"type": "string", "description": "The question, in plain words."},
    "options": {"type": "array", "items": {"type": "string"}, "description": "2 to 5 answers to pick from."},
    "recommended": {"type": "string", "description": "Your recommended answer (one of the options, or several separated by ; when multi)."},
    "because": {"type": "string", "description": "One line: why you recommend it."},
    "multi": {"type": "boolean", "description": "True when the user may pick several."}},
    "required": ["q"], "additionalProperties": False}
_TOOLS: dict[str, dict] = {}


def tool(name, description, properties=None, required=None):
    def deco(fn):
        _TOOLS[name] = {"fn": fn, "schema": {"name": name, "description": description, "inputSchema": {
            "type": "object", "properties": properties or {}, "required": required or []}}}
        return fn
    return deco


def schemas() -> list[dict]:
    return [t["schema"] for t in _TOOLS.values()]


def call(name: str, conv, args: dict, srv) -> dict:
    if name not in _TOOLS:
        raise KeyError(f"unknown tool {name}")
    return _TOOLS[name]["fn"](conv, args or {}, srv)


def _run_of(conv, a: dict) -> str:
    """The run a tool acts on: the one named, else the conversation's own run, else the one in view."""
    rid = a.get("run") or (conv.key[4:] if str(getattr(conv, "key", "")).startswith("run:") else "") or (conv.view or {}).get("run")
    if not rid:
        raise KeyError("name the run (runs/<id>): this conversation isn't on a run")
    return rid


def _emit(conv, ev: dict) -> None:
    """Cards go through tool_emit when the conversation has it (only while its turn runs)."""
    (getattr(conv, "tool_emit", None) or conv.emit)(ev)


@tool("ask_user", "Ask the user one or more questions as a card in Studio's chat (brainstorm questions, the contract to "
      "approve, options to pick). After calling it, end your turn: his answers arrive as your next message, starting "
      "with ALIGNED.",
      {"stage": {"type": "string"}, "brief": {"type": "string"}, "questions": {"type": "array", "items": QUESTION, "minItems": 1}},
      ["questions"])
def ask_user(conv, a, srv):
    qs = [{"n": i + 1, "q": q["q"], "recommended": q.get("recommended", ""), "because": q.get("because", ""),
           "options": q.get("options") or [], "multi": bool(q.get("multi"))} for i, q in enumerate(a["questions"])]
    _emit(conv, {"t": "card", "card": {"kind": "needs_alignment", "stage": a.get("stage") or "questions",
                                     "brief": a.get("brief") or "", "questions": qs}})
    conv.asked = True
    return {"shown": True, "next": "End your turn now. the user's answers come back as your next message."}


@tool("show_result", "Show the outcome of a piece of work as a result card in Studio's chat.",
      {"summary": {"type": "string"}, "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
       "deliverables": {"type": "array", "items": {"type": "string"}}, "caveats": {"type": "array", "items": {"type": "string"}}},
      ["summary"])
def show_result(conv, a, srv):
    if a.get("status") == "blocked":
        _emit(conv, {"t": "card", "card": {"kind": "blocked", "reason": a["summary"], "needed": "; ".join(a.get("caveats") or [])}})
    else:
        _emit(conv, {"t": "card", "card": {"kind": "result", "status": a.get("status") or "done", "summary": a["summary"],
                                         "deliverables": a.get("deliverables") or [], "caveats": a.get("caveats") or []}})
    return {"shown": True}


@tool("studio_view", "What the user is looking at in Studio now: the run (its brief, type, options, versions, decision and open "
      "comments), the option, version and zoom in view, and the elements he selected on the canvas (selector, option, page, "
      "text, an HTML snippet and key styles).")
def studio_view(conv, a, srv):
    v = dict(conv.view or {})
    if conv.key.startswith("run:"):
        v.setdefault("run", conv.key[4:])
    if v.get("run") and hasattr(srv, "summary"):
        try:
            r = srv.summary(srv.run_path(v["run"]))
            v["about"] = {"brief": (r.get("request") or "")[:800], "type": r.get("type"), "system": r.get("system"),
                          "options": [o["name"] for o in r.get("options", [])],
                          "versions": [x.get("label") or x.get("id") for x in r.get("versions", [])][-8:],
                          "decision": r.get("decision"), "open_comments": r.get("comments_open"), "closed": r.get("closed")}
        except Exception:  # noqa: BLE001 - the view never fails because a summary did
            pass
    return v


@tool("studio_comments", "List, reply to, or resolve the user's pinned comments on a run (feedback.json).",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."},
       "action": {"type": "string", "enum": ["list", "reply", "resolve", "reopen"]},
       "n": {"type": "integer"}, "text": {"type": "string"}, "all": {"type": "boolean"}}, ["action"])
def studio_comments(conv, a, srv):
    import review
    run = srv.run_path(_run_of(conv, a))
    act = a["action"]
    if act == "list":
        cs = review.load(run).get("comments", [])
        return {"comments": cs if a.get("all") else [c for c in cs if c.get("status") == "open"]}
    n = int(a["n"])
    if act == "reply":
        return review.update(run, n, None, a.get("text") or "", author="flynn")
    if act == "resolve":
        return review.update(run, n, "resolved", a.get("text") or None, author="flynn")
    return review.update(run, n, "open", a.get("text") or None, author="flynn")


@tool("studio_snapshot", "Freeze a run's current options as a version (like `studio.py snapshot`).",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."}, "label": {"type": "string"},
       "option": {"type": "string"}})
def studio_snapshot(conv, a, srv):
    return {"version": srv.snapshot(srv.run_path(_run_of(conv, a)), a.get("option"), a.get("label", ""))}


@tool("studio_focus", "Open a run (and optionally an option or a version) on the user's Studio canvas.",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."}, "option": {"type": "string"},
       "version": {"type": "string"}})
def studio_focus(conv, a, srv):
    rid = _run_of(conv, a)
    srv.run_path(rid)
    _emit(conv, {"t": "focus", "run": rid, "option": a.get("option"), "version": a.get("version")})
    return {"shown": True}


@tool("studio_export", "Run a Studio export for a run: html, pdf, png, tsx, pptx, eml run at once; figma and canva "
      "are queued (process them in this turn per adapters/studio/README.md).",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."}, "kind": {"type": "string"},
       "option": {"type": "string"}}, ["kind"])
def studio_export(conv, a, srv):
    return srv.export(srv.run_path(_run_of(conv, a)), a["kind"], a.get("option") or "main")


@tool("studio_edits", "the user's direct edits on a run's frames (text changes, hidden elements, style values, notes) waiting "
      "for you to apply to the source page. List them, apply each to page/index.html (or the option's page) at its "
      "selector, then mark it applied (or rejected, with a reason).",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."},
       "action": {"type": "string", "enum": ["list", "applied", "rejected"]}, "n": {"type": "integer"},
       "note": {"type": "string"}}, ["action"])
def studio_edits(conv, a, srv):
    import edits
    run = srv.run_path(_run_of(conv, a))
    if a["action"] == "list":
        return {"pending": edits.pending(run)}
    return edits.set_status(run, int(a["n"]), a["action"], a.get("note"))


@tool("studio_link", "Attach a link to a run so it shows in Studio (a merge request, a Figma or Canva file, a doc).",
      {"run": {"type": "string", "description": "Run id; defaults to this conversation's run."},
       "url": {"type": "string"}, "label": {"type": "string"}, "kind": {"type": "string", "enum": ["mr", "figma", "canva", "doc", "link"]}},
      ["url"])
def studio_link(conv, a, srv):
    return srv.link(srv.run_path(_run_of(conv, a)), a["url"], a.get("label", ""), a.get("kind", "link"))



def _register_desk(name: str) -> None:
    """Offer one of flynn desk's tools as is (same name, description and inputs; its card renders in Studio too)."""
    sys.path.insert(0, str(HERE.parents[0] / "penpot" / "panel"))
    import desk_tools
    t = desk_tools.TOOLS[name]

    def fn(conv, a, srv):
        missing = [k for k in t.schema.get("required", []) if k not in a]
        if missing:
            raise ValueError(f"{name} needs {', '.join(missing)}")
        return t.handler(conv, a, srv)
    _TOOLS[name] = {"fn": fn, "schema": {"name": name, "description": t.description.replace("flynn tab", "Studio chat"),
                                         "inputSchema": t.schema}}


_register_desk("propose_skill")
