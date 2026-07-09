import type {
  FleetResponse, EngineDetail, ReadingsResponse,
  ImpactResponse, AskResponse,
} from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
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
}
