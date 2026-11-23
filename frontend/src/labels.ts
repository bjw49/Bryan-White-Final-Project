// Decision labels. Every output goes to a human CIO; the ACIO recommends, it never approves.
// Runs saved before the rename (APPROVE, ESCALATE_TO_HUMAN, ESCALATED, ...) are shown with the new names.
const LEGACY: Record<string, string> = {
  APPROVE: 'RECOMMEND', APPROVE_WITH_CONDITIONS: 'RECOMMEND_WITH_CONDITIONS', ESCALATE_TO_HUMAN: 'NEEDS_OWNER_INPUT',
  REJECT: 'DO_NOT_RECOMMEND', ESCALATED: 'RUN_STOPPED', 'RUN STOPPED': 'RUN_STOPPED',
}
export const norm = (s?: string | null) => (s ? LEGACY[s] ?? s : '?')

export const LABELS: Record<string, { text: string; tone: 'ok' | 'warn' | 'bad'; meaning: string }> = {
  RECOMMEND: { text: 'Recommend', tone: 'ok', meaning: 'The committee stands behind this allocation and lineup.' },
  RECOMMEND_WITH_CONDITIONS: { text: 'Recommend with conditions', tone: 'ok', meaning: 'The committee stands behind it, subject to the conditions listed.' },
  NEEDS_OWNER_INPUT: { text: 'Needs owner input', tone: 'warn', meaning: 'A full draft allocation and lineup, which the committee will not stand behind until the owner answers the questions below.' },
  DO_NOT_RECOMMEND: { text: 'Do not recommend', tone: 'bad', meaning: 'The committee recommends against adopting what is on the table; the memo says what would have to change.' },
  RUN_STOPPED: { text: 'Run stopped', tone: 'bad', meaning: 'A budget, contract or integrity control stopped the run before a recommendation. Nothing unvalidated was published.' },
}
export const label = (s?: string | null) => LABELS[norm(s)] ?? { text: norm(s), tone: 'warn' as const, meaning: '' }
