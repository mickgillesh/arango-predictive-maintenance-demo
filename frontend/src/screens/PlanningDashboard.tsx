import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type {
  PlannedWorkOrder, PlanSummary, RiskBucket, WOType, WOStatus,
  ProposeEdit, PlannerChatMessage,
} from '../types'

interface LogLine { text: string; cls: string }

function deadlineColor(dl: string): string {
  const days = (new Date(dl).getTime() - Date.now()) / 86_400_000
  if (days <= 7)  return 'var(--critical)'
  if (days <= 30) return 'var(--warning)'
  return 'var(--text)'
}

function TypeBadge({ type }: { type: WOType }) {
  return <span className={`badge-${type}`}>{type}</span>
}

function StatusBadge({ status }: { status: WOStatus }) {
  const cls = status === 'pending-parts' ? 'badge-pending-parts' : 'badge-open'
  return <span className={cls}>{status}</span>
}

function RiskDot({ bucket }: { bucket: RiskBucket }) {
  const color = bucket === 'critical' ? 'var(--critical)' : bucket === 'warning' ? 'var(--warning)' : 'var(--healthy)'
  return <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: color, marginRight: 6 }} />
}

export function PlanningDashboard() {
  const [running, setRunning]       = useState(false)
  const [logLines, setLogLines]     = useState<LogLine[]>([])
  const [workOrders, setWorkOrders] = useState<PlannedWorkOrder[]>([])
  const [summary, setSummary]       = useState<PlanSummary | null>(null)
  const [resetting, setResetting]   = useState(false)
  const abortRef  = useRef<AbortController | null>(null)
  const logEndRef = useRef<HTMLDivElement | null>(null)

  // Chat assistant state
  const [chatMessages, setChatMessages] = useState<PlannerChatMessage[]>([])
  const [pendingEdits, setPendingEdits] = useState<ProposeEdit[]>([])
  const [chatInput, setChatInput]       = useState('')
  const [chatRunning, setChatRunning]   = useState(false)
  const [sessionId]                     = useState(() => crypto.randomUUID())
  const chatAbortRef                    = useRef<AbortController | null>(null)
  const chatEndRef                      = useRef<HTMLDivElement | null>(null)

  // Pre-populate from previous plan run
  useEffect(() => {
    api.planWorkOrders()
      .then(r => setWorkOrders(r.workOrders))
      .catch(() => { /* no plan yet */ })
  }, [])

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logLines])

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  const addLog = (text: string, cls = '') =>
    setLogLines(prev => [...prev, { text, cls }])

  async function handleRun() {
    if (running) return
    abortRef.current = new AbortController()
    setRunning(true)
    setLogLines([])
    setSummary(null)

    try {
      await api.planRun((event, data) => {
        const d = data as Record<string, unknown>
        if (event === 'start' || event === 'progress') {
          addLog(`▸ ${d.message as string}`)
        } else if (event === 'work_order') {
          const type = d.type as string
          const cls = type === 'procurement' ? 'procurement' : 'maintenance'
          addLog(
            `  ✓ [${type.toUpperCase()}] Engine #${d.engineId} → ${d.technicianName}  deadline: ${d.deadline}`,
            cls,
          )
          setWorkOrders(prev => [...prev, d as unknown as PlannedWorkOrder])
        } else if (event === 'summary') {
          setSummary(d as unknown as PlanSummary)
          addLog(`\n✔ Plan complete: ${d.totalWorkOrders} work orders across ${d.enginesPlanned} engines.`)
        } else if (event === 'error') {
          const code = d.error as string
          const msg =
            code === 'openai_not_configured' ? 'OPENAI_API_KEY not set — AI planner unavailable.'
            : code === 'plan_already_running' ? 'A plan is already running.'
            : `Error: ${code}${d.detail ? ` — ${d.detail}` : ''}`
          addLog(msg, 'error')
        }
      }, abortRef.current.signal)
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        addLog(`Network error: ${e.message}`, 'error')
      }
    } finally {
      setRunning(false)
    }
  }

  function handleStop() {
    abortRef.current?.abort()
  }

  async function sendChat() {
    const msg = chatInput.trim()
    if (!msg || chatRunning) return
    setChatInput('')
    setChatRunning(true)
    const userMsg: PlannerChatMessage = { id: crypto.randomUUID(), role: 'user', text: msg }
    setChatMessages(prev => [...prev, userMsg])
    chatAbortRef.current = new AbortController()
    try {
      await api.planChat(msg, sessionId, (event, data) => {
        const d = data as Record<string, unknown>
        if (event === 'thinking') {
          setChatMessages(prev => [...prev, {
            id: crypto.randomUUID(), role: 'thinking', text: d.message as string
          }])
        } else if (event === 'tool_call') {
          setChatMessages(prev => [...prev, {
            id: crypto.randomUUID(), role: 'tool_call',
            text: d.input as string, tool: d.tool as string
          }])
        } else if (event === 'propose') {
          setPendingEdits(prev => [...prev, data as ProposeEdit])
        } else if (event === 'answer') {
          setChatMessages(prev => [...prev, {
            id: crypto.randomUUID(), role: 'assistant', text: d.text as string
          }])
        } else if (event === 'error') {
          setChatMessages(prev => [...prev, {
            id: crypto.randomUUID(), role: 'assistant',
            text: `Error: ${d.error as string}${d.detail ? ` — ${d.detail}` : ''}`
          }])
        }
      }, chatAbortRef.current.signal)
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        setChatMessages(prev => [...prev, {
          id: crypto.randomUUID(), role: 'assistant', text: `Network error: ${e.message}`
        }])
      }
    } finally {
      setChatRunning(false)
    }
  }

  async function handleConfirmAll() {
    if (!pendingEdits.length) return
    const edits = [...pendingEdits]
    setPendingEdits([])
    try {
      const res = await api.planApplyEdits(edits)
      const refreshed = await api.planWorkOrders()
      setWorkOrders(refreshed.workOrders)
      setChatMessages(prev => [...prev, {
        id: crypto.randomUUID(), role: 'assistant',
        text: `Applied ${res.applied} change${res.applied !== 1 ? 's' : ''}.${
          (res.errors as unknown[]).length ? ` (${(res.errors as unknown[]).length} errors)` : ''
        }`,
      }])
    } catch {
      setChatMessages(prev => [...prev, {
        id: crypto.randomUUID(), role: 'assistant', text: 'Failed to apply changes.'
      }])
    }
  }

  function handleRejectAll() { setPendingEdits([]) }
  function dismissEdit(id: string) { setPendingEdits(prev => prev.filter(e => e.id !== id)) }

  async function handleReset() {
    if (!confirm('Delete all planner-generated work orders? Historical data is preserved.')) return
    setResetting(true)
    try {
      await api.planReset()
      setWorkOrders([])
      setSummary(null)
      setLogLines([])
    } finally {
      setResetting(false)
    }
  }

  // Group work orders by technician
  const byTech: Record<string, { name: string; homeBase: string; wos: PlannedWorkOrder[] }> = {}
  for (const wo of workOrders) {
    const tech = wo.technician ?? { id: wo._key, name: 'Unassigned', homeBase: '—' }
    if (!byTech[tech.id]) byTech[tech.id] = { name: tech.name, homeBase: tech.homeBase, wos: [] }
    byTech[tech.id].wos.push(wo)
  }

  return (
    <>
      <nav className="breadcrumb">
        <Link to="/">Fleet</Link>
        <span className="sep">›</span>
        <span>Maintenance Planner</span>
      </nav>

      <div className="section-header" style={{ marginBottom: 20 }}>
        <div>
          <h1>Maintenance Planner</h1>
          <div style={{ color: 'var(--text2)', fontSize: '0.9rem' }}>
            AI-powered work order generation for critical and warning engines
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {running ? (
            <button className="btn btn-primary" onClick={handleStop}>Stop</button>
          ) : (
            <button className="btn btn-primary" onClick={handleRun} disabled={resetting}>
              Generate Maintenance Plan
            </button>
          )}
          {workOrders.length > 0 && !running && (
            <button className="btn-danger" onClick={handleReset} disabled={resetting}>
              {resetting ? 'Resetting…' : 'Reset Plan'}
            </button>
          )}
        </div>
      </div>

      {/* Live log */}
      {(logLines.length > 0 || running) && (
        <div className="section">
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <h2>Agent Log</h2>
              {running && <span className="spinner" style={{ width: 14, height: 14 }} />}
            </div>
            <div className="log-panel">
              {logLines.map((l, i) => (
                <div key={i} className={`log-line${l.cls ? ` ${l.cls}` : ''}`}>{l.text}</div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      )}

      {/* Summary KPI bar */}
      {summary && (
        <div className="plan-kpi-grid">
          <div className="kpi-card">
            <div className="kpi-value">{summary.totalWorkOrders}</div>
            <div className="kpi-label">Work Orders</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value risk-warning">{summary.enginesPlanned}</div>
            <div className="kpi-label">Engines Planned</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value risk-healthy">{summary.maintenanceOrders}</div>
            <div className="kpi-label">Maintenance</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-value risk-critical">{summary.procurementOrders}</div>
            <div className="kpi-label">Procurement</div>
          </div>
        </div>
      )}

      {/* Pre-populated results when no summary yet */}
      {!summary && workOrders.length > 0 && !running && (
        <div style={{ marginBottom: 16, color: 'var(--text2)', fontSize: '0.88rem' }}>
          Showing {workOrders.length} work orders from previous plan run.
        </div>
      )}

      {/* Results grouped by technician */}
      {workOrders.length > 0 && (
        <div className="section">
          {Object.values(byTech).map(group => (
            <div key={group.name} className="card" style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <h3>{group.name}</h3>
                <span className="tag">{group.homeBase}</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Engine</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Risk</th>
                      <th>Deadline</th>
                      <th style={{ maxWidth: 340 }}>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.wos.map(wo => (
                      <tr key={wo._key}>
                        <td>
                          <Link to={`/engines/${wo.engineId}`}>#{wo.engineId}</Link>
                        </td>
                        <td><TypeBadge type={wo.type} /></td>
                        <td><StatusBadge status={wo.status} /></td>
                        <td>
                          <RiskDot bucket={wo.riskBucket} />
                          {wo.riskBucket}
                        </td>
                        <td style={{ color: deadlineColor(wo.deadline), fontVariantNumeric: 'tabular-nums' }}>
                          {wo.deadline}
                        </td>
                        <td style={{ color: 'var(--text2)', fontSize: '0.85rem' }}>
                          {wo.description}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Planning Assistant chat panel */}
      {workOrders.length > 0 && (
        <div className="section">
          <div className="card">
            <h2>Planning Assistant</h2>

            {/* Chat history */}
            <div className="plan-chat-msgs">
              {chatMessages.length === 0 && (
                <div className="plan-chat-empty">
                  Plan generated. Ask me to refine it — reassign work orders, adjust deadlines,
                  update technician bases, retire aircraft, update parts stock…
                </div>
              )}
              {chatMessages.map(m => {
                if (m.role === 'thinking') {
                  return (
                    <div key={m.id} className="plan-msg plan-msg-thinking">
                      <span className="spinner" style={{ width: 10, height: 10, marginRight: 6, display: 'inline-block' }} />
                      {m.text}
                    </div>
                  )
                }
                if (m.role === 'tool_call') {
                  return (
                    <div key={m.id} className="plan-msg plan-msg-tool_call">
                      <span style={{ color: 'var(--accent)' }}>{m.tool}</span>
                      {m.text ? `(${m.text.slice(0, 120)}${m.text.length > 120 ? '…' : ''})` : '()'}
                    </div>
                  )
                }
                return (
                  <div
                    key={m.id}
                    className={`plan-msg ${m.role === 'user' ? 'plan-msg-user' : 'plan-msg-assistant'}`}
                  >
                    {m.text}
                  </div>
                )
              })}
              {chatRunning && (
                <div className="plan-msg plan-msg-assistant">
                  <span className="spinner" style={{ width: 14, height: 14 }} />
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Pending changes */}
            {pendingEdits.length > 0 && (
              <div className="pending-edits">
                <div className="pending-edits-header">
                  Pending Changes ({pendingEdits.length})
                </div>
                {pendingEdits.map(edit => (
                  <div key={edit.id} className="pending-edit-row">
                    <span>&#9998; {edit.description}</span>
                    <button onClick={() => dismissEdit(edit.id)} title="Remove">&#x2715;</button>
                  </div>
                ))}
                <div className="pending-edits-actions">
                  <button className="btn btn-primary btn-sm" onClick={handleConfirmAll}>
                    Confirm All ({pendingEdits.length})
                  </button>
                  <button className="btn-outline btn-sm" onClick={handleRejectAll}>
                    Reject All
                  </button>
                </div>
              </div>
            )}

            {/* Input row */}
            <div className="chat-input-row" style={{ padding: 0, borderTop: 'none', gap: 8 }}>
              <input
                className="chat-input"
                placeholder="Reassign a WO, retire an aircraft, update a technician's base…"
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && sendChat()}
                disabled={chatRunning}
              />
              <button
                className="btn btn-primary btn-sm"
                onClick={sendChat}
                disabled={chatRunning || !chatInput.trim()}
              >
                {chatRunning
                  ? <span className="spinner" style={{ width: 13, height: 13 }} />
                  : 'Send'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {workOrders.length === 0 && !running && (
        <div className="empty-state">
          No work orders yet. Click "Generate Maintenance Plan" to run the AI planner.
        </div>
      )}
    </>
  )
}
