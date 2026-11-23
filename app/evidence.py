"""Collects the live evidence the report needs, in one command (about 20 to 30 minutes, about $0.05 to $0.50 in total):

  python -m app.evidence                 # ping, the 3 example owners, 2 repeat endowment runs (consistency),
                                         # 1 cost-cutting comparison run, then safety + tokenomics
  python -m app.evidence --only endowment --repeat 2

Writes output/<run>/ for each run, output/tokenomics.json, output/safety.json, output/evidence_summary.md and the
dashboard (output/dashboard/index.html). Needs PORTKEY_API_KEY in .env (this folder or the one above)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    sys.path.insert(0, str(ROOT))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="example ids to run (endowment, pension, foundation)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per example")
    ap.add_argument("--no-compare", action="store_true", help="skip the no-follow-ups comparison run")
    ap.add_argument("--consistency", type=int, default=3, help="total endowment runs for the consistency test (0 or 1 = skip)")
    a = ap.parse_args(argv)
    from app.run import _load_env
    _load_env()
    from app import ping
    if ping.main() != 0:
        print("Ping failed; fix the key or gateway before running the committee."); return 1
    from app.chat import EXAMPLES
    from app.llm import LLM
    import app.orchestrator as orch
    plan = [(x["id"], x["text"], None) for x in EXAMPLES if not a.only or x["id"] in a.only] * max(a.repeat, 1)
    if a.consistency > 1 and (not a.only or "endowment" in a.only):
        plan += [(f"endowment-repeat-{k}", EXAMPLES[0]["text"], None) for k in range(2, a.consistency + 1)]
    if not a.no_compare:
        plan.append(("endowment-no-followups", EXAMPLES[0]["text"], 0))
    done = []
    for i, (label, text, fu) in enumerate(plan, 1):
        print(f"\n=== run {i}/{len(plan)}: {label} ===", flush=True)
        os.environ["AII_RUN_LABEL"] = label
        old = orch.MAX_FOLLOWUPS
        if fu is not None:
            orch.MAX_FOLLOWUPS = fu
        t0 = time.monotonic()
        try:
            res = orch.run_pipeline(text, LLM(), ROOT)
        finally:
            orch.MAX_FOLLOWUPS = old
        tel = json.loads((Path(res["output_dir"]) / "telemetry.json").read_text())
        done.append((label, res, tel))
        print(f"-> {res['status']} in {time.monotonic() - t0:.0f}s, {tel['tokens']['total']:,} tokens, ${tel['cost_usd']:.4f}", flush=True)
    os.environ.pop("AII_RUN_LABEL", None)
    from app import safety, tokenomics, dashboard
    print("\n=== safety scenarios ===")
    sres = safety.run_all()
    t = tokenomics.write(ROOT)
    dashboard.build(ROOT)
    L = ["# Evidence summary", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')} local time.", "",
         "| Label | Run | Status | Time (s) | Tokens | Cached input | Cost (USD) | Follow-ups |", "|---|---|---|---|---|---|---|---|"]
    for label, res, tel in done:
        L.append(f"| {label} | {res['run_id']} | {res['status']} | {tel['elapsed_s']:.0f} | {tel['tokens']['total']:,} | "
                 f"{tel['tokens']['cached']:,} | {tel['cost_usd']:.4f} | {tel['budgets']['followups_used']} |")
    base = [x for x in done if x[0] == "endowment"]
    cmp_ = [x for x in done if x[0] == "endowment-no-followups"]
    if base and cmp_:
        def alloc(res):
            p = Path(res["output_dir"]) / "decision.json"
            return json.loads(p.read_text()).get("asset_allocation", {}).get("weights_pct") if p.exists() else None
        b, c = base[0], cmp_[0]
        L += ["", "## Cost-cutting comparison: follow-ups on vs off (endowment)", "",
              f"- Tokens: {b[2]['tokens']['total']:,} vs {c[2]['tokens']['total']:,}; cost ${b[2]['cost_usd']:.4f} vs ${c[2]['cost_usd']:.4f}; "
              f"time {b[2]['elapsed_s']:.0f}s vs {c[2]['elapsed_s']:.0f}s.",
              f"- Final allocation with follow-ups: {alloc(b[1])}", f"- Without follow-ups: {alloc(c[1])}"]
    reps = [x for x in done if x[0] == "endowment" or x[0].startswith("endowment-repeat-")]
    if len(reps) > 1:
        from app import consistency
        cm = consistency.measure([consistency._load(Path(r[1]["output_dir"])) for r in reps], [r[1]["run_id"] for r in reps])
        (ROOT / "output" / "consistency.json").write_text(json.dumps(cm, indent=2), encoding="utf-8")
        L += [""] + consistency.markdown(cm)
    L += ["", f"Safety: {sum(r['passed'] for r in sres)} of {len(sres)} defences held (output/dashboard/safety.html).", ""]
    if t.get("n_runs"):
        L.append(f"Tokenomics: ${t['per_run']['cost_usd']:.4f} per run, ${t['per_user_month_usd']:.2f} per user per month "
                 f"({t['runs_per_user_month']:g} runs), ${t['scale'][10000]['model_cost_month']:,.0f} per month at 10,000 users.")
    (ROOT / "output" / "evidence_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print("\nOpen the app (python -m app.server), Runs & Memos tab, or output/dashboard/index.html.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
