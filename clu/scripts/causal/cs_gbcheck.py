"""Recompute a GrowthBook result from per-arm aggregates with GrowthBook's own engine (gbstats), and compare."""
from __future__ import annotations

from cs_common import check, estimate, result, unavailable


def _stat(metric: str, a: dict):
    from gbstats.models.statistics import ProportionStatistic, RatioStatistic, SampleMeanStatistic
    if metric == "proportion":
        return ProportionStatistic(n=int(a["n"]), sum=float(a["sum"]))
    if metric == "mean":
        return SampleMeanStatistic(n=int(a["n"]), sum=float(a["sum"]), sum_squares=float(a["sum_squares"]))
    if metric == "ratio":
        m = SampleMeanStatistic(n=int(a["n"]), sum=float(a["sum"]), sum_squares=float(a["sum_squares"]))
        d = SampleMeanStatistic(n=int(a["n"]), sum=float(a["den_sum"]), sum_squares=float(a["den_sum_squares"]))
        return RatioStatistic(n=int(a["n"]), m_statistic=m, d_statistic=d, m_d_sum_of_products=float(a["sum_products"]))
    raise ValueError(f"metric must be mean, proportion or ratio, not {metric}")


def gbcheck(arms: list[dict], metric: str, reported: dict | None = None, alpha: float = 0.05,
            tolerance: float = 0.1) -> dict:
    """arms[0] is the control. Each arm: n, sum, sum_squares (+ den_sum, den_sum_squares, sum_products for ratio).
    reported: GrowthBook's numbers for arms[1] as {"percent_change": x, "ci_low": a, "ci_high": b} (relative, fractions)."""
    try:
        from gbstats.frequentist.tests import FrequentistConfig, TwoSidedTTest
    except ImportError:
        return unavailable("gbcheck", "gbstats is not installed; run scripts/causal/setup.sh")
    base = _stat(metric, arms[0])
    res = TwoSidedTTest([(base, _stat(metric, arms[1]))], FrequentistConfig(difference_type="relative", alpha=alpha)).compute_result()
    est = estimate(res.expected, res.ci[0], res.ci[1], res.pValue, unit="relative change vs control (gbstats, frequentist)")
    diags, warnings = [], []
    if reported:
        gap = abs(reported["percent_change"] - res.expected)
        # Pass if gap is within tolerance scaled by either effect, OR within a tenth of the CI half-width
        scale = max(abs(res.expected), abs(reported["percent_change"]))
        threshold1 = tolerance * scale
        ci_half_width = (res.ci[1] - res.ci[0]) / 2
        threshold2 = 0.1 * ci_half_width
        ok = gap <= threshold1 or gap <= threshold2
        if ok:
            diags.append(check("matches_growthbook", "pass", gap, ""))
        else:
            msg = (f"GrowthBook's reported effect differs from a recomputation on the same aggregates. "
                   f"Gap: {gap:.6f}, absolute threshold: {threshold1:.6f}, CI-based threshold: {threshold2:.6f}. "
                   f"Check CUPED, winsorising, the metric window or the analysis phase.")
            diags.append(check("matches_growthbook", "fail", gap, msg))
        if reported.get("ci_low") is not None and reported.get("ci_high") is not None:
            w_rep = float(reported["ci_high"]) - float(reported["ci_low"])
            w_ours = float(res.ci[1] - res.ci[0])
            rel = abs(w_rep - w_ours) / w_ours if w_ours else float("inf")
            diags.append(check("ci_width_matches_growthbook", "warn" if rel > 0.25 else "pass", rel,
                               f"GrowthBook's CI width {w_rep:.4g} vs recomputed {w_ours:.4g} ({rel:.0%} apart). "
                               "A narrower GrowthBook CI usually means CUPED; a different width can also mean a "
                               "different alpha, sequential testing or another analysis phase." if rel > 0.25 else ""))
    warnings.append("Only the aggregates are recomputed: this checks GrowthBook's arithmetic and settings, "
                    "not interference or assignment problems.")
    return result("gbcheck", est, "GrowthBook's relative effect of arm 1 vs control, recomputed with gbstats.", diags, warnings)
