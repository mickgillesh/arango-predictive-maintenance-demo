import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api'
import type { ImpactResponse } from '../types'

export function ImpactExplorer() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<ImpactResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api.impact(id).then(setData).catch(e => setError(String(e)))
  }, [id])

  if (error)  return <div className="error-state">Failed to load impact data: {error}</div>
  if (!data)  return <div className="empty-state"><span className="spinner" /></div>

  const blockingCount  = data.blockingParts.length
  const maxLead        = data.blockingParts.reduce((mx, p) => Math.max(mx, p.leadTimeDays), 0)
  const techCount      = data.technicians.length

  return (
    <>
      <nav className="breadcrumb">
        <Link to="/">Fleet</Link>
        <span className="sep">›</span>
        <Link to={`/engines/${id}`}>Engine #{id}</Link>
        <span className="sep">›</span>
        <span>Impact</span>
      </nav>

      <div style={{ marginBottom: 24 }}>
        <h1>Maintenance Impact — Engine #{id}</h1>
        <div style={{ color: 'var(--text2)', fontSize: '0.9rem', marginTop: 4 }}>
          Aircraft <strong style={{ color: 'var(--text)' }}>{data.aircraft.tailNumber}</strong> · Base <strong style={{ color: 'var(--text)' }}>{data.aircraft.base}</strong>
        </div>
      </div>

      {/* Summary stats */}
      <div className="summary-box">
        <div className="stat-row">
          <div>
            <div className="stat-num" style={{ color: data.engine.riskBucket === 'critical' ? 'var(--critical)' : 'var(--warning)' }}>
              {data.engine.predictedRUL}
            </div>
            <div className="stat-lbl">cycles remaining</div>
          </div>
          <div>
            <div className="stat-num" style={{ color: blockingCount > 0 ? 'var(--critical)' : 'var(--healthy)' }}>
              {blockingCount}
            </div>
            <div className="stat-lbl">blocking parts</div>
          </div>
          <div>
            <div className="stat-num">{data.parts.length}</div>
            <div className="stat-lbl">parts needed</div>
          </div>
          <div>
            <div className="stat-num">{techCount}</div>
            <div className="stat-lbl">certified techs at base</div>
          </div>
        </div>
        <p>
          {blockingCount > 0
            ? <><strong>{blockingCount} out-of-stock part{blockingCount > 1 ? 's' : ''} block maintenance</strong>
                {maxLead > 0 && ` — up to ${maxLead}-day procurement lead time.`}</>
            : <><strong>All required parts are in stock.</strong> Maintenance can proceed immediately.</>
          }
          {techCount > 0
            ? <> <strong>{techCount} certified technician{techCount !== 1 ? 's' : ''}</strong> {techCount === 1 ? 'is' : 'are'} available at {data.aircraft.base}.</>
            : <> <strong>No certified technicians</strong> currently at {data.aircraft.base} — remote dispatch may be required.</>
          }
        </p>
      </div>

      {/* Degrading subsystems */}
      <div className="section">
        <h2>Degrading Subsystems</h2>
        <div className="impact-grid">
          {data.degradingSubsystems.map(sub => (
            <div key={sub} className="impact-node">
              <div className="impact-node-type">Subsystem</div>
              <div className="impact-node-name" style={{ color: 'var(--warning)' }}>{sub}</div>
              <div className="impact-node-meta">On engine #{data.engine.id}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Parts */}
      <div className="section">
        <h2>Required Parts</h2>
        <div className="impact-grid">
          {data.parts.map(p => (
            <div key={p.id} className={`impact-node${p.blocking ? ' blocking' : ''}`}>
              <div className="impact-node-type">{p.subsystemType} part{p.blocking ? ' · ⚠ out of stock' : ''}</div>
              <div className="impact-node-name">{p.name}</div>
              <div className="impact-node-meta">
                Stock: <strong style={{ color: p.stockLevel === 0 ? 'var(--critical)' : 'var(--healthy)' }}>{p.stockLevel}</strong>
                {' · '}{p.leadTimeDays}d lead
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Technicians */}
      <div className="section">
        <h2>Certified Technicians at {data.aircraft.base}</h2>
        {data.technicians.length === 0 ? (
          <div className="empty-state" style={{ padding: '20px 0', textAlign: 'left' }}>
            No certified technicians at this base for the degrading subsystems.
          </div>
        ) : (
          <div className="impact-grid">
            {data.technicians.map(t => (
              <div key={t.id} className="impact-node">
                <div className="impact-node-type">Technician · {t.homeBase}</div>
                <div className="impact-node-name">{t.name}</div>
                <div className="impact-node-meta tag-list" style={{ marginTop: 8 }}>
                  {t.certifications.map(c => <span key={c} className="tag">{c}</span>)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
