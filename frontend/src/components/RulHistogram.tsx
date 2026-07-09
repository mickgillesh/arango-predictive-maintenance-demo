import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import type { EngineRow } from '../types'

interface Props { engines: EngineRow[] }

export function RulHistogram({ engines }: Props) {
  // Bucket engines into 10-cycle bands
  if (!engines.length) return null

  const max = Math.max(...engines.map(e => e.predictedRUL))
  const bucketSize = Math.max(10, Math.ceil(max / 12 / 10) * 10)

  const counts: Record<number, { count: number; bucket: string; risk: string }> = {}
  for (const e of engines) {
    const b = Math.floor(e.predictedRUL / bucketSize) * bucketSize
    if (!counts[b]) counts[b] = { count: 0, bucket: `${b}–${b + bucketSize - 1}`, risk: e.riskBucket }
    counts[b].count++
    // Use worst risk in bucket for colouring
    if (e.riskBucket === 'critical') counts[b].risk = 'critical'
    else if (e.riskBucket === 'warning' && counts[b].risk !== 'critical') counts[b].risk = 'warning'
  }

  const data = Object.entries(counts)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([, v]) => v)

  const COLOR: Record<string, string> = {
    critical: 'var(--critical)',
    warning:  'var(--warning)',
    healthy:  'var(--healthy)',
  }

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
          <XAxis dataKey="bucket" tick={{ fill: 'var(--text2)', fontSize: 10 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: 'var(--text2)', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: 'var(--text2)' }}
            formatter={(v) => [v as number, 'engines']}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={COLOR[entry.risk] ?? 'var(--healthy)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
