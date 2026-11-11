import type {
  FleetResponse, EngineDetail, ReadingsResponse, ImpactResponse,
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
}
