"""Regression discontinuity (rdrobust) with the manipulation test (rddensity) and falsification checks."""
from __future__ import annotations

import warnings as _w

import numpy as np

from cs_common import check, estimate, result


def _rd(y, x, c, fuzzy=None, h=None):
    from rdrobust import rdrobust
    kw = {"c": c}
    if fuzzy is not None:
        kw["fuzzy"] = fuzzy
    if h is not None:
        kw["h"] = h
    return rdrobust(y, x, **kw)


def rdd(df, x: str, y: str, cutoff: float, fuzzy: str | None = None, covariates: list[str] | None = None,
        placebo_cutoffs: list[float] | None = None, mde_abs: float | None = None) -> dict:
    from rddensity import rddensity
    _w.filterwarnings("ignore")
    d = df[[x, y] + ([fuzzy] if fuzzy else []) + (covariates or [])].dropna()
    X, Y = d[x].to_numpy(float), d[y].to_numpy(float)
    r = _rd(Y, X, cutoff, d[fuzzy].to_numpy(float) if fuzzy else None)
    coef = float(r.coef.loc["Conventional"].iloc[0])
    lo, hi = (float(v) for v in r.ci.loc["Robust"])
    p = float(r.pv.loc["Robust"].iloc[0])
    h = float(r.bws.loc["h"].iloc[0])
    est = estimate(coef, lo, hi, p, se=float(r.se.loc["Robust"].iloc[0]), unit=f"jump in {y} at {x}={cutoff:g}")
    diags, warnings = [], ["CI and p-value are robust bias-corrected (Calonico-Cattaneo-Titiunik); the point estimate is conventional."]
    dens = rddensity(X=X, c=cutoff)
    pd_ = float(dens.test["p_jk"])
    diags.append(check("density_manipulation", "fail" if pd_ < 0.05 else "pass", pd_,
                       "Units bunch on one side of the cutoff: they can sort around it, so RDD is not credible." if pd_ < 0.05 else ""))
    first_stage = None
    if fuzzy:
        D = d[fuzzy].to_numpy(float)
        fs = _rd(D, X, cutoff)
        fs_coef = float(fs.coef.loc["Conventional"].iloc[0])
        fs_p = float(fs.pv.loc["Robust"].iloc[0])
        fs_status = "fail" if fs_p >= 0.05 else ("warn" if abs(fs_coef) < 0.1 else "pass")
        fs_note = f"jump in {fuzzy} at the cutoff = {fs_coef:.3g}, robust p = {fs_p:.3g}"
        if fs_status == "fail":
            fs_note += "; weak or absent first stage makes the fuzzy estimate unreliable"
        elif fs_status == "warn":
            fs_note += "; weak first stage (|jump| < 0.1) may affect precision"
        diags.append(check("first_stage", fs_status, fs_coef, fs_note))
        first_stage = {"jump": fs_coef, "robust_p": fs_p}
    for cov in covariates or []:
        rc = _rd(d[cov].to_numpy(float), X, cutoff)
        pc = float(rc.pv.loc["Robust"].iloc[0])
        diags.append(check(f"covariate_balance:{cov}", "warn" if pc < 0.05 else "pass", pc,
                           f"{cov} jumps at the cutoff: something else changes there." if pc < 0.05 else ""))
    for pc_ in placebo_cutoffs or []:
        side = d[x] < cutoff if pc_ < cutoff else d[x] >= cutoff
        s = d[side]
        try:
            rp = _rd(s[y].to_numpy(float), s[x].to_numpy(float), pc_)
            pp = float(rp.pv.loc["Robust"].iloc[0])
            diags.append(check(f"placebo_cutoff:{pc_:g}", "warn" if pp < 0.05 else "pass", pp,
                               "An effect appears at a fake cutoff." if pp < 0.05 else ""))
        except Exception as e:  # noqa: BLE001
            diags.append(check(f"placebo_cutoff:{pc_:g}", "warn", None, f"could not run: {e}"))
    sens = {}
    for f in (0.5, 2.0):
        rs = _rd(Y, X, cutoff, d[fuzzy].to_numpy(float) if fuzzy else None, h=h * f)
        sens[f"{f}h"] = float(rs.coef.loc["Conventional"].iloc[0])
    flips = any(np.sign(v) != np.sign(coef) for v in sens.values())
    diags.append(check("bandwidth_sensitivity", "warn" if flips else "pass", sens,
                       "The sign changes with the bandwidth." if flips else ""))
    extra_dict = {"bandwidth_h": h, "n_left_right": [int(v) for v in r.N_h], "bandwidth_sensitivity": sens}
    if first_stage is not None:
        extra_dict["first_stage"] = first_stage
    kind = "fuzzy" if fuzzy else "sharp"
    estimand = (f"Effect of crossing {x}={cutoff:g} on {y} for units right at the threshold ({kind} RDD"
                + (f", scaled by the jump in {fuzzy}: effect on compliers at the cutoff" if fuzzy else "") + ").")
    return result("rdd", est, estimand, diags, warnings,
                  extra=extra_dict, mde_abs=mde_abs)
