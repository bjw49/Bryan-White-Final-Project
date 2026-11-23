"""Deterministic analytics exposed to agents through the gateway (so LLMs never do arithmetic by hand).

All functions take plain data and return plain dicts. Design thresholds are labelled [design].
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

EULER = 0.5772156649015329
EQUITY_LIKE = {"US_EQ", "INTL_EQ", "US_HY"}
SERIES_OF = {"US_EQ": "US_EQ", "INTL_EQ": "INTL_EQ", "US_IG": "US_IG_CORP", "US_HY": "US_HY"}


def _ann(x: float) -> float:
    return float(x)


def blend_stats(rets: pd.DataFrame, bench: pd.Series, w: dict[str, float]) -> dict[str, Any]:
    """rets: columns=manager ids (monthly decimal returns), common index; bench aligned. w sums to 1 (monthly rebalanced)."""
    ids = list(w)
    wv = np.array([w[i] for i in ids])
    port = rets[ids].values @ wv
    b = bench.values
    ex = port - b
    n = len(ex)
    te = ex.std(ddof=1) * math.sqrt(12)
    ann_ex = ex.mean() * 12
    ir = ann_ex / te if te > 0 else float("nan")
    beta = np.cov(port, b, ddof=1)[0, 1] / b.var(ddof=1)
    cum = np.cumprod(1 + port); peak = np.maximum.accumulate(cum)
    cumb = np.cumprod(1 + b); peakb = np.maximum.accumulate(cumb)
    r12 = pd.Series(ex).rolling(12).sum().min() if n >= 12 else float("nan")
    return {"n_months": int(n), "ann_return": float((np.prod(1 + port) ** (12 / n)) - 1), "ann_excess_arith": float(ann_ex),
            "tracking_error": float(te), "info_ratio": float(ir), "beta": float(beta), "max_dd": float((cum / peak - 1).min()),
            "bench_max_dd": float((cumb / peakb - 1).min()), "worst_12m_excess_sum": float(r12),
            "t_stat_excess": float(ex.mean() / (ex.std(ddof=1) / math.sqrt(n))) if n > 2 and ex.std(ddof=1) > 0 else float("nan")}


def risk_contrib_te(rets: pd.DataFrame, bench: pd.Series, w: dict[str, float]) -> dict[str, float]:
    ids = list(w); wv = np.array([w[i] for i in ids])
    ex = rets[ids].sub(bench, axis=0)
    cov = ex.cov().values * 12
    tot = float(wv @ cov @ wv)
    if tot <= 0:
        return {i: float("nan") for i in ids}
    mc = cov @ wv
    return {i: round(float(wv[k] * mc[k] / tot), 3) for k, i in enumerate(ids)}


def dsr_core(sr: float, T: int, skew: float, kurt_raw: float, n_trials: int, var_sr_trials: float | None = None) -> tuple[float, float]:
    """Deflated Sharpe ratio, Bailey & Lopez de Prado (2014), eq. for PSR(SR0) with SR0 = expected max of N trial Sharpes.
    var_sr_trials: variance of the Sharpe ratios across the N trials; if None, this series' own sampling variance is used.
    Returns (sr0, dsr). Verified against the paper's numerical example in tests/test_dsr_paper.py."""
    v_own = (1 - skew * sr + (kurt_raw - 1) / 4 * sr ** 2) / (T - 1)
    v = v_own if var_sr_trials is None else var_sr_trials
    N = max(int(n_trials), 1)
    sr0 = 0.0 if N == 1 else math.sqrt(v) * ((1 - EULER) * norm.ppf(1 - 1 / N) + EULER * norm.ppf(1 - 1 / (N * math.e)))
    return float(sr0), float(norm.cdf((sr - sr0) / math.sqrt(v_own)))


def deflated_sharpe(excess: np.ndarray, n_trials: int) -> dict[str, Any]:
    """Bailey & Lopez de Prado (2014). Sharpe here = monthly mean/sd of EXCESS returns. n_trials = number of strategies
    effectively tried to find this one (unknown in practice: caller states an assumption). Returns probability the true SR>0
    after deflating for selection among n_trials, assuming the cross-trial SR variance equals this series' own sampling variance."""
    x = np.asarray(excess, float); T = len(x)
    if T < 24 or x.std(ddof=1) == 0:
        return {"error": "need >=24 months and non-zero variance"}
    sr = x.mean() / x.std(ddof=1)
    s = pd.Series(x); skew = float(s.skew()); kurt = float(s.kurt() + 3)  # raw kurtosis
    var_sr = (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (T - 1)
    N = max(int(n_trials), 1)
    sr0, dsr = dsr_core(sr, T, skew, kurt, N)
    psr0 = float(norm.cdf(sr / math.sqrt(var_sr)))
    return {"n_months": T, "monthly_sharpe_excess": round(float(sr), 4), "annualised_sharpe_excess": round(float(sr * math.sqrt(12)), 3),
            "skew": round(skew, 3), "kurtosis_raw": round(kurt, 3), "n_trials_assumed": N, "sr0_threshold_monthly": round(float(sr0), 4),
            "probabilistic_sharpe_vs_zero": round(psr0, 3), "deflated_sharpe_probability": round(dsr, 3),
            "years_to_t2_if_iid": round(float((2 / (sr * math.sqrt(12))) ** 2), 1) if sr > 0 else None,
            "caveat": "n_trials is an assumption, the real number of strategies behind a track record is unknown; treat the probability as an upper bound on credibility. IID-based."}


def class_inputs(stats_rows: list[dict], corr_rows: list[dict], cma_map: list[dict], cma_summary: list[dict]) -> dict[str, Any]:
    """Assemble expected return / vol / corr per class from cma.db (direct estimates only) and historical class series."""
    st = {r["series_id"]: r for r in stats_rows}
    mp = {r["asset_class"]: r for r in cma_map}
    sm = {r["cma_class"]: r for r in cma_summary}
    out = {}
    for ac in ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]:
        m = mp[ac]; s = sm[m["cma_class"]]
        if ac == "CASH":
            out[ac] = {"exp_return": float(s["arith_mean"]), "exp_return_min": float(s["arith_min"]), "exp_return_max": float(s["arith_max"]), "vol": float(s["vol_mean"]),
                       "basis": f"CMA_DIRECT ({int(s['n_houses'])} houses)", "n_houses": int(s["n_houses"])}
            continue
        hist = st[SERIES_OF[ac]]
        out[ac] = {"exp_return": float(s["arith_mean"]), "exp_return_min": float(s["arith_min"]), "exp_return_max": float(s["arith_max"]),
                   "exp_geo_mean": float(s["geo_mean"]), "vol": float(hist["ann_vol"]), "cma_vol_mean": float(s["vol_mean"]),
                   "basis": f"CMA_DIRECT ({int(s['n_houses'])} houses, arithmetic mean; range = lowest to highest house)", "n_houses": int(s["n_houses"]),
                   "history_ann_return": float(hist["ann_return"]),
                   "note": "volatility in the covariance is the historical class series (COMMON_SAMPLE); CMA volatility mean shown for comparison"}
    c = {}
    for r in corr_rows:
        a = {"US_IG_CORP": "US_IG"}.get(r["series_a"], r["series_a"]); b = {"US_IG_CORP": "US_IG"}.get(r["series_b"], r["series_b"])
        c[(a, b)] = c[(b, a)] = r["corr"]
    return {"classes": out, "corr": c}


def _cov(ci: dict, mode: str, vol_scale: float = 1.0) -> tuple[list[str], np.ndarray, np.ndarray]:
    names = ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]
    vol = np.array([ci["classes"][n]["vol"] for n in names]) * vol_scale
    R = np.eye(5)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j:
                R[i, j] = 0.0 if "CASH" in (a, b) else ci["corr"].get((a, b), 0.0)
    if mode == "downside":  # [design] stress correlations: risk assets converge, credit/equity link tightens, IG gets a positive link
        for i, a in enumerate(names):
            for j, b in enumerate(names):
                if i == j or "CASH" in (a, b):
                    continue
                if a in ("US_EQ", "INTL_EQ") and b in ("US_EQ", "INTL_EQ"):
                    R[i, j] = max(R[i, j], 0.90)
                elif {a, b} & {"US_HY"} and {a, b} & {"US_EQ", "INTL_EQ"}:
                    R[i, j] = max(R[i, j], 0.80)
                elif {a, b} & {"US_IG"} and {a, b} & {"US_HY"}:
                    R[i, j] = max(R[i, j], 0.60)
                elif {a, b} & {"US_IG"} and {a, b} & {"US_EQ", "INTL_EQ"}:
                    R[i, j] = max(R[i, j], 0.30)
    S = np.outer(vol, vol) * R
    return names, S, R


def allocation_eval(w_pct: dict[str, float], ci: dict, episodes: list[dict], guardrails: list[dict],
                    ret_shift: float = 0.0, vol_scale: float = 1.0, corr_mode: str = "baseline") -> dict[str, Any]:
    names = ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]
    unknown = [k for k in w_pct if k not in names]
    if unknown:
        raise ValueError(f"asset classes not available: {unknown}")
    w = np.array([w_pct.get(n, 0.0) for n in names], float) / 100.0
    total = float(w.sum() * 100)
    mu = np.array([ci["classes"][n]["exp_return"] for n in names])
    risky = np.array([n in EQUITY_LIKE for n in names])
    mu_adj = mu + ret_shift * risky
    n_, S, R = _cov(ci, corr_mode, vol_scale)
    var = float(w @ S @ w); vol = math.sqrt(var)
    mc = S @ w
    rc = {names[i]: round(float(w[i] * mc[i] / var), 3) if var > 0 else 0.0 for i in range(5)}
    exp_arith = float(w @ mu_adj)
    exp_geo = exp_arith - 0.5 * var
    lo_ret = float(w @ np.array([ci["classes"][n]["exp_return_min"] for n in names])); hi_ret = float(w @ np.array([ci["classes"][n]["exp_return_max"] for n in names]))
    eps: dict[str, dict] = {}
    for e in episodes:
        eps.setdefault(e["episode"], {})[e["series_id"]] = e["cum_return"]
    ep_out = []
    for name, d in eps.items():
        tot = 0.0; miss = []
        for n, wi in zip(names, w):
            if wi == 0 or n == "CASH":
                continue
            s = SERIES_OF[n]
            if s in d and d[s] is not None and not (isinstance(d[s], float) and math.isnan(d[s])):
                tot += wi * d[s]
            else:
                miss.append(n)
        ep_out.append({"episode": name, "portfolio_cum_return_no_rebalance": round(tot, 4), "classes_missing_data": miss})
    gr = []
    gmap = {g["asset_class"]: g for g in guardrails}
    for n, wi in zip(names, w):
        g = gmap.get(n)
        if g is None:
            gr.append({"class": n, "weight": round(wi * 100, 1), "status": "NO_GUARDRAIL"}); continue
        ok = (g["min_w"] - 1e-9) <= wi <= (g["max_w"] + 1e-9)
        gr.append({"class": n, "weight": round(wi * 100, 1), "min": g["min_w"] * 100, "max": g["max_w"] * 100, "status": "PASS" if ok else "FAIL"})
    return {"corr_mode": corr_mode, "weights_sum_pct": round(total, 4), "sum_ok": abs(total - 100) < 1e-6,
            "expected_return_arith": round(exp_arith, 4), "expected_return_geometric_approx": round(exp_geo, 4),
            "volatility": round(vol, 4), "risk_contribution": rc,
            "expected_return_arith_range_across_houses": [round(lo_ret + ret_shift * float(w[risky].sum()), 4), round(hi_ret + ret_shift * float(w[risky].sum()), 4)],
            "return_note": "index-level CMA returns before any manager alpha; manager returns in this system are net of fees, so manager alpha (if any) is NOT added here",
            "bases": {n: ci["classes"][n]["basis"] for n in names if w_pct.get(n, 0) > 0},
            "episodes": ep_out, "guardrails": gr,
            "inputs_shift": {"ret_shift_on_risky": ret_shift, "vol_scale": vol_scale},
            "caveats": ["expected returns are the mean of direct CMA estimates from the houses that publish that class (range = lowest to highest house)",
                        "range low/high assume every class sits at its lowest/highest house at once: it is an outer envelope, not a confidence interval",
                        "vol and correlations are historical class series (COMMON_SAMPLE); downside correlations are [design] stress settings, not estimates"]}
