"""Interrupted time series: structural time-series model with seasonality and a post-period step."""
from __future__ import annotations

import warnings as _w

import numpy as np

from cs_common import check, estimate, result


def _fit(y, post, controls, period):
    import statsmodels.api as sm
    exog = np.column_stack([post] + ([controls] if controls is not None else []))
    seas = [{"period": period, "harmonics": min(2, period // 2)}] if period and period > 1 else None
    m = sm.tsa.UnobservedComponents(y, level="local level", freq_seasonal=seas, exog=exog)
    return m.fit(disp=False, maxiter=500)


def its(df, time: str, y: str, start, period: int = 7, controls: list[str] | None = None,
        mde_abs: float | None = None) -> dict:
    from statsmodels.stats.diagnostic import acorr_ljungbox
    _w.filterwarnings("ignore")
    d = df.sort_values(time).reset_index(drop=True)
    yv = d[y].to_numpy(float)
    post = (d[time] >= start).to_numpy(float)
    ctl = d[controls].to_numpy(float) if controls else None
    n_pre = int((post == 0).sum())
    diags, warnings = [], []
    if n_pre < 3 * max(period, 1):
        warnings.append(f"Only {n_pre} pre-period points for a seasonal period of {period}: the baseline is weak.")
    m = _fit(yv, post, ctl, period)
    i = m.param_names.index("beta.x1")
    lo, hi = m.conf_int()[i]
    est = estimate(m.params[i], lo, hi, m.pvalues[i], se=m.bse[i], unit=f"level shift in {y} per {time} step")
    conv = (getattr(m, "mle_retvals", None) or {}).get("converged")
    diags.append(check("converged", "warn" if conv is False else "pass", None if conv is None else str(bool(conv)),
                       "The model fit did not converge: the estimate and CI may be wrong. Simplify the model or add data."
                       if conv is False else ""))
    lb = acorr_ljungbox(m.resid[max(period, 1) * 2:], lags=[max(period, 2)])
    lbp = float(lb["lb_pvalue"].iloc[0])
    diags.append(check("residual_autocorrelation", "warn" if lbp < 0.05 else "pass", lbp,
                       "Residuals are autocorrelated: the CI is too narrow." if lbp < 0.05 else ""))
    if n_pre >= 6 * max(period, 1):
        mid = n_pre // 2
        pp = np.zeros(n_pre)
        pp[mid:] = 1
        mp = _fit(yv[:n_pre], pp, ctl[:n_pre] if ctl is not None else None, period)
        j = mp.param_names.index("beta.x1")
        plo, phi = mp.conf_int()[j]
        ok = plo <= 0 <= phi
        diags.append(check("placebo_break", "pass" if ok else "warn", mp.params[j],
                           "" if ok else "A fake break inside the pre-period is already significant: the model does not capture the baseline. (a single placebo has a 5% false-alarm rate; treat as a warning sign)"))
    else:
        diags.append(check("placebo_break", "warn", None, "pre-period too short for a placebo break"))
    if not controls:
        warnings.append("No control series: anything else that changed at the same time is attributed to the intervention.")
    estimand = f"Shift in {y} from {start} onward relative to the modelled pre-period trend and seasonality (single series)."
    return result("its", est, estimand, diags, warnings, extra={"n_pre": n_pre, "n_post": int(post.sum())}, mde_abs=mde_abs)
