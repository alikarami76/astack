import fetch_mr_reviews as f


def test_is_substantive():
    assert not f.is_substantive("LGTM")
    assert not f.is_substantive("+1")
    assert not f.is_substantive("approved this merge request")
    assert f.is_substantive("please move this query into the repository instead of the controller")


def test_fetch_filters_system_and_bots_and_keeps_position():
    calls = []

    def get_json(endpoint, params):
        calls.append((endpoint, params))
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 7, "title": "Add voucher report", "merged_at": "2026-09-01", "author": {"username": "dev1"}}]
        return [{"notes": [
            {"system": True, "body": "added 1 commit", "author": {"username": "dev1"}},
            {"system": False, "body": "use the existing VoucherRepository here, we already have getActive()",
             "author": {"username": "rev1"}, "position": {"new_path": "src/A.php", "new_line": 12}, "resolved": True},
            {"system": False, "body": "Danger report", "author": {"username": "danger-bot", "bot": True}},
        ]}]
    rows = f.fetch("team/x", "master", 5, get_json)
    assert rows == [{"mr_iid": 7, "mr_title": "Add voucher report", "merged_at": "2026-09-01", "mr_author": "dev1",
                     "note_author": "rev1", "body": "use the existing VoucherRepository here, we already have getActive()",
                     "file": "src/A.php", "line": 12, "resolved": True}]
    assert calls[0][0] == "/projects/team%2Fx/merge_requests"
    assert calls[0][1]["state"] == "merged" and calls[0][1]["target_branch"] == "master"


def test_ca_selection(tmp_path):
    cafile = tmp_path / "cert.pem"
    cafile.write_text("")
    import ssl
    from unittest import mock
    with mock.patch.object(ssl, "create_default_context", side_effect=lambda **kw: kw or "default") as m:
        assert f.make_ssl_context({"x509_ca": 5}, str(cafile)) == "default"
        assert f.make_ssl_context({"x509_ca": 0}, str(cafile)) == {"cafile": str(cafile)}
        assert f.make_ssl_context({"x509_ca": 0}, str(tmp_path / "missing")) == "default"


def test_discussions_paginate():
    pages = []

    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 1, "title": "t", "author": {"username": "a"}}]
        pages.append(params["page"])
        n = 100 if params["page"] == 1 else 3
        return [{"notes": [{"system": False, "body": "x" * 30, "author": {"username": "rev"}}]} for _ in range(n)]
    rows = f.fetch("g/x", "master", 5, get_json)
    assert pages == [1, 2] and len(rows) == 103


def test_bot_detection():
    assert f.is_bot({"username": "danger-bot"}) and f.is_bot({"username": "x", "bot": True})
    assert f.is_bot({"username": "sonar_qube"}) and f.is_bot({"username": "renovate"})
    assert not f.is_bot({"username": "abbott"}) and not f.is_bot({"username": "robotics-dev"})


def test_redirect_cross_host_refused():
    import urllib.error
    import urllib.request
    h = f._SameHostRedirect()
    req = urllib.request.Request("https://git.example.com/a", method="GET")
    try:
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example.org/b")
        raise AssertionError("expected refusal")
    except urllib.error.HTTPError:
        pass


def _refused(h, old, new):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(old, method="GET")
    try:
        h.redirect_request(req, None, 302, "Found", {}, new)
    except urllib.error.HTTPError:
        return True
    return False


def test_redirect_https_to_http_same_host_refused():
    h = f._SameHostRedirect()
    assert _refused(h, "https://git.example.com/a", "http://git.example.com/b")  # the token would travel in cleartext
    assert _refused(h, "https://git.example.com/a", "https://git.example.com:8443/b")  # a different port is another origin
    assert not _refused(h, "https://git.example.com/a", "https://git.example.com/b")


def test_http_get_json_refuses_non_https_base_url():
    import pytest
    with pytest.raises(ValueError, match="https"):
        f.http_get_json("/projects", {}, cfg={"base_url": "http://git.example.com", "personal_access_token": "fake"})


def test_fetch_scrubs_title_and_body():
    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 7, "title": "use $password = 'fakepw12345'", "merged_at": "2026-09-01"}]
        return [{"notes": [{"system": False, "body": "please drop token=fakepw12345 from the config file here",
                            "author": {"username": "rev1"}}]}]
    rows = f.fetch("team/x", "master", 5, get_json)
    assert "fakepw12345" not in rows[0]["mr_title"] and "fakepw12345" not in rows[0]["body"]
