import pytest

pytest.importorskip("gbstats")

import cs_gbcheck  # noqa: E402


ARMS = [{"n": 1000, "sum": 10000, "sum_squares": 104000}, {"n": 1000, "sum": 10500, "sum_squares": 114250}]

# Tiny effect arms: control mean=10.0000, treatment mean=10.0005, sd≈2
TINY_ARMS = [{"n": 1000, "sum": 10000, "sum_squares": 104000}, {"n": 1000, "sum": 10000.5, "sum_squares": 104010.0025}]


def test_gbcheck_recomputes_relative_effect():
    r = cs_gbcheck.gbcheck(ARMS, "mean")
    assert r["estimate"]["value"] == pytest.approx(0.05, abs=1e-6)
    assert r["estimate"]["ci_low"] < 0.05 < r["estimate"]["ci_high"]


def test_gbcheck_flags_mismatch_with_reported():
    ok = cs_gbcheck.gbcheck(ARMS, "mean", reported={"percent_change": 0.051, "ci_low": 0.03, "ci_high": 0.07})
    bad = cs_gbcheck.gbcheck(ARMS, "mean", reported={"percent_change": 0.09, "ci_low": 0.07, "ci_high": 0.11})
    assert ok["status"] == "ok" and bad["status"] == "diagnostic_failed"


def test_gbcheck_tiny_effect_with_noise_passes():
    # Null experiment: true effect is ~0.00005, both reported and recomputed are noise.
    # A difference of 0.0002 (absolute) should pass due to CI width.
    r = cs_gbcheck.gbcheck(TINY_ARMS, "mean", reported={"percent_change": 0.0002, "ci_low": -0.02, "ci_high": 0.02})
    assert r["status"] == "ok"


def test_gbcheck_flags_ci_width_mismatch():
    ours = cs_gbcheck.gbcheck(ARMS, "mean")["estimate"]
    width = ours["ci_high"] - ours["ci_low"]
    same = cs_gbcheck.gbcheck(ARMS, "mean", reported={"percent_change": ours["value"], "ci_low": ours["ci_low"],
                                                      "ci_high": ours["ci_high"]})
    narrow = cs_gbcheck.gbcheck(ARMS, "mean", reported={"percent_change": ours["value"],
                                                        "ci_low": ours["value"] - width * 0.25,
                                                        "ci_high": ours["value"] + width * 0.25})
    get = lambda r: next(d for d in r["diagnostics"] if d["name"] == "ci_width_matches_growthbook")  # noqa: E731
    assert get(same)["status"] == "pass"
    assert get(narrow)["status"] == "warn" and "CUPED" in get(narrow)["note"]
