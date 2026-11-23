---
name: verify-and-measure
description: Check a change to the ACIO app end to end and refresh the measured evidence (tests, live ping, live
  committee runs, safety, tokenomics, report). Use after changing agents, prompts, tools, the model client or the UI,
  or when asked to "verify", "re-run the evidence" or "update the report numbers".
---

# Verify and measure

1. **Offline tests (no key, about 30 s).** `python tests/run_all.py`. Stop and fix anything that fails.
2. **Route check (two live calls, under a cent).** `python -m app.ping`. Needs `OK`. `prompt cache: WORKING` means the
   explicit cache breakpoint is being honoured; if `NOT SEEN`, do not claim cache savings.
3. **One live committee run.** `python -m app.server`, open http://127.0.0.1:8765, run one example. It must end in a
   decision or a clear RUN STOPPED reason, not an error.
4. **Evidence batch (about 25 minutes).** `python -m app.evidence`: the three example owners plus a no-follow-ups run,
   then safety and tokenomics. Read `output/evidence_summary.md`.
5. **Live injection test (about 2 minutes).** `python -m app.injection_live`. Report the verdict as it comes out,
   including INCONCLUSIVE or PARTLY OBEYED.
6. **Report and slides.** `python docs/report/build_report.py`; the console lists any TO FILL boxes left.

## House rules
- Never edit numbers in the report by hand; rebuild from `output/`.
- Compare cost, tokens, time and decisions with the previous batch and say what changed and why.
- One run is an observation, not a result: say how many runs a claim rests on.
