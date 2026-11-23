"""Builds two human-readable files per run from the append-only audit tables:
  audit/<run>_trace.md    every model turn, its full text output, every tool call and result, every publish
  audit/<run>_summary.md  one plain-language section per agent: what it was asked, what it did, what it decided
Run: python3 access/audit_summary.py <run_id> [root]
"""
import json, sqlite3, sys
from pathlib import Path

ORDER = ["acio", "aar", "head_us_eq", "head_intl_eq", "head_us_ig", "head_us_hy", "red_team"]
NAMES = {"acio": "ACIO (Acting CIO)", "aar": "Head of Asset Allocation and Risk", "head_us_eq": "Head of US Equities",
         "head_intl_eq": "Head of International Equities", "head_us_ig": "Head of US IG Fixed Income",
         "head_us_hy": "Head of US High Yield", "red_team": "Red Team"}

def _q(c, sql, p=()):
    cur = c.execute(sql, p); cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def _pub_summary(payload: dict) -> str:
    for k in ("summary", "verdict_reason", "memo"):
        if payload.get(k): return str(payload[k])
    return "(no summary field)"

def _weights_line(p: dict) -> str:
    if p.get("selected"):
        return ", ".join(f"{m.get('manager_id')} {m.get('weight_pct')}%" for m in p["selected"])
    rec = (p.get("recommended") or {}).get("weights_pct")
    if rec: return ", ".join(f"{k} {v}%" for k, v in rec.items() if v)
    if p.get("manager_portfolio"):
        return ", ".join(f"{m.get('manager_id')} {m.get('weight_total_pct')}%" for m in p["manager_portfolio"])
    if p.get("objections") is not None:
        return f"{len(p['objections'])} objections; verdict {p.get('verdict')}"
    return ""

def build(run_id: str, root: str | Path = "."):
    root = Path(root); c = sqlite3.connect(str(root / "db" / "audit.db")); c.row_factory = None
    turns = _q(c, "SELECT * FROM agent_turn WHERE run_id=? ORDER BY id", (run_id,))
    tools = _q(c, "SELECT * FROM tool_result WHERE run_id=? ORDER BY id", (run_id,))
    qlog = _q(c, "SELECT * FROM query_log WHERE run_id=? ORDER BY id", (run_id,))
    props = _q(c, "SELECT * FROM proposal_log WHERE run_id=? ORDER BY id", (run_id,))
    c.close()
    out = root / "audit"; out.mkdir(exist_ok=True)

    # ---------- full trace
    L = [f"# Full agent trace: run {run_id}", "",
         "Contents: every model call (input and output text), every tool call with its result, and every publish. Hidden reasoning tokens are not exported by the API and are not included.", ""]
    for t in turns:
        if t["model"] == "validated":
            L += [f"## VALIDATED · {NAMES.get(t['agent'], t['agent'])} · {t['ts']}", t["output_text"], "```json", (t["structured_json"] or "")[:8000], "```", ""]
            continue
        L += [f"## {NAMES.get(t['agent'], t['agent'])} · turn {t['turn']} · {t['ts']}", f"Model: {t['model']} · system prompt sha256 {t['system_sha256'][:12]}", ""]
        msgs = json.loads(t["input_json"] or "[]")
        L.append("### Input"); L += [f"**{m.get('role','?')}:** {str(m.get('content',''))[:3000]}" for m in msgs[-4:]]
        L += ["", "### Output (text)", t["output_text"] or "(empty)", ""]
        tc = json.loads(t["tool_calls_json"] or "[]")
        if tc: L.append("### Tool calls requested: " + ", ".join(x.get("name", "?") for x in tc)); L.append("")
        for r in [x for x in tools if x["agent"] == t["agent"] and x["turn"] == t["turn"]]:
            L += [f"#### Tool `{r['tool']}`", f"args: `{r['args_json']}`", "```", r["result_json"], "```", ""]
    for p in props:
        L += [f"## PUBLISHED by {NAMES.get(p['agent'], p['agent'])} · {p['ts']}", f"payload sha256 {p['payload_sha256'][:16]}", "```json", p["payload_json"][:6000], "```", ""]
    (out / f"{run_id}_trace.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- human summary
    S = [f"# Decision summary: run {run_id}", "",
         "Plain-language account of what each agent did, what it relied on and what it decided. Full detail is in the trace file.", ""]
    agents = [a for a in ORDER if any(t["agent"] == a for t in turns)] + sorted({t["agent"] for t in turns} - set(ORDER))
    for a in agents:
        at = [t for t in turns if t["agent"] == a and t["model"] != "validated"]  # real model calls only
        aq = [q for q in qlog if q["agent"] == a]
        ap = [p for p in props if p["agent"] == a]
        S += [f"## {NAMES.get(a, a)}", ""]
        S.append(f"- Model calls: {len(at)}. Data calls: {len(aq)} ({sum(1 for q in aq if str(q['status']).lower()=='ok')} succeeded, {sum(1 for q in aq if str(q['status']).lower()!='ok')} refused or failed).")
        used = {}
        for q in aq: used[q["tool"]] = used.get(q["tool"], 0) + 1
        for r in tools:  # orchestration tools (ask_agent) are logged in tool_result, not query_log
            if r["agent"] == a and r["tool"] not in used and r["tool"] == "ask_agent":
                used["ask_agent"] = used.get("ask_agent", 0) + 1
        if used:
            S.append("- Tools used: " + ", ".join(f"{k} ×{v}" for k, v in sorted(used.items())) + ".")
        for p in ap:
            pl = json.loads(p["payload_json"])
            S.append(f"- Published: {_pub_summary(pl)}")
            w = _weights_line(pl)
            if w: S.append(f"- Result: {w}.")
        if at and not ap:
            S.append("- Final text: " + (at[-1]["output_text"] or "(none)")[:600].replace("\n", " "))
        S.append("")
    # ACIO decision and overrides
    acio = [p for p in props if p["agent"] == "acio"]
    if acio:
        d = json.loads(acio[-1]["payload_json"])
        S += ["## Final decision", "", f"**{d.get('decision', '(no decision field)')}**", ""]
        if d.get("disagreements"): S.append("Overrides and disagreements recorded by the ACIO:"); S += [f"- {json.dumps(x)[:400]}" for x in d["disagreements"]]
        S.append("")
    (out / f"{run_id}_summary.md").write_text("\n".join(S), encoding="utf-8")
    return out / f"{run_id}_trace.md", out / f"{run_id}_summary.md"

if __name__ == "__main__":
    rid = sys.argv[1]; root = sys.argv[2] if len(sys.argv) > 2 else "."
    for f in build(rid, root): print("wrote", f)
