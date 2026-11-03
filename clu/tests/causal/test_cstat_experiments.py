import json
import math

import numpy as np
import pandas as pd
import pytest

import cs_experiments as ex
import cs_inference as inf
import cstat


def test_power_matches_statsmodels():
    from statsmodels.stats.power import NormalIndPower
    r = ex.power("mean", baseline=100, sd=40, mde_rel=0.05)
    d = 5 / 40
    n_sm = NormalIndPower().solve_power(effect_size=d, alpha=0.05, power=0.8, ratio=1)
    assert abs(r["extra"]["n_per_arm"] - n_sm) <= 1
    back = ex.power("mean", baseline=100, sd=40, n_per_arm=r["extra"]["n_per_arm"])
    assert abs(back["extra"]["mde_rel"] - 0.05) < 0.001


def test_power_cluster_design_effect_and_smallest_effect_warning():
    base = ex.power("proportion", baseline=0.1, mde_rel=0.1)
    clus = ex.power("proportion", baseline=0.1, mde_rel=0.1, cluster_size=50, icc=0.02, smallest_effect_rel=0.05)
    assert clus["extra"]["design_effect"] == pytest.approx(1.98)
    assert clus["extra"]["n_per_arm"] == pytest.approx(base["extra"]["n_per_arm"] * 1.98, rel=0.01)
    assert clus["diagnostics"][0]["status"] == "warn"


def test_srm_flags_52_48_at_100k():
    assert ex.srm([52000, 48000])["status"] == "diagnostic_failed"
    assert ex.srm([50100, 49900])["status"] == "ok"


def _ratio_sim(rng, n, effect=0.0):
    den = rng.poisson(3, n) + 1
    num = den * (2.0 + effect) + rng.normal(0, 2, n)
    return num, den


def test_abtest_ratio_delta_method_ci_coverage():
    rng = np.random.default_rng(0)
    cover = 0
    reps = 300
    for _ in range(reps):
        nc, dc = _ratio_sim(rng, 2000)
        nt, dt = _ratio_sim(rng, 2000)
        df = pd.DataFrame({"arm": ["c"] * 2000 + ["t"] * 2000, "gmv": np.r_[nc, nt], "orders": np.r_[dc, dt]})
        r = ex.abtest(df, "arm", "gmv", "c", den="orders")
        cover += r["estimate"]["ci_low"] <= 0 <= r["estimate"]["ci_high"]
    assert 0.92 <= cover / reps <= 0.98


def test_abtest_cuped_shrinks_ci_and_keeps_estimate():
    rng = np.random.default_rng(1)
    n = 5000
    pre = rng.normal(100, 30, 2 * n)
    arm = np.r_[["c"] * n, ["t"] * n]
    y = pre * 0.9 + rng.normal(0, 10, 2 * n) + np.where(arm == "t", 2.0, 0.0)
    df = pd.DataFrame({"arm": arm, "y": y, "pre": pre})
    raw = ex.abtest(df, "arm", "y", "c")
    cup = ex.abtest(df, "arm", "y", "c", pre="pre")
    w = lambda r: r["estimate"]["ci_high"] - r["estimate"]["ci_low"]  # noqa: E731
    assert w(cup) < 0.5 * w(raw)
    assert cup["verdict"] == "effect detected"
    assert any(d["name"] == "cuped_variance_reduction" and d["value"] > 0.5 for d in cup["diagnostics"])


def test_abtest_underpowered_null_is_inconclusive_not_no_effect():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"arm": ["c"] * 200 + ["t"] * 200, "y": rng.normal(10, 5, 400)})
    r = ex.abtest(df, "arm", "y", "c", mde_rel=0.01)
    assert r["verdict"] == "inconclusive"


def test_multitest_bh_and_holm():
    r = ex.multitest([0.01, 0.02, 0.03, 0.5], method="bh")
    assert [x["significant"] for x in r["extra"]["results"]] == [True, True, True, False]
    h = ex.multitest([0.01, 0.02, 0.03, 0.5], method="holm")
    assert h["extra"]["results"][0]["p_adjusted"] == pytest.approx(0.04)


def test_permute_holds_size_with_five_clusters():
    rng = np.random.default_rng(3)
    rejections = 0
    for _ in range(200):
        rows = []
        treated = set(rng.choice(10, 5, replace=False))
        for c in range(10):
            level = rng.normal(0, 1)
            rows += [{"cat": c, "t": int(c in treated), "y": level + rng.normal()} for _ in range(30)]
        r = inf.permute(pd.DataFrame(rows), "cat", "t", "y")
        rejections += r["estimate"]["p_value"] < 0.05
    assert rejections / 200 < 0.09


def test_evalue_known_value():
    r = inf.evalue(rr=3.9, ci_low=1.8, ci_high=8.7)
    assert r["extra"]["evalue_point"] == pytest.approx(7.26, abs=0.01)
    assert r["extra"]["evalue_ci"] == pytest.approx(3.0, abs=0.01)
    assert inf.evalue(rr=1.2, ci_low=0.9, ci_high=1.5)["extra"]["evalue_ci"] == 1.0


def test_cli_writes_result(tmp_path):
    out = tmp_path / "srm.json"
    assert cstat.main(["srm", "--counts", "50100,49900", "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["schema_version"] == 1 and res["command"] == "srm" and res["status"] == "ok"


def test_abtest_uses_absolute_difference_when_control_mean_is_near_zero():
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"arm": ["c"] * 1000 + ["t"] * 1000, "y": rng.normal(0, 1, 2000)})
    r = ex.abtest(df, "arm", "y", "c")
    assert r["estimate"]["unit"] == "absolute difference vs control"


def test_abtest_multi_arm_headline_ci_is_multiplicity_adjusted():
    rng = np.random.default_rng(5)
    n = 1000
    df = pd.DataFrame({"arm": ["c"] * n + ["a"] * n + ["b"] * n, "y": rng.normal(10, 3, 3 * n)})
    r = ex.abtest(df, "arm", "y", "c")
    comps = r["extra"]["comparisons"]
    assert all("ci_rel_adj" in c and "p_value_holm" in c for c in comps)
    first = comps[0]
    assert r["estimate"]["ci_low"] == pytest.approx(first["ci_rel_adj"][0])
    assert r["estimate"]["ci_high"] - r["estimate"]["ci_low"] > first["ci_rel"][1] - first["ci_rel"][0]
    assert any("headline is arm" in w for w in r["warnings"])


def test_abtest_srm_failure_forces_inconclusive():
    rng = np.random.default_rng(6)
    nc, nt = 52000, 48000
    arm = np.r_[["c"] * nc, ["t"] * nt]
    y = rng.normal(10, 2, nc + nt) + np.where(arm == "t", 1.0, 0.0)  # a large, clearly detectable effect
    r = ex.abtest(pd.DataFrame({"arm": arm, "y": y}), "arm", "y", "c")
    assert r["estimate"]["ci_low"] > 0  # the CI alone would say "effect detected"
    assert r["verdict"] == "inconclusive" and r["status"] == "diagnostic_failed"
    assert any(w.startswith("SRM failed: the result is invalid") for w in r["warnings"])


def test_permute_random_draw_p_value_counts_observed():
    # 30 clusters, 15 treated: too many assignments to enumerate, so random draws are used
    rng = np.random.default_rng(8)
    rows = [{"cat": c, "t": int(c < 15), "y": (5.0 if c < 15 else 0.0) + rng.normal(scale=0.1)} for c in range(30)]
    r = inf.permute(pd.DataFrame(rows), "cat", "t", "y", iters=99)
    assert "random draws" in r["estimand"]
    assert r["estimate"]["p_value"] == pytest.approx(1 / 100)  # never 0: (0 + 1) / (99 + 1)
