"""Pydantic output contracts for the 7 agents (5 available asset classes). Validate every agent JSON against these.
Hard rules enforced in code, not in the prompt: weights only on AVAILABLE classes, sums. There are NO fixed per-manager caps, floors or exclusions in code (owner instruction); the ACIO may cap or remove a
manager only as its recorded answer to a Red Team objection (ManagerChange)."""
from __future__ import annotations
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

AvailableClass = Literal["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]
Conf = Literal["HIGH", "MEDIUM", "LOW"]
Label = Literal["STATED", "INFERRED", "ASSUMED"]
CLASSES = ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]


class Finding(BaseModel):
    claim: str
    evidence: list[str] = Field(default_factory=list)  # ["E1","E3"]; every numeric claim needs at least one


class SelectedManager(BaseModel):
    manager_id: str = Field(pattern=r"^(UE|IE|IG|HY)\d{2}$")
    weight_pct: float = Field(gt=0, le=100)  # no per-manager caps or floors (owner instruction)
    role: str = ""
    edge: str
    rationale: str
    key_risks: list[str] = Field(default_factory=list)
    ddq_open_items: list[str] = Field(default_factory=list)
    confidence: Conf


class NotSelected(BaseModel):
    manager_id: str = Field(pattern=r"^(UE|IE|IG|HY)\d{2}$")
    why_considered: str
    why_not_selected: str


class RankedManager(BaseModel):
    """Standalone rank: how attractive this manager is as the only hire for the class, ignoring portfolio weights."""
    manager_id: str = Field(pattern=r"^(UE|IE|IG|HY)\d{2}$")
    rank: int = Field(ge=1)
    rationale: str  # 1-3 sentences; stripped for the Red Team like every other rationale


class HeadProposal(BaseModel):
    summary: str
    selected: list[SelectedManager]
    not_selected: list[NotSelected]
    ranking: list[RankedManager]  # every manager in the class, once, ranks 1..N
    fit: dict
    limitations: list[str]
    data_flags: list[str] = Field(default_factory=list)
    findings: list[Finding]

    @model_validator(mode="after")
    def _check(self):
        if self.selected and abs(sum(m.weight_pct for m in self.selected) - 100) > 0.01:
            raise ValueError("selected weights must sum to 100")
        if any(m.weight_pct <= 0 for m in self.selected):
            raise ValueError("selected weights must be positive")
        ids = [m.manager_id for m in self.selected] + [m.manager_id for m in self.not_selected]
        if len(ids) != len(set(ids)):
            raise ValueError("a manager appears twice")
        if len({i[:2] for i in ids}) > 1:
            raise ValueError("managers from more than one class")
        ranked = [r.manager_id for r in self.ranking]
        if len(ranked) != len(set(ranked)):
            raise ValueError("a manager appears twice in the ranking")
        if sorted(r.rank for r in self.ranking) != list(range(1, len(self.ranking) + 1)):
            raise ValueError("ranking must be 1..N with no gaps or ties")
        if set(ranked) != set(ids):
            raise ValueError("ranking must cover exactly the managers in selected and not_selected")
        return self


class Assumption(BaseModel):
    item: str
    label: Label


class Recommended(BaseModel):
    weights_pct: dict[AvailableClass, float]
    ranges_pct: dict[AvailableClass, list[float]]
    expected_return_range: dict
    risk: dict
    guardrail_check: list[dict]

    @field_validator("weights_pct")
    @classmethod
    def _sum(cls, v):
        if abs(sum(v.values()) - 100) > 1e-6:
            raise ValueError("allocation must sum to 100")
        return v


class NotModelled(BaseModel):
    item: str
    reason: str
    implication: str


class AARProposal(BaseModel):
    summary: str
    assumptions: list[Assumption]
    recommended: Recommended
    alternatives: list[dict]
    sensitivity: dict
    not_modelled: list[NotModelled]
    implementation: dict
    limitations: list[str]
    findings: list[Finding]


class Objection(BaseModel):
    id: str
    severity: Literal["CRITICAL", "MAJOR", "MINOR"]
    target: str
    claim: str
    evidence: list[str]
    fix_criterion: str


class RedTeamReport(BaseModel):
    verdict: Literal["PROCEED", "PROCEED_WITH_CONDITIONS", "DO_NOT_PROCEED_AS_IS"]
    verdict_reason: str
    objections: list[Objection] = Field(max_length=8)
    integrity_checks: dict
    statistical_findings: list[dict]
    stress: dict
    premortem: list[dict] = Field(max_length=7)
    unverifiable: list[str] = Field(default_factory=list)
    findings: list[Finding]

    @model_validator(mode="after")
    def _evidence(self):
        for o in self.objections:
            if not o.evidence:
                raise ValueError(f"objection {o.id} has no evidence")
        return self


class ManagerPosition(BaseModel):
    manager_id: str = Field(pattern=r"^(UE|IE|IG|HY)\d{2}$")
    cls: AvailableClass = Field(alias="class")
    weight_in_class_pct: float
    weight_total_pct: float
    role: str
    condition: str = ""


class ManagerChange(BaseModel):
    """How the ACIO acts on a manager-level objection. Code applies it to the lineup: REMOVE drops the manager, CAP
    limits its weight in its class; the freed weight goes pro rata to the class's other managers."""
    manager_id: str = Field(pattern=r"^(UE|IE|IG|HY)\d{2}$")
    change: Literal["REMOVE", "CAP"]
    max_weight_in_class_pct: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _cap(self):
        if self.change == "CAP" and self.max_weight_in_class_pct is None:
            raise ValueError(f"CAP on {self.manager_id} needs max_weight_in_class_pct")
        return self


class RedTeamResponse(BaseModel):
    objection_id: str
    response: Literal["ACCEPTED", "PARTLY_ACCEPTED", "REJECTED"]
    action: str
    evidence: list[str] = Field(default_factory=list)
    manager_changes: list[ManagerChange] = Field(default_factory=list)


class ACIODecision(BaseModel):
    decision: Literal["RECOMMEND", "RECOMMEND_WITH_CONDITIONS", "NEEDS_OWNER_INPUT", "DO_NOT_RECOMMEND"]
    policy: dict
    asset_allocation: dict  # the ACIO's FINAL class weights (ACIO has final say on allocation)
    allocation_decision: dict = Field(default_factory=dict)  # {"aar_weights_pct", "final_weights_pct", "adopted": "AAR|ALTERNATIVE|ACIO_OVERRIDE", "reason", "evidence"}
    manager_portfolio: list[ManagerPosition]
    total_checks: dict
    red_team_responses: list[RedTeamResponse]
    unavailable_to_owner: list[dict]
    open_items_for_human_cio: list[str]
    disagreements: list[dict] = Field(default_factory=list)
    followups_used: int = Field(ge=0, le=4)
    memo: str
    findings: list[Finding]

    @model_validator(mode="after")
    def _check(self):
        if self.decision in ("RECOMMEND", "RECOMMEND_WITH_CONDITIONS"):
            tot = sum(p.weight_total_pct for p in self.manager_portfolio)
            cash = float(self.asset_allocation.get("weights_pct", {}).get("CASH", 0))
            if abs(tot + cash - 100) > 0.51:
                raise ValueError(f"manager weights ({tot}) + cash ({cash}) must be 100")
        if len(self.memo.split()) > 900:
            raise ValueError("memo too long")
        return self


def validate_allocation_authority(dec: "ACIODecision", aar_weights: dict) -> list[str]:
    """The ACIO decides the allocation. If its final class weights differ from the AAR's recommendation, it must say so and why."""
    errs = []
    final = {k: round(float(v), 2) for k, v in dec.asset_allocation.get("weights_pct", {}).items() if float(v)}
    aar = {k: round(float(v), 2) for k, v in (aar_weights or {}).items() if float(v)}
    if final != aar:
        ad = dec.allocation_decision or {}
        if ad.get("adopted") not in ("ALTERNATIVE", "ACIO_OVERRIDE"):
            errs.append("final weights differ from the AAR recommendation: set allocation_decision.adopted to ALTERNATIVE or ACIO_OVERRIDE")
        if not str(ad.get("reason", "")).strip():
            errs.append("allocation change has no reason")
        if not ad.get("evidence"):
            errs.append("allocation change has no evidence ids")
    return errs


MANAGER_ID = re.compile(r"\b(?:UE|IE|IG|HY)\d{2}\b")
CLASS_ID = re.compile(r"\b(US_EQ|INTL_EQ|US_IG|US_HY|CASH)\b")


def validate_decision_against_red_team(dec: ACIODecision, rt: RedTeamReport, baseline: dict | None = None) -> list[str]:
    """Programmatic coverage check. Every objection needs a response. A MAJOR or CRITICAL objection:
      * REJECTED needs evidence;
      * ACCEPTED or PARTLY_ACCEPTED cannot end with MORE money in what it targets: a manager named in the target may
        not hold a larger share of the total portfolio, and a class named in the target may not carry a larger weight,
        than in the reviewed mix the Red Team challenged (baseline). Accepting a doubt and then adding to it is the
        contradiction this check stops; the fix is a CAP or REMOVE, or rejecting the objection with evidence."""
    errs = []
    resp = {r.objection_id: r for r in dec.red_team_responses}
    base = baseline or {}
    base_m, base_c = base.get("managers", {}), base.get("classes", {})
    fin_m = {p.manager_id: p.weight_total_pct for p in dec.manager_portfolio}
    fin_c = {k: float(v) for k, v in dec.asset_allocation.get("weights_pct", {}).items()}
    for o in rt.objections:
        r = resp.get(o.id)
        if r is None:
            errs.append(f"no response to {o.id}")
            continue
        if o.severity not in ("CRITICAL", "MAJOR"):
            continue
        if r.response == "REJECTED":
            if not r.evidence:
                errs.append(f"{o.id} ({o.severity}) rejected without evidence")
            continue
        for m in sorted(set(MANAGER_ID.findall(o.target))):
            b, f = base_m.get(m), fin_m.get(m, 0.0)
            if b is not None and f > b + 0.01:
                errs.append(f"{o.id} ({o.severity}) on {m} was {r.response}, but {m}'s share of the total portfolio rose "
                            f"from {b:g}% to {f:g}%: add manager_changes [{{\"manager_id\": \"{m}\", \"change\": \"CAP\", "
                            f"\"max_weight_in_class_pct\": <lower>}}] or REMOVE to that response, or REJECT the objection with evidence")
        if not MANAGER_ID.search(o.target):
            for c in sorted(set(CLASS_ID.findall(o.target))):
                b, f = base_c.get(c), fin_c.get(c, 0.0)
                if b is not None and f > b + 0.01:
                    errs.append(f"{o.id} ({o.severity}) on {c} was {r.response}, but {c} rose from {b:g}% to {f:g}%: "
                                f"lower it, or REJECT the objection with evidence")
    return errs
