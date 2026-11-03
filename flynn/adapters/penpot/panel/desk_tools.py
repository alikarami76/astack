"""flynn desk tools: the one list of things flynn can do in flynn desk, for every AI engine.

Each tool is a name, a description, a JSON schema for its input and a handler. The handler runs inside the panel
server, next to the conversation it belongs to, and returns a JSON-able dict. Engines only carry calls:

  - local Claude (Agent SDK or the claude CLI): `desk_mcp.py`, a stdio MCP server, lists these tools and forwards
    each call to POST /api/tool on the panel server, which runs `call()`.
  - the API engine (later): `schemas()` gives the tools in the Messages API format and the engine calls `call()`
    in-process in its own tool loop.

Two tools change the conversation instead of the canvas: `ask_user` shows a question card and `show_result` a
result card. They replace the NEEDS_ALIGNMENT and RESULT text blocks (still parsed, as a fallback).
Proposals (`propose_ds_change`, `prototype_propose`) never apply anything: the user approves them on their card.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[2]
PROTOS = Path.home() / ".flynn" / "prototypes"


class ToolError(Exception):
    """A tool failed in a way flynn should read and act on (the message goes back as the tool result)."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[Any, dict, Any], dict]      # (channel, args, server module) -> result


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None):
    def deco(fn):
        TOOLS[name] = Tool(name, description, {"type": "object", "properties": properties or {},
                                               "required": required or [], "additionalProperties": False}, fn)
        return fn
    return deco


def schemas(fmt: str = "mcp") -> list[dict]:
    """The tool list: `mcp` (name, description, inputSchema) or `api` (name, description, input_schema)."""
    key = "inputSchema" if fmt == "mcp" else "input_schema"
    return [{"name": t.name, "description": t.description, key: t.schema} for t in TOOLS.values()]


def call(name: str, ch, args: dict, srv) -> dict:
    t = TOOLS.get(name)
    if not t:
        raise ToolError(f"no flynn desk tool named {name}")
    missing = [k for k in t.schema.get("required", []) if k not in (args or {})]
    if missing:
        raise ToolError(f"{name} needs {', '.join(missing)}")
    return t.handler(ch, args or {}, srv)


def _canvas(ch):
    """The canvas connector, checked to be on this conversation's file."""
    from bridge import Bridge
    b = Bridge(timeout=60)
    cur = b.current()
    if not isinstance(cur, dict) or cur.get("file") != ch.file_id:
        name = (cur or {}).get("fileName") if isinstance(cur, dict) else None
        raise ToolError(f"The canvas connector is on another file ({name or 'none'}). Ask the user to bring this file's "
                        "flynn desk tab to the front (only one tab can hold flynn's connection).")
    return b


# ---------------------------------------------------------------- conversation
QUESTION = {"type": "object", "properties": {
    "q": {"type": "string", "description": "The question, in plain words."},
    "options": {"type": "array", "items": {"type": "string"}, "description": "2 to 5 answers to pick from."},
    "recommended": {"type": "string", "description": "Your recommended answer (one of the options, or several separated by ; when multi)."},
    "because": {"type": "string", "description": "One line: why you recommend it."},
    "multi": {"type": "boolean", "description": "True when the user may pick several."}},
    "required": ["q"], "additionalProperties": False}


@tool("ask_user", "Ask the user one or more questions as a card in the flynn tab (the brainstorm questions, the design "
      "contract to approve, or options to pick). After calling it, end your turn: his answers arrive as your next "
      "message, starting with ALIGNED.",
      {"stage": {"type": "string", "description": "brainstorm, contract, references, options or another short word."},
       "brief": {"type": "string", "description": "Optional text shown above the questions (for a contract: the whole contract)."},
       "questions": {"type": "array", "items": QUESTION, "minItems": 1}}, ["questions"])
def ask_user(ch, a, srv):
    qs = [{"n": i + 1, "q": q["q"], "recommended": q.get("recommended", ""), "because": q.get("because", ""),
           "options": q.get("options") or [], "multi": bool(q.get("multi"))} for i, q in enumerate(a["questions"])]
    ch.emit({"t": "card", "card": {"kind": "needs_alignment", "stage": a.get("stage") or "questions",
                                   "brief": a.get("brief") or "", "questions": qs}})
    ch.asked = True
    return {"shown": True, "next": "End your turn now. the user's answers come back as your next message."}


@tool("show_result", "Show the outcome of a piece of work as a result card in the flynn tab.",
      {"summary": {"type": "string", "description": "What was done, in two or three plain sentences."},
       "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
       "deliverables": {"type": "array", "items": {"type": "string"}, "description": "Pages, files or runs (runs/<id>)."},
       "caveats": {"type": "array", "items": {"type": "string"}}}, ["summary"])
def show_result(ch, a, srv):
    if a.get("status") == "blocked":
        ch.emit({"t": "card", "card": {"kind": "blocked", "reason": a["summary"], "needed": "; ".join(a.get("caveats") or [])}})
    else:
        ch.emit({"t": "card", "card": {"kind": "result", "status": a.get("status") or "done", "summary": a["summary"],
                                       "deliverables": a.get("deliverables") or [], "caveats": a.get("caveats") or []}})
    return {"shown": True}


# ---------------------------------------------------------------- canvas
@tool("canvas_selection", "What the user has selected on the canvas right now: names, types, ids and components.")
def canvas_selection(ch, a, srv):
    out = srv.selection_fn(ch)
    return {k: v for k, v in out.items() if k != "preview"}


@tool("canvas_build", "Build (or rebuild) a run's pages on the canvas now, one board per option, on the page "
      "\"flynn · <run>\". Boards the user edited are kept and the new build goes beside them. Normally this happens by "
      "itself after your turn; use it when you need the boards before you continue.",
      {"run": {"type": "string", "description": "The run id, e.g. 2026-10-06-cart-empty-state."}}, ["run"])
def canvas_build(ch, a, srv):
    run = srv.RUNS_DIR / Path(a["run"]).name
    if not run.is_dir():
        raise ToolError(f"no run folder runs/{run.name}")
    if run.name not in ch.runs:
        ch.runs.append(run.name)
        ch.save_state()
    ch.sync(force=True, only=run.name)
    return {"built": run.name, "pages": ch.pushed.get(run.name, {}).get("page") if isinstance(ch.pushed.get(run.name), dict) else None}


@tool("canvas_check", "Check your own build: build the run on the canvas now (unless build=false), then compare every "
      "board with the page it came from. Returns findings (texts missing on the board, layers that failed, height and "
      "region differences, fonts that didn't load, overflow) and writes <run>/canvas-check/: <option>-canvas.png (the "
      "board as drawn), <option>-page.png (the page in a browser) and <option>-diff.png (differences outlined in red). "
      "Read the canvas and diff PNGs, fix what is wrong, and check again before you tell the user the design is ready.",
      {"run": {"type": "string", "description": "The run id."},
       "build": {"type": "boolean", "description": "Build first (default true). False checks the boards already there."}},
      ["run"])
def canvas_check(ch, a, srv):
    import canvas_check as cc
    run = srv.RUNS_DIR / Path(a["run"]).name
    if not run.is_dir():
        raise ToolError(f"no run folder runs/{run.name}")
    if a.get("build", True):
        if run.name not in ch.runs:
            ch.runs.append(run.name)
            ch.save_state()
        ch.sync(force=True, only=run.name)
    with srv._lib_lock:                                  # the builder browser does one thing at a time
        s = cc.check(run)
    return {"ok": s["ok"], "findings": s["findings"], "build": s["build"],
            "pictures": {k: {x: f"runs/{run.name}/{v[x]}" for x in ("canvas", "page", "diff") if v.get(x)}
                         for k, v in s["boards"].items()},
            "next": "Read the diff and canvas pictures. Fix every real problem in the page (or say it is a canvas "
                    "builder limit), then run canvas_check again. At most three rounds."}


@tool("canvas_pull", "Read the user's canvas edits of a run back (a PNG and the texts of every board, and which boards he "
      "edited) into <run>/penpot-pull/. Do this before revising a design that is on the canvas.",
      {"run": {"type": "string"}}, ["run"])
def canvas_pull(ch, a, srv):
    import push
    run = srv.RUNS_DIR / Path(a["run"]).name
    if not run.is_dir():
        raise ToolError(f"no run folder runs/{run.name}")
    _canvas(ch)
    r = push.pull_run(run)
    return r if isinstance(r, dict) else {"pulled": str(run / "penpot-pull")}


# ---------------------------------------------------------------- design systems
@tool("libraries", "The design system libraries in flynn desk (each library is one design system), and the one "
      "chosen for this conversation.")
def libraries(ch, a, srv):
    return {"libraries": srv.systems_list(ch.file_id), "chosen": ch.system or None}


@tool("library_sync", "Bring every design system library on the canvas up to date with the store. Progress shows "
      "in the flynn tab.")
def library_sync(ch, a, srv):
    return {"started": bool(srv.sync_libraries(ch))}


@tool("propose_ds_change", "Propose a change to a design system (tokens, components, samples, or a recording's "
      "review). Nothing changes until the user approves the card. Ops: token.set {name, kind, value}, token.remove {name}, "
      "component.set {name, fields}, component.remove {name}, component.sample {name, variant, markup}, "
      "recording.apply {session}.",
      {"system": {"type": "string"}, "title": {"type": "string", "description": "One line, what changes."},
       "why": {"type": "string"}, "source": {"type": "string", "description": "chat or recording:<folder>"},
       "ops": {"type": "array", "items": {"type": "object"}, "minItems": 1}}, ["system", "title", "ops"])
def propose_ds_change(ch, a, srv):
    sys.path.insert(0, str(FLYNN / "adapters" / "library"))
    import changes
    try:
        c = changes.propose({**a, "file": ch.file_id})
    except ValueError as e:
        raise ToolError(str(e)) from e
    ch.note_changes()
    return {"proposed": c["id"], "next": "The card is in the flynn tab; the user approves or rejects it there."}


# ---------------------------------------------------------------- prototyping
@tool("prototype_outline", "Read a canvas page as screens for prototyping: every top-level board with its likely "
      "hotspots (buttons, links, tabs, rows: id, name, label text, position, current links) and the page's flows.",
      {"page": {"type": "string", "description": "Page name; empty for the page the user is on."}})
def prototype_outline(ch, a, srv):
    import prototype
    out = prototype.outline(_canvas(ch), a.get("page"))
    if not isinstance(out, dict):
        raise ToolError(str(out)[:300])
    if out.get("error"):
        raise ToolError(out["error"] + (f". Pages: {', '.join(out.get('pages', []))}" if out.get("pages") else ""))
    return out


LINK = {"type": "object", "properties": {
    "from": {"type": "string", "description": "Id of the shape the user clicks (a hotspot id from prototype_outline)."},
    "from_name": {"type": "string", "description": "Its readable name for the card, e.g. 'Pay button (Cart)'."},
    "action": {"type": "string", "enum": ["navigate", "overlay", "close", "back", "url"]},
    "to": {"type": "string", "description": "Target board id (navigate, overlay)."},
    "to_name": {"type": "string"},
    "trigger": {"type": "string", "enum": ["click", "hover", "after"]},
    "delay": {"type": "integer", "description": "ms, for trigger after."},
    "animation": {"type": "string", "enum": ["dissolve", "slide-left", "slide-right", "slide-up", "slide-down", "push-left", "push-right"]},
    "position": {"type": "string", "description": "Overlay position: center (default), top-left, top-center, ..."},
    "url": {"type": "string"}},
    "required": ["from"], "additionalProperties": False}


@tool("prototype_propose", "Propose how a page's screens link up as a clickable prototype: a named flow with its "
      "first screen, and one link per hotspot. the user sees the links on a card and nothing is applied until he approves; "
      "then he presses Play to click through. Link every primary action and every back or close; leave decorative "
      "shapes alone.",
      {"page": {"type": "string"}, "page_id": {"type": "string"},
       "flow": {"type": "string", "description": "Flow name, e.g. 'Checkout'."},
       "start": {"type": "string", "description": "Id of the first board."}, "start_name": {"type": "string"},
       "summary": {"type": "string", "description": "One line on what the prototype lets the user try."},
       "links": {"type": "array", "items": LINK, "minItems": 1}}, ["page", "links"])
def prototype_propose(ch, a, srv):
    import prototype
    plan = {"page": a["page"], "pageId": a.get("page_id"), "flow": a.get("flow"), "start": a.get("start"),
            "start_name": a.get("start_name"), "summary": a.get("summary") or "", "links": a["links"]}
    errs = prototype.validate(plan)
    if errs:
        raise ToolError("; ".join(errs))
    now = dt.datetime.now()
    plan.update({"id": now.strftime("%Y%m%d-%H%M%S") + "-" + (re.sub(r"[^a-z0-9]+", "-", (plan["flow"] or plan["page"]).lower()).strip("-")[:30] or "proto"),
                 "file": ch.file_id, "status": "pending", "created": now.isoformat(timespec="seconds")})
    PROTOS.mkdir(parents=True, exist_ok=True)
    (PROTOS / f"{plan['id']}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    ch.emit({"t": "prototype", "plan": card(plan)})
    return {"proposed": plan["id"], "next": "The card is in the flynn tab; the user approves it there, then presses Play."}


def card(plan: dict) -> dict:
    import prototype
    return {"id": plan["id"], "page": plan["page"], "flow": plan.get("flow"), "start": plan.get("start_name"),
            "summary": plan.get("summary"), "links": [prototype.describe(L) for L in plan["links"]], "status": plan["status"]}


def load_proto(pid: str) -> dict:
    if not re.fullmatch(r"[\w.-]+", pid or ""):
        raise ValueError("bad prototype id")
    return json.loads((PROTOS / f"{pid}.json").read_text(encoding="utf-8"))


def approve_proto(ch, pid: str) -> dict:
    import prototype
    plan = load_proto(pid)
    if plan["status"] != "pending":
        raise ValueError(f"this prototype is already {plan['status']}")
    r = prototype.apply(_canvas(ch), plan)
    if not isinstance(r, dict) or r.get("error"):
        raise ToolError((r or {}).get("error") if isinstance(r, dict) else str(r)[:300])
    plan.update({"status": "applied", "applied": r, "approved": dt.datetime.now().isoformat(timespec="seconds")})
    (PROTOS / f"{pid}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


def reject_proto(pid: str) -> dict:
    plan = load_proto(pid)
    plan["status"] = "rejected"
    (PROTOS / f"{pid}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


# ---------------------------------------------------------------- skills
SKILLS = FLYNN / "skills"
CLAUDE_SKILLS = Path.home() / ".claude" / "skills"
DRAFTS = Path.home() / ".flynn" / "skill-drafts"
SKILL_NAME = re.compile(r"^flynn-[a-z0-9][a-z0-9-]{1,40}$")


@tool("propose_skill", "Propose a new flynn skill (or a new version of one you made): a SKILL.md with the instructions, "
      "and optional reference files. It is installed only when the user approves the card, into skills/<name> and "
      "~/.claude/skills, and every later flynn turn can use it. Never write into skills/ yourself.",
      {"name": {"type": "string", "description": "flynn-<short-name>, lowercase, e.g. flynn-price-tag-review."},
       "description": {"type": "string", "description": "One or two sentences: what the skill does and exactly when to use it."},
       "body": {"type": "string", "description": "The SKILL.md body in markdown (no frontmatter): steps, rules, examples."},
       "files": {"type": "object", "description": "Optional reference files: {\"references/x.md\": \"content\"}.",
                 "additionalProperties": {"type": "string"}},
       "why": {"type": "string", "description": "One line for the user: what this skill will save or improve."}},
      ["name", "description", "body"])
def propose_skill(ch, a, srv):
    name = a["name"].strip()
    if not SKILL_NAME.match(name) or name in ("flynn-core", "flynn-review"):
        raise ToolError("name must be flynn-<short-name> (lowercase letters, digits, dashes), and not a core skill")
    files = a.get("files") or {}
    for rel, txt in files.items():
        if rel.startswith("/") or ".." in Path(rel).parts or rel == "SKILL.md" or len(txt) > 200_000:
            raise ToolError(f"bad file {rel}: relative paths inside the skill only, under 200 KB")
    if len(a["body"]) > 100_000:
        raise ToolError("the SKILL.md body is too long (100 KB at most)")
    exists = (SKILLS / name / "SKILL.md").exists()
    sid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + name
    d = DRAFTS / sid
    d.mkdir(parents=True, exist_ok=True)
    desc = " ".join(a["description"].split())
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {json.dumps(desc, ensure_ascii=False)}\n---\n\n{a['body'].strip()}\n", encoding="utf-8")
    for rel, txt in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(txt, encoding="utf-8")
    meta = {"id": sid, "name": name, "description": desc, "why": a.get("why") or "", "files": sorted(files), "update": exists,
            "file": ch.file_id, "status": "pending", "created": dt.datetime.now().isoformat(timespec="seconds")}
    (d / "draft.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    preview = a["body"].strip()[:900]
    ch.emit({"t": "skill", "skill": {**meta, "preview": preview}})
    return {"proposed": sid, "next": "The card is in the flynn tab; the skill is installed when the user approves it."}


def _draft(sid: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"[\w.-]+", sid or ""):
        raise ValueError("bad skill draft id")
    d = DRAFTS / sid
    return d, json.loads((d / "draft.json").read_text(encoding="utf-8"))


def approve_skill(sid: str) -> dict:
    import shutil
    d, meta = _draft(sid)
    if meta["status"] != "pending":
        raise ValueError(f"this skill draft is already {meta['status']}")
    dest = SKILLS / meta["name"]
    if dest.exists():
        shutil.move(str(dest), str(DRAFTS / f"{sid}.previous"))      # the old version is kept beside the draft
    shutil.copytree(d, dest, ignore=shutil.ignore_patterns("draft.json"))
    link = CLAUDE_SKILLS / meta["name"]
    if not link.exists() and not link.is_symlink():
        CLAUDE_SKILLS.mkdir(parents=True, exist_ok=True)
        link.symlink_to(dest)
    meta.update({"status": "installed", "installed": dt.datetime.now().isoformat(timespec="seconds"), "path": str(dest)})
    (d / "draft.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log = FLYNN / "docs" / "changelog.md"
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n- {meta['installed'][:10]}: skill `{meta['name']}` {'updated' if meta['update'] else 'added'} from flynn desk (approved by the user). {meta['description']}\n")
    except OSError:
        pass
    return meta


def reject_skill(sid: str) -> dict:
    d, meta = _draft(sid)
    meta["status"] = "rejected"
    (d / "draft.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def instructions() -> str:
    """What the panel context tells flynn about these tools (same for every engine)."""
    return ("- flynn desk tools (prefixed mcp__desk__ for you): use them instead of writing blocks or driving the canvas "
            "by hand. Ask questions with ask_user (then end your turn) instead of a NEEDS_ALIGNMENT block, and report "
            "with show_result instead of a RESULT or BLOCKED block. canvas_selection, canvas_build, canvas_check and "
            "canvas_pull work on this file. libraries, library_sync and propose_ds_change cover design systems (proposals need "
            "the user's approval). To make designs clickable: prototype_outline the page, then prototype_propose the links "
            "(a flow, its first screen, one link per primary action, back and close); the user approves the card and "
            "presses Play. To make a new flynn skill, draft it and call propose_skill; the user approves the card.\n")
