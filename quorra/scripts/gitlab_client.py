"""GitLab HTTP for quorra: verifying SSL, same-origin redirects only, the token only in a header and never printed.
Reads use the read credentials file; writes (MR creation, thread replies) the write credentials file."""
from __future__ import annotations

import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

READ_CREDENTIALS = Path(os.environ.get("QUORRA_GITLAB_READ_CREDENTIALS") or Path.home() / ".config/quorra/gitlab-read.json")
WRITE_CREDENTIALS = Path(os.environ.get("QUORRA_GITLAB_WRITE_CREDENTIALS") or Path.home() / ".config/quorra/gitlab-write.json")
SYSTEM_CA = "/etc/ssl/cert.pem"
MAX_TEXT = 2 * 1024 * 1024  # get_text keeps the last 2 MB (the tail of a job log)


class GitLabError(RuntimeError):
    pass


def make_ssl_context(stats=None, cafile: str = SYSTEM_CA) -> ssl.SSLContext:
    """Default verifying context; fall back to the system CA file when Python ships no CA certs. Never disables verification."""
    ctx = ssl.create_default_context()
    have = (stats if stats is not None else ctx.cert_store_stats()).get("x509_ca", 0)
    if have == 0 and Path(cafile).exists():
        ctx = ssl.create_default_context(cafile=cafile)
    return ctx


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same https origin (scheme and host:port), so the token never leaves it or goes
    out in cleartext."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        old = urllib.parse.urlsplit(req.full_url)
        if new.scheme != "https" or (new.scheme, new.netloc) != (old.scheme, old.netloc):
            raise urllib.error.HTTPError(req.full_url, code, "redirect to another origin or to plain http refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def project_id(path: str) -> str:
    return urllib.parse.quote(path, safe="")


def _load(path: Path) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GitLabError(f"GitLab credentials file missing: {path}") from None
    if not cfg.get("personal_access_token") or cfg["personal_access_token"] == "REPLACE_ME":
        raise GitLabError(f"no GitLab token in {path}; the user sets it there (quorra never handles credentials)")
    return {**cfg, "base_url": cfg["base_url"].rstrip("/")}


class GitLab:
    def __init__(self, read_cfg: dict | None = None, write_cfg: dict | None = None, opener=None):
        self._read, self._write, self._opener = read_cfg, write_cfg, opener

    def _cfg(self, write: bool) -> dict:
        if write:
            self._write = self._write or _load(WRITE_CREDENTIALS)
            return self._write
        self._read = self._read or _load(READ_CREDENTIALS)
        return self._read

    def _open(self, method: str, endpoint: str, params: dict | None, body: dict | None, write: bool) -> bytes:
        cfg = self._cfg(write)
        if urllib.parse.urlsplit(cfg["base_url"]).scheme != "https":
            raise GitLabError("GitLab base_url must be https: the access token is never sent over plain http")
        url = f"{cfg['base_url']}/api/v4{endpoint}" + (f"?{urllib.parse.urlencode(params, doseq=True)}" if params else "")
        headers = {"PRIVATE-TOKEN": cfg["personal_access_token"], "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = self._opener or urllib.request.build_opener(
            _SameHostRedirect, urllib.request.HTTPSHandler(context=make_ssl_context()))
        try:
            with opener.open(req, timeout=60) as r:
                return r.read()
        except http.client.HTTPException as e:
            raise GitLabError(f"GitLab {method} {endpoint} returned a broken response: {type(e).__name__}") from None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise GitLabError(f"GitLab {method} {endpoint} refused ({e.code}); refresh the token in "
                                  f"{WRITE_CREDENTIALS if write else READ_CREDENTIALS}") from None
            raise GitLabError(f"GitLab {method} {endpoint} failed ({e.code})") from None
        except urllib.error.URLError as e:
            raise GitLabError(f"cannot reach GitLab: {e.reason}") from None

    def get_json(self, endpoint: str, params: dict | None = None) -> object:
        return json.loads(self._open("GET", endpoint, params, None, False))

    def get_text(self, endpoint: str) -> str:
        data = self._open("GET", endpoint, None, None, False)
        return data[-MAX_TEXT:].decode("utf-8", errors="replace")

    def post_json(self, endpoint: str, body: dict) -> dict:
        return json.loads(self._open("POST", endpoint, None, body, True))
