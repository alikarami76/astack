import base64
import io
import json
import urllib.error

import pytest

import confluence_client as c


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, body=b"{}", error=None):
        self.body, self.error, self.requests = body, error, []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if self.error:
            raise self.error
        return FakeResp(self.body)


CFG = {"base_url": "https://h", "token": "tok-SECRET-123", "auth_type": "pat", "username": ""}


def test_fetch_builds_url_and_bearer_header():
    op = FakeOpener(b'{"id": "5"}')
    assert c.Confluence(CFG, op).fetch("5") == {"id": "5"}
    req = op.requests[0]
    assert req.full_url == "https://h/rest/api/content/5?expand=version%2Cbody.storage%2Cspace"
    assert req.get_method() == "GET" and req.get_header("Authorization") == "Bearer tok-SECRET-123"


def test_basic_auth_header():
    op = FakeOpener()
    c.Confluence({**CFG, "auth_type": "basic", "username": "u"}, op).fetch("5")
    want = "Basic " + base64.b64encode(b"u:tok-SECRET-123").decode()
    assert op.requests[0].get_header("Authorization") == want


def test_create_posts_page_body():
    op = FakeOpener(b'{"id": "9"}')
    assert c.Confluence(CFG, op).create("DOCS", "T", "<p>x</p>", "77") == {"id": "9"}
    req = op.requests[0]
    assert req.full_url == "https://h/rest/api/content" and req.get_method() == "POST"
    assert json.loads(req.data) == {"type": "page", "title": "T", "space": {"key": "DOCS"}, "ancestors": [{"id": "77"}],
                                    "body": {"storage": {"value": "<p>x</p>", "representation": "storage"}}}


def test_update_puts_next_version():
    op = FakeOpener(b'{"id": "5"}')
    c.Confluence(CFG, op).update("5", "T", "<p>y</p>", 3)
    req = op.requests[0]
    assert req.full_url == "https://h/rest/api/content/5" and req.get_method() == "PUT"
    body = json.loads(req.data)
    assert body["version"]["number"] == 4 and body["title"] == "T" and body["type"] == "page"
    assert body["body"]["storage"] == {"value": "<p>y</p>", "representation": "storage"}


def test_http_base_refused():
    with pytest.raises(c.ConfluenceError, match="https"):
        c.Confluence({**CFG, "base_url": "http://h"}, FakeOpener()).fetch("5")


def test_auth_error_mentions_refresh_not_token():
    err = urllib.error.HTTPError("https://h/x", 401, "no", {}, io.BytesIO(b"tok-SECRET-123"))
    with pytest.raises(c.ConfluenceError) as e:
        c.Confluence(CFG, FakeOpener(error=err)).fetch("5")
    assert "401" in str(e.value) and "refresh" in str(e.value) and "tok-SECRET" not in str(e.value)


def test_other_errors_wrapped_without_body():
    err = urllib.error.HTTPError("https://h/x", 500, "boom", {}, io.BytesIO(b"secret body"))
    with pytest.raises(c.ConfluenceError, match=r"GET /rest/api/content/5 failed \(500\)") as e:
        c.Confluence(CFG, FakeOpener(error=err)).fetch("5")
    assert "secret body" not in str(e.value)
    with pytest.raises(c.ConfluenceError, match="cannot reach"):
        c.Confluence(CFG, FakeOpener(error=urllib.error.URLError("dns"))).fetch("5")
    import http.client
    with pytest.raises(c.ConfluenceError):
        c.Confluence(CFG, FakeOpener(error=http.client.IncompleteRead(b""))).fetch("5")
    with pytest.raises(c.ConfluenceError, match="JSON"):
        c.Confluence(CFG, FakeOpener(b"<html>")).fetch("5")


def test_page_url():
    assert c.Confluence(CFG, FakeOpener()).page_url({"id": "5", "_links": {"webui": "/display/DOCS/T"}}) == "https://h/display/DOCS/T"
    assert c.Confluence(CFG, FakeOpener()).page_url({"id": "5"}) == "https://h/pages/viewpage.action?pageId=5"


def test_no_delete_or_move():
    assert not hasattr(c.Confluence, "delete") and not hasattr(c.Confluence, "move")


def test_lazy_cfg_not_read_at_construction(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ENV_FILE", tmp_path / "missing.env")
    cli = c.Confluence(opener=FakeOpener())
    with pytest.raises(c.ConfluenceError, match="missing"):
        cli.fetch("5")
    env = tmp_path / "ok.env"
    env.write_text('# c\nQUORRA_CONF_BASE_URL="https://h/"\nexport QUORRA_CONF_TOKEN=abc\nQUORRA_CONF_AUTH_TYPE=pat\n')
    monkeypatch.setattr(c, "ENV_FILE", env)
    op = FakeOpener()
    c.Confluence(opener=op).fetch("5")
    assert op.requests[0].full_url.startswith("https://h/rest/api/content/5")
    assert op.requests[0].get_header("Authorization") == "Bearer abc"


def test_md_to_storage_loads_writer_with_stub_requests(tmp_path, monkeypatch):
    import sys
    fake = tmp_path / "w.py"
    fake.write_text("import requests\n\ndef md_to_storage(text):\n    return '<table><tr><td>b</td></tr></table>'\n")
    monkeypatch.setattr(c, "WRITER", fake)
    monkeypatch.setattr(c, "_writer_module", None)
    monkeypatch.setitem(sys.modules, "requests", None)  # make `import requests` fail
    out = c.md_to_storage("| a |\n|---|\n| b |")
    assert "<table>" in out
    assert c.md_to_storage("x") == out  # cached module, no reload


def test_basic_auth_without_username_fails_clearly():
    op = FakeOpener()
    with pytest.raises(c.ConfluenceError, match="No Confluence username set"):
        c.Confluence({**CFG, "auth_type": "basic", "username": ""}, op).fetch("5")
