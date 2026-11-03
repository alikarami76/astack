import json

import pytest

import perf


def test_parse_lighthouse_median(tmp_path):
    paths = []
    for i, lcp in enumerate([2000, 2600, 2400]):
        p = tmp_path / f"lh{i}.json"
        p.write_text(json.dumps({"audits": {"largest-contentful-paint": {"numericValue": lcp},
                                            "cumulative-layout-shift": {"numericValue": 0.05},
                                            "total-blocking-time": {"numericValue": 150}},
                                 "categories": {"performance": {"score": 0.8}, "accessibility": {"score": 0.95}}}))
        paths.append(str(p))
    m = perf.parse_lh(paths)
    assert m["metrics"]["lcp_ms"] == 2400 and m["spread"]["lcp_ms"] == 600 and m["metrics"]["a11y_score"] == 0.95


def test_parse_k6_and_compare(tmp_path):
    p = tmp_path / "k6.json"
    p.write_text(json.dumps({"metrics": {"http_req_duration": {"med": 100, "p(90)": 180, "p(95)": 250, "p(99)": 400, "avg": 120},
                                         "http_req_failed": {"value": 0.002}, "http_reqs": {"rate": 33.0}}}))
    now = perf.parse_k6(str(p))
    assert now["metrics"]["http_p95_ms"] == 250 and now["metrics"]["error_rate"] == 0.002
    base = {"metrics": {"http_p95_ms": 200, "http_p99_ms": 390, "error_rate": 0.001}}
    res = perf.compare(now, base, perf.DEFAULT_BUDGET)
    assert res["regressed"] == ["http_p95_ms"] and res["verdict"] == "fail"  # 25% worse > 20% limit
    assert perf.compare(now, {"metrics": {"http_p95_ms": 240}}, perf.DEFAULT_BUDGET)["verdict"] == "pass"
    lh = {"metrics": {"lcp_ms": 3100, "cls": 0.02}}
    assert perf.compare(lh, None, perf.DEFAULT_BUDGET)["regressed"] == ["lcp_ms"]


def test_targets_are_gated():
    with pytest.raises(SystemExit, match="NEEDS APPROVAL"):
        perf._gate("https://demo-api.example.com", "load", None)
    with pytest.raises(SystemExit, match="NEEDS APPROVAL"):
        perf._gate("https://seller.example.com", "load", None)
    assert perf._gate("http://localhost:3333", "load", None) == "local"


def test_baseline_into_kb(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"metrics": {"lcp_ms": 2100}}))
    assert perf.main(["baseline", str(p), "--target", "discount-step", "--env", "local"]) == 0
    import store
    assert store.rows(store.connect(), "baseline")[0]["value"] == 2100
