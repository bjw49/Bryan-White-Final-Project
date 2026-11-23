"""Adds the market-implied inflation (breakeven) curve to db/cma.db as table cma_inflation.

Source: US Treasury nominal minus TIPS breakeven curve, supplied by the owner, as of 2026-10-31 (the same month-end as
the manager and benchmark data). Breakeven is a market-implied planning assumption, not a pure inflation forecast: it
also contains an inflation risk premium and TIPS liquidity effects. An inflation assumption stated by the owner
overrides it.

  python build/build_breakevens.py            # reads build/breakevens.csv, rewrites table cma_inflation"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2026-10-31"
SOURCE = "US Treasury nominal minus TIPS breakeven inflation curve, owner-supplied market data"
NOTE = ("Market-implied planning assumption, not a forecast: includes an inflation risk premium and TIPS liquidity effects. "
        "An owner-stated inflation assumption overrides it. The 10-year point matches the 10-year CMA horizon.")


def build(db: Path = ROOT / "db" / "cma.db", src: Path = ROOT / "build" / "breakevens.csv") -> int:
    rows = [(int(r["tenor_years"]), float(r["breakeven"])) for r in csv.DictReader(open(src, newline=""))]
    if not rows or any(not (0 <= b < 0.2) for _, b in rows):
        raise ValueError("breakevens must be decimals between 0 and 0.2 (2.34% = 0.0234)")
    c = sqlite3.connect(db)
    c.executescript("DROP TABLE IF EXISTS cma_inflation; CREATE TABLE cma_inflation(tenor_years INTEGER PRIMARY KEY, "
                    "breakeven REAL, as_of TEXT, source TEXT, note TEXT);")
    c.executemany("INSERT INTO cma_inflation VALUES(?,?,?,?,?)", [(t, b, AS_OF, SOURCE, NOTE) for t, b in rows])
    c.commit(); c.close()
    return len(rows)


if __name__ == "__main__":
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "db" / "cma.db"
    print(f"cma_inflation: {build(db)} tenors written to {db}")
