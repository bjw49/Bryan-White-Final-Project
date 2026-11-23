// In-app view of one finished run: the decision and what it means, questions for the owner, allocation (proposed vs
// final), manager lineup, Red Team objections with the ACIO's responses, flags, cost, and the memo.
import { useEffect, useState } from 'react'
import { api, type RunDetail } from './api'
import { label, norm } from './labels'
import { Markdown } from './markdown'

const CLASSES = ['US_EQ', 'INTL_EQ', 'US_IG', 'US_HY', 'CASH']
const pct = (v: unknown) => (v === undefined || v === null || v === '' ? '-' : `${Number(v).toFixed(Number(v) % 1 ? 1 : 0)}%`)

export function DecisionView({ runId, compact }: { runId: string; compact?: boolean }) {
  const [d, setD] = useState<RunDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { setD(null); setErr(null); api.runDetail(runId).then(setD).catch(e => setErr((e as Error).message)) }, [runId])
  if (err) return <div className="bad">Could not load run {runId}: {err}</div>
  if (!d) return <div className="note pulse">Loading the decision...</div>

  const L = label(d.status)
  const dec = d.decision || {}
  const ad = dec.allocation_decision || {}
  const finalW = dec.asset_allocation?.weights_pct || ad.final_weights_pct || {}
  const aarW = ad.aar_weights_pct || {}
  const objections: any[] = d.red_team?.objections || []
  const responses: Record<string, any> = Object.fromEntries((dec.red_team_responses || []).map((r: any) => [r.objection_id, r]))
  const open: string[] = dec.open_items_for_human_cio || []
  const t = d.telemetry || {}
  const download = () => {
    const url = URL.createObjectURL(new Blob([d.memo], { type: 'text/markdown' }))
    const a = document.createElement('a'); a.href = url; a.download = `${d.run_id}_memo.md`; a.click(); URL.revokeObjectURL(url)
  }

  return <div className="decision">
    <div className={`verdict ${L.tone}`}>
      <div><small>Committee decision</small><b>{L.text}</b></div>
      <p>{L.meaning}</p>
    </div>
    <div className="meta">
      Run {d.run_id}{t.label ? ` · ${t.label}` : ''}{t.mock ? ' · scripted model' : ''} · {t.elapsed_s ?? '-'} s ·{' '}
      {(t.tokens?.total ?? 0).toLocaleString()} tokens · ${(t.cost_usd ?? 0).toFixed(4)}
      {' · '}<a href={d.dashboard} target="_blank" rel="noreferrer">Full run page</a>
      {' · '}<button className="link" onClick={download}>Download memo (.md)</button>
    </div>

    {d.stopped && d.stop && <section><h3>Why the run stopped</h3>
      <p>Stage <b>{d.stop.stage}</b>: {d.stop.detail}</p></section>}

    {open.length > 0 && <section className={norm(d.status) === 'NEEDS_OWNER_INPUT' ? 'ask' : ''}>
      <h3>{norm(d.status) === 'NEEDS_OWNER_INPUT' ? 'Questions for the owner' : 'Open items for the human CIO'}</h3>
      <ol>{open.map((o, i) => <li key={i}>{o}</li>)}</ol></section>}

    {!d.stopped && <section><h3>Asset allocation</h3>
      <table><thead><tr><th>Class</th><th>Proposed (Head of Allocation)</th><th>Final (ACIO)</th></tr></thead>
        <tbody>{CLASSES.map(c => <tr key={c}><td>{c}</td><td>{pct(aarW[c])}</td><td>{pct(finalW[c])}</td></tr>)}</tbody></table>
      {ad.adopted && <p className="note">Adopted: <b>{ad.adopted}</b>. {ad.reason}</p>}</section>}

    {(dec.manager_portfolio || []).length > 0 && <section><h3>Manager lineup</h3>
      <table><thead><tr><th>Manager</th><th>Class</th><th>In class</th><th>Of total</th><th>Role</th>{!compact && <th>Conditions</th>}</tr></thead>
        <tbody>{dec.manager_portfolio.map((m: any) => <tr key={m.manager_id}>
          <td><b>{m.manager_id}</b></td><td>{m.class}</td><td>{pct(m.weight_in_class_pct)}</td><td>{pct(m.weight_total_pct)}</td>
          <td>{m.role}</td>{!compact && <td className="small">{m.condition}</td>}</tr>)}</tbody></table></section>}

    {objections.length > 0 && <section><h3>Red Team: {d.red_team?.verdict} ({objections.length} objections)</h3>
      <table><thead><tr><th>#</th><th>Severity</th><th>Objection</th><th>ACIO response</th></tr></thead>
        <tbody>{objections.map((o: any) => { const r = responses[o.id] || {}; return <tr key={o.id}>
          <td>{o.id}</td><td className={o.severity === 'CRITICAL' ? 'bad' : ''}>{o.severity}</td>
          <td><b>{o.target}</b><div className="small">{o.claim}</div></td>
          <td><b>{r.response ?? 'no response'}</b><div className="small">{r.action}</div></td></tr> })}</tbody></table></section>}

    {d.flags?.length > 0 && <section><h3>Flags for the human CIO</h3><ul className="small">{d.flags.map((f, i) => <li key={i}>{f}</li>)}</ul></section>}

    <details open={!compact}><summary>Investment memo</summary><div className="memo"><Markdown text={d.memo} /></div></details>
  </div>
}
