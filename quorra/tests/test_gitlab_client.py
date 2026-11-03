import io
import json
import urllib.error

import pytest

import gitlab_client as g


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


CFG = {"base_url": "https://git.example.com", "personal_access_token": "tok-SECRET-123"}


def test_get_json_builds_url_and_sends_token_header():
    op = FakeOpener(b'[{"iid": 1}]')
    out = g.GitLab(read_cfg=CFG, opener=op).get_json("/projects/a%2Fb/merge_requests", {"state": "opened"})
    assert out == [{"iid": 1}]
    req = op.requests[0]
    assert req.full_url == "https://git.example.com/api/v4/projects/a%2Fb/merge_requests?state=opened"
    assert req.get_method() == "GET" and req.get_header("Private-token") == "tok-SECRET-123"


def test_post_json_uses_write_credentials():
    op = FakeOpener(b'{"id": 9}')
    cli = g.GitLab(read_cfg={**CFG, "personal_access_token": "read"}, write_cfg=CFG, opener=op)
    assert cli.post_json("/projects/x/merge_requests/1/discussions/d/notes", {"body": "hi"}) == {"id": 9}
    req = op.requests[0]
    assert req.get_method() == "POST" and req.get_header("Private-token") == "tok-SECRET-123"
    assert json.loads(req.data) == {"body": "hi"}


def test_http_base_url_refused():
    with pytest.raises(g.GitLabError, match="https"):
        g.GitLab(read_cfg={**CFG, "base_url": "http://git.example.com"}, opener=FakeOpener()).get_json("/x")


def test_auth_error_is_plain_and_never_shows_token():
    err = urllib.error.HTTPError("https://git.example.com/api/v4/x", 401, "Unauthorized", {}, io.BytesIO(b"tok-SECRET-123"))
    with pytest.raises(g.GitLabError) as e:
        g.GitLab(read_cfg=CFG, opener=FakeOpener(error=err)).get_json("/x")
    assert "401" in str(e.value) and "refresh" in str(e.value) and "tok-SECRET" not in str(e.value)


def test_get_text_returns_decoded_text():
    assert g.GitLab(read_cfg=CFG, opener=FakeOpener("line ✓\n".encode())).get_text("/projects/x/jobs/1/trace") == "line ✓\n"


def test_project_id_quotes_path():
    assert g.project_id("team/web-app") == "team%2Fweb-app"


def test_fetch_mr_reviews_still_exports_ssl_helpers():
    import fetch_mr_reviews as f
    assert f.make_ssl_context is g.make_ssl_context and f._SameHostRedirect is g._SameHostRedirect


def test_broken_response_becomes_gitlab_error():
    import http.client

    class Broken:
        def open(self, req, timeout=None):
            class R(FakeResp):
                def read(self, *a):
                    raise http.client.IncompleteRead(b"x")
            return R()
    gl = g.GitLab(read_cfg=CFG, opener=Broken())
    with pytest.raises(g.GitLabError, match="broken response"):
        gl.get_json("/x")


def test_get_text_keeps_only_the_last_2mb():
    body = b"A" * (3 * 1024 * 1024) + b"TAIL"
    gl = g.GitLab(read_cfg=CFG, opener=FakeOpener(body=body))
    text = gl.get_text("/x")
    assert len(text) == g.MAX_TEXT and text.endswith("TAIL")
