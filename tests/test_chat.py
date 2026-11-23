"""Chat server and time budget checks. Runs on a temp copy so db/audit.db and output/ stay clean."""
import json, shutil, sys, tempfile, threading, time, urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
tmp = Path(tempfile.mkdtemp(prefix="aii-chat-"))
for item in SRC.iterdir():
    if item.name in ("output", "audit", ".env") or item.name.startswith("."):
        continue
    (shutil.copytree if item.is_dir() else shutil.copy2)(item, tmp / item.name, **({"ignore": shutil.ignore_patterns("__pycache__")} if item.is_dir() else {}))
(tmp / "output").mkdir(); (tmp / "audit").mkdir()

import app.chat as c
import app.orchestrator as orch
c.ROOT = tmp
c._mock = True
results = []
def check(name, ok):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name)

srv = c.make_server(8798); threading.Thread(target=srv.serve_forever, daemon=True).start()
B = "http://127.0.0.1:8798"
def get(p): return json.load(urllib.request.urlopen(B + p))
def post(p, o):
    r = urllib.request.Request(B + p, data=json.dumps(o).encode(), headers={"Content-Type": "application/json"})
    try:
        return 200, json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

check("page served", urllib.request.urlopen(B + "/").status == 200)
ex = get("/api/examples")
check("three examples", len(ex) == 3 and {x["id"] for x in ex} == {"endowment", "pension", "foundation"})
code, pe = post("/api/policy", {"owner": ex[1]["text"]})
check("pension example reads as corporate_db_pension", code == 200 and pe["policy"]["owner_type"] == "corporate_db_pension")
code, _ = post("/api/policy", {"owner": "   "})
check("empty ask refused with 400", code == 400)
code, _ = post("/api/run", {"run_id": "nope", "policy": None})
check("unknown run refused with 400", code == 400)

code, j = post("/api/run", {"run_id": pe["run_id"], "policy": pe["policy"]})
for _ in range(300):
    v = get("/api/job/" + j["job_id"])
    if v["status"] != "running":
        break
    time.sleep(0.2)
check("pension run completes with a dashboard link", v["status"] == "done" and v["result"]["dashboard"].startswith("/files/dashboard/"))
check("dashboard page reachable", urllib.request.urlopen(B + v["result"]["dashboard"]).status == 200)
check("progress messages recorded", len(v["messages"]) >= 4)

# Time budget: with a zero budget every agent stops at its first model call, and the run escalates instead of hanging.
orch_budget = orch.TIME_BUDGET_S
code, pe2 = post("/api/policy", {"owner": ex[0]["text"]})
orch.TIME_BUDGET_S = 0
from app.mock_llm import ScriptedLLM
from app.orchestrator import run_pipeline
out = run_pipeline(ex[0]["text"], ScriptedLLM(), str(tmp), "deadline-test-001", policy=pe2["policy"])
check("zero time budget escalates with a time-budget stop", out["status"] == "RUN_STOPPED" and "time budget" in (out["stop"] or {}).get("detail", ""))
orch.TIME_BUDGET_S = orch_budget

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("\n", "ALL PASS" if all(results) else f"{results.count(False)} FAILED")
sys.exit(0 if all(results) else 1)
