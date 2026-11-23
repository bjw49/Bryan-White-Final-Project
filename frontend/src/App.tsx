// ACIO app: Committee (describe the owner, answer the ACIO's questions, watch the run, read the decision),
// Runs & Memos (every saved run), Tokenomics and Safety.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, getCode, setCode, setTimeBudget, type Config, type Example, type Policy, type Question } from './api'
import { CastGrid } from './cast'
import { DecisionView } from './DecisionView'
import { RunPanel } from './RunPanel'
import { RunsPage } from './RunsPage'

type Item =
  | { id: number; kind: 'me'; text: string }
  | { id: number; kind: 'acio'; text: string }
  | { id: number; kind: 'thinking'; text?: string }
  | { id: number; kind: 'policy'; policy: Policy; note?: string }
  | { id: number; kind: 'questions'; runId: string; policy: Policy; questions: Question[]; done?: boolean }
  | { id: number; kind: 'error'; text: string }
  | { id: number; kind: 'run'; jobId: string }
  | { id: number; kind: 'decision'; runId: string }

type NewItem = Item extends infer T ? (T extends Item ? Omit<T, 'id'> : never) : never // Omit across each union member
type Tab = 'committee' | 'runs' | 'tokenomics' | 'safety'
const TABS: [Tab, string][] = [['committee', 'Committee'], ['runs', 'Runs & Memos'], ['tokenomics', 'Tokenomics'], ['safety', 'Safety']]
const POLICY_ROWS = ['owner_type', 'objective', 'horizon', 'risk_tolerance', 'constraints'] as const
let nextId = 1

export default function App() {
  const [tab, setTab] = useState<Tab>('committee')
  const [runsSel, setRunsSel] = useState<string | null>(null)
  const [items, setItems] = useState<Item[]>([{ id: 0, kind: 'acio', text:
    'Describe the asset owner in plain English, or pick an example. I will read the ask, ask you up to three questions if something important is unclear, then run the committee and show you the decision.' }])
  const [examples, setExamples] = useState<Example[]>([])
  const [config, setConfig] = useState<Config | null>(null)
  const [ask, setAsk] = useState('')
  const [busy, setBusy] = useState(false)
  const [code, setCodeState] = useState(getCode())
  const [live, setLive] = useState<Record<string, number>>({})
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.examples().then(setExamples).catch(e => push({ kind: 'error', text: (e as Error).message }))
    api.config().then(c => { setTimeBudget(c.time_budget_s); setConfig(c) }).catch(() => setConfig(null))
  }, [])
  useEffect(() => { if (tab === 'committee') bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [items, tab])

  const push = (it: NewItem) => { const id = nextId++; setItems(xs => [...xs, { ...it, id } as Item]); return id }
  const replace = (id: number, it: NewItem) => setItems(xs => xs.map(x => (x.id === id ? ({ ...it, id } as Item) : x)))
  const flash = useCallback((k: string) => {
    setLive(l => ({ ...l, [k]: (l[k] ?? 0) + 1 }))
    setTimeout(() => setLive(l => { const n = { ...l }; if (n[k]) n[k] = 0; return n }), 2200)
  }, [])

  async function startRun(runId: string, policy: Policy) {
    const { job_id } = await api.run(runId, policy)
    push({ kind: 'run', jobId: job_id }) // RunPanel clears busy when the job ends
  }

  async function readAsk(text: string) {
    if (busy) return
    push({ kind: 'me', text })
    setBusy(true)
    const tid = push({ kind: 'thinking', text: 'Reading the ask...' })
    try {
      const r = await api.policy(text)
      replace(tid, { kind: 'policy', policy: r.policy })
      if (r.questions?.length) {
        push({ kind: 'questions', runId: r.run_id, policy: r.policy, questions: r.questions })
        setBusy(false) // wait for the owner's answers
        return
      }
      await startRun(r.run_id, r.policy)
    } catch (e) {
      replace(tid, { kind: 'error', text: (e as Error).message })
      setBusy(false)
    }
  }

  async function answer(itemId: number, runId: string, policy: Policy, answers: string[] | null) {
    setItems(xs => xs.map(x => (x.id === itemId && x.kind === 'questions' ? { ...x, done: true } : x)))
    setBusy(true)
    try {
      if (answers === null) {
        push({ kind: 'acio', text: "Running on my stated assumptions (shown with each question)." })
        await startRun(runId, policy)
        return
      }
      const tid = push({ kind: 'thinking', text: 'Updating the policy with your answers...' })
      const r = await api.clarify(runId, answers)
      replace(tid, { kind: 'policy', policy: r.policy, note: 'updated with your answers' })
      await startRun(runId, r.policy)
    } catch (e) {
      push({ kind: 'error', text: (e as Error).message })
      setBusy(false)
    }
  }

  const finished = (runId?: string) => { setBusy(false); if (runId) push({ kind: 'decision', runId }) }
  const send = () => { const t = ask.trim(); if (t && !busy) { setAsk(''); readAsk(t) } }
  const needCode = config?.access_code_required

  return <>
    <header>
      <h1>ACIO: Augmented Investment Intelligence</h1>
      <p>Describe the owner. The ACIO reads the ask, asks what it must, runs the committee and shows its decision.
        {config?.mock && <span className="badge">Mock model</span>}</p>
      <nav className="tabs">{TABS.map(([k, t]) =>
        <button key={k} className={tab === k ? 'tab on' : 'tab'} onClick={() => setTab(k)}>{t}</button>)}</nav>
    </header>
    <main className={tab === 'committee' ? '' : 'wide'}>
      <div hidden={tab !== 'committee'}>
        <CastGrid live={live} />
        <div className="disc">Original characters, named for the desk each agent runs. Their animations match their roles. The reasoning comes from each agent's prompt, tools and data.</div>
        {needCode && <div className="codebox">
          <label>Access code <input type="password" value={code} placeholder="required on the hosted app"
            onChange={e => { setCodeState(e.target.value); setCode(e.target.value) }} /></label>
          <span className="note">The hosted app runs on the owner's model budget, so runs need a code.</span>
        </div>}
        <div className="examples">{examples.map(x =>
          <button key={x.id} className="alt" disabled={busy} onClick={() => readAsk(x.text)}>Example: {x.label}</button>)}</div>
        <div>{items.map(it => <Bubble key={it.id} item={it} busy={busy} onAgent={flash} onDone={finished}
          onAnswer={answer} onOpenRuns={id => { setRunsSel(id); setTab('runs') }} />)}</div>
        <div ref={bottom} />
        <textarea value={ask} onChange={e => setAsk(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send() }}
          placeholder="Describe the asset owner: what it is, its return target or liabilities, spending or cash needs, risk tolerance, horizon, and any constraints." />
        <div className="bar">
          <button onClick={send} disabled={busy || !ask.trim()}>Send to ACIO</button>
          <span className="note">Ctrl+Enter sends. Every run is saved under Runs &amp; Memos.</span>
        </div>
      </div>
      {tab === 'runs' && <RunsPage initial={runsSel} />}
      {tab === 'tokenomics' && <iframe className="page" title="Tokenomics" src="/files/dashboard/tokenomics.html" />}
      {tab === 'safety' && <iframe className="page" title="Safety" src="/files/dashboard/safety.html" />}
    </main>
  </>
}

function Questions({ item, busy, onAnswer }: { item: Extract<Item, { kind: 'questions' }>; busy: boolean;
  onAnswer: (itemId: number, runId: string, policy: Policy, answers: string[] | null) => void }) {
  const [vals, setVals] = useState<string[]>(item.questions.map(() => ''))
  const locked = item.done || busy
  return <div className="questions">
    <div>Before I run the committee, {item.questions.length === 1 ? 'one answer' : `${item.questions.length} answers`} would change the policy:</div>
    {item.questions.map((q, i) => <div key={i} className="q">
      <b>{i + 1}. {q.question}</b>
      {q.why && <div className="small">Why it matters: {q.why}</div>}
      <input value={vals[i]} disabled={locked} placeholder={q.default ? `If left blank I will assume: ${q.default}` : 'Your answer'}
        onChange={e => setVals(v => v.map((x, j) => (j === i ? e.target.value : x)))} />
    </div>)}
    {!item.done && <div className="bar">
      <button disabled={locked || vals.every(v => !v.trim())} onClick={() => onAnswer(item.id, item.runId, item.policy, vals)}>Answer and run</button>
      <button className="alt" disabled={locked} onClick={() => onAnswer(item.id, item.runId, item.policy, null)}>Run with the ACIO's assumptions</button>
    </div>}
    {item.done && <div className="note">Sent.</div>}
  </div>
}

function Bubble({ item, busy, onAgent, onDone, onAnswer, onOpenRuns }: {
  item: Item; busy: boolean; onAgent: (k: string) => void; onDone: (runId?: string) => void
  onAnswer: (itemId: number, runId: string, policy: Policy, answers: string[] | null) => void; onOpenRuns: (id: string) => void }) {
  const me = item.kind === 'me'
  return <div className={`msg${me ? ' me' : ''}${item.kind === 'decision' ? ' wide' : ''}`}>
    <div className="who">{me ? 'You' : 'ACIO'}</div>
    {item.kind === 'me' && <div>{item.text}</div>}
    {item.kind === 'acio' && <div>{item.text}</div>}
    {item.kind === 'thinking' && <div className="pulse">{item.text ?? 'Working...'}</div>}
    {item.kind === 'error' && <div className="bad">{item.text}</div>}
    {item.kind === 'policy' && <div>
      <div>The ACIO's reading of the ask{item.note ? ` (${item.note})` : ''}:</div>
      <table><tbody>{POLICY_ROWS.map(k =>
        <tr key={k}><th>{k.replace('_', ' ')}</th><td>{String(item.policy[k] ?? '')}</td></tr>)}</tbody></table>
    </div>}
    {item.kind === 'questions' && <Questions item={item} busy={busy} onAnswer={onAnswer} />}
    {item.kind === 'run' && <RunPanel jobId={item.jobId} onAgent={onAgent} onDone={onDone} />}
    {item.kind === 'decision' && <div>
      <DecisionView runId={item.runId} compact />
      <div className="bar"><button className="alt" onClick={() => onOpenRuns(item.runId)}>Open in Runs &amp; Memos</button></div>
    </div>}
  </div>
}
