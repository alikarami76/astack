"""Studio's App builder: Vite + React + TypeScript apps in their own git repos (backend only).

Each app lives in <root>/<name> (env FLYNN_APPS_DIR, else ~/flynn-apps) and is versioned with git.
npm install and the dev-server preview run as background processes; logs go to <app>/.flynn/.
Routes are dispatched from studio.py the same way chat.py's are.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import datetime as _dt
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat  # noqa: E402

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
TEMPLATE = HERE / "app_template"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
HIDDEN = {"node_modules", ".git", "dist", ".flynn"}
MAX_READ = 300 * 1024
PORTS = range(5200, 5300)

_lock = threading.Lock()
_previews: dict[str, subprocess.Popen] = {}
_installs: dict[str, dict] = {}


def apps_root() -> Path:
    return Path(os.environ.get("FLYNN_APPS_DIR") or Path.home() / "flynn-apps")


def npm_bin() -> str:
    return os.environ.get("FLYNN_NPM") or "npm"


def _slug(name: str) -> str:
    if not isinstance(name, str) or not SLUG.match(name):
        raise ValueError("app name must be lowercase letters, digits and dashes (2 to 41 characters)")
    return name


def app_dir(name: str) -> Path:
    d = apps_root() / _slug(name)
    if not d.is_dir():
        raise KeyError(f"no app named {name}")
    return d


def _git(d: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "user.name=flynn", "-c", "user.email=flynn@localhost", "-c", "commit.gpgsign=false", *args]
    r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=60)
    if check and r.returncode:
        raise RuntimeError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def _tail(p: Path, n: int = 20) -> list[str]:
    try:
        return p.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------- themes ----------

def _tokens_css(tokens: list) -> str:
    seen, lines = set(), []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        var, val = t.get("css_var"), t.get("value")
        if not var or val is None or var in seen or not re.match(r"^--[A-Za-z0-9_-]+$", str(var)):
            continue
        if re.search(r"[;{}]", str(val)):
            continue
        seen.add(var)
        lines.append(f"  {var}: {val};")
    return ":root {\n" + "\n".join(lines) + "\n}\n" if lines else ""


def _root_block(css: str) -> str:
    m = re.search(r"(^|\n)\s*:root\s*\{", css)
    if not m:
        return ""
    i, depth = css.index("{", m.start()), 0
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[m.start():j + 1].strip() + "\n"
    return ""


SYSTEMS_DIR = FLYNN / "systems"
THEMES_DIR = FLYNN / "adapters" / "html" / "themes"


def _theme_css(system: str | None) -> str:
    if not system:
        return ""
    try:
        if system == "admin-panel":
            return _tokens_css(json.loads((SYSTEMS_DIR / "admin-panel" / "tokens.json").read_text()))
        css = THEMES_DIR / system / "style.css"
        return _root_block(css.read_text()) if css.is_file() else ""
    except (OSError, ValueError):
        return ""


# ---------- create / info ----------

def create_app(name: str, description: str, system: str | None = None) -> dict:
    _slug(name)
    description = str(description or "").strip()
    if system is not None and system != "" and not isinstance(system, str):
        raise ValueError("system must be text")
    system = system or None
    if system and not SLUG.match(system):
        raise ValueError("unknown design system name")
    d = apps_root() / name
    if d.exists():
        raise FileExistsError(f"{name} already exists")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, d)
    theme = _theme_css(system)
    subs = {"__NAME__": name, "__NAME_HTML__": html.escape(name), "__NAME_JSON__": json.dumps(name),
            "__DESCRIPTION_JSON__": json.dumps(description, ensure_ascii=False),
            "__THEME_IMPORT__": 'import "./theme.css";' if theme else ""}
    for f in d.rglob("*"):
        if f.is_file():
            t = f.read_text()
            for k in sorted(subs, key=len, reverse=True):
                t = t.replace(k, subs[k])
            f.write_text(t)
    if theme:
        (d / "src" / "theme.css").write_text(theme)
    created = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    meta = {"name": name, "description": description, "system": system, "created": created, "ai": True}
    (d / "app.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    _git(d, "init", "-q")
    commit(name, f"Create {name}")
    return app_info(name)


def app_info(name: str) -> dict:
    d = app_dir(name)
    m = _read_json(d / "app.json")
    v = versions(name, 1)
    return {"name": name, "description": m.get("description", ""), "system": m.get("system"),
            "created": m.get("created"), "installed": (d / "node_modules").is_dir(),
            "preview": preview_state(name), "head": v[0] if v else None,
            "ai": bool(m.get("ai")), "ai_usage": ai_usage(name)}


AI_FILES = ("flynn-ai.ts", "src/lib/ai.ts")
AI_DEP = ("@anthropic-ai/claude-agent-sdk", "0.3.289")


def ai_usage(name: str) -> dict:
    """The app's AI calls and spend (flynn-ai.ts logs every call to .flynn/ai-usage.jsonl)."""
    p = app_dir(name) / ".flynn" / "ai-usage.jsonl"
    calls, cost, today = 0, 0.0, 0.0
    day = _dt.date.today().isoformat()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            calls += 1
            cost += float(e.get("cost") or 0)
            if str(e.get("at", "")).startswith(day):
                today += float(e.get("cost") or 0)
    return {"calls": calls, "cost": round(cost, 4), "today": round(today, 4)}


def enable_ai(name: str) -> dict:
    """Give an existing app the AI endpoint and client (new apps have them from the template), then reinstall."""
    d = app_dir(name)
    for rel in AI_FILES:
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (d / rel).exists():
            shutil.copy(TEMPLATE / rel, d / rel)
    vc = d / "vite.config.ts"
    t = vc.read_text() if vc.exists() else (TEMPLATE / "vite.config.ts").read_text()
    if "flynnAi" not in t:
        t = t.replace('import react from "@vitejs/plugin-react";', 'import react from "@vitejs/plugin-react";\nimport { flynnAi } from "./flynn-ai";', 1)
        t = re.sub(r"plugins:\s*\[([^\]]*)\]", lambda m: f"plugins: [{m.group(1).strip()}, flynnAi()]", t, count=1)
        if "flynnAi()" not in t:
            raise ValueError("couldn't add the AI plugin to vite.config.ts; add `flynnAi()` to its plugins by hand")
        vc.write_text(t)
    pj = d / "package.json"
    pkg = json.loads(pj.read_text())
    pkg.setdefault("dependencies", {})[AI_DEP[0]] = AI_DEP[1]
    pkg.setdefault("devDependencies", {}).setdefault("@types/node", "20.14.10")
    pj.write_text(json.dumps(pkg, indent=2) + "\n")
    meta = _read_json(d / "app.json")
    meta["ai"] = True
    (d / "app.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    commit(name, "Enable AI features (flynn-ai.ts, src/lib/ai.ts)")
    install(name)
    return app_info(name)


def list_apps() -> list[dict]:
    r = apps_root()
    if not r.is_dir():
        return []
    out = []
    for d in sorted(r.iterdir()):
        if d.is_dir() and SLUG.match(d.name) and (d / "app.json").exists():
            try:
                out.append(app_info(d.name))
            except (RuntimeError, OSError):
                pass
    return out


# ---------- npm install ----------

PUBLIC_REGISTRY = "https://registry.npmjs.org/"


def normalize_lock(d: Path) -> bool:
    """package-lock.json keeps public registry addresses: a team server's package cache (FLYNN_NPM_PROXY) is how this
    computer downloads, never part of the app, so the same app installs anywhere."""
    proxy = (os.environ.get("FLYNN_NPM_PROXY") or "").rstrip("/") + "/"
    lock = d / "package-lock.json"
    if proxy == "/" or not lock.is_file():
        return False
    t = lock.read_text()
    if proxy not in t:
        return False
    lock.write_text(t.replace(proxy, PUBLIC_REGISTRY))
    return True

def install(name: str) -> dict:
    d = app_dir(name)
    with _lock:
        if _installs.get(name, {}).get("state") == "running":
            raise RuntimeError("install is already running")
        (d / ".flynn").mkdir(exist_ok=True)
        log = open(d / ".flynn" / "install.log", "w")
        try:
            p = subprocess.Popen([npm_bin(), "install"], cwd=d, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL)
        except OSError as e:
            log.close()
            raise RuntimeError(f"cannot run npm: {e}")
        st = {"state": "running", "code": None}
        _installs[name] = st

    def wait():
        code = p.wait()
        if code != 0 and os.environ.get("FLYNN_NPM_PROXY"):
            # the team server's package cache didn't answer: install from the public registry instead (slower)
            log.write("\n[flynn] the team server's package cache failed; installing from the public npm registry\n")
            log.flush()
            try:
                code = subprocess.Popen([npm_bin(), "install", "--registry", PUBLIC_REGISTRY], cwd=d, stdout=log,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).wait()
            except OSError:
                pass
        if code == 0:
            normalize_lock(d)
        log.close()
        with _lock:
            st.update(state="done" if code == 0 else "failed", code=code)
    threading.Thread(target=wait, daemon=True).start()
    return install_state(name)


def install_state(name: str) -> dict:
    d = app_dir(name)
    with _lock:
        st = dict(_installs.get(name) or {"state": "idle", "code": None})
    return {**st, "tail": _tail(d / ".flynn" / "install.log")}


# ---------- preview ----------

def _free_port() -> int:
    used = {p for p in (_port_of(n) for n in _previews) if p}
    for port in PORTS:
        if port in used:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free preview port in 5200-5299")


_ports: dict[str, int] = {}


def _port_of(name: str) -> int | None:
    return _ports.get(name)


def _alive(name: str) -> bool:
    p = _previews.get(name)
    return bool(p and p.poll() is None)


def start_preview(name: str) -> dict:
    d = app_dir(name)
    with _lock:
        if _alive(name):
            port = _ports[name]
            return {"port": port, "url": f"http://127.0.0.1:{port}/"}
        if not (d / "node_modules").is_dir():
            raise RuntimeError("install the app's packages first")
        port = _free_port()
        (d / ".flynn").mkdir(exist_ok=True)
        log = open(d / ".flynn" / "preview.log", "w")
        try:
            p = subprocess.Popen([npm_bin(), "run", "dev", "--", "--port", str(port), "--strictPort",
                                  "--host", "127.0.0.1"], cwd=d, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise RuntimeError(f"cannot run npm: {e}")
        finally:
            log.close()
        _previews[name], _ports[name] = p, port
    return {"port": port, "url": f"http://127.0.0.1:{port}/"}


def preview_state(name: str) -> dict:
    d = app_dir(name)
    with _lock:
        run = _alive(name)
        port = _ports.get(name) if run else None
    return {"running": run, "port": port, "url": f"http://127.0.0.1:{port}/" if port else None,
            "tail": _tail(d / ".flynn" / "preview.log")}


def _kill(p: subprocess.Popen) -> None:
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                p.kill()
            p.wait(timeout=5)


def stop_preview(name: str) -> dict:
    app_dir(name)
    with _lock:
        p = _previews.pop(name, None)
        _ports.pop(name, None)
    if p:
        _kill(p)
    return preview_state(name)


def stop_all() -> None:
    with _lock:
        ps = list(_previews.values())
        _previews.clear()
        _ports.clear()
    for p in ps:
        _kill(p)


# ---------- files ----------

def files(name: str) -> list[dict]:
    d = app_dir(name)
    out = []
    for root, dirs, fs in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x not in HIDDEN)
        for f in sorted(fs):
            p = Path(root) / f
            if p.is_file() and not p.is_symlink():
                out.append({"path": p.relative_to(d).as_posix(), "size": p.stat().st_size})
    return out


def read_file(name: str, rel: str) -> str:
    d = app_dir(name).resolve()
    try:
        p = (d / str(rel or "")).resolve()
        inside = p.relative_to(d)
    except (ValueError, OSError):
        raise KeyError("path is outside the app")
    if not rel or HIDDEN & set(inside.parts) or not p.is_file():
        raise KeyError(f"no file {rel}")
    if p.stat().st_size > MAX_READ:
        raise ValueError("file is larger than 300 KB")
    b = p.read_bytes()
    if b"\0" in b:
        raise ValueError("binary file")
    try:
        return b.decode()
    except UnicodeDecodeError:
        raise ValueError("binary file")


# ---------- versions ----------

def versions(name: str, limit: int = 50) -> list[dict]:
    d = app_dir(name)
    r = _git(d, "log", f"-n{max(1, int(limit))}", "--format=%H%x1f%s%x1f%aI", check=False)
    out = []
    for line in r.stdout.splitlines():
        h, _, rest = line.partition("\x1f")
        msg, _, t = rest.partition("\x1f")
        out.append({"hash": h, "message": msg, "time": t})
    return out


def commit(name: str, message: str) -> dict | None:
    d = app_dir(name)
    message = str(message or "").strip()
    normalize_lock(d)
    if not message:
        raise ValueError("commit message is empty")
    _git(d, "add", "-A")
    if not _git(d, "status", "--porcelain").stdout.strip():
        return None
    _git(d, "commit", "-q", "-m", message)
    return versions(name, 1)[0]


def restore(name: str, hash: str) -> dict | None:
    d = app_dir(name)
    h = str(hash or "").lower()
    if not re.match(r"^[0-9a-f]{4,40}$", h):
        raise ValueError("bad version id")
    full = next((v["hash"] for v in versions(name, 100000) if v["hash"].startswith(h)), None)
    if not full:
        raise KeyError("no such version")
    _git(d, "checkout", full, "--", ".")
    return commit(name, f"Restore {full[:7]}")


# ---------- health ----------

def health() -> list[dict]:
    out = []
    for i, label, cmd, fix in (("node", "Node.js", ["node", "-v"], "Install Node 18 or newer (brew install node)"),
                               ("npm", "npm", [npm_bin(), "-v"], "Install Node, which includes npm"),
                               ("git", "git", ["git", "--version"], "xcode-select --install")):
        found = shutil.which(cmd[0]) or (cmd[0] if os.path.exists(cmd[0]) else None)
        if not found:
            out.append({"id": i, "label": label, "status": "fail", "detail": "not found", "fix": fix})
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            v = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "?"
            out.append({"id": i, "label": label, "status": "ok" if r.returncode == 0 else "warn",
                        "detail": v, "fix": "" if r.returncode == 0 else fix})
        except (OSError, subprocess.TimeoutExpired):
            out.append({"id": i, "label": label, "status": "warn", "detail": "could not check", "fix": fix})
    return out


# ---------- operations (the same table runs here, or on a person's own Mac through their Studio agent) ----------

def _ops(name: str | None, args: dict) -> dict:
    a = args if isinstance(args, dict) else {}
    return {
        "list": lambda: {"apps": list_apps()},
        "info": lambda: app_info(name),
        "files": lambda: {"files": files(name)},
        "file": lambda: {"path": a.get("path", ""), "content": read_file(name, a.get("path", ""))},
        "versions": lambda: {"versions": versions(name)},
        "install_state": lambda: install_state(name),
        "create": lambda: create_app(a.get("name"), a.get("description", ""), a.get("system")),
        "install": lambda: install(name),
        "preview/start": lambda: start_preview(name),
        "preview/stop": lambda: stop_preview(name),
        "ai/enable": lambda: enable_ai(name),
        "commit": lambda: {"commit": commit(name, a.get("message"))},
        "restore": lambda: {"commit": restore(name, a.get("hash"))},
    }


CHANGES = ("create", "commit", "restore", "ai/enable")   # after these the agent saves the app's source on the server


def local_op(op: str, name: str | None, args: dict | None = None) -> dict:
    calls = _ops(name, args or {})
    if op not in calls:
        raise ValueError(f"unknown app operation {op}")
    return calls[op]()


class Offline(RuntimeError):
    """The person's computer (their flynn Studio agent) isn't connected, so their apps can't be reached."""


def run_op(me: dict | None, op: str, name: str | None, args: dict | None = None, timeout: float = 90) -> dict:
    """An App builder operation: here (Studio on your own Mac), or on the signed-in person's own Mac through their
    flynn Studio agent (a team server; the app's files, packages and preview live there, never on the server)."""
    if not chat.team.on():
        return local_op(op, name, args)
    import agent_hub
    job = {"kind": "app", "op": op, "name": name, "args": args or {}}
    if op == "list" and agent_hub.APPS_FOR is not None:      # copies saved on the server, for a new computer
        d = agent_hub.APPS_FOR((me or {}).get("email", ""))
        job["saved"] = sorted(f.stem for f in Path(d).glob("*.tgz")) if d and Path(d).is_dir() else []
    r = agent_hub.call((me or {}).get("email", ""), job, timeout)
    if r is None:
        raise Offline("Your computer isn't connected, so your apps can't be reached. Apps are built and previewed on "
                      "your own Mac: connect it in Settings (Your computer), then reload.")
    if r.get("ok"):
        return r.get("data") or {}
    err = str(r.get("error") or "the app operation failed")
    raise {"value": ValueError, "key": KeyError, "exists": FileExistsError}.get(r.get("kind"), RuntimeError)(err)


def op_error(e: Exception) -> dict:
    kind = "value" if isinstance(e, ValueError) else "key" if isinstance(e, KeyError) else \
        "exists" if isinstance(e, FileExistsError) else "runtime"
    return {"ok": False, "kind": kind, "error": (str(e.args[0]) if e.args else str(e))[:400]}


# ---------- HTTP ----------

def _q(query: dict, k: str, default=None):
    v = (query or {}).get(k, default)
    return v[0] if isinstance(v, list) and v else (default if isinstance(v, list) else v)


def _guard(h, fn):
    try:
        return fn()
    except ValueError as e:
        h._json({"error": str(e)}, 400)
    except KeyError as e:
        h._json({"error": str(e.args[0]) if e.args else "not found"}, 404)
    except Offline as e:
        h._json({"error": str(e), "offline": True}, 503)
    except (FileExistsError, RuntimeError) as e:
        h._json({"error": str(e)}, 409)
    return True


def _name(h, parts: list[str]) -> str | None:
    n = unquote(parts[2])
    if not SLUG.match(n):
        h._json({"error": f"no app named {n}"}, 404)
        return None
    return n


def handle_get(h, parts: list[str], query: dict) -> bool:
    if parts[:2] != ["api", "apps"] or len(parts) > 4:
        return False
    if not chat.host_ok(h):
        h._json({"error": "host not allowed"}, 403)
        return True
    me = getattr(h, "me", None)
    if len(parts) == 2:
        return _guard(h, lambda: h._json(run_op(me, "list", None)) or True)
    n = _name(h, parts)
    if n is None:
        return True
    what = parts[3] if len(parts) == 4 else None
    ops = {None: "info", "files": "files", "file": "file", "versions": "versions", "install": "install_state"}
    if what not in ops:
        return False
    _guard(h, lambda: h._json(run_op(me, ops[what], n, {"path": _q(query, "path", "")})))
    return True


def handle_post(h, parts: list[str], body: dict) -> bool:
    if parts[:2] != ["api", "apps"] or len(parts) > 5:
        return False
    if not chat.origin_ok(h):
        h._json({"error": "origin not allowed"}, 403)
        return True
    body = body if isinstance(body, dict) else {}
    me = getattr(h, "me", None)
    if len(parts) == 2:
        _guard(h, lambda: h._json(run_op(me, "create", None, {k: body.get(k) for k in ("name", "description", "system")},
                                         timeout=180), 201))
        return True
    n = _name(h, parts)
    if n is None:
        return True
    what = "/".join(parts[3:])
    if what not in ("install", "preview/start", "preview/stop", "ai/enable", "commit", "restore"):
        return False
    _guard(h, lambda: h._json(run_op(me, what, n, {k: body.get(k) for k in ("message", "hash")},
                                     timeout=240 if what == "ai/enable" else 90)))
    return True
