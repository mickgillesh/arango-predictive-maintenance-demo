# AeroFleet — Predictive Maintenance Demo

A sales demo for the **ArangoDB Contextual Data Platform**, modelling a fleet operator
managing 100 turbofan engines across five global maintenance bases. Real NASA C-MAPSS
telemetry drives a deterministic health-index scorer. A synthetic operational graph
connects each engine to its aircraft, subsystems, spare parts, technicians, and work
orders. Users explore the fleet through a React dashboard, query it in natural language
via an AI chat panel, and use an AI planning assistant to generate and refine a
multi-engine maintenance schedule.

**Narrative: Predict → Understand → Ask → Plan**

---

## Screens

| Screen | What it shows | Core demo point |
|---|---|---|
| Fleet Overview | Health dashboard for all 100 engines; critical / warning / healthy KPI tiles | Spot at-risk engines before a grounding event |
| Engine Detail | Sensor trends for 14 drifting channels + full engine metadata | Understand *why* an engine is degrading |
| Impact Explorer | Affected aircraft, degrading subsystems, blocking parts, qualified technicians — one AQL traversal | Multi-hop graph answers "what breaks?" and "who can fix it?" in a single query |
| Chat (always-on) | Natural-language questions answered with live AQL, shown in a collapsible panel | Non-technical users explore the graph without writing a query |
| Planning Dashboard | AI-generated maintenance schedule: Gantt chart by technician, work order table, clickable work order drawer, planning chat assistant | End-to-end from prediction to executable plan; constrained AI planning with bi-temporal assignment history |

---

## Architecture

```
NASA C-MAPSS FD001 dataset
        │
        ▼
pipeline/loader.py        ──► ArangoDB cloud (aerofleet-demo)
pipeline/synthetic_graph.py    8 vertex collections, 9 edge collections,
                               named graph fleetGraph
pipeline/scorer.py        ──► health index + predictedRUL written back to engines

backend/app.py (FastAPI)
  ├── GET  /api/fleet                      fleet list with risk KPI tiles
  ├── GET  /api/engines/{id}               full engine document
  ├── GET  /api/engines/{id}/readings      sensor time-series (C-MAPSS)
  ├── GET  /api/engines/{id}/impact        multi-hop graph traversal (4 edge types)
  ├── GET  /api/health                     service health check
  ├── GET  /api/suggestions                preset demo questions for chat panel
  ├── POST /api/ask                        natural language → AQL → answer
  ├── POST /api/plan/run                   generate AI maintenance schedule (SSE)
  ├── POST /api/plan/reset                 wipe generated work orders
  ├── GET  /api/plan/work-orders           fetch current planned work orders + schedule
  ├── GET  /api/plan/schedule-at?t=<unix>  historical schedule snapshot (bi-temporal)
  ├── POST /api/plan/chat                  planning assistant conversation (SSE)
  └── POST /api/plan/apply-edits           apply confirmed changes to ArangoDB

AI components
  ├── Chat panel — LangChain ArangoGraphQAChain (GPT-4o)
  │     Introspects live DB schema; generates + executes read-only AQL
  └── Planning assistant — LangGraph ReAct agent (GPT-4o)
        Tools: query_ontology, read_graph, get_work_orders,
               find_eligible_technicians, check_technician_availability,
               propose_reassign_work_order, propose_update_entity,
               propose_delete_entity, propose_create/delete_relationship
        Pattern: propose → user confirms → apply-edits writes to DB

frontend/ (React + TypeScript strict)
  ├── /                   Fleet Overview
  ├── /engines/:id        Engine Detail
  ├── /engines/:id/impact Impact Explorer
  └── /plan               Planning Dashboard
       ├── Gantt chart (technician × time; bars clickable → work order drawer)
       ├── Work order table (rows clickable → work order drawer)
       └── Planning chat assistant with Pending Changes panel
```

---

## Data sources

### NASA C-MAPSS FD001

20,631 rows of simulated turbofan telemetry from 100 engines. Each row is one
flight cycle with 21 sensor readings and 3 operational-setting channels. The
dataset records degradation from a healthy state through to failure, making it
ideal for remaining-useful-life (RUL) modelling.

Source: NASA Prognostics Data Repository — CMAPSS Jet Engine Simulated Data.

### Synthetic operational graph

Generated deterministically (seed=42) to wrap each engine in a realistic
operational context across five bases: **LHR, JFK, SIN, DXB, FRA**.

| Vertex collection | Count | Description |
|---|---|---|
| `engines` | 100 | Turbofan engines with health scores and predicted RUL |
| `aircraft` | 50 | Airframes; 2 engines per aircraft |
| `subsystems` | 600 | 6 subsystem types per engine (fan, LPC, HPC, HPT, LPT, combustor) |
| `sensors` | 2,100 | 21 sensors per engine, mapped to their monitored subsystem |
| `parts` | 39 | Spare parts catalogue; some zero-stock (blocking), some in-stock |
| `technicians` | 10 | Maintenance staff with subsystem certifications; 2 per base |
| `workOrders` | 200 + planner | 200 historical closed WOs; planner adds procurement + maintenance WOs |

| Edge collection | Direction | Meaning |
|---|---|---|
| `installedOn` | engine → aircraft | Engine is installed on an airframe |
| `partOf` | subsystem → engine | Subsystem belongs to an engine |
| `monitors` | sensor → subsystem | Sensor measures a subsystem |
| `requiredBy` | part → subsystem | Spare part is required by a subsystem type |
| `certifiedFor` | technician → subsystem | Technician holds certification for a subsystem at their base |
| `maintains` | workOrder → engine | Work order covers an engine |
| `performedBy` | workOrder → technician | Work order assigned to a technician (bi-temporal — see below) |
| `consumed` | workOrder → part | Work order consumes a spare part |

The named graph `fleetGraph` spans all 8 operational edge collections.

---

## Health scoring

The health index scorer (`pipeline/scorer.py`) computes a 0–1 score per engine from the
14 drifting sensor channels using exponential drift saturation:

- **DRIFT_SATURATION = 12.5 σ** — a sensor reading this far from its per-engine baseline is counted as fully degraded.
- **Warning threshold = 0.62** — engines below this are flagged as warning.
- **Critical threshold** — derived from the predictedRUL distribution; engines with very low RUL are marked critical.

Typical distribution across 100 engines: ~3 critical / ~10 warning / ~87 healthy.

---

## AI planning assistant

The Planning Dashboard generates a complete maintenance schedule for all critical and
warning engines, then lets a conversational assistant refine it.

### Plan generation

`POST /api/plan/run` streams a LangGraph ReAct agent that:

1. Reads fleet context (engines, parts, technicians) via AQL.
2. For each at-risk engine, creates `procurement` work orders (if blocking parts exist)
   and `maintenance` work orders, scheduling them serially with no technician overlap.
3. Assigns technicians by base and certification match.
4. Writes work orders and bi-temporal `performedBy` edges to ArangoDB.

Work orders are scheduled in working hours (Mon–Fri, 08:00–16:00) from today.

### Bi-temporal assignment history

`performedBy` edges carry `validFrom` and `validTo` (Unix seconds). Reassignment
**never deletes** the old edge — it sets `validTo = now` on the old record and inserts
a new edge. This means:

- **"Who is assigned to WO X right now?"** — filter `validTo > now`
- **"Who was assigned at time T?"** — filter `validFrom <= T AND validTo > T`
- `GET /api/plan/schedule-at?t=<unix>` replays the schedule at any point in the past

### Planning assistant tools

The planning assistant enforces hard constraints automatically — the LLM cannot bypass them:

| Tool | Purpose |
|---|---|
| `find_eligible_technicians(wo_key)` | Returns only technicians at the correct base with a matching certification |
| `check_technician_availability(tech_key)` | Returns the technician's current schedule slots |
| `propose_reassign_work_order(wo_key, new_tech_key)` | Validates base, cert, and schedule overlap; expires old edge, creates new one |
| `propose_update_entity(...)` | Stage a field change on any entity |
| `propose_delete_entity(...)` | Stage a cascade delete (aircraft, engine, WO) |
| `query_ontology()` | Inspect allowed operations and editable fields |
| `read_graph(aql)` | Read-only AQL execution |

All `propose_*` tools return a pending proposal — **no DB write occurs until the user
confirms** in the Pending Changes panel.

---

## Setup

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+ with npm
- ArangoDB cloud instance (3.12+) with a database whose name contains `aerofleet`
- OpenAI API key (GPT-4o)

### 1. Clone and install

```bash
git clone <repo>
cd arango-predictive-maintenance-demo
uv sync                      # Python deps
cd frontend && npm install && cd ..
```

### 2. Configure environment

```bash
cp .env.example .env.local
# Edit .env.local:
#   ARANGO_URL        ArangoDB instance URL (e.g. https://xxx.arangodb.cloud:8529)
#   ARANGO_DB         Database name (must contain "aerofleet")
#   ARANGO_USER       Database username
#   ARANGO_PASSWORD   Database password
#   OPENAI_API_KEY    OpenAI key
#   OPENAI_MODEL      Optional — default: gpt-4o
#   FORCED_CRITICAL   Optional — comma-separated engine IDs to pin as critical
```

### 3. Load data

```bash
make load    # downloads C-MAPSS, generates synthetic graph, writes to ArangoDB
make score   # computes health scores, writes predictedRUL back to engines
# Shorthand: make reset  (runs both)
```

### 4. Run

```bash
make dev     # FastAPI on :8000 + Vite dev server on :5173 (with API proxy)
```

Open `http://localhost:5173`.

For production-style single-process serving:

```bash
cd frontend && npm run build && cd ..
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# Served at http://localhost:8000
```

### 5. Test

```bash
make test    # pytest + frontend TypeScript build check
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ARANGO_URL` | Yes | ArangoDB instance URL |
| `ARANGO_DB` | Yes | Database name (must contain `aerofleet`) |
| `ARANGO_USER` | Yes | Database username |
| `ARANGO_PASSWORD` | Yes | Database password |
| `OPENAI_API_KEY` | Yes | OpenAI key for both AI features |
| `OPENAI_MODEL` | No | Model name (default: `gpt-4o`) |
| `FORCED_CRITICAL` | No | Comma-separated engine IDs pinned to critical risk (useful for demos) |

Without `OPENAI_API_KEY` the app runs; the chat panel shows "not configured" and
the planning assistant is disabled.

---

## Project conventions

- All AQL queries are bind-parameterised and live in `backend/aql.py`. No string interpolation of user input anywhere.
- Data generation is deterministic (seed=42). Output must not change across runs.
- Loader safety: destructive operations are guarded to databases whose name contains `aerofleet`.
- No secrets in the repository. All config via environment variables mirrored in `.env.example`.
- Python: ruff-clean, type hints on all public functions.
- Frontend: TypeScript strict mode.
