// Live view of one committee run: clock, tokens, cost, stage strip and the agent-to-agent wire. Polls /api/job.
import { useEffect, useRef, useState } from 'react'
import { api, type JobView, type WireMsg } from './api'
import { who } from './cast'
import { label } from './labels'

const STAGES: [string, string][] = [
  ['Policy', 'reading the owner'], ['AAR + 4 Heads', 'working in parallel'], ['ACIO review', 'reviewing the lineups'],
  ['Red Team', 'Red Team is challenging'], ['Decision', 'writing the final decision'], ['Memo', 'Decision written'],
]
const pad = (n: number, w = 3) => String(n).padStart(w, '0')
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

export function RunPanel({ jobId, onAgent, onDone }: { jobId: string; onAgent: (k: string) => void; onDone: (runId?: string) => void }) {
  const [job, setJob] = useState<JobView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const seen = useRef(0)
  const cb = useRef({ onAgent, onDone }); cb.current = { onAgent, onDone }

  useEffect(() => {
    let alive = true
    const tick = async () => {
      while (alive) {
        await new Promise(r => setTimeout(r, 1500))
        try {
          const j = await api.job(jobId)
          if (!alive) return
          for (; seen.current < j.messages.length; seen.current++) {
            const m = j.messages[seen.current]
            if (m.from) { cb.current.onAgent(m.from); if (m.to) cb.current.onAgent(m.to) }
          }
          setJob(j)
          if (j.status !== 'running') { cb.current.onDone(j.result?.run_id); return }
        } catch (e) { if (alive) { setErr((e as Error).message); cb.current.onDone() } return }
      }
    }
    tick()
    return () => { alive = false }
  }, [jobId])

  // Current stage: the last plain-text progress line that names a stage.
  let stage = 0
  for (const m of job?.messages ?? []) {
    if (m.from) continue
    const i = STAGES.findIndex(s => m.text.includes(s[1]))
    if (i >= 0) stage = i
  }
  const finished = job && job.status !== 'running'
  const memoDone = stage === STAGES.length - 1

  return <div>
    <div className="tick">
      <div><small>Elapsed</small><span>{clock(job?.elapsed ?? 0)}</span></div>
      <div><small>Tokens</small><span>{(job?.tokens ?? 0).toLocaleString()}</span></div>
      <div><small>Cost (USD)</small><span>${(job?.cost_usd ?? 0).toFixed(4)}</span></div>
    </div>
    <div className="strip">{STAGES.map(([label], i) =>
      <div key={label} className={memoDone || i < stage ? 'done' : i === stage ? 'on' : ''}>{label}</div>)}</div>
    <div className="log">{(job?.messages ?? []).map((m: WireMsg, i) => m.from
      ? <div key={i} className="wire">[{pad(m.t)}s] <span className="f">{who(m.from)}</span> &rarr; <span className="t">{who(m.to)}</span>: {m.text}</div>
      : <div key={i}>[{pad(m.t)}s] {m.text}</div>)}</div>
    <div className="note">
      {err ? <span className="bad">{err}</span>
        : !finished ? 'Running. Typical runs take several minutes; the limit is 10.'
        : job!.status === 'error' ? <span className="bad">Run failed: {job!.result?.error}</span>
        : <Result job={job!} />}
    </div>
  </div>
}

function Result({ job }: { job: JobView }) {
  const r = job.result ?? {}
  const L = label(r.status)
  return <>
    <div className={L.tone}>
      Decision: <b>{L.text}</b> {r.stop ? `(stopped at ${r.stop.stage}: ${r.stop.detail})` : ''}
    </div>
    <div>Elapsed: {job.elapsed} s of the 600 s limit &middot; {job.tokens.toLocaleString()} tokens &middot; ${job.cost_usd.toFixed(4)}. Run id: {r.run_id}</div>
    <div className="note">The decision opens below. Every run is also under Runs &amp; Memos.</div>
  </>
}
