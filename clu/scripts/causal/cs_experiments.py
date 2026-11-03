"""Experiment statistics: power/MDE, SRM, two-arm tests (delta method, CUPED), multiple-testing correction."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

from cs_common import check, estimate, result

SRM_ALPHA = 0.001


def design_effect(cluster_size: float | None, icc: float | None) -> float:
    if not cluster_size or icc is None:
        return 1.0
    return 1.0 + (cluster_size - 1.0) * icc


def power(metric: str, baseline: float, sd: float | None = None, mde_rel: float | None = None,
          n_per_arm: float | None = None, alpha: float = 0.05, power_: float = 0.8, arms: int = 2,
          cluster_size: float | None = None, icc: float | None = None, units_per_day: float | None = None,
          smallest_effect_rel: float | None = None) -> dict:
    """Two-sided z-approximation, equal allocation. Give mde_rel to get n, or n_per_arm to get the MDE."""
    if metric == "proportion":
        var = baseline * (1 - baseline)
    elif sd is not None:
        var = sd ** 2
    else:
        raise ValueError("sd is required for mean and ratio metrics (for ratio: sd of the delta-method linearised metric)")
    comparisons = max(arms - 1, 1)
    a = alpha / comparisons  # Bonferroni across treatment arms vs control
    z = stats.norm.ppf(1 - a / 2) + stats.norm.ppf(power_)
    deff = design_effect(cluster_size, icc)
    warnings, diags = [], []
    if a != alpha:
        warnings.append(f"{comparisons} treatment arms: alpha split to {a:.4f} per comparison (Bonferroni).")
    if mde_rel is not None:
        delta = abs(mde_rel) * baseline
        n = math.ceil(2 * var * z ** 2 / delta ** 2 * deff)
        out_n, out_mde = n, abs(mde_rel)
    elif n_per_arm is not None:
        delta = z * math.sqrt(2 * var * deff / n_per_arm)
        out_n, out_mde = int(n_per_arm), delta / baseline
    else:
        raise ValueError("give mde_rel or n_per_arm")
    days = math.ceil(out_n * arms / units_per_day) if units_per_day else None
    if days is not None:
        weeks = math.ceil(days / 7)
        warnings.append(f"Run whole weeks: {days} days needed, plan {weeks * 7} days (weekday and weekend behaviour differ).")
    if smallest_effect_rel is not None:
        ok = out_mde <= abs(smallest_effect_rel)
        diags.append(check("mde_vs_smallest_effect_that_matters", "pass" if ok else "warn", out_mde,
                           "" if ok else "The test cannot detect the smallest effect that matters; a null result would be inconclusive."))
    if deff > 1:
        warnings.append(f"Cluster design effect {deff:.2f}: sample counts are in units, analyse at the cluster level.")
    extra = {"n_per_arm": out_n, "mde_rel": out_mde, "mde_abs": out_mde * baseline, "design_effect": deff,
             "alpha_per_comparison": a, "days": days, "total_units": out_n * arms}
    return result("power", None, "Planning quantity, not an effect estimate.", diags, warnings, extra=extra)


def srm(counts: list[float], weights: list[float] | None = None) -> dict:
    counts = np.asarray(counts, dtype=float)
    w = np.asarray(weights if weights else [1.0] * len(counts), dtype=float)
    expected = counts.sum() * w / w.sum()
    chi2, p = stats.chisquare(counts, expected)
    ok = p >= SRM_ALPHA
    d = [check("sample_ratio_mismatch", "pass" if ok else "fail", p,
               "" if ok else "Allocation differs from the design. Stop: find the cause (assignment, logging, bots, redirects) before reading any metric.")]
    return result("srm", None, "Test that observed arm sizes match the planned split.", d, [],
                  extra={"chi2": float(chi2), "p_value": float(p), "observed": counts.tolist(), "expected": expected.tolist()})


def _arm_stats(y: np.ndarray, den: np.ndarray | None):
    n = len(y)
    if den is None:
        m = y.mean()
        return n, m, y.var(ddof=1)
    my, md = y.mean(), den.mean()
    vy, vd = y.var(ddof=1), den.var(ddof=1)
    cov = np.cov(y, den, ddof=1)[0, 1]
    r = my / md
    var_r_unit = (vy / md ** 2 - 2 * my * cov / md ** 3 + my ** 2 * vd / md ** 4)  # delta method, per unit
    return n, r, var_r_unit


def abtest(df, arm: str, y: str, control, den: str | None = None, pre: str | None = None,
           weights: list[float] | None = None, winsorize: float | None = None, mde_rel: float | None = None,
           alpha: float = 0.05) -> dict:
    """Difference between each treatment arm and control, analysed at the row (= randomisation unit) level."""
    import pandas as pd
    from statsmodels.stats.multitest import multipletests
    df = df.copy()
    warnings, diags = [], []
    arms = [a for a in pd.unique(df[arm]) if a != control]
    if control not in set(df[arm]):
        raise ValueError(f"control arm {control!r} not in column {arm}")
    order = [control, *arms]
    counts = [int((df[arm] == a).sum()) for a in order]
    diags += srm(counts, weights)["diagnostics"]
    if winsorize:
        cap = df[y].quantile(winsorize)
        df[y] = df[y].clip(upper=cap)
        warnings.append(f"{y} capped at its {winsorize:.3f} quantile ({cap:g}); this rule must match the analysis plan.")
    top = df[y].abs().sort_values(ascending=False)
    k = max(1, int(len(top) * 0.01))
    top_share = float(top.iloc[:k].sum() / top.sum()) if top.sum() else 0.0
    diags.append(check("heavy_tail_top1pct_share", "warn" if top_share > 0.25 else "pass", top_share,
                       "Top 1% of units carry over 25% of the metric; pre-declare winsorising or report a trimmed version." if top_share > 0.25 else ""))
    if pre and den:
        warnings.append("CUPED is not applied to ratio metrics in this toolkit; pre-period column ignored.")
        pre = None
    if pre:
        x = df[pre]
        theta = np.cov(df[y], x, ddof=1)[0, 1] / x.var(ddof=1)
        raw_var = df[y].var(ddof=1)
        df[y] = df[y] - theta * (x - x.mean())
        reduction = 1 - df[y].var(ddof=1) / raw_var
        diags.append(check("cuped_variance_reduction", "pass", reduction, f"theta={theta:.4g}"))
        c, t = df[df[arm] == control][pre], df[df[arm] != control][pre]
        pb = stats.ttest_ind(c, t, equal_var=False).pvalue
        diags.append(check("pre_period_balance", "warn" if pb < 0.01 else "pass", pb,
                           "Arms differ before treatment: check assignment." if pb < 0.01 else ""))
    base = df[df[arm] == control]
    nc, mc, vc = _arm_stats(base[y].to_numpy(float), base[den].to_numpy(float) if den else None)
    comps = []
    zc = stats.norm.ppf(1 - alpha / 2)
    zadj = stats.norm.ppf(1 - alpha / len(arms) / 2)  # Bonferroni across treatment arms
    for a in arms:
        g = df[df[arm] == a]
        nt, mt, vt = _arm_stats(g[y].to_numpy(float), g[den].to_numpy(float) if den else None)
        diff = mt - mc
        se = math.sqrt(vt / nt + vc / nc)
        se_rel = math.sqrt(vt / (nt * mc ** 2) + mt ** 2 * vc / (nc * mc ** 4))
        p = 2 * (1 - stats.norm.cdf(abs(diff / se)))
        comps.append({"arm": a, "n": nt, "value": mt, "diff_abs": diff, "ci_abs": [diff - zc * se, diff + zc * se],
                      "diff_rel": diff / mc, "ci_rel": [diff / mc - zc * se_rel, diff / mc + zc * se_rel], "p_value": p,
                      "ci_abs_adj": [diff - zadj * se, diff + zadj * se],
                      "ci_rel_adj": [diff / mc - zadj * se_rel, diff / mc + zadj * se_rel]})
    multi = len(comps) > 1
    if multi:
        adj = multipletests([c["p_value"] for c in comps], method="holm")[1]
        for c, pa in zip(comps, adj):
            c["p_value_holm"] = float(pa)
        warnings.append(f"More than one treatment arm: the headline is arm '{comps[0]['arm']}' with a Holm-adjusted p-value and a "
                        f"Bonferroni-adjusted CI (alpha/{len(comps)}). The other arms are in extra.comparisons with "
                        "p_value_holm, ci_abs_adj and ci_rel_adj.")
    first = comps[0]
    p_head = first.get("p_value_holm", first["p_value"])
    if abs(mc) < 2 * math.sqrt(vc / nc):
        warnings.append("Control mean is indistinguishable from zero, so a relative change is meaningless; the headline is the absolute difference.")
        ci_a = first["ci_abs_adj"] if multi else first["ci_abs"]
        est = estimate(first["diff_abs"], ci_a[0], ci_a[1], p_head, unit="absolute difference vs control")
        mde_rel = None
    else:
        ci_r = first["ci_rel_adj"] if multi else first["ci_rel"]
        est = estimate(first["diff_rel"], ci_r[0], ci_r[1], p_head, unit="relative change vs control")
    metric = f"sum({y})/sum({den})" if den else f"mean({y})"
    estimand = (f"Average effect of assignment to '{first['arm']}' vs '{control}' on {metric}, per randomisation unit "
                f"(intent to treat). Relative to control = {mc:.6g}.")
    srm_failed = any(d["name"] == "sample_ratio_mismatch" and d["status"] == "fail" for d in diags)
    if srm_failed:
        warnings.append("SRM failed: the result is invalid until the allocation problem is found; do not report the effect.")
    out = result("abtest", est, estimand, diags, warnings,
                 extra={"control": {"arm": control, "n": nc, "value": mc}, "comparisons": comps}, mde_abs=mde_rel)
    if srm_failed:
        out["verdict"] = "inconclusive"
    return out


def multitest(pvalues: list[float], names: list[str] | None = None, method: str = "bh", alpha: float = 0.05) -> dict:
    from statsmodels.stats.multitest import multipletests
    m = {"bh": "fdr_bh", "holm": "holm"}[method]
    reject, adj, _, _ = multipletests(pvalues, alpha=alpha, method=m)
    names = names or [f"h{i + 1}" for i in range(len(pvalues))]
    rows = [{"name": n, "p": float(p), "p_adjusted": float(a), "significant": bool(r)}
            for n, p, a, r in zip(names, pvalues, adj, reject)]
    return result("multitest", None, f"{method.upper()} adjustment over {len(pvalues)} hypotheses.", [], [],
                  extra={"method": method, "alpha": alpha, "results": rows})
