"""Studio's model-API engine: Flynn through a provider's API (OpenAI, Anthropic, DeepSeek, Gemini, Mistral, xAI, Groq,
OpenRouter, Qwen, Ollama or any OpenAI-compatible address) instead of the Claude Agent SDK.

The model calls go through aisuite (andrewyng/aisuite), streamed. The turn loop is Studio's own, so the chat shows each
piece as it happens (text, thinking, every tool call) and Stop works between pieces: stream one model step, run the
tool calls it asked for, send the results back, repeat until it answers without tools or reaches the step limit.

Tools: the design tools (design_tools.py, the same ones the MCP server offers), Studio's chat tools (ask_user,
show_result, studio_view, studio_focus, studio_edits, studio_link) and read-only access to flynn's own guides
(flynn_read, flynn_search). No shell and no files outside the person's own runs. There are no specialists here: the model
composes and reviews the design itself, following flynn's instructions and rubric.
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import api_keys
import design_tools

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]

# id: label, aisuite provider, default address (OpenAI-compatible model listing), key needed, sends pictures
PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "aisuite": "anthropic", "models_url": "https://api.anthropic.com/v1/models", "key": True, "vision": True,
                  "hint": "claude-sonnet-…, claude-opus-…"},
    "openai": {"label": "OpenAI", "aisuite": "openai", "models_url": "https://api.openai.com/v1/models", "key": True, "vision": True,
               "hint": "gpt-…"},
    "deepseek": {"label": "DeepSeek", "aisuite": "deepseek", "models_url": "https://api.deepseek.com/models", "key": True, "vision": False,
                 "hint": "deepseek-chat, deepseek-reasoner"},
    "gemini": {"label": "Google Gemini", "aisuite": "gemini", "models_url": "https://generativelanguage.googleapis.com/v1beta/openai/models", "key": True, "vision": True,
               "hint": "gemini-…"},
    "mistral": {"label": "Mistral", "aisuite": "mistral", "models_url": "https://api.mistral.ai/v1/models", "key": True, "vision": True,
                "hint": "mistral-large-latest, …"},
    "xai": {"label": "xAI (Grok)", "aisuite": "xai", "models_url": "https://api.x.ai/v1/models", "key": True, "vision": True, "hint": "grok-…"},
    "groq": {"label": "Groq", "aisuite": "groq", "models_url": "https://api.groq.com/openai/v1/models", "key": True, "vision": False, "hint": "llama-…, qwen-…"},
    "openrouter": {"label": "OpenRouter (many models)", "aisuite": "openrouter", "models_url": "https://openrouter.ai/api/v1/models", "key": True, "vision": True,
                   "hint": "provider/model, e.g. deepseek/deepseek-chat"},
    "tongyi": {"label": "Qwen (Alibaba Cloud)", "aisuite": "tongyi", "models_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/models", "key": True, "vision": True,
               "hint": "qwen-max, qwen-plus, …"},
    "ollama": {"label": "Ollama (models on this computer)", "aisuite": "ollama", "models_url": "http://localhost:11434/v1/models", "key": False, "vision": True,
               "hint": "a model you pulled, e.g. qwen3", "local": True},
    "custom": {"label": "Another OpenAI-compatible API", "aisuite": "openai", "models_url": "", "key": True, "vision": True,
               "hint": "the model id your API expects", "base_url": True},
}
STEPS = {"quick": 12, "balanced": 30, "thorough": 60, "max": 120}
CHAT_TOOLS = ("ask_user", "show_result", "studio_view", "studio_focus", "studio_edits", "studio_link")
READ_ROOTS = ("agents/", "skills/", "systems/", "docs/", "adapters/html/themes/", "adapters/html/templates/",
              "adapters/html/components.md", "adapters/html/README.md") + tuple(
    f"adapters/{d.name}/" for d in (FLYNN / "adapters").glob("*kit") if d.is_dir())     # a product kit, when installed
READ_SKIP = re.compile(r"(^|/)(\.env|secrets?|.*\.(key|pem|sqlite|db))(/|$)", re.I)


class ApiUnavailable(RuntimeError):
    pass


def providers_payload(me) -> dict:
    keys = api_keys.masked(me)
    local_ok = not __import__("team").on()
    return {"providers": [{"id": k, "label": v["label"], "hint": v["hint"], "key": v["key"], "has_key": k in keys,
                           "key_hint": keys.get(k), "base_url": bool(v.get("base_url")), "vision": v["vision"]}
                          for k, v in PROVIDERS.items() if local_ok or not v.get("local")],
            "choice": api_keys.choice(me), "sdk": _sdk_ok()}


def _sdk_ok() -> dict:
    try:
        import aisuite  # noqa: F401
        return {"ok": True, "version": getattr(__import__("aisuite"), "__version__", "?")}
    except ImportError:
        return {"ok": False, "fix": 'python3 -m pip install --user "aisuite==0.2.0" openai anthropic groq mistralai google-genai'}


def _config(me, provider: str, base_url: str = "") -> dict:
    p = PROVIDERS.get(provider)
    if not p:
        raise ValueError(f"unknown provider {provider}")
    if p.get("local") and __import__("team").on():
        raise ValueError("Ollama runs on your own computer; use Studio on your Mac for it")
    cfg = {}
    key = api_keys.get(me, provider)
    if p["key"]:
        if not key:
            raise ApiUnavailable(f"add your {p['label']} API key in Settings (Model API)")
        cfg["api_key"] = key
    if provider == "custom":
        if not base_url:
            raise ApiUnavailable("set the API's address in Settings (Model API)")
        cfg["base_url"] = base_url
    return cfg


def _client(me, provider: str, base_url: str = ""):
    try:
        import aisuite
    except ImportError as e:
        raise ApiUnavailable("the aisuite package isn't installed: " + _sdk_ok().get("fix", "")) from e
    return aisuite.Client(provider_configs={PROVIDERS[provider]["aisuite"]: _config(me, provider, base_url)})


def list_models(me, provider: str, base_url: str = "") -> list[str]:
    """The provider's model ids, from its /models endpoint."""
    import urllib.request
    p = PROVIDERS[provider]
    url = (base_url.rstrip("/") + "/models") if provider == "custom" else p["models_url"]
    if not url:
        return []
    cfg = _config(me, provider, base_url)
    headers = {"Accept": "application/json"}
    if provider == "anthropic":
        headers.update({"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"})
    elif cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20, context=_ssl()) as r:
        j = json.loads(r.read())
    items = j.get("data") or j.get("models") or []
    ids = [str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in items if isinstance(m, dict)]
    return sorted(i for i in ids if i)


def _ssl():
    import pkgcache
    return pkgcache._ssl()


def test(me, provider: str, model: str, base_url: str = "") -> dict:
    t0 = time.time()
    client = _client(me, provider, base_url)
    r = client.chat.completions.create(model=f"{PROVIDERS[provider]['aisuite']}:{model}",
                                       messages=[{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=20)
    return {"ok": True, "reply": (r.choices[0].message.content or "").strip()[:80], "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- flynn's guides (read-only)
def _flynn_read(conv, a):
    rel = str(a.get("path") or "").lstrip("/")
    if ".." in Path(rel).parts or not rel.startswith(READ_ROOTS) or READ_SKIP.search(rel):
        raise ValueError("flynn_read reads flynn's guides only: " + ", ".join(READ_ROOTS))
    p = FLYNN / rel
    if p.is_dir():
        return {"path": rel, "files": sorted(str(f.relative_to(FLYNN)) for f in p.iterdir())[:200]}
    if not p.is_file() or p.stat().st_size > 400_000:
        raise KeyError(f"no readable file {rel}")
    return {"path": rel, "content": p.read_text(encoding="utf-8", errors="replace")}


def _flynn_search(conv, a):
    r = subprocess.run([sys.executable, str(FLYNN / "kb" / "kb.py"), "search", str(a.get("query") or "")],
                       capture_output=True, text=True, timeout=60)
    return {"results": (r.stdout or r.stderr)[-20000:]}


EXTRA = {
    "flynn_read": ({"name": "flynn_read", "description": "Read one of flynn's own guides: agents/flynn.md, "
                    "skills/flynn-core/SKILL.md and its references (rubric.md, dataviz.md), systems/<id>/GUIDE.md, "
                    "adapters/html/README.md, components.md, themes and templates. A folder lists its files.",
                    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}, _flynn_read),
    "flynn_search": ({"name": "flynn_search", "description": "Search flynn's knowledge store (components, tokens, "
                      "patterns, approved references, lessons, taste notes).",
                      "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}, _flynn_search),
}


def _system_prompt(t) -> str:
    if getattr(t, "no_tools", False):         # a plain one-shot ask (title cards): just the turn's own instructions
        return t.system or ""
    base = ""
    try:
        text = (FLYNN / "agents" / "flynn.md").read_text(encoding="utf-8")
        base = text.split("---", 2)[2] if text.startswith("---") else text
    except OSError:
        pass
    return (base.strip() + "\n\n# How you run here\nYou run through a model API inside flynn Studio, not in Claude Code. You "
            "have no shell and no specialists (no subagents): compose, check and critique the work yourself. Your tools "
            "are listed; files live in runs (studio_* tools). Read flynn's protocol and rubric with flynn_read "
            "(skills/flynn-core/SKILL.md, skills/flynn-core/references/rubric.md) and the design system's guide "
            "(studio_get_system) before designing. After every change, look at it with studio_render and fix what you "
            "see and what studio_lint reports. Ask questions with ask_user, then end your turn.\n\n" + (t.system or ""))


def _openai_tools(names_extra=True) -> tuple[list, dict]:
    """Tool specs in OpenAI function format, and name -> ("design"|"chat"|"extra") for dispatch."""
    import studio_tools
    specs, kind = [], {}
    for s in design_tools.schemas():
        if s["name"] == "flynn_ask":
            continue
        specs.append(s); kind[s["name"]] = "design"
    for s in studio_tools.schemas():
        if s["name"] in CHAT_TOOLS:
            specs.append(s); kind[s["name"]] = "chat"
    for n, (s, _) in EXTRA.items():
        specs.append(s); kind[n] = "extra"
    return ([{"type": "function", "function": {"name": s["name"], "description": s["description"][:1000],
                                               "parameters": s["inputSchema"]}} for s in specs], kind)


def _summary(name: str, args: dict) -> str:
    for k in ("path", "run", "query", "system", "title", "kind", "name"):
        if args.get(k):
            return str(args[k])[:120]
    return ""


class ApiEngine:
    """engines.py-shaped: run(turn, emit) blocks for the turn; stop() asks it to end after the current piece."""
    name, label = "api", "Model API (aisuite)"

    def __init__(self, conv):
        self.conv = conv
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # history per session (not per conversation folder: a Home conversation that moves into its run keeps it)
    def _hist(self, session: str) -> Path:
        d = Path(getattr(self.conv, "base", self.conv.dir.parent)) / "_api"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{re.sub(r'[^a-z0-9-]', '', session)}.json"

    def run(self, t, emit) -> None:
        me = getattr(self.conv, "me", None) or None
        ch = api_keys.choice(me)
        provider, model = ch.get("provider") or "", ch.get("model") or ""
        try:
            if provider not in PROVIDERS or not model:
                raise ApiUnavailable("choose a provider and a model in Settings (Model API)")
            client = _client(me, provider, ch.get("base_url") or "")
        except (ApiUnavailable, ValueError) as e:
            emit({"t": "error", "text": str(e)})
            emit({"t": "turn_end", "ok": False, "cost": None})
            return
        session = t.session if (t.session or "").startswith("api-") else "api-" + secrets.token_hex(8)
        emit({"t": "session", "id": session})
        hist_p = self._hist(session)
        try:
            history = json.loads(hist_p.read_text()) if hist_p.is_file() else []
        except ValueError:
            history = []
        history.append({"role": "user", "content": t.prompt})
        specs, kind = ([], {}) if getattr(t, "no_tools", False) else _openai_tools()    # no_tools: the model is offered none
        ctx = design_tools.Ctx(me=me, base_url=getattr(__import__("team"), "public_url")() if __import__("team").on() else "",
                               srv=__import__("chat").SRV)
        model_id = f"{PROVIDERS[provider]['aisuite']}:{model}"
        vision = PROVIDERS[provider]["vision"]
        steps = STEPS.get(getattr(self.conv, "effort", "balanced"), 30)
        usage = {"in": 0, "out": 0}
        ok, final, nudges, compacted = False, "", 0, False
        try:
            step = 0
            while step < steps:
                step += 1
                if self._stop.is_set():
                    break
                try:
                    text, calls, reasoning, finish = self._stream(client, model_id, _system_prompt(t), history, specs,
                                                                  emit, usage, provider)
                except _Stopped:
                    break
                except Exception as e:  # noqa: BLE001 - too long: compact once and go again (OpenWorker's compaction)
                    if OVERFLOW.search(str(e)) and not compacted:
                        compacted = True
                        history[:] = _trim(history, 120_000)
                        emit({"t": "activity", "agent": "model", "tool": "compact",
                              "text": "the conversation was too long for the model; older turns were shortened"})
                        step -= 1
                        continue
                    raise
                msg = {"role": "assistant", "content": text or ""}
                if calls:
                    msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                                         for c in calls]
                history.append(msg)
                if text:
                    emit({"t": "assistant_end"})
                    final = text
                if finish == "stopped":
                    break
                if not calls:
                    if finish == "length" and nudges < 2:      # cut off by the output limit: ask it to go on
                        nudges += 1
                        history.append({"role": "user", "content": "Your reply was cut off by the length limit. "
                                        "Continue exactly where you stopped."})
                        continue
                    ok = True
                    break
                images, ran = [], set()
                for c in calls:
                    if self._stop.is_set():
                        break
                    ran.add(c["id"])
                    if c["parsed"] is None:                    # never run a mangled call; say why
                        why = ("cut off by the output limit: send smaller pieces (write the page in parts with "
                               "studio_edit_file)" if finish == "length" else "not valid JSON")
                        emit({"t": "tool", "name": c["name"], "summary": "(arguments unreadable)"})
                        history.append({"role": "tool", "tool_call_id": c["id"],
                                        "content": json.dumps({"error": f"your arguments for {c['name']} were {why}; nothing was done"})})
                        continue
                    args = c["parsed"] if isinstance(c["parsed"], dict) else {}
                    emit({"t": "tool", "name": c["name"], "summary": _summary(c["name"], args)})
                    out = self._call(c["name"], kind.get(c["name"]), args, ctx)
                    img = out.pop("_image", None) if isinstance(out, dict) else None
                    if img and vision:
                        images.append(img)
                    history.append({"role": "tool", "tool_call_id": c["id"],
                                    "content": json.dumps(out, ensure_ascii=False)[:30000]})
                for c in calls:                                # stopped part way: every call still gets an answer
                    if c["id"] not in ran:
                        history.append({"role": "tool", "tool_call_id": c["id"],
                                        "content": json.dumps({"error": "interrupted: the person pressed Stop"})})
                if images:
                    history.append({"role": "user", "content": [{"type": "text", "text": "The screenshot from studio_render:"}]
                                    + [{"type": "image_url", "image_url": {"url": f"data:{i['mime']};base64,{i['data']}"}} for i in images[-2:]]})
                if any(c["name"] == "ask_user" for c in calls):
                    ok = True
                    break                                   # questions end the turn; answers arrive as the next message
            else:
                emit({"t": "error", "text": f"Flynn reached this effort's limit of {steps} steps. Say continue to go on, "
                                            "or pick a higher effort."})
        except Exception as e:  # noqa: BLE001 - shown in the chat, without the key
            emit({"t": "error", "text": _clean(_friendly(e), me, provider)[:500]})
        finally:
            hist_p.write_text(json.dumps(_trim(history), ensure_ascii=False))
        if self._stop.is_set():
            emit({"t": "stopped"})
        emit({"t": "turn_end", "ok": ok and not self._stop.is_set(), "cost": _cost(ch, usage, provider, model), "text": final,
              "tokens": usage})

    def _call(self, name, kind, args, ctx) -> dict:
        try:
            if kind == "design":
                return design_tools.call(name, ctx, args)
            if kind == "chat":
                import studio_tools
                return studio_tools.call(name, self.conv, args, ctx.srv)
            if kind == "extra":
                return EXTRA[name][1](self.conv, args)
            return {"error": f"unknown tool {name}"}
        except Exception as e:  # noqa: BLE001 - the error goes back to the model
            return {"error": f"{type(e).__name__}: {e}"[:1500]}

    # ---- one model step, streamed. Adapted from OpenWorker's turn engine (andrewyng/openworker, MIT): deltas plus one
    # assembled turn; tool calls keyed by index and parsed once at the end; a stalled stream can't hold Stop; retries
    # only before the first chunk; options a provider rejects are dropped and retried ("param-fix").
    USAGE_OPT = {"openai", "deepseek", "xai", "groq", "openrouter", "tongyi", "custom", "ollama", "mistral"}
    STALL = 300.0
    FLUSH = 0.04

    def _open(self, client, model_id, msgs, specs, provider, emit):
        """The stream and its first chunk, retrying before anything was shown: 429 and 5xx back off (Retry-After
        honoured), a rejected option is dropped once."""
        kw = {"stream_options": {"include_usage": True}} if provider in self.USAGE_OPT else {}
        attempt = 0
        while True:
            try:
                it = iter(client.chat.completions.create(model=model_id, messages=msgs, **({"tools": specs} if specs else {}), stream=True, **kw))
                first = next(it, None)
                return it, first
            except Exception as e:  # noqa: BLE001
                text, code = str(e), _status(e)
                if "stream_options" in kw and re.search(r"stream_options|include_usage|unrecognized|unknown|extra|not permitted", text, re.I):
                    kw.pop("stream_options")
                    continue
                if (code in (408, 409, 429, 500, 502, 503, 504, 529) or re.search(r"rate.?limit|overloaded|timed? ?out|temporarily", text, re.I)) and attempt < 4:
                    attempt += 1
                    wait = _retry_after(e) or min(30.0, 2.0 ** attempt)
                    emit({"t": "activity", "agent": "model", "tool": "wait",
                          "text": f"the provider is busy ({code or 'retry'}); trying again in {wait:.0f}s"})
                    if self._stop.wait(wait):
                        raise _Stopped() from None
                    continue
                raise

    def _stream(self, client, model_id, system, history, specs, emit, usage, provider="openai"):
        msgs = [{"role": "system", "content": system}] + history
        text, reasoning, calls, finish = "", "", {}, None
        it, first = self._open(client, model_id, msgs, specs, provider, emit)
        q: "queue.Queue" = queue.Queue(maxsize=256)
        done = object()

        def produce():                        # the provider's iterator runs here, so Stop never waits on it
            try:
                if first is not None:
                    q.put(first)
                for ch in it:
                    if self._stop.is_set():
                        break
                    q.put(ch)
            except Exception as e:  # noqa: BLE001
                q.put(e)
            q.put(done)
        threading.Thread(target=produce, daemon=True).start()
        buf, last_flush, last_chunk = "", time.time(), time.time()

        def flush():
            nonlocal buf, last_flush
            if buf:
                emit({"t": "delta", "text": buf})
                buf = ""
            last_flush = time.time()
        while True:
            if self._stop.is_set():
                break
            try:
                ch = q.get(timeout=0.5)
            except queue.Empty:
                if time.time() - last_chunk > self.STALL:
                    flush()
                    raise RuntimeError(f"the provider stopped sending for {self.STALL:.0f}s")
                if buf and time.time() - last_flush > self.FLUSH:
                    flush()
                continue
            last_chunk = time.time()
            if ch is done:
                break
            if isinstance(ch, Exception):
                flush()
                raise ch
            u = getattr(ch, "usage", None)
            if u:
                cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
                usage["in"] += max(0, int(getattr(u, "prompt_tokens", 0) or 0) - int(cached))
                usage["cached"] = usage.get("cached", 0) + int(cached)
                usage["out"] += int(getattr(u, "completion_tokens", 0) or 0)
                usage["last_prompt"] = int(getattr(u, "prompt_tokens", 0) or 0)
            if not getattr(ch, "choices", None):
                continue
            c0 = ch.choices[0]
            finish = getattr(c0, "finish_reason", None) or finish
            d = c0.delta
            r = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
            if r:
                flush()
                reasoning += r
                emit({"t": "note", "text": r})
            if getattr(d, "content", None):
                text += d.content
                buf += d.content
                if time.time() - last_flush > self.FLUSH:
                    flush()
            for tc in getattr(d, "tool_calls", None) or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    c["id"] = tc.id
                f = tc.function
                if f is not None:
                    if f.name:
                        c["name"] = f.name
                    if f.arguments:
                        c["args"] += f.arguments
        flush()
        if self._stop.is_set():
            return text, [], reasoning, "stopped"     # a stopped step keeps its text, never half a tool call
        out = []
        for i in sorted(calls):
            c = calls[i]
            if c["name"]:
                c["id"] = c["id"] or f"call_{secrets.token_hex(6)}"
                try:
                    c["parsed"] = json.loads(c["args"] or "{}")
                except ValueError:
                    c["parsed"] = None                 # mangled: explained to the model instead of run
                out.append(c)
        return text, out, reasoning, finish


class _Stopped(Exception):
    pass


def _status(e) -> int | None:
    for obj in (e, getattr(e, "response", None)):
        v = getattr(obj, "status_code", None) or getattr(obj, "status", None)
        if isinstance(v, int):
            return v
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(e))
    return int(m.group(1)) if m else None


def _retry_after(e) -> float | None:
    h = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        v = float(h.get("retry-after") or h.get("Retry-After") or 0)
        return min(v, 60.0) if v > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


OVERFLOW = re.compile(r"context.{0,20}(length|window|limit)|maximum context|too many tokens|prompt is too long|"
                      r"context_length_exceeded|reduce the length|input is too long", re.I)


def _trim(history: list, limit: int = 400_000) -> list:
    """Keep the conversation under a size the models take: drop the oldest turns (and old pictures) first."""
    h = [m if not isinstance(m.get("content"), list) or i >= len(history) - 4 else {"role": m["role"], "content": "(a screenshot)"}
         for i, m in enumerate(history)]
    while len(json.dumps(h)) > limit and len(h) > 6:
        h.pop(0)
        while h and h[0].get("role") != "user":
            h.pop(0)
    return h


def prices() -> dict:
    """USD per million tokens, keyed provider:model (api_prices.json; the person's own prices in Settings win)."""
    try:
        return json.loads((HERE / "api_prices.json").read_text()).get("prices", {})
    except (OSError, ValueError):
        return {}


def _cost(ch: dict, usage: dict, provider: str = "", model: str = ""):
    p = ch.get("prices") or prices().get(f"{provider}:{model}") or {}
    try:
        if not (p.get("in") and p.get("out")):
            return None
        cached = usage.get("cached", 0) * float(p.get("cached", p["in"]))
        return round((usage["in"] * float(p["in"]) + cached + usage["out"] * float(p["out"])) / 1e6, 4)
    except (TypeError, ValueError, KeyError):
        return None


def _friendly(e: Exception) -> str:
    """Provider errors in plain words (after OpenWorker's friendly_model_error)."""
    t, code = str(e), _status(e)
    if code == 401 or re.search(r"invalid.{0,10}(api.?key|x-api-key)|unauthori[sz]ed|authentication", t, re.I):
        return "The provider refused the API key. Check it in Settings (Model API)."
    if code == 402 or re.search(r"insufficient|quota|billing|credit", t, re.I):
        return "The provider says the account has no credit or quota left."
    if code == 404 or re.search(r"model.{0,30}(not found|does not exist|unknown)", t, re.I):
        return "The provider doesn't know that model. Pick one with Load models in Settings."
    if code == 429:
        return "The provider is rate-limiting this key; wait a minute and send again."
    if OVERFLOW.search(t):
        return "The conversation is too long for this model even after shortening it; start a new conversation."
    return f"{type(e).__name__}: {t}"


def _clean(text: str, me, provider: str) -> str:
    k = api_keys.get(me, provider)
    return text.replace(k, "…") if k else text
