// The committee's cast: original characters named for the desk each agent runs, drawn in code (no image files).
import type { ReactNode } from 'react'

export interface Member { name: string; role: string; src: string }
export const CAST: Record<string, Member> = {
  acio: { name: 'The Chairman', role: 'Acting CIO', src: 'Final say on allocation and selection' },
  aar: { name: 'The Quant', role: 'Asset Allocation & Risk', src: 'Proposes the mix and ranges' },
  head_us_eq: { name: 'The Stock Picker', role: 'Head of US Equity', src: 'Ranks US equity managers' },
  head_intl_eq: { name: 'The Globe Trotter', role: 'Head of International Equity', src: 'Ranks international managers' },
  head_us_ig: { name: 'The Rates Desk', role: 'Head of US IG Fixed Income', src: 'Ranks investment-grade managers' },
  head_us_hy: { name: 'The Credit Hawk', role: 'Head of US High Yield', src: 'Ranks high-yield managers' },
  red_team: { name: 'The Prosecutor', role: 'Red Team', src: 'Challenges the integrated portfolio' },
  owner: { name: 'Owner (you)', role: 'Asset owner', src: 'Your ask' },
}
export const who = (k?: string) => (k && CAST[k] ? CAST[k].name : k ?? '')

const TINT: Record<string, string> = {
  acio: '#ffb000', aar: '#19e6ff', head_us_eq: '#3dffb4', head_intl_eq: '#19e6ff',
  head_us_ig: '#7dd3fc', head_us_hy: '#ff8a5b', red_team: '#ff4d6d', owner: '#d9e6f2',
}

export function Avatar({ k }: { k: string }) {
  const t = TINT[k] ?? '#19e6ff'
  const head = (cx: number, cy: number) => <circle cx={cx} cy={cy} r={8} fill="#0a1420" stroke={t} strokeWidth={1.5} />
  const body = (x0: number, cx: number, x1: number) => <path d={`M${x0} 60 Q${cx} 40 ${x1} 60 Z`} fill={t} fillOpacity={0.6} />
  let inner: ReactNode
  switch (k) {
    case 'acio': // rings expand from the desk, head nods in charge
      inner = <>
        <g className="nod"><circle cx={32} cy={26} r={6} fill={t} fillOpacity={0.8} /></g>
        <circle className="ring" cx={32} cy={26} r={8} fill="none" stroke={t} />
        <circle className="ring r2" cx={32} cy={26} r={8} fill="none" stroke={t} />
        {body(14, 32, 50)}
      </>; break
    case 'aar': // bars rise and fall like a risk model
      inner = <>{[[12, 20, 36, ''], [23, 10, 46, 'b2'], [34, 28, 28, 'b3'], [45, 16, 40, 'b4']].map(([x, y, h, c]) =>
        <rect key={String(x)} className={`bar ${c}`} x={x as number} y={y as number} width={7} height={h as number} fill={t} />)}</>; break
    case 'head_us_eq': // a magnifier sweeps the screen for names
      inner = <>{head(30, 24)}{body(14, 30, 46)}
        <g className="sweep"><circle cx={46} cy={30} r={7} fill="none" stroke={t} strokeWidth={2.5} /><line x1={51} y1={35} x2={56} y2={40} stroke={t} strokeWidth={2.5} /></g></>; break
    case 'head_intl_eq': // the globe turns
      inner = <g className="spin"><circle cx={32} cy={32} r={16} fill="none" stroke={t} strokeWidth={1.5} />
        <ellipse cx={32} cy={32} rx={7} ry={16} fill="none" stroke={t} /><line x1={16} y1={32} x2={48} y2={32} stroke={t} /></g>; break
    case 'head_us_ig': // a yield curve draws itself
      inner = <><path className="draw" d="M8 48 Q20 40 32 30 T56 16" fill="none" stroke={t} strokeWidth={2.5} pathLength={100} />
        <line x1={8} y1={54} x2={56} y2={54} stroke={t} strokeOpacity={0.5} /></>; break
    case 'head_us_hy': // an arrow darts up and down
      inner = <path className="dart" d="M32 8 L44 28 L36 28 L36 56 L28 56 L28 28 L20 28 Z" fill={t} fillOpacity={0.85} />; break
    case 'red_team': // a question mark flips to challenge the case
      inner = <text className="flip" x={32} y={46} textAnchor="middle" fontSize={40} fontWeight={700} fill={t} fontFamily="Segoe UI,sans-serif">?</text>; break
    default: // owner: a steady, quiet figure
      inner = <g className="bob">{head(32, 26)}{body(14, 32, 50)}</g>
  }
  return <svg className="av" viewBox="0 0 64 64" aria-hidden="true">
    <rect x={1} y={1} width={62} height={62} fill="#06101b" stroke={t} strokeOpacity={0.5} />{inner}</svg>
}

export function CastGrid({ live }: { live: Record<string, number> }) {
  return <div className="cast">{Object.entries(CAST).map(([k, c]) =>
    <div key={`${k}-${live[k] ?? 0}`} className={`card${live[k] ? ' live' : ''}`}>
      <Avatar k={k} />
      <div className="txt"><b>{c.name}</b><small>{c.role}</small><span>{c.src}</span></div>
    </div>)}</div>
}
