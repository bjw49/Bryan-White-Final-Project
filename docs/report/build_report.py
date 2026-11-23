"""Builds report.html and slides.html (single self-contained files, images embedded) at the repo root from the
measured outputs in output/ and audit/, the screenshots in docs/report/shots/, and the pilot facts in pilot.json.

  python docs/report/build_report.py            # build from existing screenshots
  python docs/report/build_report.py --shots    # re-capture screenshots first (pip install playwright; playwright install chromium)

Anything the evidence does not yet support is shown as a yellow TO FILL box and listed at the end of the console output.
Numbers are never typed into this file: they are read from output/tokenomics.json, output/safety.json and the run files."""
from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
sys.path.insert(0, str(ROOT))
TODO: list[str] = []


def e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def todo(msg: str) -> str:
    TODO.append(msg)
    return f"<span class='todo'>TO FILL: {e(msg)}</span>"


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def usd(x, d=4) -> str:
    x = float(x or 0)
    return f"${x:,.{d}f}" if x < 1 else f"${x:,.{min(d, 2)}f}"


def mmss(s) -> str:
    s = int(round(float(s or 0)))
    return f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- data
def live_runs() -> list[dict]:
    out = []
    for p in sorted((ROOT / "output").glob("*/telemetry.json"), key=lambda p: p.stat().st_mtime):
        t = load(p)
        if t and not t.get("mock") and t.get("model_calls"):
            t["_dir"] = p.parent
            out.append(t)
    return out


def pick_trace_run(runs: list[dict]) -> dict | None:
    """The run traced in the report: the latest completed live endowment run, else the latest completed live run."""
    done = [r for r in runs if r.get("status") not in (None, "RUN_STOPPED", "ESCALATED")]
    for pool in ([r for r in done if (r.get("label") or "").startswith("endowment") and "no-followups" not in (r.get("label") or "")], done, runs):
        if pool:
            return pool[-1]
    return None


def latest_any_run() -> Path | None:
    ds = [d for d in (ROOT / "output").iterdir() if d.is_dir() and (d / "memo.md").exists()] if (ROOT / "output").exists() else []
    return max(ds, key=lambda d: d.stat().st_mtime) if ds else None


# --------------------------------------------------------------------------- screenshots
def capture(run_dir: Path | None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install playwright && python -m playwright install chromium"); return
    from app import dashboard
    dashboard.build(ROOT)
    SHOTS.mkdir(parents=True, exist_ok=True)
    dash = ROOT / "output" / "dashboard"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)

        def section(url, start, stop, name, maxh=1400):
            pg.goto(url); pg.wait_for_timeout(600)
            a = pg.locator(f"h2:text-is('{start}')").first.bounding_box()
            z = pg.locator(f"h2:text-is('{stop}')").first.bounding_box() if stop else None
            if not a:
                return
            y0 = a["y"] - 6; y1 = (z["y"] - 8) if z else y0 + maxh
            pg.screenshot(path=str(SHOTS / name), full_page=True, clip={"x": 0, "y": y0, "width": 1440, "height": min(y1 - y0, maxh)})

        if run_dir is not None:
            url = (dash / f"run-{run_dir.name}.html").as_uri()
            pg.goto(url); pg.wait_for_timeout(800)
            pg.screenshot(path=str(SHOTS / "run_top.png"), clip={"x": 0, "y": 70, "width": 1440, "height": 400})
            section(url, "Stage timeline", "Asset allocation", "run_timeline.png")
            section(url, "Asset allocation", "Red Team", "run_alloc.png")
            section(url, "Red Team", "Data and process flags", "run_redteam.png")
            section(url, "Data and process flags", "Agent messages", "run_flags.png", 700)
            pg.goto(url); pg.wait_for_timeout(26000)  # let the beam replay finish
            net = pg.locator("svg.net").first
            if net.count():
                net.screenshot(path=str(SHOTS / "run_beams.png"))
            section(url, "Agent messages", "Tokens by agent", "run_wire.png", 1500)
            section(url, "Tokens by agent", "Investment memo", "run_agents.png")
            section(url, "Investment memo", "Audit trail", "run_memo.png", 1300)
            summ = ROOT / "audit" / f"{run_dir.name}_summary.md"
            if summ.exists():
                tmp = dash / "_audit_preview.html"
                tmp.write_text(dashboard._page("Audit summary", "<h1>Audit summary</h1>" + dashboard.md_to_html(summ.read_text(encoding="utf-8"))), encoding="utf-8")
                pg.goto(tmp.as_uri()); pg.wait_for_timeout(400)
                pg.screenshot(path=str(SHOTS / "audit_summary.png"), clip={"x": 0, "y": 60, "width": 1440, "height": 1100})
                tmp.unlink()
            cls_url = (dash / "class-US_EQ.html").as_uri()
            pg.goto(cls_url); pg.wait_for_timeout(400)
            pg.screenshot(path=str(SHOTS / "class_us_eq.png"), clip={"x": 0, "y": 0, "width": 1440, "height": 1000})
            section(cls_url, "Standalone ranking, all managers", "The committee", "class_ranking.png", 900)
        pg.goto((dash / "index.html").as_uri()); pg.wait_for_timeout(400)
        pg.screenshot(path=str(SHOTS / "index.png"), clip={"x": 0, "y": 0, "width": 1440, "height": 900})
        pg.goto((dash / "tokenomics.html").as_uri()); pg.wait_for_timeout(400)
        pg.screenshot(path=str(SHOTS / "tokenomics.png"), full_page=True, clip={"x": 0, "y": 0, "width": 1440, "height": 1160})
        pg.goto((dash / "safety.html").as_uri()); pg.wait_for_timeout(400)
        pg.screenshot(path=str(SHOTS / "safety.png"), full_page=True, clip={"x": 0, "y": 60, "width": 1440, "height": 1500})
        # chat UI: scripted run so the screenshot never costs tokens; a live chat screenshot can be dropped in as chat_live.png
        port = 8799
        srv = subprocess.Popen([sys.executable, "-m", "app.chat", "--mock", "--port", str(port)], cwd=str(ROOT),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2.0)
            pg.goto(f"http://127.0.0.1:{port}/"); pg.wait_for_timeout(1200)
            pg.screenshot(path=str(SHOTS / "chat_home.png"), clip={"x": 0, "y": 0, "width": 1440, "height": 900})
            pg.locator("button.alt").first.click()
            pg.wait_for_selector("text=Open this run on the dashboard", timeout=60000); pg.wait_for_timeout(800)
            pg.screenshot(path=str(SHOTS / "chat_run_mock.png"), full_page=True)
        finally:
            srv.terminate()
        b.close()
    from app import dashboard as d2
    d2.build(ROOT)
    print("screenshots written to", SHOTS)


def img(name: str, max_h: int | None = None) -> str | None:
    p = SHOTS / name
    if not p.exists():
        return None
    mime, raw = "image/png", p.read_bytes()
    try:  # dark screenshots compress well as JPEG; keeps report.html small enough to email
        import io
        from PIL import Image
        im = Image.open(p).convert("RGB")
        if im.width > 1300:
            im = im.resize((1300, round(im.height * 1300 / im.width)))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=80, optimize=True)
        mime, raw = "image/jpeg", buf.getvalue()
    except Exception:
        pass
    data = base64.b64encode(raw).decode()
    style = f" style='max-height:{max_h}px'" if max_h else ""
    return f"<img src='data:{mime};base64,{data}' alt='{e(name)}'{style}>"


def pair(a: str, b: str) -> str:
    return f"<div class='two'>{a}{b}</div>"


def fig(name: str, caption: str, need: str = "", max_h: int | None = None) -> str:
    i = img(name, max_h)
    if i is None:
        return f"<figure class='missing'>{todo(need or f'screenshot {name}')}<figcaption>{caption}</figcaption></figure>"
    return f"<figure>{i}<figcaption>{caption}</figcaption></figure>"


# --------------------------------------------------------------------------- the agent diagram (inline SVG, works offline)
def diagram(dark: bool = False) -> str:
    fg, mute, box, line = ("#e6f0f8", "#8fa6bb", "#0a1420", "#19e6ff") if dark else ("#0b1b2b", "#4a5d70", "#ffffff", "#0a8fb3")
    amb, red, grn = "#ffb000", "#ff4d6d", "#1fbf87"

    def node(x, y, w, h, title, sub, col, sub2=""):
        return (f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='6' fill='{box}' stroke='{col}' stroke-width='2'/>"
                f"<text x='{x + w / 2}' y='{y + 22}' text-anchor='middle' font-weight='700' font-size='15' fill='{fg}'>{title}</text>"
                f"<text x='{x + w / 2}' y='{y + 40}' text-anchor='middle' font-size='11.5' fill='{mute}'>{sub}</text>"
                + (f"<text x='{x + w / 2}' y='{y + 55}' text-anchor='middle' font-size='11.5' fill='{mute}'>{sub2}</text>" if sub2 else ""))

    def arrow(x1, y1, x2, y2, col=line, dash=""):
        return f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{col}' stroke-width='2' {dash} marker-end='url(#ah{col[1:]})'/>"
    marks = "".join(f"<marker id='ah{c[1:]}' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='{c}'/></marker>"
                    for c in (line, amb, red, grn, mute))
    heads = [("US Equity", "10 managers"), ("Intl Equity", "10 managers"), ("US IG", "10 managers"), ("US High Yield", "10 managers")]
    s = [f"<svg viewBox='0 0 980 560' xmlns='http://www.w3.org/2000/svg' font-family='Segoe UI,Helvetica,Arial,sans-serif' role='img' aria-label='Agent organisation'><defs>{marks}</defs>"]
    s.append(node(20, 20, 200, 62, "Asset owner", "plain-English description", mute))
    s.append(node(390, 20, 200, 62, "ACIO", "policy, review, final say", amb, "max 2 follow-ups"))
    s.append(arrow(220, 51, 388, 51, mute))
    s.append(f"<text x='304' y='44' text-anchor='middle' font-size='11' fill='{mute}'>ask</text>")
    s.append(f"<rect x='15' y='118' width='950' height='190' rx='8' fill='none' stroke='{mute}' stroke-dasharray='5 4'/>")
    s.append(f"<text x='28' y='138' font-size='12' fill='{mute}'>Stage 2: in parallel. Each agent sees only its own data (information barrier)</text>")
    s.append(node(30, 152, 170, 72, "AAR", "Asset Allocation & Risk", line, "CMAs, history, stress"))
    for i, (t, sub) in enumerate(heads):
        x = 215 + i * 185
        s.append(node(x, 152, 172, 72, f"Head: {t}", sub, grn, "own class DB only"))
    for x in [115] + [215 + i * 185 + 86 for i in range(4)]:
        s.append(arrow(490, 82, x, 150, amb))
    s.append(f"<rect x='30' y='238' width='915' height='56' rx='6' fill='{box}' stroke='{mute}'/>")
    s.append(f"<text x='487' y='260' text-anchor='middle' font-size='13' font-weight='700' fill='{fg}'>Data gateway (code): 23 fixed tools, read-only SQLite, per-agent allowlists and call budgets</text>")
    s.append(f"<text x='487' y='280' text-anchor='middle' font-size='11.5' fill='{mute}'>stats, deflated Sharpe, factor loadings, mix risk, allocation and stress run in code; untrusted text wrapped and scanned; every call logged</text>")
    s.append(node(390, 340, 200, 62, "ACIO review", "reads proposals, asks follow-ups", amb, "code builds the lineup"))
    s.append(arrow(490, 308, 490, 338, line))
    s.append(node(700, 340, 230, 62, "Red Team", "attacks the combined portfolio", red, "sees no author reasoning"))
    s.append(arrow(590, 371, 698, 371, red))
    s.append(node(390, 440, 200, 62, "ACIO decision", "answers every objection", amb, "contracts checked in code"))
    s.append(arrow(815, 402, 592, 470, red))
    s.append(arrow(490, 402, 490, 438, amb))
    s.append(node(700, 470, 230, 62, "Human CIO", "memo, dashboard, audit trail", mute, "approves or sends back"))
    s.append(arrow(590, 480, 698, 498, mute))
    s.append(node(30, 440, 300, 80, "Escalation", "budget hit, invalid output twice,", red, "or unanswered objection: no decision"))
    s.append(arrow(390, 471, 332, 478, red, "stroke-dasharray='4 3'"))
    s.append("</svg>")
    return "".join(s)


FIXES_BY_BATCH = [  # what each live batch exposed and what was changed before the next one (development history)
    "ACIO follow-ups crashed the run (a database connection reused across threads); the Red Team hit its 60-call budget and was "
    "locked out of reading the proposals; Heads wasted calls guessing factor-set and class labels. Fixed: one connection per thread, "
    "reading proposals exempt from the budget (Red Team budget 90), own class and factor set as defaults, unexpected tool errors returned to the agent.",
    "No crashes. Some Heads opened with one tool call and ran out of turns before fetching any manager statistics; one Head refused "
    "to pick managers and the code then refused an allocation with no lineup behind it. Fixed: the Heads' first tool returns a "
    "statistics overview of all 10 managers, and Heads get 5 turns instead of 4.",
    "No crashes or code stops: every run reached a decision. These runs are the measured evidence in Section 4.",
]
OLD_BRIEFS = {  # example owner texts used in the first live batches (replaced afterwards by achievable briefs)
    "endowment": "5% real return target, 4.5% spending, maximum drawdown about 25%",
    "endowment-no-followups": "5% real return target, 4.5% spending, maximum drawdown about 25%",
    "pension": "reduce funded-status volatility; no liability duration or numeric risk limit given",
    "foundation": "5% grants, preserve real value, drawdown tolerance about 15%",
}


def batches(runs: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for r in runs:
        if not out or (r.get("label") == "endowment"):
            out.append([])
        out[-1].append(r)
    return out


def live_testing_html(runs: list[dict]) -> str:
    bs = batches(runs)
    if len(bs) < 2:
        return ""
    rows = []
    for i, b in enumerate(bs):
        from collections import Counter
        c = Counter(("run stopped by a control" if r.get("status") in ("RUN_STOPPED", "ESCALATED") else r.get("status")) for r in b)
        what = FIXES_BY_BATCH[i] if i < len(FIXES_BY_BATCH) and len(bs) == len(FIXES_BY_BATCH) else ""
        rows.append(f"<tr><td class='n'>{i + 1}</td><td class='n'>{len(b)}</td><td>{e(', '.join(f'{v} {k}' for k, v in c.items()))}</td>"
                    f"<td class='n'>{mmss(sum(r['elapsed_s'] for r in b) / len(b))}</td><td>{e(what)}</td></tr>")
    return ("<h3>What live testing changed</h3><p>Every model run was live; the results below are from "
            f"{len(runs)} runs in {len(bs)} batches of <code>python -m app.evidence</code>. Each batch exposed problems that the scripted "
            "tests could not, and each was fixed and covered by a new regression test before the next batch.</p>"
            "<table><tr><th class='n'>Batch</th><th class='n'>Runs</th><th>Outcomes</th><th class='n'>Mean time</th><th>What it exposed, and the fix</th></tr>"
            + "".join(rows) + "</table>")


def infeasible_html(runs: list[dict]) -> str:
    bs = batches(runs)
    if len(bs) < 2:
        return ""
    rows, seen = [], set()
    for b in reversed(bs[:-1]):  # latest earlier batch first; one example per owner type
        for r in b:
            kind = (r.get("label") or "").split("-")[0]
            if r.get("status") != "NEEDS_OWNER_INPUT" or kind in seen:
                continue
            seen.add(kind)
            dec = load(r["_dir"] / "decision.json") or {}
            memo = " ".join((dec.get("memo") or "").replace("#", "").split())
            import re
            memo = re.sub(r"\[E\d+\]", "", memo)
            sent = re.split(r"(?<=[.])\s+", memo)
            excerpt = " ".join(x for x in sent[:3] if x.lower().strip() not in ("recommendation.", "recommendation"))[:420]
            rows.append(f"<tr><td>{e(OLD_BRIEFS.get(r.get('label'), r.get('label')))}</td><td>{e(excerpt)}</td></tr>")
    if not rows:
        return ""
    return ("<h3>When the brief cannot be met</h3><p>The first example briefs asked for combinations the five available asset classes "
            "cannot deliver. The committee did not approve anyway: it escalated to the human CIO and said why. This is how real portfolio "
            "construction works: a return target, a loss limit and a liquidity or guardrail constraint can be jointly unachievable, and the "
            "useful answer is the trade-off, not a forced mix. The example briefs were then rewritten to be achievable (Section 3).</p>"
            "<table><tr><th style='width:32%'>Brief (first batches)</th><th>The ACIO's memo, opening lines (verbatim, evidence ids removed)</th></tr>"
            + "".join(rows[:3]) + "</table>")


# --------------------------------------------------------------------------- report
CSS = """
@page{size:Letter;margin:16mm 15mm}
*{box-sizing:border-box}
body{font:10pt/1.42 'Segoe UI',Helvetica,Arial,sans-serif;color:#13202d;max-width:880px;margin:0 auto;padding:28px 22px;background:#fff}
h1{font-size:25pt;margin:0 0 4px;color:#06263d;letter-spacing:.01em}
h2{font-size:15.5pt;color:#06263d;border-bottom:2px solid #0a8fb3;padding-bottom:3px;margin:28px 0 10px;page-break-after:avoid}
h3{font-size:12pt;color:#0a5f80;margin:16px 0 6px;page-break-after:avoid}
p{margin:6px 0 9px}
.sub{color:#3d5266;font-size:11.5pt}
.meta{font-size:10pt;color:#3d5266}
table{border-collapse:collapse;width:100%;margin:8px 0 12px;font-size:9.6pt}
tr{page-break-inside:avoid}
th,td{border-bottom:1px solid #d6e0e8;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#eef5f9;color:#24394d;font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
figure{margin:10px 0 14px;page-break-inside:avoid;text-align:center}
figure img{max-width:100%;border:1px solid #c9d6e0;border-radius:3px}
figure svg{max-width:100%;height:auto}
figcaption{font-size:9.3pt;color:#4a5d70;margin-top:4px;text-align:left}
.todo{display:inline-block;background:#fff3c4;border:1px solid #e0b100;color:#5b4500;padding:2px 6px;font-weight:600;font-size:9.5pt}
figure.missing{border:1px dashed #e0b100;padding:18px;background:#fffbe9}
.box{background:#f3f8fb;border-left:4px solid #0a8fb3;padding:8px 12px;margin:10px 0}
.warn{background:#fff6f6;border-left:4px solid #d9435e}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:start}
.two figure{margin:4px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:8px 0 12px}
.kpi{border:1px solid #c9d6e0;border-radius:4px;padding:6px 9px}
.kpi b{display:block;font-size:16pt;color:#06263d}
.kpi span{font-size:8.8pt;color:#4a5d70}
code{font-size:9.3pt;background:#f1f4f7;padding:0 3px}
ol li,ul li{margin:2px 0}
.pb{page-break-before:always}
.small{font-size:9.3pt;color:#4a5d70}
"""


def build_report(ctx: dict) -> str:
    tok, sf, tr, pilot = ctx["tok"], ctx["safety"], ctx["trace"], ctx["pilot"]
    pi = pilot.get("pilot", {})
    repo = pilot.get("repo_url") or todo("GitHub repo URL in pilot.json (repo_url)")
    live_url = pilot.get("live_url")
    have_tok = bool(tok and tok.get("n_runs"))
    pr = tok["per_run"] if have_tok else {}

    # ---------------- title
    out = [f"<h1>ACIO: Augmented Investment Intelligence</h1>"
           f"<p class='sub'>An AI investment committee for asset owners that hire external managers</p>"
           f"<p class='meta'><b>{e(pilot.get('author', 'Bryan White'))}</b> &middot; MGT 409 AI Foundations for Managers, Yale SOM &middot; Final project, November 2026<br>"
           f"Repository: {repo if not repo.startswith('http') else f'<a href={e(repo)}>{e(repo)}</a>'}"
           + (f" &middot; Live app: <a href='{e(live_url)}'>{e(live_url)}</a>" if live_url else " &middot; Runs locally from a fresh clone (README)") + "</p>"
           "<div class='box'><b>One sentence.</b> A pension, endowment or foundation describes itself in plain English, and seven GPT agents "
           "(an Artificial CIO, an asset allocation and risk head, four asset-class heads and a Red Team) produce an evidence-cited asset "
           "allocation, a ranked and sized manager lineup and a decision memo for the human CIO, in under ten minutes, for cents.</div>"]

    # ---------------- 1 motivation
    out.append("<h2>1. Motivation</h2>")
    out.append("<p><b>Who has the problem.</b> The people who decide where an institution's long-term money goes: the CIO and a handful of "
               "investment staff at a pension plan, endowment, foundation or insurer that hires external managers. The author works in institutional "
               "manager selection and asset allocation, and the app is built around the decisions that work makes every quarter.</p>")
    out.append("<p><b>How they handle it now.</b> Two linked decisions are made by small teams with spreadsheets, a manager database and a consultant: "
               "(1) the asset allocation, built from capital market assumptions (CMAs) that a dozen houses publish in long PDFs that disagree, and "
               "(2) which external managers to hire in each asset class and in what size, from return histories and marketing descriptions. "
               "The work is repetitive (pull returns, compute statistics against the benchmark, compare peers, write the memo), slow (a single "
               "manager search runs for weeks to months), and fragile in ways that are well documented: picking on trailing returns, under-weighting "
               "the multiple-testing problem when ten candidates are compared, and committee dynamics that rarely produce a formal dissent.</p>")
    out.append("<p><b>What it costs.</b> Staff time, consultant retainers and, above all, decision quality. A manager hired on a lucky record costs "
               "fees and underperformance for years. Smaller owners feel it most: of roughly 149,000 US private foundations (Cause IQ directory), most report "
               "no staff or under ten employees, yet foundation assets total about $1.8 trillion (FoundationMark, 2025). These owners rarely have an "
               "investment committee process at all.</p>")
    out.append("<p><b>Why it matters.</b> The money pays pensions, scholarships and grants. A transparent, repeatable first draft of the committee's "
               "work, with every number traceable to data and a built-in devil's advocate, lets a small team spend its time on judgment instead of "
               "assembly, and lets a large team test more owner scenarios than it could by hand.</p>")

    # ---------------- 2 what is new
    out.append("<h2>2. What is new</h2>")
    out.append("<table><tr><th>Existing option</th><th>What it does well</th><th>What it does not do</th></tr>"
               "<tr><td><b>Nasdaq eVestment</b> (manager database; asset owner research tools; an agentic “Next Best Action” agent built with Snowflake)</td>"
               "<td>The industry manager universe (27,500+ institutional strategies), peer screens, analytics</td>"
               "<td>Its published agent helps <i>asset managers</i> find capital; the asset owner still assembles the allocation, the lineup, the dissent and the memo</td></tr>"
               "<tr><td><b>BlackRock Aladdin / eFront Copilot</b></td><td>Natural-language questions over portfolio and private-markets data, turned into visualisations</td>"
               "<td>A query assistant on top of a platform; no committee of role-separated agents, no manager selection decision with a challenger</td></tr>"
               "<tr><td><b>Investment consultants</b> (e.g. Mercer, Aon, Callan)</td><td>Experienced manager research, ODD, relationships</td>"
               "<td>Expensive, weeks per search, opaque reasoning; one view, no standing red team</td></tr>"
               "<tr><td><b>General chatbots</b> (ChatGPT, Claude)</td><td>Fluent analysis of pasted data</td>"
               "<td>No access control, does arithmetic in text, invents figures, no audit trail, no budget or escalation rules</td></tr>"
               "<tr><td><b>Open-source agent “trading firms”</b> (TradingAgents, ai-hedge-fund)</td><td>Multi-agent debate on single stocks</td>"
               "<td>Built for trading one ticker, research only; not the asset-owner workflow (policy, allocation, external managers, governance)</td></tr></table>")
    out.append("<p><b>What this project does differently.</b></p><ol>"
               "<li><b>Models the asset owner's committee, not a trading desk.</b> Policy from a plain description, a five-class allocation with ranges, "
               "a ranked and sized lineup of external managers in each class, and a memo that lists the alternatives considered and why they were not chosen.</li>"
               "<li><b>An information barrier enforced in code.</b> Each asset-class head can open only its own database; the allocator sees no manager data; "
               "the Red Team sees the proposals with the authors' reasoning stripped. This mirrors how real committees prevent anchoring.</li>"
               "<li><b>The model never does the math.</b> Statistics, the deflated Sharpe ratio (verified against the paper's own worked example), "
               "factor loadings, mix risk, allocation risk and stress tests run in code. Agents interpret and must cite the tool result (evidence id) for every claim; "
               "uncited claims are dropped.</li>"
               "<li><b>Governance built in.</b> The ACIO may override the allocator only with a recorded reason and evidence; every Red Team objection needs a "
               "recorded answer; any invalid output, budget breach or unanswered objection escalates to the human CIO with no decision written.</li>"
               "<li><b>Real public CMAs</b> from seven houses, extracted with page references, with coverage graded per class.</li></ol>")
    out.append("<p><b>Honest assessment.</b> The advantage is workflow and control, not secret alpha: the same model family powers every agent, so blind spots are "
               "correlated; the manager data are anonymised stand-ins; and nothing here replaces operational due diligence or a consultant's relationships. "
               "What it does better than the alternatives above is produce a transparent, reproducible, challengeable first draft of the committee decision, cheaply.</p>")

    # ---------------- 3 agents
    out.append("<h2>3. The agents</h2>")
    out.append(f"<figure>{diagram(False)}<figcaption>Figure 1. Organisation and flow. Amber: ACIO instructions; green: asset-class heads; red: challenge and escalation. "
               "The data gateway sits between every agent and the data.</figcaption></figure>")
    out.append("<table><tr><th>Agent (cast name)</th><th>Role</th><th>Tools (all run in code)</th><th>Cannot</th></tr>"
               "<tr><td><b>ACIO</b> (The Chairman)</td><td>Infers the policy from the owner's text; reviews all proposals; up to 2 follow-up questions to any head or the AAR; final allocation and lineup; memo</td>"
               "<td>investable universe, CMAs, class stats and correlations, episodes, manager cards and text, read proposals, evaluate allocation, stress test, evaluate manager mix, ask_agent</td>"
               "<td>read raw manager returns; change the AAR's mix without a recorded reason</td></tr>"
               "<tr><td><b>Head of Asset Allocation and Risk</b> (The Quant)</td><td>Recommended mix, ranges, conservative and aggressive alternatives, sensitivity, stress under baseline and downside correlations</td>"
               "<td>universe, CMAs (7 houses), class statistics and correlations, benchmark returns, episodes, evaluate_allocation</td><td>see any manager</td></tr>"
               "<tr><td><b>Four asset-class Heads</b> (Stock Picker, Globe Trotter, Rates Desk, Credit Hawk)</td><td>Force-rank all 10 managers in their class with a rationale; select and size a lineup summing to 100%</td>"
               "<td>manager list, cards, strategy text (untrusted), statistics by window, returns, excess correlation, peer stats, factors and loadings, deflated Sharpe, evaluate_manager_mix</td>"
               "<td>see another class, the other heads' proposals, or the allocation</td></tr>"
               "<tr><td><b>Red Team</b> (The Prosecutor)</td><td>Attacks the combined portfolio: concentration, stress, luck vs skill, weak process evidence; objections with a fix criterion</td>"
               "<td>read-only versions of all analytic tools, stress test, read proposals (reasoning stripped)</td><td>publish anything or see the authors' arguments</td></tr></table>")
    out.append("<p><b>Why this organisation.</b> It is how real committees separate duties: allocation is decided without looking at managers (so a favourite manager "
               "cannot drive the mix), each head is a specialist with a defensible scope, and dissent is a standing role rather than an accident. Running the AAR and "
               "four heads in parallel keeps a full run inside ten minutes. The pipeline is deterministic code; agents decide only within their stage.</p>")
    out.append("<p><b>Model.</b> All agents use <code>gpt-6-luna</code>, the course model. The client accepts either course key: with <code>PORTKEY_API_KEY</code> it calls the course "
               "Portkey gateway; with an OpenAI <code>OPENAI_API_KEY</code> it calls OpenAI directly, as the measured runs did. At $0.10 / $0.01 / $0.50 per million input, "
               "cached and output tokens, one twentieth of the price of gpt-6.1-sol for the same tokens (Section 4). Because the heavy lifting (statistics) is in code, "
               "the agents need judgment and reading, not arithmetic. The cost of one model family for every role is correlated blind spots; the mitigations are "
               "separate contexts, stripped reasoning for the Red Team, and code-enforced contracts. Reasoning effort is set to <code>none</code> because the Azure "
               "back end rejects function tools with reasoning on <code>/chat/completions</code> (found in live testing).</p>")

    # traced run
    out.append("<h3>One real run, traced step by step</h3>")
    if tr:
        d = tr["_dir"]; dec = load(d / "decision.json") or {}; props = load(d / "proposals.json") or {}; rt = load(d / "red_team.json") or {}
        pol = dec.get("policy") or (props.get("acio") or {}).get("policy") or {}
        st = {x["stage"]: x for x in tr.get("stages", []) if x.get("end") is not None}
        aar_w = ((props.get("aar") or {}).get("recommended") or {}).get("weights_pct", {})
        fin_w = (dec.get("asset_allocation") or {}).get("weights_pct", {})
        fu = [ev for ev in tr.get("events", []) if ev.get("from") == "acio" and str(ev.get("text", "")).startswith("question")]
        objs = rt.get("objections", [])
        resp = {x["objection_id"]: x for x in dec.get("red_team_responses", [])}
        out.append(f"<p class='small'>Run <code>{e(tr['run_id'])}</code> ({e(tr.get('label') or 'chat')}), {e(tr.get('model'))}, status <b>{e(tr.get('status'))}</b>, "
                   f"{mmss(tr['elapsed_s'])} elapsed, {tr['model_calls']} model calls, {tr['tokens']['total']:,} tokens, {usd(tr['cost_usd'])}.</p>")
        steps = [
            ("Ask", "The owner text: “" + e(ctx["owner_text"] or "") + "”"),
            ("Policy (ACIO)", f"Inferred owner type <b>{e(pol.get('owner_type'))}</b>; objective: {e(pol.get('objective'))}; risk: {e(pol.get('risk_tolerance'))}. "
             f"({st.get('Policy (ACIO)', {}).get('end', 0) - st.get('Policy (ACIO)', {}).get('start', 0):.0f} s)"),
            ("AAR + 4 Heads in parallel", "AAR proposed " + ", ".join(f"{k} {v:g}%" for k, v in aar_w.items()) +
             "; each Head ranked its 10 managers and sized a lineup. " + (f"({st['AAR + 4 Heads (parallel)']['end'] - st['AAR + 4 Heads (parallel)']['start']:.0f} s)" if 'AAR + 4 Heads (parallel)' in st else "")),
            ("ACIO review", (f"{len(fu)} follow-up(s): " + "; ".join(e(x['text'][:140]) for x in fu)) if fu else "No follow-ups needed."),
            ("Red Team", f"Verdict <b>{e(rt.get('verdict'))}</b>, {len(objs)} objection(s): " + "; ".join(f"{e(o['id'])} ({e(o['severity'])}) {e(o['claim'][:120])}" for o in objs[:3])),
            ("ACIO decision", f"<b>{e(dec.get('decision'))}</b>. Final mix " + ", ".join(f"{k} {v:g}%" for k, v in fin_w.items()) +
             f". Allocation adopted: {e((dec.get('allocation_decision') or {}).get('adopted', 'AAR'))}. Responses: " +
             "; ".join(f"{e(k)} {e(v.get('response'))}" for k, v in resp.items())),
        ]
        out.append("<table><tr><th>Step</th><th>What happened (from the run's files)</th></tr>" + "".join(f"<tr><td><b>{a}</b></td><td>{b}</td></tr>" for a, b in steps) + "</table>")
    else:
        out.append(f"<p>{todo('run python -m app.evidence (live) so a real run can be traced here')}</p>")
    out.append(live_testing_html(ctx.get("all_runs") or []))
    out.append(pair(
        fig("chat_live.png" if (SHOTS / "chat_live.png").exists() else "chat_run_mock.png",
            "Figure 2. Chat: the ask, the inferred policy, then the live wire of agent messages with clock, tokens and cost"
            + ("." if (SHOTS / "chat_live.png").exists() else " (scripted stand-in shown)."), max_h=430),
        fig("run_wire.png", "Figure 3. The wire for the traced run: every message between agents, time-stamped.", "agent message log screenshot", max_h=430)))
    out.append(pair(
        fig("run_top.png", "Figure 4. Run header: status, time against the 10-minute limit, tokens (cached share), cost, calls.", "live run dashboard screenshot (build with --shots after python -m app.evidence)"),
        fig("run_timeline.png", "Figure 5. Stage timeline: the AAR and four Heads run in parallel, which keeps the run inside the limit.", "stage timeline screenshot")))
    out.append(pair(
        fig("run_alloc.png", "Figure 6. AAR proposal (with ranges) vs the ACIO's final, and the lineup the code computed.", "allocation screenshot", max_h=400),
        fig("run_redteam.png", "Figure 7. Red Team objections and the ACIO's recorded response to each.", "Red Team screenshot", max_h=400)))
    out.append(pair(
        fig("class_us_eq.png", "Figure 8. Asset-class page: selected managers with weights and rationale; next-best managers and why not.", "class page screenshot", max_h=400),
        fig("audit_summary.png", "Figure 9. Plain-language audit summary for the run; the full trace keeps every prompt, tool call and result.", "audit summary screenshot", max_h=400)))

    # ---------------- 4 tokenomics
    out.append("<h2>4. Tokenomics</h2>")
    if have_tok:
        out.append(f"<p>Measured on the final batch of <b>{tok['n_runs']} live runs</b> of {e(tok['model'])} ({tok['n_completed']} reached a decision; averages use those), "
                   f"run on the submitted code. The {tok.get('n_all_live_runs', tok['n_runs']) - tok['n_runs']} earlier development runs (Section 3) are excluded because the code changed after them. "
                   f"Token counts are what the gateway reported per call (prompt, cached prompt, completion), recorded in each run's <code>telemetry.json</code>. "
                   f"Prices: {e(tok['price_source'])}.</p>")
        out.append("<div class='kpis'>" +
                   f"<div class='kpi'><b>{usd(pr['cost_usd'])}</b><span>cost per run (range {usd(pr['cost_usd_min'])} to {usd(pr['cost_usd_max'])})</span></div>"
                   f"<div class='kpi'><b>{pr['total']:,.0f}</b><span>tokens per run: {pr['input']:,.0f} in, {pr['output']:,.0f} out</span></div>"
                   f"<div class='kpi'><b>{pr['cache_share']:.0%}</b><span>of input tokens served from cache</span></div>"
                   f"<div class='kpi'><b>{mmss(pr['elapsed_s'])}</b><span>mean time per run (max {mmss(pr['elapsed_s_max'])})</span></div></div>")
        rows = "".join(f"<tr><td>{e(r['label'] or '')}</td><td>{e(r['status'])}</td><td class='n'>{mmss(r['elapsed_s'])}</td><td class='n'>{r['calls']}</td>"
                       f"<td class='n'>{r['prompt']:,}</td><td class='n'>{r['cached']:,}</td><td class='n'>{r['completion']:,}</td><td class='n'>{usd(r['cost_usd'])}</td></tr>" for r in tok["runs"])
        out.append("<table><tr><th>Run</th><th>Status</th><th class='n'>Time</th><th class='n'>Calls</th><th class='n'>Input</th><th class='n'>Cached input</th><th class='n'>Output</th><th class='n'>Cost</th></tr>" + rows + "</table>")
        names = {"acio": "ACIO", "aar": "Asset Allocation & Risk", "head_us_eq": "Head, US Equity", "head_intl_eq": "Head, Intl Equity",
                 "head_us_ig": "Head, US IG", "head_us_hy": "Head, US High Yield", "red_team": "Red Team"}
        ag = "".join(f"<tr><td>{e(names.get(a, a))}</td><td class='n'>{v['calls']:.1f}</td><td class='n'>{v['input']:,.0f}</td><td class='n'>{v['cached']:,.0f}</td><td class='n'>{v['output']:,.0f}</td><td class='n'>{usd(v['cost_usd'])}</td></tr>"
                     for a, v in sorted(tok["by_agent_mean"].items(), key=lambda kv: -kv[1]["cost_usd"]))
        out.append("<h3>Where the tokens go (mean per completed run)</h3><table><tr><th>Agent</th><th class='n'>Calls</th><th class='n'>Input</th><th class='n'>Cached</th><th class='n'>Output</th><th class='n'>Cost</th></tr>" + ag + "</table>")
        sc = tok["scale"]
        out.append(f"<h3>Per user and at scale</h3><p>Assumption (stated): one user is one investment professional who runs <b>{tok['runs_per_user_month']:g}</b> committee runs a month "
                   f"(a manager search or allocation review needs several owner scenarios and re-runs). Model cost per user per month: <b>{usd(tok['per_user_month_usd'], 2)}</b>.</p>"
                   "<table><tr><th class='n'>Users</th><th class='n'>Runs per month</th><th class='n'>Model cost per month</th><th class='n'>Per year</th></tr>" +
                   "".join(f"<tr><td class='n'>{v['users']:,}</td><td class='n'>{v['runs_per_month']:,.0f}</td><td class='n'>{usd(v['model_cost_month'], 2)}</td><td class='n'>{usd(v['model_cost_year'], 2)}</td></tr>" for v in sc.values()) +
                   "</table><p class='small'>Model cost only. At 10,000 users the app would be hosted: a modest always-on server and database are a stated estimate of a few hundred dollars a month, "
                   "small next to the model bill, and authentication and per-tenant data isolation would be required (Section 8).</p>")
        om = tok["same_tokens_other_models"]
        out.append("<h3>Cost-reduction steps and their effect</h3><ol>"
                   f"<li><b>Model choice.</b> The same tokens would cost {usd(om.get('gpt-6.1-sol'))} per run on gpt-6.1-sol and {usd(om.get('gpt-6-astra'))} on gpt-6-astra, "
                   f"against {usd(om.get('gpt-6-luna'))} on luna. Because statistics run in code, the cheaper model is enough for interpretation.</li>"
                   f"<li><b>Prompt caching.</b> Each agent's long system prompt is identical across its turns and runs, so the provider caches it: {pr['cache_share']:.0%} of input tokens "
                   f"were billed at the cached rate. Without caching the mean run would cost {usd(tok['no_cache_cost_usd'])} instead of {usd(pr['cost_usd'])}.</li>"
                   "<li><b>Short core prompts, long references on demand.</b> Each agent gets a compiled core prompt; detailed doctrine stays out of context unless needed.</li>"
                   "<li><b>Code instead of tokens.</b> Returns are never pasted into context for arithmetic; tools return statistics (rows capped at 120 by default, text at 6,000 characters, tool results at 14,000).</li>"
                   "<li><b>Hard limits.</b> 4 model steps per agent (5 for the asset-class Heads, 6 for the ACIO), 2 follow-ups, a per-agent call budget, a 420 s time budget, a 3M-token and $1.00 per-run cap.</li></ol>")
        cmp_ = ctx.get("compare")
        if cmp_:
            out.append("<h3>Measured trade-off: ACIO follow-ups on vs off</h3>" + cmp_)
        else:
            out.append(f"<p>{todo('comparison run (python -m app.evidence includes an endowment run with follow-ups off)')}</p>")
    else:
        out.append(f"<p>{todo('live runs: python -m app.evidence, then rebuild this report')}. Until then the dashboard records zero tokens for scripted runs, by design.</p>")

    # ---------------- 5 safety
    out.append("<h2>5. Safety</h2>")
    if sf:
        res = sf["results"]; n = sum(r["passed"] for r in res)
        out.append(f"<p><code>python -m app.safety</code> runs {len(res)} scenarios against the real gateway, orchestrator and contracts (the scripted model plays the attacker "
                   f"or the faulty agent; the code's response is the same in live runs). <b>{n} of {len(res)} defences held</b> on {e(sf['generated_utc'])}. "
                   "The same scenarios run in the test suite (<code>tests/test_safety.py</code>).</p>")
        out.append("<table><tr><th style='width:13%'>Area</th><th style='width:33%'>Risk tested</th><th>Built defence</th><th style='width:8%'>Result</th></tr>" +
                   "".join(f"<tr><td>{e(r['area'])}</td><td>{e(r['attack'])}</td><td>{e(r['defence'])}</td>"
                           f"<td><b style='color:{'#127a4f' if r['passed'] else '#b3123a'}'>{'HELD' if r['passed'] else 'FAILED'}</b></td></tr>" for r in res) + "</table>")
        out.append(fig("safety.png", "Figure 10. The Safety page in the app: each scenario, the defence, the result and what the code returned (for example the injection flag on a planted manager document and the escalation record when the token budget is hit).", "safety page screenshot", max_h=380))
    else:
        out.append(f"<p>{todo('python -m app.safety')}</p>")
    out.append("<div class='box warn'><b>Residual risk, stated plainly.</b> The defences above are in code and are tested. What is <i>not</i> measured is how often the live model "
               "would be persuaded by injected text that the scan misses; the design limits the damage instead (no write, send, file or network tools; contracts and "
               "the Red Team between any agent and the decision; a human CIO signs off). All agents share one model family, so a blind spot can be shared.</div>")

    # ---------------- 6 impact
    out.append("<h2>6. Impact</h2>")
    base_h = pi.get("baseline_hours"); app_m = pi.get("with_app_minutes"); rev_m = pi.get("review_minutes")
    rate = float(pilot.get("analyst_cost_per_hour_usd") or 150)
    out.append("<h3>Real user trial</h3>")
    if pi.get("who") and pi.get("quote"):
        out.append(f"<p><b>{e(pi['who'])}</b> ({e(pi.get('role'))}) used the app on {e(pi.get('date'))} for: {e(pi.get('task'))}.</p>"
                   f"<blockquote class='box'>“{e(pi['quote'])}”</blockquote>"
                   + (f"<p>What they would change: {e(pi.get('what_they_would_change'))}</p>" if pi.get("what_they_would_change") else ""))
    else:
        out.append(f"<p>{todo('pilot.json: who tried it (a role such as investment professional at an institutional allocator, no employer), date, task, a direct quote')}</p>")
    out.append(infeasible_html(ctx.get("all_runs") or []))
    out.append("<h3>Quantified benefits</h3>")
    rows = []
    if have_tok:
        rows.append(("Turnaround for a first-draft committee package (policy, allocation, 4 lineups, challenge, memo)", f"{mmss(pr['elapsed_s'])} per run, measured over {tok['n_runs']} live runs", "App telemetry"))
        rows.append(("Model cost per draft", usd(pr["cost_usd"]), "App telemetry at course prices"))
    if base_h:
        hours_saved = float(base_h) - (float(app_m or (pr.get("elapsed_s", 0) / 60 if have_tok else 0)) + float(rev_m or 0)) / 60
        rows.append(("Analyst time for the same first draft today", f"{float(base_h):g} hours", e(pi.get("baseline_basis") or "pilot estimate")))
        rows.append(("Time with the app, including human review", f"{(float(app_m or 0) + float(rev_m or 0)):g} minutes", "pilot, timed"))
        rows.append(("Time saved per draft", f"{hours_saved:.1f} hours (about {usd(hours_saved * rate, 0)} at {usd(rate, 0)}/hour, a stated estimate)", "computed"))
        if have_tok:
            rows.append(("Cost ratio, analyst time vs model", f"about {hours_saved * rate / max(pr['cost_usd'], 1e-6):,.0f} to 1", "computed"))
    else:
        rows.append(("Analyst time for the same first draft today", todo("pilot.json baseline_hours and baseline_basis (your own estimate for a 4-class manager short-list plus allocation memo, or a timed attempt)"), ""))
    rows.append(("Quality: every claim traceable", "Uncited findings are dropped by code; every number is a tool result with an evidence id", "Safety tests"))
    rows.append(("Quality: luck vs skill", "Deflated Sharpe ratio for every manager, reproducing the paper's example to 4 decimals (0.9004, 0.9505)", "tests/test_dsr_paper.py"))
    rows.append(("Newly possible", "Run the same manager universe against several owner profiles in one sitting and compare; a standing dissent on every decision", "App"))
    rows.append(("Reach", "About 149,000 US private foundations, most with 0 to 10 staff, holding about $1.8 trillion", "Cause IQ; FoundationMark 2025"))
    out.append("<table><tr><th>Benefit</th><th>Number</th><th>Source</th></tr>" + "".join(f"<tr><td>{a}</td><td>{b}</td><td class='small'>{c}</td></tr>" for a, b, c in rows) + "</table>")
    out.append("<p class='small'>Limits of these numbers: the time saved is for a first draft that a professional still reviews; it does not include due diligence meetings, "
               "operational due diligence or legal work, which the app does not do. The manager data are anonymised stand-ins, so the app's picks demonstrate the workflow, not real manager quality.</p>")
    pf = pilot.get("peer_fresh_clone", {})
    if pf.get("who"):
        out.append(f"<p><b>Fresh-clone test.</b> {e(pf['who'])} cloned the repo and had the app running in {e(pf.get('minutes_to_running'))} minutes following the README. {e(pf.get('notes'))}</p>")
    else:
        out.append(f"<p><b>Fresh-clone test.</b> {todo('peer_fresh_clone in pilot.json: who, minutes to running')}</p>")

    # ---------------- 7 limits
    out.append("<h2>7. Limits and next steps</h2>")
    out.append("<p><b>What does not work yet.</b></p><ul>"
               "<li><b>Data.</b> Forty anonymised managers (ten per class) with altered return streams; benchmarks and factors without full documentation; CMAs differ in index, date and currency across houses, and international equity has only three direct estimates.</li>"
               "<li><b>Method.</b> Manager mixes are evaluated in-sample; the deflated Sharpe ratio needs an assumed number of trials; stress scenarios are calendar episodes; there is no liability model, so a pension's funded-status risk is described, not optimised.</li>"
               "<li><b>Agents.</b> One model family for all roles; reasoning effort forced to none by the gateway; agent quality is not yet scored against expert decisions or across repeated runs.</li>"
               "<li><b>Product.</b> Local, single user; no authentication, sign-off workflow, or versioned prompts and data; operational due diligence is out of scope.</li></ul>")
    out.append("<p><b>What I would build next.</b></p><ol>"
               "<li>An evaluation set: owner profiles with expert answers, scored for agreement and run-to-run stability.</li>"
               "<li>Upload of the owner's current policy and holdings so recommendations are changes from where the owner is, with turnover and transition cost.</li>"
               "<li>A secure sandbox so proprietary data (licensed manager universes, consultant research, private funds) can be used; then hedge fund, private credit and private equity heads.</li>"
               "<li>A pool of Red Team challengers (allocation, manager, model, data) and an independent risk officer (ACRO) as a second line.</li>"
               "<li>Peer-to-peer consultation between specialist agents (capital markets, issuer research) with a logged prior-update for each, the “collective market intelligence” target design.</li></ol>")
    out.append("<p class='small'>Sources: Bailey and López de Prado (2014), The Deflated Sharpe Ratio, Journal of Portfolio Management; Nasdaq eVestment product pages and the "
               "Snowflake blog on eVestment's agent (May 2026); BlackRock eFront Copilot pages; TradingAgents (Xiao et al., arXiv 2412.20138); Cause IQ private foundation directory; "
               "FoundationMark via Financial Planning (2025); CMA reports from KKR, Invesco, Amundi, PGIM, Northern Trust, BNY and Nuveen (page references in <code>db/cma.db</code>); "
               "MGT 409 lecture 11 for model prices. AI tools were used for drafting and coding; the author is responsible for every claim and number.</p>")
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>ACIO: Augmented Investment Intelligence (report)</title><style>{CSS}</style></head><body>{''.join(out)}</body></html>"


# --------------------------------------------------------------------------- slides
SLIDE_CSS = """
html,body{margin:0;height:100%;background:#05090f}
.reveal{font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:#e6f0f8}
.reveal .slides section{text-align:left}
.reveal h1,.reveal h2{font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:#19e6ff;text-transform:uppercase;letter-spacing:.08em;text-shadow:0 0 14px rgba(25,230,255,.45)}
.reveal h1{font-size:1.9em}.reveal h2{font-size:1.25em;margin-bottom:.4em}
.reveal p,.reveal li{font-size:.78em;line-height:1.4}
.reveal .big{font:700 2.1em ui-monospace,Consolas,monospace;color:#ffb000;text-shadow:0 0 12px rgba(255,176,0,.45)}
.reveal .lbl{font-size:.5em;letter-spacing:.16em;text-transform:uppercase;color:#8fa6bb}
.reveal .kp{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:.5em}
.reveal .kp div{border:1px solid rgba(25,230,255,.3);padding:12px 14px;background:rgba(10,20,32,.9)}
.reveal img{max-height:520px;max-width:100%;border:1px solid rgba(25,230,255,.35);margin:0}
.reveal .two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}
.reveal .todo{background:#fff3c4;color:#5b4500;padding:2px 8px;font-size:.6em}
.reveal svg{max-height:540px;width:100%}
.reveal .mute{color:#8fa6bb;font-size:.6em}
.reveal table{font-size:.62em;border-collapse:collapse;width:100%}
.reveal td,.reveal th{border-bottom:1px solid rgba(25,230,255,.18);padding:4px 6px;text-align:left}
.reveal th{color:#8fa6bb}
/* offline fallback when reveal.js cannot load: one slide at a time, arrow keys */
body.noreveal .slides>section{display:none;padding:40px 60px;min-height:90vh}
body.noreveal .slides>section.on{display:block}
body.noreveal{color:#e6f0f8;font:34px 'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden}
body.noreveal .slides>section{box-sizing:border-box;max-width:100vw;height:100vh;overflow:hidden}
body.noreveal .reveal h1{font-size:2.2em;margin:.2em 0}body.noreveal .reveal h2{font-size:1.3em;margin:.1em 0 .5em}
body.noreveal .reveal img{max-height:62vh}
"""


def build_slides(ctx: dict) -> str:
    tok, sf, pilot = ctx["tok"], ctx["safety"], ctx["pilot"]
    have_tok = bool(tok and tok.get("n_runs")); pr = tok["per_run"] if have_tok else {}
    S = []
    S.append("<section><h1>ACIO</h1><h2 style='color:#ffb000'>Augmented Investment Intelligence</h2>"
             "<p>An AI investment committee for pensions, endowments and foundations that hire external managers.</p>"
             f"<p class='mute'>{e(pilot.get('author', 'Bryan White'))} &middot; MGT 409 &middot; Yale SOM &middot; November 2026</p></section>")
    S.append("<section><h2>The problem</h2><ul>"
             "<li>Two decisions drive an institution's long-term money: the <b>asset allocation</b> and <b>which external managers</b> to hire.</li>"
             "<li>Small teams assemble them by hand: CMAs from a dozen PDFs, return statistics in spreadsheets, a memo. Weeks per manager search.</li>"
             "<li>Known failure modes: hiring on lucky track records, ten candidates compared without a multiple-testing correction, no formal dissent.</li>"
             "<li>~149,000 US private foundations hold ~$1.8T; most have 0 to 10 staff (Cause IQ; FoundationMark).</li>"
             "<li>Built by a practitioner in institutional manager selection and allocation.</li></ul></section>")
    S.append("<section><h2>What is new</h2><table><tr><th>Option</th><th>Gap</th></tr>"
             "<tr><td>eVestment</td><td>Data and screens; its agent serves managers seeking capital</td></tr>"
             "<tr><td>Aladdin / eFront Copilot</td><td>Query assistant, no committee or decision</td></tr>"
             "<tr><td>Consultants</td><td>Weeks, expensive, one opaque view</td></tr>"
             "<tr><td>ChatGPT / Claude</td><td>Does math in text, no access control or audit</td></tr>"
             "<tr><td>TradingAgents</td><td>Trades one stock; not the asset-owner workflow</td></tr></table>"
             "<p><b>ACIO</b>: owner policy &rarr; allocation &rarr; ranked, sized manager lineups &rarr; Red Team &rarr; memo. Code does the math, an information barrier separates roles, every claim cites a tool result.</p></section>")
    S.append(f"<section><h2>Seven agents, one committee</h2>{diagram(True)}</section>")
    shot = img("chat_live.png") or img("chat_run_mock.png")
    S.append("<section><h2>Demo: ask &rarr; committee &rarr; memo</h2><div class='two'>"
             f"<div>{shot or todo('chat screenshot')}</div><div>{img('run_top.png') or todo('run dashboard screenshot')}"
             f"<div style='margin-top:10px'>{img('class_us_eq.png') or ''}</div></div></div></section>")
    S.append(f"<section><h2>Every message, replayed</h2><div class='two'><div>{img('run_beams.png') or todo('beam replay screenshot')}</div>"
             f"<div>{img('run_alloc.png') or todo('allocation screenshot')}</div></div></section>")
    if have_tok:
        S.append("<section><h2>Cost, measured</h2><div class='kp'>"
                 f"<div><div class='lbl'>Cost per run</div><div class='big'>{usd(pr['cost_usd'])}</div></div>"
                 f"<div><div class='lbl'>Tokens per run</div><div class='big'>{pr['total'] / 1000:,.0f}k</div></div>"
                 f"<div><div class='lbl'>Time per run</div><div class='big'>{mmss(pr['elapsed_s'])}</div></div>"
                 f"<div><div class='lbl'>Per user / month</div><div class='big'>{usd(tok['per_user_month_usd'], 2)}</div></div>"
                 f"<div><div class='lbl'>10,000 users / month</div><div class='big'>{usd(tok['scale'][10000]['model_cost_month'], 0)}</div></div>"
                 f"<div><div class='lbl'>Input from cache</div><div class='big'>{pr['cache_share']:.0%}</div></div></div>"
                 f"<p class='mute'>{tok['n_runs']} live runs of gpt-6-luna; {tok['runs_per_user_month']:g} runs per user per month assumed. "
                 f"Same tokens on gpt-6.1-sol: {usd(tok['same_tokens_other_models'].get('gpt-6.1-sol'))} per run.</p></section>")
    else:
        S.append(f"<section><h2>Cost, measured</h2><p>{todo('live runs (python -m app.evidence) then rebuild')}</p></section>")
    if sf:
        res = sf["results"]; n = sum(r["passed"] for r in res)
        S.append(f"<section><h2>Safety: {n} of {len(res)} defences held</h2><div class='two'><div><ul>"
                 "<li><b>Attacks:</b> injection in the ask and in manager documents flagged and wrapped; information barrier; no SQL, file or network tools</li>"
                 "<li><b>Malfunctions:</b> uncited facts dropped; off-role tools refused; silent overrides and ignored objections escalate; loops capped</li>"
                 "<li><b>Overspending:</b> time, token, dollar and call budgets checked before every call</li></ul></div>"
                 f"<div>{img('safety.png') or ''}</div></div></section>")
    base_h = (pilot.get("pilot") or {}).get("baseline_hours")
    imp = []
    if have_tok:
        imp.append(f"<li>First-draft committee package in <b>{mmss(pr['elapsed_s'])}</b> for <b>{usd(pr['cost_usd'])}</b></li>")
    imp.append(f"<li>Today: {e(str(base_h) + ' analyst hours') if base_h else todo('baseline hours')}</li>")
    q = (pilot.get("pilot") or {}).get("quote")
    imp.append(f"<li>Pilot user: {('“' + e(q) + '”') if q else todo('pilot quote')}</li>")
    imp.append("<li>Deflated Sharpe reproduces the paper's example (0.9004 / 0.9505)</li>")
    S.append("<section><h2>Impact</h2><ul>" + "".join(imp) + "</ul></section>")
    S.append("<section><h2>Limits and next steps</h2><ul>"
             "<li>Infeasible briefs (e.g. 5% real return with a 25% loss limit) are escalated with the trade-off, not forced; that is real portfolio construction</li>"
             "<li>Anonymised sample managers; in-sample mix statistics; no liability model; one model family for all agents</li>"
             "<li>Next: expert-scored evaluation set; upload of current policy and holdings; secure sandbox for licensed and private-fund data</li>"
             "<li>Then: hedge fund and private markets heads, a pool of Red Team challengers, an independent risk officer, and peer-to-peer consultation between specialist agents</li>"
             "</ul><p class='mute'>Decision support for a human CIO. Not investment advice.</p></section>")
    slides = "".join(S)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>ACIO slides</title>"
            "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css'>"
            "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/black.css'>"
            f"<style>{SLIDE_CSS}</style></head><body><div class='reveal'><div class='slides'>{slides}</div></div>"
            "<script src='https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js'></script>"
            "<script>if(window.Reveal){Reveal.initialize({hash:true,width:1280,height:760,margin:0.04,transition:'fade'});}"
            "else{document.body.classList.add('noreveal');const s=[...document.querySelectorAll('.slides>section')];let i=0;"
            "const show=()=>s.forEach((x,k)=>x.classList.toggle('on',k===i));show();"
            "document.addEventListener('keydown',ev=>{if(['ArrowRight','ArrowDown',' ','PageDown'].includes(ev.key))i=Math.min(i+1,s.length-1);"
            "if(['ArrowLeft','ArrowUp','PageUp'].includes(ev.key))i=Math.max(i-1,0);show();});}</script></body></html>")


def comparison_html() -> str | None:
    runs = live_runs()
    a = [r for r in runs if r.get("label") == "endowment" and r.get("status") not in ("RUN_STOPPED", "ESCALATED")]
    b = [r for r in runs if r.get("label") == "endowment-no-followups" and r.get("status") not in ("RUN_STOPPED", "ESCALATED")]
    if not a or not b:
        return None
    a, b = a[-1], b[-1]

    def w(r):
        d = load(r["_dir"] / "decision.json") or {}
        return (d.get("asset_allocation") or {}).get("weights_pct", {}), {p["manager_id"] for p in d.get("manager_portfolio", [])}
    (wa, ma), (wb, mb) = w(a), w(b)
    diff = sum(abs(float(wa.get(k, 0)) - float(wb.get(k, 0))) for k in set(wa) | set(wb)) / 2
    return ("<table><tr><th></th><th class='n'>Follow-ups on</th><th class='n'>Follow-ups off</th></tr>"
            f"<tr><td>Follow-ups used</td><td class='n'>{a['budgets']['followups_used']}</td><td class='n'>{b['budgets']['followups_used']}</td></tr>"
            f"<tr><td>Tokens</td><td class='n'>{a['tokens']['total']:,}</td><td class='n'>{b['tokens']['total']:,}</td></tr>"
            f"<tr><td>Cost</td><td class='n'>{usd(a['cost_usd'])}</td><td class='n'>{usd(b['cost_usd'])}</td></tr>"
            f"<tr><td>Time</td><td class='n'>{mmss(a['elapsed_s'])}</td><td class='n'>{mmss(b['elapsed_s'])}</td></tr></table>"
            f"<p>Allocation difference between the two runs: {diff:.1f} percentage points of the portfolio moved; "
            f"{len(ma & mb)} of {len(ma | mb)} managers appear in both lineups. Part of any difference is run-to-run variation of the model, "
            "not the follow-ups alone; a single pair cannot separate the two, so this is evidence of the cost saved and an indication, not proof, of the quality effect.</p>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build report.html and slides.html")
    ap.add_argument("--shots", action="store_true", help="re-capture screenshots with playwright first")
    a = ap.parse_args(argv)
    from app import tokenomics
    runs = live_runs()
    bstart = tokenomics.batch_start(ROOT)
    tr = pick_trace_run([r for r in runs if not bstart or r["run_id"] >= bstart])
    if a.shots:
        # run pages are captured only from a LIVE run, so a scripted run can never stand in for the traced run
        capture(tr["_dir"] if tr else None)
    if not (ROOT / "output" / "safety.json").exists():
        from app import safety
        safety.run_all()
    owner_text = None
    if tr:
        from app.chat import EXAMPLES
        lab = (tr.get("label") or "").split("-")[0]
        owner_text = next((x["text"] for x in EXAMPLES if x["id"] == lab), None)
        if owner_text is None:
            pol = (load(tr["_dir"] / "decision.json") or {}).get("policy", {})
            owner_text = "(typed in the chat; inferred policy shown in the next row)"
    ctx = {"all_runs": live_runs(), "tok": tokenomics.summarise(ROOT), "safety": load(ROOT / "output" / "safety.json"), "trace": tr,
           "pilot": load(HERE / "pilot.json") or {}, "owner_text": owner_text, "compare": comparison_html()}
    (ROOT / "report.html").write_text(build_report(ctx), encoding="utf-8")
    (ROOT / "slides.html").write_text(build_slides(ctx), encoding="utf-8")
    print("wrote report.html and slides.html")
    if TODO:
        print("\nStill to fill (shown as yellow boxes):")
        for t in dict.fromkeys(TODO):
            print(" -", t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
