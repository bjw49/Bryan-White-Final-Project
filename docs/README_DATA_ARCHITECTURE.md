# Data architecture and agent design (v2: 7 agents, 5 investable classes)

## Agents
ACIO (final authority) / Head of Asset Allocation and Risk (AAR) / Head of US IG / Head of US HY / Head of US Equities / Head of International Equities / Red Team.

## Why this should beat a single agent
1. **Narrower context per agent.** Each Head sees ~10 managers in one class plus a class-specific playbook (archetypes, what the numbers mean in that asset class, known pitfalls). A single agent reading 40 managers across four classes dilutes attention and applies equity intuition to credit (for example IG information ratios are large by construction because tracking error is small).
2. **Independence.** Heads cannot see each other or the AAR's proposals; the Red Team sees structured proposals with authors' reasoning stripped (including nested fields), so it audits rather than agrees.
3. **Evaluation is not return-only.** Managers are judged as a human expert would: process and edge from the strategy text, return evidence with its statistical strength, narrative-vs-numbers consistency, and fit within the lineup's risk budget. The pillar weights of the earlier design are gone; there are no scores to compute and no gates.
4. **Deterministic math in code.** Mix statistics, deflated Sharpe, allocation, stress and guardrail checks are tools; LLMs do not do arithmetic.
5. **The ACIO cannot allocate to what does not exist.** `get_investable_universe(owner_type)` is step 0 for ACIO, AAR and Red Team; the allocation tools reject any class outside US_EQ, INTL_EQ, US_IG, US_HY, CASH; the pydantic contract rejects it again.

## Data tiers
| Tier | Content | Where | Who |
|---|---|---|---|
| T0 | benchmarks, dated factors, universe, guardrails, episodes | db/market.db | all agents via tools |
| T0 | capital-market assumptions, 7 houses, every row quoted with its PDF page | db/cma.db | AAR, ACIO, Red Team via get_cma |
| T1 | pseudonymized managers, one physical DB per class | db/mgr_*.db | only that class's Head (+ ACIO and Red Team via tools) |
| T2 | owner-confidential policy, liabilities, holdings | run object in app memory, never in a DB | passed to agents field by field |
| T3 | identity vault (opaque id -> "Mgr N", tickers), API keys | secure/vault.db, .env | no agent tool reaches it |

## Controls (enforced in access/gateway.py, tested in tests/test_gateway.py)
Default-deny per-agent tool allowlist and class scope; read-only SQLite (`mode=ro`, `query_only`); no free-form SQL (fixed parameterised tools, regex-validated arguments); call budgets and row/text caps; strategy text returned inside `<untrusted_data>` with an injection scan; quarantined managers withheld from everyone except Red Team; append-only audit log and proposal log (triggers); stats for quarantined managers nulled on the card.

## Manager statuses
HISTORY_60_PLUS, HISTORY_36_59, HISTORY_UNDER_36: descriptive history-length labels only; no manager is excluded or capped. Process evidence: STRATEGY_TEXT, FIRM_LEVEL_ONLY or NONE, also descriptive.

## Data status after the owner's fixes (details: docs/DATA_QUALITY_REPORT.md)
- 38 managers have 60+ months and 2 have under 36 months (Mgr 25 at 24 and Mgr 35 at 25). All 40 are open to the agents with no cap, floor or exclusion (owner instruction). Returns are in USD and net of fees; whether the international managers hedge currency is not stated.
- Mgr 34 (HY04): the updated column is in percent units (values up to -16.9). The build divides it by 100 and flags UNITS_PERCENT_RESCALED (correlation to the HY benchmark 0.97 afterward). The old duplicate-of-Mgr-29 problem is gone.
- Mgr 14 (IE04): sign fixed, 67 months. Mgr 12 and Mgr 13 now have descriptions. Mgr 26 deliberately keeps a firm-level description (owner's test: is the qualitative evidence weighed?). Mgr 26 can be selected at any weight; the test is whether the agents let a firm-only description affect their judgment.
- Mgr 18 is a portable-alpha structure (not a pure international equity mandate); Mgr 22 benchmark fit is low (corr 0.75).
- Factors: one dated file (Treasury, Credit, Momentum, Economic Momentum, Growth, Value, Quality, Crowding, Innovation, Realized Volatility). get_factor_loadings gives descriptive OLS loadings of a manager's excess return; factor definitions and signs are undocumented.

## CMA database (db/cma.db)
7 houses (KKR, Invesco, Amundi, PGIM, Northern Trust, BNY, Nuveen), 10-year horizon, 60 rows; the build fails if a row's quote is not found on its PDF page. Direct estimates per available class: US equity 7 houses, international equity 3 (PGIM, BNY, Nuveen EAFE; Invesco ACWI ex-US includes EM and is excluded; KKR, Amundi and Northern Trust publish only regional components), US IG corporate 6, US HY 7, cash 5. Arithmetic returns are STATED where published, otherwise DERIVED (geometric + vol^2/2; Nuveen +vol^2/4 because its convention is unstated). Northern Trust publishes no volatility (filled with the cross-house mean and flagged). Invesco's HY volatility (4.0%) is flagged as an outlier. Only two houses publish numeric correlations, for a few pairs; the covariance in the allocation tools uses the historical class series.

## Not modelled (disclosed to the owner)
Private equity/VC/credit, real estate, infrastructure, commodities, hedge funds, EM, Treasuries/TIPS, securitized, munis, global bonds, derivatives/leverage; liability matching; insurer capital charges; taxes; manager alpha (CMA returns are index-level; manager returns are net of fees).

## Build and run
```
python3 build/build_databases.py --raw "<Final Project folder>" --out .
python3 build/build_cma_db.py --raw "<Final Project folder>" --out .   # needs pdftotext/pdfinfo
python3 build/assemble_prompts.py
python3 tests/test_gateway.py
```
