"""Design tools any AI can use in Studio: the MCP server (mcp_server.py) and the model-API engine (api_engine.py) both
call these. Each works inside the caller's own runs folder (fpaths.runs_dir(), which on a team server is the signed-in
person's own) and never outside it; files are limited to a run's page folders and to text formats.

  find      studio_list_runs, studio_get_run, studio_read_file, studio_list_systems, studio_get_system
  change    studio_create_run, studio_write_file, studio_edit_file, studio_add_option
  check     studio_render (a screenshot the AI can see, and the layout checks), studio_lint
  review    studio_comments, studio_snapshot, studio_pick, studio_export
  Flynn     flynn_ask (hand a request to Flynn itself, in a run's or Home's conversation, and wait for its reply)

A handler takes (ctx, args) and returns a dict; ctx is Ctx (who is calling, the Studio address, Studio's run functions).
A handler may return {"_image": {"mime": ..., "data": <base64>}, ...}: the MCP server sends it as an image.
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path[:0] = [str(FLYNN / "scripts"), str(HERE)]
import fpaths  # noqa: E402

TEXT_EXT = {".html", ".css", ".svg", ".md", ".json", ".txt", ".js"}
PAGE_PATH = re.compile(r"^(page|options/[a-z0-9][a-z0-9-]{0,40}/page)/[A-Za-z0-9._/-]+$")
READ_EXTRA = re.compile(r"^(run\.json|contract\.md|content\.md|content-brief\.md|feedback\.md|TEMPLATE\.md|docs/[A-Za-z0-9._/-]+)$")
OPTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RUN_TYPES = ("ui", "deck", "graphic", "chart")
MAX_FILE = 2 * 1024 * 1024
_TOOLS: dict[str, dict] = {}


@dataclass
class Ctx:
    me: dict | None = None          # the person (team server) or None (Studio on this computer)
    base_url: str = ""              # Studio's address, for links
    srv: object = None              # chat.Srv: Studio's run functions
    notes: list = field(default_factory=list)


def tool(name: str, description: str, properties: dict | None = None, required: list | None = None, writes: bool = False):
    def deco(fn):
        _TOOLS[name] = {"fn": fn, "writes": writes, "schema": {"name": name, "description": description,
                        "inputSchema": {"type": "object", "properties": properties or {}, "required": required or []}}}
        return fn
    return deco


def schemas(include: set | None = None) -> list[dict]:
    return [t["schema"] for n, t in _TOOLS.items() if include is None or n in include]


def call(name: str, ctx: Ctx, args: dict) -> dict:
    if name not in _TOOLS:
        raise KeyError(f"unknown tool {name}")
    with fpaths.use_runs(_runs_root(ctx)):
        return _TOOLS[name]["fn"](ctx, args or {})


def _runs_root(ctx: Ctx):
    if ctx.me and ctx.me.get("uid"):
        import team
        return team.runs_of(ctx.me)
    return None


# ---------------------------------------------------------------- paths
def _run(rid: str) -> Path:
    root = fpaths.runs_dir().resolve()
    p = (root / str(rid or "")).resolve()
    if root not in p.parents or not p.is_dir():
        raise KeyError(f"no run {rid!r}; studio_list_runs shows the runs you can use")
    return p


def _file(run: Path, rel: str, write: bool) -> Path:
    rel = str(rel or "").lstrip("/")
    if ".." in Path(rel).parts or not (PAGE_PATH.match(rel) or (not write and READ_EXTRA.match(rel))):
        raise ValueError("files are limited to a run's page/ and options/<name>/page/ folders"
                         + ("" if write else " (and run.json, contract.md, content.md, feedback.md, docs/)"))
    if Path(rel).suffix.lower() not in TEXT_EXT:
        raise ValueError(f"only text files: {', '.join(sorted(TEXT_EXT))}")
    return run / rel


def _page_dir(run: Path, option: str) -> Path:
    if option in ("", "main", None):
        return run / "page"
    if not OPTION.match(option):
        raise ValueError("an option name is lowercase letters, digits and dashes")
    return run / "options" / option / "page"


def _url(ctx: Ctx, run: str, option: str = "") -> str:
    return f"{ctx.base_url}/run/{run}" + (f"?option={option}" if option and option != "main" else "")


# ---------------------------------------------------------------- find
@tool("studio_list_runs", "List your Studio runs (designs, decks, reports, charts), newest first. Each has its id, type, "
      "design system, request, options, open comments and whether an option was picked.",
      {"query": {"type": "string", "description": "Only runs whose id or request contains this text."},
       "limit": {"type": "integer", "description": "At most this many (default 30)."}})
def list_runs(ctx, a):
    rows = ctx.srv.list_runs() if ctx.srv and hasattr(ctx.srv, "list_runs") else []
    q = str(a.get("query") or "").lower()
    out = [{"id": r["id"], "type": r.get("type"), "kind": r.get("kind"), "system": r.get("system"),
            "request": (r.get("request") or "")[:200], "options": [o["name"] for o in r.get("options", [])],
            "comments_open": r.get("comments_open"), "picked": (r.get("decision") or {}).get("option"),
            "url": _url(ctx, r["id"])} for r in rows if not q or q in r["id"].lower() or q in (r.get("request") or "").lower()]
    return {"runs": out[: int(a.get("limit") or 30)]}


@tool("studio_get_run", "One run in detail: its brief, options (with their files), versions, comments, edits waiting "
      "to be applied, the picked option and exports.", {"run": {"type": "string"}}, ["run"])
def get_run(ctx, a):
    run = _run(a["run"])
    s = ctx.srv.summary(run) if ctx.srv else {}
    files = {}
    for o in s.get("options", []):
        d = run / Path(o["page"]).parent
        files[o["name"]] = sorted(str(f.relative_to(run)) for f in d.rglob("*") if f.is_file() and f.suffix.lower() in TEXT_EXT)[:60]
    import review
    comments = [{"n": c["n"], "text": c["text"], "status": c["status"], "author": c.get("author"),
                 "replies": [r.get("text") for r in c.get("replies", [])]} for c in review.load(run).get("comments", [])]
    return {"id": run.name, "type": s.get("type"), "system": s.get("system"), "request": s.get("request"),
            "options": [o["name"] for o in s.get("options", [])], "files": files,
            "versions": [v["id"] for v in s.get("versions", [])], "comments": comments,
            "edits_pending": s.get("edits_pending"), "picked": s.get("decision"), "exports": s.get("exports", [])[-5:],
            "url": _url(ctx, run.name)}


@tool("studio_read_file", "Read a text file of a run: page/index.html, page/style.css, options/<name>/page/..., or "
      "run.json, contract.md, content.md, feedback.md, docs/...", {"run": {"type": "string"}, "path": {"type": "string"}},
      ["run", "path"])
def read_file(ctx, a):
    f = _file(_run(a["run"]), a["path"], write=False)
    if not f.is_file():
        raise KeyError(f"no file {a['path']} in {a['run']}")
    if f.stat().st_size > MAX_FILE:
        raise ValueError("that file is too large to read here")
    return {"path": a["path"], "content": f.read_text(encoding="utf-8", errors="replace")}


@tool("studio_list_systems", "The design systems Studio designs from (Admin Panel, DK Editorial, ...), with what "
      "each holds. Design systems are maintained by the admin; use them, don't change them.")
def list_systems(ctx, a):
    import systems
    return {"systems": [{"id": s["id"], "name": s.get("name"), "kind": s.get("kind"), "counts": s.get("counts")}
                        for s in systems.list_systems()]}


@tool("studio_get_system", "One design system: its guide (how to design with it), tokens (colours, type, spacing), "
      "components (with their usage rules), or its product knowledge: pages (each page's place in the navigation, what "
      "it is for, its actions, data and states), features (functionalities and their business rules) and flows (UX "
      "journeys step by step). Read the guide, and the pages, features and flows your design touches, before designing.",
      {"system": {"type": "string"},
       "part": {"type": "string", "enum": ["guide", "tokens", "components", "pages", "features", "flows"]},
       "route": {"type": "string", "description": "pages: one page and the pages under it (e.g. /orders)."},
       "query": {"type": "string", "description": "pages, features, flows: only those mentioning this (English or Persian)."}},
      ["system"])
def get_system(ctx, a):
    import systems
    sid = str(a["system"])
    d = systems.system_detail(sid)
    part = a.get("part") or "guide"
    if part == "guide":
        g = systems._guide(systems._folder(sid, d.get("kind") or "library"))
        text = g.read_text(encoding="utf-8", errors="replace") if g else ""
        return {"system": sid, "guide": text[:60000] or "This system has no guide yet.",
                **({"theme_files": d["theme_files"]} if d.get("theme_files") else {})}
    if part == "tokens":
        return {"system": sid, "tokens": d.get("tokens", [])[:400]}
    if part in ("pages", "features", "flows"):
        rows = d.get({"pages": "screens"}.get(part, part), [])
        q, route = str(a.get("query") or "").lower(), str(a.get("route") or "").rstrip("/")
        if route and part == "pages":
            rows = [r for r in rows if r.get("route") == route or str(r.get("route", "")).startswith(route + "/")
                    or r.get("parent") == route]
        if q:
            rows = [r for r in rows if q in json.dumps(r, ensure_ascii=False).lower()]
        out = {"system": sid, part: rows[:120], "total": len(rows)}
        if part == "pages" and not route and not q and d.get("navigation"):
            out["navigation"] = d["navigation"]
        return out
    return {"system": sid, "components": d.get("components", [])[:200], "patterns": d.get("patterns", [])}


# ---------------------------------------------------------------- change
@tool("studio_create_run", "Start a new design in Studio. Gives you a run with page/index.html, from a template and "
      "theme (release-page, report, report-a4, deck, newsletter with editorial or dk-mckinsey; handbook, guide and newsletter with dk-publication), or your own HTML. Then "
      "edit it with studio_write_file / studio_edit_file and check it with studio_render.",
      {"title": {"type": "string", "description": "Short name, e.g. 'Periodic price redesign'."},
       "request": {"type": "string", "description": "What the design is for, in a sentence or two."},
       "type": {"type": "string", "enum": list(RUN_TYPES)},
       "system": {"type": "string", "description": "The design system id (studio_list_systems): a library or a document "
                  "theme (recorded as the run's theme). \"none\" means no design system. Leave out to let Flynn choose."},
       "template": {"type": "string", "description": "An HTML template to start from (optional)."},
       "theme": {"type": "string", "description": "The theme for the template (default editorial)."},
       "html": {"type": "string", "description": "Your own page instead of a template (optional)."},
       "css": {"type": "string", "description": "page/style.css with your own HTML (optional)."}},
      ["title", "type"], writes=True)
def create_run(ctx, a):
    import run_record
    slug = re.sub(r"[^a-z0-9]+", "-", str(a["title"]).lower()).strip("-")[:48] or "design"
    import systems
    sysarg = str(a.get("system") or "").strip()
    run = run_record.new(slug, a.get("type") or "ui", str(a.get("request") or a["title"]),
                         "" if sysarg == "none" else sysarg, runs_root=fpaths.runs_dir())
    if sysarg:
        systems.record_choice(run, sysarg)        # a document theme also goes in as run.json's theme; none as design_system
    default_theme = sysarg if sysarg in systems._theme_ids() else "editorial"
    if a.get("template"):
        r = subprocess.run([sys.executable, str(FLYNN / "adapters" / "html" / "new_page.py"), str(run), "--template",
                            str(a["template"]), "--theme", str(a.get("theme") or default_theme)], capture_output=True, text=True, timeout=120)
        if r.returncode:
            shutil.rmtree(run, ignore_errors=True)
            raise ValueError((r.stderr or r.stdout).strip()[-300:])
    else:
        (run / "page").mkdir(exist_ok=True)
        (run / "page" / "index.html").write_text(str(a.get("html") or
            "<!doctype html><html><head><meta charset=\"utf-8\"><link rel=\"stylesheet\" href=\"style.css\"></head><body></body></html>"), encoding="utf-8")
        (run / "page" / "style.css").write_text(str(a.get("css") or ""), encoding="utf-8")
    return {"run": run.name, "files": sorted(str(f.relative_to(run)) for f in (run / "page").glob("*") if f.is_file()),
            "url": _url(ctx, run.name)}


@tool("studio_write_file", "Write (create or replace) a text file in a run's page folders, e.g. page/index.html or "
      "options/b/page/style.css. The person sees the change on Studio's canvas at once. Set snapshot to keep the "
      "current state as a version first.",
      {"run": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"},
       "snapshot": {"type": "boolean", "description": "Save the current option as a version before writing."}},
      ["run", "path", "content"], writes=True)
def write_file(ctx, a):
    run = _run(a["run"])
    f = _file(run, a["path"], write=True)
    data = str(a["content"])
    if len(data.encode()) > MAX_FILE:
        raise ValueError("files are limited to 2 MB")
    if a.get("snapshot") and ctx.srv:
        ctx.srv.snapshot(run, None, "before an AI edit")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(data, encoding="utf-8")
    return {"written": a["path"], "bytes": len(data.encode()), "url": _url(ctx, run.name)}


@tool("studio_edit_file", "Replace one exact piece of text in a run's page file (old must appear exactly once).",
      {"run": {"type": "string"}, "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
      ["run", "path", "old", "new"], writes=True)
def edit_file(ctx, a):
    run = _run(a["run"])
    f = _file(run, a["path"], write=True)
    if not f.is_file():
        raise KeyError(f"no file {a['path']}")
    text = f.read_text(encoding="utf-8")
    n = text.count(str(a["old"]))
    if n != 1:
        raise ValueError(f"old text found {n} times; it must appear exactly once (include more around it)")
    f.write_text(text.replace(str(a["old"]), str(a["new"]), 1), encoding="utf-8")
    return {"edited": a["path"], "url": _url(ctx, run.name)}


@tool("studio_add_option", "Add another option to a run (a copy of an existing option's page to change), so the "
      "person can compare them side by side on the canvas.",
      {"run": {"type": "string"}, "name": {"type": "string", "description": "e.g. b, c, compact"},
       "from_option": {"type": "string", "description": "The option to copy (default main)."}}, ["run", "name"], writes=True)
def add_option(ctx, a):
    run = _run(a["run"])
    name = str(a["name"])
    if not OPTION.match(name) or name == "main":
        raise ValueError("an option name is lowercase letters, digits and dashes (not main)")
    dst = _page_dir(run, name)
    if dst.exists():
        raise ValueError(f"option {name} exists already")
    src = _page_dir(run, str(a.get("from_option") or "main"))
    if not src.is_dir():
        raise KeyError("no such option to copy")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.pdf"))
    return {"option": name, "files": sorted(str(f.relative_to(run)) for f in dst.rglob("*") if f.is_file())[:40],
            "url": _url(ctx, run.name, name)}


# ---------------------------------------------------------------- check
def _shot(png: Path, max_w: int = 1200) -> dict:
    from PIL import Image
    im = Image.open(png).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    if im.height > 4000:
        im = im.crop((0, 0, im.width, 4000))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78)
    return {"mime": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}


@tool("studio_render", "Render an option of a run in a browser and look at it: returns a screenshot (top 4000 px) and "
      "the layout checks (fonts loaded, broken images, horizontal overflow). Use it after every change.",
      {"run": {"type": "string"}, "option": {"type": "string"}, "width": {"type": "integer", "description": "default 1040"}},
      ["run"])
def render(ctx, a):
    run = _run(a["run"])
    page = _page_dir(run, str(a.get("option") or "main")) / "index.html"
    if not page.is_file():
        raise KeyError("that option has no index.html")
    out = run / "shots" if str(a.get("option") or "main") == "main" else run / "options" / str(a["option"]) / "shots"
    args = [sys.executable, str(FLYNN / "adapters" / "html" / "render.py"), str(page), "--out", str(out)]
    html = page.read_text(encoding="utf-8", errors="ignore")
    if re.search(r'class="(?:[^"]* )?slide(?: [^"]*)?"', html):
        args.append("--slides")
    elif re.search(r'class="(?:[^"]* )?page(?: [^"]*)?"', html):
        args.append("--pages")
    if a.get("width"):
        args += ["--width", str(int(a["width"]))]
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    try:
        checks = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        raise RuntimeError(f"render failed: {(r.stderr or r.stdout).strip()[-400:]}") from None
    png = out / "full.png"
    if not png.is_file():
        pngs = sorted(out.rglob("*.png"))
        png = pngs[0] if pngs else None
    res = {"checks": {k: v for k, v in checks.items() if k != "shots"}, "url": _url(ctx, run.name, a.get("option") or "")}
    if png:
        res["_image"] = _shot(png)
    return res


@tool("studio_lint", "flynn's design lint on an option: off-token colours, off-scale type, contrast, Persian markers and "
      "the other rules taught by the design owner. Fix every finding.", {"run": {"type": "string"}, "option": {"type": "string"}},
      ["run"])
def lint(ctx, a):
    run = _run(a["run"])
    page = _page_dir(run, str(a.get("option") or "main")) / "index.html"
    r = subprocess.run([sys.executable, str(FLYNN / "scripts" / "lint_page.py"), str(page), "--json"],
                       capture_output=True, text=True, timeout=300)
    try:
        return {"lint": json.loads(r.stdout)}
    except ValueError:
        raise RuntimeError(f"lint failed: {(r.stderr or r.stdout).strip()[-400:]}") from None


# ---------------------------------------------------------------- review
@tool("studio_comments", "The comments pinned on a run: list them, add one, reply, or resolve.",
      {"run": {"type": "string"}, "action": {"type": "string", "enum": ["list", "add", "reply", "resolve"]},
       "n": {"type": "integer"}, "text": {"type": "string"}}, ["run"], writes=True)
def comments(ctx, a):
    import review
    run = _run(a["run"])
    act = a.get("action") or "list"
    who = "AI (MCP)"
    if act == "add":
        return review.add_comment(run, str(a.get("text") or ""), {}, who, "page/index.html")
    if act == "reply":
        return review.update(run, int(a["n"]), None, str(a.get("text") or ""), author=who)
    if act == "resolve":
        return review.update(run, int(a["n"]), "resolved", a.get("text"), author=who)
    return {"comments": review.load(run).get("comments", [])}


@tool("studio_snapshot", "Keep the run's current options as a version (so the person can compare and go back).",
      {"run": {"type": "string"}, "option": {"type": "string"}, "label": {"type": "string"}}, ["run"], writes=True)
def snapshot(ctx, a):
    return {"version": ctx.srv.snapshot(_run(a["run"]), a.get("option"), str(a.get("label") or ""))}


@tool("studio_pick", "Mark one option as the chosen one.", {"run": {"type": "string"}, "option": {"type": "string"},
      "note": {"type": "string"}}, ["run", "option"], writes=True)
def pick(ctx, a):
    run = _run(a["run"])
    return {"decision": ctx.srv.pick(run, str(a["option"]), str(a.get("note") or ""))}


@tool("studio_export", "Export an option: pdf, png (section pictures), html (zip), tsx (React), pptx (decks), eml "
      "(newsletters). Returns where the file is in the run.", {"run": {"type": "string"}, "kind": {"type": "string"},
      "option": {"type": "string"}}, ["run", "kind"], writes=True)
def export(ctx, a):
    return ctx.srv.export(_run(a["run"]), str(a["kind"]), str(a.get("option") or "main"))


# ---------------------------------------------------------------- Flynn
@tool("flynn_ask", "Hand a request to Flynn, Studio's own design agent (it follows the design system and its review "
      "rules, and may ask questions). Pass run to work in that run's conversation, or leave it out for Home (new work). "
      "Waits up to wait_seconds for Flynn's reply; if Flynn is still working, call again with the same run and "
      "message empty to keep waiting.",
      {"message": {"type": "string"}, "run": {"type": "string"},
       "effort": {"type": "string", "enum": ["quick", "balanced", "thorough", "max"]},
       "wait_seconds": {"type": "integer", "description": "default 300, at most 1500"},
       "system": {"type": "string", "description": "A design system id (or \"none\") for this conversation; only before "
                  "its first message (refused after). Leave out to let Flynn choose."}}, [], writes=True)
def flynn_ask(ctx, a):
    import chat
    key = f"run:{_run(a['run']).name}" if a.get("run") else "home"
    conv = chat.get(key, ctx.me)
    start = len(conv.events)
    if a.get("system"):
        try:
            conv.set_design_system(a["system"])
        except chat.DesignLocked as e:
            raise RuntimeError(str(e)) from None
    if str(a.get("message") or "").strip():
        if not conv.start_turn(str(a["message"]), effort=a.get("effort") or "balanced"):
            raise RuntimeError("Flynn is still working on the last message in this conversation; call again with an "
                               "empty message to wait for it")
    else:
        start = max((i for i, e in enumerate(conv.events) if e.get("t") == "user"), default=start)
    end = time.time() + min(int(a.get("wait_seconds") or 300), 1500)
    with conv.cond:
        while conv.running and time.time() < end:
            conv.cond.wait(timeout=5)
    evs = conv.events[start:]
    text = "".join(e.get("text", "") for e in evs if e.get("t") == "delta")
    cards = [e["card"] for e in evs if e.get("t") == "card"]
    runs = [e["run"] for e in evs if e.get("t") in ("run_created", "moved")]
    out = {"done": not conv.running, "reply": text[-12000:], "conversation": key,
           "errors": [e.get("text") for e in evs if e.get("t") == "error"][-3:]}
    if cards:
        out["cards"] = cards[-3:]
        if any(c.get("kind") == "needs_alignment" for c in cards):
            out["note"] = ("Flynn is asking questions. Answer with flynn_ask (same run, the answers as the message), "
                           "or let the person answer in Studio.")
    if runs:
        out["runs"] = runs
    return out
