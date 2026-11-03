"""Inference for few clusters (randomisation inference) and sensitivity to unmeasured confounding (E-values)."""
from __future__ import annotations

import itertools
import math

import numpy as np

from cs_common import check, estimate, result

EXACT_LIMIT = 20000


def permute(df, cluster: str, arm: str, y: str, treated=1, iters: int = 10000, seed: int = 0) -> dict:
    """Randomisation inference on cluster means: re-draw which clusters were treated and compare the observed gap."""
    g = df.groupby(cluster).agg(y=(y, "mean"), t=(arm, "first"))
    if df.groupby(cluster)[arm].nunique().max() > 1:
        raise ValueError(f"{arm} varies within a {cluster}; randomisation inference needs cluster-level assignment")
    t = (g["t"] == treated).to_numpy()
    yv = g["y"].to_numpy(float)
    n, k = len(t), int(t.sum())
    obs = yv[t].mean() - yv[~t].mean()
    total = math.comb(n, k)
    if total <= EXACT_LIMIT:
        draws = []
        for idx in itertools.combinations(range(n), k):
            m = np.zeros(n, bool)
            m[list(idx)] = True
            draws.append(yv[m].mean() - yv[~m].mean())
        mode = "exact"
    else:
        rng = np.random.default_rng(seed)
        draws = []
        for _ in range(iters):
            m = np.zeros(n, bool)
            m[rng.choice(n, k, replace=False)] = True
            draws.append(yv[m].mean() - yv[~m].mean())
        mode = f"{iters} random draws"
    draws = np.asarray(draws)
    hits = int((np.abs(draws) >= abs(obs) - 1e-12).sum())
    # exact enumeration includes the observed assignment; random draws do not, so add it back: (count + 1) / (iters + 1)
    p = hits / len(draws) if mode == "exact" else (hits + 1) / (len(draws) + 1)
    diags = [check("cluster_count", "warn" if n < 10 else "pass", n,
                   f"Only {n} clusters: the smallest attainable p-value is {1 / total:.3g}." if n < 10 else "")]
    est = estimate(obs, None, None, p, unit=f"difference in cluster means of {y}")
    return result("permute", est, f"Difference in mean {y} between treated and control {cluster}s; p-value from "
                  f"re-assigning treatment across {n} clusters ({mode}).", diags, [],
                  extra={"n_clusters": n, "n_treated": k, "assignments": total})


def _e(rr: float) -> float:
    rr = 1 / rr if rr < 1 else rr
    return rr + math.sqrt(rr * (rr - 1))


def evalue(rr: float | None = None, ci_low: float | None = None, ci_high: float | None = None,
           smd: float | None = None) -> dict:
    """VanderWeele-Ding E-value. For a standardised mean difference, RR ~ exp(0.91 * d)."""
    if rr is None and smd is None:
        raise ValueError("give rr or smd")
    warnings = []
    if smd is not None:
        rr = math.exp(0.91 * smd)
        ci_low = math.exp(0.91 * ci_low) if ci_low is not None else None
        ci_high = math.exp(0.91 * ci_high) if ci_high is not None else None
        warnings.append("Converted a standardised mean difference to an approximate risk ratio (exp(0.91 d)).")
    e_point = _e(rr)
    if ci_low is not None and ci_high is not None:
        e_ci = 1.0 if ci_low <= 1 <= ci_high else _e(ci_low if rr > 1 else ci_high)
    else:
        e_ci = None
    note = (f"An unmeasured confounder would need a risk-ratio association of {e_point:.2f} with both treatment and "
            f"outcome to explain the estimate away" + (f", and {e_ci:.2f} to move the CI to include no effect." if e_ci else "."))
    return result("evalue", None, note, [], warnings, extra={"rr": rr, "evalue_point": e_point, "evalue_ci": e_ci})
