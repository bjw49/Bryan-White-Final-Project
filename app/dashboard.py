"""Static dashboard built from output/<run>/ files. No external libraries; every model-written value is HTML-escaped.
Pages (written to output/dashboard/):
  index.html          all runs, with status, final allocation and flags; links to the asset-class pages
  run-<id>.html       one run: decision or stop reason, allocation, lineup, Red Team, flags, memo, audit links
  class-<CLS>.html    one asset class from its most recent run: selected managers, next-best managers, full standalone ranking
Build: python3 -m app.dashboard   (also called at the end of every pipeline run)."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

CLASSES = ["US_EQ", "INTL_EQ", "US_IG", "US_HY", "CASH"]
NAMES = {"US_EQ": "US Equity", "INTL_EQ": "International Equity", "US_IG": "US Investment Grade (corporate)",
         "US_HY": "US High Yield", "CASH": "Cash"}
HEAD_OF = {"US_EQ": "head_us_eq", "INTL_EQ": "head_intl_eq", "US_IG": "head_us_ig", "US_HY": "head_us_hy"}
AUDIT = "../../audit"
CSS = """html.big{zoom:1.4}

:root{--bg:#05090f;--panel:#0a1420;--line:rgba(25,230,255,.22);--cy:#19e6ff;--cy2:#0a8fb3;--amb:#ffb000;--tx:#e6f0f8;--mute:#8fa6bb;--red:#ff4d6d;--grn:#3dffb4}
*{box-sizing:border-box}
body{margin:0;font:17px/1.55 "Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);max-width:none;margin:0;padding:22px 2.5vw 60px;
  background:radial-gradient(ellipse at 50% -10%,#0d2236 0%,#05090f 60%) fixed}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.5;
  background-image:linear-gradient(rgba(25,230,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(25,230,255,.05) 1px,transparent 1px);background-size:40px 40px}
h1{font:700 30px "Segoe UI",sans-serif;letter-spacing:.18em;text-transform:uppercase;color:var(--cy);text-shadow:0 0 12px rgba(25,230,255,.55);margin:0 0 6px}
h2{font:600 17px "Segoe UI",sans-serif;letter-spacing:.24em;text-transform:uppercase;color:var(--cy);margin:30px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
nav{margin:10px 0 20px;border-bottom:1px solid var(--line);padding-bottom:10px}
nav a{margin-right:18px;color:var(--cy);text-decoration:none;font:600 15px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase}
nav a:hover{text-shadow:0 0 8px var(--cy)}
a{color:var(--cy)}
.note{color:var(--mute);font-size:15px}
table{border-collapse:collapse;width:100%;font:15.5px ui-monospace,Consolas,"Courier New",monospace;margin:8px 0;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid rgba(25,230,255,.12);padding:8px 10px;text-align:left;vertical-align:top}
th{background:rgba(25,230,255,.07);color:var(--mute);font:600 13.5px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase}
td.num{text-align:right;white-space:nowrap;color:var(--amb)}
.tag{display:inline-block;padding:2px 10px;font:600 14px "Segoe UI",sans-serif;letter-spacing:.1em;text-transform:uppercase;border:1px solid var(--line);color:var(--tx);background:rgba(0,0,0,.4)}
.ok{color:var(--grn);border-color:rgba(61,255,180,.45)}
.esc{color:var(--red);border-color:rgba(255,77,109,.55)}
.warn{color:var(--amb);border-color:rgba(255,176,0,.55)}
pre{white-space:pre-wrap;background:rgba(0,0,0,.55);border:1px solid var(--line);padding:14px;font:15px ui-monospace,Consolas,monospace;color:#bfe9f5}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{position:relative;background:linear-gradient(180deg,rgba(10,20,32,.95),rgba(6,12,20,.95));border:1px solid var(--line);padding:14px 16px}
.card::before,.card::after{content:"";position:absolute;width:12px;height:12px;border:2px solid var(--cy);filter:drop-shadow(0 0 4px var(--cy));pointer-events:none}
.card::before{top:-1px;left:-1px;border-right:0;border-bottom:0}.card::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.card b{display:block;font:600 14px "Segoe UI",sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--cy)}
.big{font:700 40px ui-monospace,Consolas,monospace;color:var(--amb);text-shadow:0 0 12px rgba(255,176,0,.45);font-variant-numeric:tabular-nums;line-height:1.1}
.label{font:600 13.5px "Segoe UI",sans-serif;letter-spacing:.16em;text-transform:uppercase;color:var(--mute)}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:14px;margin:10px 0}
.kpi{position:relative;border:1px solid var(--line);background:rgba(0,0,0,.35);padding:14px 16px}
.net{width:100%;max-width:none;display:block;height:auto;background:rgba(0,0,0,.35);border:1px solid var(--line);margin:8px 0}
.net text{font:600 15px "Segoe UI",sans-serif;fill:var(--tx)}
.net .lbl{fill:var(--cy)}
.roster{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin-top:8px}
.roster .card{display:flex;gap:12px;align-items:center}
.mini{flex:0 0 auto;width:46px;height:46px}
.mini .bob{animation:bob 3.2s ease-in-out infinite}
.mini .spinner{animation:spin 6s linear infinite;transform-origin:32px 32px}
.mini .bars rect{transform-origin:bottom;animation:bars 1.6s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}
@keyframes bars{0%,100%{transform:scaleY(.55)}50%{transform:scaleY(1)}}
@keyframes spin{to{transform:rotate(360deg)}}
html{background:#05090f}
.kpis.k4{grid-template-columns:repeat(4,minmax(170px,1fr))}
.memo{background:rgba(0,0,0,.45);border:1px solid var(--line);padding:6px 22px 16px}
.memo h3{font:600 15px "Segoe UI",sans-serif;letter-spacing:.18em;text-transform:uppercase;color:var(--amb);margin:20px 0 8px}
.memo h2{border:0;margin-top:14px}
.memo p,.memo li{color:#cfe3ee}
.memo code{color:var(--cy);font-size:14px}
.tl{width:100%;height:auto;margin:6px 0 4px}
.tl text{font:600 13px "Segoe UI",sans-serif;fill:var(--tx)}
.tl .ax{fill:var(--mute);font-weight:400}
.pass{color:var(--grn);font-weight:700}.fail{color:var(--red);font-weight:700}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.grid2,.kpis.k4{grid-template-columns:1fr}}
"""


def e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _pct(x) -> str:
    return "" if x is None else f"{float(x):g}%"


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{e(title)}</title><style>{CSS}</style>"
            # 1.4x scale for laptop readability when opened directly; inside the app's frame the app already scales
            "<script>if(window.self===window.top)document.documentElement.classList.add('big')</script></head><body>"
            f"<nav><a href='index.html'>All runs</a>" +
            "".join(f"<a href='class-{c}.html'>{e(NAMES[c])}</a>" for c in CLASSES) +
            "<a href='tokenomics.html'>Tokenomics</a><a href='safety.html'>Safety</a>" +
            f"</nav>{body}{roster_block()}<p class='note'>Decision support for a human CIO. Not investment advice. Returns are net of fees (owner instruction), in USD.</p>"
            "</body></html>")


def run_dirs(root: Path) -> list[Path]:
    out = Path(root) / "output"
    if not out.exists():
        return []
    dirs = [d for d in out.iterdir() if d.is_dir() and d.name != "dashboard" and (d / "memo.md").exists()]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def load_run(d: Path) -> dict:
    return {"id": d.name, "dec": _load(d / "decision.json"), "esc": _load(d / "escalation.json"),
            "flags": _load(d / "flags.json") or [], "props": _load(d / "proposals.json") or {},
            "rt": _load(d / "red_team.json"), "tel": _load(d / "telemetry.json") or {},
            "memo": (d / "memo.md").read_text(encoding="utf-8")}


def status_of(r: dict) -> tuple[str, str]:
    if r["esc"]:
        return "RUN STOPPED", "esc"
    return (r["dec"] or {}).get("decision", "?"), "ok"


def latest_head(runs: list[dict], cls: str):
    if cls not in HEAD_OF:  # CASH has no Head and no managers
        return None, None
    agent = HEAD_OF[cls]
    for r in runs:
        h = r["props"].get(agent)
        if h:
            return r, h
    return None, None


# --------------------------------------------------------------------------- pages
def _mmss(r: dict) -> str:
    el = (r.get("tel") or {}).get("elapsed_s")
    if el is None:
        return "-"
    mm, ss = divmod(int(round(float(el))), 60)
    return f"{mm}:{ss:02d}"


def _tok(r: dict) -> str:
    t = (r.get("tel") or {}).get("tokens", {}).get("total")
    return "-" if t is None else f"{int(t):,}"


def index_page(runs: list[dict]) -> str:
    rows = []
    for r in runs:
        st, cls_ = status_of(r)
        alloc = ""
        if r["dec"]:
            w = r["dec"].get("asset_allocation", {}).get("weights_pct", {})
            alloc = ", ".join(f"{c} {_pct(w[c])}" for c in CLASSES if w.get(c))
        stop = f"stopped at {e((r['esc'] or {}).get('stage'))}" if r["esc"] else ""
        rows.append(f"<tr><td><a href='run-{e(r['id'])}.html'>{e(r['id'])}</a></td>"
                    f"<td><span class='tag {cls_}'>{e(st)}</span> {stop}</td><td>{e(alloc)}</td>"
                    f"<td class='num'>{len(r['flags'])}</td>"
                    f"<td class='num'>{_mmss(r)}</td><td class='num'>{_tok(r)}</td><td class='num'>{_usd((r.get('tel') or {}).get('cost_usd', 0))}</td></tr>")
    table = ("<table><tr><th>Run</th><th>Status</th><th>Final class weights</th><th class='num'>Flags</th>"
             "<th class='num'>Time</th><th class='num'>Tokens</th><th class='num'>Cost</th></tr>"
             + ("".join(rows) if rows else "<tr><td colspan=7>No runs yet. Run: python3 -m app.run --mock --owner \"...\"</td></tr>") + "</table>")
    cards = []
    for c in CLASSES:
        r, h = latest_head(runs, c)
        if c == "CASH":
            txt = "No managers: cash is a plain sleeve."
        elif h is None:
            txt = "No lineup yet."
        else:
            txt = f"{len(h.get('selected', []))} selected, {len(h.get('ranking', []))} ranked. Run {e(r['id'])}."
        cards.append(f"<div class='card'><a href='class-{c}.html'><b>{e(NAMES[c])}</b></a><br><span class='note'>{txt}</span></div>")
    live = [r for r in runs if (r.get("tel") or {}).get("model_calls")]
    summary = ""
    if runs:
        done = [r for r in runs if not r["esc"]]
        avg_cost = sum(float(r["tel"].get("cost_usd", 0)) for r in live) / len(live) if live else None
        avg_t = sum(float(r["tel"].get("elapsed_s", 0)) for r in live) / len(live) if live else None
        summary = (f"<div class='kpis k4'><div class='kpi'><div class='label'>Runs</div><div class='big'>{len(runs)}</div>"
                   f"<div class='note'>{len(live)} live &middot; {len(runs) - len(live)} mock</div></div>"
                   f"<div class='kpi'><div class='label'>Reached a decision</div><div class='big'>{len(done)}</div><div class='note'>{len(runs) - len(done)} stopped by a control (no recommendation)</div></div>"
                   f"<div class='kpi'><div class='label'>Avg live cost</div><div class='big'>{_usd(avg_cost) if avg_cost is not None else '-'}</div><div class='note'><a href='tokenomics.html'>tokenomics</a></div></div>"
                   f"<div class='kpi'><div class='label'>Avg live time</div><div class='big'>{(f'{int(avg_t // 60)}:{int(avg_t % 60):02d}') if avg_t is not None else '-'}</div><div class='note'>limit 10:00</div></div></div>")
    body = (f"<h1>Investment decision runs</h1><p class='note'>Each run is one pass of the agent pipeline on one owner description. "
            f"Stopped runs ended before a recommendation (a budget, contract or integrity control fired); the reason is shown on the run page.</p>{summary}"
            f"<h2>Runs</h2>{table}<h2>Asset classes</h2><div class='cards'>{''.join(cards)}</div>")
    return _page("Investment decision runs", body)



ROSTER = [
    ("acio", "The Chairman", "Acting CIO", "Final say on allocation and selection", "#ffb000"),
    ("aar", "The Quant", "Asset Allocation & Risk", "Proposes the mix and ranges", "#19e6ff"),
    ("head_us_eq", "The Stock Picker", "Head of US Equity", "Ranks US equity managers", "#3dffb4"),
    ("head_intl_eq", "The Globe Trotter", "Head of International Equity", "Ranks international managers", "#19e6ff"),
    ("head_us_ig", "The Rates Desk", "Head of US IG Fixed Income", "Ranks investment-grade managers", "#7dd3fc"),
    ("head_us_hy", "The Credit Hawk", "Head of US High Yield", "Ranks high-yield managers", "#ff8a5b"),
    ("red_team", "The Prosecutor", "Red Team", "Challenges the integrated portfolio", "#ff4d6d"),
]
NAME_OF = {k: n for k, n, *_ in ROSTER} | {"owner": "Owner"}


def mini_avatar(k: str, color: str) -> str:
    """Original illustrations drawn in code, one per character, matched to its desk."""
    shapes = {
        "acio": f"<circle cx='32' cy='32' r='9' fill='{color}' fill-opacity='.85'/><circle cx='32' cy='32' r='20' fill='none' stroke='{color}' stroke-opacity='.5'/>",
        "aar": f"<g class='bars'><rect x='12' y='22' width='7' height='30' fill='{color}'/><rect x='23' y='12' width='7' height='40' fill='{color}'/><rect x='34' y='28' width='7' height='24' fill='{color}'/><rect x='45' y='16' width='7' height='36' fill='{color}'/></g>",
        "head_us_eq": f"<circle cx='28' cy='28' r='10' fill='none' stroke='{color}' stroke-width='3'/><line x1='36' y1='36' x2='50' y2='50' stroke='{color}' stroke-width='3'/>",
        "head_intl_eq": f"<g class='spinner'><circle cx='32' cy='32' r='18' fill='none' stroke='{color}' stroke-width='2'/><ellipse cx='32' cy='32' rx='8' ry='18' fill='none' stroke='{color}'/><line x1='14' y1='32' x2='50' y2='32' stroke='{color}'/></g>",
        "head_us_ig": f"<path d='M8 48 Q20 40 32 30 T56 16' fill='none' stroke='{color}' stroke-width='3'/>",
        "head_us_hy": f"<path d='M32 8 L44 28 L36 28 L36 56 L28 56 L28 28 L20 28 Z' fill='{color}' fill-opacity='.85'/>",
        "red_team": f"<text x='32' y='46' text-anchor='middle' font-size='40' font-weight='700' fill='{color}' font-family='Segoe UI,sans-serif'>?</text>",
    }
    inner = shapes.get(k, f"<circle cx='32' cy='28' r='9' fill='{color}' fill-opacity='.8'/>")
    return (f"<svg class='mini' viewBox='0 0 64 64' aria-hidden='true'><rect x='1' y='1' width='62' height='62' fill='#06101b' "
            f"stroke='{color}' stroke-opacity='.5'/><g class='bob'>{inner}</g></svg>")


def roster_block() -> str:
    cards = "".join(f"<div class='card'>{mini_avatar(k, c)}<div><b>{e(n)}</b><span class='note'>{e(role)}<br>{e(src)}</span></div></div>"
                    for k, n, role, src, c in ROSTER)
    return ("<h2>The committee</h2><p class='note'>Original characters, named for the desk each agent runs. "
            "They are labels for the agents; the reasoning comes from each agent's prompt, tools and data.</p>"
            f"<div class='roster'>{cards}</div>")


def _inline(t: str) -> str:
    import re
    t = e(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def md_to_html(md: str) -> str:
    """Small Markdown renderer for the memo (headings, tables, bullets, bold, code). Text is escaped first."""
    out, para, i = [], [], 0
    lines = (md or "").splitlines()

    def flush():
        if para:
            out.append("<p>" + " ".join(_inline(x) for x in para) + "</p>"); para.clear()
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            flush(); i += 1; continue
        if ln.startswith("#"):
            flush(); lvl = len(ln) - len(ln.lstrip("#")); txt = ln.lstrip("#").strip()
            out.append(f"<h2>{_inline(txt)}</h2>" if lvl == 1 else f"<h3>{_inline(txt)}</h3>"); i += 1; continue
        if ln.lstrip().startswith("|"):
            flush(); rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head, *body = rows
                out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" +
                           "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</table>")
            continue
        if ln.lstrip().startswith(("- ", "* ")):
            flush(); items = []
            while i < len(lines) and lines[i].lstrip().startswith(("- ", "* ")):
                items.append(lines[i].lstrip()[2:]); i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>"); continue
        para.append(ln.strip()); i += 1
    flush()
    return "<div class='memo'>" + "".join(out) + "</div>"


def _usd(x) -> str:
    x = float(x or 0)
    return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


def kpi_block(r: dict) -> str:
    tel = r.get("tel") or {}
    if not tel:
        return "<p class='note'>No telemetry for this run (it was produced before the telemetry update).</p>"
    el = float(tel.get("elapsed_s", 0)); mm, ss = divmod(int(round(el)), 60)
    tok = tel.get("tokens", {})
    cached = int(tok.get("cached", 0)); prompt = int(tok.get("prompt", 0))
    share = f"{cached / prompt:.0%} of input cached" if prompt else "no input tokens"
    mock = "<p class='note'>Mock run: the scripted stand-in model uses no tokens, so tokens and cost are zero. Live runs record the counts the gateway reports.</p>" if tel.get("mock") else ""
    return (f"<div class='kpis k4'>"
            f"<div class='kpi'><div class='label'>Elapsed</div><div class='big'>{mm:d}:{ss:02d}</div><div class='note'>limit 10:00 &middot; budget {int(tel.get('budgets', {}).get('time_s', 420))} s</div></div>"
            f"<div class='kpi'><div class='label'>Tokens</div><div class='big'>{int(tok.get('total', 0)):,}</div>"
            f"<div class='note'>input {prompt:,} ({share}) &middot; output {int(tok.get('completion', 0)):,}</div></div>"
            f"<div class='kpi'><div class='label'>Cost</div><div class='big'>{_usd(tel.get('cost_usd', 0))}</div><div class='note'>{'no model calls' if tel.get('mock') else e(tel.get('model', 'gpt-6-luna')) + ' at course prices'}</div></div>"
            f"<div class='kpi'><div class='label'>Model calls</div><div class='big'>{int(tel.get('model_calls', 0))}</div>"
            f"<div class='note'>follow-ups {tel.get('budgets', {}).get('followups_used', 0)} of {tel.get('budgets', {}).get('followups_max', 2)}</div></div>"
            "</div>" + mock)


def timeline_block(tel: dict) -> str:
    st = [x for x in tel.get("stages", []) if x.get("end") is not None]
    if not st:
        return ""
    tmax = max(float(tel.get("elapsed_s", 0)), max(x["end"] for x in st), 1.0)
    W, L, rowh = 1000, 210, 30
    H = rowh * len(st) + 34
    bars = []
    for i, x in enumerate(st):
        x0 = L + (W - L - 20) * x["start"] / tmax; x1 = L + (W - L - 20) * x["end"] / tmax
        y = 8 + i * rowh
        bars.append(f"<text x='{L - 10}' y='{y + 17}' text-anchor='end'>{e(x['stage'])}</text>"
                    f"<rect x='{x0:.1f}' y='{y + 3}' width='{max(x1 - x0, 2):.1f}' height='{rowh - 10}' fill='#19e6ff' fill-opacity='.75'/>"
                    f"<text x='{x1 + 6:.1f}' y='{y + 17}' class='ax'>{x['end'] - x['start']:.0f}s</text>")
    ticks = "".join(f"<text x='{L + (W - L - 20) * t / tmax:.1f}' y='{H - 4}' class='ax' text-anchor='middle'>{(f'{t:.0f}' if tmax >= 20 else f'{t:.1f}')}s</text>"
                    for t in [tmax * k / 4 for k in range(5)])
    return (f"<h2>Stage timeline</h2><svg class='tl' viewBox='0 0 {W} {H}' role='img' aria-label='Stage timeline'>"
            f"{''.join(bars)}{ticks}</svg>")


def agent_table(tel: dict) -> str:
    rows = "".join(f"<tr><td>{e(NAME_OF.get(a, a))}</td><td class='num'>{v['calls']}</td>"
                   f"<td class='num'>{v['prompt']:,}</td><td class='num'>{v.get('cached', 0):,}</td><td class='num'>{v['completion']:,}</td>"
                   f"<td class='num'>{v.get('latency_s', 0):.0f}s</td><td class='num'>{_usd(v.get('cost_usd', 0))}</td></tr>"
                   for a, v in sorted(tel.get("by_agent", {}).items(), key=lambda kv: -(kv[1]['prompt'] + kv[1]['completion'])))
    if not rows:
        return ""
    return ("<h2>Tokens by agent</h2><table><tr><th>Agent</th><th class='num'>Calls</th><th class='num'>Input</th><th class='num'>Cached input</th>"
            f"<th class='num'>Output</th><th class='num'>Model time</th><th class='num'>Cost</th></tr>{rows}</table>")


# Fixed layout: the Chairman in the centre, the Quant above, equity Heads left/right top, fixed income Heads left/right
# lower, the Prosecutor below, the owner in the corner (receives only the final decision).
POS = {"acio": (380, 215), "aar": (380, 50), "head_intl_eq": (120, 120), "head_us_eq": (640, 120),
       "head_us_hy": (120, 310), "head_us_ig": (640, 310), "red_team": (380, 380), "owner": (700, 420)}


def beam_block(tel: dict) -> str:
    events = [ev for ev in tel.get("events", []) if ev.get("from") in POS and ev.get("to") in POS]
    if not events:
        return ""
    tmax = max(float(ev["t"]) for ev in events) or 1.0
    span = 24.0  # the replay compresses the real timing into this many seconds
    color = dict((kk, c) for kk, _, _, _, c in ROSTER)
    nodes = "".join(
        f"<circle cx='{x}' cy='{y}' r='{30 if k == 'acio' else 22}' fill='#06101b' stroke='{color.get(k, '#8fa6bb')}' stroke-width='2.5'/>"
        f"<text x='{x}' y='{y + (46 if k == 'acio' else 38)}' text-anchor='middle' font-size='15'>{e(NAME_OF.get(k, k).replace('The ', ''))}</text>"
        for k, (x, y) in POS.items())
    moves = []
    for ev in events:
        (x1, y1), (x2, y2) = POS[ev["from"]], POS[ev["to"]]
        b = round(float(ev["t"]) / tmax * span, 2)
        col = "#ffb000" if ev["from"] == "acio" else "#19e6ff"
        moves.append(
            f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{col}' stroke-width='2' opacity='0'>"
            f"<animate attributeName='opacity' values='0;1;.25' keyTimes='0;.2;1' dur='1.6s' begin='{b}s' fill='freeze'/></line>"
            f"<circle r='6' fill='{col}' opacity='0'><set attributeName='opacity' to='1' begin='{b}s'/>"
            f"<animateMotion dur='1.4s' begin='{b}s' fill='freeze' path='M{x1},{y1} L{x2},{y2}'/></circle>")
    svg = (f"<svg class='net' viewBox='0 0 760 470' role='img' aria-label='Agent messages, replayed'>"
           f"<ellipse cx='380' cy='215' rx='265' ry='165' fill='none' stroke='rgba(25,230,255,.10)'/>{''.join(moves)}{nodes}</svg>")
    rows = "".join(f"<tr><td class='num'>{float(ev['t']):.0f}s</td><td>{e(NAME_OF.get(ev['from'], ev['from']))} &rarr; "
                   f"{e(NAME_OF.get(ev['to'], ev['to']))}</td><td>{e(ev['text'])}</td></tr>" for ev in events)
    return (f"<h2>Agent messages</h2><p class='note'>Replay of the wire during the run. Amber beams leave the Chairman; "
            f"cyan beams come back. Real timing is compressed into {span:.0f} seconds.</p>{svg}"
            f"<table><tr><th class='num'>At</th><th>From &rarr; to</th><th>Message</th></tr>{rows}</table>")


def run_page(r: dict) -> str:
    st, cls_ = status_of(r)
    dec, rt = r["dec"] or {}, r["rt"]
    aar = (r["props"].get("aar") or {}).get("recommended", {})
    aar_w, aar_rng = aar.get("weights_pct", {}), aar.get("ranges_pct", {})
    final_w = dec.get("asset_allocation", {}).get("weights_pct", {})
    alloc_rows = "".join(f"<tr><td>{c}</td><td class='num'>{_pct(aar_w.get(c))}</td>"
                         f"<td class='num'>{e(aar_rng.get(c, ''))}</td><td class='num'>{_pct(final_w.get(c))}</td></tr>" for c in CLASSES)
    alloc = ("<table><tr><th>Class</th><th class='num'>AAR</th><th>AAR range</th><th class='num'>ACIO final</th></tr>"
             f"{alloc_rows}</table>")
    ad = dec.get("allocation_decision")
    if ad:
        alloc += f"<p>Adopted: <b>{e(ad.get('adopted'))}</b>. {e(ad.get('reason'))}</p>"
    lineup = dec.get("manager_portfolio", [])
    lineup_html = ("<table><tr><th>Manager</th><th>Class</th><th class='num'>Weight in class</th><th class='num'>Weight of total</th><th>Role</th></tr>"
                   + "".join(f"<tr><td>{e(p['manager_id'])}</td><td>{e(p['class'])}</td><td class='num'>{_pct(p['weight_in_class_pct'])}</td>"
                             f"<td class='num'>{_pct(p['weight_total_pct'])}</td><td>{e(p.get('role'))}</td></tr>" for p in lineup)
                   + "</table>") if lineup else "<p class='note'>No lineup: the run did not reach a decision.</p>"
    rt_html = "<p class='note'>No Red Team report saved for this run.</p>"
    if rt:
        resp = {x["objection_id"]: x for x in dec.get("red_team_responses", [])}
        objs = "".join(f"<li><b>{e(o['id'])} ({e(o['severity'])}, {e(o['target'])})</b>: {e(o['claim'])} "
                       f"<span class='note'>Fix criterion: {e(o['fix_criterion'])}</span><br>"
                       f"ACIO: <b>{e(resp.get(o['id'], {}).get('response', 'NO RESPONSE'))}</b>. {e(resp.get(o['id'], {}).get('action', ''))}</li>"
                       for o in rt.get("objections", []))
        rt_html = (f"<p>Verdict: <b>{e(rt.get('verdict'))}</b>. {e(rt.get('verdict_reason'))}</p><ul>{objs}</ul>")
    esc_html = ""
    if r["esc"]:
        esc_html = f"<h2>Run stopped: no recommendation produced</h2><p>Stage: <b>{e(r['esc'].get('stage'))}</b></p><pre>{e(r['esc'].get('detail'))}</pre>"
    flags = "".join(f"<li>{e(f)}</li>" for f in r["flags"]) or "<li class='note'>None</li>"
    tel = r.get("tel") or {}
    owner = (r["props"].get("acio_policy") or {})
    body = (f"<h1>Run {e(r['id'])}</h1><p><span class='tag {cls_}'>{e(st)}</span> "
            f"<span class='note'>{'mock (scripted stand-in)' if tel.get('mock') else e(tel.get('model', ''))}</span></p>"
            f"{kpi_block(r)}{esc_html}{timeline_block(tel)}"
            f"<h2>Asset allocation</h2>{alloc}<h2>Manager lineup (computed by code)</h2>{lineup_html}"
            f"<h2>Red Team</h2>{rt_html}<h2>Data and process flags</h2><ul>{flags}</ul>"
            f"<h2>Open items for the human CIO</h2><ul>{''.join(f'<li>{e(x)}</li>' for x in dec.get('open_items_for_human_cio', [])) or '<li class=note>None</li>'}</ul>"
            f"{beam_block(tel)}{agent_table(tel)}"
            f"<h2>Investment memo</h2>{md_to_html(r['memo'])}"
            f"<h2>Audit trail</h2><p><a href='{AUDIT}/{e(r['id'])}_summary.md'>Plain-language summary</a> · "
            f"<a href='{AUDIT}/{e(r['id'])}_trace.md'>Full trace (every model turn, tool call and result)</a></p>")
    return _page(f"Run {r['id']}", body)


def _f(x, pct=True, d=1):
    if x is None:
        return ""
    return f"{float(x) * 100:.{d}f}%" if pct else f"{float(x):.2f}"


def class_page(cls: str, run: dict | None, h: dict | None, stats: dict | None = None) -> str:
    stats = stats or {}
    name = NAMES[cls]
    if cls == "CASH":
        return _page(name, f"<h1>{e(name)}</h1><p>Cash is a plain sleeve with no managers.</p>")
    if h is None or run is None:
        return _page(name, f"<h1>{e(name)}</h1><p>No lineup yet. Run the pipeline to populate this page.</p>")
    rk = {x["manager_id"]: x for x in h.get("ranking", [])}
    sel = {x["manager_id"]: x for x in h.get("selected", [])}
    nsel = {x["manager_id"]: x for x in h.get("not_selected", [])}
    dec = run.get("dec") or {}
    class_weight = (dec.get("asset_allocation", {}).get("weights_pct", {}) or {}).get(cls)
    sel_rows = "".join(
        f"<tr><td><b>{e(m)}</b></td><td class='num'>{_pct(s['weight_pct'])}</td><td>{e(s.get('role'))}</td>"
        f"<td class='num'>{rk.get(m, {}).get('rank', '')}</td><td>{e(s.get('edge'))}</td>"
        f"<td>{e(rk.get(m, {}).get('rationale', ''))}</td><td>{e(', '.join(s.get('key_risks', [])))}</td></tr>"
        for m, s in sel.items())
    ranked_not = sorted([m for m in rk if m not in sel], key=lambda m: rk[m]["rank"])
    next_rows = "".join(
        f"<tr><td><b>{e(m)}</b></td><td class='num'>{rk[m]['rank']}</td><td>{e(rk[m]['rationale'])}</td>"
        f"<td>{e(nsel.get(m, {}).get('why_not_selected', ''))}</td></tr>" for m in ranked_not[:3])
    def st(m):
        z = stats.get(m) or {}
        return (f"<td class='num'>{z.get('n_months', '')}</td><td class='num'>{_f(z.get('ann_excess'))}</td>"
                f"<td class='num'>{_f(z.get('tracking_error'))}</td><td class='num'>{_f(z.get('info_ratio'), False)}</td>"
                f"<td class='num'>{_f(z.get('down_capture'), False)}</td><td class='num'>{_f(z.get('max_dd_manager'), d=0)}</td>")
    full_rows = "".join(
        f"<tr><td class='num'>{x['rank']}</td><td><b>{e(x['manager_id'])}</b></td>"
        f"<td>{'<span class=tag>selected ' + _pct(sel[x['manager_id']]['weight_pct']) + '</span>' if x['manager_id'] in sel else ''}</td>"
        f"{st(x['manager_id'])}<td>{e(x['rationale'])}</td></tr>" for x in sorted(h.get("ranking", []), key=lambda x: x["rank"]))
    cw = f" The class carries {_pct(class_weight)} of the total portfolio in the ACIO's final allocation." if class_weight else ""
    body = (f"<h1>{e(name)}</h1>"
            f"<p class='note'>From run {e(run['id'])}. Head summary: {e(h.get('summary'))}{e(cw)}</p>"
            f"<h2>Selected managers</h2>"
            f"<table><tr><th>Manager</th><th class='num'>Weight in class</th><th>Role</th><th class='num'>Standalone rank</th>"
            f"<th>Edge</th><th>Why this rank</th><th>Key risks</th></tr>{sel_rows or '<tr><td colspan=7>None selected.</td></tr>'}</table>"
            f"<h2>Next-best managers</h2><p class='note'>The three highest-ranked managers the Head did not select.</p>"
            f"<table><tr><th>Manager</th><th class='num'>Standalone rank</th><th>Why this rank</th><th>Why not selected</th></tr>"
            f"{next_rows or '<tr><td colspan=4>None.</td></tr>'}</table>"
            f"<h2>Standalone ranking, all managers</h2>"
            f"<p class='note'>Each manager is ranked alone, as if it were the only hire for this class. Weights are not considered here.</p>"
            f"<p class='note'>Statistics are computed by code over each manager's full history vs the class benchmark, net of fees, USD.</p>"
            f"<table><tr><th class='num'>Rank</th><th>Manager</th><th>Selection</th><th class='num'>Months</th><th class='num'>Excess / yr</th>"
            f"<th class='num'>Tracking error</th><th class='num'>Info ratio</th><th class='num'>Down capture</th><th class='num'>Max DD</th>"
            f"<th>Rationale</th></tr>{full_rows}</table>")
    return _page(name, body)


def tokenomics_page(t: dict) -> str:
    if not t.get("n_runs"):
        return _page("Tokenomics", "<h1>Tokenomics</h1><p>No live runs yet, so there are no measured token counts. "
                     "Run <code>python -m app.evidence</code> (needs PORTKEY_API_KEY), or run the chat without <code>--mock</code>. "
                     "Mock runs use no tokens and are excluded.</p>")
    pr = t["per_run"]
    k = (f"<div class='kpis k4'><div class='kpi'><div class='label'>Cost per run</div><div class='big'>{_usd(pr['cost_usd'])}</div>"
         f"<div class='note'>range {_usd(pr['cost_usd_min'])} to {_usd(pr['cost_usd_max'])}</div></div>"
         f"<div class='kpi'><div class='label'>Tokens per run</div><div class='big'>{pr['total']:,.0f}</div>"
         f"<div class='note'>input {pr['input']:,.0f} &middot; output {pr['output']:,.0f}</div></div>"
         f"<div class='kpi'><div class='label'>Cached input</div><div class='big'>{pr['cache_share']:.0%}</div>"
         f"<div class='note'>{pr['cached_input']:,.0f} tokens at 1/10 price</div></div>"
         f"<div class='kpi'><div class='label'>Time per run</div><div class='big'>{int(pr['elapsed_s'] // 60)}:{int(pr['elapsed_s'] % 60):02d}</div>"
         f"<div class='note'>max {int(pr['elapsed_s_max'] // 60)}:{int(pr['elapsed_s_max'] % 60):02d} of 10:00</div></div></div>")
    runs = "".join(f"<tr><td>{e(r['run'])}</td><td>{e(r.get('label') or '')}</td><td>{e(r['status'])}</td><td class='num'>{r['elapsed_s']:.0f}s</td>"
                   f"<td class='num'>{r['calls']}</td><td class='num'>{r['prompt']:,}</td><td class='num'>{r['cached']:,}</td>"
                   f"<td class='num'>{r['completion']:,}</td><td class='num'>{_usd(r['cost_usd'])}</td></tr>" for r in t["runs"])
    sc = "".join(f"<tr><td class='num'>{v['users']:,}</td><td class='num'>{v['runs_per_month']:,.0f}</td>"
                 f"<td class='num'>{_usd(v['model_cost_month'])}</td><td class='num'>{_usd(v['model_cost_year'])}</td></tr>" for v in t["scale"].values())
    om = "".join(f"<tr><td>{e(m)}</td><td class='num'>{_usd(c)}</td><td class='num'>{c / pr['cost_usd']:.0f}x</td></tr>"
                 for m, c in t["same_tokens_other_models"].items() if pr["cost_usd"])
    ag = "".join(f"<tr><td>{e(NAME_OF.get(a, a))}</td><td class='num'>{v['calls']:.1f}</td><td class='num'>{v['input']:,.0f}</td>"
                 f"<td class='num'>{v['cached']:,.0f}</td><td class='num'>{v['output']:,.0f}</td><td class='num'>{_usd(v['cost_usd'])}</td></tr>"
                 for a, v in sorted(t["by_agent_mean"].items(), key=lambda kv: -kv[1]["cost_usd"]))
    body = (f"<h1>Tokenomics</h1><p class='note'>Measured on {t['n_runs']} live run(s) of {e(t['model'])} "
            f"({t['n_completed']} reached a decision; averages use those). Prices: {e(t['price_source'])}.</p>{k}"
            f"<h2>Per user and at scale</h2><p class='note'>Assumes {t['runs_per_user_month']:g} committee runs per user per month. "
            f"Model cost only; the app runs locally, so there is no hosting cost in this table.</p>"
            f"<table><tr><th class='num'>Users</th><th class='num'>Runs / month</th><th class='num'>Model cost / month</th><th class='num'>Per year</th></tr>{sc}</table>"
            f"<h2>Cost by agent (mean per run)</h2><table><tr><th>Agent</th><th class='num'>Calls</th><th class='num'>Input</th>"
            f"<th class='num'>Cached</th><th class='num'>Output</th><th class='num'>Cost</th></tr>{ag}</table>"
            f"<h2>Same tokens on other course models</h2><p class='note'>Without prompt caching the mean run would cost {_usd(t['no_cache_cost_usd'])}.</p>"
            f"<table><tr><th>Model</th><th class='num'>Cost per run</th><th class='num'>vs luna</th></tr>{om}</table>"
            f"<h2>Every live run</h2><table><tr><th>Run</th><th>Label</th><th>Status</th><th class='num'>Time</th><th class='num'>Calls</th>"
            f"<th class='num'>Input</th><th class='num'>Cached</th><th class='num'>Output</th><th class='num'>Cost</th></tr>{runs}</table>")
    return _page("Tokenomics", body)


def safety_page(sf: dict | None) -> str:
    if not sf:
        return _page("Safety", "<h1>Safety</h1><p>Run <code>python -m app.safety</code> to execute the attack, malfunction and overspending scenarios.</p>")
    res = sf["results"]
    n = sum(r["passed"] for r in res)
    rows = "".join(f"<tr><td>{e(r['area'])}</td><td>{e(r['attack'])}</td><td>{e(r['defence'])}</td>"
                   f"<td class='{'pass' if r['passed'] else 'fail'}'>{'HELD' if r['passed'] else 'FAILED'}</td>"
                   f"<td><code>{e(r['observed'])}</code></td></tr>" for r in res)
    body = (f"<h1>Safety</h1><p><span class='tag {'ok' if n == len(res) else 'esc'}'>{n} of {len(res)} defences held</span> "
            f"<span class='note'>Executed {e(sf['generated_utc'])} by <code>python -m app.safety</code> against the real gateway, "
            "orchestrator and contracts. The scripted model plays the attacker or the faulty agent.</span></p>"
            f"<table><tr><th>Area</th><th>Attack or failure</th><th>Defence (built)</th><th>Result</th><th>What the code returned</th></tr>{rows}</table>")
    return _page("Safety", body)


def _stats(root: Path, cls: str) -> dict:
    import sqlite3
    dbs = {"US_EQ": "mgr_us_equity.db", "INTL_EQ": "mgr_intl_equity.db", "US_IG": "mgr_us_ig.db", "US_HY": "mgr_us_hy.db"}
    p = Path(root) / "db" / dbs.get(cls, "")
    if cls not in dbs or not p.exists():
        return {}
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    out = {r["manager_id"]: dict(r) for r in c.execute("SELECT * FROM manager_stats WHERE window='FULL'")}
    c.close()
    return out


def build(root: str | Path = ".") -> list[Path]:
    root = Path(root)
    dash = root / "output" / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    runs = [load_run(d) for d in run_dirs(root)]
    written = []

    def put(name: str, text: str):
        p = dash / name
        p.write_text(text, encoding="utf-8")
        written.append(p)

    put("index.html", index_page(runs))
    for r in runs:
        put(f"run-{r['id']}.html", run_page(r))
    for c in CLASSES:
        run, h = latest_head(runs, c) if c != "CASH" else (None, None)
        put(f"class-{c}.html", class_page(c, run, h, _stats(root, c)))
    from app import tokenomics
    put("tokenomics.html", tokenomics_page(tokenomics.summarise(root)))
    put("safety.html", safety_page(_load(root / "output" / "safety.json")))
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the static dashboard from output/.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    for p in build(a.root):
        print("wrote", p)
