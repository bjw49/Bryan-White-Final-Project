"""Hand-check of the deflated Sharpe ratio against the numerical example in Bailey & Lopez de Prado (2014),
"The Deflated Sharpe Ratio", Journal of Portfolio Management: N=100 trials, V[SR]=1/2 (annualised), T=1250 daily
observations, annualised SR=2.5, skew=-3, kurtosis=10. The paper reports DSR of about 0.90 ("only a 90% chance"),
0.9505 if N had been 46, and about 0.95 at N=88 had returns been Normal (skew 0, kurtosis 3).
Run: python tests/test_dsr_paper.py"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "access"))
from analytics import dsr_core

sr = 2.5 / math.sqrt(250); v = 0.5 / 250
cases = [((100, -3, 10), 0.9004), ((46, -3, 10), 0.9505), ((88, 0, 3), 0.9505)]
fails = 0
for (N, sk, ku), want in cases:
    sr0, d = dsr_core(sr, 1250, sk, ku, N, v)
    ok = abs(d - want) < 0.0015
    fails += not ok
    print(("PASS " if ok else "FAIL ") + f"N={N} skew={sk} kurt={ku}: SR0 annualised {sr0 * math.sqrt(250):.3f}, DSR {d:.4f} (paper {want})")
print("\n", "ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
