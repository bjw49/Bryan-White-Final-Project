"""Compose the final system prompt for each agent: shared rules + (head method + class playbook | role prompt).
Run: python3 build/assemble_prompts.py   -> agent_prompts/compiled/<agent>.md"""
from pathlib import Path
R = Path(__file__).resolve().parent.parent / "agent_prompts"
shared = (R / "00_shared_rules.md").read_text()
common = (R / "01_head_common_method.md").read_text()
parts = {
    "acio": ["40_acio.md"], "aar": ["20_head_asset_allocation_risk.md"], "red_team": ["30_red_team.md"],
    "head_us_eq": ["10_head_us_equities.md"], "head_intl_eq": ["11_head_international_equities.md"],
    "head_us_ig": ["12_head_us_ig_fixed_income.md"], "head_us_hy": ["13_head_us_high_yield_fixed_income.md"],
}
out = R / "compiled"; out.mkdir(exist_ok=True)
for agent, files in parts.items():
    body = [shared]
    if agent.startswith("head_"):
        body.append(common)
    body += [(R / f).read_text() for f in files]
    (out / f"{agent}.md").write_text("\n\n---\n\n".join(body))
    print(agent, len("\n\n".join(body).split()), "words")
