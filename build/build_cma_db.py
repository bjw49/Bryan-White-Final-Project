#!/usr/bin/env python3
"""Build db/cma.db: capital-market-assumption estimates extracted from the 7 house PDFs.

Every row carries the PDF page and the exact quoted row text; the build FAILS if a quote cannot be found on its page.
Usage: python3 build/build_cma_db.py --raw "<Final Project folder>" --out .

Conventions (all percentages in the sources, stored as decimals):
  exp_geo   stated 10-year geometric (compound) return, if stated
  exp_arith arithmetic expected return: STATED by the house, else DERIVED = geo + vol^2/2 (Nuveen, convention unstated: geo + vol^2/4, as decided earlier)
  vol       STATED, else filled with the cross-house mean for the same class and flagged (Northern Trust publishes no volatility)
"""
from __future__ import annotations

import argparse, re, sqlite3, subprocess
from pathlib import Path
from statistics import mean, median

import pandas as pd

HOUSES = {
    "KKR": dict(file="kkr-capital-market-assumptions-july-2026.pdf", doc="KKR Capital Market Assumptions, July 2026", as_of="2026-04-30",
                conv="geometric (p.2: all returns compounded annually); 10-year column used", note="USD; reported volatility column used"),
    "Invesco": dict(file="invesco-capital-market-assumption-usd.pdf", doc="Invesco 2026 CMA USD Q3 update", as_of="2026-06-30",
                    conv="geometric and arithmetic both stated (10-year)", note="USD"),
    "Amundi": dict(file="2026.08-CMA_H2 2026 Update.pdf", doc="Amundi Capital Market Assumptions H2 update, Aug 2026", as_of="2026-08-01",
                   conv="geometric and arithmetic both stated (10-year)", note="LOCAL-CURRENCY indices"),
    "PGIM": dict(file="PGIM 10Y Capital Market Assumptions.pdf", doc="PGIM 10-Year CMAs, Q4 2026 (as of 30 Sep 2026)", as_of="2026-09-30",
                 conv="geometric and arithmetic both stated; gross of fees (index-level)", note="international equity unhedged"),
    "Northern Trust": dict(file="2026-capital-market-assumptions-report.pdf", doc="Northern Trust 10-Year Outlook, 2026 Edition (Dec 2025)", as_of="2025-12-01",
                           conv="geometric (annual returns, geometric basis); no volatility published", note="2026 CMA-year column used, not the historical 10-year actual column"),
    "BNY": dict(file="endurance-under-pressure-capital-market-assumptions-2026-full.pdf", doc="BNY Investments CMAs: Endurance Under Pressure 2026 (data as of 31 Dec 2025)", as_of="2025-12-31",
                conv="geometric (framework basis, p.45); 2026 column used", note="no US IG corporate or Treasury row published"),
    "Nuveen": dict(file="Target Date Multi-Asset Team Capital market assumptions.pdf", doc="Nuveen Target Date Multi-Asset Team CMAs (as of 30 Sep 2026)", as_of="2026-09-30",
                   conv="NOT STATED ('10-year estimated total return'); treated as geometric with +vol^2/4", note="benchmarks: US equity Russell 3000; international equity MSCI EAFE"),
}

# (house, cma_class, index_label, geo%, arith% or None, vol% or None, quote on page, relation, flags)
R = []
def add(h, c, label, geo, arith, vol, quote, rel="DIRECT", flags=""):
    R.append((h, c, label, geo, arith, vol, quote, rel, flags))

# ---- US equity
add("KKR", "US_EQUITY", "U.S. Large Cap Equity", 5.7, None, 15.6, "U.S. Large Cap Equity 6.2% 5.7% 6.4% 1.4% 15.6%")
add("Invesco", "US_EQUITY", "S&P 500 (US large cap)", 5.5, 6.8, 17.0, "US large cap S&P 500 5.5 6.8 3.1 17.0")
add("Amundi", "US_EQUITY", "US Equity (NDDLUS, local currency)", 7.0, 8.0, 15.8, "US Equity NDDLUS Index 7.3% 7.0% 8.0% 15.8%")
add("PGIM", "US_EQUITY", "U.S. Equities", 5.95, 7.10, 15.15, "U.S. Equities 5.95 7.10 15.15")
add("Northern Trust", "US_EQUITY", "MSCI USA", 6.8, None, None, "U.S. MSCI USA 6.8 7.5 6.3 6.0 4.3 4.7 15.3")
add("BNY", "US_EQUITY", "U.S. Equity", 7.6, None, 15.9, "U.S. Equity 7.6% 15.9% 7.5% 16.2%")
add("Nuveen", "US_EQUITY", "US Equity (Russell 3000)", 7.70, None, 16.03, "US Equity 7.70 16.03")
# ---- International developed equity (ex-US)
add("PGIM", "INTL_EQUITY", "Developed International ex-U.S. Equities (unhedged)", 6.79, 8.07, 16.00, "Developed International ex-U.S. Equities Unhedged 6.79 8.07 16.00")
add("BNY", "INTL_EQUITY", "Developed Ex-U.S.", 7.8, None, 15.9, "Developed Ex-U.S. 7.8% 15.9% 6.7% 16.3%")
add("Nuveen", "INTL_EQUITY", "International Equity (MSCI EAFE)", 7.78, None, 16.73, "International Equity 7.78 16.73")
add("Invesco", "INTL_EQUITY", "MSCI ACWI ex-US (includes emerging markets)", 7.4, 8.8, 17.7, "Global ex-US equity MSCI ACWI ex-US 7.4 8.8 4.0 17.7",
    rel="NEAR", flags="INCLUDES_EMERGING_MARKETS; excluded from the class summary")
# regional components (no single developed ex-US row published by these houses)
add("KKR", "EUROPE_EQUITY", "Europe Equity", 6.8, None, 18.5, "Europe Equity 7.1% 6.8% 7.0% 3.5% 18.5%", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
add("KKR", "JAPAN_EQUITY", "Japan Equity", 7.2, None, 15.7, "Japan Equity 7.3% 7.2% 7.2% 2.5% 15.7%", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
add("Amundi", "EUROPE_EQUITY", "Europe Equity (NDDLE15, local currency)", 6.9, 7.9, 16.2, "Europe Equity NDDLE15 index 7.9% 6.9% 7.9% 16.2%", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
add("Amundi", "JAPAN_EQUITY", "Japan Equity (NDDLJN, local currency)", 6.2, 7.7, 18.5, "Japan Equity NDDLJN Index 7.8% 6.2% 7.7% 18.5%", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
add("Amundi", "PACIFIC_EXJP_EQUITY", "Pacific ex Japan Equity (NDDLPXJ, local currency)", 7.0, 7.8, 14.7, "Pacific ex Japan Equity NDDLPXJ Index 7.8% 7.0% 7.8% 14.7%", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
add("Northern Trust", "JAPAN_EQUITY", "MSCI Japan", 7.3, None, None, "Japan MSCI Japan 7.3 6.0 6.0 6.3 4.1 3.8 10.9", rel="COMPONENT", flags="component of developed ex-US; not a class estimate")
# ---- US investment-grade corporate
add("KKR", "US_IG_CORP", "U.S. Corp Bonds", 4.9, None, 6.6, "U.S. Corp Bonds 5.0% 4.9% 4.9% 4.9% 6.6%")
add("Invesco", "US_IG_CORP", "BBG US IG corporate", 5.4, 5.6, 5.5, "US IG corp BBG US IG 5.4 5.6 5.5 5.5")
add("Amundi", "US_IG_CORP", "US Corporate IG (C0A0, local currency)", 5.2, 5.3, 6.8, "US Corporate IG C0A0 index 6.3 4.5% 5.2% 5.3% 6.8%")
add("PGIM", "US_IG_CORP", "U.S. Investment Grade Bonds", 6.47, 6.68, 6.56, "U.S. Investment Grade Bonds 6.47 6.68 6.56")
add("Northern Trust", "US_IG_CORP", "Bloomberg U.S. Corporate", 5.1, None, None, "Corporates Bloomberg U.S. Corporate 5.1 5.2 5.5 4.7 2.6 2.7 3.1")
add("Nuveen", "US_IG_CORP", "Corporate IG Bond (Bloomberg IG Corporate)", 6.34, None, 6.86, "Corporate IG Bond 6.34 6.86")
# ---- US high yield
add("KKR", "US_HY", "U.S. High Yield", 5.9, None, 8.7, "U.S. High Yield 6.0% 5.9% 5.8% 7.1% 8.7%")
add("Invesco", "US_HY", "BBG US HY corporate", 5.9, 6.0, 4.0, "US HY corps BBG US HY 5.9 6.0 7.9 4.0",
    flags="VOL_LOW_OUTLIER: published risk of 4.0% is below this house's own IG corporate risk (5.5%); verify before relying on the volatility")
add("Amundi", "US_HY", "US Corporate HY (H0A0, local currency)", 5.1, 5.5, 10.3, "US Corporate HY H0A0 index 2.9 4.4% 5.1% 5.5% 10.3%")
add("PGIM", "US_HY", "U.S. High Yield Bonds", 6.43, 6.76, 8.14, "U.S. High Yield Bonds 6.43 6.76 8.14")
add("Northern Trust", "US_HY", "Bloomberg U.S. High Yield", 5.5, None, None, "High Yield Bloomberg U.S. High Yield 5.5 5.6 6.7 7.4 3.5 5.5 6.2")
add("BNY", "US_HY", "U.S. High Yield", 6.1, None, 7.9, "U.S. High Yield 6.1% 7.9% 6.0% 8.4%")
add("Nuveen", "US_HY", "Corporate HY Bond (Bloomberg US HY Corporate)", 6.95, None, 8.93, "Corporate HY Bond 6.95 8.93")
add("Nuveen", "US_HY_BB_B", "High Yield (BB-B) Bond (ICE BofA BB-B constrained)", 6.96, None, 8.56, "High Yield (BB-B) Bond 6.96 8.56", rel="ALTERNATE", flags="BB-B subset; not used in class summary")
# ---- US government (reference)
add("KKR", "US_GOVT", "U.S. Gov't Bonds", 4.4, None, 5.2, "U.S. Gov't Bonds 4.7% 4.4% 4.3% 3.9% 5.2%", rel="REFERENCE", flags="not an investable class here")
add("Invesco", "US_GOVT", "BBG US Treasury", 5.0, 5.1, 4.5, "US tsy BBG US tsy 5.0 5.1 4.2 4.5", rel="REFERENCE", flags="not an investable class here")
add("Amundi", "US_GOVT", "US Bond (JPMTUS, local currency)", 4.6, 4.7, 5.3, "US Bond JPMTUS Index 5.6 4.2% 4.6% 4.7% 5.3%", rel="REFERENCE", flags="not an investable class here")
add("PGIM", "US_GOVT", "U.S. Treasury Bonds", 5.62, 5.73, 4.67, "U.S. Treasury Bonds 5.62 5.73 4.67", rel="REFERENCE", flags="not an investable class here")
add("Northern Trust", "US_GOVT", "Bloomberg U.S. Treasury", 4.6, None, None, "Government Bloomberg U.S. Treasury 4.6 4.3 4.1 3.1 2.2 1.9 1.2", rel="REFERENCE", flags="not an investable class here")
# ---- global equity, aggregate, cash: carried over from the earlier extraction (cma.csv), re-verified by quote
add("KKR", "GLOBAL_EQUITY", "Global Equity (MSCI ACWI)", 6.1, None, 15.8, "Global Equity 6.7% 6.1% 6.7% 2.0% 15.8%", rel="REFERENCE")
add("Invesco", "GLOBAL_EQUITY", "MSCI ACWI", 6.4, 7.6, 16.5, "Global equity MSCI ACWI 6.4 7.6 3.4 16.5", rel="REFERENCE")
add("Amundi", "GLOBAL_EQUITY", "AC World Equity (NDLEACWF, local currency)", 7.0, 7.9, 15.2, "AC World Equity NDLEACWF Index 7.8% 7.0% 7.9% 15.2%", rel="REFERENCE")
add("PGIM", "GLOBAL_EQUITY", "Global Equities Unhedged", 6.25, 7.39, 15.11, "Global Equities Unhedged 6.25 7.39 15.11", rel="REFERENCE")
add("Northern Trust", "GLOBAL_EQUITY", "MSCI AC World", 6.9, None, None, "Global Equities MSCI AC World 6.9 7.1 6.3 6.1 4.6 4.9 12.7", rel="REFERENCE")
add("KKR", "US_AGG", "U.S. Aggregate", 4.5, None, 5.0, "U.S. Aggregate 4.8% 4.5% 4.5% 4.2% 5.0%", rel="REFERENCE")
add("Invesco", "US_AGG", "BBG US Aggregate", 5.2, 5.4, 5.0, "US agg BBG US agg 5.2 5.4 4.7 5.0", rel="REFERENCE")
add("Amundi", "US_AGG", "Barclays US Aggregate", 4.8, 4.9, 5.1, "Barclays US Aggregate LBUSTRUU Index 5.8 4.3% 4.8% 4.9% 5.1%", rel="REFERENCE")
add("PGIM", "US_AGG", "U.S. Aggregate Bonds", 5.95, 6.10, 5.42, "U.S. Aggregate Bonds 5.95 6.10 5.42", rel="REFERENCE")
add("BNY", "US_AGG", "U.S. Aggregate", 4.3, None, 5.2, "U.S. Aggregate 4.3% 5.2% 4.8% 5.4%", rel="REFERENCE")
add("Northern Trust", "US_AGG", "Bloomberg U.S. Aggregate", 5.0, None, None, "Investment Grade Bloomberg U.S. Aggregate 5.0 4.7 4.7 3.7 2.4 2.3 1.8", rel="REFERENCE")
add("Nuveen", "US_AGG", "US Bond (Bloomberg Agg)", 5.93, None, 5.17, "US Bond (Bloomberg Agg) 5.93 5.17", rel="REFERENCE")
add("KKR", "CASH", "Cash (USD)", 3.4, None, 1.1, "Cash (USD) 3.4% 3.4% 3.4% 3.4% 1.1%")
add("Amundi", "CASH", "US Cash (JPCAUS3M)", 3.3, 3.2, 1.2, "US Cash JPCAUS3M Index 0.2 3.2% 3.3% 3.2% 1.2%", flags="stated arithmetic (3.2) is below stated geometric (3.3): table quirk; geometric treated as the anchor")
add("PGIM", "CASH", "Cash", 3.03, None, None, "Cash 3.03 -- -- --")
add("Northern Trust", "CASH", "3-Month U.S. T-Bill", 3.3, None, None, "Cash 3-Month U.S. T-Bill 3.3 3.3 2.9 2.8 0.3 0.1 2.1")
add("Nuveen", "CASH", "Cash (T-bill)", 3.54, None, 0.26, "Cash 3.54 0.26")

# private credit: out of scope for this system, kept for the 'not available' disclosure only
add("KKR", "PRIVATE_CREDIT", "Direct Lending", 6.9, None, 7.7, "Direct Lending 6.6% 6.9% 7.0% 8.2%", rel="NOT_INVESTABLE", flags="private credit is not an available asset class")
add("PGIM", "PRIVATE_CREDIT", "U.S. Private Debt", 7.63, 8.06, 9.28, "U.S. Private Placement (HY) 7.97 8.53 10.60", rel="NOT_INVESTABLE", flags="private credit is not an available asset class; row shown is private placement (HY)")

# numeric correlation matrices published as text: (house, a, b, corr, quote)
CORR = [
    ("KKR", "GLOBAL_EQUITY", "US_GOVT", 0.18, "U.S. Gov’t Bonds 0.18 1.00"),
    ("KKR", "GLOBAL_EQUITY", "US_IG_CORP", 0.67, "U.S. Corp Bonds 0.67 0.78 1.00"),
    ("KKR", "GLOBAL_EQUITY", "US_AGG", 0.43, "U.S. Aggregate 0.43 0.95 0.93 1.00"),
    ("KKR", "US_IG_CORP", "US_AGG", 0.93, "U.S. Aggregate 0.43 0.95 0.93 1.00"),
    ("Nuveen", "US_EQUITY", "INTL_EQUITY", 0.85, "International Equity -0.02 -0.03 -0.03 -0.07 0.22 0.26 0.20 0.54 0.76 0.76 0.34 0.34 0.58 0.20 0.70 0.85 1.00"),
    ("Nuveen", "US_EQUITY", "US_IG_CORP", 0.47, "US Equity -0.01 -0.11 -0.08 -0.09 0.12 0.18 0.18 0.47 0.77 0.77"),
    ("Nuveen", "US_EQUITY", "US_HY", 0.77, "US Equity -0.01 -0.11 -0.08 -0.09 0.12 0.18 0.18 0.47 0.77 0.77"),
    ("Nuveen", "INTL_EQUITY", "US_IG_CORP", 0.54, "International Equity -0.02 -0.03 -0.03 -0.07 0.22 0.26 0.20 0.54 0.76 0.76"),
    ("Nuveen", "INTL_EQUITY", "US_HY", 0.76, "International Equity -0.02 -0.03 -0.03 -0.07 0.22 0.26 0.20 0.54 0.76 0.76"),
    ("Nuveen", "US_IG_CORP", "US_HY", 0.67, "Corporate HY Bond -0.02 -0.04 -0.01 -0.06 0.27 0.33 0.25 0.67 0.98 1.00"),
    ("Nuveen", "US_EQUITY", "US_AGG", 0.18, "US Equity -0.01 -0.11 -0.08 -0.09 0.12 0.18 0.18 0.47 0.77 0.77"),
]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).strip()


def load_pages(pdf: Path) -> list[str]:
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout).group(1))
    return [norm(subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), "-raw", str(pdf), "-"], capture_output=True, text=True).stdout) for p in range(1, n + 1)]


def build(raw: Path, out: Path):
    cdir = raw / "Capital Market Assumptions"
    pages = {h: load_pages(cdir / v["file"]) for h, v in HOUSES.items()}

    def page_of(h, quote):
        q = norm(quote)
        hits = [i + 1 for i, t in enumerate(pages[h]) if q in t]
        if not hits:
            raise SystemExit(f"QUOTE NOT FOUND ({h}): {quote}")
        return hits[0]

    rows = []
    for h, c, label, geo, arith, vol, quote, rel, flags in R:
        rows.append(dict(house=h, cma_class=c, index_label=label, stated_geo=geo / 100, stated_arith=None if arith is None else arith / 100,
                         stated_vol=None if vol is None else vol / 100, relation=rel, flags=flags, quote=norm(quote), pdf_page=page_of(h, quote)))
    df = pd.DataFrame(rows)
    # cross-house mean vol per class (used only where a house publishes none)
    vm = df[df.relation.isin(["DIRECT"])].groupby("cma_class").stated_vol.mean().to_dict()
    ex_arith, ex_src, vol_f, vol_src, fl = [], [], [], [], []
    for _, r in df.iterrows():
        v = r.stated_vol; vs = "STATED"
        f = r["flags"]
        if pd.isna(v):
            v = vm.get(r.cma_class)
            vs = "CROSS_HOUSE_MEAN"
            f = (f + "; " if f else "") + "VOL_FILLED_FROM_CROSS_HOUSE_MEAN"
        if pd.notna(r.stated_arith):
            a, s = r.stated_arith, "STATED"
        else:
            k = 0.25 if r.house == "Nuveen" else 0.5
            a, s = r.stated_geo + k * (v if v is not None and not pd.isna(v) else 0) ** 2, "DERIVED_GEO_PLUS_VOL2_OVER_" + ("4" if k == 0.25 else "2")
        ex_arith.append(a); ex_src.append(s); vol_f.append(v); vol_src.append(vs); fl.append(f)
    df["exp_arith"], df["arith_source"], df["vol"], df["vol_source"], df["flags"] = ex_arith, ex_src, vol_f, vol_src, fl
    df["as_of"] = df.house.map(lambda h: HOUSES[h]["as_of"]); df["document"] = df.house.map(lambda h: HOUSES[h]["doc"])

    path = out / "db" / "cma.db"; path.unlink(missing_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE cma_house(house TEXT PRIMARY KEY, document TEXT, as_of TEXT, convention TEXT, note TEXT, pdf_file TEXT);
    CREATE TABLE cma_estimate(id INTEGER PRIMARY KEY, cma_class TEXT, house TEXT, index_label TEXT, relation TEXT,
        exp_geo REAL, exp_arith REAL, arith_source TEXT, vol REAL, vol_source TEXT, as_of TEXT, document TEXT, pdf_page INTEGER, quote TEXT, flags TEXT);
    CREATE TABLE cma_corr(house TEXT, a TEXT, b TEXT, corr REAL, pdf_page INTEGER, quote TEXT);
    CREATE TABLE cma_class_map(asset_class TEXT PRIMARY KEY, cma_class TEXT, n_direct_houses INTEGER, coverage TEXT, note TEXT);
    CREATE VIEW cma_class_summary AS
        SELECT cma_class, COUNT(*) AS n_houses, ROUND(MIN(exp_geo),4) AS geo_min, ROUND(AVG(exp_geo),4) AS geo_mean, ROUND(MAX(exp_geo),4) AS geo_max,
               ROUND(MIN(exp_arith),4) AS arith_min, ROUND(AVG(exp_arith),4) AS arith_mean, ROUND(MAX(exp_arith),4) AS arith_max, ROUND(AVG(vol),4) AS vol_mean
        FROM cma_estimate WHERE relation='DIRECT' GROUP BY cma_class;
    """)
    for h, v in HOUSES.items():
        c.execute("INSERT INTO cma_house VALUES(?,?,?,?,?,?)", (h, v["doc"], v["as_of"], v["conv"], v["note"], v["file"]))
    for _, r in df.iterrows():
        c.execute("INSERT INTO cma_estimate(cma_class,house,index_label,relation,exp_geo,exp_arith,arith_source,vol,vol_source,as_of,document,pdf_page,quote,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (r.cma_class, r.house, r.index_label, r.relation, r.stated_geo, round(r.exp_arith, 5), r.arith_source, None if pd.isna(r.vol) else round(float(r.vol), 5), r.vol_source,
                   r.as_of, r.document, int(r.pdf_page), r.quote, r["flags"]))
    for h, a, b, v, q in CORR:
        c.execute("INSERT INTO cma_corr VALUES(?,?,?,?,?,?)", (h, a, b, v, page_of(h, q), norm(q)))
    n_dir = df[df.relation == "DIRECT"].groupby("cma_class").house.nunique().to_dict()
    def cov(n): return "FULL" if n >= 5 else "PARTIAL" if n >= 3 else "THIN" if n >= 1 else "NONE"
    maps = [("US_EQ", "US_EQUITY", "Direct US equity estimates from 7 houses (benchmarks differ: S&P 500, MSCI USA, Russell 3000, large cap)."),
            ("INTL_EQ", "INTL_EQUITY", "Direct developed ex-US estimates from 3 houses (PGIM unhedged, BNY, Nuveen EAFE). KKR, Amundi and Northern Trust publish only regional components; Invesco publishes ACWI ex-US (includes EM), shown but excluded."),
            ("US_IG", "US_IG_CORP", "US investment-grade corporate estimates from 6 houses (BNY publishes only the Aggregate). Estimates are index-level; different index definitions."),
            ("US_HY", "US_HY", "US high-yield estimates from 7 houses. Invesco's volatility looks low and is flagged."),
            ("CASH", "CASH", "Direct cash estimates.")]
    for ac, cc, note in maps:
        n = n_dir.get(cc, 0)
        c.execute("INSERT INTO cma_class_map VALUES(?,?,?,?,?)", (ac, cc, n, cov(n), note))
    c.executemany("INSERT INTO meta VALUES(?,?)", [("built_utc", pd.Timestamp.now("UTC").isoformat()), ("rows", str(len(df))),
                   ("horizon", "10-year (KKR also shows 5- and 20-year; the 10-year column is used)"),
                   ("basis", "Index-level, before any manager fees (PGIM states gross of fees). Currency: houses differ (Amundi local currency; PGIM international unhedged)."),
                   ("verification", "every row's quoted text was found on its cited PDF page at build time")])
    c.commit()
    print(pd.read_sql("SELECT * FROM cma_class_summary", c).to_string())
    print(pd.read_sql("SELECT * FROM cma_class_map", c).to_string())
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--raw", required=True); ap.add_argument("--out", default=".")
    a = ap.parse_args(); build(Path(a.raw), Path(a.out))
