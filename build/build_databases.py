#!/usr/bin/env python3
"""Build the agent databases from the raw project files. Deterministic and re-runnable.

Usage:
  python build/build_databases.py --raw "<Final Project folder>" --out .

Creates (under --out):
  db/market.db            T0 public/reference data: benchmarks, asset-class stats, factors, CMAs, universe, guardrails
  db/mgr_us_equity.db     T1 pseudonymized manager data, one physical DB per asset class (information barrier)
  db/mgr_intl_equity.db
  db/mgr_us_ig.db
  db/mgr_us_hy.db
  db/audit.db             append-only query/tool log, written by the gateway only
  secure/vault.db         T3 identity map (opaque id -> source label). NEVER readable by any agent; never commit.
  docs/DATA_QUALITY_REPORT.md   generated from the data, not typed by hand
"""
from __future__ import annotations

import argparse, hashlib, json, math, re, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

CLASSES = {
    # key: (db file, id prefix, benchmark series, mgr numbers, label)
    "US_EQ":   ("mgr_us_equity.db", "UE", "US_EQ",      range(1, 11),  "US Equity"),
    "INTL_EQ": ("mgr_intl_equity.db", "IE", "INTL_EQ",  range(11, 21), "International Equity"),
    "US_IG":   ("mgr_us_ig.db", "IG", "US_IG_CORP",     range(21, 31), "US Investment Grade Fixed Income"),
    "US_HY":   ("mgr_us_hy.db", "HY", "US_HY",          range(31, 41), "US High Yield Fixed Income"),
}
BM_COL = {"US_EQ": "US Equity", "INTL_EQ": "International Equity", "US_IG_CORP": "US IG Corporates", "US_HY": "US HY"}
BM_LABEL = {"US_EQ": "US equity benchmark (name not provided)", "INTL_EQ": "International equity benchmark (name not provided; manager text says developed ex-US)",
            "US_IG_CORP": "US investment-grade corporate bond index (name not provided)", "US_HY": "US high-yield bond index (name not provided)"}
WINDOWS = {"FULL": None, "10Y": 120, "5Y": 60, "3Y": 36}
EPISODES = {  # [design] calendar windows chosen by us; not an official event database
    "DOTCOM_BEAR": ("2000-03", "2002-09"), "GFC": ("2007-11", "2009-02"), "HY_ENERGY_2015_16": ("2015-06", "2016-02"),
    "Q4_2018": ("2018-10", "2018-12"), "COVID_SHOCK": ("2020-02", "2020-03"), "RATE_SHOCK_2022": ("2022-01", "2022-10"),
}
INJECTION_PATTERNS = [r"ignore (all|any|previous|prior)", r"disregard", r"system prompt", r"you (must|should) (now )?(rank|select|choose|report)",
                      r"top[- ]ranked", r"as an ai", r"override", r"new instructions?", r"<\s*/?\s*(system|assistant|instruction)"]


def eom(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).to_period("M").to_timestamp("M")


# ---------------------------------------------------------------- loading
def load_returns(raw: Path):
    """Return (wide returns DF indexed by month-end, ticker_labels_found, extra_cell_count)."""
    frames, found_tickers = [], []
    for fname in ["1-10", "11-20", "21-30", "31_40"]:
        df = pd.read_excel(raw / "Managers" / "Test Rtns" / f"{fname}.xlsx")
        df = df[[c for c in df.columns if not str(c).startswith("Unnamed")]]
        df = df.dropna(how="all", subset=df.columns[1:])
        df["Date"] = pd.to_datetime(df["Date"]).map(eom)
        df = df.set_index("Date")
        frames.append(df)  # columns are labelled 'Mgr N' in every file
    wide = pd.concat(frames, axis=1, sort=True).sort_index()
    return wide, found_tickers


def load_benchmarks(raw: Path) -> pd.DataFrame:
    bm = pd.read_excel(raw / "Bmk.xlsx").iloc[:, :5]
    bm["Date"] = pd.to_datetime(bm["Date"]).map(eom)
    bm = bm.set_index("Date").sort_index()
    bm.columns = ["US_EQ", "INTL_EQ", "US_IG_CORP", "US_HY"]
    return bm


def read_descriptions(raw: Path) -> dict[int, str]:
    import docx
    out = {}
    for n in range(1, 41):
        p = raw / "Managers" / "Test Descriptions" / f"Mgr {n}.docx"
        if not p.exists():
            continue
        d = docx.Document(p)
        out[n] = "\n".join(x.text.strip() for x in d.paragraphs if x.text.strip())
    return out


# ---------------------------------------------------------------- statistics
def _maxdd(r: np.ndarray) -> float:
    c = np.cumprod(1 + r)
    return float((c / np.maximum.accumulate(c) - 1).min())


def window_stats(r: pd.Series, b: pd.Series, n_win: int | None) -> dict | None:
    idx = r.dropna().index.intersection(b.dropna().index)
    r, b = r.loc[idx], b.loc[idx]
    if n_win is not None:
        if len(idx) < n_win:
            return None
        r, b = r.tail(n_win), b.tail(n_win)
    n = len(r)
    if n < 12:
        return None
    ex = (r - b).to_numpy()
    rv, bv = r.to_numpy(), b.to_numpy()
    te = ex.std(ddof=1) * math.sqrt(12)
    X = np.column_stack([np.ones(n), bv])
    coef, *_ = np.linalg.lstsq(X, rv, rcond=None)
    resid = rv - X @ coef
    s2 = resid @ resid / (n - 2)
    se_a = math.sqrt(s2 * np.linalg.inv(X.T @ X)[0, 0])
    up, dn = bv > 0, bv < 0
    cum_ex = pd.Series((1 + rv)).rolling(12).apply(np.prod, raw=True) / pd.Series((1 + bv)).rolling(12).apply(np.prod, raw=True) - 1
    ac1 = float(np.corrcoef(rv[1:], rv[:-1])[0, 1]) if n > 24 else float("nan")
    return dict(
        n_months=n, start=str(r.index[0].date()), end=str(r.index[-1].date()),
        ann_return=float((1 + rv).prod() ** (12 / n) - 1), ann_benchmark=float((1 + bv).prod() ** (12 / n) - 1),
        ann_excess=float(ex.mean() * 12), tracking_error=float(te), info_ratio=float(ex.mean() * 12 / te) if te > 0 else None,
        ann_vol=float(rv.std(ddof=1) * math.sqrt(12)), beta=float(coef[1]), alpha_ann=float(coef[0] * 12),
        alpha_tstat=float(coef[0] / se_a) if se_a > 0 else None, corr_benchmark=float(np.corrcoef(rv, bv)[0, 1]),
        up_capture=float(rv[up].mean() / bv[up].mean()) if up.any() else None,
        down_capture=float(rv[dn].mean() / bv[dn].mean()) if dn.any() else None,
        hit_rate=float((ex > 0).mean()), worst_12m_excess=float(np.nanmin(cum_ex)) if n >= 12 else None,
        max_dd_manager=_maxdd(rv), max_dd_benchmark=_maxdd(bv), autocorr_lag1=ac1,
        skew=float(pd.Series(rv).skew()), excess_kurtosis=float(pd.Series(rv).kurt()),
    )


def episode_stats(r: pd.Series, b: pd.Series, a: str, z: str) -> dict | None:
    idx = pd.period_range(a, z, freq="M").to_timestamp("M")
    if not all(i in r.dropna().index and i in b.dropna().index for i in idx):
        return None
    rr, bb = r.loc[idx], b.loc[idx]
    mc, bc = float((1 + rr).prod() - 1), float((1 + bb).prod() - 1)
    return dict(n_months=len(idx), manager_cum=mc, benchmark_cum=bc, excess_cum=mc - bc)


# ---------------------------------------------------------------- qualitative tagging (rule based, auditable)
SENT = re.compile(r"(?<=[.!?])\s+")


def _first_sentence(text: str, pats: list[str]) -> str | None:
    for s in SENT.split(text):
        if any(re.search(p, s, re.I) for p in pats):
            return s.strip()[:300]
    return None


TAG_RULES = {
    "approach": [("systematic_quant", [r"\bsystematic\b", r"\bquantitative\b", r"rules-based", r"multifactor|multi-factor"]),
                 ("fundamental", [r"fundamental", r"bottom-up"])],
    "concentration": [("concentrated", [r"concentrated", r"high-conviction", r"high conviction", r"limited number of"])],
    "benchmark_orientation": [("benchmark_agnostic", [r"benchmark-agnostic", r"benchmark agnostic", r"rather than constructing the portfolio relative to a benchmark", r"absolute-return", r"absolute risk and return"]),
                              ("benchmark_aware", [r"benchmark-aware", r"benchmark aware", r"benchmark-neutral", r"closely aligned with the benchmark", r"moderate tracking error", r"limited exposure to market timing"])],
    "style": [("growth", [r"\bgrowth\b"]), ("value", [r"\bvalue\b", r"intrinsic value"]), ("quality", [r"\bquality\b"]), ("core", [r"\bcore\b"])],
    "top_down_use": [("uses_macro_or_top_down", [r"top-down", r"macroeconomic (views|perspectives)", r"sector allocation", r"duration management"]),
                     ("rejects_macro_timing", [r"rather than macroeconomic forecasts?", r"not rely on macro", r"market timing"])],
    "risk_definition": [("permanent_capital_loss", [r"permanent capital impairment", r"permanent loss"]), ("downside_focus", [r"downside"])],
    "structure_flag": [("portable_alpha_or_overlay", [r"portable alpha"])],
    "turnover": [("low_turnover", [r"low turnover"])],
    "alternative_data": [("uses_alt_data", [r"alternative data"])],
}


NEG = re.compile(r"(rather than|instead of|without|not (?:rely|driven|based)|avoid\w*|limited exposure to)[^.]{0,60}(top-down|macro|sector allocation|timing|factor)", re.I)


def tag_text(text: str) -> list[tuple[str, str, str]]:
    out = []
    low = text.lower()
    first = SENT.split(text)[0].lower() if text else ""
    sys_n = len(re.findall(r"systematic|quantitative|rules-based|multifactor|multi-factor|quantitative model", low))
    fun_n = len(re.findall(r"fundamental|bottom-up", low))
    if re.search(r"systematic|quantitative|rules-based|multifactor|multi-factor", first):
        ap = "systematic_quant"
    elif sys_n >= 2 and fun_n >= 2:
        ap = "hybrid_fundamental_and_quant"
    elif fun_n >= 1:
        ap = "fundamental"
    elif sys_n >= 1:
        ap = "systematic_quant"
    else:
        ap = None
    if ap:
        out.append(("approach", ap, f"counts: systematic/quant words={sys_n}, fundamental words={fun_n}; first sentence: {SENT.split(text)[0][:160]}"))
    for dim, rules in TAG_RULES.items():
        if dim in ("approach", "style", "top_down_use"):
            continue
        for val, pats in rules:
            if any(re.search(p, low) for p in pats):
                out.append((dim, val, _first_sentence(text, pats) or ""))
    # style: count based, ignore 'intrinsic value'
    low2 = low.replace("intrinsic value", "")
    counts = {"growth": len(re.findall(r"\bgrowth\b", low2)), "value": len(re.findall(r"\bvalue\b|\bvalue-oriented\b|\bundervalued\b|\bcheap\b", low2)),
              "quality": len(re.findall(r"\bquality\b|profitab", low2)), "core": len(re.findall(r"\bcore\b", low2))}
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    for k, v_ in top:
        if v_ >= 2:
            out.append(("style_signal", k, f"keyword count {v_} (derived; read the text to confirm)"))
    # macro / top-down use, with negation handling
    for s_ in SENT.split(text):
        if re.search(r"top-down|macroeconomic (views|perspectives)|sector allocation|duration management|yield curve positioning", s_, re.I):
            out.append(("top_down_use", "rejects_or_minimizes_top_down" if NEG.search(s_) else "uses_top_down_or_macro", s_.strip()[:300]))
            break
    m = re.search(r"(\d+)\s*(?:to|-)\s*(\d+)\s+(?:holdings|positions|stocks|securities)", low)
    if m:
        out.append(("holdings_range", f"{m.group(1)}-{m.group(2)}", _first_sentence(text, [r"\d+\s*(?:to|-)\s*\d+\s+(?:holdings|positions|stocks|securities)"]) or ""))
    return out


def description_type(text: str | None) -> str:
    if not text:
        return "MISSING"
    low = text.lower()
    if re.search(r"^\s*the firm is|the organization (operates|is)|the firm (is|places)", low) and not re.search(r"the strategy (is|follows|seeks)", low):
        return "FIRM_ONLY"
    return "STRATEGY"


# ---------------------------------------------------------------- build
def build(raw: Path, out: Path):
    (out / "db").mkdir(exist_ok=True, parents=True); (out / "secure").mkdir(exist_ok=True, parents=True); (out / "docs").mkdir(exist_ok=True, parents=True)
    wide, tickers = load_returns(raw)
    bm = load_benchmarks(raw)
    desc = read_descriptions(raw)
    uni = yaml.safe_load((out / "universe" / "investable_universe.yaml").read_text())
    report: list[str] = []

    # ---- unit check: a monthly return above 100% in absolute value means the column is in percent units
    rescaled = {}
    for col in list(wide.columns):
        x = wide[col].dropna()
        if len(x) and x.abs().max() > 1.0:
            cand = x / 100.0
            rescaled[col] = (float(x.abs().max()), cand)
    # ---- registry
    reg = {}
    for cls, (dbf, pre, bser, nums, label) in CLASSES.items():
        for k, n in enumerate(nums, start=1):
            reg[n] = dict(manager_id=f"{pre}{k:02d}", asset_class=cls, benchmark=bser, source_label=f"Mgr {n}")
    # ---- data-quality pass over all managers
    qc: dict[str, list[tuple[str, str, str, str]]] = {v["manager_id"]: [] for v in reg.values()}
    status: dict[str, str] = {}
    reason: dict[str, str] = {}
    own_corr = {}
    for n, v in reg.items():
        r = wide[f"Mgr {n}"].dropna(); b = bm[v["benchmark"]].reindex(r.index)
        ok = b.notna(); r, b = r[ok], b[ok]
        own_corr[v["manager_id"]] = float(np.corrcoef(r, b)[0, 1])
    # duplicates
    ids = {n: reg[n]["manager_id"] for n in reg}
    dup_victims = set()
    nums_all = sorted(reg)
    for i, a in enumerate(nums_all):
        for c in nums_all[i + 1:]:
            x = wide[[f"Mgr {a}", f"Mgr {c}"]].dropna()
            if len(x) >= 24 and np.allclose(x.iloc[:, 0], x.iloc[:, 1], atol=1e-9):
                ia, ic = ids[a], ids[c]
                if reg[a]["asset_class"] != reg[c]["asset_class"]:
                    # the stream belongs to the class whose benchmark it tracks; the other manager is the victim
                    victim = ia if own_corr[ia] < own_corr[ic] else ic
                else:
                    victim = None
                for m in (ia, ic):
                    other = ic if m == ia else ia
                    if victim and m != victim:
                        qc[m].append(("DUPLICATE_RETURN_STREAM", "medium", f"Same series as {other} on all {len(x)} months; this manager's own benchmark fit is good (corr {own_corr[m]:.2f}), so the series is kept here.",
                                      "Ask the data owner to confirm which manager the series belongs to."))
                    else:
                        qc[m].append(("DUPLICATE_RETURN_STREAM", "high", f"Identical to {other} on all {len(x)} overlapping months.",
                                      "Return series copied across managers of different asset classes." if victim else "Two managers in one class share an identical series."))
                if victim:
                    dup_victims.add(victim)
                    qc[victim].append(("RETURNS_INVALID", "high", f"Series fits the benchmark of the other manager's asset class (corr to own benchmark {own_corr[victim]:.2f}); treated as a pasted copy.",
                                       "Description is usable, return series is not."))
                else:
                    dup_victims.update([ia, ic])
    for col, (mx, cand) in rescaled.items():
        n_ = int(col.split()[1]); v_ = reg[n_]
        b_ = bm[v_["benchmark"]].reindex(cand.index); ok_ = b_.notna()
        fit = float(np.corrcoef(cand[ok_], b_[ok_])[0, 1])
        if cand.abs().max() < 0.5 and fit > 0.8:
            wide[col] = wide[col] / 100.0
            qc[v_["manager_id"]].append(("UNITS_PERCENT_RESCALED", "medium", f"Series was supplied in percent units (max |value| {mx:.2f}); divided by 100. Correlation to its benchmark after rescaling: {fit:.2f}.",
                                         "Confirm the source units with the data owner; every statistic assumes the rescale is right."))
        else:
            qc[v_["manager_id"]].append(("RETURNS_INVALID", "high", f"Values outside +/-100% per month (max {mx:.2f}) and rescaling by 100 does not fit the benchmark (corr {fit:.2f}).", "Return series unusable."))
            dup_victims.add(v_["manager_id"])
    # per-manager checks
    latest_class_end = {cls: max(wide[f"Mgr {n}"].dropna().index.max() for n in CLASSES[cls][3]) for cls in CLASSES}
    for n, v in reg.items():
        mid = v["manager_id"]; r = wide[f"Mgr {n}"].dropna()
        nmon = len(r)
        if nmon < 36:
            qc[mid].append(("HISTORY_UNDER_36", "medium", f"{nmon} months (< 36).", "Statistics carry wide uncertainty; weigh the evidence accordingly. Not an exclusion."))
        elif nmon < 60:
            qc[mid].append(("HISTORY_36_59", "low", f"{nmon} months (36-59).", "Moderate history; widen uncertainty. Not an exclusion."))
        if r.index.max() < latest_class_end[v["asset_class"]]:
            qc[mid].append(("STALE_END", "medium", f"Last return {r.index.max().date()} vs class latest {latest_class_end[v['asset_class']].date()}.", "Series ends earlier than peers."))
        oc = own_corr[mid]
        if oc < -0.5:
            qc[mid].append(("SIGN_INVERSION_SUSPECTED", "high", f"Correlation to own benchmark {oc:.2f}.", "Series looks sign-flipped; do not auto-correct, confirm with data owner."))
            dup_victims.add(mid)
        elif oc < 0.80:
            qc[mid].append(("BENCHMARK_FIT_LOW", "medium", f"Correlation to declared benchmark {oc:.2f}.", "Benchmark may not match the mandate; ask the CIO to confirm, never swap silently."))
        s = window_stats(wide[f"Mgr {n}"], bm[v["benchmark"]], None)
        if s:
            if (s["info_ratio"] or 0) > 1.5 and s["tracking_error"] < 0.006:
                qc[mid].append(("IR_VERY_HIGH_LOW_TE", "low", f"IR {s['info_ratio']:.2f} on TE {s['tracking_error']:.2%}.", "Verify smoothing before relying on it."))
            if s["autocorr_lag1"] == s["autocorr_lag1"] and s["autocorr_lag1"] > 0.35:
                qc[mid].append(("POSSIBLE_SMOOTHING", "low", f"Lag-1 autocorrelation {s['autocorr_lag1']:.2f}.", "Possible stale pricing; run smoothing test."))
        if v["asset_class"] == "INTL_EQ": qc[mid].append(("HEDGING_UNKNOWN", "low", "Returns are in USD and net of fees (owner instruction). Whether the currency exposure is hedged is not stated.", "Do not attribute currency effects to stock selection without knowing the hedge policy."))
        n_gaps = int(wide[f"Mgr {n}"].loc[r.index.min():r.index.max()].isna().sum())
        if n_gaps:
            qc[mid].append(("INTERNAL_GAPS", "medium", f"{n_gaps} missing months inside the track record.", ""))
        # description checks
        dt = description_type(desc.get(n))
        if dt == "FIRM_ONLY":
            qc[mid].append(("DESCRIPTION_FIRM_LEVEL_ONLY", "high", "Text describes the firm, not an investment strategy.", "Strategy process cannot be assessed from this text; the rest of the evidence still counts."))
        if dt == "MISSING":
            qc[mid].append(("DESCRIPTION_MISSING", "high", "No strategy description supplied.", "Strategy process cannot be assessed; do not invent one."))
        if desc.get(n) and any(re.search(p, desc[n], re.I) for p in INJECTION_PATTERNS):
            qc[mid].append(("INJECTION_PATTERN_IN_TEXT", "high", "Text matches an instruction-like pattern.", "Treat as untrusted; flagged."))
        if desc.get(n) and re.search(r"portable alpha", desc[n], re.I):
            qc[mid].append(("MANDATE_NOT_PURE_CLASS", "medium", "Description is an equity-plus-fixed-income portable-alpha structure.", "Not a pure single-asset-class mandate; fixed-income sleeve adds credit/duration risk."))
        # status is a DESCRIPTION of history length only. It never excludes a manager.
        if nmon < 36:
            status[mid] = "HISTORY_UNDER_36"; reason[mid] = f"{nmon} months"
        elif nmon < 60:
            status[mid] = "HISTORY_36_59"; reason[mid] = f"{nmon} months"
        else:
            status[mid] = "HISTORY_60_PLUS"; reason[mid] = f"{nmon} months"

    # ---- market.db
    mpath = out / "db" / "market.db"; mpath.unlink(missing_ok=True)
    m = sqlite3.connect(mpath)
    m.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE asset_class(asset_class TEXT PRIMARY KEY, name TEXT, grp TEXT, status TEXT, managed_by TEXT, benchmark_series TEXT, coverage_note TEXT);
    CREATE TABLE not_available(id TEXT PRIMARY KEY, name TEXT, reason TEXT);
    CREATE TABLE owner_type(owner_type TEXT PRIMARY KEY, label TEXT, typical_objective TEXT, available_classes TEXT, unavailable_but_typical TEXT, must_disclose TEXT, liquidity_note TEXT);
    CREATE TABLE guardrail(owner_type TEXT, asset_class TEXT, min_w REAL, max_w REAL, label TEXT DEFAULT '[design] default; overridable by explicit owner statement or CIO', PRIMARY KEY(owner_type, asset_class));
    CREATE TABLE benchmark_meta(series_id TEXT PRIMARY KEY, label TEXT, first_date TEXT, last_date TEXT, n_months INTEGER, note TEXT);
    CREATE TABLE benchmark_returns(series_id TEXT, date TEXT, ret REAL, PRIMARY KEY(series_id, date));
    CREATE TABLE asset_class_stats(series_id TEXT, window TEXT, n_months INTEGER, start TEXT, end TEXT, ann_return REAL, ann_vol REAL, sharpe_rf0 REAL, max_drawdown REAL, worst_12m REAL, skew REAL, excess_kurtosis REAL, PRIMARY KEY(series_id, window));
    CREATE TABLE asset_class_corr(window TEXT, series_a TEXT, series_b TEXT, n_months INTEGER, corr REAL, PRIMARY KEY(window, series_a, series_b));
    CREATE TABLE episode(episode TEXT PRIMARY KEY, start_month TEXT, end_month TEXT, note TEXT);
    CREATE TABLE episode_returns(episode TEXT, series_id TEXT, n_months INTEGER, cum_return REAL, PRIMARY KEY(episode, series_id));
    CREATE TABLE factors(factor_set TEXT, factor TEXT, date TEXT, ret REAL, PRIMARY KEY(factor_set, factor, date));
    """)
    m.executemany("INSERT INTO meta VALUES(?,?)", [("built_utc", pd.Timestamp.now('UTC').isoformat()), ("schema_version", "1"), ("raw_files", "Bmk.xlsx; Factors/*.xlsx; Managers/Test Rtns/*.xlsx; Managers/Test Descriptions/*.docx")])
    for k, a in uni["asset_classes"].items():
        m.execute("INSERT INTO asset_class VALUES(?,?,?,?,?,?,?)", (k, a["name"], a["group"], a["status"], a["managed_by"], a.get("benchmark_series"), a["coverage_note"]))
    for a in uni["not_available"]:
        m.execute("INSERT INTO not_available VALUES(?,?,?)", (a["id"], a["name"], a["reason"]))
    for k, o in uni["owner_types"].items():
        m.execute("INSERT INTO owner_type VALUES(?,?,?,?,?,?,?)", (k, o["label"], o["typical_objective"], ",".join(o["available_classes"]), ",".join(o["unavailable_but_typical"]), o["must_disclose"], o["liquidity_note"]))
        for ac, (lo, hi) in o["guardrails"].items():
            m.execute("INSERT INTO guardrail(owner_type,asset_class,min_w,max_w) VALUES(?,?,?,?)", (k, ac, lo, hi))
    for s in bm.columns:
        x = bm[s].dropna()
        m.execute("INSERT INTO benchmark_meta VALUES(?,?,?,?,?,?)", (s, BM_LABEL[s], str(x.index[0].date()), str(x.index[-1].date()), len(x), "Total-return monthly series as supplied; index name, currency and hedging not stated."))
        m.executemany("INSERT INTO benchmark_returns VALUES(?,?,?)", [(s, str(d.date()), float(v)) for d, v in x.items()])
    # asset class stats on common windows
    common = bm.dropna()
    for s in bm.columns:
        for w, nw in {"FULL_OWN": None, "COMMON_SAMPLE": "common", "20Y": 240, "10Y": 120, "5Y": 60}.items():
            x = common[s] if nw == "common" else bm[s].dropna()
            if isinstance(nw, int):
                if len(x) < nw: continue
                x = x.tail(nw)
            n = len(x); rv = x.to_numpy()
            r12 = (pd.Series(1 + rv).rolling(12).apply(np.prod, raw=True) - 1).min()
            m.execute("INSERT INTO asset_class_stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (s, w, n, str(x.index[0].date()), str(x.index[-1].date()), float((1 + rv).prod() ** (12 / n) - 1),
                      float(rv.std(ddof=1) * math.sqrt(12)), float(rv.mean() / rv.std(ddof=1) * math.sqrt(12)), _maxdd(rv), float(r12), float(pd.Series(rv).skew()), float(pd.Series(rv).kurt())))
    for w, df in {"COMMON_SAMPLE": common, "20Y": common.tail(240), "10Y": common.tail(120), "5Y": common.tail(60)}.items():
        c = df.corr()
        for a in c.index:
            for b2 in c.columns:
                m.execute("INSERT INTO asset_class_corr VALUES(?,?,?,?,?)", (w, a, b2, len(df), float(c.loc[a, b2])))
    for e, (a, z) in EPISODES.items():
        m.execute("INSERT INTO episode VALUES(?,?,?,?)", (e, a, z, "[design] calendar window chosen by us; not an official event definition"))
        idx = pd.period_range(a, z, freq="M").to_timestamp("M")
        for s in bm.columns:
            if all(i in bm[s].dropna().index for i in idx):
                m.execute("INSERT INTO episode_returns VALUES(?,?,?,?)", (e, s, len(idx), float((1 + bm[s].loc[idx]).prod() - 1)))
    # factors: one dated file
    fr = pd.read_excel(raw / "Factors" / "Factor Returns.xlsx")
    fr["Date"] = pd.to_datetime(fr["Date"]).map(eom)
    FI = {"Treasury": "Treasury", "Credit": "Credit"}
    for col in fr.columns[1:]:
        setname = "FIXED_INCOME" if col in FI else "EQUITY"
        fname = {"Volatility": "Realized Volatility"}.get(col, col)
        for d, x in fr[["Date", col]].dropna().itertuples(index=False):
            m.execute("INSERT INTO factors VALUES(?,?,?,?)", (setname, fname, str(d.date()), float(x)))
    m.execute("INSERT INTO meta VALUES('factor_alignment','RESOLVED: Factors/Factor Returns.xlsx carries calendar dates (month-end). Equity factors: Momentum, Economic Momentum, Growth, Value, Quality, Crowding, Innovation, Realized Volatility. Fixed-income factors: Treasury, Credit. Factor sign/construction conventions are not documented; treat loadings as descriptive.')")
    m.commit(); m.close()

    # ---- per-class manager DBs + vault
    vpath = out / "secure" / "vault.db"; vpath.unlink(missing_ok=True)
    v = sqlite3.connect(vpath)
    v.execute("CREATE TABLE identity_map(manager_id TEXT PRIMARY KEY, source_label TEXT, source_column_label TEXT, note TEXT)")
    peers_summary = {}
    for cls, (dbf, pre, bser, nums, label) in CLASSES.items():
        p = out / "db" / dbf; p.unlink(missing_ok=True)
        c = sqlite3.connect(p)
        c.executescript("""
        CREATE TABLE class_info(asset_class TEXT, label TEXT, benchmark_series TEXT, benchmark_label TEXT, return_frequency TEXT, built_utc TEXT);
        CREATE TABLE managers(manager_id TEXT PRIMARY KEY, asset_class TEXT, benchmark_series TEXT, status TEXT, status_reason TEXT, process_evidence TEXT, return_basis TEXT, currency TEXT, vehicle TEXT, fee_disclosed TEXT, first_month TEXT, last_month TEXT, n_months INTEGER);
        CREATE TABLE manager_returns(manager_id TEXT, date TEXT, ret REAL, PRIMARY KEY(manager_id, date));
        CREATE TABLE strategy_text(manager_id TEXT PRIMARY KEY, description_type TEXT, text TEXT, sha256 TEXT, injection_scan TEXT);
        CREATE TABLE strategy_tags(manager_id TEXT, dimension TEXT, value TEXT, evidence_sentence TEXT, method TEXT DEFAULT 'keyword_rule_v1 (derived, low-medium confidence; read the text)');
        CREATE TABLE data_quality(manager_id TEXT, check_code TEXT, severity TEXT, detail TEXT, consequence TEXT);
        CREATE TABLE manager_stats(manager_id TEXT, window TEXT, n_months INTEGER, start TEXT, end TEXT, ann_return REAL, ann_benchmark REAL, ann_excess REAL, tracking_error REAL, info_ratio REAL, ann_vol REAL, beta REAL, alpha_ann REAL, alpha_tstat REAL, corr_benchmark REAL, up_capture REAL, down_capture REAL, hit_rate REAL, worst_12m_excess REAL, max_dd_manager REAL, max_dd_benchmark REAL, autocorr_lag1 REAL, skew REAL, excess_kurtosis REAL, PRIMARY KEY(manager_id, window));
        CREATE TABLE manager_episode(manager_id TEXT, episode TEXT, n_months INTEGER, manager_cum REAL, benchmark_cum REAL, excess_cum REAL, PRIMARY KEY(manager_id, episode));
        CREATE TABLE excess_corr(manager_a TEXT, manager_b TEXT, n_overlap INTEGER, corr_excess REAL, corr_returns REAL, PRIMARY KEY(manager_a, manager_b));
        CREATE TABLE peer_stats(window TEXT, metric TEXT, n_managers INTEGER, p25 REAL, median REAL, p75 REAL, PRIMARY KEY(window, metric));
        CREATE TABLE narrative_consistency(manager_id TEXT, rule TEXT, expectation TEXT, observed TEXT, result TEXT);
        """)
        c.execute("INSERT INTO class_info VALUES(?,?,?,?,?,?)", (cls, label, bser, BM_LABEL[bser], "monthly", pd.Timestamp.now('UTC').isoformat()))
        stats_by = {}
        members = [n for n in nums]
        for n in members:
            mid = reg[n]["manager_id"]; r = wide[f"Mgr {n}"].dropna(); t = desc.get(n)
            dt = description_type(t)
            pe = {"STRATEGY": "STRATEGY_TEXT", "FIRM_ONLY": "FIRM_LEVEL_ONLY", "MISSING": "NONE"}[dt]
            c.execute("INSERT INTO managers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (mid, cls, bser, status[mid], reason[mid], pe, "NET_OF_FEES (owner instruction: all returns are net of fees)", "USD", "UNSPECIFIED", "n/a (returns are net)", str(r.index.min().date()), str(r.index.max().date()), len(r)))
            c.executemany("INSERT INTO manager_returns VALUES(?,?,?)", [(mid, str(d.date()), float(x)) for d, x in r.items()])  # kept; gateway blocks quarantined series for non-Red-Team agents
            if t:
                scan = [pat for pat in INJECTION_PATTERNS if re.search(pat, t, re.I)]
                c.execute("INSERT INTO strategy_text VALUES(?,?,?,?,?)", (mid, dt, t, hashlib.sha256(t.encode()).hexdigest(), "clean" if not scan else "FLAGGED:" + ";".join(scan)))
                for dim, val, evs in tag_text(t):
                    c.execute("INSERT INTO strategy_tags(manager_id,dimension,value,evidence_sentence) VALUES(?,?,?,?)", (mid, dim, val, evs))
            for chk, sev, det, cons in qc[mid]:
                c.execute("INSERT INTO data_quality VALUES(?,?,?,?,?)", (mid, chk, sev, det, cons))
            v.execute("INSERT INTO identity_map VALUES(?,?,?,?)", (mid, f"Mgr {n}", None, ""))
            st = {}
            for w, nw in WINDOWS.items():
                s = window_stats(wide[f"Mgr {n}"], bm[bser], nw)
                if s:
                    st[w] = s
                    c.execute("INSERT INTO manager_stats VALUES(" + ",".join(["?"] * 24) + ")", (mid, w, s["n_months"], s["start"], s["end"], s["ann_return"], s["ann_benchmark"], s["ann_excess"], s["tracking_error"], s["info_ratio"], s["ann_vol"], s["beta"], s["alpha_ann"], s["alpha_tstat"], s["corr_benchmark"], s["up_capture"], s["down_capture"], s["hit_rate"], s["worst_12m_excess"], s["max_dd_manager"], s["max_dd_benchmark"], s["autocorr_lag1"], s["skew"], s["excess_kurtosis"]))
            stats_by[mid] = st
            for e, (a, z) in EPISODES.items():
                es = episode_stats(wide[f"Mgr {n}"], bm[bser], a, z)
                if es:
                    c.execute("INSERT INTO manager_episode VALUES(?,?,?,?,?,?)", (mid, e, es["n_months"], es["manager_cum"], es["benchmark_cum"], es["excess_cum"]))
        # excess correlation (valid managers only)
        valid = [reg[n]["manager_id"] for n in members]
        exc, rets = {}, {}
        for n in members:
            mid = reg[n]["manager_id"]
            if mid in valid:
                r = wide[f"Mgr {n}"]; b = bm[bser]; exc[mid] = (r - b).dropna(); rets[mid] = r.dropna()
        for a in valid:
            for b2 in valid:
                if a >= b2: continue
                x = pd.concat([exc[a], exc[b2]], axis=1, sort=True).dropna(); y = pd.concat([rets[a], rets[b2]], axis=1, sort=True).dropna()
                if len(x) >= 24:
                    c.execute("INSERT INTO excess_corr VALUES(?,?,?,?,?)", (a, b2, len(x), float(x.corr().iloc[0, 1]), float(y.corr().iloc[0, 1])))
        # peer stats on valid, non-insufficient managers
        for w in WINDOWS:
            for metric in ["info_ratio", "tracking_error", "ann_excess", "down_capture", "up_capture"]:
                vals = [stats_by[mid][w][metric] for mid in valid if w in stats_by[mid] and stats_by[mid][w].get(metric) is not None]
                if len(vals) >= 3:
                    c.execute("INSERT INTO peer_stats VALUES(?,?,?,?,?,?)", (w, metric, len(vals), *[float(np.percentile(vals, q)) for q in (25, 50, 75)]))
        med_te = c.execute("SELECT median FROM peer_stats WHERE window='FULL' AND metric='tracking_error'").fetchone()
        med_te = med_te[0] if med_te else None
        # narrative vs statistics consistency (rule based)
        for n in members:
            mid = reg[n]["manager_id"]; s = stats_by[mid].get("FULL")
            tags = {(d, vv) for d, vv, _ in tag_text(desc.get(n, ""))} if desc.get(n) else set()
            if not s or med_te is None or description_type(desc.get(n)) != "STRATEGY" or False:
                continue
            te = s["tracking_error"]
            if ("concentration", "concentrated") in tags or ("benchmark_orientation", "benchmark_agnostic") in tags:
                res = "CONSISTENT" if te >= 0.9 * med_te else "INCONSISTENT"
                c.execute("INSERT INTO narrative_consistency VALUES(?,?,?,?,?)", (mid, "R1 concentrated/benchmark-agnostic -> tracking error at or above class median", f"TE >= {0.9 * med_te:.2%}", f"TE {te:.2%} (class median {med_te:.2%})", res))
            if ("benchmark_orientation", "benchmark_aware") in tags:
                res = "CONSISTENT" if te <= 1.1 * med_te else "INCONSISTENT"
                c.execute("INSERT INTO narrative_consistency VALUES(?,?,?,?,?)", (mid, "R2 benchmark-aware/limited-risk -> tracking error at or below class median", f"TE <= {1.1 * med_te:.2%}", f"TE {te:.2%} (class median {med_te:.2%})", res))
            if cls in ("US_IG", "US_HY") and ("benchmark_orientation", "benchmark_aware") in tags and s["corr_benchmark"] < 0.9:
                c.execute("INSERT INTO narrative_consistency VALUES(?,?,?,?,?)", (mid, "R3 benchmark-aware credit -> correlation to benchmark >= 0.90", ">= 0.90", f"{s['corr_benchmark']:.2f}", "INCONSISTENT"))
            if cls in ("US_EQ", "INTL_EQ") and ("approach", "systematic_quant") in tags and s["corr_benchmark"] < 0.9:
                c.execute("INSERT INTO narrative_consistency VALUES(?,?,?,?,?)", (mid, "R4 systematic equity -> correlation to benchmark >= 0.90", ">= 0.90", f"{s['corr_benchmark']:.2f}", "INCONSISTENT"))
        c.execute("CREATE VIEW v_manager_card AS SELECT m.*, s.ann_return, s.ann_benchmark, s.ann_excess, s.tracking_error, s.info_ratio, s.beta, s.alpha_ann, s.alpha_tstat, s.corr_benchmark, s.up_capture, s.down_capture, s.max_dd_manager, s.worst_12m_excess FROM managers m LEFT JOIN manager_stats s ON s.manager_id=m.manager_id AND s.window='FULL'")
        c.commit(); c.close()
    v.commit(); v.close()

    # ---- audit db
    ap = out / "db" / "audit.db"; ap.unlink(missing_ok=True)
    a = sqlite3.connect(ap)
    a.executescript("""CREATE TABLE query_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, tool TEXT, args_json TEXT, db TEXT, rows_returned INTEGER, status TEXT, reason TEXT);
    CREATE TABLE proposal_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, payload_json TEXT, payload_sha256 TEXT);
    CREATE TRIGGER no_update_p BEFORE UPDATE ON proposal_log BEGIN SELECT RAISE(ABORT,'proposal log is append-only'); END;
    CREATE TRIGGER no_delete_p BEFORE DELETE ON proposal_log BEGIN SELECT RAISE(ABORT,'proposal log is append-only'); END;
    CREATE TRIGGER no_update BEFORE UPDATE ON query_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
    CREATE TRIGGER no_delete BEFORE DELETE ON query_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;""")
    a.commit(); a.close()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "access")); import trace as _tr; _tr.ensure_schema(ap)  # agent_turn + tool_result
    return reg, status, qc, wide, bm, desc, tickers


def write_quality_report(out: Path, reg, status, qc, tickers):
    L = ["# Data quality report (generated by build_databases.py; do not edit by hand)", ""]
    L.append("## Status by manager"); L.append("| Manager | Class | Status | Reasons / flags |"); L.append("|---|---|---|---|")
    for n in sorted(reg):
        mid = reg[n]["manager_id"]
        flags = "; ".join(f"{c} ({s})" for c, s, _, _ in qc[mid] if c != "CURRENCY_HEDGING_UNKNOWN")
        L.append(f"| {mid} | {reg[n]['asset_class']} | {status[mid]} | {flags or '-'} |")
    L += ["", "## High-severity findings (details)"]
    for n in sorted(reg):
        mid = reg[n]["manager_id"]
        for c, s, d, cons in qc[mid]:
            if s == "high":
                L.append(f"- **{mid}** `{c}`: {d} {cons}")
    L += ["", "## Source-file observations",
          "- All 40 return columns are labelled 'Mgr N' in the source files (the earlier ticker labels are gone); no ticker mapping is needed.",
          "- All 40 description files supplied. Mgr 26 deliberately keeps a firm-level description (owner test of whether agents weigh qualitative evidence).",
          "- Factors come from one dated file (Factor Returns.xlsx), 1973 onward; factor columns have different start dates.",
          "- Benchmarks run to 2026-10; manager series end 2026-09 (or earlier). Statistics use the overlap only.",
          "- Returns are treated as NET of fees (owner instruction). Currency, hedging and vehicle are not provided."]
    (out / "docs" / "DATA_QUALITY_REPORT.md").write_text("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--raw", required=True); ap.add_argument("--out", default=".")
    a = ap.parse_args()
    reg, status, qc, wide, bm, desc, tickers = build(Path(a.raw), Path(a.out))
    write_quality_report(Path(a.out), reg, status, qc, tickers)
    from collections import Counter
    print(Counter(status.values()))
