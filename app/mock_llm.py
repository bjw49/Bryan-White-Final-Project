"""Scripted stand-in for the live model, used when no key is available (--mock).
It makes the same kinds of decisions a model would: which tools to call, and what to write. It never touches the
database itself: it reads only the messages the orchestrator sends it, including the tool results. Tool numbers
are real (they come from the gateway); the wording is canned. Use it to test the wiring, not to judge the output."""
from __future__ import annotations

import json
import os
import re

from .llm import Fn, Msg, ToolCall

AAR_W = {"US_EQ": 40, "INTL_EQ": 10, "US_IG": 35, "US_HY": 10, "CASH": 5}
POLICY = {"owner_type": "endowment", "objective": "Real return of 5% a year over inflation, with spending of 4.5% a year.",
          "horizon": "Perpetual, 10-year planning", "risk_tolerance": "Maximum drawdown of about 25%; volatility target 10%",
          "constraints": "No private markets in this system; no leverage", "labels": {"owner_type": "STATED", "objective": "INFERRED"}}
POLICY_BY_OWNER = {
    "pension": {"owner_type": "corporate_db_pension", "objective": "Fund the liability over time with a funded-status target; reduce funded-status volatility.",
                "horizon": "Long-dated liability, 15-year planning", "risk_tolerance": "Low funded-status volatility; drawdown tolerance about 15%",
                "constraints": "No private markets in this system; no leverage; no Treasury securities in this system",
                "labels": {"owner_type": "STATED", "objective": "INFERRED"}},
    "foundation": {"owner_type": "foundation", "objective": "Sustain grant spending at about 5% of assets a year and preserve real value.",
                   "horizon": "Perpetual, 10-year planning", "risk_tolerance": "Low drawdown tolerance, about 15%",
                   "constraints": "No private markets in this system; no leverage",
                   "labels": {"owner_type": "STATED", "objective": "INFERRED"}},
}
def policy_for(text: str) -> dict:
    t = (text or "").lower()
    if "pension" in t or "defined benefit" in t or "liabilit" in t:
        return POLICY_BY_OWNER["pension"]
    if "foundation" in t or "grant" in t:
        return POLICY_BY_OWNER["foundation"]
    return POLICY
CLASS_WEIGHTS_HEADS = {"US_EQ": "head_us_eq", "INTL_EQ": "head_intl_eq", "US_IG": "head_us_ig", "US_HY": "head_us_hy"}


def _phase(msgs):
    """The phase is the most recent PHASE tag in the conversation (repair messages carry no tag of their own)."""
    for m in reversed(msgs):
        if m.get("role") == "user":
            hit = re.search(r"PHASE: (\w+)", str(m.get("content", "")))
            if hit:
                return hit.group(1)
    return ""


def _field(msgs, key):
    for m in reversed(msgs):
        if m.get("role") == "user":
            hit = re.search(rf"{key}: (\w+)", str(m.get("content", "")))
            if hit:
                return hit.group(1)
    return None


def _tool_msgs(msgs):
    return [m for m in msgs if m.get("role") == "tool"]


def _eids(tool_msgs):
    out = []
    for m in tool_msgs:
        out += re.findall(r'"evidence_id": "(E\d+)"', m["content"])
    return out


def _tool_content(tool_msgs, name):
    for m in tool_msgs:
        if f'"tool": "{name}"' in m["content"]:
            return m["content"]
    return ""


def class_ids(msgs):
    """Manager ids from the list_managers result in this conversation, in order, without repeats."""
    ids = []
    for mid in re.findall(r'"manager_id": "([A-Z]{2}\d{2})"', _tool_content(_tool_msgs(msgs), "list_managers")):
        if mid not in ids:
            ids.append(mid)
    return ids


def head_output(ids, eids, selected_n=3):
    """PLACEHOLDER content with the right shape. Rationales are not assessments; the scripted model ranks in list order."""
    ranking = [{"manager_id": m, "rank": i + 1,
                "rationale": f"Placeholder standalone rank {i + 1} of {len(ids)} from the scripted stand-in; not an assessment."}
               for i, m in enumerate(ids)]
    sel_ids = ids[:selected_n]
    weights = {3: [50, 30, 20]}.get(selected_n, [])
    sel = [{"manager_id": m, "weight_pct": weights[i], "role": ["core", "satellite", "diversifier"][i], "edge": "claimed edge (from text)",
            "rationale": f"Placeholder selection rationale citing {eids[0] if eids else 'no evidence'}.", "key_risks": ["style concentration"],
            "ddq_open_items": ["confirm capacity and turnover"], "confidence": "MEDIUM"} for i, m in enumerate(sel_ids)]
    nsel = [{"manager_id": m, "why_considered": "in class", "why_not_selected": "Placeholder: lower conviction than the selected managers."} for m in ids[selected_n:]]
    return {"summary": f"Placeholder lineup of {len(sel_ids)} managers from {len(ids)}; scripted stand-in, not a judgment.",
            "selected": sel, "not_selected": nsel, "ranking": ranking,
            "no_lineup_reason": None if sel else "NO_DEFENSIBLE_MANAGER",
            "fit": {"expected_active_risk_note": "not run in mock", "diversification_note": "mock", "sizing_logic": "placeholder"},
            "limitations": ["scripted stand-in output"], "data_flags": [],
            "findings": [{"claim": "Placeholder finding.", "evidence": eids[:1]}]}


def _calls(*pairs):
    return Msg(None, [ToolCall(f"call_{i}", Fn(n, json.dumps(a))) for i, (n, a) in enumerate(pairs)])


def _text(obj):
    return Msg(json.dumps(obj))


class ScriptedLLM:
    policy = POLICY

    def otype(self):
        return self.policy["owner_type"]

    model_name = "scripted-mock"

    def __init__(self):
        self.usage = []

    def chat(self, agent, msgs, tools=None):
        phase = _phase(msgs)
        tm = _tool_msgs(msgs)
        stage = len(tm)  # number of tool results so far in this loop
        eids = _eids(tm)

        if phase == "policy":
            users = [m.get("content") or "" for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
            last_user = users[-1] if users else ""
            self.policy = policy_for(str(last_user))
            if os.environ.get("AII_MOCK_QUESTIONS") == "1" and "OWNER'S ANSWERS" not in str(last_user):
                # demo of the pre-run clarifying round without a key (python -m app.server --mock, AII_MOCK_QUESTIONS=1)
                return _text({**self.policy, "questions": [
                    {"question": "Is the stated real return target net of spending, or in addition to it?",
                     "why": "It moves the required nominal return by the full spending rate.", "default": "net of spending"},
                    {"question": "How much cash must be available within 12 months?",
                     "why": "Sets the cash floor and limits illiquid-leaning credit.", "default": "one year of payouts"}]})
            return _text(self.policy)

        if phase == "allocate":  # Head of Asset Allocation and Risk
            if stage == 0:
                return _calls(("get_investable_universe", {"owner_type": self.otype()}), ("get_cma", {}),
                              ("evaluate_allocation", {"weights_pct": AAR_W, "owner_type": self.otype()}))
            rec = {"weights_pct": dict(AAR_W), "ranges_pct": {k: [max(0, v - 5), min(100, v + 5)] for k, v in AAR_W.items()},
                   "expected_return_range": {"low": 5.0, "base": 6.5, "high": 7.5, "basis": "CMA_DIRECT mean", "evidence": eids[:1]},
                   "risk": {"volatility": 9.5, "max_drawdown_history": -30.0, "worst_episode": "GFC", "evidence": eids[-1:]},
                   "guardrail_check": [{"rule": "endowment guardrails", "status": "PASS", "note": "inside default ranges"}]}
            return _text({"summary": "Equity-led mix with a 35% core credit sleeve and a small high-yield satellite. Stress drawdown is the binding risk.",
                          "assumptions": [{"item": "owner is an endowment", "label": "STATED"}],
                          "recommended": rec, "alternatives": [{"name": "Conservative", "weights_pct": {**AAR_W, "US_EQ": 30, "CASH": 15}, "trade_off": "Lower drawdown, lower return.", "evidence": eids[-1:]}],
                          "sensitivity": {"tested": "CMA return +/-1pt", "result": "Equity weight moves little", "evidence": eids[-1:]},
                          "not_modelled": [{"item": "PRIVATE_EQUITY", "reason": "not available in this system", "implication": "Endowment may hold private equity elsewhere"}],
                          "implementation": {"rebalancing_band": "+/-5 points (design guidance)", "manager_alpha_note": "expected alpha not modelled; returns are index-level"},
                          "limitations": ["hedging policy unknown (returns are USD)"],
                          "findings": [{"claim": "Equity carries most of the risk budget.", "evidence": eids[-1:]}]})

        if phase == "select":  # the four Heads: standalone ranking of every manager, then a selection
            cls = _field(msgs, "CLASS")
            if stage == 0:
                return _calls(("list_managers", {"asset_class": cls}), ("get_peer_stats", {"asset_class": cls, "window": "FULL"}))
            ids = class_ids(msgs)
            return _text(head_output(ids, eids, selected_n=3))

        if phase == "review":  # ACIO review: reads proposals, may ask follow-ups
            if stage == 0:
                return _calls(("read_proposals", {}))
            if stage == 1:
                return _calls(("ask_agent", {"agent": "head_us_eq", "question": "What is the case for the top US equity weight?"}))
            return _text({"review": "Accept the AAR mix. Head lineups are acceptable pending Red Team.",
                          "provisional_allocation_pct": dict(AAR_W), "requested_changes": []})

        if phase == "followup":  # a Head answering the ACIO
            return _text({"answer": "The top manager carries the most evidence; the weight reflects that and the stated tracking-error budget.",
                          "findings": [], "revised_proposal": None})

        if phase == "challenge":  # Red Team
            if stage == 0:
                return _calls(("stress_test", {"weights_pct": AAR_W, "max_loss_tolerance": 0.25}))
            return _text({"verdict": "PROCEED_WITH_CONDITIONS", "verdict_reason": "Stress loss is near the tolerance; conditions required.",
                          "objections": [{"id": "O1", "severity": "MAJOR", "target": "US_EQ weight", "claim": "Stress loss is close to the tolerance.",
                                          "evidence": eids[-1:], "fix_criterion": "Stress loss below the stated tolerance under downside correlation."}],
                          "integrity_checks": {"quarantine_violations": [], "cap_violations": [], "universe_violations": [], "arithmetic_violations": [], "injection_flags": []},
                          "statistical_findings": [], "stress": {"evidence": eids[-1:]},
                          "premortem": [{"mechanism": "Equity drawdown in a credit-led sell-off", "early_warning": "HY spreads widen first", "data": "episode table"}],
                          "unverifiable": ["currency hedging policy"], "findings": [{"claim": "Stress test run on the provisional mix.", "evidence": eids[-1:]}]})

        if phase == "final":  # ACIO final decision
            if stage == 0:
                return _calls(("evaluate_allocation", {"weights_pct": AAR_W, "owner_type": self.otype()}))
            return _text({"decision": "RECOMMEND_WITH_CONDITIONS", "policy": POLICY,
                          "asset_allocation": {"weights_pct": dict(AAR_W), "ranges_pct": {}, "rationale": "AAR mix; Red Team condition on drawdown"},
                          "allocation_decision": {"aar_weights_pct": dict(AAR_W), "final_weights_pct": dict(AAR_W), "adopted": "AAR", "reason": "Red Team objection addressed by a condition, not a weight change.", "evidence": eids[-1:]},
                          "manager_portfolio": [], "total_checks": {"sum_total_pct": 100.0},
                          "red_team_responses": [{"objection_id": "O1", "response": "PARTLY_ACCEPTED", "action": "Adds a drawdown monitoring condition.", "evidence": eids[-1:]}],
                          "unavailable_to_owner": [{"item": "PRIVATE_EQUITY", "reason": "not available in this system"}],
                          "open_items_for_human_cio": ["Confirm currency hedging of international managers", "Confirm drawdown tolerance (O1)"],
                          "disagreements": [], "followups_used": 0,
                          "memo": "Recommend the AAR mix with conditions. The Red Team's stress objection is addressed by a monitoring condition. Human CIO should confirm the drawdown tolerance and hedging policy before funding.",
                          "findings": [{"claim": "Final mix is inside the default endowment guardrails.", "evidence": eids[-1:]}]})

        return Msg("{}")
