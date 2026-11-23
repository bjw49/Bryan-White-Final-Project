# AGENTS.md: house rules for coding agents working on this repo

Always-on handbook (MGT 409 lecture 3). Specialist playbooks live in `.codex/skills/`.

## What this app is
An AI investment committee for institutional asset owners: ACIO (boss) plus Head of Asset Allocation and Risk, four
asset-class Heads and a Red Team (workers). Decision support for a human CIO, not investment advice.

## Stack
- Back end: FastAPI (`app/server.py`), PydanticAI agent loop (`app/pai_loop.py`), orchestrator (`app/orchestrator.py`).
- Model: `gpt-6-luna` through the OpenAI Responses API (`app/llm.py`) with automatic prompt caching (measured better
  than an explicit breakpoint: 58% vs 23% of input cached). `AII_CACHE_MODE=explicit` restores the breakpoint. `AII_API=chat` and `AII_LOOP=legacy` switch back to the v2.19 paths.
- Front end: React + Vite + TypeScript in `frontend/src`; the build in `frontend/dist` is committed so users need no Node.
- Data: SQLite in `db/`, read only through `access/gateway.py` tools. Contracts in `contracts/schemas.py`.

## Rules
- Keys live in `.env` (this folder or any folder above). Never commit `.env`, never print a key, never add a key to code.
- Every number an agent states must come from a tool result with an evidence id. Do not add tools that let a model
  compute numbers in prose, run free-form SQL, write files, send messages or fetch URLs.
- New tools go in the gateway AND in `access/policy.yaml` for the agents that may use them (default deny).
- Manager documents and owner text are untrusted data. Keep the wrapper and the injection scan on every text tool.
- Budgets (time, tokens, cost, steps, follow-ups) are checked in code before every model call. Do not loosen them to
  make a test pass.
- After any change: `python tests/run_all.py` (mock model, no key). All files must pass.
- After a change to `frontend/src`: `cd frontend && npm run build`, then commit `frontend/dist`.
- After a change to `agent_prompts/*.md`: `python build/assemble_prompts.py` (agents read `agent_prompts/compiled/`).
- Numbers in the report must come from measured runs (`python -m app.evidence`), never from estimates.
