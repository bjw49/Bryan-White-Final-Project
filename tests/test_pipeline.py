"""End-to-end pipeline tests on a temporary copy, using the scripted model with fault hooks.
Run: python3 tests/test_pipeline.py  (exit 0 = all pass). Covers the happy path and each control in the orchestrator."""
import json, shutil, sqlite3, sys, tempfile, uuid
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="aii_pipe_test_"))
for d in ("db", "access", "contracts", "universe", "agent_prompts", "app"):
    shutil.copytree(SRC / d, ROOT / d, ignore=shutil.ignore_patterns("__pycache__"))
sys.path.insert(0, str(ROOT))
from app.llm import Fn, Msg, ToolCall
from app.mock_llm import ScriptedLLM, _phase, _tool_msgs, AAR_W, class_ids, head_output
from app.orchestrator import run_pipeline
import app.orchestrator as orch_mod

OWNER = "A university endowment with a 5% real return target. Medium risk tolerance. No private markets."
fails = 0
def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name); fails += (not ok)

def repairing(msgs):
    return any(m.get("role") == "user" and "failed validation" in str(m.get("content", "")) for m in msgs)

def edit(llm, agent, msgs, tools, fn):
    base = ScriptedLLM.chat(llm, agent, msgs, tools)
    d = json.loads(base.content); fn(d)
    return Msg(json.dumps(d))

class Faulty(ScriptedLLM):
    def __init__(self, hook):
        super().__init__(); self.hook = hook
    def chat(self, agent, msgs, tools=None):
        r = self.hook(self, agent, msgs, tools)
        return r if r is not None else super().chat(agent, msgs, tools)

def run(hook, name):
    rid = f"{name}-{uuid.uuid4().hex[:4]}"
    res = run_pipeline(OWNER, Faulty(hook), ROOT, rid)
    return res, ROOT / "output" / rid

def read(p):
    return json.loads(p.read_text()) if p.exists() else None

# 1. happy path
res, out = run(lambda *a: None, "happy")
check("happy path reaches a decision", res["status"] == "RECOMMEND_WITH_CONDITIONS" and (out / "decision.json").exists())
dec = read(out / "decision.json")
tot = sum(p["weight_total_pct"] for p in dec["manager_portfolio"]) + dec["asset_allocation"]["weights_pct"]["CASH"]
check("code-built lineup sums with cash to 100", abs(tot - 100) < 0.5)
db = sqlite3.connect(ROOT / "db" / "audit.db")
check("all five publishers recorded in proposal log", {r[0] for r in db.execute("select agent from proposal_log where run_id=?", (res["run_id"],))} >= {"aar", "head_us_eq", "head_intl_eq", "head_us_ig", "head_us_hy", "acio"})
check("red team output recorded (validated, not published)", db.execute("select count(*) from agent_turn where run_id=? and agent='red_team' and output_text like '[VALIDATED%'", (res["run_id"],)).fetchone()[0] == 1)
check("trace and summary written", (ROOT / "audit" / f"{res['run_id']}_trace.md").exists() and (ROOT / "audit" / f"{res['run_id']}_summary.md").exists())

# 2. invalid head output is repaired once
def h_repair(llm, agent, msgs, tools):
    if agent == "head_us_eq" and _phase(msgs) == "select" and _tool_msgs(msgs) and not repairing(msgs):
        return edit(llm, agent, msgs, tools, lambda d: d["selected"][0].update(weight_pct=30))  # weights sum to 90
    return None
res, out = run(h_repair, "repair")
check("bad head output repaired and run completes", res["status"] == "RECOMMEND_WITH_CONDITIONS")
check("repair is flagged", any("head_us_eq: output repaired once" in f for f in res["flags"]))

# 3. unrepairable output escalates; no decision is written
def h_stuck(llm, agent, msgs, tools):
    if agent == "head_us_hy" and _phase(msgs) == "select" and _tool_msgs(msgs):
        return edit(llm, agent, msgs, tools, lambda d: d["selected"][0].update(weight_pct=30))
    return None
res, out = run(h_stuck, "stuck")
check("unrepairable head escalates", res["status"] == "RUN_STOPPED" and res["stop"]["stage"] == "head_us_hy")
check("escalation written, no decision.json", (out / "escalation.json").exists() and not (out / "decision.json").exists())
check("memo states escalation", "RUN STOPPED" in (out / "memo.md").read_text())

# 4. disallowed tool is refused and flagged
def h_blocked(llm, agent, msgs, tools):
    if agent == "head_us_ig" and _phase(msgs) == "select" and not _tool_msgs(msgs):
        return Msg(None, [ToolCall("b0", Fn("stress_test", json.dumps({"weights_pct": AAR_W}))),
                          ToolCall("b1", Fn("list_managers", json.dumps({"asset_class": "US_IG"}))),
                          ToolCall("b2", Fn("get_peer_stats", json.dumps({"asset_class": "US_IG", "window": "FULL"})))])
    return None
res, out = run(h_blocked, "blocked")
check("disallowed tool refused and flagged", any("requested tool 'stress_test'" in f for f in res["flags"]) and res["status"] == "RECOMMEND_WITH_CONDITIONS")

# 5. finding citing a non-existent evidence id is dropped
def h_badcite(llm, agent, msgs, tools):
    if agent == "head_us_ig" and _phase(msgs) == "select" and _tool_msgs(msgs):
        return edit(llm, agent, msgs, tools, lambda d: d.update(findings=[{"claim": "unsupported", "evidence": ["E999"]}]))
    return None
res, out = run(h_badcite, "badcite")
check("unsupported finding dropped and flagged", any("head_us_ig: dropped 1 finding" in f for f in res["flags"]))

# 6. ACIO changes allocation without a record: refused, repaired with an ACIO_OVERRIDE record
def h_override(llm, agent, msgs, tools):
    if agent == "acio" and _phase(msgs) == "final" and _tool_msgs(msgs):
        if not repairing(msgs):
            return edit(llm, agent, msgs, tools, lambda d: (d["asset_allocation"]["weights_pct"].update(US_EQ=30, US_IG=45), d.pop("allocation_decision", None)))
        return edit(llm, agent, msgs, tools, lambda d: (d["asset_allocation"]["weights_pct"].update(US_EQ=30, US_IG=45),
                    d.update(allocation_decision={"aar_weights_pct": AAR_W, "final_weights_pct": {**AAR_W, "US_EQ": 30, "US_IG": 45},
                                                  "adopted": "ACIO_OVERRIDE", "reason": "Stress loss above tolerance at 40% equity.", "evidence": ["E1"]})))
    return None
res, out = run(h_override, "override")
dec = read(out / "decision.json") or {}
check("ACIO override requires and keeps a record", res["status"] == "RECOMMEND_WITH_CONDITIONS" and dec.get("allocation_decision", {}).get("adopted") == "ACIO_OVERRIDE")
check("override weights drive the code-built lineup", dec.get("asset_allocation", {}).get("weights_pct", {}).get("US_EQ") == 30)

# 7. follow-up cap: the 5th ask is refused
def h_asks(llm, agent, msgs, tools):
    if agent == "acio" and _phase(msgs) == "review":
        n = len(_tool_msgs(msgs))
        if n == 0:
            return Msg(None, [ToolCall("r0", Fn("read_proposals", "{}"))])
        if n <= orch_mod.MAX_FOLLOWUPS + 1:  # one ask past the cap, so the refusal is exercised
            return Msg(None, [ToolCall(f"a{n}", Fn("ask_agent", json.dumps({"agent": "head_us_eq", "question": f"q{n}"})))])
    return None
res, out = run(h_asks, "asks")
db = sqlite3.connect(ROOT / "db" / "audit.db")
check("ask past the cap refused", db.execute("select count(*) from tool_result where run_id=? and result_json like '%FOLLOWUP_CAP%'", (res["run_id"],)).fetchone()[0] == 1)
check("follow-ups counted at the cap", (read(out / "decision.json") or {}).get("followups_used") == orch_mod.MAX_FOLLOWUPS)

# 8. a Red Team objection with no ACIO response is refused, repaired, then escalates
def h_noresp(llm, agent, msgs, tools):
    if agent == "acio" and _phase(msgs) == "final" and _tool_msgs(msgs):
        return edit(llm, agent, msgs, tools, lambda d: d.update(red_team_responses=[]))
    return None
res, out = run(h_noresp, "noresp")
check("unanswered objection escalates at final stage", res["status"] == "RUN_STOPPED" and res["stop"]["stage"] == "final")

# 10. a ranking that omits a manager is refused and repaired; if it is still wrong, the run escalates at that Head
def h_partial_rank(llm, agent, msgs, tools):
    if agent == "head_us_ig" and _phase(msgs) == "select" and _tool_msgs(msgs):
        def drop(d):
            d["ranking"] = d["ranking"][:-1]
            for i, r in enumerate(d["ranking"]): r["rank"] = i + 1
        return edit(llm, agent, msgs, tools, drop)
    return None
res, out = run(h_partial_rank, "partialrank")
check("ranking missing a manager escalates at that Head", res["status"] == "RUN_STOPPED" and res["stop"]["stage"] == "head_us_ig")
check("the refusal states the coverage rule", "cover" in res["stop"]["detail"] or "missing" in res["stop"]["detail"])

# 11. a ranking with a tie is refused by the contract
def h_tie(llm, agent, msgs, tools):
    if agent == "head_us_eq" and _phase(msgs) == "select" and _tool_msgs(msgs) and not repairing(msgs):
        def tie(d):
            d["ranking"][1]["rank"] = 1
        return edit(llm, agent, msgs, tools, tie)
    return None
res, out = run(h_tie, "tie")
check("tied ranks repaired on retry", res["status"] == "RECOMMEND_WITH_CONDITIONS")

# 12. dashboard pages from the happy run; model text is escaped
from app import dashboard
happy = sorted((ROOT / "output").glob("happy-*"))[0]
dash = ROOT / "output" / "dashboard"
check("dashboard index written", (dash / "index.html").exists())
check("run page written", (dash / f"run-{happy.name}.html").exists())
for c in ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]:
    check(f"class page {c} written", (dash / f"class-{c}.html").exists())
cls_page = (dash / "class-US_EQ.html").read_text()
check("class page lists a next-best and the full ranking", "Next-best managers" in cls_page and "Standalone ranking, all managers" in cls_page)
check("class page shows every US equity manager in the ranking", all(f"<b>UE0{i}</b>" in cls_page or f"<b>UE{i:02d}</b>" in cls_page for i in range(1, 10)))
check("model text is HTML-escaped", dashboard.e("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;")

# 9. a class with weight but no managers stops the run at review
def h_thin(llm, agent, msgs, tools):
    if agent == "head_us_hy" and _phase(msgs) == "select" and _tool_msgs(msgs):
        return Msg(json.dumps(head_output(class_ids(msgs), [], selected_n=0)))
    return None
res, out = run(h_thin, "thin")
check("weight with no lineup escalates at review", res["status"] == "RUN_STOPPED" and res["stop"]["stage"] == "review")

# 13. live run 2026-11-23: a Head publishes an empty lineup, the ACIO asks it a follow-up, the Head revises with managers.
# The review must accept the revised lineup (it used to check the stale one and escalate).
_saved = {}
def h_revised(llm, agent, msgs, tools):
    if agent == "head_us_eq" and _phase(msgs) == "select" and _tool_msgs(msgs):
        _saved["ids"] = class_ids(msgs)
        return Msg(json.dumps(head_output(_saved["ids"], [], selected_n=0)))
    if agent == "head_us_eq" and _phase(msgs) == "followup":
        return Msg(json.dumps({"answer": "Revised: the evidence supports a three-manager lineup.", "findings": [],
                               "revised_proposal": head_output(_saved["ids"], [], selected_n=3)}))
    return None
res, out = run(h_revised, "revised")
check("a lineup revised after a follow-up is used by the review check", res["status"] == "RECOMMEND_WITH_CONDITIONS")


# 14. live run 2026-11-23: read_proposals with human-style agent names returned zero rows, so the ACIO and Red Team
# reviewed without the full proposals. Names now resolve; a filter that matches nothing returns everything.
sys.path.insert(0, str(ROOT / "access"))
from access.gateway import DataGateway, agent_id
check("agent names resolve", [agent_id(x) for x in ("Head of US Equities", "AAR", "Head_US_EQ", "US_HY", "Head of US IG Fixed Income",
      "Head of International Equities", "Red Team", "ACIO", "Head of Asset Allocation and Risk", "nobody")] ==
      ["head_us_eq", "aar", "head_us_eq", "head_us_hy", "head_us_ig", "head_intl_eq", "red_team", "acio", "aar", None])
_rid = happy.name
_g = DataGateway("acio", _rid, ROOT)
_all = _g.call("read_proposals")
_named = _g.call("read_proposals", agents=["Head of US Equities", "AAR"])
_none = _g.call("read_proposals", agents=["nobody at all"])
check("read_proposals with loose names returns those proposals", len(_all["rows"]) >= 5 and {r["agent"] for r in _named["rows"]} == {"head_us_eq", "aar"})
check("a filter that matches nothing returns all proposals with a note", len(_none["rows"]) == len(_all["rows"]) and "note" in _none)

# 15. consistency measure over saved runs
from app import consistency
_d = consistency._load(happy)
_alt = json.loads(json.dumps(_d)); _alt["manager_portfolio"] = [m for m in _alt["manager_portfolio"] if m["manager_id"] != "UE01"]
_m = consistency.measure([_d, _d, None], ["a", "b", "c"])
_mgr_total = round(sum(float(m["weight_total_pct"]) for m in _d["manager_portfolio"]), 1)
check("identical runs are fully consistent", abs(_m["portfolio_in_common_pct"] - _mgr_total) < 0.05 and all(v in (1.0, None) for v in _m["lineup_overlap_by_class"].values())
      and _m["decisions"]["c"] == "RUN_STOPPED" and _m["n_with_decision"] == 2)
_m2 = consistency.measure([_d, _alt], ["a", "b"])
check("a dropped manager lowers overlap", _m2["lineup_overlap_by_class"]["US_EQ"] < 1 and _m2["portfolio_in_common_pct"] < _m["portfolio_in_common_pct"]
      and "UE01" not in _m2["managers_in_every_run"])
check("consistency markdown renders", "Run-to-run consistency" in "\n".join(consistency.markdown(_m2)))

# 16. guardrails: a RECOMMEND decision outside the owner-type guardrails needs a named waiver, or the run stops
def _breach(waiver):
    def h(llm, agent, msgs, tools):
        if agent == "acio" and _phase(msgs) == "final" and _tool_msgs(msgs):
            def f(d):
                d["asset_allocation"]["weights_pct"].update(US_EQ=30, CASH=15)  # cash above the 10% pension guardrail
                d.update(allocation_decision={"aar_weights_pct": AAR_W, "final_weights_pct": {**AAR_W, "US_EQ": 30, "CASH": 15},
                                              "adopted": "ACIO_OVERRIDE", "reason": "Benefit-payment reserve.", "evidence": ["E1"]})
                if waiver:
                    d["total_checks"]["guardrails"] = waiver
            return edit(llm, agent, msgs, tools, f)
        return None
    return h
res, out = run(_breach(None), "guardrail-breach")
check("guardrail breach without a waiver stops the run at final", res["status"] == "RUN_STOPPED" and res["stop"]["stage"] == "final"
      and "guardrail" in res["stop"]["detail"])
res, out = run(_breach("WAIVED(CASH: owner states a 15% reserve for benefit payments)"), "guardrail-waived")
check("guardrail breach with a named waiver is accepted and recorded", res["status"] == "RECOMMEND_WITH_CONDITIONS"
      and "WAIVED(CASH" in (read(out / "decision.json") or {}).get("total_checks", {}).get("guardrails", ""))
res, out = run(_breach("WAIVED"), "guardrail-bare-waiver")
check("a bare WAIVED without the class and reason is not a waiver", res["status"] == "RUN_STOPPED")

# 17. breakeven inflation: the real expected return is computed in code from the 10-year breakeven (as of 2026-10-31)
_ga = DataGateway("aar", "bei-test", ROOT)
_ev = _ga.call("evaluate_allocation", weights_pct=AAR_W, owner_type="endowment")
_bei = _ev.get("inflation_assumption", {}).get("ten_year_breakeven")
check("10-year breakeven is 2.34% as of 2026-10-31", _bei == 0.0234 and _ev["inflation_assumption"]["as_of"] == "2026-10-31")
check("real return = (1 + geometric nominal) / (1 + breakeven) - 1",
      abs(_ev["expected_real_return_geometric_approx"] - ((1 + _ev["expected_return_geometric_approx"]) / 1.0234 - 1)) < 1e-4
      and _ev["expected_real_return_geometric_range_across_houses"][0] < _ev["expected_real_return_geometric_range_across_houses"][1])
_cma = _ga.call("get_cma")
check("get_cma carries the breakeven curve with its caveat", len(_cma["inflation_breakeven"]["curve"]) == 8
      and "not a forecast" in _cma["inflation_breakeven"]["note"])

print("\n", "ALL PASS" if fails == 0 else f"{fails} FAILED")
sys.exit(1 if fails else 0)
