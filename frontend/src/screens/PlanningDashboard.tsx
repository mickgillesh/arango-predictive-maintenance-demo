import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type {
  PlannedWorkOrder, PlanSummary, RiskBucket, WOType, WOStatus,
  ProposeEdit, PlannerChatMessage, TechnicianTimeline, ScheduledTask,
} from '../types'

interface LogLine { text: string; cls: string }

// Convert a working-hour offset to a real wall-clock Date, skipping weekends.
// Hour 0 = today 08:00; each 8-hour block is one Mon-Fri working day.
function hourOffsetToDate(h: number): Date {
  const base = new Date()
  base.setHours(0, 0, 0, 0)
  let d = new Date(base)
  let workingDaysLeft = Math.floor(h / 8)
  while (workingDaysLeft > 0) {
    d.setDate(d.getDate() + 1)
    if (d.getDay() !== 0 && d.getDay() !== 6) workingDaysLeft--
  }
  d.setHours(8 + (h % 8), 0, 0, 0)
  return d
}

function fmtDateTime(h: number | null | undefined): string {
  if (h == null) return '—'
  return hourOffsetToDate(h).toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function fmtDayTick(dayOffset: number): string {
  // dayOffset is a working-day count, reuse hourOffsetToDate at day boundary
  const d = hourOffsetToDate(dayOffset * 8)
  d.setHours(0, 0, 0, 0)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

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

function WorkOrderDrawer({ wo, onClose }: { wo: PlannedWorkOrder; onClose: () => void }) {
  const techName = wo.technician?.name ?? '—'
  const techBase = wo.technician?.homeBase ?? '—'
  const endHour  = wo.scheduledHourStart != null && wo.estimatedHours != null
    ? wo.scheduledHourStart + wo.estimatedHours : null

  return (
    <>
      <div className="wo-drawer-backdrop" onClick={onClose} />
      <div className="wo-drawer">
        <div className="wo-drawer-header">
          <div>
            <div className="wo-drawer-key">{wo._key}</div>
            <div className="wo-drawer-title">Engine #{wo.engineId} — {wo.description}</div>
          </div>
          <button className="wo-drawer-close" onClick={onClose}>✕</button>
        </div>

        <div className="wo-drawer-grid">
          <span className="wo-drawer-label">Type</span>
          <span><TypeBadge type={wo.type} /></span>
          <span className="wo-drawer-label">Status</span>
          <span><StatusBadge status={wo.status} /></span>
          <span className="wo-drawer-label">Risk</span>
          <span><RiskDot bucket={wo.riskBucket} />{wo.riskBucket}</span>
          <span className="wo-drawer-label">Technician</span>
          <span>{techName} <span className="tag" style={{ marginLeft: 4 }}>{techBase}</span></span>
          <span className="wo-drawer-label">Start</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {fmtDateTime(wo.scheduledHourStart)}
          </span>
          <span className="wo-drawer-label">End</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {fmtDateTime(endHour)}{wo.estimatedHours != null ? ` · ${wo.estimatedHours}h` : ''}
          </span>
          <span className="wo-drawer-label">Deadline</span>
          <span style={{ color: deadlineColor(wo.deadline), fontVariantNumeric: 'tabular-nums' }}>
            {wo.deadline}
          </span>
        </div>

        {wo.parts?.length > 0 && (
          <>
            <div className="wo-drawer-section-title">Parts</div>
            {wo.parts.map(p => (
              <div key={p.id} className="wo-drawer-part-row">
                <span className={p.blocking ? 'wo-part-blocking' : 'wo-part-ok'}>
                  {p.blocking ? '⚠' : '✓'}
                </span>
                <div className="wo-drawer-part-info">
                  <div className="wo-drawer-part-name">{p.name}</div>
                  <div className="wo-drawer-part-meta">
                    {p.blocking
                      ? `Out of stock — ${p.leadTimeDays}d lead time`
                      : `In stock (${p.stockLevel})`}
                    <span className="tag" style={{ marginLeft: 8 }}>{p.subsystemType}</span>
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  )
}

function PlanGantt({
  timelines,
  workOrdersByKey,
  onSelect,
}: {
  timelines: TechnicianTimeline[]
  workOrdersByKey: Map<string, PlannedWorkOrder>
  onSelect: (wo: PlannedWorkOrder) => void
}) {
  if (!timelines.length) return null
  const allTasks = timelines.flatMap(tl => tl.tasks)
  // Axis in working-hours so sub-day tasks don't overlap visually.
  // Round up to the next full 8-hour day boundary.
  const maxHours = Math.ceil(Math.max(40, ...allTasks.map(t => t.hourEnd ?? (t.dayEnd + 1) * 8)) / 8) * 8
  // One tick per day (every 8 working-hours); aim for ≤10 ticks
  const dayCount = maxHours / 8
  const tickEvery = dayCount <= 10 ? 1 : dayCount <= 20 ? 2 : 5
  const ticks = Array.from({ length: Math.floor(dayCount / tickEvery) + 1 }, (_, i) => i * tickEvery)

  return (
    <div className="section">
      <div className="card">
        <h2>Technician Timelines</h2>
        <div className="gantt-wrap">
          {/* Day header */}
          <div className="gantt-row" style={{ paddingBottom: 4 }}>
            <div className="gantt-label-col" />
            <div className="gantt-track-col" style={{ position: 'relative', height: 18 }}>
              {ticks.map(d => (
                <span key={d} className="gantt-tick-label" style={{ left: `${((d * 8) / maxHours) * 100}%` }}>
                  {fmtDayTick(d)}
                </span>
              ))}
            </div>
          </div>

          {timelines.map(tl => (
            <div key={tl.technicianId} className="gantt-row">
              <div className="gantt-label-col">
                <div className="gantt-tech-name">{tl.technicianName}</div>
              </div>
              <div className="gantt-track-col">
                <div className="gantt-track">
                  {ticks.slice(1).map(d => (
                    <div key={d} className="gantt-grid-line" style={{ left: `${((d * 8) / maxHours) * 100}%` }} />
                  ))}
                  {tl.tasks.map((task: ScheduledTask, i: number) => {
                    const hs   = task.hourStart ?? task.dayStart * 8
                    const he   = task.hourEnd   ?? hs + (task.estimatedHours || 8)
                    const left = (hs / maxHours) * 100
                    const pct  = ((he - hs) / maxHours) * 100
                    const wo   = workOrdersByKey.get(task.woKey)
                    return (
                      <div
                        key={i}
                        className={`gantt-bar gantt-bar-${task.taskType}${wo ? ' gantt-bar-clickable' : ''}`}
                        style={{ left: `${left}%`, width: `max(calc(${pct}% - 2px), 4px)` }}
                        title={`${task.description} — ${task.estimatedHours}h`}
                        onClick={wo ? () => onSelect(wo) : undefined}
                      >
                        E{task.engineId} · {task.estimatedHours}h
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          ))}

          {/* Legend */}
          <div className="gantt-legend">
            <span className="gantt-legend-dot gantt-bar-maintenance" />Maintenance
            <span className="gantt-legend-dot gantt-bar-procurement" style={{ marginLeft: 16 }} />Procurement
          </div>
        </div>
      </div>
    </div>
  )
}

export function PlanningDashboard() {
  const [running, setRunning]       = useState(false)
  const [logLines, setLogLines]     = useState<LogLine[]>([])
  const [workOrders, setWorkOrders] = useState<PlannedWorkOrder[]>([])
  const [summary, setSummary]       = useState<PlanSummary | null>(null)
  const [timelines, setTimelines]   = useState<TechnicianTimeline[]>([])
  const [resetting, setResetting]   = useState(false)
  const [selectedWO, setSelectedWO] = useState<PlannedWorkOrder | null>(null)
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

  // Derive timeline from work orders whenever they change (covers both initial
  // load and post-plan refresh, so the Gantt survives page navigation).
  useEffect(() => {
    const scheduled = workOrders.filter(wo => wo.scheduledHourStart != null)
    if (!scheduled.length) { setTimelines([]); return }

    const techMap = new Map<string, TechnicianTimeline>()
    for (const wo of scheduled) {
      if (!wo.technician) continue
      const tid = wo.technician.id
      if (!techMap.has(tid)) techMap.set(tid, { technicianId: tid, technicianName: wo.technician.name, tasks: [] })
      const hourStart = wo.scheduledHourStart!
      const hourEnd   = hourStart + (wo.estimatedHours ?? 8)
      techMap.get(tid)!.tasks.push({
        woKey: wo._key,
        engineId: wo.engineId,
        taskType: wo.type,
        hourStart,
        hourEnd,
        dayStart: Math.floor(hourStart / 8),
        dayEnd:   Math.floor((hourEnd - 0.001) / 8),
        estimatedHours: wo.estimatedHours ?? 0,
        description: wo.description,
      })
    }
    setTimelines(Array.from(techMap.values()))
  }, [workOrders])

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
        } else if (event === 'timeline') {
          setTimelines((d.timelines as TechnicianTimeline[]) ?? [])
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
      // Refresh from DB to get fully-joined work orders (with technician, parts, riskBucket)
      try {
        const refreshed = await api.planWorkOrders()
        if (refreshed.workOrders.length > 0) setWorkOrders(refreshed.workOrders)
      } catch { /* non-fatal */ }
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

  // Index for Gantt bar → WO lookup
  const workOrdersByKey = new Map(workOrders.map(wo => [wo._key, wo]))

  // Group work orders by technician
  const byTech: Record<string, { name: string; homeBase: string; wos: PlannedWorkOrder[] }> = {}
  for (const wo of workOrders) {
    const tech = wo.technician ?? { id: wo._key, name: 'Unassigned', homeBase: '—' }
    if (!byTech[tech.id]) byTech[tech.id] = { name: tech.name, homeBase: tech.homeBase, wos: [] }
    byTech[tech.id].wos.push(wo)
  }

  return (
    <>
      {selectedWO && <WorkOrderDrawer wo={selectedWO} onClose={() => setSelectedWO(null)} />}
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
                      <th>Sched. Start</th>
                      <th>Est. Hours</th>
                      <th>Deadline</th>
                      <th style={{ maxWidth: 300 }}>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.wos.map(wo => (
                      <tr key={wo._key} className="wo-row-clickable" onClick={() => setSelectedWO(wo)}>
                        <td>
                          <Link to={`/engines/${wo.engineId}`} onClick={e => e.stopPropagation()}>#{wo.engineId}</Link>
                        </td>
                        <td><TypeBadge type={wo.type} /></td>
                        <td><StatusBadge status={wo.status} /></td>
                        <td>
                          <RiskDot bucket={wo.riskBucket} />
                          {wo.riskBucket}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--text2)' }}>
                          {wo.scheduledStart ?? '—'}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                          {wo.estimatedHours != null ? `${wo.estimatedHours}h` : '—'}
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

      {/* Technician timeline Gantt */}
      <PlanGantt
        timelines={timelines}
        workOrdersByKey={workOrdersByKey}
        onSelect={setSelectedWO}
      />

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
