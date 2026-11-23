// Every saved run with its decision; selecting one shows the decision view and the memo.
import { useEffect, useState } from 'react'
import { api, type RunSummary } from './api'
import { DecisionView } from './DecisionView'
import { label } from './labels'

export function RunsPage({ initial }: { initial?: string | null }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null)
  const [sel, setSel] = useState<string | null>(initial ?? null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.runs().then(r => { setRuns(r); if (!sel && r.length) setSel(r[0].run_id) }).catch(e => setErr((e as Error).message))
  useEffect(() => { load() }, [])
  useEffect(() => { if (initial) setSel(initial) }, [initial])

  return <div className="runs">
    <div className="bar"><h2>Runs and memos</h2><button className="alt" onClick={load}>Refresh</button>
      <span className="note">Every committee run on this machine, newest first. On the hosted app, runs last until the service restarts.</span></div>
    {err && <div className="bad">{err}</div>}
    {runs && runs.length === 0 && <div className="note">No runs yet. Run the committee from the Committee tab.</div>}
    {runs && runs.length > 0 && <table className="runlist"><thead><tr><th>Finished (UTC)</th><th>Owner</th><th>Decision</th><th>Time</th><th>Cost</th><th>Run</th></tr></thead>
      <tbody>{runs.map(r => { const L = label(r.status); return <tr key={r.run_id} className={sel === r.run_id ? 'sel' : ''} onClick={() => setSel(r.run_id)}>
        <td>{(r.finished_utc || '').replace('T', ' ').slice(0, 16)}</td><td>{r.owner_type || r.label || '-'}</td>
        <td className={L.tone}>{L.text}{r.mock ? ' (scripted)' : ''}</td><td>{r.elapsed_s ?? '-'} s</td>
        <td>{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : '-'}</td><td className="small">{r.run_id}</td></tr> })}</tbody></table>}
    {sel && <DecisionView runId={sel} />}
  </div>
}
