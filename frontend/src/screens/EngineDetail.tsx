import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { RulGauge } from '../components/RulGauge'
import { SensorTrend } from '../components/SensorTrend'
import type { EngineDetail as EngineDetailType, ReadingsResponse } from '../types'

export function EngineDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [engine, setEngine] = useState<EngineDetailType | null>(null)
  const [readings, setReadings] = useState<ReadingsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api.engine(id)
      .then(e => {
        setEngine(e)
        return api.readings(id, e.driverSensors.slice(0, 3))
      })
      .then(setReadings)
      .catch(e => setError(String(e)))
  }, [id])

  if (error)   return <div className="error-state">Engine not found or failed to load: {error}</div>
  if (!engine) return <div className="empty-state"><span className="spinner" /></div>

  const riskClass = `badge badge-${engine.riskBucket}`

  return (
    <>
      <nav className="breadcrumb">
        <Link to="/">Fleet</Link>
        <span className="sep">›</span>
        <span>Engine #{id}</span>
      </nav>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 32, marginBottom: 28, flexWrap: 'wrap' }}>
        <RulGauge rul={engine.predictedRUL} riskBucket={engine.riskBucket} size={170} />
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <h1>Engine #{engine.engineId}</h1>
            <span className={riskClass}>{engine.riskBucket}</span>
          </div>
          <div className="card">
            <div className="detail-row"><span className="detail-label">Model</span><span>{engine.model}</span></div>
            <div className="detail-row"><span className="detail-label">Aircraft</span><span>{engine.aircraft.tailNumber}</span></div>
            <div className="detail-row"><span className="detail-label">Base</span><span>{engine.aircraft.base}</span></div>
            <div className="detail-row"><span className="detail-label">In service</span><span>{engine.entryIntoService}</span></div>
            <div className="detail-row"><span className="detail-label">Health index</span><span>{(engine.healthIndex * 100).toFixed(1)}%</span></div>
            <div className="detail-row"><span className="detail-label">Risk score</span><span>{engine.riskScore.toFixed(3)}</span></div>
          </div>
        </div>
      </div>

      {/* Degrading subsystems */}
      <div className="section">
        <div className="card">
          <h2>Degrading Subsystems</h2>
          <div className="tag-list">
            {engine.driverSubsystems.map(s => (
              <span key={s} className="tag" style={{ color: 'var(--warning)', borderColor: 'rgba(210,153,34,.4)' }}>{s}</span>
            ))}
          </div>
          <div style={{ marginTop: 12, fontSize: '0.85rem', color: 'var(--text2)' }}>
            Driver sensors: {engine.driverSensors.join(', ')}
          </div>
        </div>
      </div>

      {/* Sensor trends */}
      {readings && readings.readings.length > 0 && (
        <div className="section">
          <div className="card">
            <div className="section-header">
              <h2>Sensor Trends (driver sensors)</h2>
            </div>
            <SensorTrend readings={readings.readings} sensors={readings.sensors} />
          </div>
        </div>
      )}

      {/* Impact CTA */}
      <div className="section">
        <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
          <div>
            <h3>Maintenance Impact</h3>
            <div style={{ color: 'var(--text2)', fontSize: '0.88rem' }}>
              See parts availability, certified technicians, and blocking items.
            </div>
          </div>
          <button className="btn btn-primary" onClick={() => navigate(`/engines/${id}/impact`)}>
            View Impact →
          </button>
        </div>
      </div>
    </>
  )
}
