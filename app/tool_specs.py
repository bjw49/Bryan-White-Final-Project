"""Builds the OpenAI-style function schemas for each agent from the gateway's own method signatures and policy allowlist.
Single source of truth: a tool exists for a model only if the gateway defines it AND the agent's policy allows it."""
from __future__ import annotations

import inspect
import types
import typing

_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}


def _unwrap_optional(t):
    if typing.get_origin(t) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        if args:
            return args[0]
    return t


def _schema(t) -> dict:
    t = _unwrap_optional(t)
    origin = typing.get_origin(t) or t
    if origin is list:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": _JSON.get(origin, "string")}


# Allowed values the gateway enforces, given to the model in the schema so it does not waste turns guessing them
# (live run 2026-11-23: a Head spent two of its five steps on list_managers(status="ACTIVE"/"ALL"), both rejected).
CLASSES = ["US_EQ", "INTL_EQ", "US_IG", "US_HY"]
WINDOWS_MGR = ["FULL", "10Y", "5Y", "3Y"]
WINDOWS_MKT = ["COMMON_SAMPLE", "20Y", "10Y", "5Y"]
PARAM_HINTS = {
    "manager_id": {"pattern": "^(UE|IE|IG|HY)\\d{2}$", "description": "Manager id such as UE01, IE02, IG03, HY04"},
    "status": {"enum": ["HISTORY_60_PLUS", "HISTORY_36_59", "HISTORY_UNDER_36"],
               "description": "Optional history filter; omit it to list every manager in the class"},
    "factor_set": {"enum": ["EQUITY", "FIXED_INCOME"]},
    "series": {"enum": ["US_EQ", "INTL_EQ", "US_IG_CORP", "US_HY"]},
    "corr_mode": {"enum": ["baseline", "downside"]},
    "start": {"description": "Date YYYY-MM-DD"},
    "end": {"description": "Date YYYY-MM-DD"},
    "weights": {"description": "Object {manager_id: percent of the class portfolio}, same class, percents sum to 100"},
    "agents": {"items": {"type": "string", "enum": ["aar", "head_us_eq", "head_intl_eq", "head_us_ig", "head_us_hy", "red_team", "acio"]},
               "description": "Optional: whose proposals to read. Omit it to read every proposal in this run"},
    "weights_pct": {"description": "Object {asset class: percent}, classes from US_EQ, INTL_EQ, US_IG, US_HY, CASH, sum to 100"},
}
TOOL_PARAM_HINTS = {  # parameters whose allowed values differ by tool
    ("get_asset_class_stats", "window"): {"enum": WINDOWS_MKT + ["FULL_OWN"]},
    ("get_asset_class_corr", "window"): {"enum": WINDOWS_MKT},
    ("get_manager_stats", "window"): {"enum": WINDOWS_MGR},
    ("get_factor_loadings", "window"): {"enum": WINDOWS_MGR},
    ("get_peer_stats", "window"): {"enum": WINDOWS_MGR},
    ("get_cma", "asset_class"): {"enum": CLASSES + ["CASH"]},
}


def _hints(gw, tool: str, param: str) -> dict:
    h = dict(PARAM_HINTS.get(param, {}))
    h.update(TOOL_PARAM_HINTS.get((tool, param), {}))
    if param == "asset_class" and (tool, param) not in TOOL_PARAM_HINTS:
        own = list(gw.cfg.get("classes") or [])
        h["enum"] = own if len(own) == 1 else CLASSES  # a Head sees only its own class (information barrier)
    return h


def gateway_tools(gw, exclude: tuple = ("publish_proposal",)) -> list[dict]:
    """Specs for every tool the policy allows this agent. publish_proposal is excluded from model use: the orchestrator
    publishes only validated outputs on the agent's behalf."""
    specs = []
    for name in gw.cfg["tools"]:
        if name in exclude or name in gw.cfg.get("never", []):
            continue
        fn = getattr(gw, "t_" + name)
        hints = typing.get_type_hints(fn)
        props, req = {}, []
        for p in inspect.signature(fn).parameters.values():
            if p.name == "self":
                continue
            props[p.name] = {**_schema(hints.get(p.name, str)), **_hints(gw, name, p.name)}
            if p.default is inspect.Parameter.empty:
                req.append(p.name)
        doc = (inspect.getdoc(fn) or name).splitlines()[0]
        specs.append({"type": "function", "function": {"name": name, "description": doc,
                                                       "parameters": {"type": "object", "properties": props, "required": req}}})
    return specs


def ask_agent_spec(max_followups: int) -> dict:
    """The ACIO's follow-up tool. The cap in the description is the one the orchestrator enforces (AII_MAX_FOLLOWUPS)."""
    return {"type": "function", "function": {
        "name": "ask_agent",
        "description": f"Ask a Head or the Head of Asset Allocation and Risk a follow-up question. Hard cap of {max_followups} per run.",
        "parameters": {"type": "object", "properties": {"agent": {"type": "string"}, "question": {"type": "string"}},
                       "required": ["agent", "question"]}}}
