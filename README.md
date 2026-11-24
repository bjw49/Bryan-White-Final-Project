# ACIO: Augmented Investment Intelligence

An AI investment committee for institutional asset owners (pensions, endowments, foundations) that hire external managers.
You describe the owner in plain English. An Artificial CIO (ACIO) infers the investment policy, a Head of Asset Allocation
and Risk proposes a five-class mix, four asset-class Heads each rank ten managers and propose a lineup, a Red Team attacks
the combined portfolio, and the ACIO writes a decision memo for a human CIO. Every number comes from code-run tools on the
data; every claim must cite a tool result; every run leaves an audit trail.

Decision support for a human CIO. Not investment advice.

## Run it (about 5 minutes from a fresh clone)

Needs Python 3.10 or newer.

1. **Key.** Put a `.env` file containing `OPENAI_API_KEY=...` (an OpenAI key) or `PORTKEY_API_KEY=...` (the course gateway) in the folder **above** this repo (or in this folder).
   The app checks this folder first, then the folder above, then each folder further up until it finds one. `.env.example` lists the names. Never commit `.env`.
2. **Install** (from inside this folder; a virtual environment is recommended):

       python -m venv .venv
       .venv\Scripts\activate          (Windows; macOS/Linux: source .venv/bin/activate)
       python -m pip install -r requirements.txt
       python tests/run_all.py          (optional check: all tests, scripted model, no key, about 10 seconds)

   On macOS/Linux use `python3` if `python` is not Python 3.
3. **Start the app:**

       python -m app.server

   Then open **http://127.0.0.1:8765** in a browser. The console says where the key was loaded from.
   The web interface is a React + TypeScript app that is already built and included (`frontend/dist`), served by a
   FastAPI back end, so you need Python only. Interactive API docs are at http://127.0.0.1:8765/docs.
4. **Use it.** Click one of the three examples (university endowment, corporate DB pension, private foundation) or type
   your own owner description and press **Send to ACIO**. The ACIO shows the policy it inferred, then the committee runs.
   If something important is missing it asks up to three questions first. The live panel shows the clock, tokens, cost
   and each agent-to-agent message. A run takes about 2 to 3 minutes; a 420-second time budget stops it otherwise.
5. **Results.** When the run ends, the decision opens below the panel: label, open items for the human CIO, allocation
   (proposed vs final), manager lineup, Red Team objections with the ACIO's answers, and the memo. The **Runs & Memos**
   tab keeps every run, with a full run page (stage timeline, tokens and cost by agent, the agent message log) and the
   asset-class pages (selected managers, next-best managers, full ranking with statistics). **Tokenomics** and
   **Safety** are tabs at the top.

No key? `python -m app.server --mock` runs the same pipeline with a scripted stand-in model in about a second. It tests the
wiring, tools and safety controls, not the judgment, and uses no tokens.

## Other commands

| Command | What it does |
|---|---|
| `python -m app.chat` | The original single-file interface (standard library only), kept as a fallback |
| `python -m app.ping` | Two live calls: checks the key, the gateway, tool calling and the prompt cache (costs a fraction of a cent) |
| `python -m app.run --owner "A foundation that..."` | One committee run from the command line (add `--mock` for no key) |
| `python -m app.evidence` | The 3 example owners plus a no-follow-ups comparison run, then safety and tokenomics (about 25 minutes) |
| `python -m app.safety` | 17 attack, malfunction and overspending scenarios against the real code; writes the Safety page |
| `python -m app.tokenomics` | Cost per run, per user and at 100 and 10,000 users, from measured live runs |
| `python tests/run_all.py` | All tests (mock model, no key, about 10 seconds) |

Output goes to `output/<run_id>/` (memo, decision, proposals, telemetry) and `audit/<run_id>_trace.md` /
`_summary.md` (every model turn, tool call and result, plus a plain-language summary per agent).

## Data

All data ships in `db/` (small SQLite files, no download needed): 40 managers (10 per class) with monthly net-of-fee USD
returns and strategy descriptions, anonymised so no manager is identifiable; class benchmarks; equity and fixed income
factor returns; capital market assumptions extracted from 7 published house reports (KKR, Invesco, Amundi, PGIM,
Northern Trust, BNY, Nuveen) with page references. The data set is already small; there is no large/small switch.
To evaluate your own managers, `build/build_databases.py` rebuilds the class databases from spreadsheets
(see `docs/README_DATA_ARCHITECTURE.md`).

## Settings (environment variables, all optional)

`AII_TIME_BUDGET_S` (420), `AII_FOLLOWUP_CUTOFF_S` (150), `AII_MAX_FOLLOWUPS` (2), `AII_TOKEN_BUDGET` (3,000,000),
`AII_COST_CAP_USD` (1.00), `AII_API` (`responses`, or `chat` for the original /chat/completions path), `AII_CACHE_MODE` (`auto`, or `explicit` for a cache breakpoint after each agent's fixed prompt), `AII_CACHE_TTL` (30m), `AII_RUNS_PER_USER_MONTH` (12, for tokenomics), `MODEL_NAME` (gpt-6-luna),
`PORTKEY_BASE_URL` (https://api.portkey.ai/v1).

## Layout

```
frontend/   React + Vite + TypeScript interface (src/), prebuilt into dist/ and served by app/server.py
app/        FastAPI server, original chat UI, orchestrator, live and scripted model clients, dashboard, safety, tokenomics, evidence
access/     data gateway (tool allowlists, read-only DBs, call budgets, injection scan), analytics, audit trail
contracts/  output schemas every agent must satisfy before anything is published
agent_prompts/  one prompt per agent (compiled/ is what the agents receive)
db/         market, CMA and one database per asset class; audit.db is append-only
tests/      gateway, audit, pipeline faults, chat, deflated-Sharpe paper check, safety
```

## Hosted version (Render)

Live at **https://acio.onrender.com/** (access code required; graders receive it with the Canvas submission).

`render.yaml` deploys the same app as one Render web service: FastAPI serves the API and the prebuilt React app.
In Render: **New > Blueprint**, pick this repo, then enter `OPENAI_API_KEY` (or `PORTKEY_API_KEY`) and an
`AII_ACCESS_CODE` when asked. The hosted app asks for the access code before it will run the committee, so the
public cannot spend the token budget; one run at a time, with the same per-run time, token and cost caps. It uses Render's free instance: the first page load after 15 idle minutes takes about a minute while the service wakes.
Run history on the hosted app is not kept across restarts (Render's disk is temporary); the local version keeps it.

## Changing the interface (optional, needs Node.js 18+)

    cd frontend
    npm install
    npm run dev        # http://localhost:5173, forwards /api calls to python -m app.server on port 8765
    npm run build      # rebuilds frontend/dist, which the server serves

## Troubleshooting

- `No PORTKEY_API_KEY found`: the console says where the search started; it walks up to the drive root. Check the file is named `.env` (not `.env.txt`).
- Port in use: `python -m app.server --port 8770`.
- A run that hits the time, token or cost budget, or produces output that fails its contract twice, stops and is shown
  as **RUN STOPPED** with the stage and reason. That is the designed behaviour: nothing unvalidated is published.
