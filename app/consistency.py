"""Run-to-run consistency: the same owner brief run several times. LLM agents are not deterministic, so a human CIO
needs to know how much the recommendation moves when nothing in the input changes. Pure functions over saved runs.

  python -m app.consistency run-A run-B run-C     # report on saved runs (python -m app.evidence runs them for you)"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASSES = ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]


def _load(run_dir: Path) -> dict | None:
    p = Path(run_dir) / "decision.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def measure(decisions: list[dict | None], run_ids: list[str]) -> dict:
    ok = [(r, d) for r, d in zip(run_ids, decisions) if d]
    out = {"runs": run_ids, "decisions": {r: (d or {}).get("decision", "RUN_STOPPED") for r, d in zip(run_ids, decisions)},
           "n_with_decision": len(ok)}
    if len(ok) < 2:
        out["note"] = "fewer than two runs reached a decision; nothing to compare"
        return out
    w = {r: {c: float((d.get("asset_allocation") or {}).get("weights_pct", {}).get(c, 0) or 0) for c in CLASSES} for r, d in ok}
    out["class_weights"] = w
    out["class_weight_range_pts"] = {c: max(x[c] for x in w.values()) - min(x[c] for x in w.values()) for c in CLASSES}
    sel = {r: {} for r, _ in ok}
    tot = {r: {} for r, _ in ok}
    for r, d in ok:
        for m in d.get("manager_portfolio") or []:
            sel[r].setdefault(m.get("class"), set()).add(m.get("manager_id"))
            tot[r][m.get("manager_id")] = float(m.get("weight_total_pct") or 0)
    jac = {}
    for c in CLASSES[:-1]:
        vals = []
        for a, b in itertools.combinations(sel, 2):
            A, B = sel[a].get(c, set()), sel[b].get(c, set())
            if A or B:
                vals.append(len(A & B) / len(A | B))
        jac[c] = round(sum(vals) / len(vals), 3) if vals else None
    out["lineup_overlap_by_class"] = jac  # average pairwise Jaccard of the selected managers (1 = identical)
    common = []
    for a, b in itertools.combinations(tot, 2):
        common.append(sum(min(tot[a].get(m, 0), tot[b].get(m, 0)) for m in set(tot[a]) | set(tot[b])))
    out["portfolio_in_common_pct"] = round(sum(common) / len(common), 1)  # % of the total portfolio held identically (cash excluded)
    every = set.intersection(*[set(t) for t in tot.values()])
    anyr = set.union(*[set(t) for t in tot.values()])
    out["managers_in_every_run"] = sorted(every)
    out["managers_in_any_run"] = sorted(anyr)
    return out


def markdown(m: dict, label: str = "endowment") -> list[str]:
    L = [f"## Run-to-run consistency ({label}, {len(m['runs'])} runs of the same brief)", ""]
    L.append("- Decisions: " + ", ".join(f"{r}: {d}" for r, d in m["decisions"].items()))
    if m.get("note"):
        return L + [f"- {m['note']}", ""]
    L.append("- Class weight range across runs (percentage points): " +
             ", ".join(f"{c} {v:g}" for c, v in m["class_weight_range_pts"].items()))
    L.append("- Lineup overlap by class (average pairwise Jaccard, 1 = identical): " +
             ", ".join(f"{c} {v if v is not None else '-'}" for c, v in m["lineup_overlap_by_class"].items()))
    L.append(f"- Share of the total portfolio held identically in a pair of runs (average, manager positions only, cash "
             f"excluded): {m['portfolio_in_common_pct']}%")
    L.append(f"- Managers selected in every run: {', '.join(m['managers_in_every_run']) or 'none'}; "
             f"in at least one run: {len(m['managers_in_any_run'])}")
    return L + [""]


def main(argv=None) -> int:
    ids = list(argv if argv is not None else sys.argv[1:])
    if len(ids) < 2:
        print(__doc__); return 1
    m = measure([_load(ROOT / "output" / r) for r in ids], ids)
    print("\n".join(markdown(m)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
