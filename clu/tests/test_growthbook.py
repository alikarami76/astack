import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import growthbook as gb

KEY = "secret_readonly_abc123"
SEEN = []

EXP = {"id": "exp_1", "name": "Promo badge on listing page", "status": "running", "hashAttribute": "seller_id",
       "hypothesis": "badge lifts CTR", "variations": [{"variationId": "v0", "key": "0", "name": "Control"},
                                                        {"variationId": "v1", "key": "1", "name": "Badge"}],
       "phases": [{"name": "Main", "dateStarted": "2026-09-01", "trafficSplit": [{"variationId": "v0", "weight": 0.5}]}]}
RES = {"result": {"experimentId": "exp_1", "phase": "0", "results": [{"dimension": "All", "totalUsers": 2000,
       "checks": {"srm": 0.42}, "metrics": [{"metricId": "met_ctr", "variations": [
           {"variationId": "v0", "users": 1000, "analyses": [{"engine": "frequentist", "mean": 0.10}]},
           {"variationId": "v1", "users": 1000, "analyses": [{"engine": "frequentist", "mean": 0.11, "percentChange": 0.1, "chanceToBeatControl": 0.97, "numerator": 110, "denominator": 1000,
                                                              "ciLow": 0.02, "ciHigh": 0.18, "pValue": 0.01}]}]}]}]}}


class H(BaseHTTPRequestHandler):
    request_count = 0

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"message": "unauthorized"})
        if self.path.startswith("/api/v1/experiments/exp_1/results"):
            return self._send(200, RES)
        if self.path.startswith("/api/v1/experiments/exp_1"):
            return self._send(200, {"experiment": EXP})
        if self.path.startswith("/api/v1/empty-page"):
            return self._send(200, {"experiments": [], "hasMore": True, "nextOffset": 0})
        if self.path.startswith("/api/v1/retry-after-test"):
            H.request_count += 1
            if H.request_count == 1:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "Wed, 01 Jan 2099 00:00:00 GMT")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "rate limited"}).encode())
                return
            else:
                return self._send(200, {"experiments": [], "hasMore": False})
        if self.path.startswith("/api/v1/experiments"):
            return self._send(200, {"experiments": [EXP, {**EXP, "id": "exp_2", "name": "Old", "status": "stopped"}],
                                    "hasMore": False})
        if self.path.startswith("/api/v1/metrics"):
            return self._send(200, {"metrics": [{"id": "met_ctr", "name": "CTR", "type": "binomial"}], "hasMore": False})
        if self.path.startswith("/api/v1/redirect-test"):
            self.send_response(302)
            self.send_header("Location", "https://evil.com/api/v1/experiments")
            self.end_headers()
            return
        if self.path.startswith("/api/v1/html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html>Request Rejected</html>")
            return
        return self._send(404, {})

    def do_POST(self):
        SEEN.append(("POST", self.path, None))
        self._send(405, {})


@pytest.fixture
def server(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    secrets = tmp_path / "growthbook.env"
    secrets.write_text(f"GROWTHBOOK_API_URL=http://127.0.0.1:{srv.server_port}\nGROWTHBOOK_API_KEY={KEY}\n")
    monkeypatch.setattr(gb, "SECRETS", secrets)
    monkeypatch.delenv("GROWTHBOOK_API_URL", raising=False)
    monkeypatch.delenv("GROWTHBOOK_API_KEY", raising=False)
    SEEN.clear()
    yield srv
    srv.shutdown()


def test_lists_and_filters_experiments(server):
    rows = gb.list_experiments(status="running")
    assert [r["id"] for r in rows] == ["exp_1"] and rows[0]["hashAttribute"] == "seller_id"


def test_results_summary_and_cache_never_contain_key(server, tmp_path, capsys):
    cache = tmp_path / "gb"
    assert gb.main(["results", "exp_1", "--cache", str(cache)]) == 0
    out = capsys.readouterr().out
    r = json.loads(out)
    v1 = r["dimensions"][0]["metrics"][0]["variations"][1]
    assert r["dimensions"][0]["srm_p"] == 0.42 and v1["percentChange"] == 0.1 and v1["pValue"] == 0.01
    assert KEY not in out and KEY not in (cache / "results-exp_1.json").read_text()


def test_only_get_requests_are_sent(server):
    gb.experiment("exp_1")
    gb.list_metrics("ctr")
    assert SEEN and all(m == "GET" for m, _, _ in SEEN)


def test_missing_credentials_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gb, "SECRETS", tmp_path / "none.env")
    monkeypatch.delenv("GROWTHBOOK_API_URL", raising=False)
    monkeypatch.delenv("GROWTHBOOK_API_KEY", raising=False)
    assert gb.main(["experiments"]) == 1
    assert "credentials missing" in capsys.readouterr().err


def test_non_https_url_is_rejected(tmp_path, monkeypatch, capsys):
    secrets = tmp_path / "growthbook.env"
    secrets.write_text(f"GROWTHBOOK_API_URL=http://gb-api.example.com\nGROWTHBOOK_API_KEY={KEY}\n")
    monkeypatch.setattr(gb, "SECRETS", secrets)
    monkeypatch.delenv("GROWTHBOOK_API_URL", raising=False)
    monkeypatch.delenv("GROWTHBOOK_API_KEY", raising=False)
    with pytest.raises(gb.GBError, match="must use https"):
        gb._creds()
    assert gb.main(["experiments"]) == 1
    assert "must use https" in capsys.readouterr().err
    secrets.write_text(f"GROWTHBOOK_API_URL=https://gb-api.example.com\nGROWTHBOOK_API_KEY={KEY}\n")
    assert gb._creds()[0] == "https://gb-api.example.com"
    secrets.write_text(f"GROWTHBOOK_API_URL=http://localhost:3100\nGROWTHBOOK_API_KEY={KEY}\n")
    assert gb._creds()[0] == "http://localhost:3100"


def test_record_upserts_experiment_facts(server, kbenv):
    import store
    eid = gb.record(gb.experiment("exp_1"))
    row = store.get(store.connect(), eid)
    assert row["growthbook_id"] == "exp_1" and row["unit"] == "seller_id" and row["kind"] == "rct"
    assert "Hypothesis: badge lifts CTR" in row["design"]


def test_pagination_terminates_on_empty_page(server, monkeypatch):
    """Pagination terminates when hasMore=True but page is empty."""
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    # Create a mock for get that returns empty page with hasMore=True
    call_count = [0]
    original_get = gb.get
    def mock_get(path, params=None):
        call_count[0] += 1
        if "empty-page" in path:
            return {"experiments": [], "hasMore": True, "nextOffset": 0}
        return original_get(path, params)
    monkeypatch.setattr(gb, "get", mock_get)

    # _paged should break when it gets an empty page
    rows = gb._paged("empty-page", "experiments", {}, 1000)
    assert rows == []  # Should return empty, not loop forever


def test_retry_after_http_date_and_sleep_capped(server, monkeypatch):
    """Retry-After HTTP date is parsed and sleep is capped at 60 seconds."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda x: sleep_calls.append(x))
    H.request_count = 0
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    # Call endpoint that returns 429 once then 200
    rows = gb._paged("retry-after-test", "experiments", {}, 100)
    assert len(sleep_calls) > 0, "Should have slept during retry"
    assert all(s <= 60 for s in sleep_calls), f"All sleeps should be ≤ 60s, got {sleep_calls}"


def test_results_survives_null_metrics(server, monkeypatch):
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))
    # Patch get to return null metrics
    original_get = gb.get
    def patched_get(path, params=None):
        result = original_get(path, params)
        if "results" in path and "result" in result:
            result["result"]["results"][0]["metrics"] = None
        return result
    monkeypatch.setattr(gb, "get", patched_get)
    r = gb.results("exp_1")
    assert r["dimensions"][0]["metrics"] == []


def test_record_with_duplicate_names_uses_id(server, kbenv, monkeypatch):
    import store
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    # Create first experiment with a name
    exp1 = {"id": "exp_1", "name": "Test Exp", "status": "running", "hashAttribute": "seller_id",
            "hypothesis": "test", "variations": [], "phases": []}
    eid1 = gb.record(exp1)
    row1 = store.get(store.connect(), eid1)
    assert row1["growthbook_id"] == "exp_1"

    # Create second experiment with same name but different id
    exp2 = {"id": "exp_999", "name": "Test Exp", "status": "running", "hashAttribute": "seller_id",
            "hypothesis": "test", "variations": [], "phases": []}
    eid2 = gb.record(exp2)
    row2 = store.get(store.connect(), eid2)
    assert row2["growthbook_id"] == "exp_999"
    assert row1["id"] != row2["id"], "Duplicate names should create separate rows"


def test_redirect_raises_error_and_no_key_leak(server, monkeypatch, capsys):
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    try:
        gb.get("redirect-test")
        assert False, "Should have raised GBError"
    except gb.GBError as e:
        err = str(e)
        assert "redirect" in err.lower()
        assert key not in err


def test_retry_after_unparseable_uses_exponential_backoff(server, monkeypatch):
    """Unparseable Retry-After should use 2**attempt."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda x: sleep_calls.append(x))

    # Test _parse_retry_after directly
    result = gb._parse_retry_after("unparseable_value", attempt=2)
    assert result == 2.0 ** 2  # Should return 4.0


def test_retry_after_negative_and_nan_clamped(server, monkeypatch):
    """Negative and nan values should be clamped to valid range [0, 60]."""
    assert 0 <= gb._parse_retry_after("-5", attempt=0) <= 60
    assert 0 <= gb._parse_retry_after("nan", attempt=0) <= 60


def test_retry_after_large_value_capped_at_60(server, monkeypatch):
    """Large Retry-After values should be capped at 60 seconds."""
    result = gb._parse_retry_after("3600", attempt=0)
    assert result == 60.0


def test_experiment_survives_null_variations_phases_settings(server, monkeypatch):
    """experiment() should handle null variations, phases, and settings."""
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    # Patch get to return null fields
    original_get = gb.get
    def patched_get(path, params=None):
        result = original_get(path, params)
        if "experiments/" in path and "results" not in path:
            result["experiment"]["variations"] = None
            result["experiment"]["phases"] = None
            result["experiment"]["settings"] = None
        return result
    monkeypatch.setattr(gb, "get", patched_get)

    exp = gb.experiment("exp_1")
    assert exp["variations"] == []
    assert exp["phases"] == []
    assert exp["settings"] == {}


def test_record_name_collision_appends_id_preserves_first(server, kbenv, monkeypatch):
    """record() should append [id] when name collision detected, preserve first row's growthbook_id."""
    import store
    url, key = "http://127.0.0.1:" + str(server.server_port), KEY
    monkeypatch.setattr(gb, "_creds", lambda: (url, key))

    # Create first experiment with a name
    exp1 = {"id": "exp_1", "name": "Collision Test", "status": "running", "hashAttribute": "seller_id",
            "hypothesis": "test", "variations": [], "phases": []}
    eid1 = gb.record(exp1)
    row1 = store.get(store.connect(), eid1)
    assert row1["growthbook_id"] == "exp_1"
    original_name = row1["name"]

    # Create second experiment with same name but different id
    exp2 = {"id": "exp_999", "name": "Collision Test", "status": "running", "hashAttribute": "seller_id",
            "hypothesis": "test", "variations": [], "phases": []}
    eid2 = gb.record(exp2)
    row2 = store.get(store.connect(), eid2)

    # Verify second row has appended id
    assert row2["growthbook_id"] == "exp_999"
    assert row2["name"].endswith("[exp_999]"), f"Expected name to end with [exp_999], got {row2['name']}"
    assert row1["id"] != row2["id"], "Duplicate names should create separate rows"

    # Verify first row's growthbook_id is preserved
    row1_refetch = store.get(store.connect(), row1["id"])
    assert row1_refetch["growthbook_id"] == "exp_1"


def test_record_with_null_fields(kbenv):
    """record() should handle null variations, phases, settings, hashAttribute."""
    import store
    exp = {"id": "exp_n", "name": "Null fields", "status": "draft", "variations": None,
           "phases": None, "settings": None, "hashAttribute": None}
    eid = gb.record(exp)
    row = store.get(store.connect(), eid)
    assert row["growthbook_id"] == "exp_n"
    assert row["design"].startswith("0 variations")


def test_real_result_fields_are_parsed(server):
    v1 = gb.results("exp_1")["dimensions"][0]["metrics"][0]["variations"][1]
    assert v1["chanceToWin"] == 0.97 and v1["numerator"] == 110 and v1["denominator"] == 1000


def test_html_page_is_a_clear_error(server):
    with pytest.raises(gb.GBError, match="HTML page"):
        gb.get("html")


def test_metrics_fall_back_when_fact_metrics_missing(server):
    assert [m["id"] for m in gb.list_metrics()] == ["met_ctr"]
