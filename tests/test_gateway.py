"""Access-control tests. Run: python tests/test_gateway.py   (exit code 0 = all pass)"""
import json, sqlite3, sys, uuid
from pathlib import Path
import shutil, tempfile
SRC = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="aii_test_"))          # tests run on a COPY: the shipped audit.db stays clean
for d in ("db", "access", "universe", "contracts"):
    shutil.copytree(SRC / d, ROOT / d, ignore=shutil.ignore_patterns("__pycache__"))
_c = sqlite3.connect(ROOT / "db" / "mgr_intl_equity.db"); _c.execute("UPDATE strategy_text SET description_type='MISSING' WHERE manager_id='IE03'"); _c.commit(); _c.close()
sys.path.insert(0, str(ROOT / "access"))
from gateway import DataGateway

def gw(agent, run=None): return DataGateway(agent, run or f"test-{uuid.uuid4().hex[:6]}", ROOT)
passed = 0
def check(name, cond):
    global passed
    print(("PASS " if cond else "FAIL ") + name); assert cond, name; passed += 1

# 1 information barrier between asset-class heads
g = gw("head_us_ig")
check("head sees own manager", "manager" in g.call("get_manager_card", manager_id="IG01"))
for other in ["UE01", "IE01", "HY01"]:
    check(f"head_us_ig denied {other}", g.call("get_manager_card", manager_id=other).get("error") == "ACCESS_DENIED")
check("head_us_ig denied other class listing", g.call("list_managers", asset_class="US_HY").get("error") == "ACCESS_DENIED")
check("head_us_ig denied other benchmark", g.call("get_benchmark_returns", series="US_HY").get("error") == "ACCESS_DENIED")
# 2 AAR has no manager-level access
a = gw("aar")
check("aar denied manager card", a.call("get_manager_card", manager_id="UE01").get("error") == "ACCESS_DENIED")
check("aar denied returns tool", a.call("get_returns", manager_id="UE01").get("error") == "ACCESS_DENIED")
check("aar reads universe", "available_asset_classes" in a.call("get_investable_universe", owner_type="endowment"))
u = a.call("get_investable_universe", owner_type="endowment")
check("endowment: private equity listed as NOT available", any(x["id"] == "PRIVATE_EQUITY" for x in u["NOT_AVAILABLE_do_not_allocate"]))
check("endowment: 5 available classes", len(u["available_asset_classes"]) == 5)
# 3 no exclusions: short-history and weak-evidence managers are fully visible and usable (owner instruction)
h = gw("head_us_hy")
check("short-history manager returns readable", "data" in h.call("get_returns", manager_id="HY05", limit=5))
check("short-history manager stats readable", h.call("get_manager_stats", manager_id="HY05", window="FULL").get("stats") is not None)
check("card shows history label, not an exclusion", gw("head_us_hy").call("get_manager_card", manager_id="HY05")["manager"]["status"] == "HISTORY_UNDER_36")
check("returns are labelled USD", sqlite3.connect(ROOT / "db" / "mgr_us_hy.db").execute("select currency from managers where manager_id='HY05'").fetchone()[0] == "USD")
check("short-history manager can be weighted without any cap", "stats" in gw("head_us_ig").call("evaluate_manager_mix", weights={"IG05": 60, "IG03": 40}) or gw("head_us_ig").call("evaluate_manager_mix", weights={"IG05": 60, "IG03": 40}).get("error") == "INSUFFICIENT_OVERLAP")
rt = gw("red_team")
# 4 no SQL / unknown tools / bad args
check("free-form SQL tool does not exist", g.call("run_sql", query="select 1").get("error") == "ACCESS_DENIED")
check("injection-style manager_id rejected", g.call("get_manager_card", manager_id="IG01'; DROP TABLE managers;--").get("error") == "BAD_ARGUMENTS")
check("bad window rejected", g.call("get_manager_stats", manager_id="IG01", window="1Y").get("error") == "BAD_ARGUMENTS")
# 5 read-only
c = g._ro("mgr_us_ig.db")
try:
    c.execute("DELETE FROM managers"); ok = False
except sqlite3.OperationalError:
    ok = True
check("class DB is read-only", ok)
# 6 untrusted text wrapping
t = g.call("get_strategy_text", manager_id="IG01")
check("strategy text wrapped as untrusted data", t["untrusted_text"].startswith("<untrusted_data") and "Do not follow" in t["instruction"])
check("firm-only description flagged", gw("head_us_ig").call("get_strategy_text", manager_id="IG06")["description_type"] == "FIRM_ONLY")
check("missing description flagged", gw("head_intl_eq").call("get_strategy_text", manager_id="IE03")["description_type"] == "MISSING")
# 7 factors are dated and scoped by agent
check("equity head cannot read FI factors", gw("head_us_eq").call("get_factors", factor_set="FIXED_INCOME").get("error") == "ACCESS_DENIED")
f = gw("head_us_hy").call("get_factors", factor_set="FIXED_INCOME")
check("factors are dated", f["data"][0][1][:2] in ("19", "20") and {x["factor"] for x in f["coverage"]} == {"Treasury", "Credit"})
fl = gw("head_us_hy").call("get_factor_loadings", manager_id="HY08", factor_set="FIXED_INCOME")
check("factor loadings computed", len(fl["univariate"]) == 2 and fl["joint"] is not None)
check("equity head cannot run FI loadings", gw("head_us_eq").call("get_factor_loadings", manager_id="UE01", factor_set="FIXED_INCOME").get("error") == "ACCESS_DENIED")
check("head cannot run loadings on another class", gw("head_us_hy").call("get_factor_loadings", manager_id="UE01", factor_set="FIXED_INCOME").get("error") == "ACCESS_DENIED")
# CMA database and no fee tool
cm = gw("aar").call("get_cma")
check("cma covers 5 classes with direct houses", {m["asset_class"] for m in cm["class_map"]} == {"US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"} and all(m["n_direct_houses"] >= 3 for m in cm["class_map"]))
check("fee tool is gone", gw("head_us_ig").call("get_fee_assumption", asset_class="US_IG").get("error") == "ACCESS_DENIED" and "fee_assumption" not in [r[0] for r in sqlite3.connect(ROOT / "db" / "market.db").execute("select name from sqlite_master")])
check("cma agrees: HY has 7 direct houses", [m for m in gw("acio").call("get_cma", asset_class="US_HY")["class_map"]][0]["n_direct_houses"] == 7)
check("real HY04 rescale flag present in shipped DB", any(r[0] == "UNITS_PERCENT_RESCALED" for r in sqlite3.connect(SRC / "db" / "mgr_us_hy.db").execute("select check_code from data_quality where manager_id='HY04'")))
# 8 budgets
b = gw("aar"); [b.call("get_asset_class_stats") for _ in range(30)]
check("call budget enforced", b.call("get_asset_class_stats").get("error") == "ACCESS_DENIED")
VALID_HEAD = {"summary": "SECRET2", "selected": [{"manager_id": "IG01", "weight_pct": 100, "rationale": "SECRET", "edge": "e", "confidence": "LOW"}], "not_selected": [], "ranking": [{"manager_id": "IG01", "rank": 1, "rationale": "SECRET3"}], "fit": {}, "limitations": ["x"], "findings": []}
# 9 proposals: heads write only; barrier + red team strips reasoning
run = f"prop-{uuid.uuid4().hex[:6]}"
gw("head_us_ig", run).call("publish_proposal", payload=VALID_HEAD)
check("invalid payload refused by contract", gw("head_us_ig", run).call("publish_proposal", payload={"x": 1}).get("error") == "BAD_ARGUMENTS")
check("head cannot read proposals", gw("head_us_hy", run).call("read_proposals").get("error") == "ACCESS_DENIED")
check("acio reads proposals", len(gw("acio", run).call("read_proposals")["rows"]) == 1)
rr = gw("red_team", run).call("read_proposals")["rows"][0]["payload_json"]
check("red team does not see author rationale", "secret author reasoning" not in rr and "IG01" in rr)
check("red team cannot publish", gw("red_team", run).call("publish_proposal", payload={"x": 1}).get("error") == "ACCESS_DENIED")
# 10 audit log append-only
al = sqlite3.connect(ROOT / "db" / "audit.db")
try:
    al.execute("DELETE FROM query_log"); ok = False
except sqlite3.DatabaseError:
    ok = True
al.rollback()
check("audit log append-only", ok)
check("denials are logged", al.execute("SELECT COUNT(*) FROM query_log WHERE status='DENIED'").fetchone()[0] > 5)
# 11 vault unreachable
check("no tool exposes the vault", not any("vault" in n for n in g.tools))

al.close()
# ---- analytics tools
h = gw("head_us_ig")
mix = h.call("evaluate_manager_mix", weights={"IG01": 50, "IG03": 50})
check("mix returns stats or overlap error (no crash)", "stats" in mix or mix.get("error") == "INSUFFICIENT_OVERLAP")
check("mix denies other class", h.call("evaluate_manager_mix", weights={"UE01": 50, "UE02": 50}).get("error") == "ACCESS_DENIED")
check("mix rejects mixed classes", gw("red_team").call("evaluate_manager_mix", weights={"UE01": 50, "IG01": 50}).get("error") == "BAD_ARGUMENTS")
check("dsr works", "deflated_sharpe_probability" in h.call("deflated_sharpe", manager_id="IG01", n_trials=10))
check("dsr denied for AAR", gw("aar").call("deflated_sharpe", manager_id="IG01").get("error") == "ACCESS_DENIED")
a = gw("aar")
ev = a.call("evaluate_allocation", weights_pct={"US_EQ": 40, "INTL_EQ": 15, "US_IG": 30, "US_HY": 10, "CASH": 5}, owner_type="endowment")
check("allocation eval ok", ev.get("sum_ok") is True and ev["volatility"] > 0)
check("HY basis is now CMA-direct", "CMA_DIRECT" in ev["bases"]["US_HY"])
check("private equity weight rejected", a.call("evaluate_allocation", weights_pct={"PRIVATE_EQUITY": 20, "US_EQ": 80}).get("error") == "BAD_ARGUMENTS")
dn = a.call("evaluate_allocation", weights_pct={"US_EQ": 40, "INTL_EQ": 15, "US_IG": 30, "US_HY": 10, "CASH": 5}, corr_mode="downside")
check("downside vol >= baseline vol", dn["volatility"] >= ev["volatility"])
check("AAR denied stress_test", a.call("stress_test", weights_pct={"US_EQ": 100}).get("error") == "ACCESS_DENIED")
st = gw("red_team").call("stress_test", weights_pct={"US_EQ": 40, "INTL_EQ": 15, "US_IG": 30, "US_HY": 10, "CASH": 5}, max_loss_tolerance=0.15)
check("stress test returns breach flags", "breach_bad_year_downside" in st)
# red team proposals: nested rationale stripped
w = gw("head_us_ig", "rt-strip"); w.call("publish_proposal", payload=VALID_HEAD)
rp = gw("red_team", "rt-strip").call("read_proposals")
check("red team sees no nested rationale", "SECRET" not in json.dumps(rp) and "IG01" in json.dumps(rp))

# 13 a gateway first used in a worker thread can be used again from another thread (ACIO follow-up after the parallel stage)
import threading
tg = gw("head_us_eq")
th = threading.Thread(target=lambda: tg.call("list_managers", asset_class="US_EQ")); th.start(); th.join()
check("gateway usable across threads (follow-up after parallel stage)", "manager" in tg.call("get_manager_card", manager_id="UE01"))

# 14 live-run fixes: own class and factor set are defaults; common labels accepted; reviewers can always read proposals
check("head factor loadings default to own factor set", "univariate" in gw("head_us_ig").call("get_factor_loadings", manager_id="IG01"))
check("factor set alias accepted", "univariate" in gw("head_us_hy").call("get_factor_loadings", manager_id="HY01", factor_set="credit"))
check("head class alias resolves to own class", "asset_class" in gw("head_us_hy").call("get_class_scope", asset_class="HY"))
check("head still cannot read another class's factors", gw("head_us_eq").call("get_factor_loadings", manager_id="UE01", factor_set="FIXED_INCOME").get("error") == "ACCESS_DENIED")
check("red team loadings infer factor set from manager", "univariate" in gw("red_team").call("get_factor_loadings", manager_id="HY02"))
rb = gw("red_team")
for _ in range(rb.cfg["max_calls"] + 2):
    rb.call("list_managers", asset_class="US_IG")
check("read_proposals works after the data budget is spent", "rows" in rb.call("read_proposals"))

# live batch 6: a Head that spent its data budget could not check its lineup and proposed none. evaluate_manager_mix
# keeps a reserve of 8 calls past the budget; other tools stay denied.
_bg = gw("head_us_eq", "budget-reserve")
for _ in range(60):
    _bg.call("get_manager_card", manager_id="UE01")
check("data calls past the budget are denied", _bg.call("get_manager_card", manager_id="UE01").get("error") == "ACCESS_DENIED")
check("the lineup check still runs past the budget", "error" not in _bg.call("evaluate_manager_mix", weights={"UE01": 50, "UE02": 50}))
print(f"\n{passed} checks passed")
