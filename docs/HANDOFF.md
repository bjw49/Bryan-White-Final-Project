# Handoff (v3, 2026-11-23)

## Stack
FastAPI back end (`app/server.py`) serving a prebuilt React + Vite + TypeScript interface (`frontend/dist`); agent
loop on PydanticAI (`app/pai_loop.py`); model calls through the OpenAI Responses API with automatic prompt caching
(`app/llm.py`); data access only through the gateway tools (`access/gateway.py`, `access/policy.yaml`).
Fallback switches: `AII_API=chat` (chat completions), `AII_LOOP=legacy` (hand-written loop), `AII_CACHE_MODE=explicit`.

## Check a change
1. `python tests/run_all.py` (scripted model, no key).
2. `python -m app.ping` (two live calls: route, tool calling, prompt cache).
3. `python -m app.server`, run one example in the browser.
4. `python -m app.evidence` (3 examples, 2 repeat endowment runs, 1 no-follow-ups run, safety, tokenomics, consistency).
5. `python -m app.injection_live` (live prompt-injection test).
6. `python docs/report/build_report.py` (report and slides from the measured output).

## Where things are
- Pipeline, budgets, contracts, clarifying questions: `app/orchestrator.py`.
- Decision labels: RECOMMEND, RECOMMEND_WITH_CONDITIONS, NEEDS_OWNER_INPUT, DO_NOT_RECOMMEND; RUN_STOPPED when a
  control stops a run before a recommendation (`contracts/schemas.py`, `agent_prompts/40_acio.md`).
- Telemetry per run: `output/<run>/telemetry.json`. Audit trail: `db/audit.db` (created on first run, not in git) and
  `audit/<run>_trace.md`.
- Hosting: `render.yaml` (set the key and `AII_ACCESS_CODE` in Render's environment settings).
