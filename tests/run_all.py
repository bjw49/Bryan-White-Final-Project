"""Runs every test file (mock model only, no key, about a minute). Exit code 0 = all pass.
Run: python tests/run_all.py"""
import subprocess, sys
from pathlib import Path
here = Path(__file__).resolve().parent
files = ["test_gateway.py", "test_audit.py", "test_pipeline.py", "test_chat.py", "test_dsr_paper.py", "test_safety.py", "test_responses_api.py", "test_server.py"]
bad = []
for f in files:
    r = subprocess.run([sys.executable, str(here / f)], capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1].strip()
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {f:22s} {tail}")
    if r.returncode:
        bad.append(f); print(r.stdout[-1500:], r.stderr[-1500:])
print("\nALL TEST FILES PASS" if not bad else f"\nFAILED: {bad}")
sys.exit(1 if bad else 0)
