"""Confluence HTTP for quorra: https only, verifying SSL, same-origin redirects only, the token only in the
Authorization header and never printed. No delete and no move: quorra only reads, creates and updates pages."""
from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import os
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from gitlab_client import _SameHostRedirect, make_ssl_context

WRITER = Path(os.environ.get("QUORRA_CONFLUENCE_WRITER") or Path.home() / ".config/quorra/confluence_writer.py")
ENV_FILE = Path.home() / ".claude/secrets/confluence.env"
EXPAND = "version,body.storage,space"

_writer_module = None


class ConfluenceError(RuntimeError):
    pass


def md_to_storage(text: str) -> str:
    """Markdown to Confluence storage format, using the converter script named by QUORRA_CONFLUENCE_WRITER (loaded by path, once)."""
    global _writer_module
    if _writer_module is None:
        try:
            import requests  # noqa: F401
        except ImportError:  # the writer imports it at top level but its converter never uses it
            sys.modules["requests"] = types.ModuleType("requests")
        if not Path(WRITER).exists():
            raise ConfluenceError(f"confluence writer script missing: {WRITER}")
        spec = importlib.util.spec_from_file_location("quorra_confluence_writer", WRITER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _writer_module = mod
    return _writer_module.md_to_storage(text)


def _load_env() -> dict:
    try:
        lines = Path(ENV_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise ConfluenceError(f"Confluence env file missing: {ENV_FILE}") from None
    vals = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        vals[key] = val.strip().strip("'\"")
    if not vals.get("QUORRA_CONF_BASE_URL") or not vals.get("QUORRA_CONF_TOKEN"):
        raise ConfluenceError(f"QUORRA_CONF_BASE_URL and QUORRA_CONF_TOKEN must be set in {ENV_FILE}")
    return {"base_url": vals["QUORRA_CONF_BASE_URL"], "token": vals["QUORRA_CONF_TOKEN"],
            "auth_type": vals.get("QUORRA_CONF_AUTH_TYPE", "pat"), "username": vals.get("QUORRA_CONF_USERNAME", "")}


class Confluence:
    def __init__(self, cfg: dict | None = None, opener=None):
        self._cfg, self._opener = cfg, opener

    def _config(self) -> dict:
        if self._cfg is None:
            self._cfg = _load_env()
        return self._cfg

    def _auth(self, cfg: dict) -> str:
        if cfg.get("auth_type", "pat") == "basic":
            if not cfg.get("username"):
                raise ConfluenceError(
                    "No Confluence username set. Add QUORRA_CONF_USERNAME=<your username> to ~/.claude/secrets/confluence.env, "
                    'or run: "$HOME/agent-stack/install.sh" --reconfigure confluence')
            return "Basic " + base64.b64encode(f"{cfg.get('username', '')}:{cfg['token']}".encode()).decode()
        return f"Bearer {cfg['token']}"

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        cfg = self._config()
        base = cfg["base_url"].rstrip("/")
        if urllib.parse.urlsplit(base).scheme != "https":
            raise ConfluenceError("Confluence base_url must be https: the token is never sent over plain http")
        headers = {"Authorization": self._auth(cfg), "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        opener = self._opener or urllib.request.build_opener(
            _SameHostRedirect, urllib.request.HTTPSHandler(context=make_ssl_context()))
        short = path.split("?")[0]
        try:
            with opener.open(req, timeout=60) as r:
                raw = r.read()
            return json.loads(raw)
        except http.client.HTTPException as e:
            raise ConfluenceError(f"Confluence {method} {short} returned a broken response: {type(e).__name__}") from None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ConfluenceError(f"Confluence {method} {short} refused ({e.code}); "
                                      f"refresh the token in {ENV_FILE}") from None
            raise ConfluenceError(f"Confluence {method} {short} failed ({e.code})") from None
        except urllib.error.URLError as e:
            raise ConfluenceError(f"cannot reach Confluence: {e.reason}") from None
        except ValueError:
            raise ConfluenceError(f"Confluence {method} {short} returned invalid JSON") from None

    def fetch(self, page_id: str) -> dict:
        return self._request("GET", f"/rest/api/content/{urllib.parse.quote(str(page_id), safe='')}"
                                    f"?expand={urllib.parse.quote(EXPAND, safe='.')}")

    def create(self, space: str, title: str, body: str, parent_id: str) -> dict:
        return self._request("POST", "/rest/api/content", {
            "type": "page", "title": title, "space": {"key": space}, "ancestors": [{"id": str(parent_id)}],
            "body": {"storage": {"value": body, "representation": "storage"}}})

    def update(self, page_id: str, title: str, body: str, version: int) -> dict:
        return self._request("PUT", f"/rest/api/content/{urllib.parse.quote(str(page_id), safe='')}", {
            "type": "page", "title": title, "version": {"number": int(version) + 1},
            "body": {"storage": {"value": body, "representation": "storage"}}})

    def page_url(self, page: dict) -> str:
        base = self._config()["base_url"].rstrip("/")
        webui = (page.get("_links") or {}).get("webui")
        return base + webui if webui else f"{base}/pages/viewpage.action?pageId={page['id']}"
