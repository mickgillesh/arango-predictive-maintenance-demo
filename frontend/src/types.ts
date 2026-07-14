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

// ---------------------------------------------------------------------------
// Maintenance planner
// ---------------------------------------------------------------------------

export type WOType   = 'maintenance' | 'procurement'
export type WOStatus = 'open' | 'pending-parts' | 'closed'

export interface PlannedWorkOrder {
  _key: string
  type: WOType
  status: WOStatus
  engineId: string
  deadline: string
  description: string
  riskBucket: RiskBucket
  technician: { id: string; name: string; homeBase: string }
  parts: Part[]
}

export interface PlanSummary {
  totalWorkOrders: number
  maintenanceOrders: number
  procurementOrders: number
  enginesPlanned: number
  reasoningSummary?: string
}

// ---------------------------------------------------------------------------
// Planning chat assistant
// ---------------------------------------------------------------------------

export type ProposeOpType =
  | 'create_entity'
  | 'update_entity'
  | 'delete_entity'
  | 'create_relationship'
  | 'delete_relationship'

export interface ProposeEditOperation {
  type: ProposeOpType
  entity_type?: string
  entity_key?: string
  fields?: Record<string, unknown>
  edge_type?: string
  from_id?: string
  to_id?: string
}

export interface ProposeEdit {
  id: string
  description: string
  operation: ProposeEditOperation
}

export type PlannerChatRole = 'user' | 'assistant' | 'thinking' | 'tool_call'

export interface PlannerChatMessage {
  id: string
  role: PlannerChatRole
  text: string
  tool?: string
}
