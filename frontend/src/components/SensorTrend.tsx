import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { ReadingPoint } from '../types'

const COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a78bfa', '#34d399']

interface Props {
  readings: ReadingPoint[]
  sensors: string[]
}

export function SensorTrend({ readings, sensors }: Props) {
  if (!readings.length || !sensors.length) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {sensors.map((sensor, i) => (
        <div key={sensor}>
          <div style={{ fontSize: '0.82rem', color: 'var(--text2)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '.04em' }}>
            {sensor}
          </div>
          <div className="sensor-chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={readings} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                <XAxis dataKey="cycle" tick={{ fill: 'var(--text2)', fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: 'var(--text2)', fontSize: 11 }} tickLine={false} axisLine={false} width={52} />
                <Tooltip
                  contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}
                  labelStyle={{ color: 'var(--text2)' }}
                  itemStyle={{ color: COLORS[i % COLORS.length] }}
                  formatter={(v) => [(v as number).toFixed(2), sensor]}
                  labelFormatter={(l) => `Cycle ${l as number}`}
                />
                <Line
                  type="monotone"
                  dataKey={sensor}
                  stroke={COLORS[i % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ))}
    </div>
  )
}
