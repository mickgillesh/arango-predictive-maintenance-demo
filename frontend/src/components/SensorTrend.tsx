import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { ReadingPoint } from '../types'

const COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a78bfa', '#34d399']

// NASA C-MAPSS FD001 sensor metadata
const SENSOR_META: Record<string, { label: string; subsystem: string }> = {
  s2:  { label: 'LPC outlet temperature (T24)',        subsystem: 'LPC' },
  s3:  { label: 'HPC outlet temperature (T30)',        subsystem: 'HPC' },
  s4:  { label: 'LPT outlet temperature (T50)',        subsystem: 'LPT' },
  s7:  { label: 'HPC outlet pressure (P30)',           subsystem: 'HPC' },
  s8:  { label: 'Physical fan speed (Nf)',             subsystem: 'Fan' },
  s9:  { label: 'Physical core speed (Nc)',            subsystem: 'HPC' },
  s11: { label: 'HPC outlet static pressure (Ps30)',   subsystem: 'HPC' },
  s12: { label: 'Fuel-flow / Ps30 ratio (φ)',          subsystem: 'Combustor' },
  s13: { label: 'Corrected fan speed (NRf)',           subsystem: 'Fan' },
  s14: { label: 'Corrected core speed (NRc)',          subsystem: 'HPC' },
  s15: { label: 'Bypass ratio (BPR)',                  subsystem: 'Fan' },
  s17: { label: 'Bleed enthalpy (htBleed)',            subsystem: 'HPC' },
  s20: { label: 'HPT coolant bleed (W31)',             subsystem: 'HPT' },
  s21: { label: 'LPT coolant bleed (W32)',             subsystem: 'LPT' },
}

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
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
            <span style={{ fontSize: '0.82rem', color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.04em', fontWeight: 600 }}>
              {sensor}
            </span>
            {SENSOR_META[sensor] && (
              <>
                <span style={{ fontSize: '0.80rem', color: 'var(--text1)' }}>
                  {SENSOR_META[sensor].label}
                </span>
                <span style={{ fontSize: '0.72rem', background: 'var(--surface2)', color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 6px' }}>
                  {SENSOR_META[sensor].subsystem}
                </span>
              </>
            )}
          </div>
          <div className="sensor-chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={readings} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                <XAxis dataKey="cycle" tick={{ fill: 'var(--text2)', fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: 'var(--text2)', fontSize: 11 }} tickLine={false} axisLine={false} width={52} domain={['auto', 'auto']} />
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
