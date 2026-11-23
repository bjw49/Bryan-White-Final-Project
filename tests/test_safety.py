"""Runs every safety scenario in app/safety.py (attacks, malfunctions, overspending) on a temporary copy.
Run: python tests/test_safety.py   (python -m app.safety does the same and also writes the dashboard page)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.safety import run_all

rs = run_all(write=False)
for r in rs:
    print(("PASS " if r["passed"] else "FAIL ") + f"[{r['area']}] {r['attack']}")
bad = [r for r in rs if not r["passed"]]
print("\n", "ALL PASS" if not bad else f"{len(bad)} FAILED")
sys.exit(1 if bad else 0)
