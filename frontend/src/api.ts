// Typed client for the FastAPI back end (app/server.py).
export interface Example { id: string; label: string; text: string }
export interface Policy { owner_type?: string; objective?: string; horizon?: string; risk_tolerance?: string; constraints?: string; [k: string]: unknown }
export interface Question { question: string; why?: string; default?: string }
export interface PolicyResponse { run_id: string; policy: Policy; questions: Question[] }
export interface ClarifyResponse { run_id: string; policy: Policy; answers: { question: string; answer: string }[] }
export interface RunSummary { run_id: string; status: string; stopped: boolean; label?: string | null; owner_type?: string | null;
  finished_utc?: string | null; cost_usd?: number | null; elapsed_s?: number | null; mock?: boolean; tokens?: number | null }
export interface RunDetail { run_id: string; status: string; stopped: boolean; stop: { stage?: string; detail?: string } | null;
  decision: any; red_team: any; flags: string[]; memo: string; dashboard: string;
  telemetry: { label?: string; route?: string; elapsed_s?: number; model_calls?: number; tokens?: any; cost_usd?: number; finished_utc?: string; mock?: boolean } }
export interface WireMsg { t: number; from?: string; to?: string; text: string }
export interface RunResult { status?: string; stop?: { stage: string; detail: string } | null; run_id?: string; dashboard?: string; dashboard_index?: string; error?: string }
export interface JobView { status: 'running' | 'done' | 'error'; messages: WireMsg[]; result: RunResult | null; elapsed: number; tokens: number; cost_usd: number }
export interface Config { mock: boolean; access_code_required: boolean; model: string; api: string; time_budget_s?: number }
let budgetS = 420
export const timeBudget = () => budgetS
export function setTimeBudget(s?: number) { if (s) budgetS = s }

const CODE_KEY = 'acio-access-code'
export function getCode(): string { try { return sessionStorage.getItem(CODE_KEY) || '' } catch { return '' } }
export function setCode(c: string) { try { sessionStorage.setItem(CODE_KEY, c) } catch { /* storage unavailable: keep in memory only */ } memCode = c }
let memCode = ''

async function call<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const code = getCode() || memCode
  if (code) headers['X-Access-Code'] = code
  const r = await fetch(path, { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined })
  let j: any = {}
  try { j = await r.json() } catch { /* non-JSON error page */ }
  if (!r.ok) {
    const detail = j.error ?? (Array.isArray(j.detail) ? j.detail.map((d: any) => d.msg).join('; ') : j.detail)
    throw new Error(detail || r.statusText)
  }
  return j as T
}

export const api = {
  config: () => call<Config>('/api/config'),
  examples: () => call<Example[]>('/api/examples'),
  policy: (owner: string) => call<PolicyResponse>('/api/policy', { owner }),
  clarify: (run_id: string, answers: string[]) => call<ClarifyResponse>('/api/clarify', { run_id, answers }),
  run: (run_id: string, policy: Policy) => call<{ job_id: string }>('/api/run', { run_id, policy }),
  runs: () => call<RunSummary[]>('/api/runs'),
  runDetail: (id: string) => call<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  job: (id: string) => call<JobView>(`/api/job/${id}`),
}
