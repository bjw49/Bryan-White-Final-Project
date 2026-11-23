"""Audit trail and ACIO allocation authority tests. Run: python3 tests/test_audit.py (exit 0 = pass). Runs on a temp copy."""
import shutil, sqlite3, sys, tempfile, uuid
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="aii_audit_test_"))
for d in ("db", "access", "contracts"):
    shutil.copytree(SRC / d, ROOT / d, ignore=shutil.ignore_patterns("__pycache__"))
sys.path.insert(0, str(ROOT / "access")); sys.path.insert(0, str(ROOT / "contracts"))
from trace import TraceLog
import audit_summary
from schemas import ACIODecision, validate_allocation_authority
fails = 0
def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name); fails += (not ok)

run = f"test-{uuid.uuid4().hex[:6]}"
t = TraceLog(ROOT, run)
t.turn("aar", 1, "gpt-6-luna", "SYSTEM", [{"role": "user", "content": "policy brief"}], "Recommend 60/40 style mix [E1].", [{"name": "get_cma"}])
t.tool("aar", 1, "get_cma", {"asset_class": "US_EQ"}, {"rows": 7})
c = sqlite3.connect(ROOT / "db" / "audit.db")
check("turn stored with full input and output text", c.execute("select output_text from agent_turn where run_id=?", (run,)).fetchone()[0].startswith("Recommend"))
check("tool result stored", c.execute("select tool from tool_result where run_id=?", (run,)).fetchone()[0] == "get_cma")
for stmt in ["UPDATE agent_turn SET output_text='x'", "DELETE FROM agent_turn", "UPDATE tool_result SET tool='x'", "DELETE FROM tool_result"]:
    try:
        c.execute(stmt); c.commit(); check(f"blocked: {stmt}", False)
    except sqlite3.DatabaseError:
        check(f"blocked: {stmt}", True)

base = {"decision": "NEEDS_OWNER_INPUT", "policy": {}, "manager_portfolio": [], "total_checks": {}, "red_team_responses": [],
        "unavailable_to_owner": [], "open_items_for_human_cio": [], "followups_used": 0, "memo": "m", "findings": []}
aar = {"US_EQ": 40, "INTL_EQ": 10, "US_IG": 35, "US_HY": 10, "CASH": 5}
same = ACIODecision(**base, asset_allocation={"weights_pct": dict(aar)})
check("ACIO adopting AAR weights needs no override record", validate_allocation_authority(same, aar) == [])
over = ACIODecision(**base, asset_allocation={"weights_pct": {**aar, "US_EQ": 30, "CASH": 15}})
check("ACIO change without record is refused", len(validate_allocation_authority(over, aar)) >= 2)
rec = ACIODecision(**base, asset_allocation={"weights_pct": {**aar, "US_EQ": 30, "CASH": 15}},
                   allocation_decision={"adopted": "ACIO_OVERRIDE", "reason": "stress loss too high", "evidence": ["E4"], "aar_weights_pct": aar, "final_weights_pct": {}})
check("ACIO override with reason and evidence passes", validate_allocation_authority(rec, aar) == [])

c.rollback(); c.close()
t.turn("acio", 1, "gpt-6-luna", "SYS", [{"role": "user", "content": "review"}], "Override US equity.", [])
c = sqlite3.connect(ROOT / "db" / "audit.db"); c.execute("INSERT INTO proposal_log(ts,run_id,agent,payload_json,payload_sha256) VALUES('t',?,?,?,?)", (run, "acio", '{"decision":"RECOMMEND","summary":"ok"}', "h")); c.commit(); c.close()
p1, p2 = audit_summary.build(run, ROOT)
check("trace file written", p1.exists() and "Override US equity" in p1.read_text())
s = p2.read_text()
check("summary has a section per agent and a final decision", "Head of Asset Allocation and Risk" in s and "Final decision" in s and "RECOMMEND" in s)
print("\n", "ALL PASS" if fails == 0 else f"{fails} FAILED"); sys.exit(1 if fails else 0)
