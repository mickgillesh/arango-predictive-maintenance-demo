export type RiskBucket = 'critical' | 'warning' | 'healthy'

export interface EngineRow {
  id: string
  tailNumber: string | null
  base: string | null
  predictedRUL: number
  riskBucket: RiskBucket
}

export interface KPI {
  critical?: number
  warning?: number
  healthy?: number
}

export interface FleetResponse {
  kpi: KPI
  engines: EngineRow[]
}

export interface EngineDetail {
  _key: string
  engineId: number
  model: string
  entryIntoService: string
  healthIndex: number
  predictedRUL: number
  riskScore: number
  riskBucket: RiskBucket
  driverSensors: string[]
  driverSubsystems: string[]
  scoringMethod: string
  aircraft: { tailNumber: string; base: string }
}

export interface ReadingPoint {
  cycle: number
  [sensor: string]: number
}

export interface ReadingsResponse {
  engineId: number
  sensors: string[]
  readings: ReadingPoint[]
}

export interface Part {
  id: string
  name: string
  subsystemType: string
  stockLevel: number
  leadTimeDays: number
  blocking: boolean
}

export interface Technician {
  id: string
  name: string
  homeBase: string
  certifications: string[]
}

export interface ImpactResponse {
  engine: {
    id: string
    riskBucket: RiskBucket
    predictedRUL: number
    driverSubsystems: string[]
  }
  aircraft: { tailNumber: string; base: string }
  degradingSubsystems: string[]
  parts: Part[]
  technicians: Technician[]
  blockingParts: Part[]
}

export interface AskResponse {
  answer: string | null
  aql: string | null
  raw: Record<string, unknown> | null
  error: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  aql?: string | null
  error?: string | null
}
