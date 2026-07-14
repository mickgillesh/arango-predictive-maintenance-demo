import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api } from '../api'
import { RulHistogram } from '../components/RulHistogram'
import type { FleetResponse, RiskBucket } from '../types'

function RiskBadge({ bucket }: { bucket: RiskBucket }) {
  return <span className={`badge badge-${bucket}`}>{bucket}</span>
}

export function FleetOverview() {
  const [data, setData] = useState<FleetResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.fleet()
      .then(setData)
      .catch(e => setError(String(e)))
  }, [])

  if (error)  return <div className="error-state">Failed to load fleet data: {error}</div>
  if (!data)  return <div className="empty-state"><span className="spinner" /></div>

  const { kpi, engines } = data

  return (
    <>
      <div className="section-header" style={{ marginBottom: 24 }}>
        <div>
          <h1>AeroFleet</h1>
          <div style={{ color: 'var(--text2)', fontSize: '0.9rem' }}>
            {engines.length} engines · predictive maintenance dashboard
          </div>
        </div>
        <Link to="/plan" className="btn-outline">Maintenance Planner →</Link>
      </div>

      {/* KPI cards */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-value risk-critical">{kpi.critical ?? 0}</div>
          <div className="kpi-label">Critical</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value risk-warning">{kpi.warning ?? 0}</div>
          <div className="kpi-label">Warning</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value risk-healthy">{kpi.healthy ?? 0}</div>
          <div className="kpi-label">Healthy</div>
        </div>
      </div>

      {/* RUL distribution */}
      <div className="section">
        <div className="card">
          <h2>RUL Distribution</h2>
          <RulHistogram engines={engines} />
        </div>
      </div>

      {/* Engine table */}
      <div className="section">
        <div className="card">
          <h2>Fleet — sorted by RUL</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Engine</th>
                  <th>Aircraft</th>
                  <th>Base</th>
                  <th>RUL (cycles)</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {engines.map(e => (
                  <tr key={e.id} onClick={() => navigate(`/engines/${e.id}`)}>
                    <td style={{ fontWeight: 600 }}>#{e.id}</td>
                    <td>{e.tailNumber ?? '—'}</td>
                    <td>{e.base ?? '—'}</td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>{e.predictedRUL}</td>
                    <td><RiskBadge bucket={e.riskBucket} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  )
}
