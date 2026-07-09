import type { RiskBucket } from '../types'

const RISK_COLOR: Record<RiskBucket, string> = {
  critical: 'var(--critical)',
  warning:  'var(--warning)',
  healthy:  'var(--healthy)',
}

const MAX_RUL = 300 // normalise against fleet-wide approximate max

interface Props {
  rul: number
  riskBucket: RiskBucket
  size?: number
}

export function RulGauge({ rul, riskBucket, size = 160 }: Props) {
  const pct   = Math.min(rul / MAX_RUL, 1)
  const color = RISK_COLOR[riskBucket]
  const r     = size / 2 - 14
  const cx    = size / 2
  const cy    = size / 2

  // Arc from 220° to 320° (280° sweep, opening at the bottom)
  const startAngle = 220
  const sweepTotal = 280
  const endAngle   = startAngle + sweepTotal * pct

  function polar(deg: number) {
    const rad = (deg * Math.PI) / 180
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
  }

  function arcPath(from: number, to: number) {
    const s  = polar(from)
    const e  = polar(to)
    const la = to - from > 180 ? 1 : 0
    return `M ${s.x} ${s.y} A ${r} ${r} 0 ${la} 1 ${e.x} ${e.y}`
  }

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {/* Track */}
      <path
        d={arcPath(startAngle, startAngle + sweepTotal)}
        fill="none"
        stroke="var(--surface2)"
        strokeWidth={10}
        strokeLinecap="round"
      />
      {/* Fill */}
      {pct > 0.01 && (
        <path
          d={arcPath(startAngle, endAngle)}
          fill="none"
          stroke={color}
          strokeWidth={10}
          strokeLinecap="round"
        />
      )}
      {/* RUL number */}
      <text x={cx} y={cy - 4} textAnchor="middle" fill={color} fontSize={size * 0.22} fontWeight={800}>
        {rul}
      </text>
      <text x={cx} y={cy + size * 0.14} textAnchor="middle" fill="var(--text2)" fontSize={size * 0.09}>
        cycles left
      </text>
    </svg>
  )
}
