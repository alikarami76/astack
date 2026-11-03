"""Title cards (covers) for Studio's run canvas.

A cover is a 1600x1000 card Flynn makes for a run: the kind of document, a short title and subtitle, a few highlight
chips, the design system, the date, the options and small pictures of the designs. It is built in three steps:

  1. facts(): everything Studio already knows about the run (run.json, classify(), options and their headings,
     versions, comments, the pick, the design system's line);
  2. copy: the title, subtitle and chips, written by the same AI engine the person's chat uses (a turn with no tools,
     the Quick level) or, if that is unavailable, fails or takes over 60 seconds, written from the facts alone;
  3. render: thumbnails of the options (headless Chromium, like exports), one HTML template per kind from
     cover_templates/, and cover/cover.png rendered from it.

Output, inside the run folder: cover/index.html (self-contained, with its images and fonts beside it) and
cover/cover.png. The result is recorded in the run's Studio state under "cover". The endpoints (studio.py) are
POST /api/run/<run>/cover (202, the work happens in a background thread) and GET /api/run/<run>/cover
({state: none|working|ready|error, ...}).

This module never imports studio.py (which can run as __main__); studio.py passes the few functions it needs as a
`host` object: options, classify, state, save_state, versions, comments, lock, now."""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
STATIC = HERE / "static"
TEMPLATES = HERE / "cover_templates"
sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
sys.path.insert(0, str(HERE))

W, H = 1600, 1000
AI_TIMEOUT = 60                      # seconds, then the deterministic copy is used
MAX_THUMBS = 3
TITLE_MAX, SUB_MAX, CHIP_MAX = 60, 110, 28
TEMPLATE_FOR = {"screen": "ui", "release": "release", "newsletter": "newsletter", "report": "report",
                "deck": "deck", "chart": "chart"}
KIND_LABEL = {"ui": "Product UI", "release": "Release note", "newsletter": "Newsletter", "report": "Report",
              "deck": "Deck", "chart": "Chart", "generic": "Design"}
KIND_LABEL_FA = {"ui": "رابط محصول", "release": "یادداشت انتشار", "newsletter": "خبرنامه", "report": "گزارش",
                 "deck": "ارائه", "chart": "نمودار", "generic": "طراحی"}
FA = re.compile(r"[؀-ۿ]")
JOBS: dict[str, dict] = {}           # run folder -> {state, started, ...}; the record in state.json outlives a restart
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------- small helpers
def template_name(kind: str, area: str = "") -> str:
    """The template for what classify() says: UI runs get "ui", doc kinds their own, anything else "generic"."""
    if area == "ui":
        return "ui"
    return TEMPLATE_FOR.get(kind, "generic")


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    if " " in cut[n // 2:]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:-–—·") + "…"


def fa_digits(s: str) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def jalali(d: _dt.date) -> tuple[int, int, int]:
    """Gregorian to Jalali (the usual arithmetic algorithm)."""
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


MONTHS_FA = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def date_text(iso: str, rtl: bool) -> str:
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    if rtl:
        jy, jm, jd = jalali(d)
        return fa_digits(f"{jd} {MONTHS_FA[jm - 1]} {jy}")
    return f"{d.day} {d.strftime('%b %Y')}"


def extra_acronyms() -> dict:
    """Optional extra acronyms for titles read from run names: {"abc": "ABC"} in cover_acronyms.json in FLYNN_HOME."""
    try:
        d = json.loads((Path(os.environ.get("FLYNN_HOME") or Path.home() / ".flynn") / "cover_acronyms.json").read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def id_title(run_id: str) -> str:
    """'2026-10-21-price-calculator-states' -> 'Price Calculator States' (the same reading Home uses)."""
    small = {"a", "an", "and", "for", "of", "the", "to", "with", "in", "on", "vs"}
    up = {"ui": "UI", "ceo": "CEO", "pdf": "PDF", "api": "API", **extra_acronyms()}
    body = run_id[11:] if re.match(r"\d{4}-\d{2}-\d{2}-", run_id) else run_id
    words = [w for w in body.split("-") if w]
    out = []
    for i, w in enumerate(words):
        out.append(up.get(w) or (w if i and w in small else w.capitalize()))
    return " ".join(out) or run_id


# ---------------------------------------------------------------- facts
VOID = {"meta", "link", "br", "img", "input", "hr", "area", "base", "col", "embed", "source", "track", "wbr"}
HARD_SKIP = {"script", "style", "svg", "table", "figure", "template", "noscript"}
SHELL_TAGS = {"nav", "aside", "header", "footer"}
SHELL_ROLES = {"navigation", "menu", "menubar", "complementary", "banner", "contentinfo", "toolbar"}
SHELL_CLASS = re.compile(r"sidebar|side-bar|sidenav|menu|navbar|nav-|-nav\b|topbar|breadcrumb|drawer|header|footer", re.I)
DIALOG_CLASS = re.compile(r"\b(modal|popup|dialog|popover)\b|[-_](modal|popup|dialog)\b|\b(modal|popup|dialog)[-_]", re.I)


class _Page(HTMLParser):
    """Title, first h1, section headings (h2, else h3), the dek (first lead paragraph), language, direction and viewport
    width of a page. For a product UI (ui=True) the app shell is skipped (nav, aside, header, footer, menus, sidebars) and
    the heading of an open dialog or modal is kept apart, because those say what the design shows."""

    def __init__(self, ui: bool = False):
        super().__init__(convert_charrefs=True)
        self.ui = ui
        self.title = ""
        self.h1 = ""
        self.h2: list[str] = []
        self.h3: list[str] = []
        self.dialog = ""
        self.dek = ""
        self.para = ""
        self.lang = ""
        self.dir = ""
        self.width = 0
        self._in: str | None = None
        self._cls = ""
        self._buf: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []     # (tag, skips its content, is a dialog)
        self._skip = 0
        self._dlg = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang, self.dir = (a.get("lang") or "").lower(), (a.get("dir") or "").lower()
        if tag == "meta" and (a.get("name") or "").lower() == "viewport":
            m = re.search(r"width=(\d+)", a.get("content") or "")
            self.width = int(m.group(1)) if m else 0
        if tag == "br" and self._in:
            self._buf.append(" ")
        if tag in VOID:
            return
        role = (a.get("role") or "").lower()
        ident = f"{a.get('class') or ''} {a.get('id') or ''}"
        dlg = tag == "dialog" or role in ("dialog", "alertdialog") or bool(DIALOG_CLASS.search(ident))
        skip = tag in HARD_SKIP or (self.ui and not dlg and (tag in SHELL_TAGS or role in SHELL_ROLES or bool(SHELL_CLASS.search(ident))))
        self._stack.append((tag, skip, dlg))
        self._skip += skip
        self._dlg += dlg
        if tag in ("title", "h1", "h2", "h3", "h4", "h5", "h6", "p") and not self._skip:
            self._in, self._buf, self._cls = tag, [], (a.get("class") or "")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if any(t == tag for t, _, _ in self._stack):
            while self._stack:
                t, skip, dlg = self._stack.pop()
                self._skip -= skip
                self._dlg -= dlg
                if t == tag:
                    break
        if self._in == tag:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if text:
                if tag == "title" and not self.title:
                    self.title = text
                elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._dlg and 3 <= len(text) <= 90:
                    self.dialog = self.dialog or text
                if tag == "h1" and not self.h1:
                    self.h1 = text
                elif tag == "h2" and len(self.h2) < 6 and 3 <= len(text) <= 90:
                    self.h2.append(text)
                elif tag == "h3" and len(self.h3) < 6 and 3 <= len(text) <= 90:
                    self.h3.append(text)
                elif tag == "p" and len(text) >= 30 and not self.ui:
                    if not self.dek and re.search(r"dek|lead|standfirst|subtitle|summary|intro", self._cls):
                        self.dek = text
                    if not self.para:
                        self.para = text
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)


def read_page(path: Path, ui: bool = False) -> dict:
    p = _Page(ui)
    try:
        p.feed(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - odd markup should never stop a cover
        pass
    return {"title": p.title, "h1": p.h1, "headings": (p.h2 or p.h3)[:6], "dek": p.dek or p.para, "dialog": p.dialog,
            "lang": p.lang, "dir": p.dir, "width": p.width}


THEME_IDS = ("editorial", "dk-mckinsey", "dk-publication", "dk-release-note")


def page_theme(run: Path, page_rel: str, meta: dict) -> str | None:
    """The theme a document was built with: run.json (theme, template, system) when one of them names a theme, else the
    name in the head comment of the page's own stylesheets (for example "dk-mckinsey report: ...")."""
    for k in ("theme", "template", "system"):
        v = str(meta.get(k) or "").lower()
        for t in THEME_IDS:
            if t in v:
                return t
    try:
        html = (run / page_rel).read_text(encoding="utf-8", errors="replace")
        for href in re.findall(r'<link[^>]+href="([^"#?]+\.css)"', html):
            css = (run / page_rel).parent / href
            if css.is_file():
                head = css.read_text(encoding="utf-8", errors="replace")[:700].lower()
                m = re.search(r"(editorial|dk-mckinsey|dk-publication|dk-release-note)", head)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def system_line(system: str | None) -> str:
    """What the store says about a design system, in one line: 'Admin Panel · 148 library components' for a
    library, 'DK Editorial · document theme' for a theme (the same summary the Systems page shows, systems.py)."""
    if not system:
        return ""
    try:
        import systems as ds
        name = ds.display_name(system)
        for s in ds.list_systems():
            if s["id"] == system:
                if s["kind"] == "theme":
                    return f"{s['name']} · document theme"
                n = sum((s.get("counts") or {}).get("component", {}).values())
                return f"{s['name']} · {n} library components" if n else s["name"]
        return name
    except Exception:  # noqa: BLE001 - the store may be empty or away
        return str(system)


def option_label(name: str) -> str:
    return "A" if name == "main" else name.upper() if len(name) <= 2 else name


def facts(run: Path, host, sysline=None) -> dict:
    meta = {}
    try:
        meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cl = host.classify(meta, run)
    opts = []
    for o in host.options(run):
        pg = read_page(run / o["page"], cl["area"] == "ui")
        opts.append({"name": o["name"], "label": option_label(o["name"]), "page": o["page"], **pg})
    st = host.state(run)
    try:
        comments = host.comments(run)
    except Exception:  # noqa: BLE001
        comments = []
    first = opts[0] if opts else {}
    rtl = (first.get("dir") == "rtl" or first.get("lang", "").startswith("fa") or
           bool(FA.search(" ".join([first.get("h1", ""), first.get("title", "")])))) if first else bool(FA.search(meta.get("request", "")))
    outcome = meta.get("outcome")
    if outcome is not None and not isinstance(outcome, str):
        outcome = json.dumps(outcome, ensure_ascii=False)
    kind = template_name(cl["kind"], cl["area"])
    theme = page_theme(run, first["page"], meta) if (first and cl["area"] != "ui") else None
    dec = st.get("decision") or {}
    return {
        "run": run.name, "name": id_title(run.name), "date": meta.get("date") or run.name[:10],
        "request": meta.get("request", ""), "type": meta.get("type"), "system": meta.get("system"),
        "template_hint": meta.get("template"), "outcome": clip(outcome or "", 400),
        "area": cl["area"], "kind": cl["kind"], "template": kind, "options": opts, "rtl": rtl,
        "versions": len(host.versions(run)), "comments": len(comments),
        "comments_open": sum(c.get("status") == "open" for c in comments),
        "picked": dec.get("option"), "theme": theme,
        "system_line": (sysline or system_line)(theme or meta.get("system")),
    }


# ---------------------------------------------------------------- the copy: Flynn's, or the deterministic one
def short_title(text: str) -> str:
    """A page heading shortened to the title limit: at a colon, dash or semicolon when that leaves a real title, else at a
    word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= TITLE_MAX:
        return text
    m = re.search(r"[:;—–]", text[:TITLE_MAX])
    if m and m.start() >= 18:
        return text[:m.start()].strip()
    return clip(text, TITLE_MAX)


def first_sentence(text: str, limit: int) -> str:
    """The shortest complete piece of text that fits: the first sentence, else as many whole clauses of it as fit (split at
    , ، ; : and dashes), and only as a last resort a cut at a word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.+?[.!?؟])(\s|$)", text)
    sent = (m.group(1) if m else text).rstrip(".!?؟ ")
    if len(sent) <= limit:
        return sent
    parts = re.split(r"(?<=[,،;:])\s+|\s+[—–-]\s+", sent)
    best = ""
    for i in range(len(parts)):
        cand = " ".join(parts[:i + 1]).rstrip(",،;: ")
        if len(cand) > limit:
            break
        best = cand
    return best if len(best) >= 20 else clip(sent, limit)


def fallback_copy(f: dict) -> dict:
    """Title, subtitle and chips from the pages themselves, used when no AI engine is available or it fails: the title is
    the page's own h1 or <title> (shortened), the subtitle its dek or first paragraph (left empty when there is none), and
    the chips are its section headings (the 2nd and 3rd, then the first), never the option count or the text direction."""
    opts = f.get("options") or []
    first = opts[0] if opts else {}
    title = ""
    for cand in (first.get("h1"), first.get("title")):
        if cand and len(cand) <= TITLE_MAX:
            title = cand
            break
    title = title or short_title(first.get("h1") or first.get("title") or "") or clip(f.get("name") or f.get("run", ""), TITLE_MAX)
    if f.get("template") == "ui":      # a UI's own words: the dialog it shows, else a heading of the main content
        sub = first_sentence(first.get("dialog") or next((h for h in first.get("headings", []) if h != title), ""), SUB_MAX)
    else:
        sub = first_sentence(first.get("dek", ""), SUB_MAX) if first.get("dek") else ""
    if sub.lower() == title.lower():
        sub = ""
    heads = ([first["dialog"]] if first.get("dialog") else []) + list(first.get("headings", []))
    pool = heads[1:4] + heads[:1] + [o.get("h1") or o.get("title") for o in opts[1:]]
    seen = {title.lower(), sub.lower()}
    out: list[str] = []
    for h in pool:
        h = re.sub(r"\s+", " ", h or "").strip()
        if h and len(h) <= CHIP_MAX and h.lower() not in seen and not noisy_chip(h):     # a chip is never cut mid-phrase
            seen.add(h.lower())
            out.append(h)
    return {"title": title, "subtitle": sub, "highlights": out[:3]}


COUNT_CHIP = re.compile(r"^[\d۰-۹]+\s*(options?|designs?|variants?|versions?|گزینه|طرح|نسخه)\b", re.I)


def noisy_chip(c: str) -> bool:
    """Chips that tell the reader nothing or repeat the card's footer: text direction, option counts."""
    return c.strip().lower() in ("rtl", "ltr") or bool(COUNT_CHIP.match(c.strip()))


def parse_reply(text: str) -> dict | None:
    """The {title, subtitle, highlights} object in an AI reply: tolerates code fences, text around it and trailing
    commas. Returns clean, length-limited copy, or None when there is no usable title."""
    if not text:
        return None
    s = re.sub(r"```[a-zA-Z]*", "", text)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", s):
        chunk = s[m.start():]
        obj = None
        try:
            obj, _ = dec.raw_decode(chunk)
        except ValueError:
            try:
                obj, _ = dec.raw_decode(re.sub(r",\s*([}\]])", r"\1", chunk))
            except ValueError:
                continue
        if not isinstance(obj, dict):
            continue
        title = clip(obj.get("title") if isinstance(obj.get("title"), str) else "", TITLE_MAX)
        if not title:
            continue
        sub = clip(obj.get("subtitle") if isinstance(obj.get("subtitle"), str) else "", SUB_MAX)
        hl = obj.get("highlights") if isinstance(obj.get("highlights"), list) else []
        chips, seen = [], {title.lower(), sub.lower()}
        for h in hl:
            h = clip(h, CHIP_MAX) if isinstance(h, str) else ""
            if h and h.lower() not in seen and not noisy_chip(h):
                seen.add(h.lower())
                chips.append(h)
        return {"title": title, "subtitle": sub, "highlights": chips[:4]}
    return None


def build_prompt(f: dict) -> str:
    lang = "Persian (فارسی), with natural Persian wording" if f.get("rtl") else "English"
    brief = {
        "run": f["run"], "date": f["date"], "kind": KIND_LABEL[f["template"]], "request": clip(f["request"], 500),
        "run_type": f["type"], "design_system": f["system_line"] or f["system"], "template": f["template_hint"],
        "outcome": f["outcome"], "versions": f["versions"], "comments": f["comments"],
        "options": [{"option": o["label"], "page_title": o["title"], "h1": clip(o["h1"], 120), "headings": o["headings"]}
                    for o in f["options"][:6]],
        "option_count": len(f["options"]), "picked_option": option_label(f["picked"]) if f["picked"] else None,
    }
    return (
        "Write the copy for the title card (cover) of one design run in flynn Studio. Use only the facts below; do not "
        "invent numbers, names or features that they do not show.\n\n"
        f"Facts (JSON):\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\n"
        f"Write in {lang}. Return only a JSON object, with no code fence and no other text:\n"
        f'{{"title": "...", "subtitle": "...", "highlights": ["...", "..."]}}\n'
        f"- title: at most {TITLE_MAX} characters, what the run is about, specific, no trailing period.\n"
        f"- subtitle: one line, at most {SUB_MAX} characters, what the designs show or decide.\n"
        f"- highlights: 2 to 4 short chips, each at most {CHIP_MAX} characters, about what the designs cover "
        '(for example "empty and error states", "pricing by band"). Do not mention the number of options, the text '
        'direction or RTL (the card shows the count itself), and do not repeat the subtitle. No emoji.')


def _shim(me: dict | None, scratch: Path):
    """The little of a Conversation that api_engine.ApiEngine reads (me, base, dir, effort)."""
    return SimpleNamespace(me=me or {}, base=scratch, dir=scratch / "cover", effort="quick", key="cover")


def get_engine(me: dict | None, scratch: Path):
    """The engine the person's chat would use (chat.py Conversation._engine): the model API when they chose it, their
    own computer in team mode, else the local SDK engine (or the CLI). None when AI is switched off."""
    import chat
    import team
    import api_keys
    uses_api = (api_keys.choice(me or None).get("engine") == "api") if team.on() else chat.settings().get("engine") == "api"
    if uses_api:
        import api_engine
        return api_engine.ApiEngine(_shim(me, scratch))
    if team.on():
        import agent_hub
        return agent_hub.RemoteEngine()
    name = chat.settings().get("engine", "sdk")
    if name == "off":
        return None
    eng = chat.engines.choose({"sdk": "local", "cli": "cli"}.get(name, "local"))
    return chat.StudioSdkEngine() if type(eng) is chat.engines.SdkEngine else eng


def make_turn(prompt: str, run_name: str, me: dict | None, scratch: Path):
    """One turn with no tools: no allowed tools, no MCP servers, no named agent, the Quick level's model and effort."""
    import chat
    e = chat.EFFORT["quick"]
    t = chat.engines.Turn(prompt=prompt, session=None, system="You write short copy for design title cards. Answer with "
                             "one JSON object and nothing else. Do not use tools.", allow=[], deny=[],
                             file_id=f"cover:{run_name}", cwd=str(scratch), claude=chat.claude_bin(),
                             user=(me or {}).get("email") or "ali", agent="", mcp={}, model=e["model"],
                             effort=e["effort"], budget_usd=chat.cap("quick"))
    t.no_tools = True          # api_engine.ApiEngine offers the model no tools for this turn
    return t


def ai_copy(f: dict, me: dict | None, engine_factory=None, timeout: float = AI_TIMEOUT) -> tuple[dict | None, str]:
    """(copy, why): Flynn's copy from the person's engine, or (None, the reason) so the caller can fall back."""
    scratch = Path(tempfile.mkdtemp(prefix="flynn-cover-"))
    try:
        try:
            eng = (engine_factory or get_engine)(me, scratch)
        except Exception as e:  # noqa: BLE001
            return None, f"engine unavailable ({type(e).__name__})"
        if eng is None:
            return None, "AI is switched off"
        turn = make_turn(build_prompt(f), f["run"], me, scratch)
        text, errors, done = [], [], threading.Event()

        def emit(ev):
            t = ev.get("t")
            if t == "delta":
                text.append(ev.get("text", ""))
            elif t == "error":
                errors.append(str(ev.get("text", ""))[:200])
            elif t == "turn_end":
                if ev.get("text") and not text:
                    text.append(ev["text"])
                done.set()

        def go():
            try:
                eng.run(turn, emit)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}"[:200])
            finally:
                done.set()
        threading.Thread(target=go, daemon=True).start()
        if not done.wait(timeout):
            try:
                eng.stop()
            except Exception:  # noqa: BLE001
                pass
            return None, f"no answer within {int(timeout)} s"
        copy = parse_reply("".join(text))
        if copy:
            return copy, ""
        return None, errors[0] if errors else "the reply had no usable JSON"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- pictures of the designs
def _px(page: dict, default: int, cap: int = 1500) -> int:
    w = page.get("width") or 0
    return w if 360 <= w <= cap else default


def capture(run: Path, f: dict, outdir: Path) -> list[str]:
    """Thumbnails of up to three options with the headless Chromium Studio exports with (FLYNN_CHROME_BIN or
    Playwright's own). The crop depends on the kind of document. Returns the file names written into outdir."""
    from playwright.sync_api import sync_playwright
    kind, pages = f["template"], f["options"]
    made: list[str] = []
    raws: list[bytes] = []                  # product UI: kept until all are taken, then cropped to their content together
    if not pages:
        return made

    def save(png: bytes) -> None:
        name = f"thumb-{len(made) + 1}.png"
        (outdir / name).write_bytes(png)
        made.append(name)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        try:
            def open_page(o, width, height=1000):
                pg = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                pg.goto((run / o["page"]).resolve().as_uri())
                pg.wait_for_load_state("networkidle")
                pg.evaluate("document.fonts.ready")
                pg.wait_for_timeout(250)
                return pg

            def crop(pg, width, height, y=0):
                total = pg.evaluate("document.documentElement.scrollHeight")
                if y >= total:
                    return None
                return pg.screenshot(full_page=True, clip={"x": 0, "y": y, "width": width, "height": min(height, total - y)})

            def elements(pg, selector, limit):
                out = []
                loc = pg.locator(selector)
                for i in range(min(loc.count(), limit)):
                    try:
                        out.append(loc.nth(i).screenshot())
                    except Exception:  # noqa: BLE001
                        continue
                return out

            for o in pages:
                if len(made) + len(raws) >= MAX_THUMBS:
                    break
                try:
                    if kind in ("ui", "generic"):
                        w = _px(o, 1440)
                        ratio = 0.75 if kind == "ui" else 0.625
                        pg = open_page(o, w, round(w * ratio))
                        png = crop(pg, w, round(w * ratio))
                        pg.close()
                        if png and kind == "ui":
                            raws.append(png)
                        elif png:
                            save(png)
                    elif kind == "deck":
                        w = _px(o, 1280)
                        pg = open_page(o, w, round(w * 0.5625))
                        shots = elements(pg, ".slide", MAX_THUMBS - len(made) if len(pages) == 1 else 1)
                        if not shots:
                            shots = [crop(pg, w, round(w * 0.5625))]
                        pg.close()
                        [save(s) for s in shots if s]
                    elif kind == "report":
                        w = _px(o, 1040, 1100)
                        pg = open_page(o, w)
                        shots = elements(pg, ".page", MAX_THUMBS - len(made)) if len(pages) == 1 else []
                        if not shots:
                            hh = round(w * 1.3)
                            first = crop(pg, w, hh)
                            shots = [first] + ([crop(pg, w, hh, hh)] if len(pages) == 1 else [])
                        pg.close()
                        [save(s) for s in shots if s]
                    elif kind in ("newsletter", "release"):
                        w = _px(o, 720 if kind == "newsletter" else 1040, 1100)
                        pg = open_page(o, w)
                        png = crop(pg, w, round(w * 1.7))
                        pg.close()
                        png and save(png)
                        if len(made) >= 2:
                            break
                    elif kind == "chart":
                        w = _px(o, 1040, 1300)
                        pg = open_page(o, w)
                        best, area = None, 0
                        loc = pg.locator("svg, figure, .exhibit")
                        for i in range(min(loc.count(), 40)):
                            bb = loc.nth(i).bounding_box()
                            if bb and bb["width"] >= 300 and bb["height"] >= 150 and bb["width"] * bb["height"] > area:
                                best, area = i, bb["width"] * bb["height"]
                        png = loc.nth(best).screenshot() if best is not None else crop(pg, w, round(w * 0.62))
                        pg.close()
                        png and save(png)
                        break
                except Exception:  # noqa: BLE001 - one bad page should not stop the card
                    continue
        finally:
            b.close()
    for png in trim_to_content(raws):
        save(png)
    return made[:MAX_THUMBS]


def content_height(img, floor: int) -> int:
    """Height of an image down to its last row that differs from the background colour (the page's blank bottom is not
    design), plus a little air, never less than floor."""
    from PIL import Image, ImageChops
    rgb = img.convert("RGB")
    bg = rgb.getpixel((rgb.width - 1, rgb.height - 1))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L").point(lambda p: 255 if p > 14 else 0)
    box = diff.getbbox()
    return max(floor, min(rgb.height, (box[3] + 28) if box else rgb.height))


def trim_to_content(pngs: list[bytes]) -> list[bytes]:
    """Crop screenshots to their content, all to the same height (the shortest content, at least 45% of the width), so
    the frames hold design and sit evenly. Returns them unchanged when Pillow is not installed."""
    if not pngs:
        return []
    try:
        import io
        from PIL import Image
        imgs = [Image.open(io.BytesIO(p)) for p in pngs]
        h = min(content_height(i, round(i.width * 0.45)) for i in imgs)
        out = []
        for i in imgs:
            buf = io.BytesIO()
            i.convert("RGB").crop((0, 0, i.width, min(h, i.height))).save(buf, "PNG")
            out.append(buf.getvalue())
        return out
    except Exception:  # noqa: BLE001
        return pngs


def png_ratio(path: Path) -> str:
    """'width/height' of a PNG file from its header ('4/3' when unreadable)."""
    try:
        import struct
        w, h = struct.unpack(">II", path.read_bytes()[16:24])
        return f"{w}/{h}"
    except Exception:  # noqa: BLE001
        return "4/3"


def render_png(index: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("FLYNN_CHROME_BIN") or None)
        try:
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            pg.goto(index.resolve().as_uri())
            pg.wait_for_load_state("networkidle")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
        finally:
            b.close()


# ---------------------------------------------------------------- the HTML
def esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def fill(template: str, values: dict) -> str:
    """{{key}} slots. Values are HTML-escaped, except keys ending in _html or _css, which the code builds itself
    (from escaped parts) and trusts."""
    def sub(m):
        k = m.group(1)
        v = values.get(k, "")
        return str(v) if k.endswith(("_html", "_css")) else esc(v)
    return re.sub(r"\{\{(\w+)\}\}", sub, template)


def title_px(title: str, big: int, small: int) -> int:
    n = len(title)
    if n <= 20:
        return big
    if n >= TITLE_MAX:
        return small
    return round(big - (big - small) * (n - 20) / (TITLE_MAX - 20))


def generic_label(run_type, rtl: bool) -> str:
    """The eyebrow of a "generic" card: what the run is, from run.json's type ("Run" when unknown)."""
    en = {"learn-system": "System learning", "question": "Question", "sync": "Sync", "graphic": "Graphic",
          "chart": "Chart", "deck": "Deck", "ui": "Design"}.get(run_type or "") or (str(run_type).replace("-", " ").capitalize() if run_type else "Run")
    fa = {"System learning": "یادگیری سیستم", "Question": "پرسش", "Sync": "همگام‌سازی", "Graphic": "گرافیک", "Chart": "نمودار",
          "Deck": "ارائه", "Design": "طراحی", "Run": "اجرا"}
    return fa.get(en, en) if rtl else en


def values_for(f: dict, copy: dict, thumbs: list[str], big: int = 92, small: int = 58, ratio: str = "4/3") -> dict:
    rtl = f["rtl"]
    n = len(f["options"])
    kind = f["template"]
    opt_text = (fa_digits(f"{n} گزینه") if rtl else f"{n} option{'s' if n != 1 else ''}") if n else ""
    if f["picked"]:
        lab = option_label(f["picked"])
        picked = f"انتخاب‌شده: گزینهٔ {lab}" if rtl else f"Picked: option {lab}" if n > 1 else "Picked"
    else:
        picked = "هنوز انتخابی نشده" if rtl else "No option picked yet"
    chips = "".join(f'<span class="chip">{esc(c)}</span>' for c in copy["highlights"])
    figs = "".join(f'<figure class="th th{i + 1}"><img src="{esc(t)}" alt=""></figure>' for i, t in enumerate(thumbs))
    if not figs:
        figs = '<figure class="th th1 ph"></figure>'
    return {
        "lang": "fa" if rtl else "en", "dir": "rtl" if rtl else "ltr", "base_css": (TEMPLATES / "base.css").read_text(encoding="utf-8"),
        "kind_label": (KIND_LABEL_FA[kind] if rtl else KIND_LABEL[kind]) if kind != "generic" else generic_label(f.get("type"), rtl), "title": copy["title"], "subtitle": copy["subtitle"],
        "system": f["system_line"] or (f["system"] or ""), "date": date_text(f["date"], rtl),
        "options_text": opt_text, "picked_text": picked, "chips_html": chips, "thumbs_html": figs,
        "l_date": "تاریخ" if rtl else "Date", "l_system": "سیستم طراحی" if rtl else "Design system",
        "l_designs": "طرح‌ها" if rtl else "Designs", "l_decision": "تصمیم" if rtl else "Decision",
        "thumb_ratio": ratio, "title_px": title_px(copy["title"], big, small), "run_name": f["run"], "count": fa_digits(str(n)) if rtl else str(n),
    }


SIZES = {"ui": (92, 58), "release": (88, 56), "newsletter": (84, 54), "report": (84, 54), "deck": (92, 58),
         "chart": (68, 46), "generic": (88, 56)}


def build_html(f: dict, copy: dict, thumbs: list[str], ratio: str = "4/3") -> str:
    big, small = SIZES[f["template"]]
    tpl = (TEMPLATES / f"{f['template']}.html").read_text(encoding="utf-8")
    return fill(tpl, values_for(f, copy, thumbs, big, small, ratio))


# ---------------------------------------------------------------- the job
def make(run: Path, host, me: dict | None = None, engine_factory=None, timeout: float = AI_TIMEOUT,
         template: str | None = None, use_ai: bool = True) -> dict:
    """Build the cover (blocking). Returns the record saved in the run's Studio state."""
    f = facts(run, host)
    if template:
        f["template"] = template
    copy, why = (ai_copy(f, me, engine_factory, timeout) if use_ai else (None, "not asked for"))
    source = "flynn" if copy else "fallback"
    if not copy:
        copy = fallback_copy(f)
    fb = fallback_copy(f)
    if not copy.get("title"):
        copy["title"] = fb["title"]
    if len(copy.get("highlights", [])) < 1:
        copy["highlights"] = fb["highlights"]
    out = run / "cover.tmp"                  # built beside the old card, swapped in only when it is complete
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    for font in ("geist.woff2", "vazirmatn-variable.woff2"):
        shutil.copy(STATIC / font, out / font)
    thumbs: list[str] = []
    try:
        thumbs = capture(run, f, out)
    except Exception:  # noqa: BLE001 - a card without pictures still beats none
        thumbs = []
    ratio = png_ratio(out / thumbs[0]) if thumbs else "4/3"
    (out / "index.html").write_text(build_html(f, copy, thumbs, ratio), encoding="utf-8")
    render_png(out / "index.html", out / "cover.png")
    if (run / "cover").exists():
        shutil.rmtree(run / "cover")
    out.rename(run / "cover")
    rec = {"time": host.now(), "kind": f["kind"], "template": f["template"], "copy": source, "copy_note": why,
           "title": copy["title"], "subtitle": copy["subtitle"], "highlights": copy["highlights"],
           "thumbs": len(thumbs), "rtl": f["rtl"]}
    with host.lock:
        st = host.state(run)
        st["cover"] = rec
        host.save_state(run, st)
    return rec


def status(run: Path, st: dict | None = None) -> dict:
    """{state: none|working|ready|error, ...}: the running job if there is one, else the record in the Studio state."""
    job = JOBS.get(str(run))
    if job and job["state"] == "working":
        return {"state": "working", "started": job["started"]}
    rec = (st or {}).get("cover")
    png = run / "cover" / "cover.png"
    if job and job["state"] == "error":
        return {"state": "error", "error": job["error"], **({"ready": True, **rec} if rec and png.exists() else {})}
    if rec and png.exists():
        return {"state": "ready", "png": "cover/cover.png", "page": "cover/index.html", **rec}
    return {"state": "none"}


def start(run: Path, host, me: dict | None = None, engine_factory=None, timeout: float = AI_TIMEOUT, **kw) -> dict:
    """Start the job in a background thread (the endpoint answers 202 at once). One job per run at a time."""
    key = str(run)
    with _jobs_lock:
        if JOBS.get(key, {}).get("state") == "working":
            return {"state": "working", "started": JOBS[key]["started"]}
        JOBS[key] = {"state": "working", "started": host.now()}

    def go():
        try:
            make(run, host, me, engine_factory, timeout, **kw)
            JOBS.pop(key, None)
        except Exception as e:  # noqa: BLE001 - shown in the Tools pane
            JOBS[key] = {"state": "error", "error": f"{type(e).__name__}: {e}"[:300], "started": JOBS.get(key, {}).get("started")}
    threading.Thread(target=go, daemon=True).start()
    return {"state": "working", "started": JOBS[key]["started"]}
