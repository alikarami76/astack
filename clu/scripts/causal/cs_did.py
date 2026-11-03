"""Difference-in-differences for 2x2 and staggered adoption, via diff-diff (Callaway-Sant'Anna, Sun-Abraham,
imputation). Plain TWFE is only run as a comparison, never as the headline for staggered rollouts."""
from __future__ import annotations

import warnings as _w

import numpy as np

from cs_common import check, estimate, result


def _fit(method: str, df, y, unit, time, first_treat, control, cluster, covariates, aggregate="event_study",
         n_bootstrap: int = 0):
    import diff_diff as dd
    kw = dict(outcome=y, unit=unit, time=time, first_treat=first_treat, covariates=covariates or None)
    boot = dict(n_bootstrap=n_bootstrap, seed=0) if n_bootstrap else {}
    if method == "cs":
        est = dd.CallawaySantAnna(control_group="not_yet_treated" if control == "notyet" else "never_treated",
                                  cluster=cluster or unit, base_period="universal", **boot)
        # "all" returns the event study plus the cohort (group) effects
        return est.fit(df, aggregate="all" if aggregate == "event_study" else aggregate, **kw)
    if method == "sa":
        return dd.SunAbraham(cluster=cluster or unit, **boot).fit(df, **kw)
    if method == "imputation":
        # pretrends=True estimates the pre-period leads, so the pre-trend checks have something to test
        return dd.ImputationDiD(cluster=cluster or unit, pretrends=True, **boot).fit(df, aggregate=aggregate, **kw)
    raise ValueError(f"unknown method {method}")


def _joint_pre_trend(r, pre: dict) -> tuple[float, float, int, str]:
    """Joint Wald test that all pre-period leads are zero: chi2 = b' V^-1 b. Uses the event-study covariance when the
    results carry one, otherwise a diagonal built from the lead SEs (which ignores their correlation)."""
    from scipy import stats
    keys = sorted(pre)
    b = np.array([float(pre[k]["effect"]) for k in keys])
    vcov, index = getattr(r, "event_study_vcov", None), getattr(r, "event_study_vcov_index", None)
    source = "diagonal of the lead SEs (ignores their covariance)"
    V = None
    if vcov is not None and index is not None:
        pos = {int(k): i for i, k in enumerate(index)}
        if all(k in pos for k in keys):
            idx = [pos[k] for k in keys]
            V = np.asarray(vcov, float)[np.ix_(idx, idx)]
            source = "event-study covariance matrix"
    if V is None:
        V = np.diag([float(pre[k]["se"]) ** 2 for k in keys])
    chi2 = float(b @ np.linalg.pinv(V) @ b)
    return chi2, float(stats.chi2.sf(chi2, len(keys))), len(keys), source


def _twfe(df, y, unit, time, first_treat, cluster):
    import pyfixest as pf
    d = df.assign(_treat=((df[first_treat] > 0) & (df[time] >= df[first_treat])).astype(int))
    fit = pf.feols(f"{y} ~ _treat | {unit} + {time}", data=d, vcov={"CRV1": cluster or unit})
    return float(fit.coef()["_treat"]), float(fit.se()["_treat"])


def did(df, y: str, unit: str, time: str, first_treat: str, method: str = "cs", control: str = "never",
        cluster: str | None = None, covariates: list[str] | None = None, placebo_shift: int | None = None,
        honest_m: float | None = 1.0, mde_abs: float | None = None, n_bootstrap: int = 0) -> dict:
    """first_treat: the first treated period for each unit, 0 for never treated."""
    import diff_diff as dd
    _w.filterwarnings("ignore")
    warnings, diags = [], []
    cohorts = sorted(c for c in df[first_treat].unique() if c > 0)
    staggered = len(cohorts) > 1
    if method == "twfe":
        warnings.append("Plain TWFE requested. With staggered adoption it compares late adopters with early adopters "
                        "and can be badly biased; the headline below uses Callaway-Sant'Anna instead.")
        method = "cs"
    if control == "never" and (df[first_treat] == 0).sum() == 0:
        warnings.append("No never-treated units: switching to not-yet-treated controls.")
        control = "notyet"
    # few treated clusters: cluster-robust SEs are too small, so say so up front
    treated_ids = df.loc[df[first_treat] > 0, cluster or unit]
    n_tc = int(treated_ids.nunique())
    level = f"clusters ('{cluster}')" if cluster else "units (no --cluster given)"
    diags.append(check("treated_clusters", "warn" if n_tc < 20 else "pass", n_tc,
                       f"Only {n_tc} treated {level}. Cluster-robust CIs are too narrow with this few clusters. Re-run with "
                       "--n-bootstrap 999, or run randomisation inference on each cluster's post-minus-pre change with "
                       "'cstat.py permute'." if n_tc < 20 else ""))
    r = _fit(method, df, y, unit, time, first_treat, control, cluster, covariates, n_bootstrap=n_bootstrap)
    est = estimate(r.overall_att, *r.overall_conf_int, p_value=r.overall_p_value, se=r.overall_se, unit=f"change in {y}")
    # pre-trends from the event study
    es = getattr(r, "event_study_effects", None) or {}
    pre = {int(k): v for k, v in es.items() if int(k) < -1 or (int(k) == -1 and v.get("se"))}
    pre = {k: v for k, v in pre.items() if v.get("se") and not np.isnan(v["se"])}
    if pre:
        bad = sorted(k for k, v in pre.items() if v["p_value"] < 0.05)
        diags.append(check("pre_trend_leads", "warn" if bad else "pass", len(bad),
                           f"{len(bad)} of {len(pre)} pre-period leads significant (event times {bad}); read with the joint test."
                           if bad else "No significant leads. This does not prove parallel trends (low power); see HonestDiD."))
        chi2, pj, k, source = _joint_pre_trend(r, pre)
        diags.append(check("pre_trend_joint", "fail" if pj < 0.05 else "pass", pj,
                           f"Joint Wald test of {k} leads, chi2={chi2:.3g}, p={pj:.3g}, using the {source}. "
                           + ("The leads are jointly non-zero: parallel trends is rejected." if pj < 0.05 else
                              "Not rejected; this does not prove parallel trends (low power).")))
    else:
        diags.append(check("pre_trend_leads", "warn", None,
                           f"No pre-period event-study estimates available for method '{method}'; parallel trends is untested."))
    # placebo date: pretend treatment started earlier, using only pre-treatment data
    if placebo_shift:
        pdf = df[(df[first_treat] == 0) | (df[time] < df[first_treat])].copy()
        pdf[first_treat] = np.where(pdf[first_treat] > 0, pdf[first_treat] - placebo_shift, 0)
        pdf = pdf[(pdf[first_treat] == 0) | (pdf[first_treat] > pdf[time].min())]
        try:
            pr = _fit(method, pdf, y, unit, time, first_treat, control, cluster, covariates, aggregate=None,
                      n_bootstrap=n_bootstrap)
            lo, hi = pr.overall_conf_int
            ok = lo <= 0 <= hi
            diags.append(check("placebo_date", "pass" if ok else "fail", pr.overall_att,
                               "" if ok else f"A fake start {placebo_shift} periods early already shows an effect; the design is not credible."))
        except Exception as e:  # noqa: BLE001
            diags.append(check("placebo_date", "warn", None, f"could not run: {e}"))
    # HonestDiD sensitivity
    if honest_m is not None:
        if not pre:
            diags.append(check("honestdid_relative_magnitude", "warn", None,
                               f"No pre-period estimates available; HonestDiD cannot assess sensitivity."))
        else:
            try:
                h = dd.compute_honest_did(r, method="relative_magnitude", M=honest_m)
                robust = not (h.ci_lb <= 0 <= h.ci_ub)
                diags.append(check("honestdid_relative_magnitude", "pass" if robust else "warn", [float(h.ci_lb), float(h.ci_ub)],
                                   f"Robust CI if post-period trend violations are up to {honest_m}x the largest pre-period one."
                                   + ("" if robust else " It includes zero: the conclusion depends on parallel trends holding closely.")))
            except Exception as e:  # noqa: BLE001
                diags.append(check("honestdid_relative_magnitude", "warn", None, f"could not run: {str(e)}"))
    extra = {"method": method, "control": control, "cohorts": [int(c) for c in cohorts],
             "event_study": {int(k): {kk: (float(vv) if not isinstance(vv, tuple) else [float(x) for x in vv])
                                      for kk, vv in v.items()} for k, v in es.items()},
             "n_bootstrap": n_bootstrap}
    ge = getattr(r, "group_effects", None)
    if ge:
        extra["cohort_effects"] = {int(g): {"effect": float(v["effect"]), "se": float(v["se"]),
                                            "ci": [float(x) for x in v["conf_int"]], "p_value": float(v["p_value"])}
                                   for g, v in ge.items()}
    if staggered:
        b, se = _twfe(df, y, unit, time, first_treat, cluster)
        extra["twfe_comparison"] = {"estimate": b, "se": se}
        gap = abs(b - est["value"]) > 2 * max(se, est["se"] or 0)
        if gap:
            warnings.append(f"Plain TWFE gives {b:.4g} vs {est['value']:.4g}: staggered-adoption bias. Do not report TWFE.")
    estimand = (f"Average effect on the treated units ({'staggered' if staggered else 'single'} adoption) of {y}, "
                f"averaged over post-treatment periods, versus {'not-yet-treated' if control == 'notyet' else 'never-treated'} units.")
    return result("did", est, estimand, diags, warnings, extra=extra, mde_abs=mde_abs)
