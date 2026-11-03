"""Synthetic control (few treated units) and synthetic DiD, via diff-diff."""
from __future__ import annotations

import warnings as _w

import numpy as np

from cs_common import check, estimate, result


def synth(df, unit: str, time: str, y: str, treated_unit, start, method: str = "sc",
          donors: list | None = None, mde_abs: float | None = None) -> dict:
    import diff_diff as dd
    _w.filterwarnings("ignore")
    d = df.copy()
    if donors:
        d = d[d[unit].isin([treated_unit, *donors])]
    d["_treated"] = ((d[unit] == treated_unit) & (d[time] >= start)).astype(int)
    d["_ever"] = (d[unit] == treated_unit).astype(int)
    diags, warnings = [], []
    n_donors = d[unit].nunique() - 1
    if method == "sdid":
        post = sorted(d.loc[d[time] >= start, time].unique().tolist())
        r = dd.SyntheticDiD(seed=0).fit(d, outcome=y, treatment="_ever", unit=unit, time=time, post_periods=post)
        est = estimate(r.att, *r.conf_int, p_value=r.p_value, se=r.se, unit=f"change in {y}")
        # pre-period fit: diff-diff's pre_treatment_fit is the RMSE between the treated unit and the weighted donors
        # (after SDID's level adjustment), scaled here by the treated series' pre-period SD, as for SC
        pre_sd = float(d[(d[unit] == treated_unit) & (d[time] < start)][y].std())
        fit_ratio = float(r.pre_treatment_fit) / pre_sd if pre_sd else float("nan")
        diags.append(check("pre_period_fit", "warn" if fit_ratio > 0.5 else "pass", fit_ratio,
                           "Pre-period RMSE over half the treated series' SD: the weighted donors do not track the treated unit."
                           if fit_ratio > 0.5 else f"Pre-period RMSE {float(r.pre_treatment_fit):.4g}."))
        uw = {k: float(v) for k, v in r.unit_weights.items()}
        top_w = max(uw.values()) if uw else float("nan")
        conc = r.get_weight_concentration()
        diags.append(check("donor_weight_concentration", "warn" if top_w > 0.5 else "pass", top_w,
                           f"Effective number of donors {conc['effective_n']:.1f}."
                           + (" One donor carries over half the weight: the result rests on that single unit." if top_w > 0.5 else "")))
        if n_donors < 10:
            warnings.append(f"Only {n_donors} donors: the synthetic comparison and its placebo-based CI rest on very few units.")
        warnings.append("Check that donors were not affected by the treatment (spillover or cannibalisation).")
        w = {str(k): round(v, 4) for k, v in sorted(uw.items(), key=lambda kv: -kv[1])[:5] if v > 0.001}
        estimand = f"Average post-period effect on {treated_unit} of {y}, versus a reweighted donor pool (synthetic DiD)."
        return result("synth", est, estimand, diags, warnings,
                      extra={"method": "sdid", "n_donors": n_donors, "pre_rmse": float(r.pre_treatment_fit),
                             "top_donor_weights": w, "effective_n_donors": float(conc["effective_n"])}, mde_abs=mde_abs)
    r = dd.SyntheticControl(seed=0).fit(d, outcome=y, treatment="_treated", unit=unit, time=time)
    pl = r.in_space_placebo()
    p = float(r.placebo_p_value)
    pre_sd = float(d[(d[unit] == treated_unit) & (d[time] < start)][y].std())
    fit_ratio = float(r.pre_rmspe) / pre_sd if pre_sd else float("nan")
    diags.append(check("pre_period_fit", "warn" if fit_ratio > 0.5 else "pass", fit_ratio,
                       "Pre-period RMSPE over half the treated series' SD: the synthetic unit does not track it." if fit_ratio > 0.5 else ""))
    diags.append(check("placebo_in_space_rank_p", "pass" if p <= 0.1 else "warn", p,
                       f"Rank p-value from {r.n_placebos} placebo units; the smallest possible is {1 / (r.n_placebos + 1):.3f}."))
    if n_donors < 10:
        warnings.append(f"Only {n_donors} donors: the placebo p-value cannot go below {1 / (n_donors + 1):.2f}.")
    warnings.append("Synthetic control gives a placebo rank p-value, not a confidence interval, so the verdict stays 'inconclusive'. Run method='sdid' for a CI, and read the placebo p together with the pre-period fit.")
    min_p = 1 / (r.n_placebos + 1)
    if abs(p - min_p) < 1e-9:
        warnings.append("The placebo p is at its floor; with this donor pool it cannot separate a real effect from chance.")
    warnings.append("Check that donors were not affected by the treatment (spillover or cannibalisation).")
    w = {str(k): round(float(v), 4) for k, v in sorted(r.donor_weights.items(), key=lambda kv: -kv[1])[:5] if v > 0.001}
    est = estimate(r.att, None, None, p, unit=f"average post-period gap in {y}")
    estimand = f"Average post-period gap in {y} between {treated_unit} and its synthetic control (weighted donors)."
    return result("synth", est, estimand, diags, warnings,
                  extra={"method": "sc", "pre_rmspe": float(r.pre_rmspe), "rmspe_ratio": float(r.rmspe_ratio),
                         "top_donor_weights": w, "n_donors": n_donors, "placebos": pl.to_dict("records")[:30]},
                  mde_abs=mde_abs)
