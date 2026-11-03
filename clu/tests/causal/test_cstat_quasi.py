import numpy as np
import pandas as pd
import pytest

pytest.importorskip("diff_diff")
pytest.importorskip("rdrobust")

import cs_did  # noqa: E402


def staggered_panel(seed=0, pre_trend_violation=0.0):
    """90 units, 12 periods, cohorts start at 4, 7, 10 plus never-treated; the effect grows 0.5 per period."""
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(90):
        g = [4, 7, 10, 0][u % 4] if u < 80 else 0
        a = rng.normal()
        for t in range(1, 13):
            d = int(bool(g) and t >= g)
            eff = (1 + 0.5 * (t - g)) if d else 0.0
            drift = pre_trend_violation * t if g else 0.0
            rows.append({"unit": u, "time": t, "first_treat": g, "y": a + 0.2 * t + drift + eff + rng.normal(scale=0.5)})
    df = pd.DataFrame(rows)
    tr = df[(df.first_treat > 0) & (df.time >= df.first_treat)]
    return df, float((1 + 0.5 * (tr.time - tr.first_treat)).mean())


def test_did_recovers_staggered_effect_and_flags_twfe_bias():
    df, truth = staggered_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat", placebo_shift=2)
    assert r["estimate"]["ci_low"] <= truth <= r["estimate"]["ci_high"]
    twfe = r["extra"]["twfe_comparison"]["estimate"]
    assert abs(twfe - truth) > 0.5
    assert any("TWFE" in w for w in r["warnings"])
    assert {d["name"] for d in r["diagnostics"]} >= {"pre_trend_leads", "placebo_date", "honestdid_relative_magnitude"}


def test_did_twfe_request_is_redirected():
    df, _ = staggered_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat", method="twfe", honest_m=None)
    assert r["extra"]["method"] == "cs"
    assert "Plain TWFE requested" in r["warnings"][0]


def test_did_flags_pre_trend_violation():
    df, _ = staggered_panel(seed=1, pre_trend_violation=0.3)
    r = cs_did.did(df, "y", "unit", "time", "first_treat", honest_m=None)
    lead = next(d for d in r["diagnostics"] if d["name"] == "pre_trend_leads")
    assert lead["status"] == "warn"


def test_did_imputation_reports_pre_trend_and_honest():
    df, _ = staggered_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat", method="imputation")
    diags = {d["name"]: d for d in r["diagnostics"]}
    # pretrends=True gives real leads, so the lead check runs (value is a count, not None) and the joint test exists
    assert diags["pre_trend_leads"]["status"] == "pass" and diags["pre_trend_leads"]["value"] == 0
    assert diags["pre_trend_joint"]["status"] == "pass"
    assert diags["honestdid_relative_magnitude"]["status"] in ("pass", "warn")


def test_did_imputation_joint_pre_trend_fails_on_violation():
    df, _ = staggered_panel(seed=1, pre_trend_violation=0.3)
    r = cs_did.did(df, "y", "unit", "time", "first_treat", method="imputation", honest_m=None)
    assert next(d for d in r["diagnostics"] if d["name"] == "pre_trend_joint")["status"] == "fail"


def test_did_cs_event_study_pre_period_normalized_to_minus_one():
    df, _ = staggered_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat")
    es = r["extra"]["event_study"]
    pre_keys = sorted([k for k in es.keys() if k < -1])
    # universal base: -1 is the reference period, so it is absent or carries no SE
    assert -1 not in es or es[-1].get("se") is None or np.isnan(es[-1]["se"]), f"-1 should be the reference: {es.get(-1)}"
    assert pre_keys == list(range(-9, -1)), "every other lead should be estimated"
    assert all(not np.isnan(es[k]["se"]) for k in pre_keys)


def test_did_sun_abraham_smoke():
    df, truth = staggered_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat", method="sa", honest_m=None)
    assert r["extra"]["method"] == "sa" and r["status"] == "ok"
    assert r["estimate"]["ci_low"] <= truth <= r["estimate"]["ci_high"]
    assert next(d for d in r["diagnostics"] if d["name"] == "pre_trend_joint")["status"] == "pass"


def test_did_joint_pre_trend_test():
    clean, _ = staggered_panel()
    bad, _ = staggered_panel(seed=1, pre_trend_violation=0.3)
    jc = next(d for d in cs_did.did(clean, "y", "unit", "time", "first_treat", honest_m=None)["diagnostics"]
              if d["name"] == "pre_trend_joint")
    rb = cs_did.did(bad, "y", "unit", "time", "first_treat", honest_m=None)
    jb = next(d for d in rb["diagnostics"] if d["name"] == "pre_trend_joint")
    assert jc["status"] == "pass" and "covariance matrix" in jc["note"]
    assert jb["status"] == "fail" and rb["status"] == "diagnostic_failed"
    # the per-lead check stays informational
    assert next(d for d in rb["diagnostics"] if d["name"] == "pre_trend_leads")["status"] == "warn"


def _few_cluster_panel(n_cats=12, seed=5):
    """Units nested in categories; treatment is assigned by category (4 treated categories)."""
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_cats):
        g = 5 if c < 4 else 0
        for u in range(10):
            a = rng.normal()
            for t in range(1, 9):
                rows.append({"unit": c * 10 + u, "cat": c, "time": t, "first_treat": g,
                             "y": a + 0.1 * t + (1.0 if g and t >= g else 0) + rng.normal(scale=0.5)})
    return pd.DataFrame(rows)


def test_did_warns_on_few_treated_clusters():
    df = _few_cluster_panel()
    r = cs_did.did(df, "y", "unit", "time", "first_treat", cluster="cat", honest_m=None)
    tc = next(d for d in r["diagnostics"] if d["name"] == "treated_clusters")
    assert tc["status"] == "warn" and tc["value"] == 4
    assert "permute" in tc["note"] and "--n-bootstrap" in tc["note"]
    ok, _ = staggered_panel()
    r2 = cs_did.did(ok, "y", "unit", "time", "first_treat", honest_m=None)
    assert next(d for d in r2["diagnostics"] if d["name"] == "treated_clusters")["status"] == "pass"


def test_did_bootstrap_and_cohort_effects(tmp_path):
    import cstat
    df, _ = staggered_panel()
    path = tmp_path / "panel.csv"
    df.to_csv(path, index=False)
    out = tmp_path / "did.json"
    assert cstat.main(["did", "--data", str(path), "--y", "y", "--unit", "unit", "--time", "time",
                       "--first-treat", "first_treat", "--n-bootstrap", "99", "--honest-m", "0.5",
                       "--out", str(out)]) == 0
    import json
    res = json.loads(out.read_text())
    assert res["extra"]["n_bootstrap"] == 99
    assert sorted(int(k) for k in res["extra"]["cohort_effects"]) == [4, 7, 10]
    r = cs_did.did(df, "y", "unit", "time", "first_treat", honest_m=None)
    ce = r["extra"]["cohort_effects"]
    assert ce[4]["effect"] > ce[7]["effect"] > ce[10]["effect"]  # earlier cohorts have longer, larger effects


import cs_its  # noqa: E402
import cs_rdd  # noqa: E402


def test_rdd_recovers_jump_and_flags_manipulation():
    rng = np.random.default_rng(1)
    x = rng.uniform(-1, 1, 3000)
    df = pd.DataFrame({"x": x, "y": 0.5 * x + (x >= 0) * 0.8 + rng.normal(scale=0.3, size=3000)})
    r = cs_rdd.rdd(df, "x", "y", 0.0, placebo_cutoffs=[-0.5, 0.5])
    assert r["estimate"]["ci_low"] <= 0.8 <= r["estimate"]["ci_high"]
    assert next(d for d in r["diagnostics"] if d["name"] == "density_manipulation")["status"] == "pass"
    xm = np.r_[x, rng.uniform(0, 0.05, 600)]
    dm = pd.DataFrame({"x": xm, "y": 0.5 * xm + (xm >= 0) * 0.8 + rng.normal(scale=0.3, size=len(xm))})
    rm = cs_rdd.rdd(dm, "x", "y", 0.0)
    assert rm["status"] == "diagnostic_failed"


def test_its_finds_known_break():
    rng = np.random.default_rng(0)
    t = np.arange(140)
    y = 5 + 0.02 * t + np.sin(2 * np.pi * t / 7) + rng.normal(scale=0.3, size=140) + (t >= 100) * 1.5
    r = cs_its.its(pd.DataFrame({"day": t, "y": y}), "day", "y", 100)
    assert r["estimate"]["ci_low"] <= 1.5 <= r["estimate"]["ci_high"]
    assert r["verdict"] == "effect detected"


def test_fuzzy_rdd_strong_first_stage():
    rng = np.random.default_rng(2)
    x = rng.uniform(-1, 1, 2000)
    d = ((x >= 0).astype(float) + rng.normal(scale=0.1, size=2000)).clip(0, 1)
    y = 0.3 * x + d * 1.0 + rng.normal(scale=0.3, size=2000)
    df = pd.DataFrame({"x": x, "d": d, "y": y})
    r = cs_rdd.rdd(df, "x", "y", 0.0, fuzzy="d")
    fs = next(d for d in r["diagnostics"] if d["name"] == "first_stage")
    assert fs["status"] == "pass"
    assert r["estimate"]["ci_low"] <= 1.0 <= r["estimate"]["ci_high"]
    assert "first_stage" in r["extra"]


def test_fuzzy_rdd_no_first_stage():
    rng = np.random.default_rng(3)
    x = rng.uniform(-1, 1, 2000)
    d = rng.uniform(0.4, 0.5, 2000)
    y = 0.3 * x + rng.normal(scale=0.3, size=2000)
    df = pd.DataFrame({"x": x, "d": d, "y": y})
    r = cs_rdd.rdd(df, "x", "y", 0.0, fuzzy="d")
    fs = next(d for d in r["diagnostics"] if d["name"] == "first_stage")
    assert fs["status"] == "fail"


def test_its_placebo_break_never_fails():
    rng = np.random.default_rng(4)
    t = np.arange(140)
    y = 5 + 0.01 * t + np.sin(2 * np.pi * t / 7) + rng.normal(scale=0.2, size=140)
    r = cs_its.its(pd.DataFrame({"day": t, "y": y}), "day", "y", 100)
    pb = next(d for d in r["diagnostics"] if d["name"] == "placebo_break")
    assert pb["status"] in ("pass", "warn"), "placebo_break should never be 'fail'"


def test_its_reports_convergence():
    rng = np.random.default_rng(0)
    t = np.arange(140)
    y = 5 + np.sin(2 * np.pi * t / 7) + rng.normal(scale=0.3, size=140) + (t >= 100) * 1.5
    r = cs_its.its(pd.DataFrame({"day": t, "y": y}), "day", "y", 100)
    conv = next(d for d in r["diagnostics"] if d["name"] == "converged")
    assert conv["status"] == "pass"


import cs_synth  # noqa: E402


def _synth_panel(effect=3.0, seed=2):
    rng = np.random.default_rng(seed)
    f = np.cumsum(rng.normal(size=30))
    rows = []
    for u in range(20):
        load = rng.uniform(0.5, 1.5)
        for t in range(30):
            rows.append({"cat": u, "week": t, "y": 10 + load * f[t] + rng.normal(scale=0.2) + (effect if u == 0 and t >= 20 else 0)})
    return pd.DataFrame(rows)


def test_synth_recovers_single_unit_effect():
    r = cs_synth.synth(_synth_panel(), "cat", "week", "y", 0, 20)
    assert abs(r["estimate"]["value"] - 3.0) < 0.5
    assert next(d for d in r["diagnostics"] if d["name"] == "placebo_in_space_rank_p")["status"] == "pass"
    s = cs_synth.synth(_synth_panel(), "cat", "week", "y", 0, 20, method="sdid")
    assert s["estimate"]["ci_low"] <= 3.0 <= s["estimate"]["ci_high"]


def test_synth_sc_verdict_inconclusive_and_warns_about_sdid():
    r = cs_synth.synth(_synth_panel(), "cat", "week", "y", 0, 20)
    assert r["verdict"] == "inconclusive"
    assert any("method='sdid'" in w for w in r["warnings"])


def test_synth_sdid_reports_fit_weights_and_spillover():
    s = cs_synth.synth(_synth_panel(), "cat", "week", "y", 0, 20, method="sdid")
    diags = {d["name"]: d for d in s["diagnostics"]}
    assert diags["pre_period_fit"]["status"] == "pass" and diags["pre_period_fit"]["value"] < 0.5
    assert diags["donor_weight_concentration"]["status"] == "pass"
    assert any("spillover" in w for w in s["warnings"])
    assert s["extra"]["pre_rmse"] > 0 and s["extra"]["top_donor_weights"]


def test_synth_sdid_warns_on_few_donors_and_concentrated_weight():
    rng = np.random.default_rng(7)
    f = np.cumsum(rng.normal(size=30))
    rows = []
    # donor 1 tracks the treated unit; donors 2-4 move strongly the other way, so the weight piles on donor 1
    # and the weighted donors track the treated unit badly
    for u in range(5):
        for t in range(30):
            base = f[t] if u in (0, 1) else -5 * f[t]
            rows.append({"cat": u, "week": t, "y": 10 + base + rng.normal(scale=0.1) + (3 if u == 0 and t >= 20 else 0)})
    s = cs_synth.synth(pd.DataFrame(rows), "cat", "week", "y", 0, 20, method="sdid")
    assert any("Only 4 donors" in w for w in s["warnings"])
    conc = next(d for d in s["diagnostics"] if d["name"] == "donor_weight_concentration")
    assert conc["status"] == "warn" and conc["value"] > 0.5
    assert next(d for d in s["diagnostics"] if d["name"] == "pre_period_fit")["status"] == "warn"
