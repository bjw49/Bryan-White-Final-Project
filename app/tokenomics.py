"""Tokenomics from measured runs. Reads output/<run>/telemetry.json for every LIVE run (mock runs use no tokens and are
excluded), and writes output/tokenomics.json plus the dashboard page. Every token count is what the gateway reported.

  python -m app.tokenomics

Assumptions (stated, change with env vars): a "user" is one investment professional at an asset owner; a typical user runs
AII_RUNS_PER_USER_MONTH committee runs a month (default 12: a manager search or allocation review needs several owner
scenarios and re-runs). Hosting is not included: the app runs locally; see the report for a hosted estimate."""
from __future__ import annotations

import json
import os
import statistics as st
from pathlib import Path

from app.pricing import PRICES, SOURCE, cost_usd

ROOT = Path(__file__).resolve().parent.parent
RUNS_PER_USER_MONTH = float(os.environ.get("AII_RUNS_PER_USER_MONTH", "12"))


def batch_start(root: Path = ROOT) -> str | None:
    """First run id of the latest `python -m app.evidence` batch (from output/evidence_summary.md), so the measured
    figures come from one consistent code version. Earlier live runs are kept and reported separately."""
    import re
    p = Path(root) / "output" / "evidence_summary.md"
    ids = re.findall(r"run-\d{8}T\d{6}-[0-9a-f]{4}", p.read_text(encoding="utf-8")) if p.exists() else []
    return min(ids) if ids else None


def live_runs(root: Path = ROOT, all_runs: bool = False) -> list[dict]:
    start = None if all_runs else batch_start(root)
    out = []
    for p in sorted((Path(root) / "output").glob("*/telemetry.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        if t.get("mock") or not t.get("model_calls"):
            continue
        if start and t.get("run_id", "") < start:
            continue
        t["_dir"] = p.parent.name
        out.append(t)
    return out


def summarise(root: Path = ROOT) -> dict:
    runs = live_runs(root)
    if not runs:
        return {"n_runs": 0, "note": "No live runs yet. Run: python -m app.evidence (or the chat without --mock)."}
    model = runs[0].get("model", "gpt-6-luna")
    done = [r for r in runs if r.get("status") not in ("RUN_STOPPED", "ESCALATED")]
    base = done or runs

    def col(key, sub=None, rs=base):
        return [float((r.get(key) or {}).get(sub, 0) if sub else r.get(key) or 0) for r in rs]
    prompt, cached, comp = col("tokens", "prompt"), col("tokens", "cached"), col("tokens", "completion")
    cwrite = col("tokens", "cache_write")
    cost, el, calls = col("cost_usd"), col("elapsed_s"), col("model_calls")
    mean = lambda xs: sum(xs) / len(xs)
    per_run = {"input": mean(prompt), "cached_input": mean(cached), "uncached_input": mean(prompt) - mean(cached) - mean(cwrite),
               "cache_write": mean(cwrite), "api": base[0].get("route", ""),
               "output": mean(comp), "total": mean(prompt) + mean(comp), "cost_usd": mean(cost),
               "elapsed_s": mean(el), "elapsed_s_max": max(el), "model_calls": mean(calls),
               "cost_usd_min": min(cost), "cost_usd_max": max(cost), "cache_share": (sum(cached) / sum(prompt)) if sum(prompt) else 0.0}
    no_cache_cost = cost_usd(per_run["input"], 0, per_run["output"], model)
    by_agent: dict = {}
    for r in base:
        for a, v in r.get("by_agent", {}).items():
            d = by_agent.setdefault(a, {"input": 0, "cached": 0, "output": 0, "calls": 0, "cost_usd": 0.0})
            d["input"] += v["prompt"]; d["cached"] += v.get("cached", 0); d["output"] += v["completion"]
            d["calls"] += v["calls"]; d["cost_usd"] += v.get("cost_usd", 0)
    for d in by_agent.values():
        for k in d:
            d[k] = d[k] / len(base)
    per_user_month = per_run["cost_usd"] * RUNS_PER_USER_MONTH
    scale = {n: {"users": n, "runs_per_month": n * RUNS_PER_USER_MONTH, "model_cost_month": per_user_month * n,
                 "model_cost_year": per_user_month * n * 12} for n in (1, 100, 10_000)}
    other_models = {m: cost_usd(per_run["input"], per_run["cached_input"], per_run["output"], m, cache_write=per_run["cache_write"])
                    for m in PRICES}
    by_label: dict = {}
    for r in runs:
        lab = r.get("label") or "default"
        by_label.setdefault(lab, []).append({"run": r["run_id"], "status": r.get("status"), "tokens": r["tokens"]["total"],
                                             "cost_usd": r.get("cost_usd"), "elapsed_s": r.get("elapsed_s"),
                                             "followups": (r.get("budgets") or {}).get("followups_used")})
    return {"n_runs": len(runs), "n_completed": len(done), "model": model, "price_source": SOURCE,
            "route": runs[0].get("route"), "n_all_live_runs": len(live_runs(root, all_runs=True)), "batch_start": batch_start(root),
            "runs_per_user_month": RUNS_PER_USER_MONTH, "per_run": per_run, "no_cache_cost_usd": no_cache_cost,
            "per_user_month_usd": per_user_month, "scale": scale, "same_tokens_other_models": other_models,
            "by_agent_mean": by_agent, "by_label": by_label,
            "runs": [{"run": r["run_id"], "label": r.get("label"), "status": r.get("status"), "elapsed_s": r.get("elapsed_s"),
                      "calls": r.get("model_calls"), **{k: r["tokens"].get(k, 0) for k in ("prompt", "cached", "completion", "total")},
                      "cost_usd": r.get("cost_usd")} for r in runs]}


def write(root: Path = ROOT) -> dict:
    s = summarise(root)
    (Path(root) / "output").mkdir(exist_ok=True)
    (Path(root) / "output" / "tokenomics.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


if __name__ == "__main__":
    s = write()
    from app import dashboard
    dashboard.build(ROOT)
    if not s["n_runs"]:
        print(s["note"])
    else:
        pr = s["per_run"]
        print(f"{s['n_runs']} live runs ({s['n_completed']} completed). Per run: {pr['total']:,.0f} tokens "
              f"({pr['cache_share']:.0%} of input cached), ${pr['cost_usd']:.4f}, {pr['elapsed_s']:.0f}s.")
        print(f"Per user per month ({s['runs_per_user_month']:g} runs): ${s['per_user_month_usd']:.2f}. "
              f"100 users: ${s['scale'][100]['model_cost_month']:,.2f}/month. 10,000 users: ${s['scale'][10000]['model_cost_month']:,.2f}/month.")
        print("Page: output/dashboard/tokenomics.html")
