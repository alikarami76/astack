"""flynn's builder: a second canvas account and its own hidden Chrome, for working on files the user does not have open.

the user approved this account on 2026-10-04. Why it exists: the canvas connector (MCP) keeps one connection per
account, and the user's tab holds the `flynn` account's connection, so flynn cannot reach a library file through it.
The builder account (`builder@flynn.local`) is an editor in the flynn team with its own MCP key; its hidden
Chrome opens the file flynn needs, and its edits show up live for the user like a collaborator's. the user's tab is never
touched.

  builder.py setup     create the account (Penpot's admin CLI), enable MCP, add it to the flynn team as editor
  builder.py open ID   open a file in the hidden Chrome and wait until the connector reaches it
  builder.py status    is the hidden Chrome running, which file is open
  builder.py close     quit the hidden Chrome

Secrets (builder password, MCP key; accounts builder-password and builder-mcp-key) go through scripts/fsecrets.py
(env, secrets folder, or the keychain, service flynn-penpot) and are never printed. The hidden Chrome has its own profile (~/.flynn/builder-chrome) and
DevTools port (9224).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "penpot"))
from bridge import Bridge, PenpotError, _keychain  # noqa: E402

BASE = os.environ.get("FLYNN_PENPOT_URL", "http://localhost:9001")
RPC = os.environ.get("FLYNN_PENPOT_RPC", "http://127.0.0.1:9001/api/rpc/command/")
EMAIL = "builder@flynn.local"
OWNER_EMAIL = os.environ.get("FLYNN_OWNER_EMAIL") or _keychain("owner-email") or "owner@flynn.local"   # bin/flynn-desk-setup
TEAM_NAME = os.environ.get("FLYNN_HOME_TEAM", "flynn")          # the team flynn's service account builds in
PORT = int(os.environ.get("FLYNN_BUILDER_PORT", "9224"))
FLYNN_HOME = Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn")))
# In a container the profile stays inside it: the server and the setup job share the data volume, and Chromium
# refuses a profile another container's Chrome has locked. The builder signs in again whenever it starts.
PROFILE = Path(os.environ.get("FLYNN_BUILDER_PROFILE") or
               ("/tmp/flynn-builder-chrome" if sys.platform.startswith("linux") else FLYNN_HOME / "builder-chrome"))
CHROME = os.environ.get("FLYNN_CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
BACKEND = "flynn-penpot-penpot-backend-1"
LOG = FLYNN_HOME / "logs" / "builder.log"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")


def _kc_set(account: str, value: str) -> None:
    import fsecrets                                   # on sys.path through bridge
    fsecrets.set(account, value)


class Rpc:
    """A logged-in session on the canvas backend."""

    def __init__(self, email: str, password: str):
        import requests
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        self("login-with-password", {"email": email, "password": password})
        # On an https address Penpot marks its session cookie Secure, and this session talks to the backend over plain
        # http inside the container, so the cookie would never be sent back (2026-10-07, the hosting platform: every call after
        # sign-in got 401). The backend is local; send the cookie anyway.
        for c in self.s.cookies:
            c.secure = False

    def __call__(self, cmd: str, params: dict | None = None):
        r = self.s.post(RPC + cmd, json=params or {})
        if r.status_code >= 400:
            raise PenpotError(f"{cmd} failed ({r.status_code}): {r.text[:300]}")
        return r.json() if r.text.strip() else None


def owner() -> Rpc:
    return Rpc(OWNER_EMAIL, _keychain("password") or "")


def builder() -> Rpc:
    return Rpc(EMAIL, _keychain("builder-password") or "")


def team_id(rpc: Rpc) -> str:
    return next(t["id"] for t in rpc("get-teams") if t["name"] == TEAM_NAME)


# ---------- setup ----------

def setup() -> dict:
    pw = _keychain("builder-password")
    if not pw:
        pw = secrets.token_urlsafe(24)
        # Penpot's own admin CLI, because sign-up is switched off on this canvas. The values travel as
        # environment variables of the exec, never as printed text.
        r = subprocess.run(
            [DOCKER, "exec", "-e", "N=flynn builder", "-e", f"E={EMAIL}", "-e", f"P={pw}", BACKEND, "sh", "-c",
             'cd /opt/penpot/backend && ./manage.py create-profile -n "$N" -e "$E" --password="$P" --skip-tutorial --skip-walkthrough'],
            capture_output=True, text=True)
        if r.returncode != 0 and "exist" not in (r.stdout + r.stderr).lower():
            raise PenpotError("could not create the builder account: " + (r.stderr or r.stdout)[-300:])
        _kc_set("builder-password", pw)
        log("builder account created")
    b = builder()
    if not _keychain("builder-mcp-key"):
        t = b("create-access-token", {"name": "flynn-builder-mcp", "type": "mcp"})
        _kc_set("builder-mcp-key", t["token"])
        log("builder mcp key created")
    b("update-profile-props", {"props": {"mcpEnabled": True}})
    o = owner()
    tid = team_id(o)
    if not any(t["id"] == tid for t in b("get-teams")):
        o("create-team-invitations", {"teamId": tid, "emails": [EMAIL], "role": "editor"})
        # Penpot adds an existing account straight away; an invitation is left only for unknown emails.
        if not any(t["id"] == tid for t in b("get-teams")):
            tok = o("get-team-invitation-token", {"teamId": tid, "email": EMAIL})
            b("verify-token", {"token": tok.get("token") if isinstance(tok, dict) else tok})
        log("builder joined the flynn team as editor")
    return {"account": EMAIL, "team": tid, "member": any(t["id"] == tid for t in b("get-teams"))}


# ---------- hidden Chrome ----------

def _tabs() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=1.5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def running() -> bool:
    try:
        _tabs()
        return True
    except Exception:
        return False


def start(headless: bool = True) -> None:
    if running():
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE, 0o700)
    args = [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1600,1000", "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"]
    if headless:
        args.append("--headless=new")
    if sys.platform.startswith("linux"):              # in the team server's container: no user namespaces, small /dev/shm
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    args.append("about:blank")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(75):
        if running():
            log(f"builder chrome started (headless={headless})")
            return
        time.sleep(0.2)
    raise PenpotError("the builder's Chrome did not start")


def _driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    drv = os.environ.get("FLYNN_CHROMEDRIVER") or \
        SeleniumManager().binary_paths(["--browser", "chrome", "--skip-driver-in-path"])["driver_path"]
    o = Options()
    o.debugger_address = f"127.0.0.1:{PORT}"
    return webdriver.Chrome(options=o, service=Service(drv))


def _login(d) -> None:
    """Sign the builder in. `?app=1` skips the landing page signed-out visitors get at / (team server)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    d.get(f"{BASE}/?app=1#/auth/login")
    try:
        WebDriverWait(d, 30).until(lambda drv: "auth/login" not in drv.current_url
                                   or drv.find_elements(By.CSS_SELECTOR, "input[type=email], input[name=email]"))
    except Exception:  # noqa: BLE001 - the checks below say what is wrong
        pass
    if "auth/login" not in d.current_url:
        return
    d.find_element(By.CSS_SELECTOR, "input[type=email], input[name=email]").send_keys(EMAIL)
    d.find_element(By.CSS_SELECTOR, "input[type=password]").send_keys(_keychain("builder-password") or "")
    d.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
    WebDriverWait(d, 30).until(lambda drv: "auth/login" not in drv.current_url)


def bridge(timeout: float = 120) -> Bridge:
    return Bridge(key=_keychain("builder-mcp-key"), timeout=timeout)


def renew_key() -> None:
    """A fresh connector (MCP) key for the builder, saved before the file opens. Penpot keeps one MCP key per account
    and making a new one silently ends the old, so a saved key can be dead (2026-10-10, team server: every build
    failed with "No Penpot instance connected for user token"). The page opened next connects with this one."""
    b = builder()
    t = b("create-access-token", {"name": "flynn-builder-mcp", "type": "mcp"})
    _kc_set("builder-mcp-key", t["token"])
    b("update-profile-props", {"props": {"mcpEnabled": True}})
    log("builder mcp key renewed")


def file_team(file_id: str) -> str | None:
    """The team a file really belongs to, asked of Penpot. A team passed in by callers is only a hint: a new letter
    had none, so the build used the server-wide "last team" setting, opened the file under someone else's team and
    fell back to the person's own tab (2026-10-10)."""
    for rpc in (builder, owner):
        try:
            r = rpc()
            f = r("get-file", {"id": file_id})
            pid = f.get("projectId") or f.get("project-id")
            return r("get-project", {"id": pid}).get("teamId")
        except Exception:  # noqa: BLE001 - this account can't see the file; try the next
            continue
    return None


def ensure_member(team: str) -> None:
    """Make the builder an editor of `team` (the service account adds it). Setup only adds it to the home team, so
    it could not open letters in an org's own team (2026-10-10)."""
    b = builder()
    if any(t["id"] == team for t in b("get-teams")):
        return
    o = owner()
    o("create-team-invitations", {"teamId": team, "emails": [EMAIL], "role": "editor"})
    if not any(t["id"] == team for t in b("get-teams")):      # an invitation instead of a direct add
        tok = o("get-team-invitation-token", {"teamId": team, "email": EMAIL})
        b("verify-token", {"token": tok.get("token") if isinstance(tok, dict) else tok})
    log(f"builder joined team {team} as editor")


# Console errors and the page's WebSocket opens and closes (keys masked), kept in window.__fl for _diag.
RECORDER_JS = r"""
(() => {
  const log = window.__fl = [];
  const push = (...a) => { try { log.push(a.map((x) => String(x && x.stack ? x.message : x)).join(' ').slice(0, 300)); if (log.length > 150) log.shift(); } catch (e) {} };
  for (const k of ['error', 'warn']) { const o = console[k]; console[k] = (...a) => { push(k.toUpperCase(), ...a); return o.apply(console, a); }; }
  const ol = console.log; console.log = (...a) => { if (/mcp|plugin/i.test(String(a[0]))) push('LOG', ...a); return ol.apply(console, a); };
  window.addEventListener('error', (e) => push('PAGEERROR', e.message));
  window.addEventListener('unhandledrejection', (e) => push('REJECTION', e.reason && (e.reason.message || e.reason)));
  const WS = window.WebSocket;
  const Wrapped = function (u, p) {
    push('WS open', String(u).replace(/(userToken|token)=[^&]+/g, '$1=*'));
    const w = p === undefined ? new WS(u) : new WS(u, p);
    w.addEventListener('close', (e) => push('WS close', String(u).split('?')[0], e.code, e.reason));
    w.addEventListener('error', () => push('WS error', String(u).split('?')[0]));
    return w;
  };
  Wrapped.prototype = WS.prototype;
  Object.assign(Wrapped, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket = Wrapped;
})();
"""


def open_file(file_id: str, team: str | None = None, wait: float = float(os.environ.get("FLYNN_BUILDER_WAIT", "120"))) -> Bridge:
    """Open a file in the hidden Chrome and return a bridge that reaches it."""
    if sys.platform.startswith("linux"):
        # On the team server every open starts from a fresh browser profile. Only the first open in a profile ever
        # connected Penpot's MCP plugin there (2026-10-10/11: every later open failed with "No Penpot instance
        # connected"), and a fresh start costs a few seconds and a sign-in.
        close()
        for _ in range(50):                            # until the old Chrome has really gone
            if not running():
                break
            time.sleep(0.2)
        import shutil
        shutil.rmtree(PROFILE, ignore_errors=True)
    start()
    team = file_team(file_id) or team or team_id(builder())
    member_err = ""
    try:
        ensure_member(team)
    except Exception as e:  # noqa: BLE001 - the open below reports what is wrong
        member_err = f"; it could not join team {team}: {str(e)[:200]}"
        log(f"could not add the builder to team {team}: {e}")
    d = _driver()
    try:
        handles = d.window_handles
        for h in handles[1:]:                       # one file at a time
            d.switch_to.window(h)
            d.close()
        d.switch_to.window(d.window_handles[0])
        if urlparse(BASE).netloc not in d.current_url or "auth/login" in d.current_url:
            _login(d)
        # A full page load, never a hash change: the key was just renewed, and only a fresh load makes the plugin
        # connect with it (a hash change kept the old, cancelled key: every open after the first failed, 2026-10-10).
        # Penpot starts its MCP plugin from the account it keeps in the browser's storage, so a copy saved while
        # mcp-enabled was off kept it off on every later load (the server's builder, 2026-10-11). Clear that storage
        # on a plain file of the same site (the sign-in is a cookie and stays).
        d.get(f"{BASE}/js/config.js")
        d.execute_script("try { localStorage.clear(); sessionStorage.clear(); } catch (e) {}")
        d.get("about:blank")
        # Only now, with no workspace open: a page still open when the old key is cancelled sees its connection drop
        # and switches the account's mcp-enabled off, so the next page never starts the plugin (2026-10-11).
        renew_key()
        try:                                           # a record of what the page says, read by _diag on failure
            d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER_JS})
        except Exception:  # noqa: BLE001
            pass
        d.get(f"{BASE}/#/workspace?team-id={team}&file-id={file_id}")
    finally:
        d.quit()
    end, last, nudged = time.time() + wait, None, 0.0
    while time.time() < end:
        # Penpot keeps MCP with an older session of this account (a page closed a moment ago still counts), and
        # the new page offers "Connect here" instead of connecting: take it over, and again if it slips.
        if time.time() > nudged and time.time() > end - wait + 8:
            nudged = time.time() + 20
            _nudge_mcp()
        try:
            cur = bridge(timeout=15).current()
            if cur.get("file") == file_id:
                log(f"builder opened {file_id}")
                return bridge()
            last = cur
        except Exception as e:  # noqa: BLE001 - the plugin is still loading (errors or plain timeouts)
            last = f"{type(e).__name__}: {e}"[:200]
        time.sleep(1.5)
    _diag(file_id)
    raise PenpotError(f"the builder could not reach file {file_id} in team {team}{member_err} (last: {last})")


NUDGE_JS = r"""
const b = document.querySelector('[data-testid=mcp-btn]');
if (!b) return 'no MCP button';
const dot = b.querySelector('[class*=mcp-button-dot]');
if (dot && /connected/.test(dot.className) && !/disconnected/.test(dot.className)) return 'already connected';
b.click();
await new Promise((r) => setTimeout(r, 1500));
const menu = document.querySelector('[class*=toolbar-mcp-menu]');
// the innermost element saying "Connect here" (Penpot shows it when another session of this account held MCP);
// clicking an outer menu item did not take the connection over (2026-10-11)
const leaves = menu ? [...menu.querySelectorAll('*')].filter((e) => e.children.length === 0) : [];
const hit = leaves.find((e) => /connect|enable|start|reconnect/i.test(e.innerText || '') && !/disconnect/i.test(e.innerText || ''));
const text = menu ? menu.innerText.replace(/\s+/g, ' ').slice(0, 300) : '(no menu)';
if (hit) hit.click();
return 'menu: ' + text + (hit ? ' | clicked: ' + hit.innerText.trim() : ' | nothing to click');
"""


def _nudge_mcp() -> None:
    """Penpot sometimes loads a file without starting its MCP plugin although MCP is on (the team server, 2026-10-11).
    Do what a person would: press the toolbar's MCP button and its connect entry."""
    try:
        d = _driver()
        try:
            res = d.execute_async_script("const done = arguments[arguments.length - 1]; (async () => {" + NUDGE_JS + "})().then(done, (e) => done('nudge error: ' + e));")
            log(f"mcp nudge: {res}")
            global _LAST_NUDGE
            _LAST_NUDGE = str(res)[:400]
        finally:
            d.quit()
    except Exception as e:  # noqa: BLE001
        log(f"mcp nudge failed: {e}")


_LAST_NUDGE = ""


def _diag(file_id: str) -> None:
    """When a file would not open: what the builder's tab shows (a screenshot, the address and the visible text) in
    runs/<today>-builder-diag/, which an admin's flynn agent can download. It holds no secrets."""
    try:
        d = _driver()
        try:
            out = FLYNN / "runs" / f"{time.strftime('%Y-%m-%d')}-builder-diag"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{file_id[-12:]}.png").write_bytes(d.get_screenshot_as_png())
            text = d.execute_script("return document.body ? document.body.innerText.slice(0, 1500) : ''") or ""
            frames = d.execute_script("""const b = document.querySelector('[data-testid=mcp-btn]');
                return b ? 'MCP button: ' + b.parentElement.outerHTML.replace(/token=[^&"]+/g, 'token=*').slice(0, 1200) : 'no MCP button';""") or ""
            rec = d.execute_script("return (window.__fl || []).join('\\n')") or "(no record)"
            # What Penpot's MCP start-up reads, asked from the page itself (no key values).
            state = d.execute_async_script("""const done = arguments[arguments.length - 1];
              const call = (c) => fetch('/api/rpc/command/' + c, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: '{}', credentials: 'include'}).then((r) => r.json());
              Promise.all([call('get-profile'), call('get-access-tokens')]).then(([p, t]) => done(JSON.stringify({
                email: p.email, props: Object.fromEntries(Object.entries(p.props || {}).filter(([k]) => /mcp/i.test(k))),
                tokens: (Array.isArray(t) ? t : [t]).map((x) => ({name: x.name, type: x.type, expiresAt: x.expiresAt || null, hasValue: !!x.token}))
              }))).catch((e) => done('state error: ' + e));""") or ""
            rec = f"{rec}\n\n=== mcp state ===\n{state}\n\n=== nudge ===\n{_LAST_NUDGE}"
            text = f"{frames}\n\n=== console ===\n{rec}\n\n=== page ===\n{text}"
            (out / f"{file_id[-12:]}.txt").write_text(f"url: {d.current_url}\nbase: {BASE}\n\n{text}", encoding="utf-8")
        finally:
            d.quit()
    except Exception as e:  # noqa: BLE001 - diagnostics never break the caller
        log(f"builder diag failed: {e}")


def status() -> dict:
    if not running():
        return {"running": False}
    try:
        cur = bridge(timeout=10).current()
    except PenpotError as e:
        cur = {"error": str(e)}
    return {"running": True, "tabs": [t["url"][:90] for t in _tabs()], "connector": cur}


def close() -> None:
    if running():
        # "--" stops pkill reading the pattern (which starts with dashes) as its own option
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={PROFILE}"], capture_output=True)
        log("builder chrome closed")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="builder.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    o = sub.add_parser("open")
    o.add_argument("file")
    sub.add_parser("status")
    sub.add_parser("close")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        print(json.dumps(setup()))
    elif a.cmd == "open":
        print(open_file(a.file).current())
    elif a.cmd == "status":
        print(json.dumps(status(), indent=1))
    else:
        close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
