"""FastAPI back end for the ACIO (MGT 409 lecture 7 stack: React + Vite + TypeScript front end, FastAPI back end).

  python -m app.server            # live model (needs PORTKEY_API_KEY or OPENAI_API_KEY in .env)
  python -m app.server --mock     # scripted stand-in, no key, no tokens
Then open http://127.0.0.1:8765.

The React front end is prebuilt into frontend/dist and committed, so running locally needs only Python (no Node).
Hosted (Render): uvicorn app.server:app --host 0.0.0.0 --port $PORT, with the key and AII_ACCESS_CODE set in the host's
environment settings. When AII_ACCESS_CODE is set, every /api call that can spend tokens must send it in the
X-Access-Code header; the public cannot run the committee on the owner's key.

Routes: GET /api/health, GET /api/config, GET /api/examples, POST /api/policy, POST /api/clarify, POST /api/run, GET /api/job/{id};
static: /files/* (output folder: dashboard pages), /audit/* (audit summaries), / (React app). Interactive API docs
at /docs. The pipeline, job handling and examples are the same code as the original standard-library server
(app/chat.py, kept as a fallback)."""
from __future__ import annotations

import argparse
import hmac
import os
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import chat as core  # noqa: E402  (job store, examples, read_ask, start_run, _job_view)

DIST = ROOT / "frontend" / "dist"
# When started directly by uvicorn (hosted), AII_MOCK=1 selects the scripted model; main() sets it from --mock locally.
core._mock = os.environ.get("AII_MOCK") == "1"
(ROOT / "output").mkdir(exist_ok=True)
(ROOT / "audit").mkdir(exist_ok=True)


def _ensure_dashboard():
    """A fresh clone (or a new Render instance) has no output yet: build the dashboard pages once at startup so the
    Dashboard, Tokenomics and Safety links work before the first run."""
    if not (ROOT / "output" / "dashboard" / "index.html").is_file():
        try:
            from app.dashboard import build
            build(str(ROOT))
        except Exception as e:  # never block startup on the dashboard
            print(f"dashboard not built at startup: {type(e).__name__}: {e}")


_ensure_dashboard()


class PolicyRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=4000, description="Plain-English description of the asset owner")


class ClarifyRequest(BaseModel):
    run_id: str = Field(..., pattern=r"^run-\d{8}T\d{6}-[0-9a-f]{4}$")
    answers: list[str] = Field(..., max_length=3, description="Answers in the order of the ACIO's questions; empty = use its default")


class RunRequest(BaseModel):
    run_id: str = Field(..., pattern=r"^run-\d{8}T\d{6}-[0-9a-f]{4}$")
    policy: dict | None = None


def access_code() -> str:
    return os.environ.get("AII_ACCESS_CODE", "")


def require_code(x_access_code: str | None = Header(default=None)):
    """Spending routes require the access code when one is configured (hosted mode). Constant-time comparison."""
    code = access_code()
    if code and not hmac.compare_digest((x_access_code or "").encode(), code.encode()):
        raise HTTPException(status_code=401, detail="Access code required. Ask the app owner for the code.")


app = FastAPI(title="ACIO: Augmented Investment Intelligence", version="3.0",
              description="AI investment committee for institutional asset owners. Decision support, not investment advice.")
# The Vite dev server (npm run dev) runs on port 5173 and calls this API on 8765: allow that origin in development.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Access-Code"])


@app.exception_handler(ValueError)
@app.exception_handler(RuntimeError)
async def _bad_request(_req, exc):  # same behaviour as the original server: readable 400s for user errors
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(RequestValidationError)
async def _invalid(_req, exc):  # one error shape for the front end: {"error": "..."}
    msg = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in exc.errors())
    return JSONResponse(status_code=422, content={"error": msg or "invalid request"})


@app.exception_handler(HTTPException)
async def _http_error(_req, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {"mock": core._mock, "access_code_required": bool(access_code()),
            "model": os.environ.get("MODEL_NAME", "gpt-6-luna"), "api": os.environ.get("AII_API", "responses"),
            "time_budget_s": _time_budget()}


def _time_budget() -> float:
    from app import orchestrator
    return orchestrator.TIME_BUDGET_S


@app.get("/api/examples")
def examples():
    return core.EXAMPLES


@app.post("/api/policy", dependencies=[Depends(require_code)])
def policy(body: PolicyRequest):
    return core.read_ask(body.owner)


@app.post("/api/clarify", dependencies=[Depends(require_code)])
def clarify(body: ClarifyRequest):
    return core.clarify(body.run_id, body.answers)


@app.post("/api/run", dependencies=[Depends(require_code)])
def run(body: RunRequest):
    if core.active_jobs() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(status_code=429, detail=f"{MAX_CONCURRENT_RUNS} committee run(s) already in progress. Try again when it ends.")
    return {"job_id": core.start_run(body.run_id, body.policy)}


@app.get("/api/job/{job_id}")
def job(job_id: str):
    view = core._job_view(job_id)
    if not view:
        raise HTTPException(status_code=404, detail="unknown job")
    return view


RUN_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$"


def _run_dir(run_id: str) -> Path:
    import re as _re
    if not _re.match(RUN_ID, run_id) or run_id == "dashboard":
        raise HTTPException(status_code=404, detail="unknown run")
    d = ROOT / "output" / run_id
    if not (d / "memo.md").is_file():
        raise HTTPException(status_code=404, detail="unknown run")
    return d


@app.get("/api/runs")
def runs():
    """Every finished run, newest first: decision (or stop), owner type, cost, time. Read-only."""
    from app.dashboard import load_run, run_dirs, status_of
    out = []
    for d in run_dirs(ROOT):
        r = load_run(d)
        label, kind = status_of(r)
        tel, dec = r["tel"] or {}, r["dec"] or {}
        out.append({"run_id": r["id"], "status": label, "stopped": kind == "esc", "label": tel.get("label"),
                    "owner_type": (dec.get("policy") or {}).get("owner_type"), "finished_utc": tel.get("finished_utc"),
                    "cost_usd": tel.get("cost_usd"), "elapsed_s": tel.get("elapsed_s"), "mock": tel.get("mock"),
                    "tokens": (tel.get("tokens") or {}).get("total")})
    out.sort(key=lambda x: x.get("finished_utc") or x["run_id"], reverse=True)
    return out


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    """One run for the in-app decision view: decision, allocation, lineup, Red Team, flags, telemetry, memo."""
    from app.dashboard import load_run, status_of
    r = load_run(_run_dir(run_id))
    label, kind = status_of(r)
    tel = r["tel"] or {}
    return {"run_id": r["id"], "status": label, "stopped": kind == "esc", "stop": r["esc"], "decision": r["dec"],
            "red_team": r["rt"], "flags": r["flags"], "memo": r["memo"],
            "telemetry": {k: tel.get(k) for k in ("label", "route", "elapsed_s", "model_calls", "tokens", "cost_usd", "finished_utc", "mock")},
            "dashboard": f"/files/dashboard/run-{r['id']}.html"}


@app.get("/dashboard")
def dashboard():
    return RedirectResponse("/files/dashboard/index.html")


MAX_CONCURRENT_RUNS = int(os.environ.get("AII_MAX_CONCURRENT_RUNS", "2"))

# Static mounts. StaticFiles refuses paths that escape its directory.
app.mount("/files", StaticFiles(directory=ROOT / "output", check_dir=False), name="files")
app.mount("/audit", StaticFiles(directory=ROOT / "audit", check_dir=False), name="audit")
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
def index():
    page = DIST / "index.html"
    if not page.is_file():
        return JSONResponse(status_code=503, content={"error": "Front end not built. Run: cd frontend && npm install && npm run build"})
    return FileResponse(page)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ACIO web app (FastAPI + React)")
    ap.add_argument("--mock", action="store_true", help="Scripted stand-in model (tests wiring, not judgment)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    a = ap.parse_args(argv)
    core._mock = a.mock or os.environ.get("AII_MOCK") == "1"
    if not core._mock:
        from app.run import _load_env
        found = _load_env()
        if not (os.environ.get("PORTKEY_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            print("No PORTKEY_API_KEY or OPENAI_API_KEY found. Searched for .env from " + found["searched"][0] +
                  " up to the drive root.\nPut .env in this folder or any folder above it, or start with --mock.")
            return 1
        print(f"Key loaded from {found['loaded'] or 'environment'}.")
    import uvicorn
    print(f"ACIO running at http://{a.host}:{a.port}  ({'mock' if core._mock else 'live'} model). API docs at /docs. Ctrl+C to stop.")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
