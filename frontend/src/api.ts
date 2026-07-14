import type {
  FleetResponse, EngineDetail, ReadingsResponse,
  ImpactResponse, AskResponse, PlannedWorkOrder, ProposeEdit,
} from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

async function readSSE(
  url: string,
  method: string,
  onEvent: (event: string, data: unknown) => void,
  signal: AbortSignal,
  body?: string,
): Promise<void> {
  const r = await fetch(url, {
    method, signal,
    ...(body ? { body, headers: { 'Content-Type': 'application/json' } } : {}),
  })
  const reader = r.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const chunks = buf.split('\n\n')
    buf = chunks.pop()!
    for (const chunk of chunks) {
      const eventMatch = chunk.match(/^event: (\w+)/m)
      const dataMatch  = chunk.match(/^data: (.+)$/m)
      if (eventMatch && dataMatch) {
        try { onEvent(eventMatch[1], JSON.parse(dataMatch[1])) } catch { /* ignore */ }
      }
    }
  }
}

export const api = {
  fleet: () => get<FleetResponse>('/fleet'),
  engine: (id: string) => get<EngineDetail>(`/engines/${id}`),
  readings: (id: string, sensors?: string[]) => {
    const q = sensors?.length ? `?sensors=${sensors.join(',')}` : ''
    return get<ReadingsResponse>(`/engines/${id}/readings${q}`)
  },
  impact: (id: string) => get<ImpactResponse>(`/engines/${id}/impact`),
  suggestions: () => get<string[]>('/suggestions'),
  ask: (question: string): Promise<AskResponse> =>
    fetch(`${BASE}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }).then(r => r.json() as Promise<AskResponse>),

  // Planning — batch generation
  planRun: (onEvent: (event: string, data: unknown) => void, signal: AbortSignal) =>
    readSSE(`${BASE}/plan/run`, 'POST', onEvent, signal),
  planReset: (): Promise<{ deleted: Record<string, number> }> =>
    fetch(`${BASE}/plan/reset`, { method: 'POST' }).then(r => r.json()),
  planWorkOrders: () => get<{ workOrders: PlannedWorkOrder[] }>('/plan/work-orders'),

  // Planning — conversational assistant
  planChat: (
    message: string,
    sessionId: string,
    onEvent: (event: string, data: unknown) => void,
    signal: AbortSignal,
  ) =>
    readSSE(
      `${BASE}/plan/chat`, 'POST', onEvent, signal,
      JSON.stringify({ message, session_id: sessionId }),
    ),

  planApplyEdits: (edits: ProposeEdit[]): Promise<{ applied: number; errors: unknown[] }> =>
    fetch(`${BASE}/plan/apply-edits`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits }),
    }).then(r => r.json()),
}
