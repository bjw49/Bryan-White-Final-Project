"""Local chat interface for the ACIO. Standard library only.

  python3 -m app.chat              # live model (needs PORTKEY_API_KEY in .env)
  python3 -m app.chat --mock       # scripted stand-in, runs in about a second per stage
Then open http://127.0.0.1:8765 in a browser.

Flow: the owner types an ask (or clicks one of three examples). The ACIO reads it and replies with the policy
it inferred. The user confirms and clicks Run. Progress streams into the chat and the dashboard link appears
when the run ends. Binds to 127.0.0.1 only."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXAMPLES = [
    {"id": "endowment", "label": "University endowment",
     "text": "A $2B university endowment with a perpetual horizon. Its spending rule pays out 4% a year of the 3-year "
             "average market value. It needs a 4% real total return over a full market cycle to fund that spending and "
             "preserve purchasing power: the 4% real return covers the spending, it is not in addition to it. Medium-high "
             "risk tolerance: it can accept a peak-to-trough drawdown of about 40% in a severe crisis such as 2008. It keeps "
             "at least 3% in cash to cover distributions. No private markets in this system."},
    {"id": "pension", "label": "Corporate DB pension",
     "text": "A corporate defined-benefit pension with about $3B of liabilities, 92% funded on an accounting basis. "
             "Liability hedging with Treasuries and derivatives is run in a separate portfolio, which also pays benefits. "
             "This mandate is the plan's return-seeking and credit portfolio, about 55% of plan assets. Goal: about 6% a "
             "year nominal, net of fees, to help close the funding gap, with moderate risk: a drawdown of no more than "
             "about 25% in a severe episode. Investment-grade credit is welcome as a partial liability match. No cash "
             "floor is needed here. No private markets."},
    {"id": "foundation", "label": "Private foundation (spend-down)",
     "text": "A $500M private foundation that plans to spend down over about 25 years. It pays out 6% of assets a year "
             "in grants and expenses, above the 5% legal minimum, all from this portfolio, and accepts that its real "
             "value will decline gradually over that period. Goal: about a 4% real total return so grants can stay "
             "roughly level in real terms. Moderate risk tolerance: it can accept a drawdown of about 30% in a severe "
             "crisis, and it holds about 5% in cash so a market fall does not force cuts to next year's grants. No private "
             "markets in this system."},
]

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_pending: dict[str, dict] = {}  # run_id -> {"owner": text, "policy": dict} from the policy step
_mock = False


def _make_llm():
    if _mock:
        from app.mock_llm import ScriptedLLM
        return ScriptedLLM()  # a new instance per run: the scripted stand-in keeps per-run state
    sys.path.insert(0, str(ROOT))
    from app.run import _load_env
    _load_env()
    from app.llm import LLM
    return LLM()  # raises RuntimeError with a readable message when no key is set


def _new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"


def read_ask(owner: str) -> dict:
    """ACIO step 1: infer and return the policy for confirmation. Nothing is published yet."""
    from app.orchestrator import Ctx, stage_policy, Stop
    owner = (owner or "").strip()
    if not owner:
        raise ValueError("Describe the owner first.")
    run_id = _new_run_id()
    t0 = time.monotonic()
    llm = _make_llm()
    ctx = Ctx(llm, str(ROOT), run_id)
    try:
        policy = stage_policy(ctx, owner)
    except Stop as s:
        raise RuntimeError(f"The ACIO could not read the ask: {s.detail}")
    questions = policy.pop("questions", []) or []
    # the same client carries on into the committee, so the policy call's tokens and time count in the run's totals
    _pending[run_id] = {"owner": owner, "policy": policy, "llm": llm, "t0": t0, "flags": list(ctx.flags),
                        "questions": questions, "clarified": False}
    return {"run_id": run_id, "policy": policy, "questions": questions}


def clarify(run_id: str, answers: list[str]) -> dict:
    """ACIO step 1b: the owner answers the ACIO's clarifying questions (one round). The ACIO re-infers the policy; the
    questions and answers are appended to the owner's brief, so they appear in the memo and the audit trail."""
    from app.orchestrator import Ctx, stage_policy, Stop
    pending = _pending.get(run_id)
    if not pending:
        raise ValueError("Unknown run. Read the ask again.")
    if pending.get("clarified"):
        raise ValueError("The ACIO asks one round of questions only. Run the committee.")
    qs = pending.get("questions") or []
    if not qs:
        raise ValueError("The ACIO asked no questions for this run.")
    qa = [{"question": q["question"], "answer": (str(answers[i]).strip()[:1000] if i < len(answers) else "")}
          for i, q in enumerate(qs)]
    ctx = Ctx(pending["llm"], str(ROOT), run_id)
    try:
        policy = stage_policy(ctx, pending["owner"], answers=qa)
    except Stop as s:
        raise RuntimeError(f"The ACIO could not update the policy: {s.detail}")
    policy.pop("questions", None)
    pending["owner"] += "\n\nOwner's answers to the ACIO's clarifying questions:\n" + "\n".join(
        f"- Q: {a['question']}\n  A: {a['answer'] or '(not answered; ACIO default used)'}" for a in qa)
    pending.update({"policy": policy, "clarified": True, "flags": pending.get("flags", []) + list(ctx.flags)})
    return {"run_id": run_id, "policy": policy, "answers": qa}


def start_run(run_id: str, policy: dict | None) -> str:
    """Starts the pipeline in a background thread with the confirmed policy. Returns a job id."""
    from app.orchestrator import run_pipeline
    pending = _pending.get(run_id)
    if not pending:
        raise ValueError("Unknown run. Read the ask again.")
    owner = pending["owner"]
    policy = policy or pending["policy"]
    job_id = uuid.uuid4().hex[:10]
    job = {"status": "running", "messages": [], "result": None, "started": pending.get("t0", time.monotonic()), "run_id": run_id}
    with _jobs_lock:
        _jobs[job_id] = job

    def progress(item):
        entry = {"text": item} if isinstance(item, str) else dict(item)
        entry["t"] = round(time.monotonic() - job["started"])
        job["messages"].append(entry)

    def work():
        try:
            llm = pending.get("llm") or _make_llm()
            job["llm"] = llm
            res = run_pipeline(owner, llm, str(ROOT), run_id, policy=policy, progress=progress, started_at=pending.get("t0"))
            job["result"] = {
                "status": res.get("status"), "stop": res.get("stop"), "run_id": res.get("run_id"),
                "dashboard": f"/files/dashboard/run-{run_id}.html",
                "dashboard_index": "/files/dashboard/index.html",
            }
            job["status"] = "done"
        except Exception as e:  # keep the job failure visible in the chat, never silent
            job["status"] = "error"
            job["result"] = {"error": f"{type(e).__name__}: {e}"}
        finally:
            job["elapsed"] = round(time.monotonic() - job["started"])
            _pending.pop(run_id, None)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def active_jobs() -> int:
    """Committee runs still in progress (the hosted server caps concurrent runs to protect the token budget)."""
    with _jobs_lock:
        return sum(1 for j in _jobs.values() if j["status"] == "running")


def _job_view(job_id: str) -> dict | None:
    job = _jobs.get(job_id)
    if not job:
        return None
    elapsed = job.get("elapsed", round(time.monotonic() - job["started"]))
    sp = {"tokens": 0, "cost": 0.0}
    if job.get("llm") is not None:
        from app.orchestrator import spend
        sp = spend(job["llm"])
    return {"status": job["status"], "messages": job["messages"], "result": job["result"], "elapsed": elapsed,
            "tokens": sp["tokens"], "cost_usd": round(sp["cost"], 4)}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ACIO - Augmented Investment Intelligence</title>
<style>
 :root{--bg:#05090f;--panel:#0a1420;--line:rgba(25,230,255,.22);--cy:#19e6ff;--cy2:#0a8fb3;--amb:#ffb000;--tx:#d9e6f2;--mute:#7d93a8;--red:#ff4d6d;--grn:#3dffb4}
 *{box-sizing:border-box}
 html{background:#05090f}
 body{margin:0;font:17px/1.55 "Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);min-height:100vh;
   background:radial-gradient(ellipse at 50% -10%,#0d2236 0%,#05090f 60%) fixed}
 header{background:rgba(5,9,15,.85);border-bottom:1px solid var(--line);padding:14px 22px;box-shadow:0 0 22px rgba(25,230,255,.08)}
 header h1{margin:0;font:700 18px "Segoe UI",sans-serif;letter-spacing:.22em;text-transform:uppercase;color:var(--cy);text-shadow:0 0 12px rgba(25,230,255,.55)}
 header p{margin:3px 0 0;color:var(--mute);font-size:13px}
 main{max-width:880px;margin:0 auto;padding:18px}
 .msg{position:relative;max-width:92%;padding:12px 16px;margin:10px 0;white-space:pre-wrap;background:linear-gradient(180deg,rgba(10,20,32,.95),rgba(6,12,20,.95));border:1px solid var(--line)}
 .msg::before{content:"";position:absolute;top:-1px;left:-1px;width:10px;height:10px;border:2px solid var(--cy);border-right:0;border-bottom:0}
 .me{margin-left:auto;border-color:rgba(255,176,0,.45)}
 .me::before{border-color:var(--amb)}
 .who{font:600 11px "Segoe UI",sans-serif;letter-spacing:.2em;text-transform:uppercase;color:var(--cy);margin-bottom:4px}
 .me .who{color:var(--amb)}
 .examples{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 12px}
 button{font:600 12px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase;border:1px solid var(--cy);background:linear-gradient(180deg,var(--cy),var(--cy2));color:#001018;padding:9px 16px;cursor:pointer;box-shadow:0 0 14px rgba(25,230,255,.35)}
 button.alt{background:rgba(0,0,0,.35);color:var(--cy);box-shadow:none}
 button.alt:hover{background:rgba(25,230,255,.12)}
 button:disabled{opacity:.4;cursor:default;box-shadow:none}
 textarea{width:100%;min-height:96px;font:inherit;color:var(--tx);padding:12px;background:rgba(0,0,0,.5);border:1px solid var(--line);resize:vertical}
 textarea:focus{outline:none;border-color:var(--cy);box-shadow:0 0 12px rgba(25,230,255,.3)}
 .bar{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
 .log{font:12px ui-monospace,Consolas,monospace;color:#9fd8e6}
 .log div{padding:2px 0}
 .cast{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:6px 0 16px}
 .card{position:relative;padding:10px 12px;background:linear-gradient(180deg,rgba(10,20,32,.95),rgba(6,12,20,.95));border:1px solid var(--line)}
 .card::before{content:"";position:absolute;top:-1px;left:-1px;width:10px;height:10px;border:2px solid var(--cy);border-right:0;border-bottom:0}
 .card b{display:block;color:var(--amb);font:700 14px "Segoe UI",sans-serif;letter-spacing:.04em}
 .card small{display:block;color:var(--cy);font:600 10.5px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase;margin-top:2px}
 .card span{display:block;color:var(--mute);font-size:11.5px;margin-top:4px}
 .wire{padding:2px 0}.wire .f{color:var(--cy);font-weight:600}.wire .t{color:var(--amb);font-weight:600}
 .disc{color:var(--mute);font-size:11.5px;margin:4px 0 14px}
 .card{display:flex;gap:10px;align-items:center}
 .card .txt{min-width:0}
 .av{flex:0 0 auto;width:54px;height:54px}
 .av .bob{animation:bob 3.2s ease-in-out infinite;transform-origin:32px 40px}
 .av .scan{animation:scan 2.6s linear infinite}
 .card.live{animation:glow 1s ease-in-out 2}
 @keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}
 @keyframes scan{0%{transform:translateY(-18px)}100%{transform:translateY(42px)}}
 .av .nod{animation:nod 2.4s ease-in-out infinite;transform-origin:32px 26px}
 .av .ring{transform-origin:32px 26px;animation:ring 2.4s ease-out infinite}
 .av .ring.r2{animation-delay:1.2s}
 .av .bar{transform-origin:bottom;animation:bars 1.6s ease-in-out infinite}
 .av .b2{animation-delay:.2s}.av .b3{animation-delay:.4s}.av .b4{animation-delay:.6s}
 .av .sweep{animation:sweep 3s ease-in-out infinite}
 .av .spin{animation:spin 6s linear infinite}
 .av .draw{stroke-dasharray:100;animation:draw 3s ease-in-out infinite}
 .av .dart{transform-origin:32px 32px;animation:dart 1.4s ease-in-out infinite}
 .av .flip{transform-origin:32px 32px;animation:flip 2.8s ease-in-out infinite}
 @keyframes nod{0%,100%{transform:translateY(0)}50%{transform:translateY(3px)}}
 @keyframes ring{0%{opacity:.9;transform:scale(1)}100%{opacity:0;transform:scale(3.2)}}
 @keyframes bars{0%,100%{transform:scaleY(.55)}50%{transform:scaleY(1)}}
 @keyframes sweep{0%,100%{transform:translate(-14px,0)}50%{transform:translate(4px,4px)}}
 @keyframes spin{to{transform:rotate(360deg)}}
 @keyframes draw{0%{stroke-dashoffset:100}60%,100%{stroke-dashoffset:0}}
 @keyframes dart{0%,100%{transform:translateY(-4px)}50%{transform:translateY(4px)}}
 @keyframes flip{0%,40%{transform:scaleX(1)}50%,90%{transform:scaleX(-1)}100%{transform:scaleX(1)}}
 @keyframes glow{0%,100%{box-shadow:0 0 0 rgba(25,230,255,0)}50%{box-shadow:0 0 18px rgba(25,230,255,.75)}}
 table{border-collapse:collapse;width:100%;font:12.5px ui-monospace,Consolas,monospace;margin-top:8px;font-variant-numeric:tabular-nums}
 th,td{border-bottom:1px solid rgba(25,230,255,.10);padding:6px 8px;text-align:left;vertical-align:top}
 th{color:var(--mute);font:600 10.5px "Segoe UI",sans-serif;letter-spacing:.16em;text-transform:uppercase;width:30%}
 td{color:var(--amb)}
 .ok{color:var(--grn)} .bad{color:var(--red)}
 .note{font-size:12.5px;color:var(--mute)}
 a{color:var(--cy)}
 .strip{display:flex;gap:6px;margin:8px 0 10px;flex-wrap:wrap}
 .strip div{flex:1 1 110px;padding:7px 8px;border:1px solid var(--line);font:600 10.5px "Segoe UI",sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);background:rgba(0,0,0,.35);text-align:center}
 .strip .on{color:#001018;background:linear-gradient(180deg,var(--cy),var(--cy2));box-shadow:0 0 14px rgba(25,230,255,.45)}
 .strip .done{color:var(--grn);border-color:rgba(61,255,180,.45)}
 .tick{display:flex;gap:22px;font:700 22px ui-monospace,Consolas,monospace;color:var(--amb);text-shadow:0 0 10px rgba(255,176,0,.4);font-variant-numeric:tabular-nums}
 .tick small{display:block;font:600 10px "Segoe UI",sans-serif;letter-spacing:.16em;color:var(--mute);text-shadow:none}
 nav.top{float:right;margin-top:-30px}
 nav.top a{margin-left:16px;font:600 12px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase;text-decoration:none}
</style></head>
<body>
<header><h1>ACIO: Augmented Investment Intelligence</h1><p>Describe the owner. The ACIO reads the ask, shows its policy, then runs the committee.</p>
<nav class="top"><a href="/files/dashboard/index.html" target="_blank">Dashboard</a><a href="/files/dashboard/tokenomics.html" target="_blank">Tokenomics</a><a href="/files/dashboard/safety.html" target="_blank">Safety</a></nav></header>
<main>
 <div class="cast" id="cast"></div>
 <div class="disc">Original characters, named for the desk each agent runs. Their animations match their roles. The reasoning comes from each agent's prompt, tools and data.</div>
 <div class="examples" id="examples"></div>
 <div id="chat"></div>
 <textarea id="ask" placeholder="Describe the asset owner: what it is, its return target or liabilities, spending or cash needs, risk tolerance, horizon, and any constraints."></textarea>
 <div class="bar">
   <button id="send">Send to ACIO</button>
   <span class="note" id="hint">Runs locally. Output is written to the dashboard folder.</span>
 </div>
</main>
<script>
const chat = document.getElementById('chat'), ask = document.getElementById('ask'), send = document.getElementById('send');
let state = {runId: null, policy: null, busy: false};
function bubble(who, html, cls){
  const d = document.createElement('div'); d.className = 'msg ' + (cls||'');
  const w = document.createElement('div'); w.className = 'who'; w.textContent = who;
  d.appendChild(w); const b = document.createElement('div'); b.innerHTML = html; d.appendChild(b);
  chat.appendChild(d); window.scrollTo(0, document.body.scrollHeight); return b;
}
const CAST = {
  acio:{name:"The Chairman", role:"Acting CIO", src:"Final say on allocation and selection"},
  aar:{name:"The Quant", role:"Asset Allocation & Risk", src:"Proposes the mix and ranges"},
  head_us_eq:{name:"The Stock Picker", role:"Head of US Equity", src:"Ranks US equity managers"},
  head_intl_eq:{name:"The Globe Trotter", role:"Head of International Equity", src:"Ranks international managers"},
  head_us_ig:{name:"The Rates Desk", role:"Head of US IG Fixed Income", src:"Ranks investment-grade managers"},
  head_us_hy:{name:"The Credit Hawk", role:"Head of US High Yield", src:"Ranks high-yield managers"},
  red_team:{name:"The Prosecutor", role:"Red Team", src:"Challenges the integrated portfolio"},
  owner:{name:"Owner (you)", role:"Asset owner", src:"Your ask"}
};
function who(k){ const c = CAST[k]; return c ? c.name : esc(k); }
const TINT = {acio:'#ffb000', aar:'#19e6ff', head_us_eq:'#3dffb4', head_intl_eq:'#19e6ff', head_us_ig:'#7dd3fc', head_us_hy:'#ff8a5b', red_team:'#ff4d6d', owner:'#d9e6f2'};
function avatar(k){
  // Original illustrations drawn in code. Each animation matches the character's name and desk.
  const t = TINT[k] || '#19e6ff';
  const frame = (inner) => `<svg class="av" viewBox="0 0 64 64" aria-hidden="true"><rect x="1" y="1" width="62" height="62" fill="#06101b" stroke="${t}" stroke-opacity=".5"/>${inner}</svg>`;
  const head = (cx, cy) => `<circle cx="${cx}" cy="${cy}" r="8" fill="#0a1420" stroke="${t}" stroke-width="1.5"/>`;
  switch (k) {
    case "acio": // The Chairman: rings expand from the desk, head nods in charge
      return frame(`<g class="nod"><circle cx="32" cy="26" r="6" fill="${t}" fill-opacity=".8"/></g>
        <circle class="ring r1" cx="32" cy="26" r="8" fill="none" stroke="${t}"/>
        <circle class="ring r2" cx="32" cy="26" r="8" fill="none" stroke="${t}"/>
        <path d="M14 60 Q32 40 50 60 Z" fill="${t}" fill-opacity=".6"/>`);
    case "aar": // The Quant: bars rise and fall like a risk model
      return frame(`<rect class="bar b1" x="12" y="20" width="7" height="36" fill="${t}"/>
        <rect class="bar b2" x="23" y="10" width="7" height="46" fill="${t}"/>
        <rect class="bar b3" x="34" y="28" width="7" height="28" fill="${t}"/>
        <rect class="bar b4" x="45" y="16" width="7" height="40" fill="${t}"/>`);
    case "head_us_eq": // The Stock Picker: a magnifier sweeps the screen for names
      return frame(`${head(30, 24)}<path d="M14 60 Q30 40 46 60 Z" fill="${t}" fill-opacity=".6"/>
        <g class="sweep"><circle cx="46" cy="30" r="7" fill="none" stroke="${t}" stroke-width="2.5"/><line x1="51" y1="35" x2="56" y2="40" stroke="${t}" stroke-width="2.5"/></g>`);
    case "head_intl_eq": // The Globe Trotter: the globe turns
      return frame(`<g class="spin" style="transform-origin:32px 32px"><circle cx="32" cy="32" r="16" fill="none" stroke="${t}" stroke-width="1.5"/>
        <ellipse cx="32" cy="32" rx="7" ry="16" fill="none" stroke="${t}"/><line x1="16" y1="32" x2="48" y2="32" stroke="${t}"/></g>`);
    case "head_us_ig": // The Rates Desk: a yield curve draws itself
      return frame(`<path class="draw" d="M8 48 Q20 40 32 30 T56 16" fill="none" stroke="${t}" stroke-width="2.5" pathLength="100"/>
        <line x1="8" y1="54" x2="56" y2="54" stroke="${t}" stroke-opacity=".5"/>`);
    case "head_us_hy": // The Credit Hawk: an arrow darts up and down
      return frame(`<path class="dart" d="M32 8 L44 28 L36 28 L36 56 L28 56 L28 28 L20 28 Z" fill="${t}" fill-opacity=".85"/>`);
    case "red_team": // The Prosecutor: a question mark flips to challenge the case
      return frame(`<text class="flip" x="32" y="46" text-anchor="middle" font-size="40" font-weight="700" fill="${t}" font-family="Segoe UI,sans-serif">?</text>`);
    default: // Owner: a steady, quiet figure
      return frame(`<g class="bob">${head(32, 26)}<path d="M14 60 Q32 40 50 60 Z" fill="${t}" fill-opacity=".6"/></g>`);
  }
}
function renderCast(){
  document.getElementById('cast').innerHTML = Object.entries(CAST).map(([k,c]) =>
    `<div class="card" data-k="${k}">${avatar(k)}<div class="txt"><b>${esc(c.name)}</b><small>${esc(c.role)}</small><span>${esc(c.src)}</span></div></div>`).join('');
}
function highlight(k){
  document.querySelectorAll(`.card[data-k="${k}"]`).forEach(el => {
    el.classList.remove('live'); void el.offsetWidth; el.classList.add('live');
    setTimeout(() => el.classList.remove('live'), 2200);
  });
}
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function policyHtml(p){
  const rows = ['owner_type','objective','horizon','risk_tolerance','constraints'].map(k =>
    `<tr><th>${esc(k.replace('_',' '))}</th><td>${esc(p[k])}</td></tr>`).join('');
  return `<div>The ACIO's reading of the ask:</div><table>${rows}</table>
  <div class="note">Running the committee on this policy now.</div>`;
}
async function api(path, body){
  const r = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'Content-Type':'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json(); if(!r.ok) throw new Error(j.error || r.statusText); return j;
}
function setBusy(b){ state.busy = b; send.disabled = b; }
async function readAsk(text){
  bubble('You', esc(text), 'me');
  setBusy(true);
  const thinking = bubble('ACIO', 'Reading the ask...');
  try{
    const r = await api('/api/policy', {owner: text});
    state = {runId: r.run_id, policy: r.policy, busy: false};
    thinking.innerHTML = policyHtml(r.policy);
    await runCommittee();
  }catch(e){ thinking.innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  finally{ setBusy(false); }
}
async function runCommittee(){
  const STAGES = [['Policy','reading the owner'],['AAR + 4 Heads','working in parallel'],['ACIO review','reviewing the lineups'],['Red Team','Red Team is challenging'],['Decision','writing the final decision'],['Memo','Decision written']];
  const panel = bubble('ACIO', '<div class="tick"><div><small>Elapsed</small><span id="tk-t">0:00</span></div><div><small>Tokens</small><span id="tk-n">0</span></div><div><small>Cost (USD)</small><span id="tk-c">$0.0000</span></div></div>'
    + '<div class="strip" id="strip">' + STAGES.map((s,i)=>`<div data-i="${i}" class="${i===0?'done':''}">${s[0]}</div>`).join('') + '</div>'
    + '<div class="log" id="log"></div><div id="res" class="note">Running. Typical runs take several minutes; the limit is 10.</div>');
  const log = panel.querySelector('#log'); let seen = 0;
  const setStage = txt => { const i = STAGES.findIndex(s => txt.includes(s[1])); if(i < 0) return;
    panel.querySelectorAll('#strip div').forEach(d => { const k = +d.dataset.i; d.className = k < i ? 'done' : (k === i ? 'on' : ''); });
    if(i === STAGES.length-1) panel.querySelectorAll('#strip div').forEach(d => d.className = 'done'); };
  const tick = j => { const m = Math.floor(j.elapsed/60), s = j.elapsed%60;
    panel.querySelector('#tk-t').textContent = m + ':' + String(s).padStart(2,'0');
    panel.querySelector('#tk-n').textContent = (j.tokens||0).toLocaleString();
    panel.querySelector('#tk-c').textContent = '$' + (j.cost_usd||0).toFixed(4); };
  const line = m => {
    if (m.from) { highlight(m.from); highlight(m.to); }
    const d = document.createElement('div');
    if (m.from) { d.className = 'wire'; d.innerHTML = `[${String(m.t).padStart(3,'0')}s] <span class="f">${esc(who(m.from))}</span> &rarr; <span class="t">${esc(who(m.to))}</span>: ${esc(m.text)}`; }
    else { d.textContent = `[${String(m.t).padStart(3,'0')}s] ${m.text}`; }
    return d;
  };
  setBusy(true);
  try{
    const {job_id} = await api('/api/run', {run_id: state.runId, policy: state.policy});
    while(true){
      await new Promise(r => setTimeout(r, 1500));
      const j = await api('/api/job/' + job_id);
      for(; seen < j.messages.length; seen++){ const m = j.messages[seen]; if(!m.from) setStage(m.text); log.appendChild(line(m)); }
      tick(j);
      if(j.status !== 'running'){
        const res = panel.querySelector('#res');
        if(j.status === 'error'){ res.innerHTML = `<span class="bad">Run failed: ${esc(j.result.error)}</span>`; }
        else {
          const r = j.result; const ok = (r.status === 'RUN_STOPPED' || r.status === 'ESCALATED') ? 'bad' : 'ok';
          res.innerHTML = `<div class="${ok}">Status: ${esc(r.status)} ${r.stop ? '(stopped at ' + esc(r.stop.stage) + ': ' + esc(r.stop.detail) + ')' : ''}</div>
            <div>Elapsed: ${j.elapsed} s of the 600 s limit &middot; ${(j.tokens||0).toLocaleString()} tokens &middot; $${(j.cost_usd||0).toFixed(4)}. Run id: ${esc(r.run_id)}</div>
            <div style="margin-top:6px"><a href="${r.dashboard}" target="_blank">Open this run on the dashboard</a> &middot; <a href="${r.dashboard_index}" target="_blank">All runs and asset-class pages</a></div>`;
        }
        break;
      }
    }
  }catch(e){ panel.querySelector('#res').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  finally{ setBusy(false); }
}
send.onclick = () => { const t = ask.value.trim(); if(t && !state.busy){ ask.value=''; readAsk(t); } };
ask.addEventListener('keydown', e => { if(e.key==='Enter' && (e.ctrlKey||e.metaKey)) send.click(); });
(async () => {
  const ex = await api('/api/examples');
  const box = document.getElementById('examples');
  ex.forEach(x => { const b = document.createElement('button'); b.className='alt'; b.textContent = 'Example: ' + x.label;
    b.onclick = () => { if(!state.busy) readAsk(x.text); }; box.appendChild(b); });
  renderCast();
  bubble('ACIO', 'Describe the asset owner in plain English, or pick an example. I will read the ask, show the policy I inferred, and run the committee.');
})();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console for the run log
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, rel: str, base_dir: str = "output"):
        base = (ROOT / base_dir).resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            return self._json(404, {"error": "not found"})
        ctype = {".html": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8",
                 ".json": "application/json", ".txt": "text/plain; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/examples":
            return self._json(200, EXAMPLES)
        if path.startswith("/api/job/"):
            view = _job_view(path.rsplit("/", 1)[-1])
            return self._json(200, view) if view else self._json(404, {"error": "unknown job"})
        if path.startswith("/files/"):
            return self._file(path[len("/files/"):])
        if path.startswith("/audit/"):  # dashboard links to ../../audit/<run>_summary.md
            return self._file(path[len("/audit/"):], "audit")
        if path in ("/dashboard", "/dashboard/"):
            self.send_response(302); self.send_header("Location", "/files/dashboard/index.html"); self.end_headers(); return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if path == "/api/policy":
                return self._json(200, read_ask(body.get("owner", "")))
            if path == "/api/run":
                return self._json(200, {"job_id": start_run(body.get("run_id", ""), body.get("policy"))})
            self._json(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def make_server(port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None) -> int:
    global _mock
    ap = argparse.ArgumentParser(description="ACIO chat interface")
    ap.add_argument("--mock", action="store_true", help="Scripted stand-in model (tests wiring, not judgment)")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    _mock = a.mock
    sys.path.insert(0, str(ROOT))
    if not _mock:
        import os
        from app.run import _load_env
        found = _load_env()
        if not (os.environ.get("PORTKEY_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            print("No PORTKEY_API_KEY found. Searched for .env from " + found["searched"][0] + " up to the drive root." +
                  "\nPut .env in this folder or any folder above it, or start with --mock to try the app without a key.")
            return 1
        print(f"Key loaded from {found['loaded'] or 'environment'}.")
    srv = make_server(a.port)
    print(f"ACIO chat running at http://127.0.0.1:{a.port}  ({'mock' if _mock else 'live'} model). Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
