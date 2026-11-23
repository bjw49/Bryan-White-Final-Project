# Open questions (resolved items moved to the bottom)

1. **Mgr 26 test.** Mgr 26 keeps a firm-level description and carries no cap. Read the live runs for whether the agents discount the missing process evidence in conviction and size, or ignore it when returns are good.
2. **International equity CMA.** Only 3 of 7 houses publish a developed ex-US estimate (PGIM, BNY, Nuveen). The range is correspondingly thin.
3. **Hedging policy** of the international managers and benchmark is unknown. Returns are USD.
4. **Factor definitions.** Signs and construction are undocumented; loadings are descriptive only.

## Resolved (v2.18, 2026-11-22)
- **Mgr 34 units: confirmed percent.** In `Test Rtns/31_40.xlsx` the median absolute monthly value of Mgr 34 is 1.60 against about 0.01 for Mgrs 31-33 and 35-40. The 1/100 rescale and its `UNITS_PERCENT_RESCALED` flag are correct.
- **Deflated Sharpe ratio: verified.** `tests/test_dsr_paper.py` reproduces the numerical example in Bailey and Lopez de Prado (2014) to four decimals: DSR 0.9004 at N=100, 0.9505 at N=46, and 0.9505 at N=88 with Normal returns. The app's one stated simplification: the variance of Sharpe ratios across trials is set to the series' own sampling variance, because the trials behind a track record are unknown.
- **Course key and model:** gpt-6-luna via the course Portkey key (PORTKEY_API_KEY), as the course rules require.
